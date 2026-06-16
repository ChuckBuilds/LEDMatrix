from flask import Blueprint, render_template, flash
from markupsafe import escape
import json
import logging
import os
import os.path
import re
from pathlib import Path

# Strict allowlists for URL-derived values used in path and script operations.
_SAFE_PLUGIN_ID_RE = re.compile(r'^[a-zA-Z0-9_-]{1,64}$')
_SAFE_WEB_UI_FILE_RE = re.compile(r'^[a-zA-Z0-9_-]{1,64}\.html$')
from src.web_interface.secret_helpers import mask_secret_fields

logger = logging.getLogger(__name__)

# Will be initialized when blueprint is registered
config_manager = None
plugin_manager = None
plugin_store_manager = None

pages_v3 = Blueprint('pages_v3', __name__)

@pages_v3.route('/')
def index():
    """Main v3 interface page"""
    try:
        if pages_v3.config_manager:
            # Load configuration data
            main_config = pages_v3.config_manager.load_config()
            schedule_config = main_config.get('schedule', {})

            # Get raw config files for JSON editor
            main_config_data = pages_v3.config_manager.get_raw_file_content('main')
            secrets_config_data = pages_v3.config_manager.get_raw_file_content('secrets')
            main_config_json = json.dumps(main_config_data, indent=4)
            secrets_config_json = json.dumps(secrets_config_data, indent=4)
        else:
            raise Exception("Config manager not initialized")

    except Exception as e:
        flash(f"Error loading configuration: {e}", "error")
        schedule_config = {}
        main_config_json = "{}"
        secrets_config_json = "{}"
        main_config_data = {}
        secrets_config_data = {}

    return render_template('v3/index.html',
                           schedule_config=schedule_config,
                           main_config_json=main_config_json,
                           secrets_config_json=secrets_config_json,
                           main_config_path=pages_v3.config_manager.get_config_path() if pages_v3.config_manager else "",
                           secrets_config_path=pages_v3.config_manager.get_secrets_path() if pages_v3.config_manager else "",
                           main_config=main_config_data,
                           secrets_config=secrets_config_data)

@pages_v3.route('/partials/<partial_name>')
def load_partial(partial_name):
    """Load HTMX partials dynamically"""
    try:
        # Map partial names to specific data loading
        if partial_name == 'overview':
            return _load_overview_partial()
        elif partial_name == 'general':
            return _load_general_partial()
        elif partial_name == 'display':
            return _load_display_partial()
        elif partial_name == 'durations':
            return _load_durations_partial()
        elif partial_name == 'schedule':
            return _load_schedule_partial()
        elif partial_name == 'weather':
            return _load_weather_partial()
        elif partial_name == 'stocks':
            return _load_stocks_partial()
        elif partial_name == 'plugins':
            return _load_plugins_partial()
        elif partial_name == 'fonts':
            return _load_fonts_partial()
        elif partial_name == 'logs':
            return _load_logs_partial()
        elif partial_name == 'raw-json':
            return _load_raw_json_partial()
        elif partial_name == 'backup-restore':
            return _load_backup_restore_partial()
        elif partial_name == 'wifi':
            return _load_wifi_partial()
        elif partial_name == 'cache':
            return _load_cache_partial()
        elif partial_name == 'operation-history':
            return _load_operation_history_partial()
        elif partial_name == 'tools':
            return _load_tools_partial()
        else:
            return "Partial not found", 404

    except Exception as e:
        logger.error("Error loading partial %s", partial_name, exc_info=True)
        return "Error loading partial", 500


@pages_v3.route('/partials/plugin-config/<plugin_id>')
def load_plugin_config_partial(plugin_id):
    """Load plugin configuration partial via HTMX - server-side rendered form"""
    try:
        return _load_plugin_config_partial(plugin_id)
    except Exception:
        logger.error("Error loading plugin config partial for %s", plugin_id, exc_info=True)
        return '<div class="text-red-500 p-4">Error loading plugin config; see logs for details</div>', 500


@pages_v3.route('/plugin-ui/<plugin_id>/web-ui/<path:filename>')
def serve_plugin_web_ui(plugin_id, filename):
    """Serve a plugin's web_ui/ HTML fragment as a standalone page.

    Wraps the fragment with a minimal HTML page that injects window.PLUGIN_ID
    and loads Tailwind CSS so the fragment runs correctly in a sandboxed iframe.
    """
    # Validate URL-derived values against strict allowlists before any path or
    # script operations.
    if not _SAFE_PLUGIN_ID_RE.match(plugin_id):
        return 'Invalid plugin ID', 400, {'Content-Type': 'text/plain'}
    if not _SAFE_WEB_UI_FILE_RE.match(filename):
        return 'Invalid filename', 400, {'Content-Type': 'text/plain'}

    # os.path.basename() is the CodeQL-recognised path sanitizer used throughout
    # this codebase (see plugin_loader.py).  Applying it here breaks the taint
    # chain even though the allowlist above already prevents path separators.
    safe_id = os.path.basename(plugin_id)
    safe_fn = os.path.basename(filename)
    if not safe_id or not safe_fn:
        return 'Invalid path component', 400, {'Content-Type': 'text/plain'}

    if not pages_v3.plugin_manager:
        return 'Plugin manager not available', 503, {'Content-Type': 'text/plain'}

    try:
        _plugins_base = Path(pages_v3.plugin_manager.plugins_dir).resolve()

        # Reconstruct from sanitised basename — CodeQL-approved pattern.
        _plugin_dir = (_plugins_base / safe_id).resolve()
        _plugin_dir.relative_to(_plugins_base)  # containment guard

        # Mirror PluginManager's ledmatrix- prefix fallback.
        if not _plugin_dir.exists():
            _alt_id  = os.path.basename(f'ledmatrix-{safe_id}')
            _alt     = (_plugins_base / _alt_id).resolve()
            try:
                _alt.relative_to(_plugins_base)
                _plugin_dir = _alt
            except ValueError:
                pass

        web_ui_path = (_plugin_dir / 'web_ui' / safe_fn).resolve()
        web_ui_path.relative_to(_plugin_dir / 'web_ui')  # second guard

        if not web_ui_path.exists():
            return 'Not found', 404, {'Content-Type': 'text/plain'}

        fragment = web_ui_path.read_text(encoding='utf-8')

        # json.dumps wraps the value in quotes.  Replace HTML meta-chars with
        # their JS Unicode escape sequences so the value cannot close or escape
        # the enclosing <script> tag.
        # r'<' is the 6-char literal string <, which JavaScript
        # interprets as <.  This is the standard JSON-in-HTML hardening pattern.
        safe_plugin_id_js = (
            json.dumps(safe_id)
            .replace('<', '\\u003c')
            .replace('>', '\\u003e')
            .replace('&', '\\u0026')
        )

        page = (
            '<!DOCTYPE html>\n'
            '<html lang="en">\n'
            '<head>\n'
            '<meta charset="UTF-8">\n'
            '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
            '<script>\n'
            # Inject plugin context before the fragment runs.
            # plugin_id is validated to [a-zA-Z0-9_-] above, so this is safe,
            # but we also Unicode-escape HTML meta-chars as defence in depth.
            f'  window.PLUGIN_ID = {safe_plugin_id_js};\n'
            '</script>\n'
            # Tailwind v2 CDN — same version used by the parent LEDMatrix UI
            '<link rel="stylesheet" '
            'href="https://cdnjs.cloudflare.com/ajax/libs/tailwindcss/2.2.19/tailwind.min.css" '
            'crossorigin="anonymous">\n'
            '<style>body{margin:0;padding:0;background:#fff;}</style>\n'
            '</head>\n'
            '<body>\n'
            + fragment +
            '\n</body>\n</html>'
        )
        return page, 200, {'Content-Type': 'text/html; charset=utf-8'}

    except ValueError:
        return 'Forbidden', 403, {'Content-Type': 'text/plain'}
    except Exception:
        logger.error('Error serving plugin web_ui %s/%s', plugin_id, filename, exc_info=True)
        return 'Error serving file', 500, {'Content-Type': 'text/plain'}

def _load_overview_partial():
    """Load overview partial with system stats"""
    try:
        if pages_v3.config_manager:
            main_config = pages_v3.config_manager.load_config()
            # This would be populated with real system stats via SSE
            return render_template('v3/partials/overview.html',
                                 main_config=main_config)
    except Exception as e:
        logger.error("Error loading partial", exc_info=True)
        return "Error loading partial", 500

def _load_general_partial():
    """Load general settings partial"""
    try:
        if pages_v3.config_manager:
            main_config = pages_v3.config_manager.load_config()
            return render_template('v3/partials/general.html',
                                 main_config=main_config)
    except Exception as e:
        logger.error("Error loading partial", exc_info=True)
        return "Error loading partial", 500

def _load_display_partial():
    """Load display settings partial"""
    try:
        if pages_v3.config_manager:
            main_config = pages_v3.config_manager.load_config()
            return render_template('v3/partials/display.html',
                                 main_config=main_config)
    except Exception as e:
        logger.error("Error loading partial", exc_info=True)
        return "Error loading partial", 500

def _load_durations_partial():
    """Load display durations partial"""
    try:
        if pages_v3.config_manager:
            main_config = pages_v3.config_manager.load_config()
            return render_template('v3/partials/durations.html',
                                 main_config=main_config)
    except Exception as e:
        logger.error("Error loading partial", exc_info=True)
        return "Error loading partial", 500

def _load_schedule_partial():
    """Load schedule settings partial"""
    try:
        if pages_v3.config_manager:
            main_config = pages_v3.config_manager.load_config()
            schedule_config = main_config.get('schedule', {})
            dim_schedule_config = main_config.get('dim_schedule', {})
            # Get normal brightness for display in dim schedule UI
            normal_brightness = main_config.get('display', {}).get('hardware', {}).get('brightness', 90)
            return render_template('v3/partials/schedule.html',
                                 schedule_config=schedule_config,
                                 dim_schedule_config=dim_schedule_config,
                                 normal_brightness=normal_brightness)
    except Exception as e:
        logger.error("Error loading partial", exc_info=True)
        return "Error loading partial", 500


def _load_weather_partial():
    """Load weather configuration partial"""
    try:
        if pages_v3.config_manager:
            main_config = pages_v3.config_manager.load_config()
            return render_template('v3/partials/weather.html',
                                 main_config=main_config)
    except Exception as e:
        logger.error("Error loading partial", exc_info=True)
        return "Error loading partial", 500

def _load_stocks_partial():
    """Load stocks configuration partial"""
    try:
        if pages_v3.config_manager:
            main_config = pages_v3.config_manager.load_config()
            return render_template('v3/partials/stocks.html',
                                 main_config=main_config)
    except Exception as e:
        logger.error("Error loading partial", exc_info=True)
        return "Error loading partial", 500

def _load_plugins_partial():
    """Load plugins management partial"""
    try:
        import json
        from pathlib import Path
        
        # Load plugin data from the plugin system
        plugins_data = []

        # Get installed plugins if managers are available
        if pages_v3.plugin_manager and pages_v3.plugin_store_manager:
            try:
                # Get all installed plugin info
                all_plugin_info = pages_v3.plugin_manager.get_all_plugin_info()

                # Load config once before the loop (not per-plugin)
                full_config = pages_v3.config_manager.load_config() if pages_v3.config_manager else {}

                # Format for the web interface
                for plugin_info in all_plugin_info:
                    plugin_id = plugin_info.get('id')

                    # Re-read manifest from disk to ensure we have the latest metadata
                    manifest_path = Path(pages_v3.plugin_manager.plugins_dir) / plugin_id / "manifest.json"
                    if manifest_path.exists():
                        try:
                            with open(manifest_path, 'r', encoding='utf-8') as f:
                                fresh_manifest = json.load(f)
                            # Update plugin_info with fresh manifest data
                            plugin_info.update(fresh_manifest)
                        except Exception as e:
                            # If we can't read the fresh manifest, use the cached one
                            logger.warning("Could not read fresh manifest for plugin: %s", plugin_id)

                    # Get enabled status from config (source of truth)
                    # Read from config file first, fall back to plugin instance if config doesn't have the key
                    enabled = None
                    if pages_v3.config_manager:
                        plugin_config = full_config.get(plugin_id, {})
                        # Check if 'enabled' key exists in config (even if False)
                        if 'enabled' in plugin_config:
                            enabled = bool(plugin_config['enabled'])
                    
                    # Fallback to plugin instance if config doesn't have enabled key
                    if enabled is None:
                        plugin_instance = pages_v3.plugin_manager.get_plugin(plugin_id)
                        if plugin_instance:
                            enabled = plugin_instance.enabled
                        else:
                            # Default to True if no config key and plugin not loaded (matches BasePlugin default)
                            enabled = True

                    # Get verified status from store registry (no GitHub API calls needed)
                    store_info = pages_v3.plugin_store_manager.get_registry_info(plugin_id)
                    verified = store_info.get('verified', False) if store_info else False

                    last_updated = plugin_info.get('last_updated')
                    last_commit = plugin_info.get('last_commit') or plugin_info.get('last_commit_sha')
                    branch = plugin_info.get('branch')

                    if store_info:
                        last_updated = last_updated or store_info.get('last_updated') or store_info.get('last_updated_iso')
                        last_commit = last_commit or store_info.get('last_commit') or store_info.get('last_commit_sha')
                        branch = branch or store_info.get('branch') or store_info.get('default_branch')

                    plugins_data.append({
                        'id': plugin_id,
                        'name': plugin_info.get('name', plugin_id),
                        'author': plugin_info.get('author', 'Unknown'),
                        'category': plugin_info.get('category', 'General'),
                        'description': plugin_info.get('description', 'No description available'),
                        'tags': plugin_info.get('tags', []),
                        'enabled': enabled,
                        'verified': verified,
                        'loaded': plugin_info.get('loaded', False),
                        'last_updated': last_updated,
                        'last_commit': last_commit,
                        'branch': branch
                    })
            except Exception as e:
                logger.error("Error loading plugin data", exc_info=True)

        return render_template('v3/partials/plugins.html',
                             plugins=plugins_data)
    except Exception as e:
        logger.error("Error loading partial", exc_info=True)
        return "Error loading partial", 500

def _load_fonts_partial():
    """Load fonts management partial"""
    try:
        # This would load font data from the font system
        fonts_data = {}  # Placeholder for font data
        return render_template('v3/partials/fonts.html',
                             fonts=fonts_data)
    except Exception as e:
        logger.error("Error loading partial", exc_info=True)
        return "Error loading partial", 500

def _load_logs_partial():
    """Load logs viewer partial"""
    try:
        return render_template('v3/partials/logs.html')
    except Exception as e:
        logger.error("Error loading partial", exc_info=True)
        return "Error loading partial", 500

def _load_raw_json_partial():
    """Load raw JSON editor partial"""
    try:
        if pages_v3.config_manager:
            main_config_data = pages_v3.config_manager.get_raw_file_content('main')
            secrets_config_data = pages_v3.config_manager.get_raw_file_content('secrets')
            main_config_json = json.dumps(main_config_data, indent=4)
            secrets_config_json = json.dumps(secrets_config_data, indent=4)

            return render_template('v3/partials/raw_json.html',
                                 main_config_json=main_config_json,
                                 secrets_config_json=secrets_config_json,
                                 main_config_path=pages_v3.config_manager.get_config_path(),
                                 secrets_config_path=pages_v3.config_manager.get_secrets_path())
    except Exception as e:
        logger.error("Error loading partial", exc_info=True)
        return "Error loading partial", 500

def _load_backup_restore_partial():
    """Load backup & restore partial."""
    try:
        return render_template('v3/partials/backup_restore.html')
    except Exception as e:
        logger.error("Error loading partial", exc_info=True)
        return "Error loading partial", 500

@pages_v3.route('/setup')
def captive_setup():
    """Lightweight captive portal setup page — self-contained, no frameworks."""
    return render_template('v3/captive_setup.html')

def _load_wifi_partial():
    """Load WiFi setup partial"""
    try:
        return render_template('v3/partials/wifi.html')
    except Exception as e:
        logger.error("Error loading partial", exc_info=True)
        return "Error loading partial", 500

def _load_cache_partial():
    """Load cache management partial"""
    try:
        return render_template('v3/partials/cache.html')
    except Exception as e:
        logger.error("Error loading partial", exc_info=True)
        return "Error loading partial", 500

def _load_operation_history_partial():
    """Load operation history partial"""
    try:
        return render_template('v3/partials/operation_history.html')
    except Exception as e:
        logger.error("Error loading partial", exc_info=True)
        return "Error loading partial", 500


def _load_tools_partial():
    """Load tools/utilities partial."""
    try:
        return render_template('v3/partials/tools.html')
    except Exception:
        logger.error("Error loading partial", exc_info=True)
        return "Error loading partial", 500


def _load_plugin_config_partial(plugin_id):
    """
    Load plugin configuration partial - server-side rendered form.
    This replaces the client-side generateConfigForm() JavaScript.
    """
    # Sanitize with basename (CodeQL-recognized sanitizer) then regex-validate format
    plugin_id = os.path.basename(plugin_id or '')
    if not re.match(r'^[a-zA-Z0-9][a-zA-Z0-9._\-:]*$', plugin_id):
        return '<div class="text-red-500 p-4">Invalid plugin ID</div>', 400

    try:
        if not pages_v3.plugin_manager:
            return '<div class="text-red-500 p-4">Plugin manager not available</div>', 500

        # Handle starlark app config (starlark:<app_id>)
        if plugin_id.startswith('starlark:'):
            return _load_starlark_config_partial(plugin_id[len('starlark:'):])

        # Resolve and validate all plugin paths against the plugins base directory
        _plugins_base = Path(pages_v3.plugin_manager.plugins_dir).resolve()
        _plugin_dir = (_plugins_base / plugin_id).resolve()
        try:
            _plugin_dir.relative_to(_plugins_base)
        except ValueError:
            return '<div class="text-red-500 p-4">Invalid plugin ID</div>', 400

        # Try to get plugin info first
        plugin_info = pages_v3.plugin_manager.get_plugin_info(plugin_id)

        # If not found, re-discover plugins (handles plugins added after startup)
        if not plugin_info:
            pages_v3.plugin_manager.discover_plugins()
            plugin_info = pages_v3.plugin_manager.get_plugin_info(plugin_id)

        if not plugin_info:
            return '<div class="text-red-500 p-4">Plugin not found</div>', 404

        # Get plugin instance (may be None if not loaded)
        plugin_instance = pages_v3.plugin_manager.get_plugin(plugin_id)

        # Get plugin configuration from config file
        config = {}
        if pages_v3.config_manager:
            full_config = pages_v3.config_manager.load_config()
            config = full_config.get(plugin_id, {})

        # Load uploaded images from metadata file if images field exists in schema
        schema_path_temp = _plugin_dir / "config_schema.json"
        if schema_path_temp.exists():
            try:
                with open(schema_path_temp, 'r', encoding='utf-8') as f:
                    temp_schema = json.load(f)
                    if (temp_schema.get('properties', {}).get('images', {}).get('x-widget') == 'file-upload' or
                        temp_schema.get('properties', {}).get('images', {}).get('x_widget') == 'file-upload'):
                        _assets_base = (Path(__file__).parent.parent.parent / 'assets' / 'plugins').resolve()
                        metadata_file = (_assets_base / plugin_id / 'uploads' / '.metadata.json').resolve()
                        try:
                            metadata_file.relative_to(_assets_base)
                        except ValueError:
                            metadata_file = None
                        if metadata_file and metadata_file.exists():
                            try:
                                with open(metadata_file, 'r', encoding='utf-8') as mf:
                                    metadata = json.load(mf)
                                    images_from_metadata = list(metadata.values())
                                    if not config.get('images') or len(config.get('images', [])) == 0:
                                        config['images'] = images_from_metadata
                                    else:
                                        config_image_ids = {img.get('id') for img in config.get('images', []) if img.get('id')}
                                        new_images = [img for img in images_from_metadata if img.get('id') not in config_image_ids]
                                        if new_images:
                                            config['images'] = config.get('images', []) + new_images
                            except Exception as e:
                                logger.warning("Could not load plugin upload metadata: %s", e)
            except Exception as e:  # nosec B110 - metadata pre-load is optional; schema loads fully below
                logger.debug("Metadata pre-load skipped for plugin %s: %s", plugin_id, e)

        # Get plugin schema
        schema = {}
        schema_path = _plugin_dir / "config_schema.json"
        if schema_path.exists():
            try:
                with open(schema_path, 'r', encoding='utf-8') as f:
                    schema = json.load(f)
            except Exception as e:
                logger.warning("Could not load schema for plugin: %s", e)

        # Get web UI actions from plugin manifest
        web_ui_actions = []
        manifest_path = _plugin_dir / "manifest.json"
        if manifest_path.exists():
            try:
                with open(manifest_path, 'r', encoding='utf-8') as f:
                    manifest = json.load(f)
                    web_ui_actions = manifest.get('web_ui_actions', [])
            except Exception as e:
                logger.warning("Could not load manifest for plugin: %s", e)
        
        # Mask secret fields before rendering template (fail closed — never leak secrets)
        schema_properties = schema.get('properties') if isinstance(schema, dict) else None
        if not isinstance(schema_properties, dict):
            return '<div class="text-red-500 p-4">Error loading plugin config securely: schema unavailable.</div>', 500
        config = mask_secret_fields(config, schema_properties)

        # Determine enabled status
        enabled = config.get('enabled', True)
        if plugin_instance:
            enabled = plugin_instance.enabled

        # Build plugin data for template
        plugin_data = {
            'id': plugin_id,
            'name': plugin_info.get('name', plugin_id),
            'author': plugin_info.get('author', 'Unknown'),
            'version': plugin_info.get('version', ''),
            'description': plugin_info.get('description', ''),
            'category': plugin_info.get('category', 'General'),
            'tags': plugin_info.get('tags', []),
            'enabled': enabled,
            'last_commit': plugin_info.get('last_commit') or plugin_info.get('last_commit_sha', ''),
            'branch': plugin_info.get('branch', ''),
        }
        
        return render_template(
            'v3/partials/plugin_config.html',
            plugin=plugin_data,
            config=config,
            schema=schema,
            web_ui_actions=web_ui_actions
        )
        
    except Exception as e:
        logger.error("Error loading plugin config partial for %s", plugin_id, exc_info=True)
        return '<div class="text-red-500 p-4">Error loading plugin config; see logs for details</div>', 500


def _load_starlark_config_partial(app_id):
    """Load configuration partial for a Starlark app."""
    # Sanitize with basename (CodeQL-recognized sanitizer) then regex-validate format
    app_id = os.path.basename(app_id or '')
    if not re.match(r'^[a-zA-Z0-9][a-zA-Z0-9_\-]*$', app_id):
        return '<div class="text-red-500 p-4">Invalid app ID</div>', 400

    try:
        starlark_plugin = pages_v3.plugin_manager.get_plugin('starlark-apps') if pages_v3.plugin_manager else None

        if starlark_plugin and hasattr(starlark_plugin, 'apps'):
            app = starlark_plugin.apps.get(app_id)
            if not app:
                return '<div class="text-red-500 p-4">Starlark app not found</div>', 404
            return render_template(
                'v3/partials/starlark_config.html',
                app_id=app_id,
                app_name=app.manifest.get('name', app_id),
                app_enabled=app.is_enabled(),
                render_interval=app.get_render_interval(),
                display_duration=app.get_display_duration(),
                config=app.config,
                schema=app.schema,
                has_frames=app.frames is not None,
                frame_count=len(app.frames) if app.frames else 0,
                last_render_time=app.last_render_time,
            )

        # Standalone: read from manifest file
        starlark_base = (Path(__file__).resolve().parent.parent.parent / 'starlark-apps').resolve()
        manifest_file = starlark_base / 'manifest.json'
        if not manifest_file.exists():
            return '<div class="text-red-500 p-4">Starlark app not found</div>', 404

        with open(manifest_file, 'r') as f:
            manifest = json.load(f)

        app_data = manifest.get('apps', {}).get(app_id)
        if not app_data:
            return '<div class="text-red-500 p-4">Starlark app not found</div>', 404

        # Load schema from schema.json if it exists — validate path stays within starlark_base
        schema = None
        schema_file = (starlark_base / app_id / 'schema.json').resolve()
        try:
            schema_file.relative_to(starlark_base)
        except ValueError:
            schema_file = None
        if schema_file and schema_file.exists():
            try:
                with open(schema_file, 'r') as f:
                    schema = json.load(f)
            except (OSError, json.JSONDecodeError) as e:
                logger.warning("Could not load starlark schema for app: %s", e)

        # Load config from config.json if it exists — validate path stays within starlark_base
        config = {}
        config_file = (starlark_base / app_id / 'config.json').resolve()
        try:
            config_file.relative_to(starlark_base)
        except ValueError:
            config_file = None
        if config_file and config_file.exists():
            try:
                with open(config_file, 'r') as f:
                    config = json.load(f)
            except (OSError, json.JSONDecodeError) as e:
                logger.warning("Could not load starlark config for app: %s", e)

        return render_template(
            'v3/partials/starlark_config.html',
            app_id=app_id,
            app_name=app_data.get('name', app_id),
            app_enabled=app_data.get('enabled', True),
            render_interval=app_data.get('render_interval', 300),
            display_duration=app_data.get('display_duration', 15),
            config=config,
            schema=schema,
            has_frames=False,
            frame_count=0,
            last_render_time=None,
        )

    except Exception as e:
        logger.error("[Pages V3] Error loading starlark config for app", exc_info=True)
        return '<div class="text-red-500 p-4">Error loading starlark config; see logs for details</div>', 500
