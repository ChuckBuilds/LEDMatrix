"""Tests for src/common/sports_scroll.py (phase B3).

The module is drawn along the split the survey found: the orchestration layer
is shared, the content layer is not. Two things are worth asserting beyond
"it works":

* ``prepare_scroll_content`` must stay an override point — a base class that
  quietly rendered *something* would let a plugin ship a blank scroll.
* Speed must depend only on ``scroll_speed`` and the panel refresh. The helper
  steps a fixed number of whole pixels per presented frame and the panel
  presents at its refresh divided by the frame hold, so a ladder computed for
  any other rate -- the global ``target_fps`` used to be one -- plays back at
  the wrong speed.
"""

import logging
import sys
from unittest.mock import MagicMock

import pytest
from PIL import Image

sys.modules.setdefault("rgbmatrix", MagicMock())

from src.common.sports_scroll import (  # noqa: E402
    DEFAULT_SCROLL_SETTINGS,
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
        assert (display._get_scroll_settings()["scroll_speed"]
                == DEFAULT_SCROLL_SETTINGS["scroll_speed"])


# ---------------------------------------------------------------------------
# Helper configuration — including the reason this module exists upstream
# ---------------------------------------------------------------------------

class TestConfigureScrollHelper:
    """Pacing now comes from scroll_config, the same resolver every other
    scrolling plugin uses. What this class guards is the translation into it,
    because the two modules read identically-named keys differently."""

    def test_config_speed_is_pixels_per_second_not_pixels_per_step(self, build):
        """The collision that makes a naive hand-off wrong by 1/scroll_delay.

        sports config states scroll_speed in px/SECOND and uses scroll_delay
        only to reach px/frame. scroll_config states it in px per STEP, so it
        computes px/s as speed/delay. Handing this module's settings dict
        straight to the resolver turns 50 px/s into 5000 px/s, which its own
        bounds then clamp to 500 -- a tenfold speed-up on every scoreboard.
        """
        display = build({"nhl": {"scroll_settings": {
            "scroll_speed": 50.0, "scroll_delay": 0.01}}})
        applied = display.scroll_helper.set_scroll_speed.call_args[0][0]
        assert applied == pytest.approx(50.0, abs=1.0), (
            f"applied {applied} px/s; 500 means the settings dict was passed "
            "through to the resolver instead of a plain px/s")

    def test_speed_is_snapped_to_whole_pixel_motion(self, build):
        """50 px/s on a 100Hz panel is half a pixel per refresh, which cannot
        render as motion -- it alternates 0 and 1 px steps and judders. The
        ladder keeps the speed and holds each frame for two refreshes."""
        display = build()
        assert display._scroll_settings.frame_hold == 2
        assert display._scroll_settings.pixels_per_second == pytest.approx(50.0)

    def test_the_resolved_hold_is_what_gets_published(self, build):
        display = build()
        assert display._scroll_frame_hold() == 2

    def test_hold_defaults_to_one_before_anything_is_resolved(self, display_manager):
        bare = SportsScrollDisplay.__new__(SportsScrollDisplay)
        assert SportsScrollDisplay._scroll_frame_hold(bare) == 1

    def test_helper_steps_whole_pixels_per_presented_frame(self, build):
        """No wall clock: the helper advances the ladder's whole-pixel step on
        every presented frame, and the frame hold sets how often that is."""
        display = build()
        helper = display.scroll_helper
        helper.set_frame_based_scrolling.assert_called_once_with(False)
        helper.set_pixels_per_frame.assert_called_once_with(
            display._scroll_settings.crisp.pixels_per_frame)

    def test_dynamic_duration_settings_are_applied(self, build):
        display = build({"nhl": {"scroll_settings": {
            "dynamic_duration": False, "min_duration": 5, "max_duration": 50}}})
        _, kwargs = display.scroll_helper.set_dynamic_duration_settings.call_args
        assert kwargs["enabled"] is False
        assert kwargs["min_duration"] == 5
        assert kwargs["max_duration"] == 50

    def test_zero_delay_still_means_pixels_per_frame(self, build):
        """The one case where scroll_speed is not already px/s."""
        display = build({"nhl": {"scroll_settings": {
            "scroll_speed": 1.0, "scroll_delay": 0}}})
        applied = display.scroll_helper.set_scroll_speed.call_args[0][0]
        assert applied == pytest.approx(100.0, abs=1.0)

    @pytest.mark.parametrize("global_config", [
        {},
        {"target_fps": 60},
        {"target_fps": 100},
        {"target_fps": 120},
        {"target_fps": 200},
        {"scroll_target_fps": 90},
        {"target_fps": 120, "scroll_target_fps": 90},
    ])
    def test_target_fps_does_not_change_scroll_speed(self, build, global_config):
        """The General tab's "Scroll Frame Rate" is not a speed control.

        It used to set the refresh the ladder was computed against, while the
        panel kept presenting at its real 100Hz: 60 doubled every scoreboard's
        speed and 200 halved it."""
        display = build(global_config=global_config)
        assert display._resolve_refresh_hz() == 100.0
        assert display._scroll_settings.pixels_per_second == pytest.approx(50.0)
        assert display._scroll_settings.frame_hold == 2
        assert display._scroll_settings.crisp.pixels_per_frame == 1

    def test_configured_hardware_refresh_sets_the_ladder(self, build):
        display = build(global_config={
            "target_fps": 60,
            "display": {"hardware": {"limit_refresh_rate_hz": 120}}})
        assert display._resolve_refresh_hz() == 120.0
        assert display._scroll_settings.crisp.refresh_hz == 120.0

    def test_display_manager_refresh_wins_over_global_config(self, build,
                                                             display_manager):
        """The display manager reports the rate the panel is driven at."""
        display_manager.refresh_hz = 60.0
        display = build(global_config={
            "target_fps": 200,
            "display": {"hardware": {"limit_refresh_rate_hz": 120}}})
        assert display._resolve_refresh_hz() == 60.0

    @pytest.mark.parametrize("reported", [True, 0, -5, "100", None])
    def test_unusable_display_manager_refresh_falls_back(self, build,
                                                          display_manager,
                                                          reported):
        display_manager.refresh_hz = reported
        display = build(global_config={
            "display": {"hardware": {"limit_refresh_rate_hz": 120}}})
        assert display._resolve_refresh_hz() == 120.0

    @pytest.mark.parametrize("bad", ["fast", None, {}, [], "", 0])
    def test_unusable_target_fps_is_harmless(self, build, bad):
        """A malformed global config must not cost the display."""
        display = build(global_config={"target_fps": bad})
        assert display._scroll_settings.pixels_per_second == pytest.approx(50.0)

    @pytest.mark.parametrize("delay", [0.001, 0.01, 0.05, 0.1])
    def test_scroll_delay_is_ignored_for_pacing(self, build, delay):
        """Kept in the settings for compatibility; it does not change speed."""
        display = build({"nhl": {"scroll_settings": {
            "scroll_speed": 50.0, "scroll_delay": delay}}})
        assert display._scroll_settings.pixels_per_second == pytest.approx(50.0)
        assert display._scroll_settings.frame_hold == 2

    @pytest.mark.parametrize("bad", [None, "fast", {}, []])
    def test_unusable_scroll_speed_degrades_instead_of_crashing(self, build, bad):
        """`.get(key, default)` only helps when the key is *absent*. A key
        present with null reaches the arithmetic and raises inside __init__,
        taking the whole display down before it renders anything."""
        display = build({"nhl": {"scroll_settings": {"scroll_speed": bad}}})
        applied = display.scroll_helper.set_scroll_speed.call_args[0][0]
        assert applied == pytest.approx(50.0, abs=1.0)

    @pytest.mark.parametrize("bad", [None, "slow", {}])
    def test_unusable_scroll_delay_degrades_instead_of_crashing(self, build, bad):
        display = build({"nhl": {"scroll_settings": {"scroll_delay": bad}}})
        applied = display.scroll_helper.set_scroll_speed.call_args[0][0]
        assert applied == pytest.approx(50.0, abs=1.0)

    def test_numeric_strings_are_accepted(self, build):
        display = build({"nhl": {"scroll_settings": {
            "scroll_speed": "100", "scroll_delay": "0.02"}}})
        applied = display.scroll_helper.set_scroll_speed.call_args[0][0]
        assert applied == pytest.approx(100.0, abs=1.0)


class TestScrollingStateIsPublished:
    """The core has to be told, or the frame hold is never applied and
    deferred work runs in the middle of the scroll."""

    def _drawable(self, display):
        display.scroll_helper.cached_image = Image.new("RGB", (400, 32))
        display.scroll_helper.get_visible_portion.return_value = Image.new(
            "RGB", (128, 32))

    def test_a_drawn_frame_declares_the_hold(self, build, display_manager):
        display = build()
        self._drawable(display)
        assert display.display_scroll_frame() is True
        display_manager.set_scrolling_state.assert_called_with(
            True, frame_hold=display._scroll_frame_hold())

    def test_completion_releases_the_state(self, build, display_manager):
        display = build()
        display.scroll_helper.is_scroll_complete.return_value = True
        assert display.is_scroll_complete() is True
        display_manager.set_scrolling_state.assert_called_with(False)

    def test_an_incomplete_scroll_does_not_release(self, build, display_manager):
        display = build()
        display.scroll_helper.is_scroll_complete.return_value = False
        display.is_scroll_complete()
        assert (False,) not in [c.args for c in
                                display_manager.set_scrolling_state.call_args_list]

    def test_clearing_releases_the_state(self, build, display_manager):
        display = build()
        display.clear()
        display_manager.set_scrolling_state.assert_called_with(False)

    def test_an_older_core_without_the_call_is_tolerated(self, build):
        """Plugins ship independently of the core they run against."""
        display = build()
        del display.display_manager.set_scrolling_state
        self._drawable(display)
        assert display.display_scroll_frame() is True



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

    @pytest.mark.parametrize("failing", ["update_scroll_position",
                                         "get_visible_portion"])
    def test_a_scroll_helper_failure_is_contained_too(self, build, failing):
        """These ran outside the try, so a raise there reached the caller's
        frame loop despite the stated promise that none can."""
        display = build()
        display.prepare_scroll_content([{"id": "g1"}], "live", ["nhl"])
        getattr(display.scroll_helper, failing).side_effect = RuntimeError("boom")
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
        return _Manager(display_manager, {}, LOGGER, global_config={
            "target_fps": 120,
            "display": {"hardware": {"limit_refresh_rate_hz": 120}}})

    def test_displays_are_created_lazily_and_reused(self, manager):
        first = manager.get_scroll_display("live")
        assert manager.get_scroll_display("live") is first
        assert isinstance(first, _Display)

    def test_each_game_type_gets_its_own(self, manager):
        assert manager.get_scroll_display("live") is not manager.get_scroll_display(
            "recent")

    def test_global_config_is_threaded_to_children(self, manager):
        """The child needs the global config to find the panel refresh when
        the display manager cannot report one."""
        child = manager.get_scroll_display("live")
        assert child.global_config["target_fps"] == 120
        # What proves the hand-off is that the child reasons about the 120Hz
        # hardware refresh from that config (target_fps plays no part).
        assert child._resolve_refresh_hz() == 120.0

    def test_prepare_sets_the_active_type(self, manager):
        assert manager.prepare_and_display([{"id": "g1"}], "live", ["nhl"]) is True
        assert manager._current_game_type == "live"

    def test_failed_prepare_does_not_become_active(self, manager):
        assert manager.prepare_and_display([], "live", ["nhl"]) is False
        assert not manager._current_game_type

    def test_a_raising_subclass_does_not_escape_the_orchestration(self, manager):
        """prepare_scroll_content is subclass code building cards from feed
        data. One sport's bad payload must not take down the others."""
        display = manager.get_scroll_display("live")
        display.prepare_scroll_content = MagicMock(side_effect=KeyError("status"))
        assert manager.prepare_and_display([{"id": "g1"}], "live", ["nhl"]) is False
        assert not manager._current_game_type

    def test_empty_game_type_sentinel_matches_the_display(self, manager):
        """Both classes must spell 'nothing active' the same way; two spellings
        across two classes is a trap for anyone comparing their state."""
        manager.prepare_and_display([{"id": "g1"}], "live", ["nhl"])
        manager.clear_all()
        assert (manager._current_game_type
                == manager.get_scroll_display("live")._current_game_type == "")

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
        assert not manager._current_game_type
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
        display = real.get_scroll_display("live")
        helper = display.scroll_helper
        # 500 px/s on the default 100Hz panel is a whole number of pixels per
        # refresh, and the helper steps that many pixels per presented frame.
        assert helper.frame_based_scrolling is False
        assert helper.scroll_speed == pytest.approx(
            display._scroll_settings.pixels_per_second)
        assert helper.scroll_speed == pytest.approx(500.0, abs=25.0)
        assert helper.fixed_pixels_per_frame == (
            display._scroll_settings.crisp.pixels_per_frame)
        # target_fps is informational: the presentation rate the hold gives.
        assert helper.target_fps == pytest.approx(
            100.0 / display._scroll_settings.frame_hold, abs=1.0)

    def test_a_strip_is_built_and_scrolls_to_completion(self, real):
        import time

        assert real.prepare_and_display(
            [{"id": "a"}, {"id": "b"}, {"id": "c"}], "live", ["nhl"]) is True
        display = real.get_scroll_display("live")
        # 3 cards of 100px + gaps, so the strip is wider than the 128px panel.
        assert display.scroll_helper.cached_image.width > 128

        # The helper steps a fixed whole-pixel amount per presented frame and
        # consults no clock, so the scroll completes in a bounded number of
        # frames however fast this loop spins.
        deadline = time.time() + 20
        frames = 0
        while not real.is_complete() and time.time() < deadline and frames < 10000:
            real.display_frame()
            frames += 1

        assert time.time() < deadline, "scroll did not finish within 20s"
        assert real.is_complete() is True
        assert display.scroll_helper.scroll_position > 128

    def test_dynamic_duration_is_a_real_number(self, real):
        real.prepare_and_display([{"id": "a"}], "live", ["nhl"])
        assert real.get_scroll_display("live").get_dynamic_duration() > 0

    @pytest.mark.parametrize("target_fps", [None, 60, 100, 200])
    def test_presented_speed_is_independent_of_target_fps(self, monkeypatch,
                                                          target_fps):
        """End to end: the px/s the panel actually shows.

        The panel is simulated the way SwapOnVSync paces it: each drawn frame
        occupies ``frame_hold`` refreshes of a 100Hz panel. Ten seconds of
        refreshes must move the strip 500px at the default 50 px/s, whatever
        the General tab's target_fps says."""
        from src.common.scroll_helper import ScrollHelper
        monkeypatch.setattr("src.common.sports_scroll.ScrollHelper", ScrollHelper)

        class _Panel:
            matrix = None
            width, height = 128, 32
            refresh_hz = 100.0
            hold = 1
            image = None

            def set_scrolling_state(self, scrolling, frame_hold=1):
                self.hold = frame_hold

            def update_display(self):
                pass

        global_config = {"display": {"hardware": {"limit_refresh_rate_hz": 100}}}
        if target_fps is not None:
            global_config["target_fps"] = target_fps
        panel = _Panel()
        display = _RealDisplay(panel, {}, LOGGER, global_config=global_config)
        display.scroll_helper.set_scrolling_image(Image.new("RGB", (5000, 32)))

        refreshes = 0
        while refreshes < 1000:  # ten seconds at 100Hz
            assert display.display_scroll_frame() is True
            refreshes += panel.hold
        moved = display.scroll_helper.total_distance_scrolled
        assert moved / 10.0 == pytest.approx(50.0, abs=1.0)
