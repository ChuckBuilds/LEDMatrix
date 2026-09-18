"""The server-rendered plugin form must show schema defaults for unsaved keys.

A plugin update that adds a boolean option with ``"default": true`` (geochron
1.2.0's ``show_date`` / ``show_date_line``) leaves every existing install with
a saved config that lacks the key. The partial rendered it from the raw config,
so the box came up unchecked -- and the save route treats a drawn but unposted
checkbox as false, so the first save turned the option off for good.

These drive the real partial route (pages_v3) and post what a browser would
submit from its HTML back through the real save route (api_v3).
"""

import json
import sys
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from flask import Flask
from werkzeug.datastructures import MultiDict

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

SCHEMA = {
    "type": "object",
    "properties": {
        "enabled": {"type": "boolean", "default": True},
        "show_date": {"type": "boolean", "default": True},
        "show_date_line": {"type": "boolean", "default": True},
        "show_seconds": {"type": "boolean", "default": False},
        "style": {"type": "string", "enum": ["flat", "globe", "night"],
                  "default": "globe"},
        "brightness": {"type": "integer", "default": 70,
                       "minimum": 0, "maximum": 100},
        "label": {"type": "string", "default": "UTC"},
        # An object with its own default: extract_schema_defaults stops here,
        # so the route merge leaves the children missing.
        "overlay": {"type": "object", "default": {}, "properties": {
            "show_sun": {"type": "boolean", "default": True},
            "mode": {"type": "string", "enum": ["dot", "ring"],
                     "default": "ring"},
        }, "additionalProperties": False},
        # An object without one: the route merge fills the children in.
        "grid": {"type": "object", "properties": {
            "show_lines": {"type": "boolean", "default": True},
        }, "additionalProperties": False},
        "api_key": {"type": "string", "x-secret": True,
                    "default": "not-a-real-secret"},
    },
    "required": ["enabled"],
    "additionalProperties": False,
}

# Saved before any of the options above existed.
STORED = {"enabled": True, "show_seconds": True}


class _FormFields(HTMLParser):
    """Collect what a browser would submit from the rendered form."""

    def __init__(self):
        super().__init__()
        self.pairs = []
        self._select = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "select":
            self._select = a.get("name")
            return
        if tag == "option" and self._select and "selected" in a:
            self.pairs.append((self._select, a.get("value", "")))
            return
        if tag != "input" or not a.get("name") or "disabled" in a:
            return
        if a.get("type") in ("checkbox", "radio") and "checked" not in a:
            return
        if a.get("type") in ("button", "submit", "file"):
            return
        self.pairs.append((a["name"], a.get("value", "")))

    def handle_endtag(self, tag):
        if tag == "select":
            self._select = None


@pytest.fixture
def app_client(tmp_path):
    from src.plugin_system.schema_manager import SchemaManager
    from web_interface.blueprints import api_v3 as api
    from web_interface.blueprints import pages_v3 as pages

    plugins_dir = tmp_path / "plugin-repos"
    pdir = plugins_dir / "demo"
    pdir.mkdir(parents=True)
    (pdir / "config_schema.json").write_text(json.dumps(SCHEMA), encoding="utf-8")
    (pdir / "manifest.json").write_text(
        json.dumps({"id": "demo", "name": "Demo", "version": "1.0.0"}),
        encoding="utf-8")

    store = {"demo": json.loads(json.dumps(STORED))}
    cm = MagicMock()
    cm.load_config.side_effect = lambda: json.loads(json.dumps(store))
    cm.get_raw_file_content.return_value = {}
    cm.get_config_path.return_value = str(tmp_path / "config.json")

    def _save(cfg, **_kw):
        store.clear()
        store.update(cfg)
        return type("R", (), {"status": type("S", (), {"value": "success"})(),
                              "message": None})()

    cm.save_config_atomic.side_effect = _save

    pm = MagicMock()
    pm.plugins = {}
    pm.plugins_dir = plugins_dir
    pm.get_plugin.return_value = None
    pm.get_plugin_info.return_value = {"name": "Demo", "version": "1.0.0"}
    sm = SchemaManager(plugins_dir=plugins_dir, project_root=tmp_path)

    names = ("config_manager", "schema_manager", "plugin_manager")
    originals = {(bp, k): getattr(bp, k, None)
                 for bp in (api.api_v3, pages.pages_v3) for k in names}
    for bp in (api.api_v3, pages.pages_v3):
        bp.config_manager = cm
        bp.schema_manager = sm
        bp.plugin_manager = pm

    base = Path(pages.__file__).resolve().parent.parent
    app = Flask(__name__, template_folder=str(base / "templates"),
                static_folder=str(base / "static"))
    app.config["TESTING"] = True
    app.register_blueprint(pages.pages_v3, url_prefix="/v3")
    app.register_blueprint(api.api_v3, url_prefix="/api/v3")

    try:
        yield app.test_client(), store
    finally:
        for (bp, k), v in originals.items():
            setattr(bp, k, v)


def _render(client):
    resp = client.get("/v3/partials/plugin-config/demo")
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


def _fields(html):
    parser = _FormFields()
    parser.feed(html)
    return dict(parser.pairs)


def test_missing_booleans_render_their_schema_default(app_client):
    client, _ = app_client
    fields = _fields(_render(client))
    # Checked, so a browser posts them.
    assert fields.get("show_date") == "true"
    assert fields.get("show_date_line") == "true"
    assert fields.get("grid.show_lines") == "true"
    assert fields.get("overlay.show_sun") == "true"
    # A saved value still wins over the default.
    assert fields.get("show_seconds") == "true"


def test_missing_non_boolean_fields_render_their_schema_default(app_client):
    client, _ = app_client
    fields = _fields(_render(client))
    assert fields.get("style") == "globe"          # not the first option
    assert fields.get("overlay.mode") == "ring"
    assert fields.get("brightness") == "70"
    assert fields.get("label") == "UTC"


def test_a_secret_default_is_still_masked(app_client):
    client, _ = app_client
    assert "not-a-real-secret" not in _render(client)


def test_saving_the_rendered_form_keeps_default_true_booleans_on(app_client):
    client, store = app_client
    pairs = list(_fields(_render(client)).items())
    resp = client.post("/api/v3/plugins/config?plugin_id=demo",
                       data=MultiDict(pairs))
    assert resp.status_code == 200, resp.get_json()

    saved = store["demo"]
    assert saved["show_date"] is True
    assert saved["show_date_line"] is True
    assert saved["grid"]["show_lines"] is True
    assert saved["overlay"]["show_sun"] is True
    assert saved["overlay"]["mode"] == "ring"
    assert saved["style"] == "globe"
    assert saved["show_seconds"] is True
