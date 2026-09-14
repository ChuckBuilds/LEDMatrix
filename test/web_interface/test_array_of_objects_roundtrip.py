"""An array of objects with no ``x-widget`` must survive a save of its own form.

Geochron's ``cities`` is a list of ``{name, lat, lon, timezone}`` objects and
names no widget. The template sent that to the comma-separated text input,
which renders each city as a Python dict repr joined by commas. Saving the
untouched form posted those back as a list of strings, the schema rejected
them, and *every* save of the plugin failed with 400 "Configuration
validation failed" -- no setting on the page could be changed.

So the test renders the real form, submits what a browser would, and requires
the save to succeed with the cities intact.
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

CITIES = [
    {"name": "New York", "lat": 40.71, "lon": -74.01, "timezone": "America/New_York"},
    {"name": "Tokyo", "lat": 35.68, "lon": 139.65, "timezone": "Asia/Tokyo"},
]

SCHEMA = {
    "type": "object",
    "properties": {
        "enabled": {"type": "boolean", "default": False},
        "timezone": {"type": ["string", "null"], "default": None},
        "show_cities": {"type": "boolean", "default": True},
        "cities": {
            "type": "array",
            "maxItems": 8,
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "maxLength": 20},
                    "lat": {"type": "number", "minimum": -90, "maximum": 90},
                    "lon": {"type": "number", "minimum": -180, "maximum": 180},
                    "timezone": {"type": "string"},
                },
                "required": ["name", "lat", "lon"],
                "additionalProperties": False,
            },
            "default": CITIES,
        },
    },
    "required": ["enabled"],
    "additionalProperties": False,
}

STORED = {"enabled": True, "timezone": None, "show_cities": True,
          "cities": CITIES}


class _FormFields(HTMLParser):
    """Collect what a browser would submit from the rendered form."""

    def __init__(self):
        super().__init__()
        self.pairs = []
        self._select = None
        self._select_first = None
        self._select_chosen = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        name = a.get("name")
        if tag == "input" and name and "disabled" not in a:
            if a.get("type") in ("checkbox", "radio") and "checked" not in a:
                return
            if a.get("type") in ("button", "submit", "file"):
                return
            self.pairs.append((name, a.get("value", "")))
        elif tag == "select" and name:
            self._select, self._select_first, self._select_chosen = name, None, None
        elif tag == "option" and self._select:
            if self._select_first is None:
                self._select_first = a.get("value", "")
            if "selected" in a:
                self._select_chosen = a.get("value", "")

    def handle_endtag(self, tag):
        if tag == "select" and self._select:
            chosen = self._select_chosen if self._select_chosen is not None else self._select_first
            self.pairs.append((self._select, chosen or ""))
            self._select = None


def _render_form():
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


def test_the_form_does_not_flatten_objects_into_text():
    html = _render_form()
    assert "{&#39;name&#39;" not in html and "{'name'" not in html, (
        "cities rendered as a dict repr in a comma-separated text box")


def test_saving_the_untouched_form_succeeds(post):
    parser = _FormFields()
    parser.feed(_render_form())
    resp, cfg = post(parser.pairs)
    assert resp.status_code == 200, resp.get_json()
    assert cfg["cities"] == CITIES


def test_an_item_without_its_optional_object_does_not_gain_an_empty_one(post):
    """News: a custom feed with no logo. The unchecked-checkbox pass walked
    into ``logo`` to look for booleans and left ``logo: {}`` behind, which
    fails the logo's ``required: [id, path]`` -- 400 on every save."""
    from web_interface.blueprints.api_v3 import _set_missing_booleans_to_false

    props = {"feeds": {"type": "object", "properties": {"custom_feeds": {
        "type": "array",
        "items": {"type": "object", "properties": {
            "name": {"type": "string"},
            "enabled": {"type": "boolean"},
            "logo": {"type": "object", "required": ["id", "path"],
                     "properties": {"id": {"type": "string"},
                                    "path": {"type": "string"}}},
            "extra": {"type": "object",
                      "properties": {"flag": {"type": "boolean"}}},
        }},
    }}}}
    cfg = {"feeds": {"custom_feeds": [{"name": "Verge", "enabled": True}]}}
    _set_missing_booleans_to_false(
        cfg, props, {"feeds.custom_feeds.0.name"}, sections={"feeds"})

    item = cfg["feeds"]["custom_feeds"][0]
    assert "logo" not in item
    # A nested object that really does hold an unchecked box still gets it.
    assert item["extra"] == {"flag": False}
    assert item["enabled"] is False
