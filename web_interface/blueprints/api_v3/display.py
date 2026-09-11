"""Display control, on-demand playback and preview.

Routes decorate the shared `api_v3` Blueprint from ._common, so their
endpoint names are unchanged by living here.
"""
from web_interface.blueprints.api_v3 import (
    _ensure_cache_manager, _ensure_display_service_running,
    _get_display_service_status, _stop_display_service, api_v3,
    describe_exception, jsonify, logger, os, request, uuid,
)
import web_interface.blueprints.api_v3 as _pkg
# Read through the module rather than bound by value: tests patch these
# as module attributes, and a value binding would not see the patch.
# Several are also called from helpers that live in __init__, so the
# package is the only patch point that covers every caller.


@api_v3.route('/display/current', methods=['GET'])
def get_display_current():
    """Get current display state"""
    try:
        import base64
        from PIL import Image
        import io

        snapshot_path = "/tmp/led_matrix_preview.png"

        # Get display dimensions from config
        try:
            if api_v3.config_manager:
                main_config = api_v3.config_manager.load_config()
                hardware_config = main_config.get('display', {}).get('hardware', {})
                cols = hardware_config.get('cols', 64)
                chain_length = hardware_config.get('chain_length', 2)
                rows = hardware_config.get('rows', 32)
                parallel = hardware_config.get('parallel', 1)
                width = cols * chain_length
                height = rows * parallel
            else:
                width = 128
                height = 64
        except Exception:
            width = 128
            height = 64

        # Try to read snapshot file
        image_data = None
        if os.path.exists(snapshot_path):
            try:
                with Image.open(snapshot_path) as img:
                    # Convert to PNG and encode as base64
                    buffer = io.BytesIO()
                    img.save(buffer, format='PNG')
                    image_data = base64.b64encode(buffer.getvalue()).decode('utf-8')
            except Exception as img_err:
                # File might be being written or corrupted, return None
                pass

        display_data = {
            'timestamp': _pkg.time.time(),
            'width': width,
            'height': height,
            'image': image_data  # Base64 encoded image data or None if unavailable
        }
        return jsonify({'status': 'success', 'data': display_data})
    except Exception as e:
        logger.error('Unhandled exception', exc_info=True)
        return jsonify({'status': 'error', 'message': 'An error occurred; see logs for details', 'details': describe_exception(e)}), 500
@api_v3.route('/display/modes', methods=['GET'])
def get_display_modes():
    """Every display mode that can be requested on-demand, with its plugin.

    /plugins/installed carries no mode information, so anything driving the
    display from outside the web UI (the Home Assistant MQTT bridge, a script)
    had to read each plugin's manifest.json off disk and reimplement the
    fallbacks in PluginManager.get_plugin_display_modes to do it. This is the
    same list the force-display dialog offers, from the source that owns it.

    Knowing each mode's plugin_id also matters because /display/on-demand/start
    falls back to find_plugin_for_mode when plugin_id is omitted, and that
    lookup only sees modes declared in a static manifest -- a plugin whose
    modes are generated (each installed Starlark app is one) 404s there.
    Sending the plugin_id from this list skips the lookup entirely.

    Query params:
        include_disabled: '1' to list modes of disabled plugins too. They can
            still be requested on-demand -- the controller enables the plugin
            for the duration -- so they are reported with enabled: false
            rather than omitted.
    """
    try:
        if not api_v3.plugin_manager:
            return jsonify({'status': 'error', 'message': 'Plugin manager not initialized'}), 500

        # Discovery is lazy and normally triggered by whichever endpoint runs
        # first, which is a person opening the dashboard. A caller that never
        # visits it would otherwise see an empty list.
        api_v3.plugin_manager.discover_plugins()

        include_disabled = request.args.get('include_disabled') in ('1', 'true', 'True')
        full_config = api_v3.config_manager.load_config() if api_v3.config_manager else {}

        modes = []
        for plugin_id, manifest in sorted(api_v3.plugin_manager.plugin_manifests.items()):
            # A hand-edited or migrated config.json can hold a non-dict under a
            # plugin id; DisplayController._reconcile guards the same shape, so
            # it happens in practice. Without this, .get() raises AttributeError,
            # the loop aborts and the endpoint answers 500 with no modes at all
            # -- one bad section would blank every entity the MQTT bridge builds
            # from this list.
            plugin_config = full_config.get(plugin_id)
            if not isinstance(plugin_config, dict):
                if plugin_config is not None:
                    logger.warning(
                        "Config for plugin %r is %s, not an object; treating it as disabled",
                        plugin_id, type(plugin_config).__name__)
                plugin_config = {}
            enabled = bool(plugin_config.get('enabled', False))
            if not enabled and not include_disabled:
                continue
            plugin_name = (manifest or {}).get('name') or plugin_id
            plugin_modes = api_v3.plugin_manager.get_plugin_display_modes(plugin_id) or [plugin_id]
            for mode in plugin_modes:
                # A single-mode plugin's mode is the plugin, so its own name is
                # the readable label. Multi-mode plugins have no per-mode name
                # anywhere, so the raw mode string is the only thing to show.
                modes.append({
                    'mode': mode,
                    'plugin_id': plugin_id,
                    'plugin_name': plugin_name,
                    'name': plugin_name if len(plugin_modes) == 1 else mode,
                    'enabled': enabled,
                })

        return jsonify({'status': 'success', 'data': {'modes': modes}})
    except Exception as exc:
        # describe_exception, not a bare message: test_web_error_detail.py
        # enforces that every handler here returns it, because a device whose
        # storage is failing otherwise answers "see logs for details" from the
        # log viewer too. It redacts credentials out of the exception text.
        # CodeQL flags this as stack-trace exposure across all ~75 handlers;
        # it is the project's deliberate, reviewed trade-off.
        logger.error('Error in get_display_modes', exc_info=True)
        return jsonify({'status': 'error', 'message': 'An error occurred; see logs for details', 'details': describe_exception(exc)}), 500
@api_v3.route('/display/on-demand/status', methods=['GET'])
def get_on_demand_status():
    """Return the current on-demand display state."""
    try:
        cache = _ensure_cache_manager()
        state = cache.get('display_on_demand_state', max_age=120)
        if state is None:
            state = {
                'active': False,
                'status': 'idle',
                'last_updated': None
            }
        service_status = _get_display_service_status()
        return jsonify({
            'status': 'success',
            'data': {
                'state': state,
                'service': service_status
            }
        })
    except Exception as exc:
        logger.error('Error in get_on_demand_status', exc_info=True)
        return jsonify({'status': 'error', 'message': 'An error occurred; see logs for details', 'details': describe_exception(exc)}), 500
@api_v3.route('/display/on-demand/start', methods=['POST'])
def start_on_demand_display():
    """Request the display controller to run a specific plugin on-demand."""
    try:
        data = request.get_json(silent=True) or {}
        plugin_id = data.get('plugin_id')
        mode = data.get('mode')
        duration = data.get('duration')
        pinned = bool(data.get('pinned', False))
        start_service = data.get('start_service', True)

        if not plugin_id and not mode:
            return jsonify({'status': 'error', 'message': 'plugin_id or mode is required'}), 400

        resolved_plugin = plugin_id
        resolved_mode = mode

        if api_v3.plugin_manager:
            if resolved_plugin and resolved_plugin not in api_v3.plugin_manager.plugin_manifests:
                return jsonify({'status': 'error', 'message': f'Plugin {resolved_plugin} not found'}), 404

            if resolved_plugin and not resolved_mode:
                modes = api_v3.plugin_manager.get_plugin_display_modes(resolved_plugin)
                resolved_mode = modes[0] if modes else resolved_plugin
            elif resolved_mode and not resolved_plugin:
                resolved_plugin = api_v3.plugin_manager.find_plugin_for_mode(resolved_mode)
                if not resolved_plugin:
                    return jsonify({'status': 'error', 'message': f'Mode {resolved_mode} not found'}), 404

        # Note: On-demand can work with disabled plugins - the display controller
        # will temporarily enable them during initialization if needed
        # We don't block the request here, but log it for debugging
        if api_v3.config_manager and resolved_plugin:
            config = api_v3.config_manager.load_config()
            plugin_config = config.get(resolved_plugin, {})
            if 'enabled' in plugin_config and not plugin_config.get('enabled', False):
                logger.info(
                    "On-demand request for disabled plugin '%s' - will be temporarily enabled",
                    resolved_plugin,
                )

        # Set the on-demand request in cache FIRST (before starting service)
        # This ensures the request is available when the service starts/restarts
        cache = _ensure_cache_manager()
        request_id = data.get('request_id') or str(uuid.uuid4())
        request_payload = {
            'request_id': request_id,
            'action': 'start',
            'plugin_id': resolved_plugin,
            'mode': resolved_mode,
            'duration': duration,
            'pinned': pinned,
            'timestamp': _pkg.time.time()
        }
        cache.set('display_on_demand_request', request_payload)

        # Check if display service is running (or will be started)
        service_status = _get_display_service_status()
        service_was_running = service_status.get('active', False)
        
        # Stop the display service first to ensure clean state when we will restart it
        if service_was_running and start_service:
            import time as time_module
            logger.debug("Stopping display service before starting on-demand mode")
            _stop_display_service()
            # Wait a brief moment for the service to fully stop
            time_module.sleep(1.5)
            logger.debug("Display service stopped, now starting with on-demand request")

        if not service_status.get('active') and not start_service:
            return jsonify({
                'status': 'error',
                'message': 'Display service is not running. Please start the display service or enable "Start Service" option.',
                'service_status': service_status
            }), 400

        service_result = None
        if start_service:
            service_result = _ensure_display_service_running()
            # Check if service actually started
            if service_result and not service_result.get('active'):
                return jsonify({
                    'status': 'error',
                    'message': 'Failed to start display service. Please check service logs or start it manually.',
                    'service_result': service_result
                }), 500
            
            # Service was restarted (or started fresh) with on-demand request in cache
            # The display controller will read the request during initialization or when it polls

        response_data = {
            'request_id': request_id,
            'plugin_id': resolved_plugin,
            'mode': resolved_mode,
            'duration': duration,
            'pinned': pinned,
            'service': service_result
        }
        return jsonify({'status': 'success', 'data': response_data})
    except Exception as exc:
        logger.error('Error in start_on_demand_display', exc_info=True)
        return jsonify({'status': 'error', 'message': 'An error occurred; see logs for details', 'details': describe_exception(exc)}), 500
@api_v3.route('/display/on-demand/stop', methods=['POST'])
def stop_on_demand_display():
    """Request the display controller to stop on-demand mode."""
    try:
        data = request.get_json(silent=True) or {}
        stop_service = data.get('stop_service', False)

        # Set the stop request in cache FIRST
        # The display controller will poll this and restart without the on-demand filter
        cache = _ensure_cache_manager()
        request_id = data.get('request_id') or str(uuid.uuid4())
        request_payload = {
            'request_id': request_id,
            'action': 'stop',
            'timestamp': _pkg.time.time()
        }
        cache.set('display_on_demand_request', request_payload)
        
        # Note: The display controller's _clear_on_demand() will handle the restart
        # to restore normal operation with all plugins
        
        service_result = None
        if stop_service:
            service_result = _stop_display_service()

        return jsonify({
            'status': 'success',
            'data': {
                'request_id': request_id,
                'service': service_result
            }
        })
    except Exception as exc:
        logger.error('Error in stop_on_demand_display', exc_info=True)
        return jsonify({'status': 'error', 'message': 'An error occurred; see logs for details', 'details': describe_exception(exc)}), 500
@api_v3.route('/display/current-status', methods=['GET'])
def get_current_display_status():
    """Return the display mode/plugin currently intended to be shown.

    Published by the display process (display_controller._publish_current_mode_state)
    to the shared cache whenever the active mode changes, so the web UI (e.g. the
    System Logs page) can show what's on screen without querying the display
    process directly.
    """
    try:
        cache = _ensure_cache_manager()
        state = cache.get('display_current_state', max_age=120)
        if state is None:
            state = {
                'mode': None,
                'plugin_id': None,
                'last_updated': None,
            }
        return jsonify({'status': 'success', 'data': state})
    except Exception as e:
        logger.error('Error in get_current_display_status', exc_info=True)
        return jsonify({'status': 'error', 'message': 'An error occurred; see logs for details', 'details': describe_exception(e)}), 500
