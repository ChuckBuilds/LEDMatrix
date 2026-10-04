"""Display control, on-demand playback and preview.

Routes decorate the shared `api_v3` Blueprint from the package `__init__`,
so their endpoint names are unchanged by living here.
"""
from web_interface.blueprints.api_v3 import (
    _QUIET_SOCKET_REASONS, _REPORTABLE_SOCKET_REASONS,  # noqa: F401 - tests read them here
    _coerce_to_bool, _ensure_display_service_running,
    _get_display_service_status, _socket_reason_code, _stop_display_service, api_v3,
    jsonify, logger, request, uuid,
)
from web_interface import display_preview, display_state
import web_interface.blueprints.api_v3 as _pkg
from src.ipc import client as control_client
# Read through the module rather than bound by value: tests patch these
# as module attributes, and a value binding would not see the patch.
# Several are also called from helpers that live in __init__, so the
# package is the only patch point that covers every caller.


def _cache_manager():
    """The web process's CacheManager, the one app.py puts on the blueprint.

    Created on first use when nothing set it (a test app, an embedder), and
    stored back on the blueprint so every route shares that one instance.
    """
    cache = getattr(api_v3, 'cache_manager', None)
    if cache is None:
        from src.cache_manager import CacheManager
        cache = api_v3.cache_manager = CacheManager()
    return cache




def _deliver_on_demand(payload):
    """Hand an on-demand request to the display: control socket, else mailbox.

    The socket (src/ipc) answers with an acknowledgement as soon as the
    display has the command queued for its render thread. Any failure -- no
    socket (the display is stopped or predates it), a timeout, a refusal --
    writes the file mailbox instead, exactly as before the socket existed;
    the display reads it within ON_DEMAND_POLL_INTERVAL. Both carry the same
    request_id, so a request that reached the display both ways (a reply
    that timed out after the command was queued) is still processed once.

    Returns ``(transport, socket_error)``: ``'socket'`` and None, or
    ``'mailbox'`` and the socket failure's reason code.
    """
    try:
        if payload['action'] == 'start':
            control_client.on_demand_start(
                payload['request_id'], payload.get('plugin_id'), payload.get('mode'),
                payload.get('duration'), bool(payload.get('pinned', False)))
        else:
            control_client.on_demand_stop(payload['request_id'])
        return 'socket', None
    except control_client.ControlError as e:
        reason = _socket_reason_code(e.reason)
        if reason in _QUIET_SOCKET_REASONS:
            logger.debug("On-demand %s via the mailbox: %s", payload['action'], e)
        else:
            logger.warning("Control socket did not take on-demand %s (%s); "
                           "using the mailbox", payload['action'], e)
    except Exception:  # never let the socket path break the route
        logger.exception("Control socket client failed; using the mailbox")
        reason = 'internal'
    _cache_manager().set('display_on_demand_request', payload)
    return 'mailbox', reason


def _withdraw_on_demand(request_id):
    """Take a start request the route has refused back out of the mailbox.

    The display reads the mailbox for an hour without looking at a
    request's age, so one left there after an error answer ran whenever the
    display next started. Only this request is removed: the mailbox is
    re-read and cleared only while it still holds this request_id, as the
    display's _consume_on_demand_request does, so a newer request posted in
    the meantime stays for the display to take.
    """
    cache = _cache_manager()
    try:
        current = cache.get('display_on_demand_request', max_age=3600, memory_ttl=0)
        if isinstance(current, dict) and current.get('request_id') == request_id:
            cache.delete('display_on_demand_request')
    except Exception:  # the route is answering an error already
        logger.warning("Could not withdraw on-demand request %s from the mailbox",
                       request_id, exc_info=True)


@api_v3.route('/display/current', methods=['GET'])
def get_display_current():
    """The latest display preview, as the /stream/display SSE stream sends it.

    ``data`` is ``{timestamp, width, height, image}``; ``image`` is the
    snapshot PNG base64-encoded, or null when there is none to show.
    """
    # Get display dimensions from config: the logical size DisplayManager
    # renders at, so double-sided setups preview one screen
    from src.display_geometry import logical_size
    try:
        config = api_v3.config_manager.load_config() if api_v3.config_manager else {}
        width, height = logical_size(config)
    except Exception:
        width, height = logical_size({})

    try:
        image = display_preview.read_snapshot_base64()
    except FileNotFoundError:
        image = None  # the display service has not written one yet
    except OSError:
        logger.warning("Could not read the display preview snapshot", exc_info=True)
        image = None

    return jsonify({'status': 'success',
                    'data': display_preview.preview_payload(width, height, image)})
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
    if not api_v3.plugin_catalog:
        return jsonify({'status': 'error', 'message': 'Plugin catalog not initialized'}), 500

    # Discovery is lazy and normally triggered by whichever endpoint runs
    # first, which is a person opening the dashboard. A caller that never
    # visits it would otherwise see an empty list.
    api_v3.plugin_catalog.discover_plugins()

    include_disabled = request.args.get('include_disabled') in ('1', 'true', 'True')
    full_config = api_v3.config_manager.load_config() if api_v3.config_manager else {}

    modes = []
    for plugin_id, manifest in sorted(api_v3.plugin_catalog.plugin_manifests.items()):
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
        plugin_modes = api_v3.plugin_catalog.get_plugin_display_modes(plugin_id) or [plugin_id]
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
@api_v3.route('/display/on-demand/status', methods=['GET'])
def get_on_demand_status():
    """Return the current on-demand display state.

    From the display's state stream over the control socket when it is
    available (``source: "socket"``), else the cache key it also writes
    (``source: "cache"``).
    """
    state = display_state.on_demand_state(display_state.read_state())
    source = 'socket'
    if state is None:
        source = 'cache'
        cache = _cache_manager()
        # memory_ttl=0: the display service writes this key, so only the file
        # is current. This process's memory tier would keep serving the first
        # copy it read for the full max_age -- "active" for two minutes after
        # the display had already stopped.
        state = cache.get('display_on_demand_state', max_age=120, memory_ttl=0)
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
            'service': service_status,
            'source': source,
        }
    })
@api_v3.route('/display/on-demand/start', methods=['POST'])
def start_on_demand_display():
    """Request the display controller to run a specific plugin on-demand."""
    data = request.get_json(silent=True) or {}
    plugin_id = data.get('plugin_id')
    mode = data.get('mode')
    duration = data.get('duration')
    # _coerce_to_bool: bool("false") is True, so a string "false" pinned the
    # mode or (re)started the service it asked to leave alone.
    pinned = _coerce_to_bool(data.get('pinned', False))
    start_service = _coerce_to_bool(data.get('start_service', True))

    if not plugin_id and not mode:
        return jsonify({'status': 'error', 'message': 'plugin_id or mode is required'}), 400

    resolved_plugin = plugin_id
    resolved_mode = mode

    if api_v3.plugin_catalog:
        if resolved_plugin and resolved_plugin not in _pkg._discovered_plugin_manifests(resolved_plugin):
            return jsonify({'status': 'error', 'message': f'Plugin {resolved_plugin} not found'}), 404

        if resolved_plugin and not resolved_mode:
            modes = api_v3.plugin_catalog.get_plugin_display_modes(resolved_plugin)
            resolved_mode = modes[0] if modes else resolved_plugin
        elif resolved_mode and not resolved_plugin:
            _pkg._discovered_plugin_manifests()
            resolved_plugin = api_v3.plugin_catalog.find_plugin_for_mode(resolved_mode)
            if not resolved_plugin:
                # Not among what was discovered: the plugin that declares
                # it may have been installed since. Scan once more.
                _pkg._discovered_plugin_manifests(rescan=True)
                resolved_plugin = api_v3.plugin_catalog.find_plugin_for_mode(resolved_mode)
            if not resolved_plugin:
                return jsonify({'status': 'error', 'message': f'Mode {resolved_mode} not found'}), 404

    # On-demand works with disabled plugins: the running display loads one
    # for the session and unloads it afterwards, leaving config.json alone
    # (DisplayController._load_plugin_for_on_demand). Logged for debugging.
    if api_v3.config_manager and resolved_plugin:
        config = api_v3.config_manager.load_config()
        plugin_config = config.get(resolved_plugin, {})
        if 'enabled' in plugin_config and not plugin_config.get('enabled', False):
            logger.info(
                "On-demand request for disabled plugin '%s' - will be temporarily enabled",
                resolved_plugin,
            )

    # Checked before anything is delivered: a request posted and then
    # refused here stayed in the mailbox and ran when the display was next
    # started, long after the caller was told it had failed.
    service_status = _get_display_service_status()

    if not service_status.get('active') and not start_service:
        return jsonify({
            'status': 'error',
            'message': 'Display service is not running. Please start the display service or enable "Start Service" option.',
            'service_status': service_status
        }), 400

    # Deliver the request over the control socket, or post it to the
    # mailbox the display process polls (DisplayController.
    # _poll_on_demand_requests). Done before any service start: a stopped
    # display has no socket, so the request lands in the mailbox, where a
    # freshly started display finds it on its first poll.
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
    transport, socket_error = _deliver_on_demand(request_payload)

    # start_service means "start it if it is not running", as the UI's
    # checkbox says; _ensure_display_service_running leaves a running service
    # alone. This used to stop a running service, sleep 1.5s and start it
    # again, so every on-demand or "Preview on display" click -- and every
    # MQTT on-demand command, which posts here with the default -- cold-
    # restarted the display process: every plugin reloaded and the panel was
    # blank for seconds. The restart bought nothing. The running process
    # reads this mailbox every ON_DEMAND_POLL_INTERVAL (0.25s), from its
    # dwell sleep, its render loops and Vegas's interrupt check as well as
    # the main loop, and a restarted one got the request the same way: the
    # startup path only restores a session the display itself saved
    # (display_on_demand_config), so it loaded nothing it would not have had.
    service_result = None
    if start_service:
        service_result = _ensure_display_service_running()
        # Check if service actually started
        if service_result and not service_result.get('active'):
            if transport == 'mailbox':
                _withdraw_on_demand(request_id)
            return jsonify({
                'status': 'error',
                'message': 'Failed to start display service. Please check service logs or start it manually.',
                'service_result': service_result
            }), 500

    response_data = {
        'request_id': request_id,
        'plugin_id': resolved_plugin,
        'mode': resolved_mode,
        'duration': duration,
        'pinned': pinned,
        'service': service_result,
        'transport': transport,
    }
    if socket_error:
        response_data['socket_error'] = socket_error
    return jsonify({'status': 'success', 'data': response_data})
@api_v3.route('/display/on-demand/stop', methods=['POST'])
def stop_on_demand_display():
    """Request the display controller to stop on-demand mode."""
    data = request.get_json(silent=True) or {}
    # _coerce_to_bool: bool("false") is True, which stopped the service.
    stop_service = _coerce_to_bool(data.get('stop_service', False))

    # The running display takes the stop over the control socket, or reads
    # it from the mailbox within ON_DEMAND_POLL_INTERVAL, and resumes normal
    # rotation in place (_clear_on_demand); nothing is restarted.
    request_id = data.get('request_id') or str(uuid.uuid4())
    request_payload = {
        'request_id': request_id,
        'action': 'stop',
        'timestamp': _pkg.time.time()
    }
    transport, socket_error = _deliver_on_demand(request_payload)

    service_result = None
    if stop_service:
        service_result = _stop_display_service()

    response_data = {
        'request_id': request_id,
        'service': service_result,
        'transport': transport,
    }
    if socket_error:
        response_data['socket_error'] = socket_error
    return jsonify({'status': 'success', 'data': response_data})
@api_v3.route('/display/current-status', methods=['GET'])
def get_current_display_status():
    """Return the display mode/plugin currently intended to be shown.

    Read from the display's state stream over the control socket when it is
    available (``source: "socket"``). Otherwise from what the display
    publishes to the shared cache (display_controller._publish_current_mode_state)
    when the active mode changes (``source: "cache"``).
    """
    state = display_state.current_status(display_state.read_state())
    source = 'socket'
    if state is None:
        source = 'cache'
        cache = _cache_manager()
        # memory_ttl=0: written by the display service; see get_on_demand_status.
        state = cache.get('display_current_state', max_age=120, memory_ttl=0)
    if state is None:
        state = {
            'mode': None,
            'plugin_id': None,
            'last_updated': None,
        }
    return jsonify({'status': 'success', 'data': dict(state, source=source)})
