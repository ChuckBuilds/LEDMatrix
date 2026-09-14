"""Stored values the current schema no longer describes must not break the form.

Two cases from the news plugin:

* ``global.dynamic_duration`` used to be a boolean and is now an object. An
  install that has not saved since still holds ``true``; rendering the nested
  section did ``key in true`` and the whole config page failed to load.
* A custom feed logo stored with a ``path`` but no ``id``. The template always
  emitted an empty ``logo.id`` input, which the save route parsed to ``None``
  and the schema rejected (``Expected type string``) -- 400 on every save.
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
        "global": {"type": "object", "properties": {
            "dynamic_duration": {
                "type": "object",
                "properties": {
                    "enabled": {"type": "boolean", "default": True},
                    "min_duration_seconds": {"type": "integer", "default": 30,
                                             "minimum": 10, "maximum": 300},
                },
                "additionalProperties": False,
            },
        }, "additionalProperties": False},
        "feeds": {"type": "object", "properties": {
            "custom_feeds": {
                "type": "array",
                "x-widget": "custom-feeds",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "url": {"type": "string"},
                        "enabled": {"type": "boolean", "default": True},
                        "logo": {"type": "object", "required": ["path"],
                                 "properties": {"id": {"type": "string"},
                                                "path": {"type": "string"}}},
                    },
                    "required": ["name", "url"],
                    "additionalProperties": False,
                },
            },
        }, "additionalProperties": False},
    },
    "required": ["enabled"],
    "additionalProperties": False,
}

LOGO_PATH = "assets/plugins/news/uploads/verge.png"
STORED = {
    "enabled": True,
    "global": {"dynamic_duration": True},
    "feeds": {"custom_feeds": [{"name": "Verge",
                                "url": "https://www.theverge.com/rss/index.xml",
                                "enabled": True,
                                "logo": {"path": LOGO_PATH}}]},
}


class _FormFields(HTMLParser):
    """Collect what a browser would submit from the rendered form."""

    def __init__(self):
        super().__init__()
        self.pairs = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag != "input" or not a.get("name") or "disabled" in a:
            return
        if a.get("type") in ("checkbox", "radio") and "checked" not in a:
            return
        if a.get("type") in ("button", "submit", "file"):
            return
        self.pairs.append((a["name"], a.get("value", "")))


def _render():
    from jinja2 import Environment, FileSystemLoader, select_autoescape

    env = Environment(
        loader=FileSystemLoader(str(PROJECT_ROOT / "web_interface" / "templates")),
        autoescape=select_autoescape(["html"]),
    )
    plugin = {"id": "demo", "name": "Demo", "description": "", "enabled": True,
              "author": "me", "version": "1.0.0"}
    return env.get_template("v3/partials/plugin_config.html").render(
        plugin=plugin, schema=SCHEMA, config=json.loads(json.dumps(STORED)))


@pytest.fixture
def post(tmp_path):
    from src.plugin_system.schema_manager import SchemaManager
    from web_interface.blueprints import api_v3 as a

    originals = {k: getattr(a.api_v3, k, None)
                 for k in ("config_manager", "schema_manager", "plugin_manager")}

    pdir = tmp_path / "plugins" / "demo"
    pdir.mkdir(parents=True)
    (pdir / "config_schema.json").write_text(json.dumps(SCHEMA), encoding="utf-8")

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

    a.api_v3.config_manager = cm
    a.api_v3.schema_manager = SchemaManager(plugins_dir=tmp_path / "plugins",
                                            project_root=tmp_path)
    pm = MagicMock()
    pm.plugins = {}
    pm.get_plugin.return_value = None
    a.api_v3.plugin_manager = pm

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(a.api_v3, url_prefix="/api/v3")
    client = app.test_client()

    def _post(pairs):
        resp = client.post("/api/v3/plugins/config?plugin_id=demo",
                           data=MultiDict(pairs))
        return resp, store.get("demo", {})

    try:
        yield _post
    finally:
        for k, v in originals.items():
            setattr(a.api_v3, k, v)


def test_a_scalar_stored_under_an_object_field_still_renders():
    html = _render()
    assert 'name="global.dynamic_duration.min_duration_seconds"' in html


def test_a_logo_without_an_id_posts_no_id_field():
    parser = _FormFields()
    parser.feed(_render())
    names = [name for name, _ in parser.pairs]
    assert "feeds.custom_feeds.0.logo.path" in names
    assert "feeds.custom_feeds.0.logo.id" not in names


def test_saving_that_form_succeeds_and_upgrades_the_legacy_value(post):
    parser = _FormFields()
    parser.feed(_render())
    resp, cfg = post(parser.pairs)
    assert resp.status_code == 200, resp.get_json()
    assert isinstance(cfg["global"]["dynamic_duration"], dict)
    # The legacy `true` was the on switch; upgrading must not turn it off.
    assert cfg["global"]["dynamic_duration"]["enabled"] is True
    assert cfg["global"]["dynamic_duration"]["min_duration_seconds"] == 30
    assert cfg["feeds"]["custom_feeds"][0]["logo"] == {"path": LOGO_PATH}
