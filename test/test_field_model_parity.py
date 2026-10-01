"""
build_field_model() against the real render_field macro, for every schema.

The field model (src/plugin_system/field_model.py) is meant to replace the
1,100-line ``render_field`` macro in plugin_config.html as the one description
of a plugin's config form. Before anything renders from it, it has to be
complete: for each schema, the model must name exactly the form controls the
macro draws today, with the same starting values, and the same JS widgets
with the same names and values.

This test renders the macro (the real template, through a Flask Jinja
environment so ``tojson`` behaves as in the app) and parses the form:

* every named control inside the <form>: (name, control, submitted text,
  checked) in document order. A <select> contributes the option a browser
  would submit (the last ``selected`` one, else the first).
* every inline widget script: (widget, name, JSON value).

and checks both lists equal what the model predicts, in order.

Schemas covered:
* every plugin under plugin-repos/ and test/fixtures/plugins/,
* the official plugins monorepo, read-only, when a checkout is found: the
  directory named by $LEDMATRIX_MONOREPO_PLUGINS, else
  ../ledmatrix-plugins/plugins next to this checkout, else
  ~/.ledmatrix-dev-plugins/ledmatrix-plugins/plugins (dev_plugin_setup.sh),
* SYNTHETIC below: one schema reaching every branch of the macro, with a
  config that fills its tables, so CI covers every widget without the
  monorepo.

Each schema is rendered twice: with nothing stored (the macro's own default
fallback) and with the config the route really renders -- schema defaults
merged (prepare_plugin_config) and secrets masked.
"""

import html as html_lib
import json
import os
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest
from flask import Flask

from src.element_style import expand_style_elements
from src.plugin_system.field_model import (
    build_field_model, field_names, form_inputs, iter_fields, widget_mounts,
)
from src.plugin_system.schema_manager import plugin_config_defaults, prepare_plugin_config
from src.web_interface.secret_helpers import mask_secret_fields

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = PROJECT_ROOT / "web_interface" / "templates"


# ── schema sources ──────────────────────────────────────────────────────────

def _monorepo_plugins_dir():
    candidates = []
    if os.environ.get("LEDMATRIX_MONOREPO_PLUGINS"):
        candidates.append(Path(os.environ["LEDMATRIX_MONOREPO_PLUGINS"]))
    candidates.append(PROJECT_ROOT.parent / "ledmatrix-plugins" / "plugins")
    candidates.append(Path.home() / ".ledmatrix-dev-plugins" / "ledmatrix-plugins" / "plugins")
    for candidate in candidates:
        if candidate.is_dir() and any(candidate.glob("*/config_schema.json")):
            return candidate
    return None


MONOREPO = _monorepo_plugins_dir()


def _schema_files():
    found = []
    for base, label in ((PROJECT_ROOT / "plugin-repos", "plugin-repos"),
                        (PROJECT_ROOT / "test" / "fixtures" / "plugins", "fixtures"),
                        (MONOREPO, "monorepo")):
        if base is None or not base.is_dir():
            continue
        for path in sorted(base.glob("*/config_schema.json")):
            found.append((f"{label}/{path.parent.name}", path))
    return found


SCHEMA_FILES = _schema_files()


# Every branch of render_field / render_nested_section, plus a config that
# gives the row-based widgets rows to draw.
SYNTHETIC = {
    "type": "object",
    "x-propertyOrder": ["display_duration", "label", "mode", "count", "ratio",
                        "brightness", "zoom", "dup_enum", "tags", "days", "teams", "calendars",
                        "images", "feeds", "bad_feeds", "rows", "events", "colour", "credentials",
                        "files", "password", "picker", "plugin_widget", "nullable",
                        "nullable_number", "toggle", "flag", "schedule", "window",
                        "customization", "nested", "legacy", "empty_object", "hidden_one",
                        "hidden_object", "fancy_advanced", "not_advanced_object", "union"],
    "properties": {
        "enabled": {"type": "boolean", "default": True},
        "display_duration": {"type": "number", "default": 15, "minimum": 1},
        "label": {"type": "string", "default": "Hello \"world\" & <you>", "title": "Label"},
        "mode": {"type": "string", "enum": ["vs", "abbrev", "full_name"], "default": "abbrev",
                 "x-options": {"labels": {"vs": "vs."}}},
        "count": {"type": "integer", "default": 3, "enum": [1, 3, 5]},
        "ratio": {"type": "number", "minimum": 0, "maximum": 1},
        "brightness": {"type": "integer", "default": 50, "x-widget": "slider",
                       "minimum": 0, "maximum": 100},
        "zoom": {"type": "number", "x-widget": "number-input", "default": None},
        # 1 == 1.0, so both options are marked selected; a browser submits the last.
        "dup_enum": {"type": "number", "enum": [1, 1.0, 2], "default": 1},
        "tags": {"type": "array", "items": {"type": "string"}, "default": ["a", "b"]},
        "days": {"type": "array", "x-widget": "day-selector", "items": {"type": "string"},
                 "default": ["mon", "fri"]},
        "teams": {"type": "array", "x-widget": "checkbox-group",
                  "items": {"type": "string", "enum": ["NYY", "BOS", "LAD"]},
                  "x-options": {"labels": {"NYY": "Yankees"}}, "default": ["NYY"]},
        "calendars": {"type": "array", "x-widget": "google-calendar-picker",
                      "default": "primary, work"},
        "images": {"type": "array", "x-widget": "file-upload",
                   "x-upload-config": {"max_files": 3}, "items": {"type": "object"}},
        "feeds": {"type": "array", "x-widget": "custom-feeds", "items": {
            "type": "object", "properties": {
                "name": {"type": "string"}, "url": {"type": "string"},
                "logo": {"type": "object", "properties": {
                    "path": {"type": "string"}, "id": {"type": "string"}}},
                "enabled": {"type": "boolean", "default": True}}}},
        "bad_feeds": {"type": "array", "x-widget": "custom-feeds",
                      "items": {"type": "object", "properties": {"title": {"type": "string"}}}},
        "rows": {"type": "array", "items": {"type": "object", "properties": {
            "id": {"type": "string", "x-display": "hidden"},
            "symbol": {"type": "string", "description": "Ticker"},
            "shares": {"type": ["null", "integer"], "minimum": 0},
            "side": {"type": "string", "enum": ["buy", "sell", None], "default": "buy"},
            "active": {"type": "boolean", "default": True},
            "on": {"type": "string", "x-widget": "date-picker"},
            "at": {"type": "string", "x-widget": "time-picker"},
            "logo": {"type": "string", "x-widget": "file-upload-single"},
            "layout": {"type": "object", "properties": {
                "x": {"type": "integer", "default": 0},
                "secret_offset": {"type": "integer", "x-display": "hidden"},
                "y": {"type": "integer"}}},
            "note": {"type": "string", "default": "n/a"},
            "odd": {"type": ["object", "null"], "properties": {"a": {"type": "string"}}},
        }}},
        "events": {"type": "array", "x-columns": ["title", "on", "at", "logo", "kind", "gone"],
                   "items": {"type": "object", "properties": {
                       "title": {"type": "string", "default": "Untitled"},
                       "on": {"type": "string", "x-widget": "date-picker"},
                       "at": {"type": "string", "x-widget": "time-picker"},
                       "logo": {"type": "string", "x-widget": "file-upload-single"},
                       "kind": {"type": "string", "enum": ["a", "b"], "default": "b"},
                       "secret": {"type": "string", "x-display": "hidden"}}}},
        "colour": {"type": "array", "x-widget": "color-picker", "default": [10, 20, 30]},
        "credentials": {"type": "string", "x-widget": "file-upload",
                        "x-upload-config": {"target_filename": "creds.json"}},
        "files": {"type": "string", "x-widget": "json-file-manager"},
        "password": {"type": "string", "x-widget": "password-input", "x-secret": True,
                     "default": "hunter2"},
        "picker": {"type": "string", "x-widget": "font-selector", "default": "4x6"},
        "plugin_widget": {"type": "string", "x-widget": "custom-leagues", "default": "eng.1"},
        "nullable": {"type": ["null", "string"], "default": None},
        "nullable_number": {"type": "integer", "default": None},
        "toggle": {"type": "boolean", "x-widget": "toggle-switch"},
        "flag": {"type": "boolean", "default": False, "x-advanced": True},
        "schedule": {"type": "object", "x-widget": "schedule-picker",
                     "properties": {"enabled": {"type": "boolean"}}},
        "window": {"type": "object", "x-widget": "time-range", "default": {"start": "07:00"}},
        "customization": {"type": "object", "x-widget": "style-editor", "properties": {
            "score_text": {"type": "object", "properties": {
                "font": {"type": "string", "default": "PressStart2P"},
                "text_color": {"type": "array", "x-widget": "color-picker",
                               "default": [255, 0, 0]}}},
            "favorite_result_colors": {"type": "boolean", "default": True}}},
        "nested": {"type": "object", "title": "Nested", "x-propertyOrder": ["b", "a", "missing"],
                   "properties": {
                       "a": {"type": "string", "default": "x"},
                       "b": {"type": "object", "properties": {
                           "deep": {"type": "integer", "default": 7}}}}},
        "legacy": {"type": "object", "properties": {
            "enabled": {"type": "boolean"}, "seconds": {"type": "integer", "default": 30}}},
        "empty_object": {"type": "object"},
        "hidden_one": {"type": "string", "x-display": "hidden", "default": "zzz"},
        "hidden_object": {"type": "object", "properties": {
            "inner": {"type": "string", "x-display": "hidden"}}},
        "fancy_advanced": {"type": "integer", "default": 1, "x-advanced": True},
        "not_advanced_object": {"type": "object", "x-advanced": True, "properties": {
            "inner": {"type": "boolean", "default": True}}},
        "union": {"type": ["boolean", "object"], "properties": {
            "enabled": {"type": "boolean"}}},
    },
}

SYNTHETIC_CONFIG = {
    "label": "stored 'quote'",
    "teams": ["BOS", "SEA"],  # SEA is no longer an option
    "images": [{"id": "img-1", "path": "assets/a.png", "filename": "a.png",
                "schedule": {"enabled": True, "mode": "weekly"}}],
    "feeds": [
        {"name": "News", "url": "https://example.com/rss",
         "logo": {"path": "assets/logo.png", "id": "logo-1"}, "enabled": False},
        {"name": "Blog", "url": "https://example.com/blog"},
    ],
    "bad_feeds": [{"title": "ignored"}],
    "rows": [
        {"id": "row-1", "symbol": "AAPL", "shares": 10, "side": "sell", "active": False,
         "on": "2026-01-02", "layout": {"x": 3, "secret_offset": 9}, "odd": {"a": "b"}},
        {"symbol": "MSFT", "shares": None, "at": "09:30", "logo": "assets/m.png"},
    ],
    "events": [
        {"title": "Launch", "on": "2026-03-04", "at": "18:00", "logo": "assets/l.png",
         "kind": "a", "secret": "s3"},
        {"on": None, "at": None, "logo": None, "kind": None},
    ],
    "colour": [1, 2],
    "legacy": True,
    "union": True,
    "zoom": 2.5,
}

# Every branch the macro has, so a schema set that stops reaching one fails.
MACRO_WIDGETS = {
    "checkbox", "toggle-switch", "select", "number", "slider", "number-input",
    "file-upload", "checkbox-group", "google-calendar-picker", "day-selector",
    "custom-feeds", "array-table", "color-picker", "csv-text", "text",
    "json-file-manager", "password-input", "font-selector", "custom-leagues",
    "schedule-picker", "time-range", "style-editor", "section",
}


# ── rendering the macro ─────────────────────────────────────────────────────

_app = Flask("field_model_parity", template_folder=str(TEMPLATES))


def _render(schema, config, plugin_id):
    plugin = {"id": plugin_id, "name": plugin_id, "description": "", "enabled": True,
              "author": "test", "version": "1.0.0"}
    with _app.app_context():
        return _app.jinja_env.get_template("v3/partials/plugin_config.html").render(
            plugin=plugin, schema=schema, config=config, web_ui_actions=[])


class _FormParser(HTMLParser):
    """Named controls and widget scripts inside the config <form>."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.controls = []
        self.scripts = []
        self._select = None
        self._in_script = False
        self._script = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "form" and (a.get("id") or "").startswith("plugin-config-form-"):
            self.depth += 1
            return
        if not self.depth:
            return
        if tag == "script":
            self._in_script, self._script = True, []
        elif tag == "input" and a.get("name") is not None:
            kind = (a.get("type") or "text").lower()
            if kind == "checkbox":
                value = a.get("value") if a.get("value") is not None else "on"
            else:
                value = a.get("value") if a.get("value") is not None else ""
            self.controls.append({"name": a["name"], "control": kind, "text": value,
                                  "checked": "checked" in a if kind == "checkbox" else None})
        elif tag == "select" and a.get("name") is not None:
            self._select = {"name": a["name"], "control": "select", "options": [],
                            "selected": [], "checked": None}
        elif tag == "option" and self._select is not None:
            self._select["options"].append(a.get("value"))
            if "selected" in a:
                self._select["selected"].append(a.get("value"))

    def handle_endtag(self, tag):
        if tag == "form" and self.depth:
            self.depth -= 1
        elif tag == "script" and self._in_script:
            self._in_script = False
            self.scripts.append("".join(self._script))
        elif tag == "select" and self._select is not None:
            s = self._select
            text = s["selected"][-1] if s["selected"] else (s["options"][0] if s["options"] else "")
            self.controls.append({"name": s["name"], "control": "select", "text": text,
                                  "checked": None, "options": s["options"]})
            self._select = None

    def handle_data(self, data):
        if self._in_script:
            self._script.append(data)


_VALUE_RE = re.compile(r"^\s*var value = (?:fallback \? fallback\.value : )?(.*);\s*$", re.M)
_NAME_RE = re.compile(r"\bname: '([^']*)'")
_WIDGET_RE = re.compile(r"LEDMatrixWidgets\.get\('([^']+)'\)")
_PLUGIN_WIDGET_RE = re.compile(r"var WIDGET = (\".*?\");")


def _script_mount(script):
    plugin = _PLUGIN_WIDGET_RE.search(script)
    widget = json.loads(plugin.group(1)) if plugin else None
    if widget is None:
        found = _WIDGET_RE.search(script)
        widget = found.group(1) if found else None
    if widget is None:
        return None
    name = _NAME_RE.search(script)
    value = _VALUE_RE.search(script)
    return (widget,
            html_lib.unescape(name.group(1)) if name else None,
            _canon(json.loads(value.group(1))) if value else None)


def _parse_form(markup):
    parser = _FormParser()
    parser.feed(markup)
    parser.close()
    controls = [(c["name"], c["control"], c["text"], c["checked"], tuple(c.get("options") or ()))
                for c in parser.controls]
    mounts = [m for m in (_script_mount(s) for s in parser.scripts) if m]
    return controls, mounts


# ── what the model predicts ─────────────────────────────────────────────────

def _canon(value):
    """JSON round trip: tuples become lists, so equality is JSON equality."""
    return json.loads(json.dumps(value))


def _as_text(item):
    value, encoding = item["value"], item["encoding"]
    if encoding == "json":
        return value  # compared after parsing, see _expected_controls
    if encoding == "csv":
        return ", ".join(str(v) for v in value)
    if encoding == "bool":
        return "true" if value else "false"
    return str(value)


def _expected_controls(model):
    out = []
    for item in form_inputs(model):
        options = tuple(str(o) for o in item.get("options") or ())
        out.append((item["name"], item["control"], _as_text(item),
                    item.get("checked") if item["control"] == "checkbox" else None, options))
    return out


def _normalise_json_controls(controls, model_inputs):
    """Compare JSON-encoded inputs by value, not by spelling."""
    result = []
    for control, item in zip(controls, model_inputs):
        if item["encoding"] == "json" and control[0] == item["name"]:
            try:
                parsed = json.loads(control[2])
            except ValueError:
                parsed = control[2]
            control = (control[0], control[1], parsed, control[3], control[4])
        result.append(control)
    return result + list(controls[len(model_inputs):])


def _expected_mounts(model):
    return [(m["widget"], m["name"], _canon(m["value"])) for m in widget_mounts(model)]


# ── cases ───────────────────────────────────────────────────────────────────

def _route_config(schema, stored):
    """The config plugin_config.html is rendered with (pages_v3)."""
    config = prepare_plugin_config(stored, schema, plugin_config_defaults(schema))
    return mask_secret_fields(config, schema.get("properties") or {})


def _cases():
    cases = [("synthetic", "stored", SYNTHETIC, SYNTHETIC_CONFIG),
             ("synthetic", "route", SYNTHETIC, _route_config(SYNTHETIC, SYNTHETIC_CONFIG)),
             ("synthetic", "empty", SYNTHETIC, {}),
             ("schemaless", "stored", {}, {"enabled": True, "a": True, "b": 2.5, "c": "x"})]
    for label, path in SCHEMA_FILES:
        schema = expand_style_elements(json.loads(path.read_text(encoding="utf-8")))
        cases.append((label, "empty", schema, {}))
        cases.append((label, "route", schema, _route_config(schema, {})))
    return cases


CASES = _cases()


def _check(schema, config, plugin_id):
    markup = _render(schema, config, plugin_id)
    model = build_field_model(schema, config, plugin_id)
    controls, mounts = _parse_form(markup)
    inputs = form_inputs(model)
    expected = _expected_controls(model)
    expected = [(n, c, _canon(t) if i["encoding"] == "json" else t, k, o)
                for (n, c, t, k, o), i in zip(expected, inputs)]
    assert _normalise_json_controls(controls, inputs) == expected
    assert mounts == _expected_mounts(model)
    # The headline property: the same set of posted names.
    rendered = {c[0] for c in controls} | {m[1] for m in mounts if m[1]}
    assert rendered == {name for name, _ in field_names(model)}
    return model


@pytest.mark.parametrize("label,variant,schema,config", CASES,
                         ids=[f"{c[0]}[{c[1]}]" for c in CASES])
def test_model_matches_the_macro(label, variant, schema, config):
    plugin_id = label.split("/")[-1]
    _check(schema, json.loads(json.dumps(config)), plugin_id)


def test_every_macro_branch_is_reached():
    """The cases above must exercise every widget path the macro has."""
    seen = set()
    for _label, _variant, schema, config in CASES:
        model = build_field_model(schema, json.loads(json.dumps(config)), "p")
        seen |= {node["widget"] for node in iter_fields(model)}
    assert MACRO_WIDGETS <= seen, sorted(MACRO_WIDGETS - seen)


def test_the_local_schemas_are_all_covered():
    """plugin-repos/ and the fixtures are always in the parity set."""
    labels = {label for label, _ in SCHEMA_FILES}
    for base, prefix in ((PROJECT_ROOT / "plugin-repos", "plugin-repos"),
                         (PROJECT_ROOT / "test" / "fixtures" / "plugins", "fixtures")):
        for path in base.glob("*/config_schema.json"):
            assert f"{prefix}/{path.parent.name}" in labels


def test_the_synthetic_model_reads_as_documented():
    """Spot checks of the model itself, beyond parity with the HTML."""
    model = build_field_model(SYNTHETIC, json.loads(json.dumps(SYNTHETIC_CONFIG)), "demo")
    by_path = {}
    for node in iter_fields(model):
        # First wins: a style-editor shares its path with its fallback section.
        by_path.setdefault(node["path"], node)

    assert "enabled" not in by_path  # the header toggle owns it
    assert "hidden_one" not in by_path and "hidden_object" not in by_path
    assert model["rendered_sections"][-2:] == ["flag", "fancy_advanced"]
    assert [n["path"] for n in model["advanced_fields"]] == ["flag", "fancy_advanced"]
    assert by_path["not_advanced_object"]["advanced"] is False

    assert by_path["mode"]["widget"] == "select"
    assert by_path["mode"]["options"][0] == {"value": "vs", "label": "vs."}
    assert by_path["count"]["widget"] == "select"  # enum wins over integer
    assert by_path["teams"]["stale_values"] == ["SEA"]
    assert by_path["teams"]["value"] == ["BOS"]
    assert by_path["calendars"]["mount"]["value"] == ["primary", "work"]
    assert by_path["legacy"]["value"] == {"enabled": True}
    assert by_path["legacy.enabled"]["inputs"][0]["checked"] is True
    assert by_path["nested.b.deep"]["value"] == 7
    assert [c["key"] for c in by_path["nested"]["children"]] == ["b", "a"]
    assert by_path["password"]["secret"] is True
    assert by_path["plugin_widget"]["mount"]["plugin_widget"] is True
    assert by_path["customization"]["widget"] == "style-editor"
    assert by_path["customization.score_text.text_color"]["widget"] == "color-picker"
    assert [c["key"] for c in by_path["rows"]["columns"]] == ["symbol", "shares", "side", "active"]
    assert by_path["rows"]["advanced_columns"] == ["on", "at", "logo", "layout", "note", "odd"]
    assert by_path["bad_feeds"]["error"]
    assert "default" not in by_path["ratio"] and by_path["ratio"]["value"] is None
    json.dumps(model)  # plain JSON all the way down


def test_monorepo_coverage_is_reported():
    """Not a gate: say which monorepo the parity run used (or that it was absent)."""
    count = sum(1 for label, _ in SCHEMA_FILES if label.startswith("monorepo/"))
    if MONOREPO is None:
        pytest.skip("no ledmatrix-plugins checkout found; set LEDMATRIX_MONOREPO_PLUGINS")
    assert count == len(list(MONOREPO.glob("*/config_schema.json")))
