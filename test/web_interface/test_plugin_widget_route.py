"""The server half of the plugin-supplied widget feature.

``LEDMatrixWidgets.loadPluginWidget`` (static/v3/js/widgets/plugin-loader.js)
has always fetched ``/static/plugin-widgets/<plugin>/<widget>.js``, and
docs/widget-guide.md has always documented that path, but nothing served it --
so a plugin could declare a widget, ship the file, and still never load it.
soccer-scoreboard has shipped exactly that since August.

The load-bearing property here is that the manifest is the allowlist. A plugin
directory is attacker-influenced in the sense that matters -- plugins are
user-installed, and the store installs them -- so "serve files from the plugin
directory" would publish everything a plugin ships. Only a widget the manifest
declares is reachable, and only from that plugin's widgets/ directory.
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from flask import Flask

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

WIDGET_BODY = "(function(){ window.LEDMatrixWidgets.register('custom-leagues', {}); })();\n"

_UNSET = object()


def _make_plugin(plugins_dir, plugin_id="soccer-scoreboard", widgets=None,
                 files=None):
    """Write a plugin directory with a manifest and a widgets/ folder."""
    d = plugins_dir / plugin_id
    (d / "widgets").mkdir(parents=True)
    manifest = {"id": plugin_id, "name": plugin_id, "version": "1.0.0"}
    if widgets is not None:
        manifest["widgets"] = widgets
    (d / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    for name, body in (files or {}).items():
        (d / "widgets" / name).write_text(body, encoding="utf-8")
    return d


@pytest.fixture
def make_client(tmp_path):
    """Build a test client whose plugin manager points at a temp plugins dir.

    pages_v3 is a module-level Blueprint singleton shared across the test
    process, so the original plugin_manager is restored on teardown.
    """
    from web_interface.blueprints import pages_v3 as pv

    original_pm = getattr(pv.pages_v3, "plugin_manager", None)

    def _build(plugins_dir=None, plugin_manager=_UNSET):
        base = PROJECT_ROOT / "web_interface"
        app = Flask(__name__,
                    template_folder=str(base / "templates"),
                    static_folder=str(base / "static"))
        app.config["TESTING"] = True

        if plugin_manager is _UNSET:
            plugin_manager = MagicMock()
            plugin_manager.plugins_dir = str(plugins_dir or tmp_path)
        pv.pages_v3.plugin_manager = plugin_manager

        app.register_blueprint(pv.pages_v3, url_prefix="")
        return app.test_client()

    try:
        yield _build
    finally:
        pv.pages_v3.plugin_manager = original_pm


URL = "/static/plugin-widgets/{}/{}.js"


class TestDeclaredWidgetIsServed:
    def test_a_declared_widget_is_served(self, tmp_path, make_client):
        _make_plugin(tmp_path,
                     widgets=[{"name": "custom-leagues",
                               "script": "custom-leagues.js"}],
                     files={"custom-leagues.js": WIDGET_BODY})
        r = make_client().get(URL.format("soccer-scoreboard", "custom-leagues"))
        assert r.status_code == 200
        assert r.get_data(as_text=True) == WIDGET_BODY

    def test_it_is_served_as_javascript(self, tmp_path, make_client):
        """The loader uses dynamic import(); a wrong MIME type is refused."""
        _make_plugin(tmp_path,
                     widgets=[{"name": "custom-leagues",
                               "script": "custom-leagues.js"}],
                     files={"custom-leagues.js": WIDGET_BODY})
        r = make_client().get(URL.format("soccer-scoreboard", "custom-leagues"))
        assert "javascript" in r.headers["Content-Type"]

    def test_script_defaults_to_the_widget_name(self, tmp_path, make_client):
        _make_plugin(tmp_path, widgets=[{"name": "custom-leagues"}],
                     files={"custom-leagues.js": WIDGET_BODY})
        r = make_client().get(URL.format("soccer-scoreboard", "custom-leagues"))
        assert r.status_code == 200

    def test_the_ledmatrix_prefix_fallback_resolves(self, tmp_path, make_client):
        """PluginManager resolves 'music' to 'ledmatrix-music'; so must this."""
        _make_plugin(tmp_path, plugin_id="ledmatrix-music",
                     widgets=[{"name": "deck", "script": "deck.js"}],
                     files={"deck.js": WIDGET_BODY})
        r = make_client().get(URL.format("music", "deck"))
        assert r.status_code == 200


class TestTheManifestIsTheAllowlist:
    def test_an_undeclared_file_in_widgets_is_not_served(self, tmp_path, make_client):
        """The whole point: shipping a file does not publish it."""
        _make_plugin(tmp_path, widgets=[],
                     files={"secrets.js": "const KEY='hunter2';"})
        r = make_client().get(URL.format("soccer-scoreboard", "secrets"))
        assert r.status_code == 404
        assert "hunter2" not in r.get_data(as_text=True)

    def test_a_manifest_with_no_widgets_key_serves_nothing(self, tmp_path, make_client):
        _make_plugin(tmp_path, files={"anything.js": WIDGET_BODY})
        r = make_client().get(URL.format("soccer-scoreboard", "anything"))
        assert r.status_code == 404

    def test_a_declared_widget_whose_file_is_missing_is_404(self, tmp_path, make_client):
        _make_plugin(tmp_path, widgets=[{"name": "ghost", "script": "ghost.js"}])
        r = make_client().get(URL.format("soccer-scoreboard", "ghost"))
        assert r.status_code == 404

    def test_a_malformed_manifest_serves_nothing(self, tmp_path, make_client):
        d = tmp_path / "broken"
        (d / "widgets").mkdir(parents=True)
        (d / "manifest.json").write_text("{not json", encoding="utf-8")
        (d / "widgets" / "w.js").write_text(WIDGET_BODY, encoding="utf-8")
        assert make_client().get(URL.format("broken", "w")).status_code == 404


class TestPathTraversal:
    @pytest.mark.parametrize("plugin_id", ["../etc", "..%2f..", "a/b", "a\\b", ""])
    def test_a_hostile_plugin_id_never_reaches_the_filesystem(
            self, plugin_id, tmp_path, make_client):
        r = make_client().get(URL.format(plugin_id, "custom-leagues"))
        assert r.status_code in (400, 404), r.status_code

    @pytest.mark.parametrize("widget", ["../manifest", "..%2fsecret", "a/b"])
    def test_a_hostile_widget_name_never_reaches_the_filesystem(
            self, widget, tmp_path, make_client):
        _make_plugin(tmp_path, widgets=[{"name": "custom-leagues"}],
                     files={"custom-leagues.js": WIDGET_BODY})
        r = make_client().get(URL.format("soccer-scoreboard", widget))
        assert r.status_code in (400, 404), r.status_code

    def test_a_manifest_cannot_escape_the_widgets_directory(self, tmp_path, make_client):
        """A hostile manifest is the traversal vector the URL allowlist can't
        cover: the script name comes from the plugin, not the request."""
        (tmp_path / "loot.js").write_text("const KEY='hunter2';", encoding="utf-8")
        _make_plugin(tmp_path,
                     widgets=[{"name": "evil", "script": "../../loot.js"}])
        r = make_client().get(URL.format("soccer-scoreboard", "evil"))
        assert r.status_code == 404
        assert "hunter2" not in r.get_data(as_text=True)


class TestDegradation:
    def test_an_unknown_plugin_is_404(self, tmp_path, make_client):
        assert make_client().get(URL.format("nope", "w")).status_code == 404

    def test_no_plugin_manager_is_503(self, make_client):
        r = make_client(plugin_manager=None).get(URL.format("any", "w"))
        assert r.status_code == 503


def _schema_with_widget(widget_name):
    return {
        "type": "object",
        "properties": {
            "enabled": {"type": "boolean", "default": False},
            "leagues": {"type": "string", "default": "eng.1",
                        "x-widget": widget_name},
        },
    }


@pytest.fixture
def config_form(tmp_path):
    """Render a plugin's config partial with a temp plugin on disk."""
    from web_interface.blueprints import pages_v3 as pv

    orig_pm = getattr(pv.pages_v3, "plugin_manager", None)
    orig_cm = getattr(pv.pages_v3, "config_manager", None)

    def _render(plugin_id="soccer-scoreboard", schema=None, widgets=None,
                files=None):
        d = _make_plugin(tmp_path, plugin_id, widgets=widgets, files=files)
        (d / "config_schema.json").write_text(
            json.dumps(schema or {"type": "object", "properties": {}}),
            encoding="utf-8")

        pm = MagicMock()
        pm.plugins_dir = str(tmp_path)
        pm.get_plugin_info.return_value = {"id": plugin_id, "name": plugin_id}
        pm.get_plugin.return_value = None
        pv.pages_v3.plugin_manager = pm

        cm = MagicMock()
        cm.load_config.return_value = {plugin_id: {"enabled": True}}
        pv.pages_v3.config_manager = cm

        base = PROJECT_ROOT / "web_interface"
        app = Flask(__name__,
                    template_folder=str(base / "templates"),
                    static_folder=str(base / "static"))
        app.config["TESTING"] = True
        app.register_blueprint(pv.pages_v3, url_prefix="")
        return app.test_client().get(f"/partials/plugin-config/{plugin_id}")

    try:
        yield _render
    finally:
        pv.pages_v3.plugin_manager = orig_pm
        pv.pages_v3.config_manager = orig_cm


class TestTheFormRequestsPluginWidgets:
    """Without this the feature is still dead: the route can serve a widget,
    but nothing ever asks for one. The server-side form only knows a hardcoded
    list of core widget names, so a plugin's own x-widget fell through to a
    plain text input and was never fetched."""

    def test_an_unknown_widget_name_triggers_a_plugin_load(self, config_form):
        r = config_form(schema=_schema_with_widget("custom-leagues"),
                        widgets=[{"name": "custom-leagues"}],
                        files={"custom-leagues.js": WIDGET_BODY})
        assert r.status_code == 200
        body = r.get_data(as_text=True)
        assert "ensureWidget" in body
        assert '"custom-leagues"' in body

    def test_the_text_input_remains_as_the_fallback(self, config_form):
        """A widget that fails to load must not cost the user their value."""
        r = config_form(schema=_schema_with_widget("custom-leagues"),
                        widgets=[{"name": "custom-leagues"}],
                        files={"custom-leagues.js": WIDGET_BODY})
        body = r.get_data(as_text=True)
        assert 'name="leagues"' in body
        assert 'value="eng.1"' in body

    def test_a_plain_string_field_asks_for_no_widget(self, config_form):
        r = config_form(schema={"type": "object", "properties": {
            "leagues": {"type": "string", "default": "eng.1"}}})
        assert "ensureWidget" not in r.get_data(as_text=True)

    def test_a_core_widget_does_not_take_the_plugin_path(self, config_form):
        r = config_form(schema=_schema_with_widget("font-selector"))
        body = r.get_data(as_text=True)
        assert "ensureWidget" not in body
        assert "LEDMatrixWidgets.get('font-selector')" in body
