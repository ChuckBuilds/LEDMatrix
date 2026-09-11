"""pages_v3 must refuse an id that is not a plain name, not truncate it.

Both partial loaders used to run the id through ``os.path.basename`` and carry
on with what came out, so "../weather" rendered the config form for "weather".
Nothing escaped the plugins directory -- the containment guards held -- but the
handler answered a request nobody made, and validating one string while the
filesystem sees another is the shape both live traversals in this branch had.

These tests are about that: a rejected id gets a 400, and a real one still
renders.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

BACKSLASH = chr(92)


@pytest.fixture
def pages(tmp_path):
    """pages_v3 with a plugins directory holding one real plugin."""
    from web_interface.blueprints import pages_v3 as module

    plugins_dir = tmp_path / "plugin-repos"
    (plugins_dir / "weather" / "web_ui").mkdir(parents=True)
    (plugins_dir / "weather" / "config_schema.json").write_text(
        '{"type": "object", "properties": {"enabled": {"type": "boolean"}}}',
        encoding="utf-8",
    )
    (plugins_dir / "weather" / "manifest.json").write_text(
        '{"name": "Weather", "version": "1.0.0"}', encoding="utf-8"
    )
    (plugins_dir / "weather" / "web_ui" / "panel.html").write_text(
        "<p>panel</p>", encoding="utf-8"
    )

    original_pm = getattr(module.pages_v3, "plugin_manager", None)
    original_cm = getattr(module.pages_v3, "config_manager", None)

    plugin_manager = MagicMock()
    plugin_manager.plugins_dir = plugins_dir
    plugin_manager.get_plugin_info.return_value = {"name": "Weather", "version": "1.0.0"}
    plugin_manager.get_plugin.return_value = None
    module.pages_v3.plugin_manager = plugin_manager
    module.pages_v3.config_manager = MagicMock(load_config=lambda: {})

    yield module, plugins_dir

    module.pages_v3.plugin_manager = original_pm
    module.pages_v3.config_manager = original_cm


@pytest.mark.parametrize("plugin_id", [
    "../weather", "..", ".", "", None, "a/b", "x" + BACKSLASH + "y",
])
def test_a_plugin_id_that_is_not_a_plain_name_is_a_400(pages, plugin_id):
    module, _ = pages
    body, status = module._load_plugin_config_partial(plugin_id)
    assert status == 400
    assert "Invalid plugin ID" in body


def test_a_traversing_id_no_longer_renders_the_truncated_one(pages):
    """"../weather" used to render "weather"'s form. It must not."""
    module, _ = pages
    body, status = module._load_plugin_config_partial("../weather")
    assert status == 400
    assert "Weather" not in body


@pytest.mark.parametrize("app_id", ["../demo", "..", "a/b", "", None])
def test_a_starlark_app_id_that_is_not_a_plain_name_is_a_400(pages, app_id):
    module, _ = pages
    body, status = module._load_starlark_config_partial(app_id)
    assert status == 400
    assert "Invalid app ID" in body


class TestServePluginWebUi:
    """GET /plugin-ui/<plugin_id>/web-ui/<path:filename>"""

    @pytest.fixture
    def client(self, pages):
        from flask import Flask

        module, _ = pages
        base = Path(module.__file__).resolve().parent.parent
        app = Flask(
            __name__,
            template_folder=str(base / "templates"),
            static_folder=str(base / "static"),
        )
        app.config["TESTING"] = True
        app.register_blueprint(module.pages_v3, url_prefix="")
        return app.test_client()

    def test_a_real_fragment_is_still_wrapped_and_served(self, client):
        response = client.get("/plugin-ui/weather/web-ui/panel.html")
        assert response.status_code == 200
        body = response.get_data(as_text=True)
        assert "<p>panel</p>" in body
        assert 'window.PLUGIN_ID = "weather"' in body

    @pytest.mark.parametrize("url", [
        "/plugin-ui/../web-ui/panel.html",
        "/plugin-ui/%2e%2e/web-ui/panel.html",
        "/plugin-ui/weather/web-ui/../../manifest.json",
        "/plugin-ui/weather/web-ui/..%2f..%2fmanifest.json",
    ])
    def test_traversal_attempts_are_refused(self, client, url):
        response = client.get(url)
        assert response.status_code in (400, 403, 404)
        assert "version" not in response.get_data(as_text=True)

    def test_a_non_html_filename_is_still_refused(self, client):
        # The allowlist predates this change and must survive it.
        response = client.get("/plugin-ui/weather/web-ui/manifest.json")
        assert response.status_code == 400
