"""``"x-display": "hidden"`` keeps a setting declared but off the form.

Plugins keep deprecated keys in their schema so configs that still carry them
keep validating (weather's ``api_key`` and ``radar_zoom``, olympics'
notification keys), and some keys are internal (countdown's auto-generated
row ``id``). Drawn as live controls they invite edits that do nothing, or
worse, change an internal id.

The contract pinned here:

* nothing hidden is drawn -- top level, nested, in Advanced Settings, or as an
  array-table column -- and visible siblings still are;
* an object of nothing but hidden children draws no empty section;
* a hidden top-level key is not reported in ``__rendered_section`` and does
  not count toward the Advanced Settings total;
* saving the rendered form never changes a hidden value: plain and nested ones
  are simply not posted (the save deep-merges over the stored section), and a
  hidden array-row property is carried through JSON-encoded, because a posted
  row replaces the stored item wholesale;
* an unchecked *visible* checkbox beside them is still cleared;
* a JSON API save is unaffected.
"""

import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from flask import Flask
from werkzeug.datastructures import MultiDict

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

HIDDEN = {"x-display": "hidden"}

SCHEMA = {
    "type": "object",
    "properties": {
        "enabled": {"type": "boolean", "default": True},
        "city": {"type": "string", "title": "City", "default": "Dallas"},
        "legacy_key": {"type": "string", "title": "Legacy Key", **HIDDEN},
        "legacy_flag": {"type": "boolean", "title": "Legacy Flag", **HIDDEN},
        "timeout": {"type": "integer", "title": "Timeout", "default": 10,
                    "x-advanced": True},
        "old_zoom": {"type": "integer", "title": "Old Zoom", "default": 6,
                     "minimum": 4, "maximum": 8, "x-advanced": True, **HIDDEN},
        "display": {
            "type": "object",
            "title": "Display",
            "properties": {
                "show_clock": {"type": "boolean", "title": "Show Clock",
                               "default": True},
                "old_flag": {"type": "boolean", "title": "Old Flag", **HIDDEN},
                "old_mode": {"type": "string", "title": "Old Mode", **HIDDEN},
            },
        },
        # Every child hidden: no empty section.
        "internal": {
            "type": "object",
            "title": "Internal Stuff",
            "properties": {
                "token_seen": {"type": "boolean", **HIDDEN},
                "revision": {"type": "integer", **HIDDEN},
            },
        },
        "entries": {
            "type": "array",
            "title": "Entries",
            "x-widget": "array-table",
            # x-columns naming a hidden property must not draw it either.
            "x-columns": ["enabled", "name", "id"],
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "title": "Row ID", **HIDDEN},
                    "enabled": {"type": "boolean", "default": True},
                    "name": {"type": "string"},
                    "pinned": {"type": "boolean", "title": "Pinned", **HIDDEN},
                    "weight": {"type": "number", "title": "Weight", **HIDDEN},
                    "layout": {
                        "type": "object",
                        "properties": {
                            "x": {"type": "integer", "default": 0},
                            "legacy_x": {"type": "integer", **HIDDEN},
                        },
                    },
                },
                "required": ["name"],
            },
        },
    },
}

STORED = {
    "enabled": True,
    "city": "Dallas",
    "legacy_key": "keep-me",
    "legacy_flag": True,
    "timeout": 10,
    "old_zoom": 5,
    "display": {"show_clock": True, "old_flag": True, "old_mode": "classic"},
    "internal": {"token_seen": True, "revision": 7},
    "entries": [
        # An id that looks like a number must come back a string.
        {"id": "1", "enabled": True, "name": "First", "pinned": True,
         "weight": 2.5, "layout": {"x": 3, "legacy_x": 9}},
        {"id": "cd_ab12cd34ef56", "enabled": False, "name": "Second",
         "pinned": False},
    ],
}

HIDDEN_LABELS = ("Legacy Key", "Legacy Flag", "Old Zoom", "Old Flag",
                 "Old Mode", "Internal Stuff", "Row ID", "Pinned", "Weight")


class _FormFields(HTMLParser):
    """Collect what a browser would submit from the rendered form."""

    def __init__(self):
        super().__init__()
        self.pairs = []
        self._select = None
        self._first = None
        self._chosen = None

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
            self._select, self._first, self._chosen = name, None, None
        elif tag == "option" and self._select:
            if self._first is None:
                self._first = a.get("value", "")
            if "selected" in a:
                self._chosen = a.get("value", "")

    def handle_endtag(self, tag):
        if tag == "select" and self._select:
            chosen = self._chosen if self._chosen is not None else self._first
            self.pairs.append((self._select, chosen or ""))
            self._select = None


def _render(schema, config, plugin_id="demo"):
    from jinja2 import Environment, FileSystemLoader, select_autoescape

    env = Environment(
        loader=FileSystemLoader(str(PROJECT_ROOT / "web_interface" / "templates")),
        autoescape=select_autoescape(["html"]),
    )
    plugin = {"id": plugin_id, "name": "Demo", "description": "",
              "enabled": True, "author": "me", "version": "1.0.0"}
    return env.get_template("v3/partials/plugin_config.html").render(
        plugin=plugin, schema=schema, config=json.loads(json.dumps(config)))


def _form_pairs(html):
    parser = _FormFields()
    parser.feed(html)
    return parser.pairs


def _names(html):
    return {name for name, _ in _form_pairs(html)}


def _visible_names(html):
    """Names of controls a user can see (anything but type=hidden)."""
    names = set()
    for tag in re.findall(r"<(?:input|select|textarea)\b[^>]*>", html):
        m = re.search(r'\bname="([^"]+)"', tag)
        if m and 'type="hidden"' not in tag:
            names.add(m.group(1))
    return names


def _make_post(tmp_path, schema, stored, plugin_id="demo"):
    from src.plugin_system.schema_manager import SchemaManager
    from web_interface.blueprints import api_v3 as a

    originals = {k: getattr(a.api_v3, k, None)
                 for k in ("config_manager", "schema_manager", "plugin_manager")}

    pdir = tmp_path / "plugins" / plugin_id
    pdir.mkdir(parents=True)
    (pdir / "config_schema.json").write_text(json.dumps(schema), encoding="utf-8")

    store = {plugin_id: json.loads(json.dumps(stored))}
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
        resp = client.post(f"/api/v3/plugins/config?plugin_id={plugin_id}",
                           data=MultiDict(pairs))
        return resp, store.get(plugin_id, {})

    def _post_json(config):
        resp = client.post("/api/v3/plugins/config",
                           json={"plugin_id": plugin_id, "config": config})
        return resp, store.get(plugin_id, {})

    def _restore():
        for k, v in originals.items():
            setattr(a.api_v3, k, v)

    return _post, _post_json, _restore


@pytest.fixture
def post(tmp_path):
    _post, _post_json, restore = _make_post(tmp_path, SCHEMA, STORED)
    _post.json = _post_json
    try:
        yield _post
    finally:
        restore()


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

class TestRendering:
    html = None

    @classmethod
    def setup_class(cls):
        cls.html = _render(SCHEMA, STORED)

    def test_no_hidden_field_gets_a_visible_control(self):
        visible = _visible_names(self.html)
        for name in ("legacy_key", "legacy_flag", "old_zoom",
                     "display.old_flag", "display.old_mode",
                     "internal.token_seen", "internal.revision"):
            assert name not in visible, name
        assert not [n for n in visible if re.fullmatch(
            r"entries\.\d+\.(id|pinned|weight|layout\.legacy_x)", n)], visible

    def test_plain_and_nested_hidden_fields_are_not_posted_at_all(self):
        names = _names(self.html)
        for name in ("legacy_key", "legacy_flag", "old_zoom",
                     "display.old_flag", "display.old_mode",
                     "internal.token_seen", "internal.revision"):
            assert name not in names, name

    def test_no_hidden_label_is_drawn(self):
        for label in HIDDEN_LABELS:
            assert f">{label}<" not in self.html, label
            assert f"{label}\n" not in self.html, label

    def test_visible_siblings_still_render(self):
        visible = _visible_names(self.html)
        assert {"city", "timeout", "display.show_clock",
                "entries.0.name", "entries.0.enabled"} <= visible

    def test_rendered_sections_exclude_hidden_top_level_keys(self):
        sections = re.findall(r'name="__rendered_section" value="([^"]+)"',
                              self.html)
        assert set(sections) == {"city", "timeout", "display", "entries"}

    def test_an_object_of_only_hidden_children_draws_no_section(self):
        assert "Internal Stuff" not in self.html
        assert "section-internal" not in self.html

    def test_the_advanced_count_excludes_hidden_fields(self):
        assert re.findall(r"Advanced Settings \((\d+)\)", self.html) == ["1"]

    def test_advanced_settings_disappear_when_all_advanced_are_hidden(self):
        schema = json.loads(json.dumps(SCHEMA))
        del schema["properties"]["timeout"]
        html = _render(schema, STORED)
        assert "Advanced Settings" not in html

    def test_hidden_array_properties_are_not_columns(self):
        headers = re.findall(r"<th[^>]*>([^<]+)</th>", self.html)
        assert "Row ID" not in headers and "Pinned" not in headers
        cols = re.search(r"data-display-columns='([^']*)'", self.html)
        assert json.loads(cols.group(1)) == ["enabled", "name"]

    def test_hidden_array_properties_stay_out_of_the_row_editor(self):
        """The editor draws a field for every data-nested-prop and every key
        of the advanced cell's data-prop-schema."""
        assert 'data-nested-prop="pinned"' not in self.html
        assert 'data-nested-prop="layout.legacy_x"' not in self.html
        assert 'data-nested-prop="layout.x"' in self.html
        for m in re.finditer(r"class=\"array-table-advanced-data\"\s+"
                             r"data-prop-schema='([^']*)'", self.html):
            import html as _h
            adv = json.loads(_h.unescape(m.group(1)))
            assert "pinned" not in adv and "weight" not in adv and "id" not in adv

    def test_stored_hidden_row_values_are_carried_json_encoded(self):
        pairs = dict(_form_pairs(self.html))
        assert pairs["entries.0.id"] == '"1"'
        assert pairs["entries.0.pinned"] == "true"
        assert pairs["entries.0.weight"] == "2.5"
        assert pairs["entries.0.layout.legacy_x"] == "9"
        assert pairs["entries.1.id"] == '"cd_ab12cd34ef56"'
        # Absent stored values are not invented.
        assert "entries.1.weight" not in pairs
        assert "entries.1.layout.legacy_x" not in pairs


def test_a_schema_without_the_flag_renders_as_before():
    """Regression guard: the flag's absence changes nothing."""
    schema = json.loads(json.dumps(SCHEMA))

    def strip(node):
        if isinstance(node, dict):
            node.pop("x-display", None)
            for v in node.values():
                strip(v)
    strip(schema)
    html = _render(schema, STORED)
    visible = _visible_names(html)
    assert {"legacy_key", "legacy_flag", "old_zoom", "display.old_flag",
            "internal.token_seen", "entries.0.id"} <= visible
    # Not a column, so it lives in the row editor's advanced cell as before.
    assert 'data-nested-prop="pinned"' in html
    assert re.findall(r"Advanced Settings \((\d+)\)", html) == ["2"]


# --------------------------------------------------------------------------
# Saving
# --------------------------------------------------------------------------

class TestSavingTheRenderedForm:
    def test_the_untouched_form_changes_no_hidden_value(self, post):
        resp, cfg = post(_form_pairs(_render(SCHEMA, STORED)))
        assert resp.status_code == 200, resp.get_json()
        assert cfg["legacy_key"] == "keep-me"
        assert cfg["legacy_flag"] is True
        assert cfg["old_zoom"] == 5
        assert cfg["display"]["old_flag"] is True
        assert cfg["display"]["old_mode"] == "classic"
        assert cfg["internal"] == {"token_seen": True, "revision": 7}
        first, second = cfg["entries"]
        assert first["id"] == "1" and isinstance(first["id"], str)
        assert first["pinned"] is True
        assert first["weight"] == 2.5
        assert first["layout"]["legacy_x"] == 9
        assert second["id"] == "cd_ab12cd34ef56"
        assert second["pinned"] is False

    def test_visible_edits_apply_and_an_unchecked_visible_box_is_cleared(self, post):
        pairs = [(k, v) for k, v in _form_pairs(_render(SCHEMA, STORED))
                 if k != "display.show_clock"]
        pairs = [(k, "Austin" if k == "city" else v) for k, v in pairs]
        resp, cfg = post(pairs)
        assert resp.status_code == 200, resp.get_json()
        assert cfg["city"] == "Austin"
        # The existing unchecked-checkbox behaviour, in the same section as
        # hidden booleans that must not be touched.
        assert cfg["display"]["show_clock"] is False
        assert cfg["display"]["old_flag"] is True
        assert cfg["legacy_flag"] is True
        assert cfg["entries"][0]["pinned"] is True

    def test_a_deleted_row_takes_its_hidden_values_with_it(self, post):
        """Row 0 removed; the browser renumbers row 1 to index 0."""
        pairs = []
        for k, v in _form_pairs(_render(SCHEMA, STORED)):
            m = re.match(r"entries\.(\d+)\.(.*)", k)
            if m:
                if m.group(1) == "0":
                    continue
                k = f"entries.0.{m.group(2)}"
            pairs.append((k, v))
        resp, cfg = post(pairs)
        assert resp.status_code == 200, resp.get_json()
        assert [e["id"] for e in cfg["entries"]] == ["cd_ab12cd34ef56"]

    def test_a_new_row_gets_no_invented_hidden_values(self, post):
        pairs = _form_pairs(_render(SCHEMA, STORED)) + [
            ("entries.2.enabled", "true"), ("entries.2.name", "Third")]
        resp, cfg = post(pairs)
        assert resp.status_code == 200, resp.get_json()
        assert cfg["entries"][2]["name"] == "Third"
        assert "id" not in cfg["entries"][2]
        assert "pinned" not in cfg["entries"][2]

    def test_a_json_api_save_still_writes_hidden_fields(self, post):
        """Hidden is a form concern: API callers can still set these."""
        config = json.loads(json.dumps(STORED))
        config["legacy_flag"] = False
        config["display"]["old_mode"] = "modern"
        config["entries"][0]["id"] = "renamed"
        resp, cfg = post.json(config)
        assert resp.status_code == 200, resp.get_json()
        assert cfg["legacy_flag"] is False
        assert cfg["display"]["old_mode"] == "modern"
        assert cfg["entries"][0]["id"] == "renamed"


class TestMissingBooleans:
    """_set_missing_booleans_to_false, directly, at every depth."""

    def test_hidden_booleans_are_out_of_scope_everywhere(self):
        from web_interface.blueprints.api_v3 import _set_missing_booleans_to_false

        cfg = json.loads(json.dumps(STORED))
        form_keys = {"city", "entries.0.name", "entries.1.name"}
        _set_missing_booleans_to_false(
            cfg, SCHEMA["properties"], form_keys,
            sections={"city", "display", "entries", "internal", "legacy_flag"})

        assert cfg["legacy_flag"] is True
        assert cfg["display"]["old_flag"] is True
        assert cfg["internal"]["token_seen"] is True
        assert cfg["entries"][0]["pinned"] is True
        # Visible booleans in the same places are still cleared.
        assert cfg["display"]["show_clock"] is False
        assert cfg["entries"][0]["enabled"] is False

    def test_hidden_array_item_property_lookup(self):
        from web_interface.blueprints.api_v3 import _hidden_array_item_property

        assert _hidden_array_item_property(SCHEMA, "entries.0.id")
        assert _hidden_array_item_property(SCHEMA, "entries.12.layout.legacy_x")
        assert _hidden_array_item_property(SCHEMA, "entries.0.name") is None
        assert _hidden_array_item_property(SCHEMA, "entries.0.layout.x") is None
        # Only array rows qualify: plain hidden keys are never posted.
        assert _hidden_array_item_property(SCHEMA, "legacy_key") is None
        assert _hidden_array_item_property(SCHEMA, "display.old_flag") is None


# --------------------------------------------------------------------------
# Real plugin schemas (snippets copied from ledmatrix-plugins)
# --------------------------------------------------------------------------

# plugins/countdown/config_schema.json, trimmed to the countdowns table.
COUNTDOWN_SCHEMA = {
    "type": "object",
    "properties": {
        "enabled": {"type": "boolean", "default": True},
        "countdowns": {
            "type": "array",
            "x-widget": "array-table",
            "x-columns": ["enabled", "name", "target_date", "target_time", "mode"],
            "default": [],
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "title": "ID",
                           "description": "Unique identifier (auto-generated)",
                           "x-display": "hidden"},
                    "enabled": {"type": "boolean", "title": "Enabled", "default": True},
                    "name": {"type": "string", "title": "Name",
                             "minLength": 1, "maxLength": 30},
                    "target_date": {"type": "string", "title": "Target Date",
                                    "x-widget": "date-picker",
                                    "pattern": "^\\d{4}-\\d{2}-\\d{2}$"},
                    "target_time": {"type": "string", "title": "Target Time",
                                    "x-widget": "time-picker", "default": "00:00"},
                    "mode": {"type": "string", "title": "Mode",
                             "enum": ["until", "since"], "default": "until"},
                    "display_order": {"x-advanced": True, "type": "integer",
                                      "title": "Order", "default": 0, "minimum": 0},
                },
                "required": ["name", "target_date"],
            },
        },
        "show_expired": {"x-advanced": True, "type": "boolean",
                         "title": "Show Expired", "default": False},
    },
    "required": ["enabled"],
}

COUNTDOWN_STORED = {
    "enabled": True,
    "show_expired": True,
    "countdowns": [
        {"id": "cd_0123456789ab", "enabled": True, "name": "Vacation",
         "target_date": "2026-12-20", "target_time": "00:00", "mode": "until",
         "display_order": 0},
        {"id": "42", "enabled": True, "name": "Anniversary",
         "target_date": "2020-06-01", "target_time": "00:00", "mode": "since",
         "display_order": 1},
    ],
}

# plugins/ledmatrix-weather/config_schema.json, trimmed.
WEATHER_SCHEMA = {
    "type": "object",
    "properties": {
        "enabled": {"type": "boolean", "default": False},
        "location_city": {"type": "string", "default": "Dallas", "title": "City"},
        "update_interval": {"x-advanced": True, "type": "integer", "default": 1800,
                            "minimum": 300},
        "api_key": {"x-advanced": True, "x-display": "hidden", "type": "string",
                    "title": "API Key (deprecated)"},
        "radar_range_miles": {"type": "integer", "default": 75, "minimum": 10,
                              "maximum": 500, "title": "Radar Range (miles)"},
        "radar_zoom": {"x-advanced": True, "x-display": "hidden", "type": "integer",
                       "default": 6, "minimum": 4, "maximum": 8,
                       "title": "Radar Zoom Level (deprecated)"},
        "radar_show_nowcast": {"type": "boolean", "default": True,
                               "title": "Show Forecast Frames"},
    },
}

WEATHER_STORED = {"enabled": True, "location_city": "Tampa", "update_interval": 900,
                  "api_key": "legacy-owm-key", "radar_range_miles": 75,
                  "radar_zoom": 7, "radar_show_nowcast": True}


def test_countdown_ids_survive_a_form_save(tmp_path):
    html = _render(COUNTDOWN_SCHEMA, COUNTDOWN_STORED, "countdown")
    assert ">ID<" not in html
    assert "countdowns.0.id" not in _visible_names(html)

    _post, _json, restore = _make_post(tmp_path, COUNTDOWN_SCHEMA,
                                       COUNTDOWN_STORED, "countdown")
    try:
        # Edit a visible cell too.
        pairs = [(k, "Beach" if k == "countdowns.0.name" else v)
                 for k, v in _form_pairs(html)]
        resp, cfg = _post(pairs)
    finally:
        restore()
    assert resp.status_code == 200, resp.get_json()
    assert [c["id"] for c in cfg["countdowns"]] == ["cd_0123456789ab", "42"]
    assert cfg["countdowns"][0]["name"] == "Beach"


def test_weather_deprecated_keys_are_hidden_and_kept(tmp_path):
    html = _render(WEATHER_SCHEMA, WEATHER_STORED, "weather")
    assert "api_key" not in html and "radar_zoom" not in html
    assert "Radar Zoom Level" not in html and "API Key" not in html
    assert re.findall(r"Advanced Settings \((\d+)\)", html) == ["1"]
    assert "radar_range_miles" in _visible_names(html)

    _post, _json, restore = _make_post(tmp_path, WEATHER_SCHEMA,
                                       WEATHER_STORED, "weather")
    try:
        pairs = [(k, v) for k, v in _form_pairs(html)
                 if k != "radar_show_nowcast"]
        resp, cfg = _post(pairs)
    finally:
        restore()
    assert resp.status_code == 200, resp.get_json()
    assert cfg["api_key"] == "legacy-owm-key"
    assert cfg["radar_zoom"] == 7
    assert cfg["radar_show_nowcast"] is False
