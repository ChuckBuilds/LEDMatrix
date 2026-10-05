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
from web_interface import display_preview, display_state, on_demand_dispatch
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




#: How long a start is sent again to a service that systemd reports running
#: but that has no socket yet (a display still loading its plugins, or one
#: someone else just restarted). A cold start gets the dispatcher's own
#: START_WAIT_SECONDS. Either way the route answers at once (202) and the
#: web process's dispatcher does the waiting.
ON_DEMAND_SOCKET_WAIT_RUNNING_SECONDS = 10.0


def _dispatcher():
    """The web process's on-demand dispatcher (web_interface/on_demand_dispatch.py)."""
    return on_demand_dispatch.get_dispatcher(_send_on_demand)


def _pending_start_state():
    """A start the dispatcher is still delivering, one it delivered but the
    display has not acted on yet, or one it gave up on: what the status
    routes report instead of the display's own state (see _shadows). None
    when there is none.

    A delivered start reads as ``status: "starting"`` with ``delivered:
    true`` for at most DELIVERED_SHOWN_SECONDS: the display acknowledges it
    when its socket opens and acts on it seconds later, and until then
    publishes its own idle state, which would flash in the UI.
    """
    dispatcher = on_demand_dispatch.current()
    status = dispatcher.status() if dispatcher is not None else None
    if status is None:
        return None
    if status.get('status') == 'delivered':
        delivered_at = status.get('last_updated') or 0
        if _pkg.time.time() - delivered_at > on_demand_dispatch.DELIVERED_SHOWN_SECONDS:
            return None
        return dict(status, status='starting', delivered=True, delivered_at=delivered_at)
    if status.get('status') not in ('starting', 'error'):
        return None
    return status


def _display_on_demand_state(snapshot):
    """The display's own on-demand state: (state, source). From the socket's
    snapshot, else the cache key it also writes; state None when neither."""
    state = display_state.on_demand_state(snapshot)
    if state is not None:
        return state, 'socket'
    # memory_ttl=0: the display service writes this key, so only the file
    # is current. This process's memory tier would keep serving the first
    # copy it read for the full max_age -- "active" for two minutes after
    # the display had already stopped.
    return _cache_manager().get('display_on_demand_state', max_age=120, memory_ttl=0), 'cache'


def _send_on_demand(payload):
    """Hand an on-demand request to the display over the control socket.

    Returns the display's acknowledgement: it has the command queued for
    its render thread. Raises ``control_client.ControlError`` when it did
    not take it; there is no other way to reach the display (the file
    mailbox ``display_on_demand_request`` is gone).
    """
    if payload['action'] == 'start':
        return control_client.on_demand_start(
            payload['request_id'], payload.get('plugin_id'), payload.get('mode'),
            payload.get('duration'), bool(payload.get('pinned', False)))
    return control_client.on_demand_stop(payload['request_id'])


def _socket_error_response(request_id, action, reason, message=None, **extra):
    """The answer when the display did not take an on-demand request: ``400``
    for arguments it refused, else ``503``."""
    status = 400 if reason == 'invalid_args' else 503
    data = {'request_id': request_id, 'transport': 'socket', 'socket_error': reason}
    data.update(extra)
    return jsonify({
        'status': 'error',
        'message': message or (f'The display service did not accept the on-demand {action} '
                               f'request ({reason})'),
        'data': data,
    }), status


def _socket_failure_reason(error):
    """The reportable reason code for an exception from _send_on_demand, logged."""
    if isinstance(error, control_client.ControlError):
        reason = _socket_reason_code(error.reason)
        if reason in _QUIET_SOCKET_REASONS:
            logger.debug("On-demand request not taken over the control socket: %s", error)
        else:
            logger.warning("On-demand request not taken over the control socket: %s", error)
        return reason
    logger.error("Control socket client failed", exc_info=error)
    return 'internal'


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
    falls back to find_plugin_for_mode when plugin_id is omitted. While the
    display is running, both that lookup and this list use the modes it
    registered, so modes a plugin generates from its config (each installed
    Starlark app, each soccer custom league) are found (#668); with the
    display stopped they see only what manifests declare. Sending the
    plugin_id from this list skips the lookup entirely.

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
    state, source = _display_on_demand_state(display_state.read_state())
    pending = _pending_start_state()
    if pending is not None and _shadows(pending, state):
        # A start the web process is still delivering, has delivered but
        # the display has not answered yet, or gave up on (start-timeout):
        # newer than anything the display has said.
        state, source = pending, 'web'
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
def _shadows(pending, state):
    """Whether the web process's start (or its failure) is newer than the
    display's on-demand ``state``.

    * Still being sent: always.
    * Delivered: until the display publishes the state that answers it.
      A display that names its request (``request_id``) answers when the
      id matches; its startup state, published as the socket opens and so
      possibly after the acknowledgement, names no request or an older one
      and does not count. An older display without the field answers with
      any state published after the delivery.
    * Failed: until the display publishes something later.
    """
    if not isinstance(state, dict):
        return True
    if pending.get('status') == 'starting' and not pending.get('delivered'):
        return True
    if pending.get('delivered') and 'request_id' in state:
        return state.get('request_id') != pending.get('request_id')
    shown = state.get('last_updated')
    if not isinstance(shown, (int, float)) or isinstance(shown, bool):
        return True
    return shown < (pending.get('last_updated') or 0)


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

    # The display matches mode names exactly: pass the registered spelling
    # when the caller's differs only in case.
    if api_v3.plugin_catalog and resolved_plugin and resolved_mode:
        wanted = resolved_mode.strip().lower()
        for registered in api_v3.plugin_catalog.get_plugin_display_modes(resolved_plugin):
            if isinstance(registered, str) and registered.lower() == wanted:
                resolved_mode = registered
                break

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

    # The request goes over the control socket, the only way to reach the
    # display. A display that is not listening (stopped, or still starting)
    # is started when start_service asks for it, and the request is sent
    # again once its socket is up.
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
    # This start supersedes one the dispatcher is still delivering,
    # whatever becomes of it: an older request must not land after it.
    dispatcher = on_demand_dispatch.current()
    if dispatcher is not None:
        dispatcher.cancel('superseded')
    try:
        _send_on_demand(request_payload)
    except Exception as e:  # pylint: disable=broad-except
        error = e
    else:
        # A socket acknowledgement is the display itself answering: it is
        # running and has the request queued, whatever systemd says (a
        # display run by hand or in the emulator has no active unit). So
        # nothing is checked or started for it. The service is still
        # reported the way _ensure_display_service_running reports a
        # running one.
        service_result = (dict(_get_display_service_status(), started=False)
                          if start_service else None)
        return _on_demand_started(request_id, resolved_plugin, resolved_mode,
                                  duration, pinned, service_result)

    reason = _socket_failure_reason(error)
    if not control_client.display_not_listening(error):
        # The display had it and refused it, is too old for the command, or
        # this web process cannot use the socket at all (switched off, no
        # Unix sockets): starting a service would not change that.
        return _socket_error_response(request_id, 'start', reason)

    service_status = _get_display_service_status()
    if not service_status.get('active') and not start_service:
        return jsonify({
            'status': 'error',
            'message': 'Display service is not running. Please start the display service or enable "Start Service" option.',
            'service_status': service_status,
            'data': {'request_id': request_id, 'transport': 'socket', 'socket_error': reason},
        }), 400

    # start_service means "start it if it is not running", as the UI's
    # checkbox says; _ensure_display_service_running leaves a running
    # service alone (restarting it cost seconds of blank panel for nothing).
    # Either way the display has no socket yet. The route does not wait for
    # it -- a cold start can outlast a client's timeout (the MQTT bridge's is
    # 15 s) -- but answers 202 and leaves the sending to the dispatcher,
    # whose outcome the status routes report.
    wait = ON_DEMAND_SOCKET_WAIT_RUNNING_SECONDS
    service_result = None
    if not service_status.get('active'):
        service_result = _ensure_display_service_running()
        if service_result and not service_result.get('active'):
            return jsonify({
                'status': 'error',
                'message': 'Failed to start display service. Please check service logs or start it manually.',
                'service_result': service_result
            }), 500
        wait = on_demand_dispatch.START_WAIT_SECONDS
    elif start_service:
        service_result = dict(service_status, started=False)

    _dispatcher().submit(request_payload, wait_seconds=wait)
    return jsonify({
        'status': 'starting',
        'message': ('The display service is starting; the request is sent to it as soon '
                    'as it is listening. Check the on-demand status for the outcome.'),
        'data': {
            'request_id': request_id,
            'plugin_id': resolved_plugin,
            'mode': resolved_mode,
            'duration': duration,
            'pinned': pinned,
            'service': service_result,
            'transport': 'socket',
            'socket_error': reason,
            'pending': True,
            'wait_seconds': wait,
        },
    }), 202


def _on_demand_started(request_id, plugin_id, mode, duration, pinned, service_result):
    """The success answer of /display/on-demand/start."""
    response_data = {
        'request_id': request_id,
        'plugin_id': plugin_id,
        'mode': mode,
        'duration': duration,
        'pinned': pinned,
        'service': service_result,
        'transport': 'socket',
    }
    return jsonify({'status': 'success', 'data': response_data})
@api_v3.route('/display/on-demand/stop', methods=['POST'])
def stop_on_demand_display():
    """Request the display controller to stop on-demand mode."""
    data = request.get_json(silent=True) or {}
    # _coerce_to_bool: bool("false") is True, which stopped the service.
    stop_service = _coerce_to_bool(data.get('stop_service', False))

    # The running display takes the stop over the control socket and
    # resumes normal rotation in place (_clear_on_demand); nothing is
    # restarted.
    request_id = data.get('request_id') or str(uuid.uuid4())
    request_payload = {
        'request_id': request_id,
        'action': 'stop',
        'timestamp': _pkg.time.time()
    }
    socket_error = None
    # A start the web process is still delivering is dropped first: the
    # stop is newer, whatever happens to it below.
    dispatcher = on_demand_dispatch.current()
    cancelled = dispatcher.cancel('requested-stop') if dispatcher is not None else None
    try:
        _send_on_demand(request_payload)
    except Exception as e:  # pylint: disable=broad-except
        socket_error = _socket_failure_reason(e)
        if not stop_service and not (cancelled and control_client.display_not_listening(e)):
            if control_client.display_not_listening(e):
                service_status = _get_display_service_status()
                message = ('Display service is not running, so the stop could not be '
                           'delivered. If it resumes an on-demand session when it starts, '
                           'stop it then.' if not service_status.get('active') else
                           f'The display service is running but its control socket did '
                           f'not answer ({socket_error}). It may still be starting; '
                           f'try again shortly.')
                return _socket_error_response(request_id, 'stop', socket_error, message,
                                              service=service_status)
            return _socket_error_response(request_id, 'stop', socket_error)
        # Stopping the service ends on-demand too, whatever the display did
        # with the request.

    service_result = None
    if stop_service:
        service_result = _stop_display_service()

    response_data = {
        'request_id': request_id,
        'service': service_result,
        'transport': 'socket',
    }
    if cancelled:
        # The start it ended never reached the display.
        response_data['cancelled_request_id'] = cancelled
    if socket_error:
        response_data['socket_error'] = socket_error
    return jsonify({'status': 'success', 'data': response_data})


@api_v3.route('/display/current-status', methods=['GET'])
def get_current_display_status():
    """Return the display mode/plugin currently intended to be shown.

    Read from the display's state stream over the control socket when it is
    available (``source: "socket"``). Otherwise from what the display
    publishes to the shared cache (display_controller._publish_current_mode_state)
    when the active mode changes (``source: "cache"``). Unknown (every field
    None) when the socket and the heartbeat both say the display is gone
    (display_state.display_gone).
    """
    snapshot = display_state.read_state()
    state = display_state.current_status(snapshot)
    source = 'socket'
    if state is None:
        source = 'cache'
        # A stopped display leaves its last answer in the cache, where it
        # read as on (is_display_active: true) for the 120 s max_age. With
        # no socket and no live heartbeat there is no display behind it.
        if not display_state.display_gone(snapshot):
            cache = _cache_manager()
            # memory_ttl=0: written by the display service; see get_on_demand_status.
            state = cache.get('display_current_state', max_age=120, memory_ttl=0)
    if state is None:
        state = {
            'mode': None,
            'plugin_id': None,
            'last_updated': None,
        }
    data = dict(state, source=source)
    pending = _pending_start_state()
    if pending is not None and _shadows(pending, _display_on_demand_state(snapshot)[0]):
        # An on-demand start the web process is still delivering, has
        # delivered but the display has not acted on yet, or gave up on:
        # what the panel is about to show, or why it will not.
        data['on_demand_pending'] = pending
    return jsonify({'status': 'success', 'data': data})
