"""Starlark / Tronbyte app management routes.

Routes decorate the shared `api_v3` Blueprint from ._common, so their
endpoint names are unchanged by living here.
"""
import signal
import threading

from web_interface.blueprints.api_v3 import (
    PROJECT_ROOT, Path, _PIXLET_EDITOR_DEFAULT_PORT,
    _PIXLET_EDITOR_DEFAULT_TIMEOUT, _PIXLET_EDITOR_MAX_TIMEOUT,
    _PIXLET_EDITOR_SCRIPT, _PIXLET_EDITOR_STATE, _clear_pixlet_editor_state,
    _find_pixlet_binary, _install_star_file, _pixlet_editor_alive,
    _pixlet_editor_status, _read_pixlet_editor_state,
    _STARLARK_APPS_DIR, _standalone_render_starlark_app,
    _starlark_github_token, _starlark_manifest_lock,
    _validate_and_sanitize_app_id,
    _validate_starlark_app_path, _validate_timing_value, api_v3, contextlib,
    describe_exception, json, jsonify, logger, os, request, shutil,
    subprocess, tempfile,
)
import web_interface.blueprints.api_v3 as _pkg
# Read through the module rather than bound by value: tests patch these
# as module attributes, and a value binding would not see the patch.
# Several are also called from helpers that live in __init__, so the
# package is the only patch point that covers every caller.


@api_v3.route('/starlark/status', methods=['GET'])
def get_starlark_status():
    """Get Starlark plugin status and Pixlet availability."""
    try:
        starlark_plugin = _pkg._get_starlark_plugin()
        if starlark_plugin:
            info = starlark_plugin.get_info()
            magnify_info = starlark_plugin.get_magnify_recommendation()
            return jsonify({
                'status': 'success',
                'pixlet_available': info.get('pixlet_available', False),
                'pixlet_version': info.get('pixlet_version'),
                'installed_apps': info.get('installed_apps', 0),
                'enabled_apps': info.get('enabled_apps', 0),
                'current_app': info.get('current_app'),
                'plugin_enabled': starlark_plugin.enabled,
                'display_info': magnify_info
            })

        # Plugin not loaded - check Pixlet availability via shared resolver
        # (respects user-configured pixlet_path, bundled binary, and system PATH)
        full_config = api_v3.config_manager.load_config() if api_v3.config_manager else {}
        pixlet_path = _find_pixlet_binary(full_config.get('starlark-apps', {}).get('pixlet_path'))
        pixlet_available = pixlet_path is not None

        # Read app counts from manifest
        manifest = _pkg._read_starlark_manifest()
        apps = manifest.get('apps', {})
        installed_count = len(apps)
        enabled_count = sum(1 for a in apps.values() if a.get('enabled', True))

        return jsonify({
            'status': 'success',
            'pixlet_available': pixlet_available,
            'pixlet_version': None,
            'installed_apps': installed_count,
            'enabled_apps': enabled_count,
            'plugin_enabled': True,
            'plugin_loaded': False,
            'display_info': {}
        })

    except Exception as e:
        logger.exception("[Starlark] get_starlark_status failed")
        return jsonify({'status': 'error', 'message': 'Failed to get Starlark status', 'details': describe_exception(e)}), 500
@api_v3.route('/starlark/install-pixlet', methods=['POST'])
def install_pixlet():
    """Download and install Pixlet binary."""
    try:
        script_path = PROJECT_ROOT / 'scripts' / 'download_pixlet.sh'
        if not script_path.exists():
            return jsonify({'status': 'error', 'message': 'Installation script not found'}), 404

        os.chmod(script_path, 0o755)

        result = subprocess.run(
            [str(script_path)],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=300
        )

        if result.returncode == 0:
            logger.info("Pixlet downloaded successfully")
            return jsonify({'status': 'success', 'message': 'Pixlet installed successfully!', 'output': result.stdout})
        else:
            return jsonify({'status': 'error', 'message': f'Failed to download Pixlet: {result.stderr}'}), 500

    except subprocess.TimeoutExpired as err:
        logger.exception("[Starlark] Pixlet download timed out")
        return jsonify({'status': 'error', 'message': 'Download timed out',
                        'details': describe_exception(err)}), 500
    except Exception as e:
        logger.exception("[Starlark] install_pixlet failed")
        return jsonify({'status': 'error', 'message': 'Failed to install Pixlet', 'details': describe_exception(e)}), 500
@api_v3.route('/starlark/apps', methods=['GET'])
def get_starlark_apps():
    """List all installed Starlark apps."""
    try:
        starlark_plugin = _pkg._get_starlark_plugin()
        if starlark_plugin:
            apps_list = []
            for app_id, app_instance in starlark_plugin.apps.items():
                apps_list.append({
                    'id': app_id,
                    'name': app_instance.manifest.get('name', app_id),
                    'enabled': app_instance.is_enabled(),
                    'has_frames': app_instance.frames is not None,
                    'render_interval': app_instance.get_render_interval(),
                    'display_duration': app_instance.get_display_duration(),
                    'config': app_instance.config,
                    'has_schema': app_instance.schema is not None,
                    'last_render_time': app_instance.last_render_time
                })
            return jsonify({'status': 'success', 'apps': apps_list, 'count': len(apps_list)})

        # Standalone: read manifest from disk
        manifest = _pkg._read_starlark_manifest()
        apps_list = []
        for app_id, app_data in manifest.get('apps', {}).items():
            apps_list.append({
                'id': app_id,
                'name': app_data.get('name', app_id),
                'enabled': app_data.get('enabled', True),
                'has_frames': False,
                'render_interval': app_data.get('render_interval', 300),
                'display_duration': app_data.get('display_duration', 15),
                'config': app_data.get('config', {}),
                'has_schema': False,
                'last_render_time': None
            })
        return jsonify({'status': 'success', 'apps': apps_list, 'count': len(apps_list)})

    except Exception as e:
        logger.exception("[Starlark] get_starlark_apps failed")
        return jsonify({'status': 'error', 'message': 'Failed to get Starlark apps', 'details': describe_exception(e)}), 500
@api_v3.route('/starlark/apps/<app_id>', methods=['GET'])
def get_starlark_app(app_id):
    """Get details for a specific Starlark app."""
    try:
        # Validate app_id before any filesystem access
        app_dir, error_msg = _validate_starlark_app_path(app_id)
        if error_msg:
            return jsonify({'status': 'error', 'message': error_msg}), 400

        starlark_plugin = _pkg._get_starlark_plugin()
        if starlark_plugin:
            app = starlark_plugin.apps.get(app_id)
            if not app:
                return jsonify({'status': 'error', 'message': f'App not found: {app_id}'}), 404
            return jsonify({
                'status': 'success',
                'app': {
                    'id': app_id,
                    'name': app.manifest.get('name', app_id),
                    'enabled': app.is_enabled(),
                    'config': app.config,
                    'schema': app.schema,
                    'render_interval': app.get_render_interval(),
                    'display_duration': app.get_display_duration(),
                    'has_frames': app.frames is not None,
                    'frame_count': len(app.frames) if app.frames else 0,
                    'last_render_time': app.last_render_time,
                }
            })

        # Standalone: read from manifest
        manifest = _pkg._read_starlark_manifest()
        app_data = manifest.get('apps', {}).get(app_id)
        if not app_data:
            return jsonify({'status': 'error', 'message': f'App not found: {app_id}'}), 404

        # Load schema from schema.json if it exists (path already validated above)
        schema = None
        schema_file = app_dir / 'schema.json'
        if schema_file.exists():
            try:
                with open(schema_file, 'r') as f:
                    schema = json.load(f)
            except (OSError, json.JSONDecodeError) as e:
                logger.warning(f"Failed to load schema for {app_id}: {e}")

        return jsonify({
            'status': 'success',
            'app': {
                'id': app_id,
                'name': app_data.get('name', app_id),
                'enabled': app_data.get('enabled', True),
                'config': app_data.get('config', {}),
                'schema': schema,
                'render_interval': app_data.get('render_interval', 300),
                'display_duration': app_data.get('display_duration', 15),
                'has_frames': False,
                'frame_count': 0,
                'last_render_time': None,
            }
        })

    except Exception as e:
        logger.exception("[Starlark] get_starlark_app failed")
        return jsonify({'status': 'error', 'message': 'Failed to get Starlark app', 'details': describe_exception(e)}), 500
@api_v3.route('/starlark/upload', methods=['POST'])
def upload_starlark_app():
    """Upload and install a new Starlark app."""
    try:
        if 'file' not in request.files:
            return jsonify({'status': 'error', 'message': 'No file uploaded'}), 400

        file = request.files['file']
        if not file.filename or not file.filename.endswith('.star'):
            return jsonify({'status': 'error', 'message': 'File must have .star extension'}), 400

        # Check file size (limit to 5MB for .star files)
        file.seek(0, 2)  # Seek to end
        file_size = file.tell()
        file.seek(0)  # Reset to beginning
        MAX_STAR_SIZE = 5 * 1024 * 1024  # 5MB
        if file_size > MAX_STAR_SIZE:
            return jsonify({'status': 'error', 'message': f'File too large (max 5MB, got {file_size/1024/1024:.1f}MB)'}), 400

        app_name = request.form.get('name')
        app_id_input = request.form.get('app_id')
        filename_base = file.filename.replace('.star', '') if file.filename else None
        app_id, app_id_error = _validate_and_sanitize_app_id(app_id_input, fallback_source=filename_base)
        if app_id_error:
            return jsonify({'status': 'error', 'message': f'Invalid app_id: {app_id_error}'}), 400

        render_interval_input = request.form.get('render_interval')
        render_interval = 300
        if render_interval_input is not None:
            render_interval, err = _validate_timing_value(render_interval_input, 'render_interval')
            if err:
                return jsonify({'status': 'error', 'message': err}), 400
            render_interval = render_interval or 300

        display_duration_input = request.form.get('display_duration')
        display_duration = 15
        if display_duration_input is not None:
            display_duration, err = _validate_timing_value(display_duration_input, 'display_duration')
            if err:
                return jsonify({'status': 'error', 'message': err}), 400
            display_duration = display_duration or 15

        import tempfile
        with tempfile.NamedTemporaryFile(delete=False, suffix='.star') as tmp:
            file.save(tmp.name)
            temp_path = tmp.name

        try:
            metadata = {'name': app_name or app_id, 'render_interval': render_interval, 'display_duration': display_duration}
            starlark_plugin = _pkg._get_starlark_plugin()
            if starlark_plugin:
                success = starlark_plugin.install_app(app_id, temp_path, metadata)
            else:
                success = _install_star_file(app_id, temp_path, metadata)
            if success:
                return jsonify({'status': 'success', 'message': f'App installed: {app_id}', 'app_id': app_id})
            else:
                return jsonify({'status': 'error', 'message': 'Failed to install app'}), 500
        finally:
            try:
                os.unlink(temp_path)
            except OSError:
                pass

    except (OSError, IOError) as err:
        # This used to withhold the detail because it names absolute paths on
        # the device. A full disk and a bad permission are indistinguishable
        # without it, though, and describe_exception redacts credentials and
        # truncates -- the same trade-off every other handler here makes.
        logger.exception("[Starlark] File error uploading starlark app: %s", err)
        return jsonify({'status': 'error', 'message': 'File error during upload',
                        'details': describe_exception(err)}), 500
    except ImportError as err:
        logger.exception("[Starlark] Module load error uploading starlark app: %s", err)
        return jsonify({'status': 'error', 'message': 'Failed to load app module', 'details': describe_exception(err)}), 500
    except Exception as err:
        logger.exception("[Starlark] Unexpected error uploading starlark app: %s", err)
        return jsonify({'status': 'error', 'message': 'Failed to upload app', 'details': describe_exception(err)}), 500
@api_v3.route('/starlark/apps/<app_id>', methods=['DELETE'])
def uninstall_starlark_app(app_id):
    """Uninstall a Starlark app."""
    try:
        # Validate app_id before any filesystem access
        app_dir, error_msg = _validate_starlark_app_path(app_id)
        if error_msg:
            return jsonify({'status': 'error', 'message': error_msg}), 400

        starlark_plugin = _pkg._get_starlark_plugin()
        if starlark_plugin:
            success = starlark_plugin.uninstall_app(app_id)
        else:
            # Standalone: remove app dir and manifest entry. app_dir is the
            # path _validate_starlark_app_path checked, not a fresh join.
            import shutil
            if app_dir.exists():
                shutil.rmtree(app_dir)
            with _starlark_manifest_lock():
                manifest = _pkg._read_starlark_manifest()
                manifest.get('apps', {}).pop(app_id, None)
                success = _pkg._write_starlark_manifest(manifest)

        if success:
            return jsonify({'status': 'success', 'message': f'App uninstalled: {app_id}'})
        else:
            return jsonify({'status': 'error', 'message': 'Failed to uninstall app'}), 500

    except Exception as e:
        logger.exception("[Starlark] uninstall_starlark_app failed")
        return jsonify({'status': 'error', 'message': 'Failed to uninstall Starlark app', 'details': describe_exception(e)}), 500
@api_v3.route('/starlark/apps/<app_id>/config', methods=['GET'])
def get_starlark_app_config(app_id):
    """Get configuration for a Starlark app."""
    try:
        # Validate app_id before any filesystem access
        app_dir, error_msg = _validate_starlark_app_path(app_id)
        if error_msg:
            return jsonify({'status': 'error', 'message': error_msg}), 400

        starlark_plugin = _pkg._get_starlark_plugin()
        if starlark_plugin:
            app = starlark_plugin.apps.get(app_id)
            if not app:
                return jsonify({'status': 'error', 'message': f'App not found: {app_id}'}), 404
            return jsonify({'status': 'success', 'config': app.config, 'schema': app.schema})

        # Standalone: read from config.json. app_dir is the path
        # _validate_starlark_app_path checked, not a fresh join.
        config_file = app_dir / "config.json"

        if not app_dir.exists():
            return jsonify({'status': 'error', 'message': f'App not found: {app_id}'}), 404

        config = {}
        if config_file.exists():
            try:
                with open(config_file, 'r') as f:
                    config = json.load(f)
            except (OSError, json.JSONDecodeError) as e:
                logger.warning(f"Failed to load config for {app_id}: {e}")

        # Load schema from schema.json
        schema = None
        schema_file = app_dir / "schema.json"
        if schema_file.exists():
            try:
                with open(schema_file, 'r') as f:
                    schema = json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load schema for {app_id}: {e}")

        return jsonify({'status': 'success', 'config': config, 'schema': schema})

    except Exception as e:
        logger.exception("[Starlark] get_starlark_app_config failed")
        return jsonify({'status': 'error', 'message': 'Failed to get Starlark app config', 'details': describe_exception(e)}), 500
@api_v3.route('/starlark/apps/<app_id>/config', methods=['PUT'])
def update_starlark_app_config(app_id):
    """Update configuration for a Starlark app."""
    try:
        # Validate app_id before any filesystem access
        app_dir, error_msg = _validate_starlark_app_path(app_id)
        if error_msg:
            return jsonify({'status': 'error', 'message': error_msg}), 400

        data = request.get_json(silent=True)
        if not data:
            return jsonify({'status': 'error', 'message': 'No configuration provided'}), 400

        if 'render_interval' in data:
            val, err = _validate_timing_value(data['render_interval'], 'render_interval')
            if err:
                return jsonify({'status': 'error', 'message': err}), 400
            data['render_interval'] = val

        if 'display_duration' in data:
            val, err = _validate_timing_value(data['display_duration'], 'display_duration')
            if err:
                return jsonify({'status': 'error', 'message': err}), 400
            data['display_duration'] = val

        starlark_plugin = _pkg._get_starlark_plugin()
        if starlark_plugin:
            app = starlark_plugin.apps.get(app_id)
            if not app:
                return jsonify({'status': 'error', 'message': f'App not found: {app_id}'}), 404

            # Extract timing keys from data before updating config (they belong in manifest, not config)
            render_interval = data.pop('render_interval', None)
            display_duration = data.pop('display_duration', None)

            # Snapshot before mutating. save_config() can fail, and the route
            # answers 500 below when it does -- but the loaded app kept the new
            # values anyway, so a later GET returned configuration that was
            # never persisted and the plugin rendered with it.
            prev_config = dict(app.config)
            prev_manifest = dict(app.manifest)

            # Update config with non-timing fields only
            app.config.update(data)

            # Update manifest with timing fields
            timing_changed = False
            if render_interval is not None:
                app.manifest['render_interval'] = render_interval
                timing_changed = True
            if display_duration is not None:
                app.manifest['display_duration'] = display_duration
                timing_changed = True
            saved = app.save_config()
            if not saved:
                app.config.clear(); app.config.update(prev_config)
                app.manifest.clear(); app.manifest.update(prev_manifest)
            if saved:
                # Persist manifest if timing changed (same pattern as toggle endpoint)
                if timing_changed:
                    try:
                        # Use safe manifest update to prevent race conditions
                        timing_updates = {}
                        if render_interval is not None:
                            timing_updates['render_interval'] = render_interval
                        if display_duration is not None:
                            timing_updates['display_duration'] = display_duration

                        def update_fn(manifest):
                            manifest['apps'][app_id].update(timing_updates)
                        starlark_plugin._update_manifest_safe(update_fn)
                    except Exception as e:
                        logger.warning(f"Failed to persist timing to manifest for {app_id}: {e}")
                starlark_plugin._render_app(app, force=True)
                return jsonify({'status': 'success', 'message': 'Configuration updated', 'config': app.config})
            else:
                return jsonify({'status': 'error', 'message': 'Failed to save configuration'}), 500

        # Standalone: update both config.json and manifest. Both live under
        # the manifest lock (serialized against every other standalone
        # manifest read-modify-write -- see _starlark_manifest_lock), and if
        # the manifest write fails after config.json was already written,
        # config.json is rolled back so the two do not end up out of sync.
        with _starlark_manifest_lock():
            manifest = _pkg._read_starlark_manifest()
            app_data = manifest.get('apps', {}).get(app_id)
            if not app_data:
                return jsonify({'status': 'error', 'message': f'App not found: {app_id}'}), 404

            # Extract timing keys (they go in manifest, not config.json)
            render_interval = data.pop('render_interval', None)
            display_duration = data.pop('display_duration', None)

            # Update manifest with timing values
            if render_interval is not None:
                app_data['render_interval'] = render_interval
            if display_duration is not None:
                app_data['display_duration'] = display_duration

            # Load current config from config.json. app_dir is the path
            # _validate_starlark_app_path checked, not a fresh join.
            config_file = app_dir / "config.json"
            existed_before = config_file.exists()
            previous_config_bytes = None
            current_config = {}
            if existed_before:
                try:
                    previous_config_bytes = config_file.read_bytes()
                    current_config = json.loads(previous_config_bytes)
                except Exception as e:
                    logger.warning(f"Failed to load config for {app_id}: {e}")

            # Update config with new values (excluding timing keys)
            current_config.update(data)

            # Write updated config to config.json
            try:
                with open(config_file, 'w') as f:
                    json.dump(current_config, f, indent=2)
            except Exception as e:
                logger.error(f"Failed to save config.json for {app_id}: {e}")
                logger.exception("Failed to save Starlark configuration for %r", app_id)
                return jsonify({'status': 'error', 'message': 'Failed to save configuration',
                                'details': describe_exception(e)}), 500

            # Also update manifest for backward compatibility
            app_data.setdefault('config', {}).update(data)

            if _pkg._write_starlark_manifest(manifest):
                return jsonify({'status': 'success', 'message': 'Configuration updated', 'config': current_config})

            # The manifest write failed after config.json was already
            # written -- roll config.json back rather than leave the two
            # disagreeing about what was saved.
            try:
                if existed_before and previous_config_bytes is not None:
                    config_file.write_bytes(previous_config_bytes)
                elif not existed_before:
                    config_file.unlink(missing_ok=True)
            except OSError:
                logger.exception(
                    "Failed to roll back config.json for %r after manifest write failure", app_id)
            return jsonify({'status': 'error', 'message': 'Failed to save manifest'}), 500

    except Exception as e:
        logger.exception("[Starlark] update_starlark_app_config failed")
        return jsonify({'status': 'error', 'message': 'Failed to update Starlark app config', 'details': describe_exception(e)}), 500
@api_v3.route('/starlark/apps/<app_id>/toggle', methods=['POST'])
def toggle_starlark_app(app_id):
    """Enable or disable a Starlark app."""
    try:
        data = request.get_json(silent=True) or {}

        starlark_plugin = _pkg._get_starlark_plugin()
        if starlark_plugin:
            app = starlark_plugin.apps.get(app_id)
            if not app:
                return jsonify({'status': 'error', 'message': f'App not found: {app_id}'}), 404
            enabled = data.get('enabled')
            if enabled is None:
                enabled = not app.is_enabled()
            app.manifest['enabled'] = enabled
            # Use safe manifest update to prevent race conditions
            def update_fn(manifest):
                manifest['apps'][app_id]['enabled'] = enabled
            starlark_plugin._update_manifest_safe(update_fn)
            return jsonify({'status': 'success', 'message': f"App {'enabled' if enabled else 'disabled'}", 'enabled': enabled})

        # Standalone: update manifest directly
        with _starlark_manifest_lock():
            manifest = _pkg._read_starlark_manifest()
            app_data = manifest.get('apps', {}).get(app_id)
            if not app_data:
                return jsonify({'status': 'error', 'message': f'App not found: {app_id}'}), 404

            enabled = data.get('enabled')
            if enabled is None:
                enabled = not app_data.get('enabled', True)
            app_data['enabled'] = enabled
            if _pkg._write_starlark_manifest(manifest):
                return jsonify({'status': 'success', 'message': f"App {'enabled' if enabled else 'disabled'}", 'enabled': enabled})
            else:
                return jsonify({'status': 'error', 'message': 'Failed to save'}), 500

    except Exception as e:
        logger.exception("[Starlark] toggle_starlark_app failed")
        return jsonify({'status': 'error', 'message': 'Failed to toggle Starlark app', 'details': describe_exception(e)}), 500
@api_v3.route('/starlark/apps/<app_id>/render', methods=['POST'])
def render_starlark_app(app_id):
    """Force render a Starlark app."""
    try:
        app_dir, err = _validate_starlark_app_path(app_id)
        if err:
            return jsonify({'status': 'error', 'message': err}), 400

        starlark_plugin = _pkg._get_starlark_plugin()
        if starlark_plugin:
            app = starlark_plugin.apps.get(app_id)
            if not app:
                return jsonify({'status': 'error', 'message': f'App not found: {app_id}'}), 404
            success = starlark_plugin._render_app(app, force=True)
            if success:
                return jsonify({'status': 'success', 'message': 'App rendered',
                                'frame_count': len(app.frames) if app.frames else 0})
            return jsonify({'status': 'error', 'message': 'Failed to render app'}), 500

        # Web-service context: plugin not loaded, call pixlet directly
        success, status_code, error = _standalone_render_starlark_app(app_id)
        if success:
            return jsonify({'status': 'success', 'message': 'App rendered successfully', 'frame_count': 0}), status_code
        return jsonify({'status': 'error', 'message': error or 'Render failed', 'frame_count': 0}), status_code

    except Exception as e:
        logger.exception("[Starlark] render_starlark_app failed")
        return jsonify({'status': 'error', 'message': 'Failed to render Starlark app', 'details': describe_exception(e)}), 500
@api_v3.route('/starlark/repository/browse', methods=['GET'])
def browse_tronbyte_repository():
    """Browse all apps in the Tronbyte repository (bulk cached fetch).

    Returns ALL apps with metadata, categories, and authors.
    Filtering/sorting/pagination is handled client-side.
    Results are cached server-side for 2 hours.
    """
    try:
        TronbyteRepository = _pkg._get_tronbyte_repository_class()

        repo = TronbyteRepository(github_token=_starlark_github_token())

        result = repo.list_all_apps_cached()

        rate_limit = repo.get_rate_limit_info()

        # An upstream failure used to arrive here as an empty app list and go
        # out as 'success', so the store drew an empty grid and said nothing.
        # 502: the request was fine, GitHub was not.
        if result.get('error'):
            return jsonify({
                'status': 'error',
                'message': result['error'],
                'rate_limit': rate_limit,
            }), 502

        return jsonify({
            'status': 'success',
            'apps': result['apps'],
            'categories': result['categories'],
            'authors': result['authors'],
            'count': result['count'],
            'cached': result['cached'],
            'rate_limit': rate_limit,
        })

    except Exception as e:
        logger.exception("[Starlark] browse_tronbyte_repository failed")
        return jsonify({'status': 'error', 'message': 'Failed to browse repository', 'details': describe_exception(e)}), 500
@api_v3.route('/starlark/repository/install', methods=['POST'])
def install_from_tronbyte_repository():
    """Install an app from the Tronbyte repository."""
    try:
        data = request.get_json(silent=True)
        if not data or 'app_id' not in data:
            return jsonify({'status': 'error', 'message': 'app_id is required'}), 400

        app_id, app_id_error = _validate_and_sanitize_app_id(data['app_id'])
        if app_id_error:
            return jsonify({'status': 'error', 'message': f'Invalid app_id: {app_id_error}'}), 400

        TronbyteRepository = _pkg._get_tronbyte_repository_class()
        import tempfile

        repo = TronbyteRepository(github_token=_starlark_github_token())

        success, metadata, error = repo.get_app_metadata(data['app_id'])
        if not success:
            return jsonify({'status': 'error', 'message': f'Failed to fetch app metadata: {error}'}), 404

        with tempfile.NamedTemporaryFile(delete=False, suffix='.star') as tmp:
            temp_path = tmp.name

        try:
            # Pass filename from metadata (e.g., "analog_clock.star" for analogclock app)
            # Note: manifest uses 'fileName' (camelCase), not 'filename'
            filename = metadata.get('fileName') if metadata else None
            success, error = repo.download_star_file(data['app_id'], Path(temp_path), filename=filename)
            if not success:
                return jsonify({'status': 'error', 'message': f'Failed to download app: {error}'}), 500

            # Download assets (images, sources, etc.) to a temp directory
            import tempfile
            temp_assets_dir = tempfile.mkdtemp()
            try:
                success_assets, error_assets = repo.download_app_assets(data['app_id'], Path(temp_assets_dir))
                # Asset download is non-critical - log warning but continue if it fails
                if not success_assets:
                    logger.warning(f"Failed to download assets for {data['app_id']}: {error_assets}")

                render_interval = data.get('render_interval', 300)
                ri, err = _validate_timing_value(render_interval, 'render_interval')
                if err:
                    return jsonify({'status': 'error', 'message': err}), 400
                render_interval = ri or 300

                display_duration = data.get('display_duration', 15)
                dd, err = _validate_timing_value(display_duration, 'display_duration')
                if err:
                    return jsonify({'status': 'error', 'message': err}), 400
                display_duration = dd or 15

                install_metadata = {
                    'name': metadata.get('name', app_id) if metadata else app_id,
                    'render_interval': render_interval,
                    'display_duration': display_duration
                }

                starlark_plugin = _pkg._get_starlark_plugin()
                if starlark_plugin:
                    success = starlark_plugin.install_app(app_id, temp_path, install_metadata, assets_dir=temp_assets_dir)
                else:
                    success = _install_star_file(app_id, temp_path, install_metadata, assets_dir=temp_assets_dir)
            finally:
                # Clean up temp assets directory
                import shutil
                try:
                    shutil.rmtree(temp_assets_dir)
                except OSError:
                    pass

            if success:
                return jsonify({'status': 'success', 'message': f'App installed: {metadata.get("name", app_id) if metadata else app_id}', 'app_id': app_id})
            else:
                return jsonify({'status': 'error', 'message': 'Failed to install app'}), 500
        finally:
            try:
                os.unlink(temp_path)
            except OSError:
                pass

    except Exception as e:
        logger.exception("[Starlark] install_from_tronbyte_repository failed")
        return jsonify({'status': 'error', 'message': 'Failed to install from repository', 'details': describe_exception(e)}), 500
@api_v3.route('/starlark/repository/categories', methods=['GET'])
def get_tronbyte_categories():
    """Get list of available app categories (uses bulk cache)."""
    try:
        TronbyteRepository = _pkg._get_tronbyte_repository_class()
        repo = TronbyteRepository(github_token=_starlark_github_token())

        result = repo.list_all_apps_cached()

        if result.get('error'):
            return jsonify({'status': 'error', 'message': result['error']}), 502

        return jsonify({'status': 'success', 'categories': result['categories']})

    except Exception as e:
        logger.exception("[Starlark] get_tronbyte_categories failed")
        return jsonify({'status': 'error', 'message': 'Failed to fetch categories', 'details': describe_exception(e)}), 500


@api_v3.route('/starlark/editor/apps', methods=['GET'])
def list_pixlet_editor_apps():
    """Apps on disk that the editor can open.

    Read from the directory rather than the loaded plugin: the editor works on
    files, and the plugin may not be loaded in this process at all.
    """
    try:
        apps = []
        if _STARLARK_APPS_DIR.is_dir():
            for entry in sorted(_STARLARK_APPS_DIR.iterdir()):
                if not entry.is_dir():
                    continue
                star_files = sorted(entry.glob('*.star'))
                name = entry.name
                manifest = entry / 'manifest.json'
                if manifest.is_file():
                    try:
                        with open(manifest, encoding='utf-8') as handle:
                            data = json.load(handle)
                        if isinstance(data, dict):
                            name = data.get('name') or name
                    except (OSError, json.JSONDecodeError):
                        pass
                apps.append({
                    'id': entry.name,
                    'name': name,
                    'editable': bool(star_files),
                    'has_config': (entry / 'config.json').is_file(),
                })
        return jsonify({'status': 'success', 'data': {
            'apps': apps,
            'apps_dir': str(_STARLARK_APPS_DIR),
            'pixlet_available': _find_pixlet_binary() is not None,
        }})
    except Exception as e:
        logger.exception('Error listing editor apps')
        return jsonify({'status': 'error', 'message': 'Could not list apps',
                        'details': describe_exception(e)}), 500

@api_v3.route('/starlark/editor/status', methods=['GET'])
def get_pixlet_editor_status():
    """Whether a session is running, and how long it has left."""
    try:
        return jsonify({'status': 'success', 'data': _pixlet_editor_status()})
    except Exception as e:
        logger.exception('Error reading editor status')
        return jsonify({'status': 'error', 'message': 'Could not read editor status',
                        'details': describe_exception(e)}), 500

#: Flask runs threaded in the supported service, so two start requests can each
#: observe running=False, each launch an editor, and the second state write
#: replace the first PID -- orphaning a process that holds the display down with
#: nothing left recording it. The check-launch-write sequence takes this lock.
_EDITOR_START_LOCK = threading.Lock()


def _terminate_editor_process(pid, wait_s=5):
    """Signal an editor's process group and wait briefly for it to exit."""
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.kill(pid, signal.SIGTERM)
    deadline = _pkg.time.time() + wait_s
    while _pkg.time.time() < deadline and _pixlet_editor_alive(pid):
        _pkg.time.sleep(0.25)
    if _pixlet_editor_alive(pid):
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            os.killpg(os.getpgid(pid), signal.SIGKILL)


@api_v3.route('/starlark/editor/start', methods=['POST'])
def start_pixlet_editor():
    """Start an editing session for one app."""
    try:
        data = request.get_json(silent=True) or {}
        app_id = data.get('app_id')

        app_dir, err = _validate_starlark_app_path(app_id or '')
        if err or not app_dir:
            return jsonify({'status': 'error', 'message': err or 'Invalid app_id'}), 400
        if not app_dir.is_dir():
            return jsonify({'status': 'error', 'message': f'No such app: {app_id}'}), 404
        if not any(app_dir.glob('*.star')):
            return jsonify({'status': 'error',
                            'message': f'{app_id} has no .star file to edit'}), 400
        if not _PIXLET_EDITOR_SCRIPT.is_file():
            return jsonify({'status': 'error', 'message': 'Editor script not found'}), 404
        if _find_pixlet_binary() is None:
            return jsonify({'status': 'error',
                            'message': 'Pixlet is not installed - install it first'}), 503

        with _EDITOR_START_LOCK:
            current = _pixlet_editor_status()
            if current.get('running'):
                return jsonify({'status': 'error',
                                'message': f"An editor session for '{current.get('app_id')}' is "
                                           f"already running; stop it first"}), 409

            try:
                timeout_s = int(data.get('timeout') or _PIXLET_EDITOR_DEFAULT_TIMEOUT)
            except (TypeError, ValueError):
                return jsonify({'status': 'error', 'message': 'timeout must be a whole number'}), 400
            if not 60 <= timeout_s <= _PIXLET_EDITOR_MAX_TIMEOUT:
                return jsonify({'status': 'error',
                                'message': f'timeout must be between 60 and '
                                           f'{_PIXLET_EDITOR_MAX_TIMEOUT} seconds'}), 400
            try:
                port = int(data.get('port') or _PIXLET_EDITOR_DEFAULT_PORT)
            except (TypeError, ValueError):
                return jsonify({'status': 'error', 'message': 'port must be a whole number'}), 400
            if not 1024 <= port <= 65535:
                return jsonify({'status': 'error', 'message': 'port must be between 1024 and 65535'}), 400

            env = dict(os.environ)
            env['PIXLET_EDITOR_PORT'] = str(port)
            env['PIXLET_EDITOR_TIMEOUT'] = str(timeout_s)
            # A browser reaching this endpoint is remote by definition, so the
            # session needs to listen on more than loopback to be usable at all --
            # but only as a *default*. An operator who has already set
            # PIXLET_EDITOR_HOST (e.g. to keep it loopback-only even from the web
            # UI) must not have that overridden here.
            env.setdefault('PIXLET_EDITOR_HOST', '0.0.0.0')

            log_path = Path(tempfile.gettempdir()) / 'ledmatrix_pixlet_editor.log'
            log_handle = open(log_path, 'w', encoding='utf-8')  # noqa: SIM115 - owned by the child
            try:
                # start_new_session so the script leads its own process group: the
                # stop route signals the group, which is what lets the EXIT trap run
                # and hand the display back.
                process = subprocess.Popen(  # nosec B603 - fixed script path, validated app_id
                    ['/bin/bash', str(_PIXLET_EDITOR_SCRIPT), app_dir.name],
                    cwd=str(PROJECT_ROOT), env=env,
                    stdout=log_handle, stderr=subprocess.STDOUT,
                    start_new_session=True)
            finally:
                log_handle.close()

            now = _pkg.time.time()
            state = {'pid': process.pid, 'app_id': app_dir.name, 'port': port,
                     'timeout': timeout_s, 'started_at': now, 'deadline': now + timeout_s,
                     'host': env['PIXLET_EDITOR_HOST'], 'log': str(log_path)}
            try:
                with open(_PIXLET_EDITOR_STATE, 'w', encoding='utf-8') as handle:
                    json.dump(state, handle)
            except OSError as err:
                # The process is up but nothing records its PID: status and stop
                # would both report no session while the display stays down until
                # the timeout expires. Take the editor with us instead.
                logger.error('Started an editor session but could not record it: %s', err)
                _terminate_editor_process(process.pid)
                return jsonify({'status': 'error',
                                'message': 'Could not record the editor session; '
                                           'the editor was stopped.',
                                'details': describe_exception(err)}), 500

        logger.info('Pixlet editor started for %s on port %s (pid %s, %ss limit)',
                    app_dir.name, port, process.pid, timeout_s)
        return jsonify({'status': 'success',
                        'message': f"Editing '{app_dir.name}'. The display is stopped until "
                                   f"the session ends.",
                        'data': _pixlet_editor_status()})
    except Exception as e:
        logger.exception('Error starting the pixlet editor')
        return jsonify({'status': 'error', 'message': 'Could not start the editor',
                        'details': describe_exception(e)}), 500

@api_v3.route('/starlark/editor/stop', methods=['POST'])
def stop_pixlet_editor():
    """End the running session and give the display back."""
    try:
        state = _read_pixlet_editor_state()
        if not state or not _pixlet_editor_alive(state.get('pid')):
            _clear_pixlet_editor_state()
            return jsonify({'status': 'success', 'message': 'No editor session was running.',
                            'data': {'running': False}})

        pid = int(state['pid'])
        # SIGTERM the group, not the pid: bash forwards nothing to `timeout` and
        # its child on its own, and the trap needs to run to restart the display.
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError) as err:
            logger.debug('Could not signal the editor process group: %s', err)
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.kill(pid, signal.SIGTERM)

        # Give the trap a moment to stop pixlet and restart ledmatrix.
        deadline = _pkg.time.time() + 10
        while _pkg.time.time() < deadline and _pixlet_editor_alive(pid):
            _pkg.time.sleep(0.25)

        escalated = False
        if _pixlet_editor_alive(pid):
            logger.warning('Editor session %s ignored SIGTERM; sending SIGKILL.', pid)
            with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
                os.killpg(os.getpgid(pid), signal.SIGKILL)
            escalated = True

        _clear_pixlet_editor_state()

        if escalated:
            # SIGKILL gives the script's EXIT trap no chance to run, so nothing
            # has handed the display back. Reporting "the display is restarting"
            # here was simply untrue: restart it, and if that fails say so
            # rather than leave the panel dark behind a success response.
            result = _run_systemctl_command(
                ['sudo', 'systemctl', 'start', 'ledmatrix.service'])
            if result.get('returncode') != 0:
                logger.error('Display restart after SIGKILL failed: %s',
                             (result.get('stderr') or '').strip())
                return jsonify({
                    'status': 'error',
                    'message': 'Editor force-stopped, but the display could not be '
                               'restarted automatically - start it manually.',
                    'details': (result.get('stderr') or '').strip(),
                    'data': {'running': False}}), 500
            return jsonify({'status': 'success',
                            'message': 'Editor force-stopped; the display has been '
                                       'restarted.',
                            'data': {'running': False}})

        return jsonify({'status': 'success',
                        'message': 'Editor stopped; the display is restarting.',
                        'data': {'running': False}})
    except Exception as e:
        logger.exception('Error stopping the pixlet editor')
        return jsonify({'status': 'error', 'message': 'Could not stop the editor',
                        'details': describe_exception(e)}), 500
