"""Wi-Fi scanning, connection and status routes.

Routes decorate the shared `api_v3` Blueprint from ._common, so their
endpoint names are unchanged by living here.
"""
import threading
import time

from web_interface.blueprints.api_v3 import (
    api_v3, describe_exception, jsonify, logger, request,
)

# Joining a network while the setup AP is up has to take the AP down first,
# and the caller is almost always a phone connected *through* that AP. Run
# synchronously, the request outlives the link it arrived on: the browser
# never gets an answer and the Connect button appears to do nothing. So in AP
# mode the connect runs in the background and the result is kept here, where
# /wifi/status can report it once the user is back on either network.
_connect_lock = threading.Lock()
_last_connect_attempt = None
# Lets the 202 reach the phone before hostapd stops.
_AP_HANDOFF_DELAY_SECONDS = 2.0


def _last_connect_snapshot():
    with _connect_lock:
        return dict(_last_connect_attempt) if _last_connect_attempt else None


def _spawn(target):
    threading.Thread(target=target, name='wifi-connect', daemon=True).start()


def _connect_result_payload(ssid, success, message):
    """Shape a connect_to_network result for the client.

    The manager flags a rejected passphrase by prefixing the message with
    "wrong_password:"; the setup pages key off `error_type` rather than
    parsing nmcli's wording.
    """
    if success:
        return {'status': 'success', 'message': message}
    payload = {'status': 'error', 'message': message or 'Failed to connect to network'}
    if message and message.startswith('wrong_password:'):
        payload['error_type'] = 'wrong_password'
        payload['message'] = f'Incorrect password for {ssid}'
    return payload


def _run_background_connect(ssid, password):
    global _last_connect_attempt
    try:
        time.sleep(_AP_HANDOFF_DELAY_SECONDS)
        from src.wifi_manager import WiFiManager
        success, message = WiFiManager().connect_to_network(ssid, password)
        payload = _connect_result_payload(ssid, success, message)
    except Exception as e:
        logger.error("Background WiFi connect failed", exc_info=True)
        payload = {'status': 'error', 'message': describe_exception(e)}
    with _connect_lock:
        _last_connect_attempt = {
            'ssid': ssid,
            'state': 'success' if payload['status'] == 'success' else 'failed',
            'message': payload['message'],
            'error_type': payload.get('error_type'),
            'finished_at': time.time(),
        }


def _parse_bool_ish(value):
    """Coerce a JSON value that is supposed to be a boolean.

    A JSON boolean arrives as a real Python bool, but these routes are a
    public HTTP contract and not every caller sends one. `bool(value)` gets
    two common cases wrong: `bool("false")` is True (a non-empty string is
    always truthy), and a plain int does not match an `is True` check
    (`1 is True` is False, since `True` is a distinct singleton from the int
    `1`) -- so a caller sending `{"enabled": 1}` was silently treated as
    False. Recognizes a real bool, "true"/"false"/"1"/"0"/"yes"/"no"
    case-insensitively, and int 1/0.

    Returns None for anything else, rather than guessing. A supplied-but-
    unrecognized value (e.g. a typo) used to silently become False here,
    which for `enabled` on the radio route could disconnect Wi-Fi the caller
    never asked to turn off -- callers must treat None as a validation
    error, not a real False.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ('true', '1', 'yes'):
            return True
        if lowered in ('false', '0', 'no'):
            return False
        return None
    if isinstance(value, int):
        if value == 1:
            return True
        if value == 0:
            return False
        return None
    return None


# WiFi Management Endpoints
@api_v3.route('/wifi/status', methods=['GET'])
def get_wifi_status():
    """Get current WiFi connection status"""
    try:
        from src.wifi_manager import WiFiManager

        wifi_manager = WiFiManager()
        status = wifi_manager.get_wifi_status()

        # Get auto-enable setting from config
        auto_enable_ap = wifi_manager.config.get("auto_enable_ap_mode", True)  # Default: True (safe due to grace period)

        return jsonify({
            'status': 'success',
            'data': {
                'connected': status.connected,
                'ssid': status.ssid,
                'ip_address': status.ip_address,
                'signal': status.signal,
                'ap_mode_active': status.ap_mode_active,
                'auto_enable_ap_mode': auto_enable_ap,
                'last_connect_attempt': _last_connect_snapshot(),
            }
        })
    except Exception as e:
        logger.error("%s failed", request.path, exc_info=True)
        return jsonify({
            'status': 'error',
            'message': 'An error occurred; see logs for details',
            'details': describe_exception(e)
        }), 500
@api_v3.route('/wifi/scan', methods=['GET'])
def scan_wifi_networks():
    """Scan for available WiFi networks

    If AP mode is active, it will be temporarily disabled during scanning
    and automatically re-enabled afterward. Users connected to the AP will
    be briefly disconnected during this process.
    """
    try:
        from src.wifi_manager import WiFiManager

        wifi_manager = WiFiManager()

        # Check if AP mode is active before scanning (for user notification)
        ap_was_active = wifi_manager._is_ap_mode_active()

        # Perform the scan (this will handle AP mode disabling/enabling internally)
        networks, _was_cached = wifi_manager.scan_networks()

        # Convert to dict format
        networks_data = [
            {
                'ssid': net.ssid,
                'signal': net.signal,
                'security': net.security,
                'frequency': net.frequency
            }
            for net in networks
        ]

        response_data = {
            'status': 'success',
            'data': networks_data
        }

        # Inform user if AP mode was temporarily disabled
        if ap_was_active:
            response_data['message'] = (
                f'Found {len(networks_data)} networks. '
                'Note: AP mode was temporarily disabled during scanning and has been re-enabled. '
                'If you were connected to the setup network, you may need to reconnect.'
            )

        return jsonify(response_data)
    except Exception as e:
        logger.error("Error scanning WiFi networks", exc_info=True)
        error_message = 'An error occurred while scanning WiFi networks; see logs for details'

        # Provide more specific error messages for common issues
        error_str = str(e).lower()
        if 'permission' in error_str or 'sudo' in error_str:
            error_message = (
                'Permission error while scanning. '
                'The WiFi scan requires appropriate permissions. '
                'Please ensure the application has necessary privileges.'
            )
        elif 'timeout' in error_str:
            error_message = (
                'WiFi scan timed out. '
                'The scan took too long to complete. '
                'This may happen if the WiFi interface is busy or in use.'
            )
        elif 'no wifi' in error_str or 'not available' in error_str:
            error_message = (
                'WiFi scanning tools are not available. '
                'Please ensure NetworkManager (nmcli) or iwlist is installed.'
            )

        return jsonify({
            'status': 'error',
            'message': error_message
        }), 500
@api_v3.route('/wifi/connect', methods=['POST'])
def connect_wifi():
    """Connect to a WiFi network.

    With the setup AP active this answers 202 at once and connects in the
    background (see _last_connect_attempt); otherwise it waits for the result.
    """
    global _last_connect_attempt
    try:
        from src.wifi_manager import WiFiManager

        data = request.get_json(silent=True)
        if not data:
            return jsonify({
                'status': 'error',
                'message': 'Request body is required'
            }), 400

        if 'ssid' not in data:
            return jsonify({
                'status': 'error',
                'message': 'SSID is required'
            }), 400

        ssid = data['ssid']
        if not ssid or not ssid.strip():
            return jsonify({
                'status': 'error',
                'message': 'SSID cannot be empty'
            }), 400

        ssid = ssid.strip()
        password = data.get('password', '') or ''

        wifi_manager = WiFiManager()

        if wifi_manager._is_ap_mode_active():
            with _connect_lock:
                if _last_connect_attempt and _last_connect_attempt['state'] == 'pending':
                    return jsonify({
                        'status': 'error',
                        'message': f"Already connecting to {_last_connect_attempt['ssid']}"
                    }), 409
                _last_connect_attempt = {
                    'ssid': ssid, 'state': 'pending', 'message': None,
                    'error_type': None, 'finished_at': None,
                }
            _spawn(lambda: _run_background_connect(ssid, password))
            return jsonify({
                'status': 'pending',
                'message': (
                    f'Connecting to {ssid}. The LEDMatrix-Setup network will turn off, '
                    'so this page will lose its connection.'
                ),
                'data': {'ssid': ssid},
            }), 202

        success, message = wifi_manager.connect_to_network(ssid, password)
        payload = _connect_result_payload(ssid, success, message)
        return jsonify(payload), (200 if success else 400)
    except Exception as e:
        logger.error("Error connecting to WiFi", exc_info=True)
        return jsonify({
            'status': 'error',
            'message': 'An error occurred; see logs for details', 'details': describe_exception(e)
        }), 500
@api_v3.route('/wifi/disconnect', methods=['POST'])
def disconnect_wifi():
    """Disconnect from the current WiFi network"""
    try:
        from src.wifi_manager import WiFiManager

        wifi_manager = WiFiManager()
        success, message = wifi_manager.disconnect_from_network()

        if success:
            return jsonify({
                'status': 'success',
                'message': message
            })
        else:
            return jsonify({
                'status': 'error',
                'message': message or 'Failed to disconnect from network'
            }), 400
    except Exception as e:
        logger.error("Error disconnecting from WiFi", exc_info=True)
        return jsonify({
            'status': 'error',
            'message': 'An error occurred; see logs for details', 'details': describe_exception(e)
        }), 500
@api_v3.route('/wifi/ap/enable', methods=['POST'])
def enable_ap_mode():
    """Enable access point mode"""
    try:
        from src.wifi_manager import WiFiManager

        wifi_manager = WiFiManager()
        _force_raw = (request.get_json(silent=True) or {}).get('force', False)
        force = _force_raw is True or (isinstance(_force_raw, str) and _force_raw.lower() in ('true', '1'))
        success, message = wifi_manager.enable_ap_mode(force=force)

        if success:
            return jsonify({
                'status': 'success',
                'message': message
            })
        else:
            return jsonify({
                'status': 'error',
                'message': message
            }), 400
    except Exception as e:
        logger.error("%s failed", request.path, exc_info=True)
        return jsonify({
            'status': 'error',
            'message': 'An error occurred; see logs for details',
            'details': describe_exception(e)
        }), 500
@api_v3.route('/wifi/ap/disable', methods=['POST'])
def disable_ap_mode():
    """Disable access point mode"""
    try:
        from src.wifi_manager import WiFiManager

        wifi_manager = WiFiManager()
        success, message = wifi_manager.disable_ap_mode()

        if success:
            return jsonify({
                'status': 'success',
                'message': message
            })
        else:
            return jsonify({
                'status': 'error',
                'message': message
            }), 400
    except Exception as e:
        logger.error("%s failed", request.path, exc_info=True)
        return jsonify({
            'status': 'error',
            'message': 'An error occurred; see logs for details',
            'details': describe_exception(e)
        }), 500
@api_v3.route('/wifi/ap/auto-enable', methods=['GET'])
def get_auto_enable_ap_mode():
    """Get auto-enable AP mode setting"""
    try:
        from src.wifi_manager import WiFiManager

        wifi_manager = WiFiManager()
        auto_enable = wifi_manager.config.get("auto_enable_ap_mode", True)  # Default: True (safe due to grace period)

        return jsonify({
            'status': 'success',
            'data': {
                'auto_enable_ap_mode': auto_enable
            }
        })
    except Exception as e:
        logger.error("%s failed", request.path, exc_info=True)
        return jsonify({
            'status': 'error',
            'message': 'An error occurred; see logs for details',
            'details': describe_exception(e)
        }), 500
@api_v3.route('/wifi/ap/auto-enable', methods=['POST'])
def set_auto_enable_ap_mode():
    """Set auto-enable AP mode setting"""
    try:
        from src.wifi_manager import WiFiManager

        data = request.get_json(silent=True)
        if data is None or 'auto_enable_ap_mode' not in data:
            return jsonify({
                'status': 'error',
                'message': 'auto_enable_ap_mode is required'
            }), 400

        auto_enable = _parse_bool_ish(data['auto_enable_ap_mode'])
        if auto_enable is None:
            return jsonify({
                'status': 'error',
                'message': 'auto_enable_ap_mode must be a boolean'
            }), 400

        wifi_manager = WiFiManager()
        wifi_manager.config["auto_enable_ap_mode"] = auto_enable
        wifi_manager._save_config()

        return jsonify({
            'status': 'success',
            'message': f'Auto-enable AP mode set to {auto_enable}',
            'data': {
                'auto_enable_ap_mode': auto_enable
            }
        })
    except Exception as e:
        logger.error("%s failed", request.path, exc_info=True)
        return jsonify({
            'status': 'error',
            'message': 'An error occurred; see logs for details',
            'details': describe_exception(e)
        }), 500
@api_v3.route('/wifi/radio', methods=['GET'])
def get_wifi_radio():
    """Get current WiFi radio state (enabled/disabled) and wired-fallback status."""
    try:
        from src.wifi_manager import WiFiManager

        wifi_manager = WiFiManager()
        state = wifi_manager.get_wifi_radio_state()

        return jsonify({
            'status': 'success',
            'data': state
        })
    except Exception as e:
        logger.error("Error getting WiFi radio state", exc_info=True)
        return jsonify({
            'status': 'error',
            'message': 'An error occurred; see logs for details', 'details': describe_exception(e)
        }), 500
@api_v3.route('/wifi/radio', methods=['POST'])
def set_wifi_radio():
    """Turn the WiFi radio on or off.

    Body: {"enabled": bool, "force": bool (optional)}. Disabling is refused
    unless Ethernet is connected or force=True, to avoid locking the user out
    of this web interface.
    """
    try:
        from src.wifi_manager import WiFiManager

        data = request.get_json(silent=True) or {}
        if 'enabled' not in data:
            return jsonify({
                'status': 'error',
                'message': 'enabled is required'
            }), 400

        # Parse defensively: bool("false") is True and a plain int never
        # matches `is True`, so `_parse_bool_ish` handles bool, string and
        # int 1/0 — the endpoint is a public contract, not just the shipped
        # UI (which always sends real JSON booleans). An unrecognized value
        # must be rejected, not silently disable the radio: this is the
        # route that can drop the caller's own connection to this interface.
        enabled = _parse_bool_ish(data['enabled'])
        if enabled is None:
            return jsonify({
                'status': 'error',
                'message': 'enabled must be a boolean'
            }), 400
        force = _parse_bool_ish(data.get('force', False))
        if force is None:
            return jsonify({
                'status': 'error',
                'message': 'force must be a boolean'
            }), 400

        wifi_manager = WiFiManager()
        success, message, reason = wifi_manager.set_wifi_radio(enabled, force=force)

        if success:
            return jsonify({
                'status': 'success',
                'message': message,
                'data': wifi_manager.get_wifi_radio_state()
            })
        else:
            return jsonify({
                'status': 'error',
                'message': message,
                'reason': reason
            }), 400
    except Exception as e:
        logger.error("Error setting WiFi radio state", exc_info=True)
        return jsonify({
            'status': 'error',
            'message': 'An error occurred; see logs for details', 'details': describe_exception(e)
        }), 500
