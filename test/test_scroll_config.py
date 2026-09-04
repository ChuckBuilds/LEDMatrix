"""Tests for the shared scroll configuration resolver."""

import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.common.scroll_config import (  # noqa: E402
    DEFAULT_PIXELS_PER_SECOND,
    MAX_PIXELS_PER_FRAME,
    crisp_ladder,
    solve_crisp,
    MAX_PIXELS_PER_SECOND,
    MIN_PIXELS_PER_SECOND,
    ScrollSettings,
    configure,
    refresh_hz_from_config,
    resolve,
)


class FakeHelper:
    """Records what configure() applied."""

    def __init__(self, with_optional=True):
        self.speed = None
        self.frame_based = None
        self.target_fps = None
        if not with_optional:
            del FakeHelper.set_frame_based_scrolling
            del FakeHelper.set_target_fps

    def set_scroll_speed(self, speed):
        self.speed = speed

    def set_frame_based_scrolling(self, enabled):
        self.frame_based = enabled

    def set_target_fps(self, fps):
        self.target_fps = fps


class FakeDisplayManager:
    """Records the frame hold configure() applies."""

    def __init__(self):
        self.hold = None

    def set_frame_hold(self, refreshes):
        self.hold = refreshes


class MinimalHelper:
    """An older helper exposing only set_scroll_speed."""

    def __init__(self):
        self.speed = None

    def set_scroll_speed(self, speed):
        self.speed = speed


class TestPrecedence:
    def test_display_options_pair_wins(self):
        s = resolve({"display_options": {"scroll_speed": 1.0, "scroll_delay": 0.01}})
        assert s.pixels_per_second == 100.0
        assert s.source == "display_options.scroll_speed/delay"

    def test_display_block_used_when_options_absent(self):
        s = resolve({"display": {"scroll_speed": 2.0, "scroll_delay": 0.01}})
        assert s.pixels_per_second == 200.0
        assert s.source == "display.scroll_speed/delay"

    def test_root_pair_used_when_both_blocks_absent(self):
        s = resolve({"scroll_speed": 1.0, "scroll_delay": 0.02})
        assert s.pixels_per_second == 50.0

    def test_pixels_per_second_used_when_no_pair_given(self):
        s = resolve({"display_options": {"scroll_pixels_per_second": 120.0}})
        assert s.pixels_per_second == 120.0
        assert s.source == "display_options.scroll_pixels_per_second"

    def test_global_display_is_the_last_resort_before_default(self):
        s = resolve({}, {"display": {"scroll_speed": 1.0, "scroll_delay": 0.005}})
        assert s.pixels_per_second == 200.0

    def test_default_when_nothing_configured(self):
        s = resolve({}, {})
        assert s.pixels_per_second == DEFAULT_PIXELS_PER_SECOND
        assert s.source == "default"


class TestDeprecatedKeyCannotOverrideExplicitPair:
    """Regression for ChuckBuilds/ledmatrix-plugins#408.

    odds-ticker ranked scroll_pixels_per_second above the documented
    scroll_speed/scroll_delay pair. Because that key carries a schema default,
    the documented settings became unreachable for every user and the ticker
    silently ran at the default speed. The pair must win.
    """

    def test_pair_beats_pixels_per_second_in_the_same_block(self):
        s = resolve(
            {
                "display_options": {
                    "scroll_speed": 1.0,
                    "scroll_delay": 0.01,
                    "scroll_pixels_per_second": 50.0,  # schema default
                }
            }
        )
        assert s.pixels_per_second == 100.0
        assert "scroll_speed/delay" in s.source

    def test_pair_beats_pixels_per_second_in_an_outer_block(self):
        s = resolve(
            {
                "display_options": {"scroll_speed": 1.0, "scroll_delay": 0.01},
                "scroll_pixels_per_second": 50.0,
            }
        )
        assert s.pixels_per_second == 100.0


class TestMalformedValues:
    @pytest.mark.parametrize("bad", [None, "fast", "", {}, [], float("nan")])
    def test_unusable_speed_falls_through(self, bad):
        s = resolve({"display_options": {"scroll_speed": bad, "scroll_delay": 0.01}})
        assert s.pixels_per_second == DEFAULT_PIXELS_PER_SECOND

    @pytest.mark.parametrize("bad", [0, -5, 0.0])
    def test_non_positive_values_fall_through(self, bad):
        s = resolve({"display_options": {"scroll_pixels_per_second": bad}})
        assert s.pixels_per_second == DEFAULT_PIXELS_PER_SECOND

    def test_booleans_are_not_treated_as_numbers(self):
        s = resolve({"display_options": {"scroll_pixels_per_second": True}})
        assert s.pixels_per_second == DEFAULT_PIXELS_PER_SECOND

    def test_zero_delay_does_not_divide_by_zero(self):
        s = resolve({"display_options": {"scroll_speed": 1.0, "scroll_delay": 0}})
        assert s.pixels_per_second == DEFAULT_PIXELS_PER_SECOND

    def test_non_dict_blocks_are_ignored(self):
        s = resolve({"display_options": "nonsense", "display": 5})
        assert s.pixels_per_second == DEFAULT_PIXELS_PER_SECOND


class TestClamping:
    def test_absurdly_fast_is_clamped(self):
        s = resolve({"display_options": {"scroll_pixels_per_second": 100000.0}})
        assert s.pixels_per_second == MAX_PIXELS_PER_SECOND
        assert s.warning and "clamped" in s.warning

    def test_absurdly_slow_is_clamped(self):
        s = resolve({"display_options": {"scroll_pixels_per_second": 0.01}})
        assert s.pixels_per_second == MIN_PIXELS_PER_SECOND


class TestWholePixelWarning:
    """The display rule: whole pixels per refresh, or it judders."""

    def test_no_warning_when_speed_divides_evenly(self):
        s = resolve({"display_options": {"scroll_pixels_per_second": 100.0}}, refresh_hz=100)
        assert s.warning is None
        assert s.pixels_per_frame == pytest.approx(1.0)

    def test_no_warning_at_an_integer_multiple(self):
        s = resolve({"display_options": {"scroll_pixels_per_second": 200.0}}, refresh_hz=100)
        assert s.warning is None

    def test_warns_on_a_half_pixel_per_frame(self):
        s = resolve({"display_options": {"scroll_pixels_per_second": 50.0}}, refresh_hz=100)
        assert s.warning is not None
        assert "judder" in s.warning
        assert "100 px/s" in s.warning

    def test_warns_on_a_fractional_multiple(self):
        s = resolve({"display_options": {"scroll_pixels_per_second": 130.0}}, refresh_hz=100)
        assert s.warning is not None

    def test_respects_a_non_default_refresh_rate(self):
        s = resolve({"display_options": {"scroll_pixels_per_second": 150.0}}, refresh_hz=150)
        assert s.warning is None
        assert s.pixels_per_frame == pytest.approx(1.0)


class TestConfigure:
    def test_applies_time_based_mode_and_speed(self):
        helper = FakeHelper()
        s = configure(helper, {"display_options": {"scroll_speed": 1.0, "scroll_delay": 0.01}})
        assert helper.speed == 100.0
        assert helper.frame_based is False, "must not use the wall-clock step gate"
        assert helper.target_fps == 100.0
        assert s.pixels_per_second == 100.0

    def test_works_against_a_helper_without_optional_methods(self):
        helper = MinimalHelper()
        configure(helper, {"display_options": {"scroll_pixels_per_second": 100.0}})
        assert helper.speed == 100.0

    def test_snapping_removes_the_judder_warning(self, caplog):
        """50px/s cannot be shown in whole pixels at 100Hz *per refresh*, but it
        can as 1px every 2nd refresh -- so once snapped there is nothing to warn
        about. resolve() alone still warns; configure() resolves it."""
        assert "judder" in resolve(
            {"display_options": {"scroll_pixels_per_second": 50.0}},
            refresh_hz=100).warning
        helper, dm = FakeHelper(), FakeDisplayManager()
        with caplog.at_level(logging.WARNING):
            settings = configure(
                helper, {"display_options": {"scroll_pixels_per_second": 50.0}},
                display_manager=dm)
        assert settings.warning is None
        assert not [r for r in caplog.records if "judder" in r.getMessage()]

    def test_reports_the_hold_without_applying_it(self):
        """The hold belongs to a scroll, not a plugin's lifetime -- one left set
        at construction is wiped as soon as any other plugin stops scrolling.
        configure() reports it; the caller passes it to set_scrolling_state."""
        dm = FakeDisplayManager()
        s = configure(FakeHelper(), {"display_options": {"scroll_pixels_per_second": 25.0}},
                      display_manager=dm)
        assert s.frame_hold == 4, "25px/s at 100Hz is 1px every 4th refresh"
        assert dm.hold is None, "must not apply the hold behind the caller's back"

    def test_full_speed_needs_no_hold(self):
        s = configure(FakeHelper(), {"display_options": {"scroll_pixels_per_second": 100.0}},
                      display_manager=FakeDisplayManager())
        assert s.frame_hold == 1

    def test_frame_hold_is_one_when_snapping_is_off(self):
        s = configure(FakeHelper(), {"display_options": {"scroll_pixels_per_second": 50.0}},
                      snap_to_crisp=False)
        assert s.frame_hold == 1

    def test_snaps_to_the_nearest_crisp_speed_and_reports_both(self):
        s = configure(FakeHelper(), {"display_options": {"scroll_pixels_per_second": 45.0}},
                      display_manager=FakeDisplayManager())
        assert s.requested_pixels_per_second == 45.0
        assert s.pixels_per_second == 50.0
        assert s.crisp is not None and s.crisp.pixels_per_frame == 1

    def test_snapping_can_be_turned_off(self):
        s = configure(FakeHelper(), {"display_options": {"scroll_pixels_per_second": 45.0}},
                      snap_to_crisp=False)
        assert s.pixels_per_second == 45.0
        assert s.crisp is None

    def test_helper_target_fps_matches_the_presentation_rate(self):
        """At a hold of 4 the panel still refreshes at 100Hz, but frames are
        presented at 25/s -- that is the rate the helper should pace to."""
        helper = FakeHelper()
        configure(helper, {"display_options": {"scroll_pixels_per_second": 25.0}},
                  display_manager=FakeDisplayManager())
        assert helper.target_fps == pytest.approx(25.0)

    def test_returns_settings_describing_the_source(self):
        s = configure(FakeHelper(), {"display_options": {"scroll_speed": 2.0, "scroll_delay": 0.01}})
        assert isinstance(s, ScrollSettings)
        assert "200.0 px/s" in s.describe()


class TestRefreshFromConfig:
    def test_reads_the_hardware_limit(self):
        assert refresh_hz_from_config(
            {"display": {"hardware": {"limit_refresh_rate_hz": 150}}}
        ) == 150.0

    @pytest.mark.parametrize("cfg", [None, {}, {"display": {}}, {"display": {"hardware": {}}},
                                     "nonsense", {"display": {"hardware": {"limit_refresh_rate_hz": None}}}])
    def test_falls_back_to_the_default(self, cfg):
        assert refresh_hz_from_config(cfg) == 100.0


class TestCrispLadder:
    """Whole-pixel speeds available on a given panel."""

    def test_every_entry_is_exactly_reachable(self):
        for c in crisp_ladder(100):
            assert c.pixels_per_second == pytest.approx(
                c.refresh_hz / c.frame_hold * c.pixels_per_frame)

    def test_sorted_slowest_first(self):
        speeds = [c.pixels_per_second for c in crisp_ladder(100)]
        assert speeds == sorted(speeds)

    def test_no_duplicate_speeds(self):
        speeds = [round(c.pixels_per_second, 3) for c in crisp_ladder(100)]
        assert len(speeds) == len(set(speeds))

    def test_duplicates_resolve_to_the_smaller_step(self):
        """100px/s is 1px every refresh or 2px every 2nd; prefer the former."""
        entry = next(c for c in crisp_ladder(100)
                     if c.pixels_per_second == pytest.approx(100.0))
        assert entry.pixels_per_frame == 1
        assert entry.frame_hold == 1

    def test_ladder_scales_with_the_panel(self):
        assert any(c.pixels_per_second == pytest.approx(60.0)
                   for c in crisp_ladder(60))
        assert any(c.pixels_per_second == pytest.approx(30.0)
                   for c in crisp_ladder(60))

    def test_full_refresh_speed_is_present(self):
        for hz in (60, 75, 100, 120):
            assert any(c.pixels_per_second == pytest.approx(float(hz))
                       for c in crisp_ladder(hz))


class TestSolveCrisp:
    def test_exact_targets_are_matched_exactly(self):
        for target in (100, 50, 25, 20):
            assert solve_crisp(target, 100).pixels_per_second == pytest.approx(target)

    def test_prefers_smooth_motion_over_raw_proximity(self):
        """30 -> 33.3 (1px, 33fps), not 28.6 (2px at 14fps) which is nearer."""
        got = solve_crisp(30, 100)
        assert got.pixels_per_second == pytest.approx(33.333, abs=0.01)
        assert got.pixels_per_frame == 1

    def test_does_not_chase_a_jumpy_exact_match(self):
        """45 -> 50 (1px, smooth) beats 42.9 (3px at 14fps)."""
        assert solve_crisp(45, 100).pixels_per_frame == 1

    def test_allows_a_two_pixel_step_when_it_is_clearly_closer(self):
        """60 -> 66.7 (2px at 33fps) rather than 50 (1px) which is 17% slow."""
        got = solve_crisp(60, 100)
        assert got.pixels_per_second == pytest.approx(66.667, abs=0.01)
        assert got.pixels_per_frame == 2

    def test_result_is_always_on_the_ladder(self):
        ladder = {round(c.pixels_per_second, 3) for c in crisp_ladder(100)}
        for target in range(5, 205, 5):
            assert round(solve_crisp(target, 100).pixels_per_second, 3) in ladder

    def test_adapts_to_a_slower_panel(self):
        got = solve_crisp(30, 60)
        assert got.pixels_per_second == pytest.approx(30.0)
        assert got.pixels_per_frame == 1
        assert got.frame_hold == 2

    def test_speeds_beyond_the_panel_clamp_to_the_fastest_available(self):
        got = solve_crisp(10_000, 100)
        assert got.frame_hold == 1
        assert got.pixels_per_frame == MAX_PIXELS_PER_FRAME

    def test_steppiness_labels_are_sane(self):
        assert solve_crisp(100, 100).steppiness == "smooth"
        assert crisp_ladder(100)[0].steppiness == "stepped"


class TestRefreshFromDisplayManager:
    """A plugin sees only its own config section, so the display manager is the
    authoritative source for the panel's refresh rate."""

    class DM:
        def __init__(self, hz):
            self.refresh_hz = hz
            self.hold = None

        def set_frame_hold(self, refreshes):
            self.hold = refreshes

    def test_uses_the_display_manager_rate(self):
        dm = self.DM(60.0)
        s = configure(FakeHelper(), {"display_options": {"scroll_pixels_per_second": 30.0}},
                      display_manager=dm)
        # 30px/s on a 60Hz panel is 1px every 2nd refresh, exactly.
        assert s.pixels_per_second == pytest.approx(30.0)
        assert s.frame_hold == 2

    def test_explicit_refresh_hz_wins_over_the_display_manager(self):
        dm = self.DM(60.0)
        s = configure(FakeHelper(), {"display_options": {"scroll_pixels_per_second": 25.0}},
                      display_manager=dm, refresh_hz=100)
        assert s.frame_hold == 4, "25px/s at 100Hz is 1px every 4th refresh"

    def test_missing_attribute_falls_back_to_the_default(self):
        class Bare:
            def set_frame_hold(self, refreshes):
                self.hold = refreshes

        bare = Bare()
        s = configure(FakeHelper(), {"display_options": {"scroll_pixels_per_second": 50.0}},
                      display_manager=bare)
        assert s.frame_hold == 2, "assumed 100Hz"
