"""Plugin install, update and uninstall, and the plugin store and saved
repositories.

Routes decorate the shared `api_v3` Blueprint from the package `__init__`,
so their endpoint names do not depend on which module they live in.
"""
from web_interface.blueprints.api_v3 import (
    ErrorCode, OperationType, Path, _do_transactional_uninstall,
    _non_plugin_id_error, _get_plugin_version, _plugin_directory,
    _plugin_enabled_in_config, _store_restart_fields, api_v3,
    error_response, exception_error_response, json, jsonify, logger,
    request, success_response, validate_request_json,
)
from src.common.path_safety import resolve_under, safe_path_component
from typing import Optional


def _compatibility_refusal(plugin_id: str) -> Optional[str]:
    """Why the store just refused ``plugin_id`` as incompatible, or None.

    The store records the reason under the id it was handed and, for a
    reinstall, under the registry id it resolved to; both are cleared here.
    """
    store = api_v3.plugin_store_manager
    ids = [plugin_id]
    try:
        entry = store.get_registry_info(plugin_id)
    except Exception:  # noqa: BLE001 - only used to phrase an error
        entry = None
    if isinstance(entry, dict) and isinstance(entry.get('id'), str):
        ids.append(entry['id'])
    reason = store.pop_refusal(*ids)
    return reason if isinstance(reason, str) and reason else None


def _store_incompatibility(plugin: dict) -> Optional[str]:
    """The pre-download compatibility verdict for a store listing entry."""
    try:
        reason = api_v3.plugin_store_manager.registry_incompatibility(plugin.get('id'), plugin)
    except Exception:  # noqa: BLE001 - a listing must not fail over a hint
        return None
    return reason if isinstance(reason, str) and reason else None


def _listed_plugin_dir(base: Path, name: str) -> Optional[Path]:
    """The entry of ``base`` called ``name``, or None.

    The path returned comes from listing ``base``, not from joining ``name``
    onto it, so a caller that validated ``name`` doesn't have to rely on that
    validation alone: nothing reaches the filesystem unless it's already there.
    """
    try:
        for entry in base.iterdir():
            if entry.name == name:
                return entry
    except OSError:
        pass
    return None


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

        # /plugins/installed lists installed Starlark apps as virtual
        # 'starlark:<app_id>' entries. They are not plugin directories, so the
        # store manager can only fail to find them -- which used to surface as
        # a 500 "plugin not found" for an app that is installed and working.
        raw_id = data.get('plugin_id')
        if isinstance(raw_id, str) and raw_id.startswith('starlark:'):
            return error_response(
                ErrorCode.INVALID_INPUT,
                f'{raw_id} is a Starlark app, not a plugin; Starlark apps are '
                'not updated through the plugin updater',
                status_code=400
            )

        if not api_v3.plugin_store_manager:
            return error_response(
                ErrorCode.SYSTEM_ERROR,
                'Plugin store manager not initialized',
                status_code=500
            )

        # The id names a directory this handler reads a manifest out of and
        # hands to the store manager to run git in. It comes from the request
        # body, so it is validated before anything is joined to a path, and
        # the validated value -- not the raw one -- is what gets used below.
        plugin_id = safe_path_component(data['plugin_id'])
        if not plugin_id:
            return error_response(
                ErrorCode.INVALID_INPUT,
                'Invalid plugin_id',
                status_code=400
            )

        # Always do direct updates (they're fast git pull operations)
        # Operation queue is reserved for longer operations like install/uninstall
        # The resolver finds a plugin installed as ledmatrix-<id>. Either way
        # the directory used is taken from a listing of plugins_dir, matched by
        # name, never built from the request value -- so no path here depends
        # on user input. None means nothing by that name is installed; the
        # store manager still gets the id and reports that itself.
        resolved = _plugin_directory(plugin_id)
        plugin_dir = _listed_plugin_dir(
            Path(api_v3.plugin_store_manager.plugins_dir),
            resolved.name if resolved else plugin_id)
        manifest_path = None
        if plugin_dir is not None:
            manifest_path = resolve_under(plugin_dir, "manifest.json")
            if manifest_path is None:
                return error_response(
                    ErrorCode.INVALID_INPUT,
                    'Invalid plugin_id',
                    status_code=400
                )

        current_last_updated = None
        current_version = None
        current_commit = None
        current_branch = None

        if manifest_path is not None and manifest_path.exists():
            try:
                with open(manifest_path, 'r', encoding='utf-8') as f:
                    manifest = json.load(f)
                    current_last_updated = manifest.get('last_updated')
                    current_version = manifest.get('version')
                if manifest.get('local_only'):
                    logger.debug("Skipping update for local-only plugin: %s", plugin_id)
                    if api_v3.operation_history:
                        api_v3.operation_history.record_operation(
                            "update",
                            plugin_id=plugin_id,
                            status="skipped",
                            details={"reason": "local_only"}
                        )
                    return success_response(
                        data={'update_status': 'local_only'},
                        message=f'Plugin {plugin_id} is managed locally and does not receive registry updates')
            except Exception as e:
                logger.debug("Could not read local manifest for plugin: %s", e)

        git_info_before = (api_v3.plugin_store_manager._get_local_git_info(plugin_dir)
                           if plugin_dir is not None else None)
        if git_info_before:
            current_commit = git_info_before.get('sha')
            current_branch = git_info_before.get('branch')
            logger.debug("Plugin is a git repository, will update via git pull")

        remote_info = api_v3.plugin_store_manager.get_plugin_info(plugin_id, fetch_latest_from_github=True)
        remote_commit = remote_info.get('last_commit_sha') if remote_info else None
        remote_branch = remote_info.get('branch') if remote_info else None

        # Update the plugin. A refusal left over from an earlier attempt
        # (say, the automatic updater's) must not explain this one.
        _compatibility_refusal(plugin_id)
        success = api_v3.plugin_store_manager.update_plugin(plugin_id)

        if success:
            updated_last_updated = current_last_updated
            updated_version = current_version
            try:
                if manifest_path is not None and manifest_path.exists():
                    with open(manifest_path, 'r', encoding='utf-8') as f:
                        manifest = json.load(f)
                        updated_last_updated = manifest.get('last_updated', current_last_updated)
                        updated_version = manifest.get('version', current_version)
            except Exception as e:
                logger.debug("Could not read updated manifest after update: %s", e)

            updated_commit = None
            updated_branch = remote_branch or current_branch
            git_info_after = (api_v3.plugin_store_manager._get_local_git_info(plugin_dir)
                              if plugin_dir is not None else None)
            if git_info_after:
                updated_commit = git_info_after.get('sha')
                updated_branch = git_info_after.get('branch') or updated_branch

            # update_plugin() answers True for "nothing to do" as well as for
            # a real update (a ZIP-installed monorepo plugin already at the
            # registry version, a bundled plugin), so what changed is read off
            # the plugin itself: its git commit, else its manifest.
            update_status = 'updated'
            message = f'Plugin {plugin_id} updated successfully'
            if current_commit and updated_commit and current_commit == updated_commit:
                update_status = 'up_to_date'
                message = f'Plugin {plugin_id} already up to date (commit {updated_commit[:7]})'
            elif updated_commit:
                message = f'Plugin {plugin_id} updated to commit {updated_commit[:7]}'
                if updated_branch:
                    message += f' on branch {updated_branch}'
            elif updated_version and updated_version != current_version:
                message = f'Plugin {plugin_id} updated to version {updated_version}'
            elif updated_last_updated and updated_last_updated != current_last_updated:
                message = f'Plugin {plugin_id} refreshed (Last Updated {updated_last_updated})'
            elif not current_commit:
                update_status = 'up_to_date'
                message = f'Plugin {plugin_id} already up to date'
                if updated_version:
                    message += f' (version {updated_version})'

            remote_commit_short = remote_commit[:7] if remote_commit else None
            if remote_commit_short and updated_commit and remote_commit_short != updated_commit[:7]:
                message += f' (remote latest {remote_commit_short})'

            # Invalidate schema cache
            if api_v3.schema_manager:
                api_v3.schema_manager.invalidate_cache(plugin_id)

            # Rediscover plugins. The web process runs no plugin code, so
            # there is nothing here to reload: the display keeps running the
            # version it loaded until it restarts, which restart_required
            # below asks for.
            if api_v3.plugin_catalog:
                api_v3.plugin_catalog.discover_plugins()

            # Record in history (the only record of when it was updated;
            # the version is the manifest on disk).
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
                        "branch": updated_branch,
                        "update_status": update_status
                    }
                )

            return success_response(
                data={
                    'last_updated': updated_last_updated,
                    'commit': updated_commit,
                    'update_status': update_status
                },
                message=message,
                extra=_store_restart_fields(
                    'update', _plugin_enabled_in_config(plugin_id),
                    changed=update_status == 'updated'),
            )
        else:
            refusal = _compatibility_refusal(plugin_id)
            if refusal:
                # The plugin is untouched; say why rather than "check logs".
                client_msg = f'Plugin update refused: {refusal}'
            elif plugin_dir is None or not plugin_dir.exists():
                client_msg = 'Plugin update failed: plugin not found'
            else:
                git_info = api_v3.plugin_store_manager._get_local_git_info(plugin_dir)
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
                # A refusal is the plugin's requirement, not a server fault.
                status_code=409 if refusal else 500
            )

    except Exception as e:
        logger.error("Unhandled exception in update endpoint", exc_info=True)
        
        if api_v3.operation_history:
            api_v3.operation_history.record_operation(
                "update",
                plugin_id=data.get('plugin_id') if 'data' in locals() else None,
                status="failed",
                error=str(e)
            )
        return exception_error_response(e, ErrorCode.PLUGIN_UPDATE_FAILED)


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
        id_error = _non_plugin_id_error(plugin_id)
        if id_error:
            return id_error

        # Both queued and direct paths use the same transactional helper so
        # snapshot/rollback behaviour is consistent regardless of deployment.
        if api_v3.operation_queue:
            def uninstall_callback(operation):
                """Callback to execute plugin uninstallation via transactional helper."""
                # Read before the uninstall removes the config section.
                was_enabled = _plugin_enabled_in_config(plugin_id)
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
                return {'success': True, 'message': 'Plugin uninstalled successfully',
                        **_store_restart_fields('uninstall', was_enabled,
                                                preserve_config=preserve_config)}

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
            was_enabled = _plugin_enabled_in_config(plugin_id)
            success, error_msg = _do_transactional_uninstall(plugin_id, preserve_config)

            if success:
                if api_v3.operation_history:
                    api_v3.operation_history.record_operation(
                        "uninstall",
                        plugin_id=plugin_id,
                        status="success",
                        details={"preserve_config": preserve_config}
                    )
                return success_response(
                    message='Plugin uninstalled successfully',
                    extra=_store_restart_fields('uninstall', was_enabled,
                                                preserve_config=preserve_config))
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
        if api_v3.operation_history:
            api_v3.operation_history.record_operation(
                "uninstall",
                plugin_id=data.get('plugin_id') if 'data' in locals() else None,
                status="failed",
                error=str(e)
            )
        return exception_error_response(e, ErrorCode.PLUGIN_UNINSTALL_FAILED)


@api_v3.route('/plugins/install', methods=['POST'])
def install_plugin():
    """Install plugin from store"""
    if not api_v3.plugin_store_manager:
        return jsonify({'status': 'error', 'message': 'Plugin store manager not initialized'}), 500

    data = request.get_json(silent=True)
    if not data or 'plugin_id' not in data:
        return jsonify({'status': 'error', 'message': 'plugin_id required'}), 400

    plugin_id = data['plugin_id']
    branch = data.get('branch')  # Optional branch parameter

    # A registry entry that isn't a plugin (a custom registry can still
    # list old "type": "skin" entries) gets a clear refusal, not a failed
    # install.
    try:
        registry_entry = api_v3.plugin_store_manager.get_registry_info(plugin_id)
    except Exception:
        registry_entry = None
    if isinstance(registry_entry, dict) and not api_v3.plugin_store_manager.is_plugin_entry(registry_entry):
        return jsonify({'status': 'error',
                        'message': f"{plugin_id} is a {registry_entry.get('type')!r} entry, not a plugin"}), 400

    plugins_dir = api_v3.plugin_store_manager.plugins_dir
    logger.info("Installing plugin to directory: %s", plugins_dir)

    # Use operation queue if available
    if api_v3.operation_queue:
        def install_callback(operation):
            """Callback to execute plugin installation."""
            _compatibility_refusal(plugin_id)  # clear any stale one
            success = api_v3.plugin_store_manager.install_plugin(plugin_id, branch=branch)

            if success:
                # Invalidate schema cache
                if api_v3.schema_manager:
                    api_v3.schema_manager.invalidate_cache(plugin_id)

                # List the new plugin. The display loads it, from disk, when
                # it is enabled; see restart_required below for one that
                # already is.
                if api_v3.plugin_catalog:
                    api_v3.plugin_catalog.discover_plugins()

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
                return {'success': True,
                        'message': f'Plugin {plugin_id} installed successfully{branch_msg}',
                        **_store_restart_fields('install', _plugin_enabled_in_config(plugin_id))}
            else:
                error_msg = f'Failed to install plugin {plugin_id}'
                if branch:
                    error_msg += f' (branch: {branch})'
                refusal = _compatibility_refusal(plugin_id)
                if refusal:
                    error_msg += f': {refusal}'
                elif not api_v3.plugin_store_manager.get_plugin_info(plugin_id):
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
        _compatibility_refusal(plugin_id)  # clear any stale one
        success = api_v3.plugin_store_manager.install_plugin(plugin_id, branch=branch)

        if success:
            if api_v3.schema_manager:
                api_v3.schema_manager.invalidate_cache(plugin_id)
            if api_v3.plugin_catalog:
                api_v3.plugin_catalog.discover_plugins()
            if api_v3.operation_history:
                version = _get_plugin_version(plugin_id)
                api_v3.operation_history.record_operation(
                    "install",
                    plugin_id=plugin_id,
                    status="success",
                    details={"version": version, "branch": branch}
                )

            branch_msg = f" (branch: {branch})" if branch else ""
            return success_response(
                message=f'Plugin installed successfully{branch_msg}',
                extra=_store_restart_fields('install', _plugin_enabled_in_config(plugin_id)))
        else:
            error_msg = f'Failed to install plugin {plugin_id}'
            if branch:
                error_msg += f' (branch: {branch})'
            refusal = _compatibility_refusal(plugin_id)
            if refusal:
                error_msg += f': {refusal}'
            elif not api_v3.plugin_store_manager.get_plugin_info(plugin_id):
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
                status_code=409 if refusal else 500
            )


@api_v3.route('/plugins/install-from-url', methods=['POST'])
def install_plugin_from_url():
    """Install plugin from custom GitHub URL"""
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

        # List the new plugin; the display loads it when it is enabled.
        if api_v3.plugin_catalog and installed_plugin_id:
            api_v3.plugin_catalog.discover_plugins()

        branch_msg = f" (branch: {result.get('branch', branch)})" if (result.get('branch') or branch) else ""
        response_data = {
            'status': 'success',
            'message': f"Plugin {installed_plugin_id} installed successfully{branch_msg}",
            'plugin_id': installed_plugin_id,
            'name': result.get('name')
        }
        if result.get('branch'):
            response_data['branch'] = result.get('branch')
        if installed_plugin_id:
            response_data.update(_store_restart_fields(
                'install', _plugin_enabled_in_config(installed_plugin_id)))
        return jsonify(response_data)
    else:
        return jsonify({
            'status': 'error',
            'message': result.get('error', 'Failed to install plugin from URL')
        }), 500


@api_v3.route('/plugins/registry-from-url', methods=['POST'])
def get_registry_from_url():
    """Get plugin list from a registry-style monorepo URL"""
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
            'plugins': [p for p in registry.get('plugins', [])
                        if api_v3.plugin_store_manager.is_plugin_entry(p)],
            'registry_url': repo_url
        })
    else:
        return jsonify({
            'status': 'error',
            'message': 'Failed to fetch registry from URL or URL does not contain a valid registry'
        }), 400


@api_v3.route('/plugins/saved-repositories', methods=['GET'])
def get_saved_repositories():
    """Get all saved repositories"""
    if not api_v3.saved_repositories_manager:
        return jsonify({'status': 'error', 'message': 'Saved repositories manager not initialized'}), 500

    repositories = api_v3.saved_repositories_manager.get_all()
    return jsonify({'status': 'success', 'data': {'repositories': repositories}})


@api_v3.route('/plugins/saved-repositories', methods=['POST'])
def add_saved_repository():
    """Add a repository to saved list"""
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


@api_v3.route('/plugins/saved-repositories', methods=['DELETE'])
def remove_saved_repository():
    """Remove a repository from saved list"""
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


@api_v3.route('/plugins/store/list', methods=['GET'])
def list_plugin_store():
    """Search plugin store"""
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
        if not api_v3.plugin_store_manager.is_plugin_entry(plugin):
            continue
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
            'plugin_path': plugin.get('plugin_path', ''),
            # Registry fields from after 3.7.0; absent (None) in an older
            # plugins.json. `commit` is the one that introduced `version`.
            'commit': plugin.get('commit') if isinstance(plugin.get('commit'), str) else None,
            'ledmatrix_min_version': (plugin.get('ledmatrix_min_version')
                                      if isinstance(plugin.get('ledmatrix_min_version'), str) else None),
            'aliases': [a for a in plugin.get('aliases') or [] if isinstance(a, str)]
                       if isinstance(plugin.get('aliases'), list) else [],
            # What Install would answer, without trying: the same check the
            # store runs before downloading.
            'incompatible_reason': _store_incompatibility(plugin),
        })

    return jsonify({'status': 'success', 'data': {'plugins': formatted_plugins}})


@api_v3.route('/plugins/store/github-status', methods=['GET'])
def get_github_auth_status():
    """Check if GitHub authentication is configured and validate token"""
    if not api_v3.plugin_store_manager:
        return jsonify({'status': 'error', 'message': 'Plugin store manager not initialized'}), 500
        
    token = api_v3.plugin_store_manager.github_token
        
    # Check if GitHub token is configured
    if not token or len(token) == 0:
        return jsonify({
            'status': 'success',
            'data': {
                'token_status': 'none',  # nosec B105 - a status label  # nosemgrep
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
                'token_status': 'valid',  # nosec B105 - a status label  # nosemgrep
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
                'token_status': 'invalid',  # nosec B105 - a status label  # nosemgrep
                'authenticated': False,
                'rate_limit': 60,
                'message': f'GitHub token is invalid: {error_message}' if error_message else 'GitHub token is invalid',
                'error': error_message
            }
        })


@api_v3.route('/plugins/store/refresh', methods=['POST'])
def refresh_plugin_store():
    """Re-download the plugin registry, bypassing its cache.

    Takes no body. Answers ``{status, message, plugin_count}``, the count
    being the registry's entries. Commit metadata is not refreshed here: the
    store list fetches it per plugin when it is shown.
    """
    if not api_v3.plugin_store_manager:
        return jsonify({'status': 'error', 'message': 'Plugin store manager not initialized'}), 500

    registry = api_v3.plugin_store_manager.fetch_registry(force_refresh=True)
    plugin_count = len(registry.get('plugins', []))

    return jsonify({
        'status': 'success',
        'message': 'Plugin store refreshed',
        'plugin_count': plugin_count
    })
