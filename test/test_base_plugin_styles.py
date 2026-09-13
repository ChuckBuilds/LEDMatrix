"""BasePlugin.styles -- the per-element styling seam every plugin inherits.

Before this, each consumer of src.element_style repeated the same three
things: a guarded import, finding its own config_schema.json, and rebuilding
the resolver when on_config_change swapped the config dict. Getting the
second one wrong is silent -- the resolver simply has no defaults to compare
against, so every configured value reads as a deliberate override and the
plugin quietly stops honouring its own shipped styling.
"""

import json
import sys
import types

import pytest
from unittest.mock import MagicMock

from src.plugin_system.base_plugin import BasePlugin

SCHEMA = {
    "type": "object",
    "properties": {
        "customization": {
            "type": "object",
            "x-style-modes": ["live", "recent"],
            "x-style-elements": {
                "score_text": {
                    "title": "Score",
                    "font": {"default": "PressStart2P-Regular.ttf"},
                    "size": {"default": 10, "min": 4, "max": 16},
                    "color": {"default": [255, 255, 255]},
                    "offsets": True,
                },
            },
        },
    },
}


def _plugin_class(module_name, module_file, **attrs):
    """A plugin class whose module sits in a plugin directory, as a real
    one does -- that is what schema discovery keys on."""
    module = types.ModuleType(module_name)
    module.__file__ = str(module_file)
    sys.modules[module_name] = module

    namespace = dict(attrs)
    namespace["update"] = lambda self: None
    namespace["display"] = lambda self, force_clear=False: None
    cls = type("DemoPlugin", (BasePlugin,), namespace)
    cls.__module__ = module_name
    return cls


@pytest.fixture
def plugin_dir(tmp_path):
    d = tmp_path / "demo"
    d.mkdir()
    (d / "config_schema.json").write_text(json.dumps(SCHEMA), encoding="utf-8")
    return d


@pytest.fixture
def make(tmp_path, plugin_dir, request):
    created = []

    def _make(config, plugin_id="demo", schema_in_module_dir=True, **attrs):
        name = f"demo_module_{len(created)}_{id(request)}"
        module_file = (plugin_dir if schema_in_module_dir
                       else tmp_path / "elsewhere") / "manager.py"
        module_file.parent.mkdir(parents=True, exist_ok=True)
        cls = _plugin_class(name, module_file, **attrs)
        created.append(name)
        pm = MagicMock()
        pm.plugins_dir = str(tmp_path)
        return cls(plugin_id, config, MagicMock(), MagicMock(), pm)

    try:
        yield _make
    finally:
        for name in created:
            sys.modules.pop(name, None)


CONFIG = {"customization": {
    "score_text": {"font_size": 12},
    "layout": {"score_text": {"y_offset": -3}},
    "modes": {"live": {"score_text": {"font_size": 16}}},
}}


class TestSchemaDiscovery:
    def test_it_finds_the_schema_beside_the_plugin_module(self, make, plugin_dir):
        """Not beside base_plugin.py, which lives in src/plugin_system where
        no plugin schema exists."""
        p = make({})
        assert p._config_schema_path() == str(plugin_dir / "config_schema.json")

    def test_it_falls_back_to_the_plugins_directory(self, make):
        p = make({}, schema_in_module_dir=False)
        assert p._config_schema_path() is not None

    def test_it_accepts_the_ledmatrix_prefix_form(self, tmp_path, make):
        (tmp_path / "ledmatrix-music").mkdir()
        (tmp_path / "ledmatrix-music" / "config_schema.json").write_text(
            json.dumps(SCHEMA), encoding="utf-8")
        p = make({}, plugin_id="music", schema_in_module_dir=False)
        assert "ledmatrix-music" in p._config_schema_path()

    def test_a_missing_schema_is_not_fatal(self, tmp_path, make):
        p = make({}, plugin_id="ghost", schema_in_module_dir=False)
        # 'elsewhere' has no schema and neither does tmp_path/ghost
        assert p._config_schema_path() is None
        assert p.styles.style("score_text", classic_size=8).font_size == 8

    def test_the_module_directory_wins_over_the_plugins_directory(
            self, tmp_path, plugin_dir):
        """These two are the same path for an installed plugin, so the test
        has to force them apart -- and they genuinely diverge for a plugin
        symlinked in for development, where the module lives outside the
        configured plugins directory. Sourcing the path from this module's
        own __file__ instead would find neither, then quietly fall through
        to whatever the plugins directory happened to hold.
        """
        dev_dir = tmp_path / "dev-checkout"
        dev_dir.mkdir()
        dev_schema = json.loads(json.dumps(SCHEMA))
        (dev_schema["properties"]["customization"]["x-style-elements"]
         ["score_text"]["size"]["default"]) = 99
        (dev_dir / "config_schema.json").write_text(json.dumps(dev_schema),
                                                    encoding="utf-8")

        cls = _plugin_class("demo_dev_checkout", dev_dir / "manager.py")
        try:
            pm = MagicMock()
            pm.plugins_dir = str(tmp_path)   # holds demo/config_schema.json
            p = cls("demo", {}, MagicMock(), MagicMock(), pm)
            assert p._config_schema_path() == str(dev_dir / "config_schema.json")
            # and the difference is observable: 99 is this schema's default,
            # so configuring 99 must read as "not a choice".
            p.on_config_change({"customization": {"score_text": {"font_size": 99}}})
            assert p.styles.style("score_text", classic_size=10).font_size == 10
        finally:
            sys.modules.pop("demo_dev_checkout", None)

    def test_the_lookup_is_cached(self, make):
        """A miss must not re-scan the disk on every frame."""
        p = make({})
        first = p._config_schema_path()
        assert p._config_schema_path() is first


class TestResolution:
    def test_an_untouched_config_gets_the_classic_values(self, make):
        """The invariant that makes adopting this safe."""
        style = make({}).styles.style(
            "score_text", classic_font="4x6-font.ttf", classic_size=6,
            classic_color=(1, 2, 3))
        assert (style.font_name, style.font_size, style.color) == (
            "4x6-font.ttf", 6, (1, 2, 3))
        assert style.user_forced is False

    def test_a_users_choice_comes_through(self, make):
        style = make(CONFIG).styles.style("score_text", classic_size=10)
        assert style.font_size == 12
        assert style.offset == (0, -3)

    def test_style_mode_binds_without_touching_call_sites(self, make):
        """The whole point: a per-mode subclass sets one attribute and its
        existing lookups become mode-aware."""
        assert make(CONFIG).styles.style(
            "score_text", classic_size=10).font_size == 12
        assert make(CONFIG, STYLE_MODE="live").styles.style(
            "score_text", classic_size=10).font_size == 16

    def test_styles_for_handles_a_one_off_mode(self, make):
        p = make(CONFIG)
        assert p.styles_for("live").style("score_text", classic_size=10).font_size == 16
        assert p.styles_for("recent").style("score_text", classic_size=10).font_size == 12
        assert p.styles_for(None).style("score_text", classic_size=10).font_size == 12


class TestInvalidation:
    def test_the_resolver_is_reused(self, make):
        p = make(CONFIG)
        assert p.styles is p.styles

    def test_a_config_swap_rebuilds_it(self, make):
        """on_config_change replaces the dict rather than mutating it, so
        identity is the signal."""
        p = make(CONFIG)
        assert p.styles.style("score_text", classic_size=10).font_size == 12
        p.on_config_change({"customization": {"score_text": {"font_size": 6}}})
        assert p.styles.style("score_text", classic_size=10).font_size == 6

    def test_a_config_swap_rebuilds_the_per_mode_cache_too(self, make):
        p = make(CONFIG)
        assert p.styles_for("live").style("score_text", classic_size=10).font_size == 16
        p.on_config_change({"customization": {
            "modes": {"live": {"score_text": {"font_size": 5}}}}})
        assert p.styles_for("live").style("score_text", classic_size=10).font_size == 5


class TestDegradation:
    # config=None is not in this list on purpose: BasePlugin.__init__ reads
    # config.get("enabled") before styles exists, so a None config is a
    # constructor precondition rather than something styles should absorb.
    @pytest.mark.parametrize("config", [
        {}, {"customization": None}, {"customization": "nonsense"},
        {"customization": {"score_text": "nonsense"}},
        {"customization": {"layout": "nonsense"}},
        {"customization": {"modes": "nonsense"}},
    ])
    def test_a_hostile_config_still_resolves(self, config, make):
        style = make(config).styles.style("score_text", classic_size=7)
        assert style.font_size == 7

    def test_it_works_without_a_plugin_manager(self, plugin_dir):
        cls = _plugin_class("demo_no_pm", plugin_dir / "manager.py")
        try:
            p = cls("demo", {}, MagicMock(), MagicMock(), None)
            assert p.styles.style("score_text", classic_size=8).font_size == 8
        finally:
            sys.modules.pop("demo_no_pm", None)
