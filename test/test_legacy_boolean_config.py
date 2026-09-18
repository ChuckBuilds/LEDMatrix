"""A boolean the schema has since turned into an ``{enabled, ...}`` object.

The news plugin's ``global.dynamic_duration`` went from ``true`` to an object
with ``enabled``, ``min_duration_seconds``, ... An install that has not saved
the news settings since still holds ``true``, and on every start the display
service logged::

    Plugin news config does not match its schema (loading anyway):
    Field 'global.dynamic_duration': Expected type object, got bool

and flagged news degraded. The settings form already reads such a boolean as
``{"enabled": <bool>}`` (``render_nested_section`` in plugin_config.html); the
loader did not. Both now follow ``legacy_bool_as_object``, and the parity tests
below render the real macro against it so the two cannot drift.
"""

import json
import sys
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.plugin_system.plugin_manager import PluginManager
from src.plugin_system.schema_manager import (
    legacy_bool_as_object,
    normalize_legacy_booleans,
    plugin_config_defaults,
    prepare_plugin_config,
)


DYNAMIC_DURATION = {
    "type": "object",
    "properties": {
        "enabled": {"type": "boolean", "default": True},
        "min_duration_seconds": {"type": "integer", "default": 30,
                                 "minimum": 10, "maximum": 300},
        "max_duration_seconds": {"type": "integer", "default": 300,
                                 "minimum": 30, "maximum": 600},
        "buffer_ratio": {"type": "number", "default": 0.1,
                         "minimum": 0.01, "maximum": 1.0},
    },
    "additionalProperties": False,
}

# The shape of news' schema, cut down to what the tests need.
NEWS_SCHEMA = {
    "type": "object",
    "properties": {
        "enabled": {"type": "boolean", "default": True},
        "global": {
            "type": "object",
            "properties": {
                "font_size": {"type": "integer", "default": 12},
                "dynamic_duration": DYNAMIC_DURATION,
                "display": {
                    "type": "object",
                    "properties": {
                        "scroll_speed": {"type": "number", "default": 1.0},
                    },
                    "additionalProperties": False,
                },
                "headline_paging": {
                    "type": "object",
                    "properties": {
                        "enabled": {"type": "boolean", "default": False},
                        "page_hold_seconds": {"type": "number", "default": 2.0},
                    },
                    "additionalProperties": False,
                },
            },
            "additionalProperties": False,
        },
        "feeds": {
            "type": "object",
            "properties": {
                "custom_feeds": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "enabled": {"type": "boolean", "default": True},
                            "schedule": {
                                "type": "object",
                                "properties": {"enabled": {"type": "boolean"}},
                            },
                        },
                    },
                },
            },
        },
    },
    "additionalProperties": False,
}


# --------------------------------------------------------------------------
# The helper
# --------------------------------------------------------------------------

class TestLegacyBoolAsObject:
    def test_true_under_an_enabled_object_becomes_its_switch(self):
        assert legacy_bool_as_object(True, DYNAMIC_DURATION) == {"enabled": True}

    def test_false_stays_off(self):
        """Upgrading must never turn a feature the user switched off back on."""
        assert legacy_bool_as_object(False, DYNAMIC_DURATION) == {"enabled": False}

    def test_nullable_object_type_list_counts_as_object(self):
        prop = dict(DYNAMIC_DURATION, type=["object", "null"])
        assert legacy_bool_as_object(True, prop) == {"enabled": True}

    @pytest.mark.parametrize("value", [1, 0, "true", "false", None, [], 2.5])
    def test_a_value_that_is_not_a_real_bool_is_untouched(self, value):
        assert legacy_bool_as_object(value, DYNAMIC_DURATION) is value

    def test_an_object_without_enabled_is_untouched(self):
        prop = {"type": "object", "properties": {"speed": {"type": "number"}}}
        assert legacy_bool_as_object(True, prop) is True

    def test_a_boolean_or_object_union_is_untouched(self):
        """The form draws ``["boolean", "object"]`` as a checkbox, and the
        schema accepts the boolean, so there is nothing to upgrade."""
        prop = dict(DYNAMIC_DURATION, type=["boolean", "object"])
        assert legacy_bool_as_object(True, prop) is True

    def test_a_non_object_schema_is_untouched(self):
        assert legacy_bool_as_object(True, {"type": "boolean"}) is True
        assert legacy_bool_as_object(True, None) is True


class TestNormalizeLegacyBooleans:
    def test_top_level(self):
        schema = {"type": "object",
                  "properties": {"dynamic_duration": DYNAMIC_DURATION}}
        out = normalize_legacy_booleans({"dynamic_duration": True}, schema)
        assert out == {"dynamic_duration": {"enabled": True}}

    def test_nested(self):
        cfg = {"global": {"font_size": 12, "dynamic_duration": True}}
        out = normalize_legacy_booleans(cfg, NEWS_SCHEMA)
        assert out == {"global": {"font_size": 12,
                                  "dynamic_duration": {"enabled": True}}}

    def test_several_depths_report_their_paths(self):
        schema = {"type": "object", "properties": {
            "dynamic_duration": DYNAMIC_DURATION,
            "outer": {"type": "object", "properties": {
                "inner": {"type": "object", "properties": {
                    "mode": DYNAMIC_DURATION}}}},
        }}
        cfg = {"dynamic_duration": False, "outer": {"inner": {"mode": True}}}
        changed = []
        out = normalize_legacy_booleans(cfg, schema, changed)
        assert out == {"dynamic_duration": {"enabled": False},
                       "outer": {"inner": {"mode": {"enabled": True}}}}
        assert changed == ["dynamic_duration", "outer.inner.mode"]

    def test_array_items_are_left_alone_as_the_form_does(self):
        """The form hands arrays to widgets and never upgrades inside them."""
        cfg = {"feeds": {"custom_feeds": [{"name": "a", "schedule": True}]}}
        out = normalize_legacy_booleans(cfg, NEWS_SCHEMA)
        assert out is cfg

    def test_already_an_object_is_untouched(self):
        cfg = {"global": {"dynamic_duration": {"enabled": False,
                                               "min_duration_seconds": 45}}}
        assert normalize_legacy_booleans(cfg, NEWS_SCHEMA) is cfg

    def test_other_mismatches_are_untouched(self):
        cfg = {"global": {"font_size": True, "display": True,
                          "dynamic_duration": "yes"}}
        changed = []
        assert normalize_legacy_booleans(cfg, NEWS_SCHEMA, changed) is cfg
        assert changed == []

    def test_keys_the_schema_does_not_declare_are_untouched(self):
        cfg = {"global": {"mystery": True}, "vegas_width_pct": 50}
        assert normalize_legacy_booleans(cfg, NEWS_SCHEMA) is cfg

    def test_the_stored_config_is_not_mutated(self):
        """load_config() may hand back a cached dict; it must stay as stored."""
        cfg = {"enabled": True, "global": {"dynamic_duration": True}}
        before = json.loads(json.dumps(cfg))
        out = normalize_legacy_booleans(cfg, NEWS_SCHEMA)
        assert cfg == before
        assert out is not cfg and out["global"] is not cfg["global"]

    @pytest.mark.parametrize("config,schema", [
        (None, NEWS_SCHEMA), ([], NEWS_SCHEMA), ({"a": True}, None),
        ({"a": True}, {"type": "object"}),
    ])
    def test_tolerates_odd_inputs(self, config, schema):
        assert normalize_legacy_booleans(config, schema) is config


# --------------------------------------------------------------------------
# The load path
# --------------------------------------------------------------------------

PLUGIN_ID = "news-shaped"


@pytest.fixture
def load(tmp_path):
    """Run the real ``PluginManager.load_plugin`` for a plugin with
    ``NEWS_SCHEMA`` and the given stored section; the plugin class is stubbed.

    Returns ``(loaded, config the plugin received, logger, health_tracker)``.
    """
    plugins_dir = tmp_path / "plugins"
    plugin_dir = plugins_dir / PLUGIN_ID
    plugin_dir.mkdir(parents=True)
    manifest = {"id": PLUGIN_ID, "name": "News-shaped", "version": "1.0.0",
                "entry_point": "manager.py", "class_name": "Plugin"}
    (plugin_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (plugin_dir / "config_schema.json").write_text(json.dumps(NEWS_SCHEMA),
                                                   encoding="utf-8")

    def _load(stored_section):
        config_manager = MagicMock()
        config_manager.load_config.return_value = {
            PLUGIN_ID: json.loads(json.dumps(stored_section))}
        with patch('src.common.permission_utils.ensure_directory_permissions'):
            manager = PluginManager(plugins_dir=str(plugins_dir),
                                    config_manager=config_manager,
                                    display_manager=MagicMock(),
                                    cache_manager=MagicMock())
        manager.logger = MagicMock()
        manager.health_tracker = MagicMock()
        manager.plugin_manifests[PLUGIN_ID] = manifest
        manager.plugin_loader.find_plugin_directory = MagicMock(return_value=plugin_dir)

        received = {}

        def fake_load_plugin(**kwargs):
            received.update(kwargs["config"])
            instance = MagicMock()
            instance.validate_config.return_value = True
            return instance, MagicMock()

        manager.plugin_loader.load_plugin = MagicMock(side_effect=fake_load_plugin)
        loaded = manager.load_plugin(PLUGIN_ID)
        return loaded, received, manager.logger, manager.health_tracker

    return _load


def _schema_warnings(logger):
    return [c for c in logger.warning.call_args_list
            if "does not match its schema" in str(c.args[0])]


def _degraded_reason(health_tracker):
    calls = [c.args for c in health_tracker.set_degraded.call_args_list
             if c.args[0] == PLUGIN_ID]
    assert calls, "schema validation never ran"
    return calls[-1][1]


class TestLoadPath:
    def test_news_shaped_legacy_config_loads_without_warning_or_degraded(self, load):
        stored = {"enabled": True,
                  "global": {"font_size": 12, "dynamic_duration": True,
                             "headline_paging": {"enabled": False}}}
        loaded, received, logger, tracker = load(stored)

        assert loaded is True
        assert _schema_warnings(logger) == []
        assert _degraded_reason(tracker) is None
        # The plugin gets the object, with the schema defaults filled in.
        assert received["global"]["dynamic_duration"] == {
            "enabled": True, "min_duration_seconds": 30,
            "max_duration_seconds": 300, "buffer_ratio": 0.1}
        # And the upgrade is logged once, below warning level.
        assert any("global.dynamic_duration" in str(c.args)
                   for c in logger.info.call_args_list)

    def test_a_legacy_false_loads_switched_off(self, load):
        loaded, received, logger, tracker = load(
            {"enabled": True, "global": {"dynamic_duration": False}})
        assert loaded is True
        assert received["global"]["dynamic_duration"]["enabled"] is False
        assert _schema_warnings(logger) == []
        assert _degraded_reason(tracker) is None

    def test_a_real_mismatch_still_warns_and_degrades(self, load):
        loaded, received, logger, tracker = load(
            {"enabled": True, "global": {"dynamic_duration": True,
                                         "font_size": "big"}})
        assert loaded is True  # warn-only, as before
        warnings = _schema_warnings(logger)
        assert len(warnings) == 1
        assert "font_size" in str(warnings[0].args)
        assert "dynamic_duration" not in str(warnings[0].args)
        reason = _degraded_reason(tracker)
        assert reason and "font_size" in reason

    def test_a_boolean_under_an_object_without_enabled_still_warns(self, load):
        loaded, received, logger, tracker = load(
            {"enabled": True, "global": {"display": True}})
        assert loaded is True
        assert received["global"]["display"] is True
        warnings = _schema_warnings(logger)
        assert len(warnings) == 1 and "global.display" in str(warnings[0].args)
        assert _degraded_reason(tracker)


# --------------------------------------------------------------------------
# Parity with the settings form
# --------------------------------------------------------------------------

PARITY_SCHEMA = {
    "type": "object",
    "properties": {
        "enabled": {"type": "boolean", "default": True},
        "global": {"type": "object", "properties": {
            "dynamic_duration": DYNAMIC_DURATION,
            "outer": {"type": "object", "properties": {
                "inner": {"type": "object", "properties": {
                    "enabled": {"type": "boolean", "default": True},
                    "level": {"type": "integer", "default": 1},
                }},
            }},
            "union": {"type": ["boolean", "object"], "properties": {
                "enabled": {"type": "boolean"}}},
        }},
    },
}


class _Checkboxes(HTMLParser):
    def __init__(self):
        super().__init__()
        self.checked = {}

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "input" and a.get("type") == "checkbox" and a.get("name"):
            self.checked[a["name"]] = "checked" in a


def _form_checkboxes(stored):
    from jinja2 import Environment, FileSystemLoader, select_autoescape

    env = Environment(
        loader=FileSystemLoader(str(PROJECT_ROOT / "web_interface" / "templates")),
        autoescape=select_autoescape(["html"]),
    )
    plugin = {"id": "demo", "name": "Demo", "description": "", "enabled": True,
              "author": "me", "version": "1.0.0"}
    html = env.get_template("v3/partials/plugin_config.html").render(
        plugin=plugin, schema=PARITY_SCHEMA, config=json.loads(json.dumps(stored)))
    parser = _Checkboxes()
    parser.feed(html)
    return parser.checked


def _loader_enabled(prepared, *path):
    node = prepared
    for key in path:
        node = node.get(key) if isinstance(node, dict) else None
    if not isinstance(node, dict):
        # A value the loader cannot read as the object (1, "true") fails
        # validation; the form treats it as missing and draws the schema
        # default, as it does for every other missing field.
        return True
    return node.get("enabled") is True


@pytest.mark.parametrize("value", [True, False, 1, "true", None])
def test_form_and_loader_agree_on_what_a_stored_value_means(value):
    """The form draws the object's ``enabled`` checkbox ticked exactly when the
    plugin runs with it on -- legacy booleans read as objects, then schema
    defaults filled in (prepare_plugin_config)."""
    stored = {"enabled": True,
              "global": {"dynamic_duration": value, "outer": {"inner": value}}}
    prepared = prepare_plugin_config(stored, PARITY_SCHEMA,
                                     plugin_config_defaults(PARITY_SCHEMA))
    boxes = _form_checkboxes(stored)

    for path in (("global", "dynamic_duration"), ("global", "outer", "inner")):
        name = ".".join(path) + ".enabled"
        assert name in boxes, f"form drew no {name} checkbox"
        assert boxes[name] is _loader_enabled(prepared, *path), name


@pytest.mark.parametrize("value", [True, False])
def test_form_and_loader_agree_a_boolean_union_stays_a_boolean(value):
    stored = {"enabled": True, "global": {"union": value}}
    normalized = normalize_legacy_booleans(stored, PARITY_SCHEMA)
    boxes = _form_checkboxes(stored)

    assert normalized is stored
    assert boxes.get("global.union") is value
    assert "global.union.enabled" not in boxes
