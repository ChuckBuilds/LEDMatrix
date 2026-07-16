"""Tests for the skin system: discovery, version gating, fallback
semantics, module isolation, and the view-model contract."""

import json
import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PIL import Image, ImageFont

# src.base_classes.sports transitively imports the hardware matrix driver;
# stub it so the fallback-semantics tests can import SportsCore off-device.
sys.modules.setdefault("rgbmatrix", MagicMock())

from src.skin_system import skin_runtime
from src.skin_system.skin_base import (
    SKIN_API_VERSION,
    ScoreboardSkin,
    SkinContext,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURES_DIR = PROJECT_ROOT / "src" / "skin_system" / "fixtures"

# The v1.0 guaranteed view-model keys (docs/CREATING_SKINS.md). Renaming
# or removing any of these is a breaking change to every published skin:
# it requires a VIEW_MODEL_VERSION major bump and a compat shim.
GUARANTEED_KEYS = [
    "id", "game_time", "game_date", "start_time_utc", "status_text",
    "is_live", "is_final", "is_upcoming", "is_halftime",
    "home_abbr", "home_id", "home_score", "home_logo_path", "home_record",
    "away_abbr", "away_id", "away_score", "away_logo_path", "away_record",
]


def write_skin(skins_dir: Path, skin_id: str, *, api_version: str = SKIN_API_VERSION,
               body: str = None, extra_files: dict = None,
               class_name: str = "TestSkin") -> Path:
    skin_dir = skins_dir / skin_id
    skin_dir.mkdir(parents=True)
    manifest = {
        "id": skin_id, "name": skin_id, "version": "1.0.0",
        "skin_api_version": api_version, "class_name": class_name,
        "targets": {"sports": ["baseball"]},
    }
    (skin_dir / "skin.json").write_text(json.dumps(manifest))
    if body is None:
        body = (
            "from src.skin_system.skin_base import ScoreboardSkin\n"
            f"class {class_name}(ScoreboardSkin):\n"
            "    def render_live(self, ctx, game):\n"
            "        ctx.draw.rectangle([0, 0, 4, 4], fill=(255, 0, 0))\n"
            "        return True\n"
        )
    (skin_dir / "skin.py").write_text(body)
    for name, content in (extra_files or {}).items():
        (skin_dir / name).write_text(content)
    return skin_dir


class TestDiscovery:
    def test_discovers_valid_skin(self, tmp_path):
        write_skin(tmp_path, "my-skin")
        skins = skin_runtime.discover_skins(tmp_path, force_refresh=True)
        assert "my-skin" in skins
        assert skins["my-skin"]["_skin_dir"].endswith("my-skin")

    def test_skips_manifest_missing_required_fields(self, tmp_path):
        skin_dir = tmp_path / "broken"
        skin_dir.mkdir()
        (skin_dir / "skin.json").write_text(json.dumps({"id": "broken"}))
        assert skin_runtime.discover_skins(tmp_path, force_refresh=True) == {}

    def test_skips_unreadable_manifest_and_non_skin_dirs(self, tmp_path):
        (tmp_path / "not-a-skin").mkdir()
        bad = tmp_path / "bad-json"
        bad.mkdir()
        (bad / "skin.json").write_text("{nope")
        write_skin(tmp_path, "good-skin")
        skins = skin_runtime.discover_skins(tmp_path, force_refresh=True)
        assert list(skins) == ["good-skin"]

    def test_missing_directory_is_empty(self, tmp_path):
        assert skin_runtime.discover_skins(tmp_path / "nope") == {}

    def test_example_skin_in_repo_is_discoverable(self):
        skins = skin_runtime.discover_skins(force_refresh=True)
        assert "example-classic-baseball" in skins


class TestLoadSkin:
    def test_loads_and_instantiates(self, tmp_path):
        write_skin(tmp_path, "my-skin")
        skin = skin_runtime.load_skin("my-skin", sport="baseball",
                                      skins_dir=tmp_path)
        assert isinstance(skin, ScoreboardSkin)

    def test_unknown_skin_returns_none(self, tmp_path):
        assert skin_runtime.load_skin("ghost", skins_dir=tmp_path) is None

    def test_api_major_mismatch_is_refused(self, tmp_path):
        write_skin(tmp_path, "old-skin", api_version="99.0.0")
        assert skin_runtime.load_skin("old-skin", skins_dir=tmp_path) is None

    def test_target_mismatch_still_loads(self, tmp_path):
        write_skin(tmp_path, "my-skin")  # targets baseball
        skin = skin_runtime.load_skin("my-skin", sport="hockey",
                                      skins_dir=tmp_path)
        assert skin is not None  # soft warning, not a hard block

    def test_import_error_returns_none(self, tmp_path):
        write_skin(tmp_path, "crashy", body="raise RuntimeError('boom')\n")
        assert skin_runtime.load_skin("crashy", skins_dir=tmp_path) is None

    def test_wrong_class_returns_none(self, tmp_path):
        write_skin(tmp_path, "classless", body="x = 1\n")
        assert skin_runtime.load_skin("classless", skins_dir=tmp_path) is None

    def test_options_are_passed_through(self, tmp_path):
        write_skin(tmp_path, "my-skin")
        skin = skin_runtime.load_skin("my-skin", skins_dir=tmp_path,
                                      options={"accent": [1, 2, 3]})
        assert skin.options == {"accent": [1, 2, 3]}

    def test_sibling_modules_are_isolated_between_skins(self, tmp_path):
        helper = "VALUE = {!r}\n"
        body = (
            "import helpers\n"
            "from src.skin_system.skin_base import ScoreboardSkin\n"
            "class TestSkin(ScoreboardSkin):\n"
            "    def render_live(self, ctx, game):\n"
            "        ctx.logger.info(helpers.VALUE)\n"
            "        self.helper_value = helpers.VALUE\n"
            "        return False\n"
        )
        write_skin(tmp_path, "skin-a", body=body,
                   extra_files={"helpers.py": helper.format("A")})
        write_skin(tmp_path, "skin-b", body=body,
                   extra_files={"helpers.py": helper.format("B")})
        skin_a = skin_runtime.load_skin("skin-a", skins_dir=tmp_path)
        skin_b = skin_runtime.load_skin("skin-b", skins_dir=tmp_path)
        ctx = _make_context()
        skin_a.render_live(ctx, {})
        skin_b.render_live(ctx, {})
        assert skin_a.helper_value == "A"
        assert skin_b.helper_value == "B"


def _make_host(fonts=None):
    host = MagicMock()
    host.sport = "baseball"
    host.sport_key = "mlb"
    host.skin_options = {"accent": True}
    host.fonts = fonts or {"time": ImageFont.load_default()}
    host.logger = logging.getLogger("test_skin_system")
    host.display_manager.width = 128
    host.display_manager.height = 32
    return host


def _make_context(width=128, height=32):
    host = _make_host()
    return skin_runtime.build_context(host, {}, size=(width, height))


class TestBuildContext:
    def test_context_shape(self):
        host = _make_host()
        game = {"home_abbr": "LAD", "away_abbr": "SF"}
        ctx = skin_runtime.build_context(host, game)
        assert (ctx.width, ctx.height) == (128, 32)
        assert ctx.canvas.size == (128, 32)
        assert ctx.sport == "baseball"
        assert ctx.options == {"accent": True}
        assert ctx.layout.bounds.w == 128

    def test_explicit_size_overrides_display(self):
        ctx = skin_runtime.build_context(_make_host(), {}, size=(64, 64))
        assert ctx.canvas.size == (64, 64)

    def test_load_logo_binds_game_and_survives_failure(self):
        host = _make_host()
        host._load_and_resize_logo.side_effect = RuntimeError("disk gone")
        ctx = skin_runtime.build_context(
            host, {"home_id": "1", "home_abbr": "LAD",
                   "home_logo_path": "x.png", "home_logo_url": None})
        assert ctx.load_logo("home") is None      # exception swallowed
        assert ctx.load_logo("elsewhere") is None  # bad side rejected

    def test_draw_helpers_draw_on_canvas(self):
        ctx = _make_context()
        ctx.draw_text("HI", 2, 2, font=ImageFont.load_default())
        fit = ctx.layout.fit_text("42", ctx.layout.bounds)
        ctx.draw_fit(fit, ctx.layout.bounds)
        logo = Image.new("RGBA", (16, 16), (255, 0, 0, 255))
        ctx.draw_image(logo, ctx.layout.bounds.left_col(20))
        ctx.draw_image(None, ctx.layout.bounds)  # None must no-op
        assert ctx.canvas.convert("L").getbbox() is not None


class _FallbackProbe:
    """Bare-bones SportsCore stand-in that exercises the real _render_game."""

    def __init__(self, skin):
        from src.base_classes.sports import SportsCore
        self._cls = SportsCore
        self.SKIN_MODE = "live"
        self.logger = logging.getLogger("test_skin_system")
        self.sport = "baseball"
        self.sport_key = "mlb"
        self.skin_options = {}
        self.fonts = {"time": ImageFont.load_default()}
        self._skin = skin
        self._skin_load_attempted = True
        self._skin_failures = 0
        self._skin_slow_renders = 0
        self._skin_config = "test-skin"
        self.display_manager = MagicMock()
        self.display_manager.width = 128
        self.display_manager.height = 32
        self.display_manager.image = Image.new("RGB", (128, 32))
        self.builtin_calls = 0

    def _resolve_skin_id(self):
        return "test-skin"

    def _draw_scorebug_layout(self, game, force_clear=False):
        self.builtin_calls += 1

    def _render_game(self, game, force_clear=False):
        from src.base_classes.sports import SportsCore
        SportsCore._render_game(self, game, force_clear)

    def _get_skin(self):
        return self._skin


class TestRenderGameFallback:
    def test_skin_handles_render(self):
        class GoodSkin(ScoreboardSkin):
            def render_live(self, ctx, game):
                ctx.draw.rectangle([0, 0, 10, 10], fill=(0, 255, 0))
                return True

        probe = _FallbackProbe(GoodSkin({}, {}))
        probe._render_game({"status_text": "Q1"})
        assert probe.builtin_calls == 0
        probe.display_manager.update_display.assert_called_once()
        assert probe.display_manager.image.convert("L").getbbox() is not None

    def test_skin_declining_falls_back(self):
        probe = _FallbackProbe(ScoreboardSkin({}, {}))  # all renders -> False
        probe._render_game({"status_text": "Q1"})
        assert probe.builtin_calls == 1

    def test_no_skin_falls_back(self):
        probe = _FallbackProbe(None)
        probe._render_game({"status_text": "Q1"})
        assert probe.builtin_calls == 1

    def test_three_strikes_disables_skin(self):
        class BrokenSkin(ScoreboardSkin):
            calls = 0

            def render_live(self, ctx, game):
                BrokenSkin.calls += 1
                raise ValueError("kaboom")

        probe = _FallbackProbe(BrokenSkin({}, {}))
        for i in range(5):
            probe._render_game({"status_text": "Q1"})
        # every render fell back to the built-in layout...
        assert probe.builtin_calls == 5
        # ...and the skin stopped being called after the 3rd failure
        assert BrokenSkin.calls == 3
        assert probe._skin_failures == 3

    def test_skin_cannot_mutate_callers_game_dict(self):
        class MutatingSkin(ScoreboardSkin):
            def render_live(self, ctx, game):
                game.clear()
                game["hacked"] = True
                return True

        probe = _FallbackProbe(MutatingSkin({}, {}))
        game = {"status_text": "Q1", "home_score": "3"}
        probe._render_game(game)
        assert game == {"status_text": "Q1", "home_score": "3"}


class TestSkinModeResolution:
    def _core(self, skin_config, mode="live"):
        from src.base_classes.sports import SportsCore
        probe = _FallbackProbe(None)
        probe.SKIN_MODE = mode
        probe._skin_config = skin_config
        return SportsCore._resolve_skin_id(probe)

    def test_plain_id_applies_to_all_modes(self):
        assert self._core("retro", "live") == "retro"
        assert self._core("retro", "recent") == "retro"

    def test_per_mode_mapping(self):
        cfg = {"live": "retro", "recent": "built-in"}
        assert self._core(cfg, "live") == "retro"
        assert self._core(cfg, "recent") is None
        assert self._core(cfg, "upcoming") is None

    def test_builtin_and_empty_mean_none(self):
        assert self._core("built-in") is None
        assert self._core("") is None
        assert self._core(None) is None


class TestViewModelContract:
    @pytest.mark.parametrize("sport", ["baseball", "basketball", "football", "hockey"])
    @pytest.mark.parametrize("mode", ["live", "recent", "upcoming"])
    def test_fixtures_carry_all_guaranteed_keys(self, sport, mode):
        with open(FIXTURES_DIR / f"{sport}_{mode}.json") as f:
            game = json.load(f)
        missing = [k for k in GUARANTEED_KEYS if k not in game]
        assert not missing, f"{sport}_{mode} fixture missing {missing}"

    def test_extractor_produces_guaranteed_keys(self):
        """The real extractor's output must be a superset of the documented
        contract — this is the test that catches accidental renames."""
        import inspect
        from src.base_classes.sports import SportsCore
        source = inspect.getsource(SportsCore._extract_game_details_common)
        missing = [k for k in GUARANTEED_KEYS if f'"{k}"' not in source]
        assert not missing, (
            f"_extract_game_details_common no longer emits {missing}. "
            "These keys are part of the frozen skin view-model contract "
            "(VIEW_MODEL_VERSION) — renaming or removing them breaks every "
            "published skin. Add a compat shim or bump the major version.")


class TestPluginMatching:
    def test_matches_by_sport_token_and_sport_key(self, tmp_path):
        write_skin(tmp_path, "bb-skin")  # targets sports=["baseball"]
        skins = skin_runtime.discover_skins(tmp_path, force_refresh=True)
        assert "bb-skin" in skin_runtime.skins_for_plugin("baseball-scoreboard", skins)
        assert "bb-skin" not in skin_runtime.skins_for_plugin("football-scoreboard", skins)

    def test_matches_by_explicit_plugin_list(self, tmp_path):
        skin_dir = write_skin(tmp_path, "exact-skin")
        manifest = json.loads((skin_dir / "skin.json").read_text())
        manifest["targets"] = {"plugins": ["my-custom-plugin"]}
        (skin_dir / "skin.json").write_text(json.dumps(manifest))
        skins = skin_runtime.discover_skins(tmp_path, force_refresh=True)
        assert "exact-skin" in skin_runtime.skins_for_plugin("my-custom-plugin", skins)
        assert "exact-skin" not in skin_runtime.skins_for_plugin("baseball-scoreboard", skins)


class TestSchemaInjection:
    def _manager(self):
        from src.plugin_system.schema_manager import SchemaManager
        return SchemaManager()

    def test_injects_enum_with_installed_and_configured_skins(self):
        sm = self._manager()
        schema = {"type": "object", "properties": {}}
        out = sm.inject_skin_selector(schema, "baseball-scoreboard",
                                      current_value="gone-skin")
        enum = out["properties"]["skin"]["enum"]
        assert enum[0] == "built-in"
        assert "example-classic-baseball" in enum
        # an uninstalled-but-configured skin must stay selectable so the
        # saved config never becomes invalid in the UI
        assert "gone-skin" in enum
        assert "skin" not in schema["properties"]  # source schema untouched

    def test_no_matching_skins_leaves_schema_alone(self):
        sm = self._manager()
        schema = {"type": "object", "properties": {}}
        out = sm.inject_skin_selector(schema, "totally-unrelated-plugin")
        assert "skin" not in out.get("properties", {})

    def test_validation_accepts_skin_keys_without_enum(self):
        sm = self._manager()
        schema = {"type": "object", "properties": {"foo": {"type": "string"}}}
        ok, errors = sm.validate_config_against_schema(
            {"skin": "any-id-even-uninstalled", "skin_options": {"x": 1}},
            schema, "baseball-scoreboard")
        assert ok, errors
        ok, errors = sm.validate_config_against_schema(
            {"skin": {"live": "a", "recent": "built-in"}}, schema, "p")
        assert ok, errors


class TestExampleSkin:
    @pytest.mark.parametrize("mode", ["live", "recent", "upcoming"])
    @pytest.mark.parametrize("size", [(128, 32), (64, 32), (128, 64)])
    def test_renders_all_modes_and_sizes(self, mode, size):
        skin = skin_runtime.load_skin("example-classic-baseball", sport="baseball")
        assert skin is not None
        host = _make_host()
        host._load_and_resize_logo.return_value = Image.new("RGBA", (32, 32), (200, 0, 0, 255))
        with open(FIXTURES_DIR / f"baseball_{mode}.json") as f:
            game = json.load(f)
        ctx = skin_runtime.build_context(host, game, size=size)
        assert getattr(skin, f"render_{mode}")(ctx, game) is True
        assert ctx.canvas.convert("L").getbbox() is not None
