"""Installed-plugin listing, enable/disable, and plugin web-UI actions.

Routes decorate the shared `api_v3` Blueprint from the package `__init__`,
so their endpoint names do not depend on which module they live in.
"""
from web_interface.blueprints.api_v3 import (
    ErrorCode, PROJECT_ROOT, Path, _coerce_to_bool,
    _is_plugin_update_available, _plugin_directory, _starlark_virtual_plugins,
    _toggle_starlark_app, api_v3, describe_exception, error_response, json,
    jsonify, logger, os, request, subprocess, success_response,
)
from src.common.path_safety import safe_path_component
from src.plugin_system.base_plugin import (
    configured_vegas_participation, vegas_participation_value,
)
import web_interface.blueprints.api_v3 as _pkg
# Read through the module rather than bound by value: tests patch these
# as module attributes, and a value binding would not see the patch.
# Several are also called from helpers that live in __init__, so the
# package is the only patch point that covers every caller.


def _vegas_participation(plugin_id, plugin_config, manifest):
    """What Vegas does with a plugin, as far as its files say, and from where.

    The order the display resolves it in (resolve_vegas_participation), up
    to where that needs the plugin's code: the user's ``vegas_participation`` setting
    (``'config'``), then the manifest's declared ``vegas_participation``
    (``'manifest'``). Past those the display asks the plugin itself -- a
    get_vegas_participation() override or the legacy Vegas hooks -- which the
    web process never runs, so the answer is ``(None, 'runtime')``: decided
    at run time, not guessed here. A plugin that overrides
    get_vegas_participation() can still differ from its manifest.
    """
    configured = configured_vegas_participation(plugin_id, plugin_config)
    if configured is not None:
        return configured, 'config'
    declared = vegas_participation_value(
        manifest.get('vegas_participation') if isinstance(manifest, dict) else None)
    if declared is not None:
        return declared, 'manifest'
    return None, 'runtime'


@api_v3.route('/plugins/installed', methods=['GET'])
def get_installed_plugins():
    """Get installed plugins.

    Metadata comes from the plugin catalog (manifests on disk), ``enabled``
    from config.json. ``loaded``, ``state``, ``error_info``,
    ``loaded_version`` and ``loaded_at`` come from the runtime snapshot the
    display publishes (src/plugin_system/plugin_runtime.py), and only while
    that snapshot is live: when the display is stopped, hung or has never
    published, they are null and ``data.runtime.status`` says why
    (``stalled``, ``stale``, ``stopped``, ``unknown``) instead of passing on
    old truth.
    Health, metrics and errors are served by /plugins/health,
    /plugins/metrics and /errors.
    """
    if not api_v3.plugin_catalog or not api_v3.plugin_store_manager:
        return jsonify({'status': 'error', 'message': 'Plugin managers not initialized'}), 500

    # Re-discover plugins to ensure we have the latest list
    # This handles cases where plugins are added/removed after app startup
    api_v3.plugin_catalog.discover_plugins()

    # Get all installed plugin info from the catalog
    all_plugin_info = api_v3.plugin_catalog.get_all_plugin_info()

    # Load config once before the loop (not per-plugin)
    full_config = api_v3.config_manager.load_config() if api_v3.config_manager else {}
    # One read of the display's snapshot for the whole listing.
    runtime = _pkg._plugin_runtime_view()

    def _build_plugin_entry(plugin_info):
        plugin_id = plugin_info.get('id')
        try:
            return _build_plugin_entry_inner(plugin_info, plugin_id)
        except Exception:
            logger.exception("Error building plugin entry for %s — skipping", plugin_id)
            return None

    def _build_plugin_entry_inner(plugin_info, plugin_id):
        # Re-read manifest from disk to ensure we have the latest metadata.
        # Through the resolver, not plugins_dir/<id>: a plugin installed as
        # ledmatrix-<id> otherwise never had its manifest refreshed here.
        plugin_path = _plugin_directory(plugin_id)
        manifest_path = plugin_path / "manifest.json" if plugin_path else None
        if manifest_path is not None and manifest_path.exists():
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

        # Enabled status: config.json, read by the display's rule -- it runs
        # a plugin only when its section says "enabled": true, so a missing
        # flag is disabled here too.
        plugin_config = full_config.get(plugin_id, {})
        if not isinstance(plugin_config, dict):
            plugin_config = {}
        enabled = bool(plugin_config.get('enabled', False))

        # Verified + latest published version from registry (no network call)
        store_info = api_v3.plugin_store_manager.get_registry_info(plugin_id)
        verified = store_info.get('verified', False) if store_info else False
        latest_version = store_info.get('latest_version', '') if store_info else ''
        installed_version = plugin_info.get('version', '')
        update_available = _is_plugin_update_available(installed_version, latest_version)

        # Local git info (single subprocess on cache miss, zero on hit)
        local_git_info = api_v3.plugin_store_manager._get_local_git_info(plugin_path) if plugin_path else None

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

        # Vegas mode as configured. What a plugin's code would choose on its
        # own is only known to the display, which runs it.
        vegas_mode = plugin_config.get('vegas_mode')
        vegas_content_type = None

        # What Vegas does with it: 'scroll', 'pause' or 'exclude', or None
        # when only the plugin's code (run by the display) decides. The Vegas
        # order list badges a None as its configured vegas_mode, else Scroll.
        vegas_participation, vegas_participation_source = _vegas_participation(
            plugin_id, plugin_config, plugin_info)

        # The modes the manifest declares, from the catalog as /display/modes
        # and on-demand/start read them. The on-demand modal offers these;
        # without them it offered only the plugin id, which the display
        # turns into the first mode. Strings only: a manifest is hand-edited.
        declared_modes = api_v3.plugin_catalog.get_plugin_display_modes(plugin_id)
        display_modes = ([m for m in declared_modes if isinstance(m, str)]
                         if isinstance(declared_modes, list) else [])

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
            # The tab nav uses this as the <i> element's Font Awesome class
            # (app-shell.js / app-early.js); only a string can be one.
            'icon': plugin_info.get('icon') if isinstance(plugin_info.get('icon'), str) else None,
            'display_modes': display_modes,
            'enabled': enabled,
            'verified': verified,
            # loaded, state, error_info, loaded_version, loaded_at: the
            # display's snapshot, null unless it is live (see the docstring).
            **runtime.plugin(plugin_id),
            'last_updated': last_updated,
            'last_commit': last_commit,
            'last_commit_message': last_commit_message,
            'branch': branch,
            'web_ui_actions': plugin_info.get('web_ui_actions', []),
            'vegas_mode': vegas_mode,
            'vegas_content_type': vegas_content_type,
            'vegas_participation': vegas_participation,
            'vegas_participation_source': vegas_participation_source,
        }

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(_build_plugin_entry, all_plugin_info))
    plugins = [r for r in results if r is not None]
    plugins.extend(_starlark_virtual_plugins())

    return jsonify({'status': 'success', 'data': {'plugins': plugins,
                                                  'runtime': runtime.describe()}})


@api_v3.route('/plugins/toggle', methods=['POST'])
def toggle_plugin():
    """Toggle plugin enabled/disabled"""
    plugin_id = None
    enabled = None
    try:
        if not api_v3.plugin_catalog or not api_v3.config_manager:
            return jsonify({'status': 'error', 'message': 'Plugin or config manager not initialized'}), 500

        # Support both JSON and form data (for HTMX submissions)
        content_type = request.content_type or ''

        if 'application/json' in content_type:
            data = request.get_json(silent=True)
            if not data or 'plugin_id' not in data or 'enabled' not in data:
                return jsonify({'status': 'error', 'message': 'plugin_id and enabled required'}), 400
            plugin_id = data['plugin_id']
            # Coerced, not stored raw: "false" is a truthy string, and this
            # value is written to config.json and handed to the Starlark
            # toggle, so {"enabled": "false"} used to switch a plugin ON.
            enabled = _coerce_to_bool(data['enabled'])
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

        # A Starlark app is not a plugin in the catalog's sense -- it is an
        # entry in starlark-apps' own manifest -- so its enable/disable is
        # handled here rather than falling through to the check below, which
        # would answer "Plugin not found".
        if plugin_id.startswith('starlark:'):
            return _toggle_starlark_app(plugin_id[len('starlark:'):], enabled)

        # Check if plugin exists in manifests (discovered but may not be loaded)
        if plugin_id not in _pkg._discovered_plugin_manifests(plugin_id):
            return jsonify({'status': 'error', 'message': 'Plugin not found'}), 404

        # Update config (this is what the display controller reads)
        config = api_v3.config_manager.load_config()
        if plugin_id not in config:
            config[plugin_id] = {}
        config[plugin_id]['enabled'] = enabled

        success, error_msg = _pkg._save_config_atomic(api_v3.config_manager, config, create_backup=True)
        if not success:
            return error_response(
                ErrorCode.CONFIG_SAVE_FAILED,
                f"Failed to save configuration: {error_msg}",
                status_code=500
            )

        # Log operation
        if api_v3.operation_history:
            api_v3.operation_history.record_operation(
                "enable" if enabled else "disable",
                plugin_id=plugin_id,
                status="success"
            )

        # No lifecycle hooks here: the display's config watcher sees the
        # enabled flag change and loads or unloads the plugin itself
        # (DisplayController._reconcile_enabled_plugins).
        return success_response(
            message=f"Plugin {plugin_id} {'enabled' if enabled else 'disabled'} successfully"
        )
    except Exception as e:
        # Not PLUGIN_OPERATION_CONFLICT: that told the user "an operation is
        # already in progress" whatever actually went wrong.
        logger.error('Error toggling plugin %s', plugin_id, exc_info=True)
        if api_v3.operation_history:
            api_v3.operation_history.record_operation(
                "enable" if enabled else "disable",
                plugin_id=plugin_id,
                status="failed",
                error=str(e)
            )
        action = 'enable' if enabled else 'disable' if enabled is not None else 'toggle'
        return error_response(
            ErrorCode.UNKNOWN_ERROR,
            f"Failed to {action} plugin {plugin_id or ''}".rstrip(),
            details=describe_exception(e),
            status_code=500
        )


@api_v3.route('/plugins/action', methods=['POST'])
def execute_plugin_action():
    """Execute a plugin-defined action (e.g., authentication)"""
    try:
        # Try to get JSON data, with better error handling
        try:
            data = request.get_json(force=True) or {}
        except Exception as e:
            # The module logger, not a local one: binding `logger` anywhere in
            # this function made every other `logger.error` here raise
            # UnboundLocalError, so the step-1 handler below reported that
            # instead of the plugin script's real failure.
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

        # plugin_id comes from the request body and picks the directory whose
        # manifest names the script to run, so it must be a plain name.
        plugin_id = safe_path_component(plugin_id)
        if plugin_id is None:
            return jsonify({'status': 'error', 'message': 'Invalid plugin_id'}), 400

        plugin_dir = _plugin_directory(plugin_id)
        if not plugin_dir:
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

                    # The params reach the wrapper on its stdin, never in
                    # its source: written there as `params = <JSON>`, a
                    # true, false or null was an undefined name and the
                    # wrapper died with a NameError before the script ran.
                    params_json = json_lib.dumps(action_params)
                    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as wrapper:
                        wrapper.write(f'''import sys
import subprocess
import os
import json

# Set LEDMATRIX_ROOT
os.environ['LEDMATRIX_ROOT'] = r"{PROJECT_ROOT}"

# The params, as JSON on this wrapper's own stdin
params = json.loads(sys.stdin.read())

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
stdout, _ = proc.communicate(input=json.dumps(params), timeout=120)
print(stdout)
sys.exit(proc.returncode)
''')
                        wrapper_path = wrapper.name

                    try:
                        result = subprocess.run(
                            ['python3', wrapper_path],
                            input=params_json,
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
                        try:
                            # Plugin code in the web process: see the
                            # function for why, and what replaces it.
                            action_module = _pkg._import_plugin_code_in_web_process(
                                "plugin_action", script_file, reuse=False)

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
                            # Not a copy of the blueprint handler: without it, a
                            # TimeoutExpired from the plugin's script would reach
                            # this route's own `except subprocess.TimeoutExpired`
                            # and be answered as a 408 "Action timed out".
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
