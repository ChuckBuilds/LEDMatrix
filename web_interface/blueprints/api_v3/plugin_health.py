"""Plugin health, resource metrics, fetch statistics and resource limits.

The display process records health and metrics to the shared on-disk cache;
these routes read (and reset) that published state through a tracker and a
monitor backed by the same cache (app.py sets api_v3.health_tracker and
api_v3.resource_monitor). See the route docstrings.

Routes decorate the shared `api_v3` Blueprint from the package `__init__`,
so their endpoint names do not depend on which module they live in.
"""
from web_interface.blueprints.api_v3 import (
    _installed_plugin_ids, api_v3, jsonify, logger, request,
)


def _health_tracker():
    """The reader of the display's published plugin health, or None."""
    return getattr(api_v3, 'health_tracker', None)


def _resource_monitor():
    """The reader of the display's published plugin metrics, or None."""
    return getattr(api_v3, 'resource_monitor', None)


@api_v3.route('/plugins/health', methods=['GET'])
def get_plugin_health():
    """Get health metrics for all plugins"""
    if not api_v3.plugin_catalog:
        return jsonify({'status': 'error', 'message': 'Plugin catalog not initialized'}), 500

    if not _health_tracker():
        return jsonify({
            'status': 'success',
            'data': {},
            'message': 'Health tracking not available'
        })

    tracker = _health_tracker()
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


@api_v3.route('/plugins/health/<plugin_id>', methods=['GET'])
def get_plugin_health_single(plugin_id):
    """Get health metrics for a specific plugin"""
    if not api_v3.plugin_catalog:
        return jsonify({'status': 'error', 'message': 'Plugin catalog not initialized'}), 500

    if not _health_tracker():
        return jsonify({
            'status': 'error',
            'message': 'Health tracking not available'
        }), 503

    # force_reload for the same reason as the list route above.
    health_summary = _health_tracker().get_health_summary(
        plugin_id, force_reload=True)

    return jsonify({
        'status': 'success',
        'data': health_summary
    })


@api_v3.route('/plugins/health/<plugin_id>/reset', methods=['POST'])
def reset_plugin_health(plugin_id):
    """Reset health state for a plugin (manual recovery).

    This resets the web process's tracker and the persisted record. The
    display service runs its own tracker in another process and keeps its
    in-memory state, so its next recorded success or failure can write that
    state back; restart the display service for a reset it will honour.
    """
    if not api_v3.plugin_catalog:
        return jsonify({'status': 'error', 'message': 'Plugin catalog not initialized'}), 500

    if not _health_tracker():
        return jsonify({
            'status': 'error',
            'message': 'Health tracking not available'
        }), 503

    # Reset health state
    _health_tracker().reset_health(plugin_id)

    return jsonify({
        'status': 'success',
        'message': f'Health state reset for plugin {plugin_id}'
    })


@api_v3.route('/plugins/metrics', methods=['GET'])
def get_plugin_metrics():
    """Get resource metrics for all plugins"""
    if not api_v3.plugin_catalog:
        return jsonify({'status': 'error', 'message': 'Plugin catalog not initialized'}), 500

    if not _resource_monitor():
        return jsonify({
            'status': 'success',
            'data': {},
            'message': 'Resource monitoring not available'
        })

    monitor = _resource_monitor()
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


@api_v3.route('/plugins/metrics/<plugin_id>', methods=['GET'])
def get_plugin_metrics_single(plugin_id):
    """Get resource metrics for a specific plugin"""
    if not api_v3.plugin_catalog:
        return jsonify({'status': 'error', 'message': 'Plugin catalog not initialized'}), 500

    if not _resource_monitor():
        return jsonify({
            'status': 'error',
            'message': 'Resource monitoring not available'
        }), 503

    # force_reload for the same reason as the list route above.
    metrics_summary = _resource_monitor().get_metrics_summary(
        plugin_id, force_reload=True)

    return jsonify({
        'status': 'success',
        'data': metrics_summary
    })


@api_v3.route('/plugins/metrics/<plugin_id>/reset', methods=['POST'])
def reset_plugin_metrics(plugin_id):
    """Reset metrics for a plugin.

    Only the web process's copy and the persisted snapshot are cleared. The
    display service keeps accumulating in its own process and republishes
    its totals on its next persist, so the reset does not stick while it runs.
    """
    if not api_v3.plugin_catalog:
        return jsonify({'status': 'error', 'message': 'Plugin catalog not initialized'}), 500

    if not _resource_monitor():
        return jsonify({
            'status': 'error',
            'message': 'Resource monitoring not available'
        }), 503

    # Reset metrics
    _resource_monitor().reset_metrics(plugin_id)

    return jsonify({
        'status': 'success',
        'message': f'Metrics reset for plugin {plugin_id}'
    })


@api_v3.route('/plugins/fetch-stats', methods=['GET'])
def get_fetch_stats():
    """Network requests per plugin and per host, as the display counts them.

    Read-only. The display's fetch service (src/common/fetch_service.py)
    publishes its counters to the shared cache at most once a minute when
    they change; this returns that snapshot judged for staleness:
    ``data.status`` is ``live``, ``stale``, ``stopped`` or ``unknown``, and
    ``data.data`` the snapshot (None when unknown). Counters are cumulative
    since the display started.
    """
    from src.common.fetch_service import read_fetch_stats
    return jsonify({
        'status': 'success',
        'data': read_fetch_stats(getattr(api_v3, 'cache_manager', None)),
    })


@api_v3.route('/plugins/limits/<plugin_id>', methods=['GET', 'POST'])
def manage_plugin_limits(plugin_id):
    """Get or set resource limits for a plugin.

    A POST updates the web process's monitor and the persisted record. The
    display service reads persisted limits only until it has some for a
    plugin, so a change to existing limits takes effect there after the
    display service restarts.
    """
    if not api_v3.plugin_catalog:
        return jsonify({'status': 'error', 'message': 'Plugin catalog not initialized'}), 500

    if not _resource_monitor():
        return jsonify({
            'status': 'error',
            'message': 'Resource monitoring not available'
        }), 503

    if request.method == 'GET':
        # Get limits
        limits = _resource_monitor().get_limits(plugin_id)
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
        from src.plugin_system.resource_monitor import invalid_limit_field, limits_from_dict

        # Validate here: a string limit stored as-is made every later update
        # of the plugin raise TypeError inside the resource monitor. The
        # message is built from the field name, not from an exception.
        bad = invalid_limit_field(data)
        if bad == 'limits':
            return jsonify({'status': 'error', 'message': 'Limits must be a JSON object'}), 400
        if bad:
            return jsonify({'status': 'error',
                            'message': f'{bad} must be a non-negative number or null'}), 400
        limits = limits_from_dict(data)

        _resource_monitor().set_limits(plugin_id, limits)

        return jsonify({
            'status': 'success',
            'message': f'Resource limits updated for plugin {plugin_id}'
        })
