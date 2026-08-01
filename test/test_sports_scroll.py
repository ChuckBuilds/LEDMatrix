"""Tests for src/common/sports_scroll.py (phase B3).

The module is drawn along the split the survey found: the orchestration layer
is shared, the content layer is not. Two things are worth asserting beyond
"it works":

* ``prepare_scroll_content`` must stay an override point — a base class that
  quietly rendered *something* would let a plugin ship a blank scroll.
* ``target_fps`` must actually reach the helper. That is the whole reason this
  module exists upstream; the bundled plugin copies hardcode ~100 FPS.
"""

import logging
import sys
from unittest.mock import MagicMock

import pytest
from PIL import Image

sys.modules.setdefault("rgbmatrix", MagicMock())

from src.common.sports_scroll import (  # noqa: E402
    DEFAULT_SCROLL_SETTINGS,
    MAX_PIXELS_PER_FRAME,
    MIN_PIXELS_PER_FRAME,
    SportsScrollDisplay,
    SportsScrollDisplayManager,
)

LOGGER = logging.getLogger("test.sports_scroll")


@pytest.fixture
def display_manager():
    manager = MagicMock()
    manager.matrix.width = 128
    manager.matrix.height = 32
    return manager


@pytest.fixture(autouse=True)
def fake_scroll_helper(monkeypatch):
    """Replace ScrollHelper with a recording double.

    The real helper is covered by test_scroll_helper.py; here what matters is
    *which* calls this module makes and with what values.
    """
    created = []

    def _factory(width, height, logger):
        helper = MagicMock()
        helper.width, helper.height = width, height
        helper.cached_image = None
        helper.is_scroll_complete.return_value = False
        helper.get_dynamic_duration.return_value = 42
        helper.get_scroll_info.return_value = {"position": 0}
        created.append(helper)
        return helper

    monkeypatch.setattr("src.common.sports_scroll.ScrollHelper", _factory)
    return created


class _Display(SportsScrollDisplay):
    """A minimal concrete subclass, as a plugin would write it."""

    SCROLL_LEAGUE_KEYS = ("nhl", "ncaa_mens")

    def prepare_scroll_content(self, games, game_type, leagues, rankings_cache=None):
        self._current_games = list(games)
        self._current_game_type = game_type
        self._current_leagues = list(leagues)
        self.scroll_helper.cached_image = Image.new("RGB", (400, 32))
        return bool(games)


class _Manager(SportsScrollDisplayManager):
    display_class = _Display


@pytest.fixture
def build(display_manager):
    def _build(config=None, global_config=None, cls=_Display):
        return cls(display_manager, config or {}, LOGGER, global_config=global_config)
    return _build


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------

class TestConstruction:
    def test_dimensions_come_from_the_matrix(self, build):
        display = build()
        assert (display.display_width, display.display_height) == (128, 32)

    def test_dimensions_fall_back_when_there_is_no_matrix(self, display_manager):
        display_manager.matrix = None
        display_manager.width, display_manager.height = 256, 64
        display = _Display(display_manager, {}, LOGGER)
        assert (display.display_width, display.display_height) == (256, 64)

    def test_final_fallback_dimensions(self):
        """A display manager exposing neither must not crash construction."""
        bare = MagicMock(spec=[])
        display = _Display(bare, {}, LOGGER)
        assert (display.display_width, display.display_height) == (128, 32)

    def test_global_config_is_optional(self, build):
        """An older caller that doesn't pass it keeps working."""
        assert build().global_config == {}

    def test_state_starts_empty(self, build):
        display = build()
        assert display._current_games == []
        assert display._vegas_content_items == []
        assert display.get_current_game_count() == 0


# ---------------------------------------------------------------------------
# Settings resolution — the league ladder
# ---------------------------------------------------------------------------

class TestScrollSettings:
    def test_defaults_when_nothing_is_configured(self, build):
        settings = build()._get_scroll_settings()
        for key, value in DEFAULT_SCROLL_SETTINGS.items():
            assert settings[key] == value

    def test_card_width_defaults_to_the_panel_width(self, build):
        assert build()._get_scroll_settings()["game_card_width"] == 128

    def test_named_league_wins(self, build):
        display = build({"nhl": {"scroll_settings": {"scroll_speed": 10}},
                         "ncaa_mens": {"scroll_settings": {"scroll_speed": 20}}})
        assert display._get_scroll_settings("ncaa_mens")["scroll_speed"] == 20

    def test_ladder_is_walked_in_order(self, build):
        """The only reason the eight plugin copies differed: which league keys
        to try, and in what order."""
        display = build({"ncaa_mens": {"scroll_settings": {"scroll_speed": 20}},
                         "nhl": {"scroll_settings": {"scroll_speed": 10}}})
        assert display._get_scroll_settings()["scroll_speed"] == 10

    def test_ladder_falls_through_to_the_next_key(self, build):
        display = build({"ncaa_mens": {"scroll_settings": {"scroll_speed": 20}}})
        assert display._get_scroll_settings()["scroll_speed"] == 20

    def test_overrides_merge_onto_defaults(self, build):
        display = build({"nhl": {"scroll_settings": {"scroll_speed": 10}}})
        settings = display._get_scroll_settings()
        assert settings["scroll_speed"] == 10
        assert settings["gap_between_games"] == DEFAULT_SCROLL_SETTINGS[
            "gap_between_games"]

    def test_empty_override_does_not_shadow_the_next_candidate(self, build):
        display = build({"nhl": {"scroll_settings": {}},
                         "ncaa_mens": {"scroll_settings": {"scroll_speed": 20}}})
        assert display._get_scroll_settings()["scroll_speed"] == 20

    def test_single_block_config_shape(self, build):
        """The afl/nrl/soccer shape: one scroll_mode block, no league concept."""
        class _Single(_Display):
            SCROLL_LEAGUE_KEYS = ()
            SCROLL_CONFIG_KEY = "scroll_mode"

        display = build({"scroll_mode": {"scroll_speed": 33}}, cls=_Single)
        assert display._get_scroll_settings()["scroll_speed"] == 33

    def test_defaults_are_overridable_by_a_subclass(self, build):
        class _Wide(_Display):
            def scroll_settings_defaults(self):
                return {**super().scroll_settings_defaults(),
                        "gap_between_games": 24}

        assert build(cls=_Wide)._get_scroll_settings()["gap_between_games"] == 24

    def test_a_null_league_block_is_tolerated(self, build):
        """`config['nhl'] = None` appears in hand-edited configs."""
        display = build({"nhl": None})
        assert display._get_scroll_settings()["scroll_speed"] == 50.0


# ---------------------------------------------------------------------------
# Helper configuration — including the reason this module exists upstream
# ---------------------------------------------------------------------------

class TestConfigureScrollHelper:
    def test_speed_is_converted_to_pixels_per_frame(self, build):
        display = build({"nhl": {"scroll_settings": {
            "scroll_speed": 100.0, "scroll_delay": 0.02}}})
        # 100 px/s * 0.02 s/frame = 2 px/frame
        display.scroll_helper.set_scroll_speed.assert_called_with(2.0)

    def test_conversion_is_clamped_low(self, build):
        display = build({"nhl": {"scroll_settings": {
            "scroll_speed": 0.001, "scroll_delay": 0.001}}})
        display.scroll_helper.set_scroll_speed.assert_called_with(MIN_PIXELS_PER_FRAME)

    def test_conversion_is_clamped_high(self, build):
        display = build({"nhl": {"scroll_settings": {
            "scroll_speed": 5000.0, "scroll_delay": 0.5}}})
        display.scroll_helper.set_scroll_speed.assert_called_with(MAX_PIXELS_PER_FRAME)

    def test_zero_delay_assumes_a_pacing_instead_of_dividing_by_zero(self, build):
        display = build({"nhl": {"scroll_settings": {
            "scroll_speed": 100.0, "scroll_delay": 0}}})
        display.scroll_helper.set_scroll_speed.assert_called_with(1.0)

    def test_frame_based_scrolling_is_enabled(self, build):
        build().scroll_helper.set_frame_based_scrolling.assert_called_once_with(True)

    def test_dynamic_duration_settings_are_applied(self, build):
        display = build({"nhl": {"scroll_settings": {
            "dynamic_duration": False, "min_duration": 5, "max_duration": 50}}})
        _, kwargs = display.scroll_helper.set_dynamic_duration_settings.call_args
        assert kwargs["enabled"] is False
        assert kwargs["min_duration"] == 5
        assert kwargs["max_duration"] == 50

    def test_target_fps_reaches_the_helper(self, build):
        """The whole point of upstreaming: the bundled copies hardcode ~100 FPS
        via scroll_delay and never consult the global target."""
        display = build(global_config={"target_fps": 120})
        display.scroll_helper.set_target_fps.assert_called_once_with(120.0)

    def test_legacy_key_is_honored(self, build):
        display = build(global_config={"scroll_target_fps": 90})
        display.scroll_helper.set_target_fps.assert_called_once_with(90.0)

    def test_modern_key_wins_over_legacy(self, build):
        display = build(global_config={"target_fps": 120, "scroll_target_fps": 90})
        display.scroll_helper.set_target_fps.assert_called_once_with(120.0)

    def test_absent_target_fps_leaves_config_pacing_alone(self, build):
        build().scroll_helper.set_target_fps.assert_not_called()

    @pytest.mark.parametrize("bad", ["fast", None, {}, [], "", 0])
    def test_unusable_target_fps_degrades_instead_of_raising(self, build, bad):
        """A malformed global config must cost the FPS target, not the display."""
        display = build(global_config={"target_fps": bad})
        display.scroll_helper.set_target_fps.assert_not_called()

    def test_string_digits_are_accepted(self, build):
        display = build(global_config={"target_fps": "120"})
        display.scroll_helper.set_target_fps.assert_called_once_with(120.0)

    def test_fps_clamping_is_left_to_the_helper(self, build):
        """Deliberately not clamped here — a second copy of the range would
        drift from ScrollHelper.set_target_fps."""
        display = build(global_config={"target_fps": 5000})
        display.scroll_helper.set_target_fps.assert_called_once_with(5000.0)


# ---------------------------------------------------------------------------
# The content seam
# ---------------------------------------------------------------------------

class TestContentIsAnOverridePoint:
    def test_base_refuses_to_render(self, display_manager):
        """Eight plugins have eight different bodies for this; a base class that
        rendered *something* would let a plugin ship a silently blank scroll."""
        display = SportsScrollDisplay(display_manager, {}, LOGGER)
        with pytest.raises(NotImplementedError, match="prepare_scroll_content"):
            display.prepare_scroll_content([], "live", [])

    def test_the_error_names_the_offending_class(self, display_manager):
        class Incomplete(SportsScrollDisplay):
            pass

        with pytest.raises(NotImplementedError, match="Incomplete"):
            Incomplete(display_manager, {}, LOGGER).prepare_scroll_content(
                [], "live", [])

    def test_separator_icons_default_to_a_no_op(self, build):
        assert build()._separator_icons == {}


# ---------------------------------------------------------------------------
# Frame pumping
# ---------------------------------------------------------------------------

class TestFramePumping:
    def test_no_content_means_no_frame(self, build):
        assert build().display_scroll_frame() is False

    def test_a_frame_is_drawn_and_pushed(self, build):
        display = build()
        display.prepare_scroll_content([{"id": "g1"}], "live", ["nhl"])
        display.scroll_helper.get_visible_portion.return_value = Image.new(
            "RGB", (128, 32))
        assert display.display_scroll_frame() is True
        display.scroll_helper.update_scroll_position.assert_called_once()
        display.display_manager.update_display.assert_called_once()

    def test_no_visible_portion_means_no_frame(self, build):
        display = build()
        display.prepare_scroll_content([{"id": "g1"}], "live", ["nhl"])
        display.scroll_helper.get_visible_portion.return_value = None
        assert display.display_scroll_frame() is False

    def test_a_display_failure_is_contained(self, build):
        """A display error must not propagate into the plugin's render loop."""
        display = build()
        display.prepare_scroll_content([{"id": "g1"}], "live", ["nhl"])
        display.scroll_helper.get_visible_portion.return_value = Image.new(
            "RGB", (128, 32))
        display.display_manager.update_display.side_effect = RuntimeError("boom")
        assert display.display_scroll_frame() is False

    def test_frames_are_counted(self, build):
        display = build()
        display.prepare_scroll_content([{"id": "g1"}], "live", ["nhl"])
        display.scroll_helper.get_visible_portion.return_value = Image.new(
            "RGB", (128, 32))
        for _ in range(3):
            display.display_scroll_frame()
        assert display._frame_count == 3

    def test_progress_logging_is_throttled(self, build):
        display = build()
        display._log_interval = 10_000
        display._last_log_time = 0
        display._log_scroll_progress()
        first = display._last_log_time
        display._log_scroll_progress()
        assert display._last_log_time == first


class TestLifecycle:
    def test_reset_keeps_content_but_rewinds(self, build):
        display = build()
        display.prepare_scroll_content([{"id": "g1"}], "live", ["nhl"])
        display._frame_count = 9
        display.reset_scroll()
        display.scroll_helper.reset_scroll.assert_called_once()
        assert display._frame_count == 0
        assert display._current_games, "reset must not drop content"

    def test_clear_drops_everything(self, build):
        display = build()
        display.prepare_scroll_content([{"id": "g1"}], "live", ["nhl"])
        display._vegas_content_items = [Image.new("RGB", (8, 8))]
        display.clear()
        display.scroll_helper.clear_cache.assert_called_once()
        assert display._current_games == []
        assert display._current_game_type == ""
        assert display._vegas_content_items == []

    def test_completion_delegates_to_the_helper(self, build):
        display = build()
        display.scroll_helper.is_scroll_complete.return_value = True
        assert display.is_scroll_complete() is True

    def test_dynamic_duration_delegates(self, build):
        assert build().get_dynamic_duration() == 42

    def test_has_cached_content(self, build):
        display = build()
        assert display.has_cached_content() is False
        display.prepare_scroll_content([{"id": "g1"}], "live", ["nhl"])
        assert display.has_cached_content() is True

    def test_scroll_info_merges_helper_and_local_state(self, build):
        display = build()
        display.prepare_scroll_content([{"id": "g1"}], "live", ["nhl"])
        info = display.get_scroll_info()
        assert info["position"] == 0          # from the helper
        assert info["game_count"] == 1        # from this display
        assert info["leagues"] == ["nhl"]

    def test_leagues_are_returned_as_a_copy(self, build):
        display = build()
        display.prepare_scroll_content([{"id": "g1"}], "live", ["nhl"])
        display.get_current_leagues().append("mutated")
        assert display.get_current_leagues() == ["nhl"]


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------

class TestManager:
    @pytest.fixture
    def manager(self, display_manager):
        return _Manager(display_manager, {}, LOGGER, global_config={"target_fps": 120})

    def test_displays_are_created_lazily_and_reused(self, manager):
        first = manager.get_scroll_display("live")
        assert manager.get_scroll_display("live") is first
        assert isinstance(first, _Display)

    def test_each_game_type_gets_its_own(self, manager):
        assert manager.get_scroll_display("live") is not manager.get_scroll_display(
            "recent")

    def test_global_config_is_threaded_to_children(self, manager):
        """A missed hand-off here is exactly how the plugin copies ended up
        never honoring target_fps."""
        child = manager.get_scroll_display("live")
        assert child.global_config == {"target_fps": 120}
        child.scroll_helper.set_target_fps.assert_called_once_with(120.0)

    def test_prepare_sets_the_active_type(self, manager):
        assert manager.prepare_and_display([{"id": "g1"}], "live", ["nhl"]) is True
        assert manager._current_game_type == "live"

    def test_failed_prepare_does_not_become_active(self, manager):
        assert manager.prepare_and_display([], "live", ["nhl"]) is False
        assert manager._current_game_type is None

    def test_display_frame_uses_the_active_type(self, manager):
        manager.prepare_and_display([{"id": "g1"}], "live", ["nhl"])
        display = manager.get_scroll_display("live")
        display.scroll_helper.get_visible_portion.return_value = Image.new(
            "RGB", (128, 32))
        assert manager.display_frame() is True

    def test_display_frame_with_no_active_type(self, manager):
        assert manager.display_frame() is False

    def test_display_frame_for_an_unknown_type(self, manager):
        assert manager.display_frame("never-prepared") is False

    def test_completion_is_true_when_there_is_nothing_to_scroll(self, manager):
        """A caller waiting on completion must never be wedged by absence."""
        assert manager.is_complete() is True
        assert manager.is_complete("never-prepared") is True

    def test_completion_delegates_to_the_active_display(self, manager):
        manager.prepare_and_display([{"id": "g1"}], "live", ["nhl"])
        manager.get_scroll_display("live").scroll_helper \
            .is_scroll_complete.return_value = True
        assert manager.is_complete() is True

    def test_clear_all_clears_every_display(self, manager):
        manager.prepare_and_display([{"id": "g1"}], "live", ["nhl"])
        manager.prepare_and_display([{"id": "g2"}], "recent", ["nhl"])
        manager.clear_all()
        assert manager._current_game_type is None
        for game_type in ("live", "recent"):
            assert manager.get_scroll_display(game_type)._current_games == []

    def test_vegas_items_are_collected_across_displays(self, manager):
        for game_type in ("live", "recent"):
            manager.get_scroll_display(game_type)._vegas_content_items = [
                Image.new("RGB", (8, 8))]
        assert len(manager.get_all_vegas_content_items()) == 2

    def test_vegas_collection_tolerates_empty_displays(self, manager):
        manager.get_scroll_display("live")
        assert manager.get_all_vegas_content_items() == []

    def test_display_class_is_the_subclass_seam(self, display_manager):
        class Other(_Display):
            pass

        class OtherManager(SportsScrollDisplayManager):
            display_class = Other

        manager = OtherManager(display_manager, {}, LOGGER)
        assert isinstance(manager.get_scroll_display("live"), Other)


# ---------------------------------------------------------------------------
# Integration — against the real ScrollHelper
# ---------------------------------------------------------------------------

class _RealDisplay(SportsScrollDisplay):
    """Builds a strip the way a plugin would, via the real helper API."""

    SCROLL_LEAGUE_KEYS = ("nhl",)

    def prepare_scroll_content(self, games, game_type, leagues, rankings_cache=None):
        settings = self._get_scroll_settings()
        self.scroll_helper.create_scrolling_image(
            [Image.new("RGB", (100, 32), (20, 20, 20)) for _ in games],
            item_gap=settings["gap_between_games"],
        )
        self._current_games = list(games)
        self._current_game_type = game_type
        self._current_leagues = list(leagues)
        return bool(games)


class _RealManager(SportsScrollDisplayManager):
    display_class = _RealDisplay


class TestAgainstTheRealScrollHelper:
    """The mocked tests above pin *which* calls this module makes; these pin
    that those calls exist and mean what we think. Without this, a rename in
    ScrollHelper would sail past a suite built entirely on MagicMock."""

    @pytest.fixture
    def real(self, display_manager, monkeypatch):
        from src.common.scroll_helper import ScrollHelper
        monkeypatch.setattr("src.common.sports_scroll.ScrollHelper", ScrollHelper)
        return _RealManager(
            display_manager,
            {"nhl": {"scroll_settings": {"scroll_speed": 500.0,
                                         "scroll_delay": 0.001}}},
            LOGGER,
            global_config={"target_fps": 120},
        )

    def test_configuration_lands_on_the_real_helper(self, real):
        helper = real.get_scroll_display("live").scroll_helper
        assert helper.target_fps == 120.0
        assert helper.frame_based_scrolling is True
        assert helper.scroll_speed == pytest.approx(0.5)  # 500 px/s * 0.001 s

    def test_a_strip_is_built_and_scrolls_to_completion(self, real):
        import time

        assert real.prepare_and_display(
            [{"id": "a"}, {"id": "b"}, {"id": "c"}], "live", ["nhl"]) is True
        display = real.get_scroll_display("live")
        # 3 cards of 100px + gaps, so the strip is wider than the 128px panel.
        assert display.scroll_helper.cached_image.width > 128

        # Frame-based scrolling is wall-clock gated on scroll_delay, so the
        # loop has to actually pass time rather than spin.
        deadline = time.time() + 20
        while not real.is_complete() and time.time() < deadline:
            real.display_frame()
            time.sleep(0.0012)

        assert real.is_complete() is True
        assert display.scroll_helper.scroll_position > 128

    def test_dynamic_duration_is_a_real_number(self, real):
        real.prepare_and_display([{"id": "a"}], "live", ["nhl"])
        assert real.get_scroll_display("live").get_dynamic_duration() > 0
