"""Routes with no larger group of their own: errors, integrations,
cache, sync, skins, logs, health and hardware.

Routes decorate the shared `api_v3` Blueprint from ._common, so their
endpoint names are unchanged by living here.
"""
from web_interface.blueprints.api_v3 import (
    _coerce_to_bool,
    ErrorCode, Path, _JOURNALCTL, _MQTT_BRIDGE_CONFIG, _MQTT_BRIDGE_DEFAULTS,
    _MQTT_BRIDGE_DIR, _SUDO, _coerce_mqtt_bridge_value,
    _get_display_service_status, _mqtt_bridge_service_state,
    _read_mqtt_bridge_config, api_v3, contextlib, describe_exception,
    error_response, get_error_aggregator, json, jsonify, logger, os, request,
    subprocess, success_response, tempfile,
)
import web_interface.blueprints.api_v3 as _pkg
# Read through the module rather than bound by value: tests patch these
# as module attributes, and a value binding would not see the patch.
# Several are also called from helpers that live in __init__, so the
# package is the only patch point that covers every caller.


@api_v3.route('/health', methods=['GET'])
def get_health():
    """Get system health status"""
    try:
        health_status = {
            'status': 'healthy',
            'timestamp': _pkg.time.time(),
            'services': {},
            'checks': {}
        }

        # Check web interface service
        # Stamp the start _pkg.time before measuring against it -- reading it with a
        # fallback of _pkg.time.time() and only assigning afterwards made the very
        # first call subtract two separate clock reads, reporting a small
        # negative uptime.
        if not hasattr(get_health, '_start_time'):
            get_health._start_time = _pkg.time.time()
        health_status['services']['web_interface'] = {
            'status': 'running',
            'uptime_seconds': _pkg.time.time() - get_health._start_time
        }

        # Check display service
        display_service_status = _get_display_service_status()
        health_status['services']['display_service'] = {
            'status': 'active' if display_service_status.get('active') else 'inactive',
            'details': display_service_status
        }

        # Check config file accessibility
        try:
            if api_v3.config_manager:
                test_config = api_v3.config_manager.load_config()
                health_status['checks']['config_file'] = {
                    'status': 'accessible',
                    'readable': True
                }
            else:
                health_status['checks']['config_file'] = {
                    'status': 'unknown',
                    'readable': False
                }
        except Exception as e:
            health_status['checks']['config_file'] = {
                'status': 'error',
                'readable': False,
                'error': 'see logs for details'
            }

        # Check plugin system
        try:
            if api_v3.plugin_manager:
                # Try to discover plugins (lightweight check)
                plugin_count = len(api_v3.plugin_manager.get_available_plugins()) if hasattr(api_v3.plugin_manager, 'get_available_plugins') else 0
                health_status['checks']['plugin_system'] = {
                    'status': 'operational',
                    'plugin_count': plugin_count
                }
            else:
                health_status['checks']['plugin_system'] = {
                    'status': 'not_initialized'
                }
        except Exception as e:
            health_status['checks']['plugin_system'] = {
                'status': 'error',
                'error': 'see logs for details'
            }

        # Check hardware connectivity (if display manager available)
        try:
            snapshot_path = "/tmp/led_matrix_preview.png"
            if os.path.exists(snapshot_path):
                # Check if snapshot is recent (updated in last 60 seconds)
                mtime = os.path.getmtime(snapshot_path)
                age_seconds = _pkg.time.time() - mtime
                health_status['checks']['hardware'] = {
                    'status': 'connected' if age_seconds < 60 else 'stale',
                    'snapshot_age_seconds': round(age_seconds, 1)
                }
            else:
                health_status['checks']['hardware'] = {
                    'status': 'no_snapshot',
                    'note': 'Display service may not be running'
                }
        except Exception as e:
            health_status['checks']['hardware'] = {
                'status': 'unknown',
                'error': 'see logs for details'
            }

        # Determine overall health
        all_healthy = all(
            check.get('status') in ['accessible', 'operational', 'connected', 'running', 'active']
            for check in health_status['checks'].values()
        )

        if not all_healthy:
            health_status['status'] = 'degraded'

        return jsonify({'status': 'success', 'data': health_status})
    except Exception as e:
        logger.error("%s failed", request.path, exc_info=True)
        return jsonify({
            'status': 'error',
            'message': 'An error occurred; see logs for details',
            'details': describe_exception(e),
            'data': {'status': 'unhealthy'}
        }), 500
@api_v3.route('/hardware/status', methods=['GET'])
def get_hardware_status():
    """Return LED matrix hardware initialization status written by display_manager at startup."""
    status_path = "/tmp/led_matrix_hw_status.json"  # nosec B108
    try:
        with open(status_path) as f:
            hw_data = json.load(f)
        return jsonify({"status": "success", "data": hw_data})
    except FileNotFoundError:
        return jsonify({"status": "success", "data": {"ok": None, "error": "Display service not yet started"}})
    except PermissionError:
        logger.warning("Permission denied reading hardware status file; display service may be running as a different user")
        return jsonify({"status": "success", "data": {"ok": False, "error": "Hardware status temporarily unavailable"}})
    except json.JSONDecodeError:
        logger.error("Failed to parse hardware status file", exc_info=True)
        return jsonify({"status": "success", "data": {"ok": False, "error": "Hardware status file corrupted"}})
    except Exception:
        logger.error("Unexpected error reading hardware status", exc_info=True)
        return jsonify({"status": "error", "message": "Unable to read hardware status"}), 500
@api_v3.route('/skins', methods=['GET'])
def list_skins():
    """List installed visual skins (docs/SKIN_SYSTEM.md).

    Optional ?plugin_id=... filters to skins matching that plugin.
    """
    try:
        from src.skin_system import skin_runtime

        plugin_id = request.args.get('plugin_id')
        if plugin_id:
            skins = skin_runtime.skins_for_plugin(plugin_id)
        else:
            # The discovery cache self-invalidates on directory/manifest
            # mtime changes, so no force_refresh — keeps Pi disk I/O down.
            skins = skin_runtime.discover_skins()

        payload = []
        for skin_id, manifest in sorted(skins.items()):
            skin_dir = Path(manifest['_skin_dir'])
            preview = manifest.get('preview')
            payload.append({
                'id': skin_id,
                'name': manifest.get('name', skin_id),
                'version': manifest.get('version'),
                'author': manifest.get('author'),
                'description': manifest.get('description', ''),
                'skin_api_version': manifest.get('skin_api_version'),
                'targets': manifest.get('targets', {}),
                'modes': manifest.get('modes', []),
                'has_preview': bool(preview and (skin_dir / preview).is_file()),
            })
        return jsonify({'status': 'success', 'data': {'skins': payload}})
    except Exception as e:
        logger.error('Error in list_skins', exc_info=True)
        return jsonify({'status': 'error', 'message': 'An error occurred; see logs for details', 'details': describe_exception(e)}), 500
@api_v3.route('/logs', methods=['GET'])
def get_logs():
    """Get system logs from journalctl"""
    try:
        if not _JOURNALCTL:
            return jsonify({'status': 'error', 'message': 'journalctl not found on this system'}), 503
        # Get recent logs from journalctl
        _cmd = ([_SUDO, _JOURNALCTL] if _SUDO else [_JOURNALCTL]) + [
            '-u', 'ledmatrix.service', '-u', 'ledmatrix-web.service',
            '-n', '100', '--no-pager', '--output=short-iso']
        result = subprocess.run(
            _cmd,
            capture_output=True,
            text=True,
            timeout=5
        )

        if result.returncode == 0:
            logs_text = result.stdout.strip()
            return jsonify({
                'status': 'success',
                'data': {
                    'logs': logs_text if logs_text else 'No logs available from ledmatrix or ledmatrix-web service'
                }
            })
        else:
            return jsonify({
                'status': 'error',
                'message': f'Failed to get logs: {result.stderr}'
            }), 500

    except subprocess.TimeoutExpired:
        return jsonify({
            'status': 'error',
            'message': 'Timeout while fetching logs'
        }), 500
    except Exception as e:
        logger.error("%s failed", request.path, exc_info=True)
        return jsonify({
            'status': 'error',
            'message': 'An error occurred; see logs for details',
            'details': describe_exception(e)
        }), 500
# Multi-Display Sync Endpoints
@api_v3.route('/sync/status', methods=['GET'])
def get_sync_status():
    """Return live multi-display sync status written by the display process."""
    import os as _os
    status_file = "/tmp/led_matrix_sync_status.json"
    # Also surface config so the UI can show the configured role even before
    # the display process has written a status file.
    cfg_role = "standalone"
    cfg_port = 5765
    if api_v3.config_manager:
        try:
            cfg = api_v3.config_manager.load_config().get("sync", {})
            cfg_role = cfg.get("role", "standalone")
            cfg_port = int(cfg.get("port", 5765))
        except Exception:
            pass

    if _os.path.exists(status_file):
        try:
            with open(status_file) as f:
                live = json.load(f)
            return jsonify({"status": "success", "data": live})
        except Exception:
            pass

    # Status file not yet written — return config-only placeholder
    return jsonify({
        "status": "success",
        "data": {
            "role": cfg_role,
            "port": cfg_port,
            "state": "starting",
        }
    })
@api_v3.route('/cache/list', methods=['GET'])
def list_cache_files():
    """List all cache files with metadata"""
    try:
        if not api_v3.cache_manager:
            # Initialize cache manager if not already initialized
            from src.cache_manager import CacheManager
            api_v3.cache_manager = CacheManager()

        cache_files = api_v3.cache_manager.list_cache_files()
        cache_dir = api_v3.cache_manager.get_cache_dir()

        return jsonify({
            'status': 'success',
            'data': {
                'cache_files': cache_files,
                'cache_dir': cache_dir,
                'total_files': len(cache_files)
            }
        })
    except Exception as e:
        logger.error('Error in list_cache_files', exc_info=True)
        return jsonify({'status': 'error', 'message': 'An error occurred; see logs for details', 'details': describe_exception(e)}), 500
@api_v3.route('/cache/delete', methods=['POST'])
def delete_cache_file():
    """Delete a specific cache file by key"""
    try:
        if not api_v3.cache_manager:
            # Initialize cache manager if not already initialized
            from src.cache_manager import CacheManager
            api_v3.cache_manager = CacheManager()

        data = request.get_json(silent=True)
        if not data or 'key' not in data:
            return jsonify({'status': 'error', 'message': 'cache key is required'}), 400

        cache_key = data['key']

        # Delete the cache file
        api_v3.cache_manager.clear_cache(cache_key)

        return jsonify({
            'status': 'success',
            'message': f'Cache file for key "{cache_key}" deleted successfully'
        })
    except Exception as e:
        logger.error('Error in delete_cache_file', exc_info=True)
        return jsonify({'status': 'error', 'message': 'An error occurred; see logs for details', 'details': describe_exception(e)}), 500
@api_v3.route('/errors/summary', methods=['GET'])
def get_error_summary():
    """
    Get summary of all errors for monitoring and debugging.

    Returns error counts, detected patterns, and recent errors.
    """
    try:
        aggregator = get_error_aggregator()
        summary = aggregator.get_error_summary()
        return success_response(data=summary, message="Error summary retrieved")
    except Exception as e:
        logger.error(f"Error getting error summary: {e}", exc_info=True)
        return error_response(
            error_code=ErrorCode.SYSTEM_ERROR,
            message="Failed to retrieve error summary",
            status_code=500
        )
@api_v3.route('/errors/plugin/<plugin_id>', methods=['GET'])
def get_plugin_errors(plugin_id):
    """
    Get error health status for a specific plugin.

    Args:
        plugin_id: Plugin identifier

    Returns health status and error statistics for the plugin.
    """
    try:
        aggregator = get_error_aggregator()
        health = aggregator.get_plugin_health(plugin_id)
        return success_response(data=health, message="Plugin health retrieved")
    except Exception as e:
        logger.error(f"Error getting plugin health for {plugin_id}: {e}", exc_info=True)
        return error_response(
            error_code=ErrorCode.SYSTEM_ERROR,
            message=f"Failed to retrieve health for plugin {plugin_id}",
            status_code=500
        )
@api_v3.route('/errors/clear', methods=['POST'])
def clear_old_errors():
    """
    Clear error records older than specified age.

    Request body (optional):
        max_age_hours: Maximum age in hours (default: 24, max: 8760 = 1 year)
    """
    try:
        data = request.get_json(silent=True) or {}
        raw_max_age = data.get('max_age_hours', 24)

        # Validate and coerce max_age_hours
        try:
            max_age_hours = int(raw_max_age)
            if max_age_hours < 1:
                return error_response(
                    error_code=ErrorCode.INVALID_INPUT,
                    message="max_age_hours must be at least 1",
                    context={'provided_value': raw_max_age},
                    status_code=400
                )
            if max_age_hours > 8760:  # 1 year max
                return error_response(
                    error_code=ErrorCode.INVALID_INPUT,
                    message="max_age_hours cannot exceed 8760 (1 year)",
                    context={'provided_value': raw_max_age},
                    status_code=400
                )
        except (ValueError, TypeError, OverflowError):
            return error_response(
                error_code=ErrorCode.INVALID_INPUT,
                message="max_age_hours must be a valid integer",
                context={'provided_value': str(raw_max_age)},
                status_code=400
            )

        aggregator = get_error_aggregator()
        cleared_count = aggregator.clear_old_records(max_age_hours=max_age_hours)

        return success_response(
            data={'cleared_count': cleared_count},
            message=f"Cleared {cleared_count} error records older than {max_age_hours} hours"
        )
    except Exception as e:
        logger.error(f"Error clearing old errors: {e}", exc_info=True)
        return error_response(
            error_code=ErrorCode.SYSTEM_ERROR,
            message="Failed to clear old errors",
            status_code=500
        )


@api_v3.route('/integrations/mqtt-bridge', methods=['GET'])
def get_mqtt_bridge():
    """Bridge service state and its settings, minus the password."""
    try:
        config = _read_mqtt_bridge_config()
        password = config.get('mqtt_password')
        safe = {key: config.get(key, default)
                for key, default in _MQTT_BRIDGE_DEFAULTS.items()}
        return jsonify({
            'status': 'success',
            'data': {
                'service': _mqtt_bridge_service_state(),
                'config_exists': _MQTT_BRIDGE_CONFIG.is_file(),
                'config_path': str(_MQTT_BRIDGE_CONFIG),
                'config': safe,
                # Enough to render "a password is set" without disclosing it.
                'password_set': bool(password),
                'env_override_prefix': 'LEDMATRIX_MQTT_',
            }
        })
    except Exception as e:
        logger.exception('Error reading MQTT bridge settings')
        return jsonify({'status': 'error', 'message': 'Could not read bridge settings',
                        'details': describe_exception(e)}), 500

@api_v3.route('/integrations/mqtt-bridge/config', methods=['PUT'])
def update_mqtt_bridge_config():
    """Write bridge_config.json.

    The password is write-only: omit it to leave whatever is stored alone, send
    a value to replace it, or send clear_password to remove it. It is never
    returned by the GET above, so a form that round-tripped it would otherwise
    have to blank it on every save.
    """
    try:
        # No `or {}` here: get_json(silent=True) returns None for a missing or
        # unparseable body, and `None or {}` produced an empty dict that then
        # satisfied the isinstance check below -- so malformed JSON, `null`,
        # `[]` and `false` all reported success while applying nothing.
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({'status': 'error', 'message': 'Body must be a JSON object'}), 400

        config = _read_mqtt_bridge_config()
        existing_password = config.get('mqtt_password')

        updates = {}
        for key in _MQTT_BRIDGE_DEFAULTS:
            if key not in data:
                continue
            value, err = _coerce_mqtt_bridge_value(key, data[key])
            if err:
                return jsonify({'status': 'error', 'message': err}), 400
            updates[key] = value

        config.update(updates)

        # Coerced, not merely truthy: the string "false" is truthy in Python,
        # so a client echoing the field back as a string would have wiped a
        # stored password it meant to keep.
        if _coerce_to_bool(data.get('clear_password')):
            config['mqtt_password'] = None
        elif 'mqtt_password' in data and str(data['mqtt_password']) != '':
            new_password = str(data['mqtt_password'])
            if len(new_password) > 300:
                return jsonify({'status': 'error', 'message': 'Password is too long'}), 400
            config['mqtt_password'] = new_password
        else:
            config['mqtt_password'] = existing_password

        if config.get('mqtt_password') and not config.get('mqtt_tls'):
            logger.warning('MQTT bridge: a password is set without TLS; '
                           'credentials will cross the network in cleartext')

        _MQTT_BRIDGE_DIR.mkdir(parents=True, exist_ok=True)
        # Write via a temp file in the same directory so a crash mid-write
        # cannot leave a half-written config the bridge would refuse to load.
        fd, tmp_path = tempfile.mkstemp(dir=str(_MQTT_BRIDGE_DIR), prefix='.bridge_config.')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as handle:
                json.dump(config, handle, indent=2, sort_keys=True)
                handle.write('\n')
            os.chmod(tmp_path, 0o600)
            os.replace(tmp_path, _MQTT_BRIDGE_CONFIG)
        except Exception:
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)
            raise

        service = _mqtt_bridge_service_state()
        message = 'Bridge settings saved.'
        if service['active']:
            message += ' Restart the bridge for them to take effect.'
        return jsonify({'status': 'success', 'message': message,
                        'data': {'password_set': bool(config.get('mqtt_password')),
                                 'restart_required': service['active']}})
    except Exception as e:
        logger.exception('Error saving MQTT bridge settings')
        return jsonify({'status': 'error', 'message': 'Could not save bridge settings',
                        'details': describe_exception(e)}), 500
