"""Plugin operation status and history, and plugin state reconciliation.

Routes decorate the shared `api_v3` Blueprint from the package `__init__`,
so their endpoint names do not depend on which module they live in.
"""
from web_interface.blueprints.api_v3 import (
    ErrorCode, Path, Response, _coerce_to_bool, api_v3, error_response,
    exception_error_response, json, jsonify, logger, os, request, stat,
    success_response, tempfile,
)
import web_interface.blueprints.api_v3 as _pkg


@api_v3.route('/plugins/operation/<operation_id>', methods=['GET'])
def get_operation_status(operation_id):
    """Get status of a plugin operation"""
    try:
        if not api_v3.operation_queue:
            return error_response(
                ErrorCode.SYSTEM_ERROR,
                'Operation queue not initialized',
                status_code=500
            )

        operation = api_v3.operation_queue.get_operation_status(operation_id)
        if not operation:
            return error_response(
                ErrorCode.PLUGIN_NOT_FOUND,
                f'Operation {operation_id} not found',
                status_code=404
            )

        return success_response(data=operation.to_dict())
    except Exception as e:
        return exception_error_response(e, ErrorCode.SYSTEM_ERROR, with_context=False)


@api_v3.route('/plugins/operation/history', methods=['GET'])
def get_operation_history() -> Response:
    """Get operation history from the audit log."""
    if not api_v3.operation_history:
        return error_response(
            ErrorCode.SYSTEM_ERROR,
            'Operation history not initialized',
            status_code=500
        )

    try:
        limit = request.args.get('limit', 50, type=int)
        plugin_id = request.args.get('plugin_id')
        operation_type = request.args.get('operation_type')
    except (ValueError, TypeError) as e:
        return error_response(ErrorCode.INVALID_INPUT, f'Invalid query parameter: {e}', status_code=400)

    try:
        history = api_v3.operation_history.get_history(
            limit=limit,
            plugin_id=plugin_id,
            operation_type=operation_type
        )
    except (AttributeError, RuntimeError) as e:
        return exception_error_response(e, ErrorCode.SYSTEM_ERROR, with_context=False)

    return success_response(data=[record.to_dict() for record in history])


@api_v3.route('/plugins/operation/history', methods=['DELETE'])
def clear_operation_history() -> Response:
    """Clear operation history."""
    if not api_v3.operation_history:
        return error_response(
            ErrorCode.SYSTEM_ERROR,
            'Operation history not initialized',
            status_code=500
        )

    try:
        api_v3.operation_history.clear_history()
    except (OSError, RuntimeError) as e:
        return exception_error_response(e, ErrorCode.SYSTEM_ERROR, with_context=False)

    return success_response(message='Operation history cleared')


def _reconciler(store_manager=None):
    """A StateReconciliation over config + disk (desired) and the display's
    runtime snapshot (observed); None when the catalog is not set up."""
    if not api_v3.plugin_catalog or not api_v3.config_manager:
        return None
    from src.plugin_system.state_reconciliation import StateReconciliation
    return StateReconciliation(
        config_manager=api_v3.config_manager,
        plugins_dir=Path(api_v3.plugin_catalog.plugins_dir),
        store_manager=store_manager,
        runtime_source=_pkg._plugin_runtime_view,
    )


def _operation_times(plugin_ids):
    """``installed_at`` / ``last_updated`` per plugin from the operation
    history: the newest successful install, and the newest successful
    install or update. None where the history has no record (it keeps the
    last 1000 operations, and can be cleared)."""
    times = {pid: {'installed_at': None, 'last_updated': None} for pid in plugin_ids}
    history = getattr(api_v3, 'operation_history', None)
    if not history or not times:
        return times
    try:
        records = history.get_history(limit=100000)
    except Exception:
        logger.debug("[PluginState] Could not read the operation history", exc_info=True)
        return times
    for record in records:  # newest first
        entry = times.get(record.plugin_id)
        if entry is None or record.status != 'success':
            continue
        when = record.timestamp.isoformat()
        if record.operation_type == 'install' and entry['installed_at'] is None:
            entry['installed_at'] = when
        if record.operation_type in ('install', 'update') and entry['last_updated'] is None:
            entry['last_updated'] = when
    return times


def _plugin_state_status(record):
    """The old plugin_state.json ``status`` vocabulary, derived."""
    if record.get('state') == 'error':
        return 'error'
    if not record.get('installed'):
        return 'unknown'
    return 'enabled' if record.get('enabled') else 'disabled'


@api_v3.route('/plugins/state', methods=['GET'])
def get_plugin_state():
    """Every plugin's state: desired (config + disk) and observed (the
    display's runtime snapshot).

    Built on each request -- there is no state file any more
    (data/plugin_state.json is retired). ``loaded``, ``state``,
    ``error_info``, ``loaded_version`` and ``loaded_at`` are null unless the
    display's snapshot is live; ``runtime`` beside ``data`` says whether it is.
    """
    try:
        reconciler = _reconciler()
        if reconciler is None:
            return error_response(
                ErrorCode.SYSTEM_ERROR,
                'Plugin catalog not initialized',
                status_code=500
            )

        states = reconciler.plugin_states()
        times = _operation_times(states.keys())
        for plugin_id, record in states.items():
            record['status'] = _plugin_state_status(record)
            record.update(times.get(plugin_id, {}))
        runtime = _pkg._plugin_runtime_view().describe()

        plugin_id = request.args.get('plugin_id')
        if plugin_id:
            state = states.get(plugin_id)
            if not state:
                return error_response(
                    ErrorCode.PLUGIN_NOT_FOUND,
                    f'Plugin {plugin_id} is neither installed nor configured',
                    context={'plugin_id': plugin_id},
                    status_code=404
                )
            return success_response(data=state, extra={'runtime': runtime})
        return success_response(data=states, extra={'runtime': runtime})
    except Exception as e:
        return exception_error_response(e, ErrorCode.SYSTEM_ERROR)


@api_v3.route('/plugins/state/reconcile', methods=['POST'])
def reconcile_plugin_state():
    """Reconcile desired state (config + disk) with what is installed and
    what the display reports running."""
    try:
        # Parse optional `force` flag from request body, guarding against
        # non-dict bodies (bare string, array, null) that would raise AttributeError.
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            payload = {}
        force = _coerce_to_bool(payload.get('force', False))

        reconciler = _reconciler()
        if reconciler is None:
            return error_response(
                ErrorCode.SYSTEM_ERROR,
                'Config manager or plugin catalog not initialized',
                status_code=500
            )

        result = reconciler.reconcile_state(force=force)

        return success_response(
            data={
                'inconsistencies_found': len(result.inconsistencies_found),
                'inconsistencies_fixed': len(result.inconsistencies_fixed),
                'inconsistencies_manual': len(result.inconsistencies_manual),
                'inconsistencies': [
                    {
                        'plugin_id': inc.plugin_id,
                        'type': inc.inconsistency_type.value,
                        'description': inc.description,
                        'fix_action': inc.fix_action.value
                    }
                    for inc in result.inconsistencies_found
                ],
                'fixed': [
                    {
                        'plugin_id': inc.plugin_id,
                        'type': inc.inconsistency_type.value,
                        'description': inc.description
                    }
                    for inc in result.inconsistencies_fixed
                ],
                'manual_fix_required': [
                    {
                        'plugin_id': inc.plugin_id,
                        'type': inc.inconsistency_type.value,
                        'description': inc.description
                    }
                    for inc in result.inconsistencies_manual
                ]
            },
            message=result.message
        )
    except Exception as e:
        return exception_error_response(e, ErrorCode.SYSTEM_ERROR)


def _drop_stale_reconciliation_findings(unresolved):
    """Re-check a stored reconciliation verdict against current state.

    The verdict is a snapshot written once per run, and a run that could not
    apply a fix also refuses to retry -- so a resolved condition was reported
    indefinitely. Best-effort: any failure here returns the list untouched,
    because showing a stale warning beats failing the endpoint.

    Both sets come from the reconciliation module's own extractors rather than
    being re-derived here. That matters for correctness, not tidiness: a plain
    set(load_config()) also contains system keys, the secrets-file keys merged
    in by load_config(), and non-dict values, and any directory holding a
    manifest.json would count as installed even if that manifest does not
    parse. Either looseness clears findings that are still true -- and a secrets
    key read as a plugin is the very bug the filter exists to stop reporting.
    """
    try:
        from src.plugin_system.state_reconciliation import (
            config_plugin_ids, disk_plugin_ids, ignored_config_keys,
            still_unresolved,
        )

        cm = api_v3.config_manager
        plugins_dir = getattr(api_v3.plugin_catalog, 'plugins_dir', None)
        installed = disk_plugin_ids(plugins_dir) if plugins_dir else set()
        config_keys = config_plugin_ids(cm.load_config() or {},
                                       ignored_config_keys(cm, installed))
        return still_unresolved(unresolved, config_keys, installed)
    except Exception:
        logger.debug("[Reconciliation] Could not re-check stored findings", exc_info=True)
        return unresolved


@api_v3.route('/plugins/reconciliation-status', methods=['GET'])
def get_reconciliation_status():
    """Return the result of the last startup reconciliation from /tmp status file."""
    _recon_path = os.path.join(tempfile.gettempdir(), "ledmatrix_reconciliation.json")
    try:
        st = os.lstat(_recon_path)
    except FileNotFoundError:
        return jsonify({'status': 'success', 'data': {'done': False, 'unresolved': []}})
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        logger.warning("[Reconciliation] Status file is not a regular file: %s", _recon_path)
        return jsonify({'status': 'success', 'data': {'done': False, 'unresolved': []}})
    try:
        with open(_recon_path) as _f:
            data = json.load(_f)
        if data.get('unresolved'):
            data['unresolved'] = _drop_stale_reconciliation_findings(data['unresolved'])
        return jsonify({'status': 'success', 'data': data})
    except json.JSONDecodeError:
        logger.exception("[Reconciliation] Failed to parse status file: %s", _recon_path)
        return jsonify({'status': 'success', 'data': {'done': False, 'unresolved': []}})
    except PermissionError:
        logger.exception("[Reconciliation] Permission denied reading status file: %s", _recon_path)
        return jsonify({'status': 'success', 'data': {'done': False, 'unresolved': []}})
