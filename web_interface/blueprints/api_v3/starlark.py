"""Starlark / Tronbyte app management routes.

Routes decorate the shared `api_v3` Blueprint from ._common, so their
endpoint names are unchanged by living here.
"""
from web_interface.blueprints.api_v3 import (
    PROJECT_ROOT, Path, _find_pixlet_binary, _install_star_file, _standalone_render_starlark_app,
    _starlark_github_token, _validate_and_sanitize_app_id,
    _validate_starlark_app_path, _validate_timing_value, api_v3, describe_exception, json, jsonify,
    logger, os, request, shutil, subprocess, tempfile,
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

        # Standalone: update both config.json and manifest
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
        current_config = {}
        if config_file.exists():
            try:
                with open(config_file, 'r') as f:
                    current_config = json.load(f)
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
        else:
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
