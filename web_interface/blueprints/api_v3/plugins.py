"""Plugin install, update, enable/disable, config and store routes.

Routes decorate the shared `api_v3` Blueprint from ._common, so their
endpoint names are unchanged by living here.
"""
from web_interface.blueprints.api_v3 import (
    ErrorCode, OperationType, PROJECT_ROOT, Path, Response,
    _CALENDAR_LIST_MAX_PAGES, _SKIP_FIELD,
    _coerce_to_bool, _do_transactional_uninstall,
    _enhance_schema_with_core_properties, _filter_config_by_schema,
    _get_plugin_version, _get_schema_property, _installed_plugin_ids,
    _is_plugin_update_available, _parse_form_value_with_schema,
    _prune_credential_backups, _run_calendar_registration, _set_missing_booleans_to_false, _set_nested_value,
    _starlark_virtual_plugins, _toggle_starlark_app, api_v3, datetime,
    deep_merge, describe_exception, error_response, find_secret_fields,
    hashlib, json, jsonify, logger, logging, merge_secrets, os, redact_text,
    remove_empty_secrets, request, separate_secrets, shutil, stat, subprocess,
    success_response, sys, tempfile, uuid, validate_request_json,
)
import web_interface.blueprints.api_v3 as _pkg
# Read through the module rather than bound by value: tests patch these
# as module attributes, and a value binding would not see the patch.
# Several are also called from helpers that live in __init__, so the
# package is the only patch point that covers every caller.


@api_v3.route('/plugins/installed', methods=['GET'])
def get_installed_plugins():
    """Get installed plugins"""
    try:
        if not api_v3.plugin_manager or not api_v3.plugin_store_manager:
            return jsonify({'status': 'error', 'message': 'Plugin managers not initialized'}), 500

        import json
        from pathlib import Path

        # Re-discover plugins to ensure we have the latest list
        # This handles cases where plugins are added/removed after app startup
        api_v3.plugin_manager.discover_plugins()

        # Get all installed plugin info from the plugin manager
        all_plugin_info = api_v3.plugin_manager.get_all_plugin_info()

        # Load config once before the loop (not per-plugin)
        full_config = api_v3.config_manager.load_config() if api_v3.config_manager else {}

        def _build_plugin_entry(plugin_info):
            plugin_id = plugin_info.get('id')
            try:
                return _build_plugin_entry_inner(plugin_info, plugin_id)
            except Exception:
                logger.exception("Error building plugin entry for %s — skipping", plugin_id)
                return None

        def _build_plugin_entry_inner(plugin_info, plugin_id):
            # Capture runtime state (state machine + error context) before the
            # manifest merge below can shadow the 'state' key. get_all_plugin_info
            # attaches this via PluginStateManager.get_state_info(); surfacing it
            # lets the UI show *why* a plugin isn't running instead of just
            # 'loaded: false'.
            state_info = plugin_info.get('state')
            plugin_state = None
            plugin_error_info = None
            if isinstance(state_info, dict):
                plugin_state = state_info.get('state')
                plugin_error_info = state_info.get('error_info')

            # Re-read manifest from disk to ensure we have the latest metadata
            manifest_path = Path(api_v3.plugin_manager.plugins_dir) / plugin_id / "manifest.json"
            if manifest_path.exists():
                try:
                    with open(manifest_path, 'r', encoding='utf-8') as f:
                        fresh_manifest = json.load(f)
                    if isinstance(fresh_manifest, dict):
                        plugin_info.update(fresh_manifest)
                    else:
                        logger.debug("Manifest for %s is not a dict (%s) — skipping merge",
                                     plugin_id, type(fresh_manifest).__name__)
                except (FileNotFoundError, PermissionError, json.JSONDecodeError) as e:
                    logger.debug("Could not read fresh manifest for %s: %s", plugin_id, e)

            # Enabled status: config is source of truth, fall back to instance
            enabled = None
            plugin_config = full_config.get(plugin_id, {})
            if 'enabled' in plugin_config:
                enabled = bool(plugin_config['enabled'])

            # Single get_plugin() call shared for both enabled fallback and Vegas mode
            plugin_instance = api_v3.plugin_manager.get_plugin(plugin_id)
            if enabled is None:
                enabled = plugin_instance.enabled if plugin_instance else True

            # Verified + latest published version from registry (no network call)
            store_info = api_v3.plugin_store_manager.get_registry_info(plugin_id)
            verified = store_info.get('verified', False) if store_info else False
            latest_version = store_info.get('latest_version', '') if store_info else ''
            installed_version = plugin_info.get('version', '')
            update_available = _is_plugin_update_available(installed_version, latest_version)

            # Local git info (single subprocess on cache miss, zero on hit)
            plugin_path = Path(api_v3.plugin_manager.plugins_dir) / plugin_id
            local_git_info = api_v3.plugin_store_manager._get_local_git_info(plugin_path) if plugin_path.exists() else None

            if local_git_info:
                sha = local_git_info.get('sha', '')
                last_commit = local_git_info.get('short_sha') or (sha[:7] if sha else None)
                branch = local_git_info.get('branch')
                last_updated = local_git_info.get('date_iso') or local_git_info.get('date')
            else:
                last_updated = plugin_info.get('last_updated')
                last_commit = plugin_info.get('last_commit') or plugin_info.get('last_commit_sha')
                branch = plugin_info.get('branch')
                if store_info:
                    last_updated = last_updated or store_info.get('last_updated') or store_info.get('last_updated_iso')
                    last_commit = last_commit or store_info.get('last_commit') or store_info.get('last_commit_sha')
                    branch = branch or store_info.get('branch') or store_info.get('default_branch')

            last_commit_message = plugin_info.get('last_commit_message')
            if store_info and not last_commit_message:
                last_commit_message = store_info.get('last_commit_message')

            # Vegas mode from instance, overridden by explicit config value
            vegas_mode = None
            vegas_content_type = None
            if plugin_instance:
                try:
                    if hasattr(plugin_instance, 'get_vegas_display_mode'):
                        mode = plugin_instance.get_vegas_display_mode()
                        vegas_mode = mode.value if hasattr(mode, 'value') else str(mode)
                except (AttributeError, TypeError, ValueError) as e:
                    logger.debug("[%s] Failed to get vegas_display_mode: %s", plugin_id, e)
                try:
                    if hasattr(plugin_instance, 'get_vegas_content_type'):
                        vegas_content_type = plugin_instance.get_vegas_content_type()
                except (AttributeError, TypeError, ValueError) as e:
                    logger.debug("[%s] Failed to get vegas_content_type: %s", plugin_id, e)

            if 'vegas_mode' in plugin_config:
                vegas_mode = plugin_config['vegas_mode']

            return {
                'id': plugin_id,
                'name': plugin_info.get('name', plugin_id),
                'version': plugin_info.get('version', ''),
                'latest_version': latest_version,
                'update_available': update_available,
                'author': plugin_info.get('author', 'Unknown'),
                'category': plugin_info.get('category', 'General'),
                'description': plugin_info.get('description', 'No description available'),
                'tags': plugin_info.get('tags', []),
                'enabled': enabled,
                'verified': verified,
                'loaded': plugin_info.get('loaded', False),
                'state': plugin_state,
                'error_info': plugin_error_info,
                'last_updated': last_updated,
                'last_commit': last_commit,
                'last_commit_message': last_commit_message,
                'branch': branch,
                'web_ui_actions': plugin_info.get('web_ui_actions', []),
                'vegas_mode': vegas_mode,
                'vegas_content_type': vegas_content_type,
            }

        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(_build_plugin_entry, all_plugin_info))
        plugins = [r for r in results if r is not None]
        plugins.extend(_starlark_virtual_plugins())

        return jsonify({'status': 'success', 'data': {'plugins': plugins}})
    except Exception as e:
        logger.error('Error in get_installed_plugins', exc_info=True)
        return jsonify({'status': 'error', 'message': 'An error occurred; see logs for details', 'details': describe_exception(e)}), 500
@api_v3.route('/plugins/health', methods=['GET'])
def get_plugin_health():
    """Get health metrics for all plugins"""
    try:
        if not api_v3.plugin_manager:
            return jsonify({'status': 'error', 'message': 'Plugin manager not initialized'}), 500

        # Check if health tracker is available
        if not hasattr(api_v3.plugin_manager, 'health_tracker') or not api_v3.plugin_manager.health_tracker:
            return jsonify({
                'status': 'success',
                'data': {},
                'message': 'Health tracking not available'
            })

        tracker = api_v3.plugin_manager.health_tracker
        # Build per-plugin summaries by ID so persisted (cross-process) health
        # is included, then fold in any in-memory-only entries.
        health_summaries = {}
        for pid in _installed_plugin_ids():
            try:
                # force_reload: this process only reads; bypass the in-memory
                # snapshot so each poll reflects the display service's latest
                # persisted state.
                health_summaries[pid] = tracker.get_health_summary(pid, force_reload=True)
            except Exception:
                logger.debug('Could not read health summary for %s', pid, exc_info=True)
        try:
            for pid, summary in tracker.get_all_health_summaries().items():
                health_summaries.setdefault(pid, summary)
        except Exception:
            logger.debug('get_all_health_summaries failed', exc_info=True)

        return jsonify({
            'status': 'success',
            'data': health_summaries
        })
    except Exception as e:
        logger.error('Error in get_plugin_health', exc_info=True)
        return jsonify({'status': 'error', 'message': 'An error occurred; see logs for details', 'details': describe_exception(e)}), 500
@api_v3.route('/plugins/health/<plugin_id>', methods=['GET'])
def get_plugin_health_single(plugin_id):
    """Get health metrics for a specific plugin"""
    try:
        if not api_v3.plugin_manager:
            return jsonify({'status': 'error', 'message': 'Plugin manager not initialized'}), 500

        # Check if health tracker is available
        if not hasattr(api_v3.plugin_manager, 'health_tracker') or not api_v3.plugin_manager.health_tracker:
            return jsonify({
                'status': 'error',
                'message': 'Health tracking not available'
            }), 503

        # Get health summary for specific plugin
        health_summary = api_v3.plugin_manager.health_tracker.get_health_summary(plugin_id)

        return jsonify({
            'status': 'success',
            'data': health_summary
        })
    except Exception as e:
        logger.error('Error in get_plugin_health_single', exc_info=True)
        return jsonify({'status': 'error', 'message': 'An error occurred; see logs for details', 'details': describe_exception(e)}), 500
@api_v3.route('/plugins/health/<plugin_id>/reset', methods=['POST'])
def reset_plugin_health(plugin_id):
    """Reset health state for a plugin (manual recovery)"""
    try:
        if not api_v3.plugin_manager:
            return jsonify({'status': 'error', 'message': 'Plugin manager not initialized'}), 500

        # Check if health tracker is available
        if not hasattr(api_v3.plugin_manager, 'health_tracker') or not api_v3.plugin_manager.health_tracker:
            return jsonify({
                'status': 'error',
                'message': 'Health tracking not available'
            }), 503

        # Reset health state
        api_v3.plugin_manager.health_tracker.reset_health(plugin_id)

        return jsonify({
            'status': 'success',
            'message': f'Health state reset for plugin {plugin_id}'
        })
    except Exception as e:
        logger.error('Error in reset_plugin_health', exc_info=True)
        return jsonify({'status': 'error', 'message': 'An error occurred; see logs for details', 'details': describe_exception(e)}), 500
@api_v3.route('/plugins/metrics', methods=['GET'])
def get_plugin_metrics():
    """Get resource metrics for all plugins"""
    try:
        if not api_v3.plugin_manager:
            return jsonify({'status': 'error', 'message': 'Plugin manager not initialized'}), 500

        # Check if resource monitor is available
        if not hasattr(api_v3.plugin_manager, 'resource_monitor') or not api_v3.plugin_manager.resource_monitor:
            return jsonify({
                'status': 'success',
                'data': {},
                'message': 'Resource monitoring not available'
            })

        monitor = api_v3.plugin_manager.resource_monitor
        # Build per-plugin summaries by ID so persisted (cross-process) metrics
        # are included, then fold in any in-memory-only entries.
        metrics_summaries = {}
        for pid in _installed_plugin_ids():
            try:
                # force_reload: read-only path — bypass the in-memory snapshot so
                # each poll reflects the display service's latest persisted metrics.
                metrics_summaries[pid] = monitor.get_metrics_summary(pid, force_reload=True)
            except Exception:
                logger.debug('Could not read metrics summary for %s', pid, exc_info=True)
        try:
            for pid, summary in monitor.get_all_metrics_summaries().items():
                metrics_summaries.setdefault(pid, summary)
        except Exception:
            logger.debug('get_all_metrics_summaries failed', exc_info=True)

        return jsonify({
            'status': 'success',
            'data': metrics_summaries
        })
    except Exception as e:
        logger.error('Error in get_plugin_metrics', exc_info=True)
        return jsonify({'status': 'error', 'message': 'An error occurred; see logs for details', 'details': describe_exception(e)}), 500
@api_v3.route('/plugins/metrics/<plugin_id>', methods=['GET'])
def get_plugin_metrics_single(plugin_id):
    """Get resource metrics for a specific plugin"""
    try:
        if not api_v3.plugin_manager:
            return jsonify({'status': 'error', 'message': 'Plugin manager not initialized'}), 500

        # Check if resource monitor is available
        if not hasattr(api_v3.plugin_manager, 'resource_monitor') or not api_v3.plugin_manager.resource_monitor:
            return jsonify({
                'status': 'error',
                'message': 'Resource monitoring not available'
            }), 503

        # Get metrics summary for specific plugin
        metrics_summary = api_v3.plugin_manager.resource_monitor.get_metrics_summary(plugin_id)

        return jsonify({
            'status': 'success',
            'data': metrics_summary
        })
    except Exception as e:
        logger.error('Error in get_plugin_metrics_single', exc_info=True)
        return jsonify({'status': 'error', 'message': 'An error occurred; see logs for details', 'details': describe_exception(e)}), 500
@api_v3.route('/plugins/metrics/<plugin_id>/reset', methods=['POST'])
def reset_plugin_metrics(plugin_id):
    """Reset metrics for a plugin"""
    try:
        if not api_v3.plugin_manager:
            return jsonify({'status': 'error', 'message': 'Plugin manager not initialized'}), 500

        # Check if resource monitor is available
        if not hasattr(api_v3.plugin_manager, 'resource_monitor') or not api_v3.plugin_manager.resource_monitor:
            return jsonify({
                'status': 'error',
                'message': 'Resource monitoring not available'
            }), 503

        # Reset metrics
        api_v3.plugin_manager.resource_monitor.reset_metrics(plugin_id)

        return jsonify({
            'status': 'success',
            'message': f'Metrics reset for plugin {plugin_id}'
        })
    except Exception as e:
        logger.error('Error in reset_plugin_metrics', exc_info=True)
        return jsonify({'status': 'error', 'message': 'An error occurred; see logs for details', 'details': describe_exception(e)}), 500
@api_v3.route('/plugins/limits/<plugin_id>', methods=['GET', 'POST'])
def manage_plugin_limits(plugin_id):
    """Get or set resource limits for a plugin"""
    try:
        if not api_v3.plugin_manager:
            return jsonify({'status': 'error', 'message': 'Plugin manager not initialized'}), 500

        # Check if resource monitor is available
        if not hasattr(api_v3.plugin_manager, 'resource_monitor') or not api_v3.plugin_manager.resource_monitor:
            return jsonify({
                'status': 'error',
                'message': 'Resource monitoring not available'
            }), 503

        if request.method == 'GET':
            # Get limits
            limits = api_v3.plugin_manager.resource_monitor.get_limits(plugin_id)
            if limits:
                return jsonify({
                    'status': 'success',
                    'data': {
                        'max_memory_mb': limits.max_memory_mb,
                        'max_cpu_percent': limits.max_cpu_percent,
                        'max_execution_time': limits.max_execution_time,
                        'warning_threshold': limits.warning_threshold
                    }
                })
            else:
                return jsonify({
                    'status': 'success',
                    'data': None,
                    'message': 'No limits configured for this plugin'
                })
        else:
            # POST - Set limits
            data = request.get_json(silent=True) or {}
            from src.plugin_system.resource_monitor import ResourceLimits

            limits = ResourceLimits(
                max_memory_mb=data.get('max_memory_mb'),
                max_cpu_percent=data.get('max_cpu_percent'),
                max_execution_time=data.get('max_execution_time'),
                warning_threshold=data.get('warning_threshold', 0.8)
            )

            api_v3.plugin_manager.resource_monitor.set_limits(plugin_id, limits)

            return jsonify({
                'status': 'success',
                'message': f'Resource limits updated for plugin {plugin_id}'
            })
    except Exception as e:
        logger.error('Error in manage_plugin_limits', exc_info=True)
        return jsonify({'status': 'error', 'message': 'An error occurred; see logs for details', 'details': describe_exception(e)}), 500
@api_v3.route('/plugins/toggle', methods=['POST'])
def toggle_plugin():
    """Toggle plugin enabled/disabled"""
    try:
        if not api_v3.plugin_manager or not api_v3.config_manager:
            return jsonify({'status': 'error', 'message': 'Plugin or config manager not initialized'}), 500

        # Support both JSON and form data (for HTMX submissions)
        content_type = request.content_type or ''

        if 'application/json' in content_type:
            data = request.get_json(silent=True)
            if not data or 'plugin_id' not in data or 'enabled' not in data:
                return jsonify({'status': 'error', 'message': 'plugin_id and enabled required'}), 400
            plugin_id = data['plugin_id']
            enabled = data['enabled']
        else:
            # Form data or query string (HTMX submission)
            plugin_id = request.args.get('plugin_id') or request.form.get('plugin_id')
            if not plugin_id:
                return jsonify({'status': 'error', 'message': 'plugin_id required'}), 400

            # For checkbox toggle, if form was submitted, the checkbox was checked (enabled)
            # If using HTMX with hx-trigger="change", we need to check if checkbox is checked
            # The checkbox value or 'enabled' form field indicates the state
            enabled_str = request.form.get('enabled', request.args.get('enabled', ''))

            # Handle various truthy/falsy values
            if enabled_str.lower() in ('true', '1', 'on', 'yes'):
                enabled = True
            elif enabled_str.lower() in ('false', '0', 'off', 'no', ''):
                # Empty string means checkbox was unchecked (toggle off)
                enabled = False
            else:
                # Default: toggle based on current state
                config = api_v3.config_manager.load_config()
                current_enabled = config.get(plugin_id, {}).get('enabled', False)
                enabled = not current_enabled

        # A Starlark app is not a plugin in plugin_manager's sense -- it is an
        # entry in starlark-apps' own manifest -- so its enable/disable is
        # handled here rather than falling through to the check below, which
        # would answer "Plugin not found".
        if plugin_id.startswith('starlark:'):
            return _toggle_starlark_app(plugin_id[len('starlark:'):], enabled)

        # Check if plugin exists in manifests (discovered but may not be loaded)
        if plugin_id not in api_v3.plugin_manager.plugin_manifests:
            return jsonify({'status': 'error', 'message': 'Plugin not found'}), 404

        # Update config (this is what the display controller reads)
        config = api_v3.config_manager.load_config()
        if plugin_id not in config:
            config[plugin_id] = {}
        config[plugin_id]['enabled'] = enabled

        # Use atomic save if available
        if hasattr(api_v3.config_manager, 'save_config_atomic'):
            result = api_v3.config_manager.save_config_atomic(config, create_backup=True)
            if result.status.value != 'success':
                return error_response(
                    ErrorCode.CONFIG_SAVE_FAILED,
                    f"Failed to save configuration: {result.message}",
                    status_code=500
                )
        else:
            api_v3.config_manager.save_config(config)

        # Update state manager if available
        if api_v3.plugin_state_manager:
            api_v3.plugin_state_manager.set_plugin_enabled(plugin_id, enabled)

        # Log operation
        if api_v3.operation_history:
            api_v3.operation_history.record_operation(
                "enable" if enabled else "disable",
                plugin_id=plugin_id,
                status="success"
            )

        # If plugin is loaded, also call its lifecycle methods
        # Wrap in try/except to prevent lifecycle errors from failing the toggle
        plugin = api_v3.plugin_manager.get_plugin(plugin_id)
        if plugin:
            try:
                if enabled:
                    if hasattr(plugin, 'on_enable'):
                        plugin.on_enable()
                else:
                    if hasattr(plugin, 'on_disable'):
                        plugin.on_disable()
            except Exception as lifecycle_error:
                # Log the error but don't fail the toggle - config is already saved
                import logging
                logging.warning(f"Lifecycle method error for {plugin_id}: {lifecycle_error}", exc_info=True)

        return success_response(
            message=f"Plugin {plugin_id} {'enabled' if enabled else 'disabled'} successfully"
        )
    except Exception as e:
        from src.web_interface.errors import WebInterfaceError
        error = WebInterfaceError.from_exception(e, ErrorCode.PLUGIN_OPERATION_CONFLICT)
        if api_v3.operation_history:
            toggle_type = "enable" if ('data' in locals() and data.get('enabled')) else "disable"
            api_v3.operation_history.record_operation(
                toggle_type,
                plugin_id=data.get('plugin_id') if 'data' in locals() else None,
                status="failed",
                error=str(e)
            )
        return error_response(
            error.error_code,
            error.message,
            details=error.details,
            context=error.context,
            status_code=500
        )
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
        from src.web_interface.errors import WebInterfaceError
        error = WebInterfaceError.from_exception(e, ErrorCode.SYSTEM_ERROR)
        return error_response(
            error.error_code,
            error.message,
            details=error.details,
            status_code=500
        )
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
        from src.web_interface.errors import WebInterfaceError
        error = WebInterfaceError.from_exception(e, ErrorCode.SYSTEM_ERROR)
        return error_response(error.error_code, error.message, details=error.details, status_code=500)

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
        from src.web_interface.errors import WebInterfaceError
        error = WebInterfaceError.from_exception(e, ErrorCode.SYSTEM_ERROR)
        return error_response(error.error_code, error.message, details=error.details, status_code=500)

    return success_response(message='Operation history cleared')
@api_v3.route('/plugins/state', methods=['GET'])
def get_plugin_state():
    """Get plugin state from state manager"""
    try:
        if not api_v3.plugin_state_manager:
            return error_response(
                ErrorCode.SYSTEM_ERROR,
                'State manager not initialized',
                status_code=500
            )

        plugin_id = request.args.get('plugin_id')

        if plugin_id:
            # Get state for specific plugin
            state = api_v3.plugin_state_manager.get_plugin_state(plugin_id)
            if not state:
                return error_response(
                    ErrorCode.PLUGIN_NOT_FOUND,
                    f'Plugin {plugin_id} not found in state manager',
                    context={'plugin_id': plugin_id},
                    status_code=404
                )
            return success_response(data=state.to_dict())
        else:
            # Get all plugin states
            all_states = api_v3.plugin_state_manager.get_all_states()
            return success_response(data={
                plugin_id: state.to_dict()
                for plugin_id, state in all_states.items()
            })
    except Exception as e:
        from src.web_interface.errors import WebInterfaceError
        error = WebInterfaceError.from_exception(e, ErrorCode.SYSTEM_ERROR)
        return error_response(
            error.error_code,
            error.message,
            details=error.details,
            context=error.context,
            status_code=500
        )
@api_v3.route('/plugins/state/reconcile', methods=['POST'])
def reconcile_plugin_state():
    """Reconcile plugin state across all sources"""
    try:
        if not api_v3.plugin_state_manager or not api_v3.plugin_manager:
            return error_response(
                ErrorCode.SYSTEM_ERROR,
                'State manager or plugin manager not initialized',
                status_code=500
            )

        from src.plugin_system.state_reconciliation import StateReconciliation

        # Parse optional `force` flag from request body, guarding against
        # non-dict bodies (bare string, array, null) that would raise AttributeError.
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            payload = {}
        force = _coerce_to_bool(payload.get('force', False))

        reconciler = StateReconciliation(
            state_manager=api_v3.plugin_state_manager,
            config_manager=api_v3.config_manager,
            plugin_manager=api_v3.plugin_manager,
            plugins_dir=Path(api_v3.plugin_manager.plugins_dir)
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
        from src.web_interface.errors import WebInterfaceError
        error = WebInterfaceError.from_exception(e, ErrorCode.SYSTEM_ERROR)
        return error_response(
            error.error_code,
            error.message,
            details=error.details,
            context=error.context,
            status_code=500
        )
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
        config_keys = config_plugin_ids(cm.load_config() or {},
                                       ignored_config_keys(cm))
        plugins_dir = getattr(api_v3.plugin_manager, 'plugins_dir', None)
        installed = disk_plugin_ids(plugins_dir) if plugins_dir else set()
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
@api_v3.route('/plugins/config', methods=['GET'])
def get_plugin_config():
    """Get plugin configuration"""
    try:
        if not api_v3.config_manager:
            return error_response(
                ErrorCode.SYSTEM_ERROR,
                'Config manager not initialized',
                status_code=500
            )

        plugin_id = request.args.get('plugin_id')
        if not plugin_id:
            return error_response(
                ErrorCode.INVALID_INPUT,
                'plugin_id required',
                context={'missing_params': ['plugin_id']},
                status_code=400
            )

        # Get plugin configuration from config manager
        main_config = api_v3.config_manager.load_config()
        plugin_config = main_config.get(plugin_id, {})

        # Merge with defaults from schema so form shows default values for missing fields
        schema_mgr = api_v3.schema_manager
        if schema_mgr:
            try:
                defaults = schema_mgr.generate_default_config(plugin_id, use_cache=True)
                plugin_config = schema_mgr.merge_with_defaults(plugin_config, defaults)
            except Exception as e:
                # Log but don't fail - defaults merge is best effort
                import logging
                logging.warning(f"Could not merge defaults for {plugin_id}: {e}")

        # Special handling for of-the-day plugin: populate uploaded_files and categories from disk
        if plugin_id == 'of-the-day' or plugin_id == 'ledmatrix-of-the-day':
            # Get plugin directory - plugin_id in manifest is 'of-the-day', but directory is 'ledmatrix-of-the-day'
            plugin_dir_name = 'ledmatrix-of-the-day'
            if api_v3.plugin_manager:
                plugin_dir = api_v3.plugin_manager.get_plugin_directory(plugin_dir_name)
                # If not found, try with the plugin_id
                if not plugin_dir or not Path(plugin_dir).exists():
                    plugin_dir = api_v3.plugin_manager.get_plugin_directory(plugin_id)
            else:
                plugin_dir = PROJECT_ROOT / 'plugins' / plugin_dir_name
                if not plugin_dir.exists():
                    plugin_dir = PROJECT_ROOT / 'plugins' / plugin_id

            if plugin_dir and Path(plugin_dir).exists():
                data_dir = Path(plugin_dir) / 'of_the_day'
                if data_dir.exists():
                    # Scan for JSON files
                    uploaded_files = []
                    categories_from_files = {}

                    for json_file in data_dir.glob('*.json'):
                        try:
                            # Get file stats
                            stat = json_file.stat()

                            # Read JSON to count entries
                            with open(json_file, 'r', encoding='utf-8') as f:
                                json_data = json.load(f)
                                entry_count = len(json_data) if isinstance(json_data, dict) else 0

                            # Extract category name from filename
                            category_name = json_file.stem
                            filename = json_file.name

                            # Create file entry
                            file_entry = {
                                'id': category_name,
                                'category_name': category_name,
                                'filename': filename,
                                'original_filename': filename,
                                'path': f'of_the_day/{filename}',
                                'size': stat.st_size,
                                'uploaded_at': datetime.fromtimestamp(stat.st_mtime).isoformat() + 'Z',
                                'entry_count': entry_count
                            }
                            uploaded_files.append(file_entry)

                            # Create/update category entry if not in config
                            if category_name not in plugin_config.get('categories', {}):
                                display_name = category_name.replace('_', ' ').title()
                                categories_from_files[category_name] = {
                                    'enabled': False,  # Default to disabled, user can enable
                                    'data_file': f'of_the_day/{filename}',
                                    'display_name': display_name
                                }
                            else:
                                # Update with file info if needed
                                categories_from_files[category_name] = plugin_config['categories'][category_name]
                                # Ensure data_file is correct
                                categories_from_files[category_name]['data_file'] = f'of_the_day/{filename}'

                        except Exception as e:
                            logger.debug("Could not read json file: %s", e)
                            continue

                    # Update plugin_config with scanned files
                    if uploaded_files:
                        plugin_config['uploaded_files'] = uploaded_files

                    # Merge categories from files with existing config
                    # Start with existing categories (preserve user settings like enabled/disabled)
                    existing_categories = plugin_config.get('categories', {}).copy()

                    # Update existing categories with file info, add new ones from files
                    for cat_name, cat_data in categories_from_files.items():
                        if cat_name in existing_categories:
                            # Preserve existing enabled state and display_name, but update data_file path
                            existing_categories[cat_name]['data_file'] = cat_data['data_file']
                            if 'display_name' not in existing_categories[cat_name] or not existing_categories[cat_name]['display_name']:
                                existing_categories[cat_name]['display_name'] = cat_data['display_name']
                        else:
                            # Add new category from file (default to disabled)
                            existing_categories[cat_name] = cat_data

                    if existing_categories:
                        plugin_config['categories'] = existing_categories

                    # Update category_order to include all categories
                    category_order = plugin_config.get('category_order', []).copy()
                    all_category_names = set(existing_categories.keys())
                    for cat_name in all_category_names:
                        if cat_name not in category_order:
                            category_order.append(cat_name)
                    if category_order:
                        plugin_config['category_order'] = category_order

        # If no config exists, return defaults
        if not plugin_config:
            plugin_config = {
                'enabled': True,
                'display_duration': 30
            }

        return success_response(data=plugin_config)
    except Exception as e:
        from src.web_interface.errors import WebInterfaceError
        error = WebInterfaceError.from_exception(e, ErrorCode.CONFIG_LOAD_FAILED)
        return error_response(
            error.error_code,
            error.message,
            details=error.details,
            context=error.context,
            status_code=500
        )
@api_v3.route('/plugins/update', methods=['POST'])
def update_plugin():
    """Update plugin"""
    try:
        # Support both JSON and form data
        content_type = request.content_type or ''

        if 'application/json' in content_type:
            # JSON request
            data, error = validate_request_json(['plugin_id'])
            if error:
                logger.debug("[UPDATE] JSON validation failed. Content-Type: %s", content_type)
                return error
        else:
            # Form data or query string
            plugin_id = request.args.get('plugin_id') or request.form.get('plugin_id')
            if not plugin_id:
                logger.debug("[UPDATE] Missing plugin_id. Content-Type: %s", content_type)
                return error_response(
                    ErrorCode.INVALID_INPUT,
                    'plugin_id required',
                    status_code=400
                )
            data = {'plugin_id': plugin_id}

        if not api_v3.plugin_store_manager:
            return error_response(
                ErrorCode.SYSTEM_ERROR,
                'Plugin store manager not initialized',
                status_code=500
            )

        plugin_id = data['plugin_id']

        # Always do direct updates (they're fast git pull operations)
        # Operation queue is reserved for longer operations like install/uninstall
        plugin_dir = Path(api_v3.plugin_store_manager.plugins_dir) / plugin_id
        manifest_path = plugin_dir / "manifest.json"

        current_last_updated = None
        current_commit = None
        current_branch = None

        if manifest_path.exists():
            try:
                import json
                with open(manifest_path, 'r', encoding='utf-8') as f:
                    manifest = json.load(f)
                    current_last_updated = manifest.get('last_updated')
                if manifest.get('local_only'):
                    logger.debug("Skipping update for local-only plugin: %s", plugin_id)
                    if api_v3.operation_history:
                        api_v3.operation_history.record_operation(
                            "update",
                            plugin_id=plugin_id,
                            status="skipped",
                            details={"reason": "local_only"}
                        )
                    return success_response(message=f'Plugin {plugin_id} is managed locally and does not receive registry updates')
            except Exception as e:
                logger.debug("Could not read local manifest for plugin: %s", e)

        if api_v3.plugin_store_manager:
            git_info_before = api_v3.plugin_store_manager._get_local_git_info(plugin_dir)
            if git_info_before:
                current_commit = git_info_before.get('sha')
                current_branch = git_info_before.get('branch')

        # Check if plugin is a git repo first (for better error messages)
        plugin_path_dir = Path(api_v3.plugin_store_manager.plugins_dir) / plugin_id
        is_git_repo = False
        if plugin_path_dir.exists():
            git_info = api_v3.plugin_store_manager._get_local_git_info(plugin_path_dir)
            is_git_repo = git_info is not None
            if is_git_repo:
                logger.debug("Plugin is a git repository, will update via git pull")

        remote_info = api_v3.plugin_store_manager.get_plugin_info(plugin_id, fetch_latest_from_github=True)
        remote_commit = remote_info.get('last_commit_sha') if remote_info else None
        remote_branch = remote_info.get('branch') if remote_info else None

        # Update the plugin
        success = api_v3.plugin_store_manager.update_plugin(plugin_id)

        if success:
            updated_last_updated = current_last_updated
            try:
                if manifest_path.exists():
                    import json
                    with open(manifest_path, 'r', encoding='utf-8') as f:
                        manifest = json.load(f)
                        updated_last_updated = manifest.get('last_updated', current_last_updated)
            except Exception as e:
                logger.debug("Could not read updated manifest after update: %s", e)

            updated_commit = None
            updated_branch = remote_branch or current_branch
            if api_v3.plugin_store_manager:
                git_info_after = api_v3.plugin_store_manager._get_local_git_info(plugin_dir)
                if git_info_after:
                    updated_commit = git_info_after.get('sha')
                    updated_branch = git_info_after.get('branch') or updated_branch

            message = f'Plugin {plugin_id} updated successfully'
            if current_commit and updated_commit and current_commit == updated_commit:
                message = f'Plugin {plugin_id} already up to date (commit {updated_commit[:7]})'
            elif updated_commit:
                message = f'Plugin {plugin_id} updated to commit {updated_commit[:7]}'
                if updated_branch:
                    message += f' on branch {updated_branch}'
            elif updated_last_updated and updated_last_updated != current_last_updated:
                message = f'Plugin {plugin_id} refreshed (Last Updated {updated_last_updated})'

            remote_commit_short = remote_commit[:7] if remote_commit else None
            if remote_commit_short and updated_commit and remote_commit_short != updated_commit[:7]:
                message += f' (remote latest {remote_commit_short})'

            # Invalidate schema cache
            if api_v3.schema_manager:
                api_v3.schema_manager.invalidate_cache(plugin_id)

            # Rediscover plugins
            if api_v3.plugin_manager:
                api_v3.plugin_manager.discover_plugins()
                if plugin_id in api_v3.plugin_manager.plugins:
                    api_v3.plugin_manager.reload_plugin(plugin_id)

            # Update state and history
            if api_v3.plugin_state_manager:
                api_v3.plugin_state_manager.update_plugin_state(
                    plugin_id,
                    {'last_updated': datetime.now()}
                )
            if api_v3.operation_history:
                version = _get_plugin_version(plugin_id)
                api_v3.operation_history.record_operation(
                    "update",
                    plugin_id=plugin_id,
                    status="success",
                    details={
                        "version": version,
                        "previous_commit": current_commit[:7] if current_commit else None,
                        "commit": updated_commit[:7] if updated_commit else None,
                        "branch": updated_branch
                    }
                )

            return success_response(
                data={
                    'last_updated': updated_last_updated,
                    'commit': updated_commit
                },
                message=message
            )
        else:
            plugin_path_dir = Path(api_v3.plugin_store_manager.plugins_dir) / plugin_id
            if not plugin_path_dir.exists():
                client_msg = 'Plugin update failed: plugin not found'
            else:
                git_info = api_v3.plugin_store_manager._get_local_git_info(plugin_path_dir)
                if not git_info:
                    plugin_info = api_v3.plugin_store_manager.get_plugin_info(plugin_id)
                    if not plugin_info:
                        client_msg = 'Plugin update failed: not found in registry'
                    else:
                        client_msg = 'Plugin update failed; check logs for details'
                else:
                    client_msg = 'Plugin update failed; check logs for details'
            logger.error("update_plugin failed for plugin_id=%s: %s", plugin_id, client_msg)

            if api_v3.operation_history:
                api_v3.operation_history.record_operation(
                    "update",
                    plugin_id=plugin_id,
                    status="failed",
                    error=client_msg,
                    details={
                        "previous_commit": current_commit[:7] if current_commit else None,
                        "branch": current_branch
                    }
                )

            return error_response(
                ErrorCode.PLUGIN_UPDATE_FAILED,
                client_msg,
                status_code=500
            )

    except Exception as e:
        logger.error("Unhandled exception in update endpoint", exc_info=True)
        
        from src.web_interface.errors import WebInterfaceError
        error = WebInterfaceError.from_exception(e, ErrorCode.PLUGIN_UPDATE_FAILED)
        if api_v3.operation_history:
            api_v3.operation_history.record_operation(
                "update",
                plugin_id=data.get('plugin_id') if 'data' in locals() else None,
                status="failed",
                error=str(e)
            )
        return error_response(
            error.error_code,
            error.message,
            details=error.details,
            context=error.context,
            status_code=500
        )
@api_v3.route('/plugins/uninstall', methods=['POST'])
def uninstall_plugin():
    """Uninstall plugin"""
    try:
        # Validate request
        data, error = validate_request_json(['plugin_id'])
        if error:
            return error

        if not api_v3.plugin_store_manager:
            return error_response(
                ErrorCode.SYSTEM_ERROR,
                'Plugin store manager not initialized',
                status_code=500
            )

        plugin_id = data['plugin_id']
        preserve_config = data.get('preserve_config', False)

        # Both queued and direct paths use the same transactional helper so
        # snapshot/rollback behaviour is consistent regardless of deployment.
        if api_v3.operation_queue:
            def uninstall_callback(operation):
                """Callback to execute plugin uninstallation via transactional helper."""
                success, error_msg = _do_transactional_uninstall(plugin_id, preserve_config)
                if not success:
                    if api_v3.operation_history:
                        api_v3.operation_history.record_operation(
                            "uninstall",
                            plugin_id=plugin_id,
                            status="failed",
                            error=error_msg
                        )
                    raise Exception(error_msg or f'Failed to uninstall plugin {plugin_id}')
                if api_v3.operation_history:
                    api_v3.operation_history.record_operation(
                        "uninstall",
                        plugin_id=plugin_id,
                        status="success",
                        details={"preserve_config": preserve_config}
                    )
                return {'success': True, 'message': 'Plugin uninstalled successfully'}

            # Enqueue operation
            operation_id = api_v3.operation_queue.enqueue_operation(
                OperationType.UNINSTALL,
                plugin_id,
                operation_callback=uninstall_callback
            )

            return success_response(
                data={'operation_id': operation_id},
                message='Plugin uninstallation queued'
            )
        else:
            # Direct (non-queued) transactional uninstall
            success, error_msg = _do_transactional_uninstall(plugin_id, preserve_config)

            if success:
                if api_v3.operation_history:
                    api_v3.operation_history.record_operation(
                        "uninstall",
                        plugin_id=plugin_id,
                        status="success",
                        details={"preserve_config": preserve_config}
                    )
                return success_response(message='Plugin uninstalled successfully')
            else:
                if api_v3.operation_history:
                    api_v3.operation_history.record_operation(
                        "uninstall",
                        plugin_id=plugin_id,
                        status="failed",
                        error=error_msg
                    )
                return error_response(
                    ErrorCode.PLUGIN_UNINSTALL_FAILED,
                    error_msg or 'Plugin uninstall failed',
                    status_code=500
                )

    except Exception as e:
        from src.web_interface.errors import WebInterfaceError
        error = WebInterfaceError.from_exception(e, ErrorCode.PLUGIN_UNINSTALL_FAILED)
        if api_v3.operation_history:
            api_v3.operation_history.record_operation(
                "uninstall",
                plugin_id=data.get('plugin_id') if 'data' in locals() else None,
                status="failed",
                error=str(e)
            )
        return error_response(
            error.error_code,
            error.message,
            details=error.details,
            context=error.context,
            status_code=500
        )
@api_v3.route('/plugins/install', methods=['POST'])
def install_plugin():
    """Install plugin from store"""
    try:
        if not api_v3.plugin_store_manager:
            return jsonify({'status': 'error', 'message': 'Plugin store manager not initialized'}), 500

        data = request.get_json(silent=True)
        if not data or 'plugin_id' not in data:
            return jsonify({'status': 'error', 'message': 'plugin_id required'}), 400

        plugin_id = data['plugin_id']
        branch = data.get('branch')  # Optional branch parameter

        # Install the plugin
        # Log the plugins directory being used for debugging
        plugins_dir = api_v3.plugin_store_manager.plugins_dir
        branch_info = f" (branch: {branch})" if branch else ""
        logger.info("Installing plugin to directory: %s", plugins_dir)

        # Use operation queue if available
        if api_v3.operation_queue:
            def install_callback(operation):
                """Callback to execute plugin installation."""
                success = api_v3.plugin_store_manager.install_plugin(plugin_id, branch=branch)

                if success:
                    # Invalidate schema cache
                    if api_v3.schema_manager:
                        api_v3.schema_manager.invalidate_cache(plugin_id)

                    # Discover and load the new plugin
                    if api_v3.plugin_manager:
                        api_v3.plugin_manager.discover_plugins()
                        api_v3.plugin_manager.load_plugin(plugin_id)

                    # Update state manager
                    if api_v3.plugin_state_manager:
                        api_v3.plugin_state_manager.set_plugin_installed(plugin_id)

                    # Record in history
                    if api_v3.operation_history:
                        version = _get_plugin_version(plugin_id)
                        api_v3.operation_history.record_operation(
                            "install",
                            plugin_id=plugin_id,
                            status="success",
                            details={"version": version, "branch": branch}
                        )

                    branch_msg = f" (branch: {branch})" if branch else ""
                    return {'success': True, 'message': f'Plugin {plugin_id} installed successfully{branch_msg}'}
                else:
                    error_msg = f'Failed to install plugin {plugin_id}'
                    if branch:
                        error_msg += f' (branch: {branch})'
                    plugin_info = api_v3.plugin_store_manager.get_plugin_info(plugin_id)
                    if not plugin_info:
                        error_msg += ' (plugin not found in registry)'

                    # Record failure in history
                    if api_v3.operation_history:
                        api_v3.operation_history.record_operation(
                            "install",
                            plugin_id=plugin_id,
                            status="failed",
                            error=error_msg,
                            details={"branch": branch}
                        )

                    raise Exception(error_msg)

            # Enqueue operation
            operation_id = api_v3.operation_queue.enqueue_operation(
                OperationType.INSTALL,
                plugin_id,
                operation_callback=install_callback
            )

            branch_msg = f" (branch: {branch})" if branch else ""
            return success_response(
                data={'operation_id': operation_id},
                message=f'Plugin {plugin_id} installation queued{branch_msg}'
            )
        else:
            # Fallback to direct installation
            success = api_v3.plugin_store_manager.install_plugin(plugin_id, branch=branch)

            if success:
                if api_v3.schema_manager:
                    api_v3.schema_manager.invalidate_cache(plugin_id)
                if api_v3.plugin_manager:
                    api_v3.plugin_manager.discover_plugins()
                    api_v3.plugin_manager.load_plugin(plugin_id)
                if api_v3.plugin_state_manager:
                    api_v3.plugin_state_manager.set_plugin_installed(plugin_id)
                if api_v3.operation_history:
                    version = _get_plugin_version(plugin_id)
                    api_v3.operation_history.record_operation(
                        "install",
                        plugin_id=plugin_id,
                        status="success",
                        details={"version": version, "branch": branch}
                    )

                branch_msg = f" (branch: {branch})" if branch else ""
                return success_response(message=f'Plugin installed successfully{branch_msg}')
            else:
                error_msg = f'Failed to install plugin {plugin_id}'
                if branch:
                    error_msg += f' (branch: {branch})'
                plugin_info = api_v3.plugin_store_manager.get_plugin_info(plugin_id)
                if not plugin_info:
                    error_msg += ' (plugin not found in registry)'

                if api_v3.operation_history:
                    api_v3.operation_history.record_operation(
                        "install",
                        plugin_id=plugin_id,
                        status="failed",
                        error=error_msg,
                        details={"branch": branch}
                    )

                return error_response(
                    ErrorCode.PLUGIN_INSTALL_FAILED,
                    error_msg,
                    status_code=500
                )

    except Exception as e:
        logger.error('Error in install_plugin', exc_info=True)
        return jsonify({'status': 'error', 'message': 'An error occurred; see logs for details', 'details': describe_exception(e)}), 500
@api_v3.route('/plugins/install-from-url', methods=['POST'])
def install_plugin_from_url():
    """Install plugin from custom GitHub URL"""
    try:
        if not api_v3.plugin_store_manager:
            return jsonify({'status': 'error', 'message': 'Plugin store manager not initialized'}), 500

        data = request.get_json(silent=True)
        if not data or 'repo_url' not in data:
            return jsonify({'status': 'error', 'message': 'repo_url required'}), 400

        # A non-string repo_url is a client mistake, not a server fault:
        # .strip() would raise and the catch-all would report it as a 500.
        if not isinstance(data['repo_url'], str) or not data['repo_url'].strip():
            return jsonify({'status': 'error', 'message': 'repo_url must be a non-empty string'}), 400

        repo_url = data['repo_url'].strip()
        plugin_id = data.get('plugin_id')  # Optional, for monorepo installations
        plugin_path = data.get('plugin_path')  # Optional, for monorepo subdirectory
        branch = data.get('branch')  # Optional branch parameter

        # Install the plugin
        result = api_v3.plugin_store_manager.install_from_url(
            repo_url=repo_url,
            plugin_id=plugin_id,
            plugin_path=plugin_path,
            branch=branch
        )

        if result.get('success'):
            # Invalidate schema cache for the installed plugin
            installed_plugin_id = result.get('plugin_id')
            if api_v3.schema_manager and installed_plugin_id:
                api_v3.schema_manager.invalidate_cache(installed_plugin_id)

            # Discover and load the new plugin
            if api_v3.plugin_manager and installed_plugin_id:
                api_v3.plugin_manager.discover_plugins()
                api_v3.plugin_manager.load_plugin(installed_plugin_id)

            branch_msg = f" (branch: {result.get('branch', branch)})" if (result.get('branch') or branch) else ""
            response_data = {
                'status': 'success',
                'message': f"Plugin {installed_plugin_id} installed successfully{branch_msg}",
                'plugin_id': installed_plugin_id,
                'name': result.get('name')
            }
            if result.get('branch'):
                response_data['branch'] = result.get('branch')
            return jsonify(response_data)
        else:
            return jsonify({
                'status': 'error',
                'message': result.get('error', 'Failed to install plugin from URL')
            }), 500

    except Exception as e:
        logger.error('Error in install_plugin_from_url', exc_info=True)
        return jsonify({'status': 'error', 'message': 'An error occurred; see logs for details', 'details': describe_exception(e)}), 500
@api_v3.route('/plugins/registry-from-url', methods=['POST'])
def get_registry_from_url():
    """Get plugin list from a registry-style monorepo URL"""
    try:
        if not api_v3.plugin_store_manager:
            return jsonify({'status': 'error', 'message': 'Plugin store manager not initialized'}), 500

        data = request.get_json(silent=True)
        if not data or 'repo_url' not in data:
            return jsonify({'status': 'error', 'message': 'repo_url required'}), 400

        # A non-string repo_url is a client mistake, not a server fault:
        # .strip() would raise and the catch-all would report it as a 500.
        if not isinstance(data['repo_url'], str) or not data['repo_url'].strip():
            return jsonify({'status': 'error', 'message': 'repo_url must be a non-empty string'}), 400

        repo_url = data['repo_url'].strip()

        # Get registry from the URL
        registry = api_v3.plugin_store_manager.fetch_registry_from_url(repo_url)

        if registry:
            return jsonify({
                'status': 'success',
                'plugins': registry.get('plugins', []),
                'registry_url': repo_url
            })
        else:
            return jsonify({
                'status': 'error',
                'message': 'Failed to fetch registry from URL or URL does not contain a valid registry'
            }), 400

    except Exception as e:
        logger.error('Error in get_registry_from_url', exc_info=True)
        return jsonify({'status': 'error', 'message': 'An error occurred; see logs for details', 'details': describe_exception(e)}), 500
@api_v3.route('/plugins/saved-repositories', methods=['GET'])
def get_saved_repositories():
    """Get all saved repositories"""
    try:
        if not api_v3.saved_repositories_manager:
            return jsonify({'status': 'error', 'message': 'Saved repositories manager not initialized'}), 500

        repositories = api_v3.saved_repositories_manager.get_all()
        return jsonify({'status': 'success', 'data': {'repositories': repositories}})
    except Exception as e:
        logger.error('Error in get_saved_repositories', exc_info=True)
        return jsonify({'status': 'error', 'message': 'An error occurred; see logs for details', 'details': describe_exception(e)}), 500
@api_v3.route('/plugins/saved-repositories', methods=['POST'])
def add_saved_repository():
    """Add a repository to saved list"""
    try:
        if not api_v3.saved_repositories_manager:
            return jsonify({'status': 'error', 'message': 'Saved repositories manager not initialized'}), 500

        data = request.get_json(silent=True)
        if not data or 'repo_url' not in data:
            return jsonify({'status': 'error', 'message': 'repo_url required'}), 400

        # A non-string repo_url is a client mistake, not a server fault:
        # .strip() would raise and the catch-all would report it as a 500.
        if not isinstance(data['repo_url'], str) or not data['repo_url'].strip():
            return jsonify({'status': 'error', 'message': 'repo_url must be a non-empty string'}), 400

        repo_url = data['repo_url'].strip()
        name = data.get('name')

        success = api_v3.saved_repositories_manager.add(repo_url, name)

        if success:
            return jsonify({
                'status': 'success',
                'message': 'Repository saved successfully',
                'data': {'repositories': api_v3.saved_repositories_manager.get_all()}
            })
        else:
            return jsonify({
                'status': 'error',
                'message': 'Repository already exists or failed to save'
            }), 400
    except Exception as e:
        logger.error('Error in add_saved_repository', exc_info=True)
        return jsonify({'status': 'error', 'message': 'An error occurred; see logs for details', 'details': describe_exception(e)}), 500
@api_v3.route('/plugins/saved-repositories', methods=['DELETE'])
def remove_saved_repository():
    """Remove a repository from saved list"""
    try:
        if not api_v3.saved_repositories_manager:
            return jsonify({'status': 'error', 'message': 'Saved repositories manager not initialized'}), 500

        data = request.get_json(silent=True)
        if not data or 'repo_url' not in data:
            return jsonify({'status': 'error', 'message': 'repo_url required'}), 400

        repo_url = data['repo_url']

        success = api_v3.saved_repositories_manager.remove(repo_url)

        if success:
            return jsonify({
                'status': 'success',
                'message': 'Repository removed successfully',
                'data': {'repositories': api_v3.saved_repositories_manager.get_all()}
            })
        else:
            return jsonify({
                'status': 'error',
                'message': 'Repository not found'
            }), 404
    except Exception as e:
        logger.error('Error in remove_saved_repository', exc_info=True)
        return jsonify({'status': 'error', 'message': 'An error occurred; see logs for details', 'details': describe_exception(e)}), 500
@api_v3.route('/plugins/store/list', methods=['GET'])
def list_plugin_store():
    """Search plugin store"""
    try:
        if not api_v3.plugin_store_manager:
            return jsonify({'status': 'error', 'message': 'Plugin store manager not initialized'}), 500

        query = request.args.get('query', '')
        category = request.args.get('category', '')
        tags = request.args.getlist('tags')
        # Default to fetching commit metadata to ensure accurate commit timestamps
        fetch_commit_param = request.args.get('fetch_commit_info', request.args.get('fetch_latest_versions', '')).lower()
        fetch_commit = fetch_commit_param != 'false'

        # Search plugins from the registry (including saved repositories)
        plugins = api_v3.plugin_store_manager.search_plugins(
            query=query,
            category=category,
            tags=tags,
            fetch_commit_info=fetch_commit,
            include_saved_repos=True,
            saved_repositories_manager=api_v3.saved_repositories_manager
        )

        # Format plugins for the web interface
        formatted_plugins = []
        for plugin in plugins:
            formatted_plugins.append({
                'id': plugin.get('id'),
                'name': plugin.get('name'),
                'author': plugin.get('author'),
                'category': plugin.get('category'),
                'description': plugin.get('description'),
                'tags': plugin.get('tags', []),
                'stars': plugin.get('stars', 0),
                'verified': plugin.get('verified', False),
                'repo': plugin.get('repo', ''),
                'last_updated': plugin.get('last_updated') or plugin.get('last_updated_iso', ''),
                'last_updated_iso': plugin.get('last_updated_iso', ''),
                'last_commit': plugin.get('last_commit') or plugin.get('last_commit_sha'),
                'last_commit_message': plugin.get('last_commit_message'),
                'last_commit_author': plugin.get('last_commit_author'),
                'version': plugin.get('latest_version') or plugin.get('version', ''),
                'branch': plugin.get('branch') or plugin.get('default_branch'),
                'default_branch': plugin.get('default_branch'),
                'plugin_path': plugin.get('plugin_path', '')
            })

        return jsonify({'status': 'success', 'data': {'plugins': formatted_plugins}})
    except Exception as e:
        logger.error('Error in list_plugin_store', exc_info=True)
        return jsonify({'status': 'error', 'message': 'An error occurred; see logs for details', 'details': describe_exception(e)}), 500
@api_v3.route('/plugins/store/github-status', methods=['GET'])
def get_github_auth_status():
    """Check if GitHub authentication is configured and validate token"""
    try:
        if not api_v3.plugin_store_manager:
            return jsonify({'status': 'error', 'message': 'Plugin store manager not initialized'}), 500
        
        token = api_v3.plugin_store_manager.github_token
        
        # Check if GitHub token is configured
        if not token or len(token) == 0:
            return jsonify({
                'status': 'success',
                'data': {
                    'token_status': 'none',
                    'authenticated': False,
                    'rate_limit': 60,
                    'message': 'No GitHub token configured',
                    'error': None
                }
            })
        
        # Validate the token
        is_valid, error_message = api_v3.plugin_store_manager._validate_github_token(token)
        
        if is_valid:
            return jsonify({
                'status': 'success',
                'data': {
                    'token_status': 'valid',
                    'authenticated': True,
                    'rate_limit': 5000,
                    'message': 'GitHub API authenticated',
                    'error': None
                }
            })
        else:
            return jsonify({
                'status': 'success',
                'data': {
                    'token_status': 'invalid',
                    'authenticated': False,
                    'rate_limit': 60,
                    'message': f'GitHub token is invalid: {error_message}' if error_message else 'GitHub token is invalid',
                    'error': error_message
                }
            })
    except Exception as e:
        logger.error('Error in get_github_auth_status', exc_info=True)
        return jsonify({'status': 'error', 'message': 'An error occurred; see logs for details', 'details': describe_exception(e)}), 500
@api_v3.route('/plugins/store/refresh', methods=['POST'])
def refresh_plugin_store():
    """Refresh plugin store repository"""
    try:
        if not api_v3.plugin_store_manager:
            return jsonify({'status': 'error', 'message': 'Plugin store manager not initialized'}), 500

        data = request.get_json(silent=True) or {}
        fetch_commit_info = data.get('fetch_commit_info', data.get('fetch_latest_versions', False))

        # Force refresh the registry
        registry = api_v3.plugin_store_manager.fetch_registry(force_refresh=True)
        plugin_count = len(registry.get('plugins', []))

        message = 'Plugin store refreshed'
        if fetch_commit_info:
            message += ' (with refreshed commit metadata from GitHub)'

        return jsonify({
            'status': 'success',
            'message': message,
            'plugin_count': plugin_count
        })
    except Exception as e:
        logger.error('Error in refresh_plugin_store', exc_info=True)
        return jsonify({'status': 'error', 'message': 'An error occurred; see logs for details', 'details': describe_exception(e)}), 500
@api_v3.route('/plugins/config', methods=['POST'])
def save_plugin_config():
    """Save plugin configuration, separating secrets from regular config"""
    try:
        if not api_v3.config_manager:
            return error_response(
                ErrorCode.SYSTEM_ERROR,
                'Config manager not initialized',
                status_code=500
            )

        # Support both JSON and form data (for HTMX submissions)
        content_type = request.content_type or ''

        if 'application/json' in content_type:
            # JSON request
            data, error = validate_request_json(['plugin_id'])
            if error:
                return error
            plugin_id = data['plugin_id']
            plugin_config = data.get('config', {})
        else:
            # Form data (HTMX submission)
            # plugin_id comes from query string, config from form fields
            plugin_id = request.args.get('plugin_id')
            if not plugin_id:
                return error_response(
                    ErrorCode.INVALID_INPUT,
                    'plugin_id required in query string',
                    status_code=400
                )

            # Load existing config as base (partial form updates should merge, not replace)
            existing_config = {}
            if api_v3.config_manager:
                full_config = api_v3.config_manager.load_config()
                existing_config = full_config.get(plugin_id, {}).copy()

            # Get schema manager instance (needed for type conversion)
            schema_mgr = api_v3.schema_manager
            if not schema_mgr:
                return error_response(
                    ErrorCode.SYSTEM_ERROR,
                    'Schema manager not initialized',
                    status_code=500
                )

            # Load plugin schema BEFORE processing form data (needed for type conversion)
            schema = schema_mgr.load_schema(plugin_id, use_cache=False)

            # Start with existing config and apply form updates
            plugin_config = existing_config

            # Convert form data to config dict
            # Form fields can use dot notation for nested values (e.g., "transition.type")
            form_data = request.form.to_dict()

            # First pass: handle bracket notation array fields (e.g., "field_name[]" from checkbox-group)
            # These fields use getlist() to preserve all values, then replace in form_data
            # Sentinel empty value ("") allows clearing array to [] when all checkboxes unchecked
            bracket_array_fields = {}  # Maps base field path to list of values
            for key in request.form.keys():
                # Check if key ends with "[]" (bracket notation for array fields)
                if key.endswith('[]'):
                    base_path = key[:-2]  # Remove "[]" suffix
                    values = request.form.getlist(key)
                    # Filter out sentinel empty string - if only sentinel present, array should be []
                    # If sentinel + values present, use the actual values
                    filtered_values = [v for v in values if v and v.strip()]
                    # If no non-empty values but key exists, it means all checkboxes unchecked (empty array)
                    bracket_array_fields[base_path] = filtered_values
                    # Remove the bracket notation key from form_data if present
                    if key in form_data:
                        del form_data[key]
            
            # Process bracket notation fields and set directly in plugin_config
            # Use JSON encoding instead of comma-join to handle values containing commas
            import json
            for base_path, values in bracket_array_fields.items():
                # Get schema property to verify it's an array
                base_prop = _get_schema_property(schema, base_path)
                if base_prop and base_prop.get('type') == 'array':
                    # Filter out empty values and sentinel empty strings
                    filtered_values = [v for v in values if v and v.strip()]
                    # Set directly in plugin_config (values are already strings, no need to parse)
                    # Empty array (all unchecked) is represented as []
                    _set_nested_value(plugin_config, base_path, filtered_values)
                    logger.debug(f"Processed bracket notation array field {base_path}: {values} -> {filtered_values}")
                    # Remove from form_data to avoid double processing
                    if base_path in form_data:
                        del form_data[base_path]

            # Second pass: detect and combine array index fields (e.g., "text_color.0", "text_color.1" -> "text_color" as array)
            # This handles cases where forms send array fields as indexed inputs
            array_fields = {}  # Maps base field path to list of (index, value) tuples
            processed_keys = set()
            indexed_base_paths = set()  # Track which base paths have indexed fields

            for key, value in form_data.items():
                # Check if this looks like an array index field (ends with .0, .1, .2, etc.)
                if '.' in key:
                    parts = key.rsplit('.', 1)  # Split on last dot
                    if len(parts) == 2:
                        base_path, last_part = parts
                        # Check if last part is a numeric string (array index)
                        if last_part.isdigit():
                            # Get schema property for the base path to verify it's an array
                            base_prop = _get_schema_property(schema, base_path)
                            if base_prop and base_prop.get('type') == 'array':
                                # This is an array index field
                                index = int(last_part)
                                if base_path not in array_fields:
                                    array_fields[base_path] = []
                                array_fields[base_path].append((index, value))
                                processed_keys.add(key)
                                indexed_base_paths.add(base_path)
                                continue

            # Process combined array fields
            for base_path, index_values in array_fields.items():
                # Sort by index and extract values
                index_values.sort(key=lambda x: x[0])
                values = [v for _, v in index_values]
                # Combine values into comma-separated string for parsing
                combined_value = ', '.join(str(v) for v in values)
                # Parse as array using schema
                parsed_value = _parse_form_value_with_schema(combined_value, base_path, schema)
                # Debug logging
                logger.debug(f"Combined indexed array field {base_path}: {values} -> {combined_value} -> {parsed_value}")
                # Only set if not skipped
                if parsed_value is not _SKIP_FIELD:
                    _set_nested_value(plugin_config, base_path, parsed_value)
            
            # Process remaining (non-indexed) fields
            # Skip any base paths that were processed as indexed arrays
            for key, value in form_data.items():
                if key not in processed_keys:
                    # Skip if this key is a base path that was processed as indexed array
                    # (to avoid overwriting the combined array with a single value)
                    if key not in indexed_base_paths:
                        # Parse value using schema to determine correct type
                        parsed_value = _parse_form_value_with_schema(value, key, schema)
                        # Debug logging for array fields
                        if schema:
                            prop = _get_schema_property(schema, key)
                            if prop and prop.get('type') == 'array':
                                logger.debug(f"Array field {key}: form value='{value}' -> parsed={parsed_value}")
                        # Use helper to set nested values correctly (skips if _SKIP_FIELD)
                        if parsed_value is not _SKIP_FIELD:
                            _set_nested_value(plugin_config, key, parsed_value)
            
            # Post-process: Fix array fields that might have been incorrectly structured
            # This handles cases where array fields are stored as dicts (e.g., from indexed form fields)
            def fix_array_structures(config_dict, schema_props, prefix=''):
                """Recursively fix array structures (convert dicts with numeric keys to arrays, fix length issues)"""
                for prop_key, prop_schema in schema_props.items():
                    prop_type = prop_schema.get('type')

                    if prop_type == 'array':
                        # Navigate to the field location
                        if prefix:
                            parent_parts = prefix.split('.')
                            parent = config_dict
                            for part in parent_parts:
                                if isinstance(parent, dict) and part in parent:
                                    parent = parent[part]
                                else:
                                    parent = None
                                    break

                            if parent is not None and isinstance(parent, dict) and prop_key in parent:
                                current_value = parent[prop_key]
                                # If it's a dict with numeric string keys, convert to array
                                if isinstance(current_value, dict) and not isinstance(current_value, list):
                                    try:
                                        # Check if all keys are numeric strings (array indices)
                                        keys = [k for k in current_value.keys()]
                                        if all(k.isdigit() for k in keys):
                                            # Convert to sorted array by index
                                            sorted_keys = sorted(keys, key=int)
                                            array_value = [current_value[k] for k in sorted_keys]
                                            # Convert array elements to correct types based on schema
                                            items_schema = prop_schema.get('items', {})
                                            item_type = items_schema.get('type')
                                            if item_type in ('number', 'integer'):
                                                converted_array = []
                                                for v in array_value:
                                                    if isinstance(v, str):
                                                        try:
                                                            if item_type == 'integer':
                                                                converted_array.append(int(v))
                                                            else:
                                                                converted_array.append(float(v))
                                                        except (ValueError, TypeError, OverflowError):
                                                            converted_array.append(v)
                                                    else:
                                                        converted_array.append(v)
                                                array_value = converted_array
                                            parent[prop_key] = array_value
                                            current_value = array_value  # Update for length check below
                                    except (ValueError, KeyError, TypeError):
                                        # Conversion failed, check if we should use default
                                        pass

                                # If it's an array, ensure correct types and check minItems
                                if isinstance(current_value, list):
                                    # First, ensure array elements are correct types
                                    items_schema = prop_schema.get('items', {})
                                    item_type = items_schema.get('type')
                                    if item_type in ('number', 'integer'):
                                        converted_array = []
                                        for v in current_value:
                                            if isinstance(v, str):
                                                try:
                                                    if item_type == 'integer':
                                                        converted_array.append(int(v))
                                                    else:
                                                        converted_array.append(float(v))
                                                except (ValueError, TypeError, OverflowError):
                                                    converted_array.append(v)
                                            else:
                                                converted_array.append(v)
                                        parent[prop_key] = converted_array
                                        current_value = converted_array

                                    # Then check minItems
                                    min_items = prop_schema.get('minItems')
                                    if min_items is not None and len(current_value) < min_items:
                                        # Use default if available, otherwise keep as-is (validation will catch it)
                                        default = prop_schema.get('default')
                                        if default and isinstance(default, list) and len(default) >= min_items:
                                            parent[prop_key] = default
                        else:
                            # Top-level field
                            if prop_key in config_dict:
                                current_value = config_dict[prop_key]
                                # If it's a dict with numeric string keys, convert to array
                                if isinstance(current_value, dict) and not isinstance(current_value, list):
                                    try:
                                        keys = list(current_value.keys())
                                        if keys and all(str(k).isdigit() for k in keys):
                                            sorted_keys = sorted(keys, key=lambda x: int(str(x)))
                                            array_value = [current_value[k] for k in sorted_keys]
                                            # Convert array elements to correct types based on schema
                                            items_schema = prop_schema.get('items', {})
                                            item_type = items_schema.get('type')
                                            if item_type in ('number', 'integer'):
                                                converted_array = []
                                                for v in array_value:
                                                    if isinstance(v, str):
                                                        try:
                                                            if item_type == 'integer':
                                                                converted_array.append(int(v))
                                                            else:
                                                                converted_array.append(float(v))
                                                        except (ValueError, TypeError, OverflowError):
                                                            converted_array.append(v)
                                                    else:
                                                        converted_array.append(v)
                                                array_value = converted_array
                                            config_dict[prop_key] = array_value
                                            current_value = array_value  # Update for length check below
                                    except (ValueError, KeyError, TypeError) as e:
                                        logger.debug(f"Failed to convert {prop_key} to array: {e}")

                                # If it's an array, ensure correct types and check minItems
                                if isinstance(current_value, list):
                                    # First, ensure array elements are correct types
                                    items_schema = prop_schema.get('items', {})
                                    item_type = items_schema.get('type')
                                    if item_type in ('number', 'integer'):
                                        converted_array = []
                                        for v in current_value:
                                            if isinstance(v, str):
                                                try:
                                                    if item_type == 'integer':
                                                        converted_array.append(int(v))
                                                    else:
                                                        converted_array.append(float(v))
                                                except (ValueError, TypeError, OverflowError):
                                                    converted_array.append(v)
                                            else:
                                                converted_array.append(v)
                                        config_dict[prop_key] = converted_array
                                        current_value = converted_array

                                    # Then check minItems
                                    min_items = prop_schema.get('minItems')
                                    if min_items is not None and len(current_value) < min_items:
                                        default = prop_schema.get('default')
                                        if default and isinstance(default, list) and len(default) >= min_items:
                                            config_dict[prop_key] = default

                    # Recurse into nested objects
                    elif prop_type == 'object' and 'properties' in prop_schema:
                        nested_prefix = f"{prefix}.{prop_key}" if prefix else prop_key
                        if prefix:
                            parent_parts = prefix.split('.')
                            parent = config_dict
                            for part in parent_parts:
                                if isinstance(parent, dict) and part in parent:
                                    parent = parent[part]
                                else:
                                    parent = None
                                    break
                            nested_dict = parent.get(prop_key) if parent is not None and isinstance(parent, dict) else None
                        else:
                            nested_dict = config_dict.get(prop_key)

                        if isinstance(nested_dict, dict):
                            # Pass no prefix: config_dict is already the navigated sub-dict,
                            # so path segments from the parent would mis-navigate it.
                            fix_array_structures(nested_dict, prop_schema['properties'])

            # Also ensure array fields that are None get converted to empty arrays
            def ensure_array_defaults(config_dict, schema_props, prefix=''):
                """Recursively ensure array fields have defaults if None"""
                for prop_key, prop_schema in schema_props.items():
                    prop_type = prop_schema.get('type')

                    if prop_type == 'array':
                        if prefix:
                            parent_parts = prefix.split('.')
                            parent = config_dict
                            for part in parent_parts:
                                if isinstance(parent, dict) and part in parent:
                                    parent = parent[part]
                                else:
                                    parent = None
                                    break

                            if parent is not None and isinstance(parent, dict):
                                if prop_key not in parent or parent[prop_key] is None:
                                    default = prop_schema.get('default', [])
                                    parent[prop_key] = default if default else []
                        else:
                            if prop_key not in config_dict or config_dict[prop_key] is None:
                                default = prop_schema.get('default', [])
                                config_dict[prop_key] = default if default else []

                    elif prop_type == 'object' and 'properties' in prop_schema:
                        nested_prefix = f"{prefix}.{prop_key}" if prefix else prop_key
                        if prefix:
                            parent_parts = prefix.split('.')
                            parent = config_dict
                            for part in parent_parts:
                                if isinstance(parent, dict) and part in parent:
                                    parent = parent[part]
                                else:
                                    parent = None
                                    break
                            nested_dict = parent.get(prop_key) if parent is not None and isinstance(parent, dict) else None
                        else:
                            nested_dict = config_dict.get(prop_key)

                        if nested_dict is None:
                            if prefix:
                                parent_parts = prefix.split('.')
                                parent = config_dict
                                for part in parent_parts:
                                    if part not in parent:
                                        parent[part] = {}
                                    parent = parent[part]
                                if prop_key not in parent:
                                    parent[prop_key] = {}
                                nested_dict = parent[prop_key]
                            else:
                                if prop_key not in config_dict:
                                    config_dict[prop_key] = {}
                                nested_dict = config_dict[prop_key]

                        if isinstance(nested_dict, dict):
                            # Pass no prefix: config_dict is already navigated.
                            ensure_array_defaults(nested_dict, prop_schema['properties'])

            if schema and 'properties' in schema:
                # First, fix any dict structures that should be arrays
                # This must be called BEFORE validation to convert dicts with numeric keys to arrays
                fix_array_structures(plugin_config, schema['properties'])
                # Then, ensure None arrays get defaults
                ensure_array_defaults(plugin_config, schema['properties'])
                
                # Debug: Log the structure after fixing
                if 'feeds' in plugin_config and 'custom_feeds' in plugin_config.get('feeds', {}):
                    custom_feeds = plugin_config['feeds']['custom_feeds']
                    logger.debug(f"After fix_array_structures: custom_feeds type={type(custom_feeds)}, value={custom_feeds}")
                
                # Force fix for feeds.custom_feeds if it's still a dict (fallback)
                if 'feeds' in plugin_config:
                    feeds_config = plugin_config.get('feeds') or {}
                    if feeds_config and 'custom_feeds' in feeds_config and isinstance(feeds_config['custom_feeds'], dict):
                        custom_feeds_dict = feeds_config['custom_feeds']
                        # Check if all keys are numeric
                        keys = list(custom_feeds_dict.keys())
                        if keys and all(str(k).isdigit() for k in keys):
                            # Convert to array
                            sorted_keys = sorted(keys, key=lambda x: int(str(x)))
                            feeds_config['custom_feeds'] = [custom_feeds_dict[k] for k in sorted_keys]
                            logger.info(f"Force-converted feeds.custom_feeds from dict to array: {len(feeds_config['custom_feeds'])} items")

            # Fix unchecked boolean checkboxes: HTML checkboxes don't submit values
            # when unchecked, so the existing config value (potentially True) persists.
            # Walk the schema and set any boolean fields missing from form data to False.
            if schema and 'properties' in schema:
                form_keys = set(request.form.keys())
                _set_missing_booleans_to_false(plugin_config, schema['properties'], form_keys)

        # Get schema manager instance (for JSON requests)
        schema_mgr = api_v3.schema_manager
        if not schema_mgr:
            return error_response(
                ErrorCode.SYSTEM_ERROR,
                'Schema manager not initialized',
                status_code=500
            )

        # Load plugin schema using SchemaManager (force refresh to get latest schema)
        # For JSON requests, schema wasn't loaded yet
        if 'application/json' in content_type:
            schema = schema_mgr.load_schema(plugin_id, use_cache=False)

        # JSON path: fix numeric-keyed dicts that should be arrays.
        # JS dotToNested() converts feeds.custom_feeds.0.name → {'0': {name:...}}
        # instead of [{name:...}]. The form-data path has fix_array_structures for this;
        # mirror that logic here for JSON submissions.
        if 'application/json' in content_type and schema and 'properties' in schema:
            def _fix_json_arrays(cfg, props):
                for k, ps in props.items():
                    if not isinstance(cfg, dict) or k not in cfg:
                        continue
                    pt = ps.get('type')
                    val = cfg[k]
                    if pt == 'array':
                        items_schema = ps.get('items', {})
                        item_type = items_schema.get('type')
                        if isinstance(val, dict):
                            keys = list(val.keys())
                            if keys and all(str(x).isdigit() for x in keys):
                                sorted_keys = sorted(keys, key=lambda x: int(str(x)))
                                arr = [val[sk] for sk in sorted_keys]
                                if item_type in ('integer', 'number'):
                                    converted = []
                                    for v in arr:
                                        if isinstance(v, str):
                                            try:
                                                converted.append(int(v) if item_type == 'integer' else float(v))
                                            except (ValueError, TypeError, OverflowError):
                                                converted.append(v)
                                        else:
                                            converted.append(v)
                                    arr = converted
                                cfg[k] = arr
                            elif not keys:
                                cfg[k] = []
                        # Recurse into each element when items are objects with properties,
                        # covering both freshly-converted and already-list values.
                        if item_type == 'object' and 'properties' in items_schema:
                            for elem in (cfg[k] if isinstance(cfg[k], list) else []):
                                if isinstance(elem, dict):
                                    _fix_json_arrays(elem, items_schema['properties'])
                    elif pt == 'object' and 'properties' in ps and isinstance(val, dict):
                        _fix_json_arrays(val, ps['properties'])
            _fix_json_arrays(plugin_config, schema['properties'])

        # PRE-PROCESSING: Preserve 'enabled' state if not in request
        # This prevents overwriting the enabled state when saving config from a form that doesn't include the toggle
        if 'enabled' not in plugin_config:
            try:
                current_config = api_v3.config_manager.load_config()
                if plugin_id in current_config and 'enabled' in current_config[plugin_id]:
                    plugin_config['enabled'] = current_config[plugin_id]['enabled']
                    # logger.debug(f"Preserving enabled state for {plugin_id}: {plugin_config['enabled']}")
                elif api_v3.plugin_manager:
                    # Fallback to plugin instance if config doesn't have it
                    plugin_instance = api_v3.plugin_manager.get_plugin(plugin_id)
                    if plugin_instance:
                        plugin_config['enabled'] = plugin_instance.enabled
                # Final fallback: default to True if plugin is loaded (matches BasePlugin default)
                if 'enabled' not in plugin_config:
                    plugin_config['enabled'] = True
            except Exception as e:
                logger.debug("Error preserving enabled state: %s", e)
                # Default to True on error to avoid disabling plugins
                plugin_config['enabled'] = True

        # Find secret fields (supports nested schemas and array-item secrets)
        secret_fields = set()

        if schema and 'properties' in schema:
            secret_fields = find_secret_fields(schema['properties'])

        # Apply defaults from schema to config BEFORE validation
        # This ensures required fields with defaults are present before validation
        # Store preserved enabled value before merge to protect it from defaults
        preserved_enabled = None
        if 'enabled' in plugin_config:
            preserved_enabled = plugin_config['enabled']

        if schema:
            defaults = schema_mgr.generate_default_config(plugin_id, use_cache=True)
            plugin_config = schema_mgr.merge_with_defaults(plugin_config, defaults)

        # After merging defaults, replace any None array values with their schema defaults.
        # merge_with_defaults gives user config higher priority, so a None submitted by
        # the client can survive the merge — this pass cleans those up.
        def _fix_none_arrays(cfg, props):
            for k, pschema in props.items():
                if pschema.get('type') == 'array':
                    if isinstance(cfg, dict) and (k not in cfg or cfg[k] is None):
                        cfg[k] = pschema.get('default', [])
                elif pschema.get('type') == 'object' and 'properties' in pschema:
                    if isinstance(cfg, dict) and isinstance(cfg.get(k), dict):
                        _fix_none_arrays(cfg[k], pschema['properties'])

        if schema and 'properties' in schema and isinstance(plugin_config, dict):
            _fix_none_arrays(plugin_config, schema['properties'])

        # Ensure enabled state is preserved after defaults merge
        # Defaults should not overwrite an explicitly preserved enabled value
        if preserved_enabled is not None:
            # Restore preserved value if it was changed by defaults merge
            if plugin_config.get('enabled') != preserved_enabled:
                plugin_config['enabled'] = preserved_enabled

        # Normalize config data: convert string numbers to integers/floats where schema expects numbers
        # This handles form data which sends everything as strings
        def normalize_config_values(config, schema_props, prefix=''):
            """Recursively normalize config values based on schema types"""
            if not isinstance(config, dict) or not isinstance(schema_props, dict):
                return config

            normalized = {}
            for key, value in config.items():
                field_path = f"{prefix}.{key}" if prefix else key

                if key not in schema_props:
                    # Field not in schema, keep as-is (will be caught by additionalProperties check if needed)
                    normalized[key] = value
                    continue

                prop_schema = schema_props[key]
                prop_type = prop_schema.get('type')

                # Handle union types (e.g., ["integer", "null"])
                if isinstance(prop_type, list):
                    # Check if null is allowed and value is empty/null
                    if 'null' in prop_type:
                        # Handle various representations of null/empty
                        if value is None:
                            normalized[key] = None
                            continue
                        elif isinstance(value, str):
                            # Strip whitespace and check for null representations
                            value_stripped = value.strip()
                            if value_stripped == '' or value_stripped.lower() in ('null', 'none', 'undefined'):
                                normalized[key] = None
                                continue

                    # Try to normalize based on non-null types in the union
                    # Check integer first (more specific than number)
                    if 'integer' in prop_type:
                        if isinstance(value, str):
                            value_stripped = value.strip()
                            if value_stripped == '':
                                # Empty string with null allowed - already handled above, but double-check
                                if 'null' in prop_type:
                                    normalized[key] = None
                                    continue
                            try:
                                normalized[key] = int(value_stripped)
                                continue
                            except (ValueError, TypeError, OverflowError):
                                pass
                        elif isinstance(value, (int, float)):
                            normalized[key] = int(value)
                            continue

                    # Check number (less specific, but handles floats)
                    if 'number' in prop_type:
                        if isinstance(value, str):
                            value_stripped = value.strip()
                            if value_stripped == '':
                                # Empty string with null allowed - already handled above, but double-check
                                if 'null' in prop_type:
                                    normalized[key] = None
                                    continue
                            try:
                                normalized[key] = float(value_stripped)
                                continue
                            except (ValueError, TypeError, OverflowError):
                                pass
                        elif isinstance(value, (int, float)):
                            normalized[key] = float(value)
                            continue

                    # Check boolean
                    if 'boolean' in prop_type:
                        if isinstance(value, str):
                            normalized[key] = value.strip().lower() in ('true', '1', 'on', 'yes')
                            continue

                    # If no conversion worked and null is allowed, try to set to None
                    # This handles cases where the value is an empty string or can't be converted
                    if 'null' in prop_type:
                        if isinstance(value, str):
                            value_stripped = value.strip()
                            if value_stripped == '' or value_stripped.lower() in ('null', 'none', 'undefined'):
                                normalized[key] = None
                                continue
                        # If it's already None, keep it
                        if value is None:
                            normalized[key] = None
                            continue

                    # If no conversion worked, keep original value (will fail validation, but that's expected)
                    # Log a warning for debugging
                    logger.warning(f"Could not normalize field {field_path}: value={repr(value)}, type={type(value)}, schema_type={prop_type}")
                    normalized[key] = value
                    continue

                if isinstance(value, dict) and prop_type == 'object' and 'properties' in prop_schema:
                    # Recursively normalize nested objects
                    normalized[key] = normalize_config_values(value, prop_schema['properties'], field_path)
                elif isinstance(value, list) and prop_type == 'array' and 'items' in prop_schema:
                    # Normalize array items
                    items_schema = prop_schema['items']
                    item_type = items_schema.get('type')

                    # Handle union types in array items
                    if isinstance(item_type, list):
                        normalized_array = []
                        for v in value:
                            # Check if null is allowed
                            if 'null' in item_type:
                                if v is None or v == '' or (isinstance(v, str) and v.lower() in ('null', 'none')):
                                    normalized_array.append(None)
                                    continue

                            # Try to normalize based on non-null types
                            if 'integer' in item_type:
                                if isinstance(v, str):
                                    try:
                                        normalized_array.append(int(v))
                                        continue
                                    except (ValueError, TypeError, OverflowError):
                                        pass
                                elif isinstance(v, (int, float)):
                                    normalized_array.append(int(v))
                                    continue
                            elif 'number' in item_type:
                                if isinstance(v, str):
                                    try:
                                        normalized_array.append(float(v))
                                        continue
                                    except (ValueError, TypeError, OverflowError):
                                        pass
                                elif isinstance(v, (int, float)):
                                    normalized_array.append(float(v))
                                    continue

                            # If no conversion worked, keep original value
                            normalized_array.append(v)
                        normalized[key] = normalized_array
                    elif item_type == 'integer':
                        # Convert string numbers to integers
                        normalized_array = []
                        for v in value:
                            if isinstance(v, str):
                                try:
                                    normalized_array.append(int(v))
                                except (ValueError, TypeError, OverflowError):
                                    normalized_array.append(v)
                            elif isinstance(v, (int, float)):
                                normalized_array.append(int(v))
                            else:
                                normalized_array.append(v)
                        normalized[key] = normalized_array
                    elif item_type == 'number':
                        # Convert string numbers to floats
                        normalized_array = []
                        for v in value:
                            if isinstance(v, str):
                                try:
                                    normalized_array.append(float(v))
                                except (ValueError, TypeError, OverflowError):
                                    normalized_array.append(v)
                            else:
                                normalized_array.append(v)
                        normalized[key] = normalized_array
                    elif item_type == 'object' and 'properties' in items_schema:
                        # Recursively normalize array of objects
                        normalized_array = []
                        for v in value:
                            if isinstance(v, dict):
                                normalized_array.append(
                                    normalize_config_values(v, items_schema['properties'], f"{field_path}[]")
                                )
                            else:
                                normalized_array.append(v)
                        normalized[key] = normalized_array
                    else:
                        normalized[key] = value
                elif prop_type == 'integer':
                    # Convert string to integer
                    if isinstance(value, str):
                        try:
                            normalized[key] = int(value)
                        except (ValueError, TypeError, OverflowError):
                            normalized[key] = value
                    else:
                        normalized[key] = value
                elif prop_type == 'number':
                    # Convert string to float
                    if isinstance(value, str):
                        try:
                            normalized[key] = float(value)
                        except (ValueError, TypeError, OverflowError):
                            normalized[key] = value
                    else:
                        normalized[key] = value
                elif prop_type == 'boolean':
                    # Convert string booleans
                    if isinstance(value, str):
                        normalized[key] = value.lower() in ('true', '1', 'on', 'yes')
                    else:
                        normalized[key] = value
                else:
                    normalized[key] = value

            return normalized

        # Normalize config before validation
        if schema and 'properties' in schema:
            plugin_config = normalize_config_values(plugin_config, schema['properties'])

        # Filter config to only include schema-defined fields (important when additionalProperties is false)
        # Use enhanced schema with core properties to ensure core properties are preserved during filtering
        if schema and 'properties' in schema:
            enhanced_schema_for_filtering = _enhance_schema_with_core_properties(schema)
            plugin_config = _filter_config_by_schema(plugin_config, enhanced_schema_for_filtering)

        # Debug logging for union type fields (temporary)
        if 'rotation_settings' in plugin_config and 'random_seed' in plugin_config.get('rotation_settings', {}):
            seed_value = plugin_config['rotation_settings']['random_seed']
            logger.debug(f"After normalization, random_seed value: {repr(seed_value)}, type: {type(seed_value)}")

        # Validate configuration against schema before saving
        if schema:
            # Log what we're validating for debugging
            logger.info(f"Validating config for {plugin_id}")
            # Only the shape. plugin_config still holds the submitted secret
            # values at this point -- separate_secrets does not run until
            # below -- so logging it wrote live credentials to the journal.
            logger.info(f"Config keys being validated: {list(plugin_config.keys())}")

            # Get enhanced schema keys (including injected core properties)
            # We need to create an enhanced schema to get the actual allowed keys
            import copy
            enhanced_schema = copy.deepcopy(schema)
            if "properties" not in enhanced_schema:
                enhanced_schema["properties"] = {}

            # Core properties that are always injected during validation
            core_properties = ["enabled", "display_duration", "live_priority"]
            for prop_name in core_properties:
                if prop_name not in enhanced_schema["properties"]:
                    # Add placeholder to get the full list of allowed keys
                    enhanced_schema["properties"][prop_name] = {"type": "any"}

            is_valid, validation_errors = schema_mgr.validate_config_against_schema(
                plugin_config, schema, plugin_id
            )
            if not is_valid:
                # Log validation errors for debugging
                logger.error(f"Config validation failed for {plugin_id}")
                logger.error(f"Validation errors: {validation_errors}")
                # Keys only, for the same reason as above.
                logger.error(f"Config keys that failed: {list(plugin_config.keys())}")
                logger.error(f"Schema properties: {list(enhanced_schema.get('properties', {}).keys())}")

                # Also print to console for immediate visibility
                import json
                logger.warning("Config validation failed for plugin (see debug logs)")

                # Log raw form data if this was a form submission
                if 'application/json' not in (request.content_type or ''):
                    form_data = request.form.to_dict()
                return error_response(
                    ErrorCode.CONFIG_VALIDATION_FAILED,
                    'Configuration validation failed',
                    details='; '.join(validation_errors) if validation_errors else 'Unknown validation error',
                    context={
                        'plugin_id': plugin_id,
                        'validation_errors': validation_errors,
                        'config_keys': list(plugin_config.keys()),
                        'schema_keys': list(enhanced_schema.get('properties', {}).keys())
                    },
                    suggested_fixes=[
                        'Review validation errors above',
                        'Check config against schema',
                        'Verify all required fields are present'
                    ],
                    status_code=400
                )

        # Separate secrets from regular config (handles nested configs and
        # array-item secrets — see src/web_interface/secret_helpers.py)
        regular_config, secrets_config = separate_secrets(plugin_config, secret_fields)
        # The config form renders secrets masked, so every save posts
        # them back blank. Without this the blank is merged over the
        # stored value and the credential is destroyed by the act of
        # changing an unrelated setting. A blank means "unchanged".
        secrets_config = remove_empty_secrets(secrets_config)

        # Get current configs
        current_config = api_v3.config_manager.load_config()
        current_secrets = api_v3.config_manager.get_raw_file_content('secrets')

        # Deep merge plugin configuration in main config (preserves nested structures)
        if plugin_id not in current_config:
            current_config[plugin_id] = {}

        current_config[plugin_id] = deep_merge(current_config[plugin_id], regular_config)

        # Deep merge plugin secrets in secrets config
        if secrets_config:
            if plugin_id not in current_secrets:
                current_secrets[plugin_id] = {}
            # See above -- secrets lists must merge element-wise.
            current_secrets[plugin_id] = merge_secrets(
                current_secrets[plugin_id], secrets_config)
            # Save secrets file
            try:
                api_v3.config_manager.save_raw_file_content('secrets', current_secrets)
            except PermissionError as e:
                # Log the error with more details
                import os
                secrets_path = api_v3.config_manager.secrets_path
                secrets_dir = os.path.dirname(secrets_path) if secrets_path else None
                
                # Check permissions
                dir_readable = os.access(secrets_dir, os.R_OK) if secrets_dir and os.path.exists(secrets_dir) else False
                dir_writable = os.access(secrets_dir, os.W_OK) if secrets_dir and os.path.exists(secrets_dir) else False
                file_writable = os.access(secrets_path, os.W_OK) if secrets_path and os.path.exists(secrets_path) else False
                
                logger.error(
                    f"Permission error saving secrets config for {plugin_id}: {e}\n"
                    f"Secrets path: {secrets_path}\n"
                    f"Directory readable: {dir_readable}, writable: {dir_writable}\n"
                    f"File writable: {file_writable}",
                    exc_info=True
                )
                return error_response(
                    ErrorCode.CONFIG_SAVE_FAILED,
                    f"Failed to save secrets configuration: Permission denied. Check file permissions on {secrets_path}",
                    status_code=500
                )
            except Exception as e:
                # Log the error but don't fail the entire config save
                import os
                secrets_path = api_v3.config_manager.secrets_path
                logger.error("Error saving secrets config for %s (path=%s)", plugin_id, secrets_path, exc_info=True)
                # Return error response with more context
                return error_response(
                    ErrorCode.CONFIG_SAVE_FAILED,
                    "Failed to save secrets configuration; see logs for details",
                    status_code=500
                )

        # Save the updated main config using atomic save
        success, error_msg = _pkg._save_config_atomic(api_v3.config_manager, current_config, create_backup=True)
        if not success:
            return error_response(
                ErrorCode.CONFIG_SAVE_FAILED,
                f"Failed to save configuration: {error_msg}",
                status_code=500
            )

        # If the plugin is loaded, notify it of the config change with merged config
        try:
            if api_v3.plugin_manager:
                plugin_instance = api_v3.plugin_manager.get_plugin(plugin_id)
                if plugin_instance:
                    # Reload merged config (includes secrets) and pass the plugin-specific section
                    merged_config = api_v3.config_manager.load_config()
                    plugin_full_config = merged_config.get(plugin_id, {})
                    if hasattr(plugin_instance, 'on_config_change'):
                        plugin_instance.on_config_change(plugin_full_config)

                    # Update plugin state manager and call lifecycle methods based on enabled state
                    # This ensures the plugin state is synchronized with the config
                    enabled = plugin_full_config.get('enabled', plugin_instance.enabled)

                    # Update state manager if available
                    if api_v3.plugin_state_manager:
                        api_v3.plugin_state_manager.set_plugin_enabled(plugin_id, enabled)

                    # Call lifecycle methods to ensure plugin state matches config
                    try:
                        if enabled:
                            if hasattr(plugin_instance, 'on_enable'):
                                plugin_instance.on_enable()
                        else:
                            if hasattr(plugin_instance, 'on_disable'):
                                plugin_instance.on_disable()
                    except Exception as lifecycle_error:
                        # Log the error but don't fail the save - config is already saved
                        import logging
                        logging.warning(f"Lifecycle method error for {plugin_id}: {lifecycle_error}", exc_info=True)
        except Exception as hook_err:
            # Do not fail the save if hook fails; just log
            logger.warning("on_config_change failed: %s", hook_err)

        secret_count = len(secrets_config)
        message = f'Plugin {plugin_id} configuration saved successfully'
        if secret_count > 0:
            message += f' ({secret_count} secret field(s) saved to config_secrets.json)'

        return success_response(message=message)
    except Exception as e:
        from src.web_interface.errors import WebInterfaceError
        error = WebInterfaceError.from_exception(e, ErrorCode.CONFIG_SAVE_FAILED)
        if api_v3.operation_history:
            api_v3.operation_history.record_operation(
                "configure",
                plugin_id=data.get('plugin_id') if 'data' in locals() else None,
                status="failed",
                error=str(e)
            )
        return error_response(
            error.error_code,
            error.message,
            details=error.details,
            context=error.context,
            status_code=500
        )
@api_v3.route('/plugins/schema', methods=['GET'])
def get_plugin_schema():
    """Get plugin configuration schema"""
    try:
        plugin_id = request.args.get('plugin_id')
        if not plugin_id:
            return jsonify({'status': 'error', 'message': 'plugin_id required'}), 400

        # Get schema manager instance
        schema_mgr = api_v3.schema_manager
        if not schema_mgr:
            return jsonify({'status': 'error', 'message': 'Schema manager not initialized'}), 500

        # Load schema using SchemaManager (uses caching)
        schema = schema_mgr.load_schema(plugin_id, use_cache=True)

        if schema:
            # Offer installed visual skins as a dropdown (returns a copy;
            # the cached schema and validation are never enum-restricted)
            try:
                current_skin = None
                if api_v3.config_manager:
                    config = api_v3.config_manager.load_config()
                    current_skin = config.get(plugin_id, {}).get('skin')
                injected = schema_mgr.inject_skin_selector(schema, plugin_id, current_skin)
                if isinstance(injected, dict):
                    schema = injected
            except Exception:
                logger.debug('Skin selector injection failed for %s', plugin_id, exc_info=True)
            return jsonify({'status': 'success', 'data': {'schema': schema}})

        # Return a simple default schema if file not found
        default_schema = {
            'type': 'object',
            'properties': {
                'enabled': {
                    'type': 'boolean',
                    'title': 'Enable Plugin',
                    'description': 'Enable or disable this plugin',
                    'default': True
                },
                'display_duration': {
                    'type': 'integer',
                    'title': 'Display Duration',
                    'description': 'How long to show content (seconds)',
                    'minimum': 5,
                    'maximum': 300,
                    'default': 30
                }
            }
        }

        return jsonify({'status': 'success', 'data': {'schema': default_schema}})
    except Exception as e:
        logger.error('Error in get_plugin_schema', exc_info=True)
        return jsonify({'status': 'error', 'message': 'An error occurred; see logs for details', 'details': describe_exception(e)}), 500
@api_v3.route('/plugins/config/reset', methods=['POST'])
def reset_plugin_config():
    """Reset plugin configuration to schema defaults"""
    try:
        if not api_v3.config_manager:
            return jsonify({'status': 'error', 'message': 'Config manager not initialized'}), 500

        data = request.get_json(silent=True) or {}
        plugin_id = data.get('plugin_id')
        preserve_secrets = data.get('preserve_secrets', True)

        if not plugin_id:
            return jsonify({'status': 'error', 'message': 'plugin_id required'}), 400

        # Get schema manager instance
        schema_mgr = api_v3.schema_manager
        if not schema_mgr:
            return jsonify({'status': 'error', 'message': 'Schema manager not initialized'}), 500

        # Generate defaults from schema
        defaults = schema_mgr.generate_default_config(plugin_id, use_cache=True)

        # Get current configs
        current_config = api_v3.config_manager.load_config()
        current_secrets = api_v3.config_manager.get_raw_file_content('secrets')

        # Load schema to identify secret fields
        schema = schema_mgr.load_schema(plugin_id, use_cache=True)
        secret_fields = set()

        if schema and 'properties' in schema:
            secret_fields = find_secret_fields(schema['properties'])

        # Separate defaults into regular and secret configs
        default_regular, default_secrets = separate_secrets(defaults, secret_fields)

        # Update main config with defaults
        current_config[plugin_id] = default_regular

        # Update secrets config (preserve existing secrets if preserve_secrets=True)
        if preserve_secrets:
            # Keep existing secrets for this plugin
            if plugin_id in current_secrets:
                # Merge defaults with existing secrets
                existing_secrets = current_secrets[plugin_id]
                for key, value in default_secrets.items():
                    if key not in existing_secrets or not existing_secrets[key]:
                        existing_secrets[key] = value
            else:
                current_secrets[plugin_id] = default_secrets
        else:
            # Replace all secrets with defaults
            current_secrets[plugin_id] = default_secrets

        # Save updated configs
        api_v3.config_manager.save_config(current_config)
        if default_secrets or not preserve_secrets:
            api_v3.config_manager.save_raw_file_content('secrets', current_secrets)

        # Notify plugin of config change if loaded
        try:
            if api_v3.plugin_manager:
                plugin_instance = api_v3.plugin_manager.get_plugin(plugin_id)
                if plugin_instance:
                    merged_config = api_v3.config_manager.load_config()
                    plugin_full_config = merged_config.get(plugin_id, {})
                    if hasattr(plugin_instance, 'on_config_change'):
                        plugin_instance.on_config_change(plugin_full_config)
        except Exception as hook_err:
            logger.warning("on_config_change failed: %s", hook_err)

        return jsonify({
            'status': 'success',
            'message': f'Plugin {plugin_id} configuration reset to defaults',
            'data': {'config': defaults}
        })
    except Exception as e:
        logger.error('Error in reset_plugin_config', exc_info=True)
        return jsonify({'status': 'error', 'message': 'An error occurred; see logs for details', 'details': describe_exception(e)}), 500
@api_v3.route('/plugins/action', methods=['POST'])
def execute_plugin_action():
    """Execute a plugin-defined action (e.g., authentication)"""
    try:
        # Try to get JSON data, with better error handling
        try:
            data = request.get_json(force=True) or {}
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Error parsing JSON in execute_plugin_action: {e}")
            return jsonify({
                'status': 'error', 
                'message': 'Invalid JSON in request body',
                'content_type': request.content_type }), 400
        
        plugin_id = data.get('plugin_id')
        action_id = data.get('action_id')
        action_params = data.get('params', {})

        if not plugin_id or not action_id:
            return jsonify({
                'status': 'error', 
                'message': 'plugin_id and action_id required',
                'received': {'plugin_id': plugin_id, 'action_id': action_id, 'has_params': bool(action_params)}
            }), 400

        # Get plugin directory
        if api_v3.plugin_manager:
            plugin_dir = api_v3.plugin_manager.get_plugin_directory(plugin_id)
        else:
            plugin_dir = PROJECT_ROOT / 'plugins' / plugin_id

        if not plugin_dir or not Path(plugin_dir).exists():
            return jsonify({'status': 'error', 'message': 'Plugin not found'}), 404

        # Load manifest to get action definition
        manifest_path = Path(plugin_dir) / 'manifest.json'
        if not manifest_path.exists():
            return jsonify({'status': 'error', 'message': 'Plugin manifest not found'}), 404

        with open(manifest_path, 'r', encoding='utf-8') as f:
            manifest = json.load(f)

        web_ui_actions = manifest.get('web_ui_actions', [])
        action_def = None
        for action in web_ui_actions:
            if action.get('id') == action_id:
                action_def = action
                break

        if not action_def:
            return jsonify({'status': 'error', 'message': f'Action {action_id} not found in plugin manifest'}), 404

        # Set LEDMATRIX_ROOT environment variable
        env = os.environ.copy()
        env['LEDMATRIX_ROOT'] = str(PROJECT_ROOT)

        # Execute action based on type
        action_type = action_def.get('type', 'script')

        if action_type == 'script':
            # Execute a Python script
            script_path = action_def.get('script')
            if not script_path:
                return jsonify({'status': 'error', 'message': 'Script path not defined for action'}), 400

            script_file = Path(plugin_dir) / script_path
            if not script_file.exists():
                return jsonify({'status': 'error', 'message': f'Script not found: {script_path}'}), 404

            # Handle multi-step actions (like Spotify OAuth)
            step = action_params.get('step')

            if step == '2' and action_params.get('redirect_url'):
                # Step 2: Complete authentication with redirect URL
                redirect_url = action_params.get('redirect_url')
                import tempfile
                import json as json_lib

                redirect_url_escaped = json_lib.dumps(redirect_url)
                with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as wrapper:
                    wrapper.write(f'''import sys
import subprocess
import os

# Set LEDMATRIX_ROOT
os.environ['LEDMATRIX_ROOT'] = r"{PROJECT_ROOT}"

# Run the script and provide redirect URL
proc = subprocess.Popen(
    [sys.executable, r"{script_file}"],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True,
    env=os.environ
)

# Send redirect URL to stdin
redirect_url = {redirect_url_escaped}
stdout, _ = proc.communicate(input=redirect_url + "\\n", timeout=120)
print(stdout)
sys.exit(proc.returncode)
''')
                    wrapper_path = wrapper.name

                try:
                    result = subprocess.run(
                        ['python3', wrapper_path],
                        capture_output=True,
                        text=True,
                        timeout=120,
                        env=env
                    )
                    os.unlink(wrapper_path)

                    if result.returncode == 0:
                        return jsonify({
                            'status': 'success',
                            'message': action_def.get('success_message', 'Action completed successfully'),
                            'output': result.stdout
                        })
                    else:
                        return jsonify({
                            'status': 'error',
                            'message': action_def.get('error_message', 'Action failed'),
                            'output': result.stdout + result.stderr
                        }), 400
                except subprocess.TimeoutExpired:
                    if os.path.exists(wrapper_path):
                        os.unlink(wrapper_path)
                    return jsonify({'status': 'error', 'message': 'Action timed out'}), 408
            else:
                # Regular script execution - pass params via stdin if provided
                if action_params:
                    # Pass params as JSON via stdin
                    import tempfile
                    import json as json_lib

                    params_json = json_lib.dumps(action_params)
                    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as wrapper:
                        wrapper.write(f'''import sys
import subprocess
import os
import json

# Set LEDMATRIX_ROOT
os.environ['LEDMATRIX_ROOT'] = r"{PROJECT_ROOT}"

# Run the script and provide params as JSON via stdin
proc = subprocess.Popen(
    [sys.executable, r"{script_file}"],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True,
    env=os.environ
)

# Send params as JSON to stdin
params = {params_json}
stdout, _ = proc.communicate(input=json.dumps(params), timeout=120)
print(stdout)
sys.exit(proc.returncode)
''')
                        wrapper_path = wrapper.name

                    try:
                        result = subprocess.run(
                            ['python3', wrapper_path],
                            capture_output=True,
                            text=True,
                            timeout=120,
                            env=env
                        )
                        os.unlink(wrapper_path)

                        # Try to parse output as JSON
                        try:
                            output_data = json.loads(result.stdout)
                            if result.returncode == 0:
                                return jsonify(output_data)
                            else:
                                return jsonify({
                                    'status': 'error',
                                    'message': output_data.get('message', action_def.get('error_message', 'Action failed')),
                                    'output': result.stdout + result.stderr
                                }), 400
                        except json.JSONDecodeError:
                            # Output is not JSON, return as text
                            if result.returncode == 0:
                                return jsonify({
                                    'status': 'success',
                                    'message': action_def.get('success_message', 'Action completed successfully'),
                                    'output': result.stdout
                                })
                            else:
                                return jsonify({
                                    'status': 'error',
                                    'message': action_def.get('error_message', 'Action failed'),
                                    'output': result.stdout + result.stderr
                                }), 400
                    except subprocess.TimeoutExpired:
                        if os.path.exists(wrapper_path):
                            os.unlink(wrapper_path)
                        return jsonify({'status': 'error', 'message': 'Action timed out'}), 408
                else:
                    # No params - check for OAuth flow first, then run script normally
                    # Step 1: Get initial data (like auth URL)
                    # For OAuth flows, we might need to import the script as a module
                    if action_def.get('oauth_flow'):
                        # Import script as module to get auth URL
                        import sys
                        import importlib.util

                        spec = importlib.util.spec_from_file_location("plugin_action", script_file)
                        action_module = importlib.util.module_from_spec(spec)
                        sys.modules["plugin_action"] = action_module

                        try:
                            spec.loader.exec_module(action_module)

                            # Try to get auth URL using common patterns
                            auth_url = None
                            if hasattr(action_module, 'get_auth_url'):
                                auth_url = action_module.get_auth_url()
                            elif hasattr(action_module, 'load_spotify_credentials'):
                                # Spotify-specific pattern
                                client_id, client_secret, redirect_uri = action_module.load_spotify_credentials()
                                if all([client_id, client_secret, redirect_uri]):
                                    from spotipy.oauth2 import SpotifyOAuth
                                    sp_oauth = SpotifyOAuth(
                                        client_id=client_id,
                                        client_secret=client_secret,
                                        redirect_uri=redirect_uri,
                                        scope=getattr(action_module, 'SCOPE', ''),
                                        cache_path=getattr(action_module, 'SPOTIFY_AUTH_CACHE_PATH', None),
                                        open_browser=False
                                    )
                                    auth_url = sp_oauth.get_authorize_url()

                            if auth_url:
                                return jsonify({
                                    'status': 'success',
                                    'message': action_def.get('step1_message', 'Authorization URL generated'),
                                    'auth_url': auth_url,
                                    'requires_step2': True
                                })
                            else:
                                return jsonify({
                                    'status': 'error',
                                    'message': 'Could not generate authorization URL'
                                }), 400
                        except Exception as e:
                            logger.error("Error executing action step 1", exc_info=True)
                            return jsonify({
                                'status': 'error',
                                'message': 'An error occurred; see logs for details', 'details': describe_exception(e)
                            }), 500
                    else:
                        # Simple script execution
                        result = subprocess.run(
                            ['python3', str(script_file)],
                            capture_output=True,
                            text=True,
                            timeout=60,
                            env=env
                        )

                        # Try to parse output as JSON
                        try:
                            import json as json_module
                            output_data = json_module.loads(result.stdout)
                            if result.returncode == 0:
                                return jsonify(output_data)
                            else:
                                return jsonify({
                                    'status': 'error',
                                    'message': output_data.get('message', action_def.get('error_message', 'Action failed')),
                                    'output': result.stdout + result.stderr
                                }), 400
                        except json.JSONDecodeError:
                            # Output is not JSON, return as text
                            if result.returncode == 0:
                                return jsonify({
                                    'status': 'success',
                                    'message': action_def.get('success_message', 'Action completed successfully'),
                                    'output': result.stdout
                                })
                            else:
                                return jsonify({
                                    'status': 'error',
                                    'message': action_def.get('error_message', 'Action failed'),
                                    'output': result.stdout + result.stderr
                                }), 400

        elif action_type == 'endpoint':
            # Call a plugin-defined HTTP endpoint (future feature)
            return jsonify({'status': 'error', 'message': 'Endpoint actions not yet implemented'}), 501

        else:
            return jsonify({'status': 'error', 'message': f'Unknown action type: {action_type}'}), 400

    except subprocess.TimeoutExpired:
        return jsonify({'status': 'error', 'message': 'Action timed out'}), 408
    except Exception as e:
        logger.error('Error in execute_plugin_action', exc_info=True)
        return jsonify({'status': 'error', 'message': 'An error occurred; see logs for details', 'details': describe_exception(e)}), 500
@api_v3.route('/plugins/authenticate/spotify', methods=['POST'])
def authenticate_spotify():
    """Run Spotify authentication script"""
    try:
        data = request.get_json(silent=True) or {}
        redirect_url = data.get('redirect_url', '').strip()

        # Get plugin directory
        plugin_id = 'ledmatrix-music'
        if api_v3.plugin_manager:
            plugin_dir = api_v3.plugin_manager.get_plugin_directory(plugin_id)
        else:
            plugin_dir = PROJECT_ROOT / 'plugins' / plugin_id

        if not plugin_dir or not Path(plugin_dir).exists():
            return jsonify({'status': 'error', 'message': 'Plugin not found'}), 404

        auth_script = Path(plugin_dir) / 'authenticate_spotify.py'
        if not auth_script.exists():
            return jsonify({'status': 'error', 'message': 'Authentication script not found'}), 404

        # Set LEDMATRIX_ROOT environment variable
        env = os.environ.copy()
        env['LEDMATRIX_ROOT'] = str(PROJECT_ROOT)

        if redirect_url:
            # Step 2: Complete authentication with redirect URL
            # Create a wrapper script that provides the redirect URL as input
            import tempfile

            # Create a wrapper script that provides the redirect URL
            import json
            redirect_url_escaped = json.dumps(redirect_url)  # Properly escape the URL
            with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as wrapper:
                wrapper.write(f'''import sys
import subprocess
import os

# Set LEDMATRIX_ROOT
os.environ['LEDMATRIX_ROOT'] = r"{PROJECT_ROOT}"

# Run the auth script and provide redirect URL
proc = subprocess.Popen(
    [sys.executable, r"{auth_script}"],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True,
    env=os.environ
)

# Send redirect URL to stdin
redirect_url = {redirect_url_escaped}
stdout, _ = proc.communicate(input=redirect_url + "\\n", timeout=120)
print(stdout)
sys.exit(proc.returncode)
''')
                wrapper_path = wrapper.name

            try:
                result = subprocess.run(
                    ['python3', wrapper_path],
                    capture_output=True,
                    text=True,
                    timeout=120,
                    env=env
                )

                if result.returncode == 0:
                    return jsonify({
                        'status': 'success',
                        'message': 'Spotify authentication completed successfully',
                        'output': result.stdout
                    })
                else:
                    return jsonify({
                        'status': 'error',
                        'message': 'Spotify authentication failed',
                        'output': result.stdout + result.stderr
                    }), 400
            except subprocess.TimeoutExpired:
                return jsonify({'status': 'error', 'message': 'Authentication timed out'}), 408
            finally:
                # The wrapper carries the user's redirect URL, so it must not
                # survive the request on any path — including a failure to
                # launch, which the previous per-branch unlinks missed.
                if os.path.exists(wrapper_path):
                    os.unlink(wrapper_path)
        else:
            # Step 1: Get authorization URL
            # Import the script's functions directly to get the auth URL
            import sys
            import importlib.util

            # Load the authentication script as a module
            spec = importlib.util.spec_from_file_location("auth_spotify", auth_script)
            auth_module = importlib.util.module_from_spec(spec)
            sys.modules["auth_spotify"] = auth_module

            # Set LEDMATRIX_ROOT before loading
            os.environ['LEDMATRIX_ROOT'] = str(PROJECT_ROOT)

            try:
                spec.loader.exec_module(auth_module)

                # Get credentials and create OAuth object
                client_id, client_secret, redirect_uri = auth_module.load_spotify_credentials()
                if not all([client_id, client_secret, redirect_uri]):
                    return jsonify({
                        'status': 'error',
                        'message': 'Could not load Spotify credentials. Please check config/config_secrets.json.'
                    }), 400

                from spotipy.oauth2 import SpotifyOAuth
                sp_oauth = SpotifyOAuth(
                    client_id=client_id,
                    client_secret=client_secret,
                    redirect_uri=redirect_uri,
                    scope=auth_module.SCOPE,
                    cache_path=auth_module.SPOTIFY_AUTH_CACHE_PATH,
                    open_browser=False
                )

                auth_url = sp_oauth.get_authorize_url()

                return jsonify({
                    'status': 'success',
                    'message': 'Authorization URL generated',
                    'auth_url': auth_url
                })
            except Exception as e:
                logger.error("Error getting Spotify auth URL", exc_info=True)
                return jsonify({
                    'status': 'error',
                    'message': 'An error occurred; see logs for details', 'details': describe_exception(e)
                }), 500

    except Exception as e:
        logger.error('Error in authenticate_spotify', exc_info=True)
        return jsonify({'status': 'error', 'message': 'An error occurred; see logs for details', 'details': describe_exception(e)}), 500
@api_v3.route('/plugins/authenticate/ytm', methods=['POST'])
def authenticate_ytm():
    """Run YouTube Music authentication script"""
    try:
        # Get plugin directory
        plugin_id = 'ledmatrix-music'
        if api_v3.plugin_manager:
            plugin_dir = api_v3.plugin_manager.get_plugin_directory(plugin_id)
        else:
            plugin_dir = PROJECT_ROOT / 'plugins' / plugin_id

        if not plugin_dir or not Path(plugin_dir).exists():
            return jsonify({'status': 'error', 'message': 'Plugin not found'}), 404

        auth_script = Path(plugin_dir) / 'authenticate_ytm.py'
        if not auth_script.exists():
            return jsonify({'status': 'error', 'message': 'Authentication script not found'}), 404

        # Set LEDMATRIX_ROOT environment variable
        env = os.environ.copy()
        env['LEDMATRIX_ROOT'] = str(PROJECT_ROOT)

        # Run the authentication script
        result = subprocess.run(
            ['python3', str(auth_script)],
            capture_output=True,
            text=True,
            timeout=60,
            env=env
        )

        if result.returncode == 0:
            return jsonify({
                'status': 'success',
                'message': 'YouTube Music authentication completed successfully',
                'output': result.stdout
            })
        else:
            return jsonify({
                'status': 'error',
                'message': 'YouTube Music authentication failed',
                'output': result.stdout + result.stderr
            }), 400

    except subprocess.TimeoutExpired:
        return jsonify({'status': 'error', 'message': 'Authentication timed out'}), 408
    except Exception as e:
        logger.error('Error in authenticate_ytm', exc_info=True)
        return jsonify({'status': 'error', 'message': 'An error occurred; see logs for details', 'details': describe_exception(e)}), 500
@api_v3.route('/plugins/assets/upload', methods=['POST'])
def upload_plugin_asset():
    """Upload asset files for a plugin"""
    try:
        plugin_id = request.form.get('plugin_id')
        if not plugin_id:
            return jsonify({'status': 'error', 'message': 'plugin_id is required'}), 400

        if 'files' not in request.files:
            return jsonify({'status': 'error', 'message': 'No files provided'}), 400

        files = request.files.getlist('files')
        if not files or all(not f.filename for f in files):
            return jsonify({'status': 'error', 'message': 'No files provided'}), 400

        # Validate file count
        if len(files) > 10:
            return jsonify({'status': 'error', 'message': 'Maximum 10 files per upload'}), 400

        # Setup plugin assets directory
        assets_dir = PROJECT_ROOT / 'assets' / 'plugins' / plugin_id / 'uploads'
        assets_dir.mkdir(parents=True, exist_ok=True)

        # Load metadata file
        metadata_file = assets_dir / '.metadata.json'
        if metadata_file.exists():
            with open(metadata_file, 'r') as f:
                metadata = json.load(f)
        else:
            metadata = {}

        uploaded_files = []
        total_size = 0
        max_size_per_file = 5 * 1024 * 1024  # 5MB
        max_total_size = 50 * 1024 * 1024  # 50MB

        # Calculate current total size
        for entry in metadata.values():
            if 'size' in entry:
                total_size += entry.get('size', 0)

        for file in files:
            if not file.filename:
                continue

            # Validate file type
            allowed_extensions = ['.png', '.jpg', '.jpeg', '.bmp', '.gif']
            file_ext = '.' + file.filename.lower().split('.')[-1]
            if file_ext not in allowed_extensions:
                return jsonify({
                    'status': 'error',
                    'message': f'Invalid file type: {file_ext}. Allowed: {allowed_extensions}'
                }), 400

            # Read file to check size and validate
            file.seek(0, os.SEEK_END)
            file_size = file.tell()
            file.seek(0)

            if file_size > max_size_per_file:
                return jsonify({
                    'status': 'error',
                    'message': f'File {file.filename} exceeds 5MB limit'
                }), 400

            if total_size + file_size > max_total_size:
                return jsonify({
                    'status': 'error',
                    'message': f'Upload would exceed 50MB total storage limit'
                }), 400

            # Validate file is actually an image (check magic bytes)
            file_content = file.read(8)
            file.seek(0)
            is_valid_image = False
            if file_content.startswith(b'\x89PNG\r\n\x1a\n'):  # PNG
                is_valid_image = True
            elif file_content[:2] == b'\xff\xd8':  # JPEG
                is_valid_image = True
            elif file_content[:2] == b'BM':  # BMP
                is_valid_image = True
            elif file_content[:6] in [b'GIF87a', b'GIF89a']:  # GIF
                is_valid_image = True

            if not is_valid_image:
                return jsonify({
                    'status': 'error',
                    'message': f'File {file.filename} is not a valid image file'
                }), 400

            # Generate unique filename
            timestamp = int(_pkg.time.time())
            file_hash = hashlib.md5(file_content + file.filename.encode()).hexdigest()[:8]
            safe_filename = f"image_{timestamp}_{file_hash}{file_ext}"
            file_path = assets_dir / safe_filename

            # Ensure filename is unique
            counter = 1
            while file_path.exists():
                safe_filename = f"image_{timestamp}_{file_hash}_{counter}{file_ext}"
                file_path = assets_dir / safe_filename
                counter += 1

            # Save file
            file.save(str(file_path))

            # Make file readable
            os.chmod(file_path, 0o644)

            # Generate unique ID
            image_id = str(uuid.uuid4())

            # Store metadata
            relative_path = f"assets/plugins/{plugin_id}/uploads/{safe_filename}"
            metadata[image_id] = {
                'id': image_id,
                'filename': safe_filename,
                'path': relative_path,
                'size': file_size,
                'uploaded_at': datetime.utcnow().isoformat() + 'Z',
                'original_filename': file.filename
            }

            uploaded_files.append({
                'id': image_id,
                'filename': safe_filename,
                'path': relative_path,
                'size': file_size,
                'uploaded_at': metadata[image_id]['uploaded_at']
            })

            total_size += file_size

        # Save metadata
        with open(metadata_file, 'w') as f:
            json.dump(metadata, f, indent=2)

        return jsonify({
            'status': 'success',
            'uploaded_files': uploaded_files,
            'total_files': len(metadata)
        })

    except Exception as e:
        logger.error('Unhandled exception', exc_info=True)
        return jsonify({'status': 'error', 'message': 'An error occurred; see logs for details', 'details': describe_exception(e)}), 500
@api_v3.route('/plugins/of-the-day/json/upload', methods=['POST'])
def upload_of_the_day_json():
    """Upload JSON files for of-the-day plugin"""
    try:
        if 'files' not in request.files:
            return jsonify({'status': 'error', 'message': 'No files provided'}), 400

        files = request.files.getlist('files')
        if not files or all(not f.filename for f in files):
            return jsonify({'status': 'error', 'message': 'No files provided'}), 400

        # Get plugin directory
        plugin_id = 'ledmatrix-of-the-day'
        if api_v3.plugin_manager:
            plugin_dir = api_v3.plugin_manager.get_plugin_directory(plugin_id)
        else:
            plugin_dir = PROJECT_ROOT / 'plugins' / plugin_id

        if not plugin_dir or not Path(plugin_dir).exists():
            return jsonify({'status': 'error', 'message': 'Plugin not found'}), 404

        # Setup of_the_day directory
        data_dir = Path(plugin_dir) / 'of_the_day'
        data_dir.mkdir(parents=True, exist_ok=True)

        uploaded_files = []
        max_size_per_file = 5 * 1024 * 1024  # 5MB

        for file in files:
            if not file.filename:
                continue

            # Validate file extension
            if not file.filename.lower().endswith('.json'):
                return jsonify({
                    'status': 'error',
                    'message': f'File {file.filename} must be a JSON file (.json)'
                }), 400

            # Read and validate file size
            file.seek(0, os.SEEK_END)
            file_size = file.tell()
            file.seek(0)

            if file_size > max_size_per_file:
                return jsonify({
                    'status': 'error',
                    'message': f'File {file.filename} exceeds 5MB limit'
                }), 400

            # Read and validate JSON content
            try:
                file_content = file.read().decode('utf-8')
                json_data = json.loads(file_content)
            except json.JSONDecodeError as e:
                return jsonify({
                    'status': 'error',
                    'message': 'Invalid JSON in request body'
                }), 400
            except UnicodeDecodeError:
                return jsonify({
                    'status': 'error',
                    'message': f'File {file.filename} is not valid UTF-8 text'
                }), 400

            # Validate JSON structure (must be object with day number keys)
            if not isinstance(json_data, dict):
                return jsonify({
                    'status': 'error',
                    'message': f'JSON in {file.filename} must be an object with day numbers (1-365) as keys'
                }), 400

            # Check if keys are valid day numbers
            for key in json_data.keys():
                try:
                    day_num = int(key)
                    if day_num < 1 or day_num > 365:
                        return jsonify({
                            'status': 'error',
                            'message': f'Day number {day_num} in {file.filename} is out of range (must be 1-365)'
                        }), 400
                except ValueError:
                    return jsonify({
                        'status': 'error',
                        'message': f'Invalid key "{key}" in {file.filename}: must be a day number (1-365)'
                    }), 400

            # Generate safe filename from original (preserve user's filename)
            original_filename = file.filename
            safe_filename = original_filename.lower().replace(' ', '_')
            # Ensure it's a valid filename
            safe_filename = ''.join(c for c in safe_filename if c.isalnum() or c in '._-')
            if not safe_filename.endswith('.json'):
                safe_filename += '.json'

            file_path = data_dir / safe_filename

            # If file exists, add counter
            counter = 1
            base_name = safe_filename.replace('.json', '')
            while file_path.exists():
                safe_filename = f"{base_name}_{counter}.json"
                file_path = data_dir / safe_filename
                counter += 1

            # Save file
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(json_data, f, indent=2, ensure_ascii=False)

            # Make file readable
            os.chmod(file_path, 0o644)

            # Extract category name from filename (remove .json extension)
            category_name = safe_filename.replace('.json', '')
            display_name = category_name.replace('_', ' ').title()

            # Update plugin config to add category
            try:
                sys.path.insert(0, str(plugin_dir))
                from scripts.update_config import add_category_to_config
                add_category_to_config(category_name, f'of_the_day/{safe_filename}', display_name)
            except Exception as e:
                logger.warning("Could not update config: %s", e)
                # Continue anyway - file is uploaded

            # Generate file ID (use category name as ID for simplicity)
            file_id = category_name

            uploaded_files.append({
                'id': file_id,
                'filename': safe_filename,
                'original_filename': original_filename,
                'path': f'of_the_day/{safe_filename}',
                'size': file_size,
                'uploaded_at': datetime.utcnow().isoformat() + 'Z',
                'category_name': category_name,
                'display_name': display_name,
                'entry_count': len(json_data)
            })

        return jsonify({
            'status': 'success',
            'uploaded_files': uploaded_files,
            'total_files': len(uploaded_files)
        })

    except Exception as e:
        logger.error('Unhandled exception', exc_info=True)
        return jsonify({'status': 'error', 'message': 'An error occurred; see logs for details', 'details': describe_exception(e)}), 500
@api_v3.route('/plugins/of-the-day/json/delete', methods=['POST'])
def delete_of_the_day_json():
    """Delete a JSON file from of-the-day plugin"""
    try:
        data = request.get_json(silent=True) or {}
        file_id = data.get('file_id')  # This is the category_name

        if not file_id:
            return jsonify({'status': 'error', 'message': 'file_id is required'}), 400

        # Get plugin directory
        plugin_id = 'ledmatrix-of-the-day'
        if api_v3.plugin_manager:
            plugin_dir = api_v3.plugin_manager.get_plugin_directory(plugin_id)
        else:
            plugin_dir = PROJECT_ROOT / 'plugins' / plugin_id

        if not plugin_dir or not Path(plugin_dir).exists():
            return jsonify({'status': 'error', 'message': 'Plugin not found'}), 404

        data_dir = Path(plugin_dir) / 'of_the_day'
        filename = f"{file_id}.json"
        file_path = data_dir / filename

        if not file_path.exists():
            return jsonify({'status': 'error', 'message': f'File {filename} not found'}), 404

        # Delete file
        file_path.unlink()

        # Update config to remove category
        try:
            sys.path.insert(0, str(plugin_dir))
            from scripts.update_config import remove_category_from_config
            remove_category_from_config(file_id)
        except Exception as e:
            logger.warning("Could not update config: %s", e)

        return jsonify({
            'status': 'success',
            'message': f'File {filename} deleted successfully'
        })

    except Exception as e:
        logger.error('Unhandled exception', exc_info=True)
        return jsonify({'status': 'error', 'message': 'An error occurred; see logs for details', 'details': describe_exception(e)}), 500
@api_v3.route('/plugins/<plugin_id>/static/<path:file_path>', methods=['GET'])
def serve_plugin_static(plugin_id, file_path):
    """Serve static files from plugin directory"""
    try:
        # Get plugin directory
        if api_v3.plugin_manager:
            plugin_dir = api_v3.plugin_manager.get_plugin_directory(plugin_id)
        else:
            plugin_dir = PROJECT_ROOT / 'plugins' / plugin_id

        if not plugin_dir or not Path(plugin_dir).exists():
            return jsonify({'status': 'error', 'message': 'Plugin not found'}), 404

        # Resolve file path (prevent directory traversal)
        plugin_dir = Path(plugin_dir).resolve()
        requested_file = (plugin_dir / file_path).resolve()

        # Security check: ensure file is within plugin directory
        if not str(requested_file).startswith(str(plugin_dir)):
            return jsonify({'status': 'error', 'message': 'Invalid file path'}), 403

        # Check if file exists
        if not requested_file.exists() or not requested_file.is_file():
            return jsonify({'status': 'error', 'message': 'File not found'}), 404

        # Determine content type
        content_type = 'text/plain'
        if file_path.endswith('.html'):
            content_type = 'text/html'
        elif file_path.endswith('.js'):
            content_type = 'application/javascript'
        elif file_path.endswith('.css'):
            content_type = 'text/css'
        elif file_path.endswith('.json'):
            content_type = 'application/json'

        # Read and return file
        with open(requested_file, 'r', encoding='utf-8') as f:
            content = f.read()

        return Response(content, mimetype=content_type)

    except Exception as e:
        logger.error('Unhandled exception', exc_info=True)
        return jsonify({'status': 'error', 'message': 'An error occurred; see logs for details', 'details': describe_exception(e)}), 500
@api_v3.route('/plugins/calendar/upload-credentials', methods=['POST'])
def upload_calendar_credentials():
    """Upload credentials.json file for calendar plugin"""
    try:
        if 'file' not in request.files:
            return jsonify({'status': 'error', 'message': 'No file provided'}), 400

        file = request.files['file']
        if not file or not file.filename:
            return jsonify({'status': 'error', 'message': 'No file provided'}), 400

        # Validate file extension
        if not file.filename.lower().endswith('.json'):
            return jsonify({'status': 'error', 'message': 'File must be a JSON file (.json)'}), 400

        # Validate file size (max 1MB for credentials)
        file.seek(0, os.SEEK_END)
        file_size = file.tell()
        file.seek(0)

        if file_size > 1024 * 1024:  # 1MB
            return jsonify({'status': 'error', 'message': 'File exceeds 1MB limit'}), 400

        # Validate it's valid JSON
        try:
            file_content = file.read()
            file.seek(0)
            creds_data = json.loads(file_content)
        except json.JSONDecodeError:
            return jsonify({'status': 'error', 'message': 'File is not valid JSON'}), 400

        # Validate it looks like Google OAuth credentials. A bare scalar, a
        # list, true/null — all valid JSON, none of them credentials. Reject
        # rather than save: a file written as credentials.json but unusable
        # as credentials only fails later, somewhere less obvious.
        if not isinstance(creds_data, dict) or not (
                'installed' in creds_data or 'web' in creds_data):
            return jsonify({
                'status': 'error',
                'message': 'File does not appear to be a valid Google OAuth credentials file'
            }), 400

        # Get plugin directory
        plugin_id = 'calendar'
        if api_v3.plugin_manager:
            plugin_dir = api_v3.plugin_manager.get_plugin_directory(plugin_id)
        else:
            plugin_dir = PROJECT_ROOT / 'plugins' / plugin_id

        if not plugin_dir or not Path(plugin_dir).exists():
            return jsonify({'status': 'error', 'message': 'Plugin not found'}), 404

        # Save file to plugin directory
        credentials_path = Path(plugin_dir) / 'credentials.json'

        # Backup existing file if it exists
        if credentials_path.exists():
            backup_path = Path(plugin_dir) / f'credentials.json.backup.{int(_pkg.time.time())}'
            import shutil
            shutil.copy2(credentials_path, backup_path)
            _prune_credential_backups(Path(plugin_dir))

        # Save new file
        file.save(str(credentials_path))

        # Set proper permissions
        os.chmod(credentials_path, 0o600)  # Read/write for owner only

        return jsonify({
            'status': 'success',
            'message': 'Credentials file uploaded successfully',
            'path': str(credentials_path)
        })

    except Exception as e:
        logger.error('Error in upload_calendar_credentials', exc_info=True)
        return jsonify({'status': 'error', 'message': 'An error occurred; see logs for details', 'details': describe_exception(e)}), 500
@api_v3.route('/plugins/calendar/authenticate', methods=['POST'])
def authenticate_calendar():
    """Google OAuth for the calendar plugin, in the two steps it requires.

    Step 1 (no body) returns the consent URL to open. Step 2 posts back the
    URL Google redirected to -- it fails to load, because the redirect points
    at a loopback address nothing is listening on, but the address bar carries
    the authorization code -- and the script exchanges it for a token.

    Two calls rather than one because the user has to visit Google in between.
    The script persists the PKCE verifier from step 1 for step 2 to reuse; the
    exchange fails with "Missing code verifier" otherwise.
    """
    try:
        plugin_dir = _pkg._calendar_plugin_dir()
        if plugin_dir is None:
            return jsonify({
                'status': 'error',
                'message': 'The calendar plugin is not installed'
            }), 404

        if not (plugin_dir / 'credentials.json').exists():
            return jsonify({
                'status': 'error',
                'message': ('No credentials.json yet. Upload your Google OAuth '
                            'client file first (Step 1).')
            }), 400

        data = request.get_json(silent=True) or {}
        redirect_url = (data.get('redirect_url') or data.get('code') or '').strip()

        payload, error = _run_calendar_registration(plugin_dir, redirect_url)
        if error:
            return jsonify({'status': 'error', 'message': error}), 500
        if payload.get('status') != 'success':
            # The script's own diagnosis is more useful than anything that
            # could be reconstructed here -- but it interpolates exceptions
            # into its messages, so it reaches the client redacted and the
            # original goes to the log.
            logger.error('calendar authentication failed: %s', payload)
            safe = dict(payload)
            safe['message'] = redact_text(str(payload.get('message', '')
                                              or 'Authentication failed'))
            return jsonify(safe), 400
        return jsonify(payload)

    except Exception as e:
        logger.error('Error in authenticate_calendar', exc_info=True)
        return jsonify({'status': 'error',
                        'message': 'An error occurred; see logs for details',
                        'details': describe_exception(e)}), 500
@api_v3.route('/plugins/calendar/list-calendars', methods=['GET'])
def list_calendar_calendars():
    """The calendars this account can see, for the config picker.

    Reads the token the OAuth flow wrote rather than shelling out again: the
    picker is used interactively and a subprocess per click is slower than the
    API call it would be wrapping.
    """
    try:
        plugin_dir = _pkg._calendar_plugin_dir()
        if plugin_dir is None:
            return jsonify({
                'status': 'error',
                'message': 'The calendar plugin is not installed'
            }), 404

        token_file = plugin_dir / 'token.pickle'
        if not token_file.exists():
            return jsonify({
                'status': 'error',
                'message': ('Not authenticated with Google yet. Complete Step 2 '
                            'first, then load your calendars.')
            }), 400

        try:
            import pickle
            from google.auth.transport.requests import Request as GoogleRequest
            from googleapiclient.discovery import build as build_google_service
        except ImportError as e:
            return jsonify({
                'status': 'error',
                # The name of the missing module is the whole diagnosis, but it
                # arrives as an exception, so it goes through the redactor like
                # any other -- an ImportError can quote a path.
                'message': ('The Google API libraries are not installed. Install '
                            "the calendar plugin's requirements.txt. (%s)"
                            % describe_exception(e))
            }), 500

        with open(token_file, 'rb') as handle:
            # Written only by this plugin's own OAuth flow, into its own
            # directory, and read here exactly as the plugin itself reads it.
            creds = pickle.load(handle)  # nosec B301 - locally generated token

        if creds and creds.expired and creds.refresh_token:
            creds.refresh(GoogleRequest())
            with open(token_file, 'wb') as handle:
                pickle.dump(creds, handle)
            os.chmod(token_file, 0o600)

        if not creds or not creds.valid:
            return jsonify({
                'status': 'error',
                'message': ('Stored Google credentials are no longer valid. '
                            'Run Step 2 again to re-authenticate.')
            }), 400

        service = build_google_service('calendar', 'v3', credentials=creds)

        # calendarList.list returns 100 entries per page by default and caps at
        # 250, handing back a nextPageToken when there are more. Taking only
        # the first page would silently hide calendars from the picker, and the
        # user would have no way to tell the list was truncated.
        entries = []
        page_token = None
        for _ in range(_CALENDAR_LIST_MAX_PAGES):
            response = service.calendarList().list(
                maxResults=250, pageToken=page_token).execute()
            entries.extend(response.get('items', []))
            page_token = response.get('nextPageToken')
            if not page_token:
                break
        else:
            # 2500 calendars in, something is wrong with the account or the
            # token is looping; show what was collected rather than spin.
            logger.warning(
                'calendarList paging stopped at %d pages with more remaining',
                _CALENDAR_LIST_MAX_PAGES)

        calendars = [{
            'id': entry.get('id'),
            # The picker labels each row with summary and falls back to the id
            # only in its own display, so send something either way.
            'summary': entry.get('summary') or entry.get('id'),
            'primary': bool(entry.get('primary', False)),
        } for entry in entries if entry.get('id')]

        # Primary first, then alphabetically: the list is usually short but the
        # one the user wants is almost always their own calendar.
        calendars.sort(key=lambda c: (not c['primary'], c['summary'].lower()))

        return jsonify({'status': 'success', 'calendars': calendars})

    except Exception as e:
        logger.error('Error in list_calendar_calendars', exc_info=True)
        return jsonify({'status': 'error',
                        'message': 'An error occurred; see logs for details',
                        'details': describe_exception(e)}), 500
@api_v3.route('/plugins/assets/delete', methods=['POST'])
def delete_plugin_asset():
    """Delete an asset file for a plugin"""
    try:
        data = request.get_json()
        plugin_id = data.get('plugin_id')
        image_id = data.get('image_id')

        if not plugin_id or not image_id:
            return jsonify({'status': 'error', 'message': 'plugin_id and image_id are required'}), 400

        # Get asset directory
        assets_dir = PROJECT_ROOT / 'assets' / 'plugins' / plugin_id / 'uploads'
        metadata_file = assets_dir / '.metadata.json'

        if not metadata_file.exists():
            return jsonify({'status': 'error', 'message': 'Metadata file not found'}), 404

        # Load metadata
        with open(metadata_file, 'r') as f:
            metadata = json.load(f)

        if image_id not in metadata:
            return jsonify({'status': 'error', 'message': 'Image not found'}), 404

        # Delete file
        file_path = PROJECT_ROOT / metadata[image_id]['path']
        if file_path.exists():
            file_path.unlink()

        # Remove from metadata
        del metadata[image_id]

        # Save metadata
        with open(metadata_file, 'w') as f:
            json.dump(metadata, f, indent=2)

        return jsonify({'status': 'success', 'message': 'Image deleted successfully'})

    except Exception as e:
        logger.error('Unhandled exception', exc_info=True)
        return jsonify({'status': 'error', 'message': 'An error occurred; see logs for details', 'details': describe_exception(e)}), 500
@api_v3.route('/plugins/assets/list', methods=['GET'])
def list_plugin_assets():
    """List asset files for a plugin"""
    try:
        plugin_id = request.args.get('plugin_id')
        if not plugin_id:
            return jsonify({'status': 'error', 'message': 'plugin_id is required'}), 400

        # Get asset directory
        assets_dir = PROJECT_ROOT / 'assets' / 'plugins' / plugin_id / 'uploads'
        metadata_file = assets_dir / '.metadata.json'

        if not metadata_file.exists():
            return jsonify({'status': 'success', 'data': {'assets': []}})

        # Load metadata
        with open(metadata_file, 'r') as f:
            metadata = json.load(f)

        # Convert to list
        assets = list(metadata.values())

        return jsonify({'status': 'success', 'data': {'assets': assets}})

    except Exception as e:
        logger.error('Unhandled exception', exc_info=True)
        return jsonify({'status': 'error', 'message': 'An error occurred; see logs for details', 'details': describe_exception(e)}), 500
