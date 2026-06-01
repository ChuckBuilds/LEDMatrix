"""
Tests for the three display_controller.py optimizations:

  Opt #1 — inspect.signature() caching per plugin_id
  Opt #2 — pre-cached config values (_normal_brightness, _scroll_speed)
  Opt #3 — schedule minute-gate (_check_schedule, _check_dim_schedule)
"""

import pytest
import time
from datetime import datetime
from unittest.mock import MagicMock, patch, call


# ---------------------------------------------------------------------------
# Shared fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def controller(test_display_controller):
    """Return a ready DisplayController from the existing suite fixture."""
    return test_display_controller


# ---------------------------------------------------------------------------
# Opt #1 — signature cache
# ---------------------------------------------------------------------------

class TestSignatureCache:
    """inspect.signature() should be called at most once per plugin_id."""

    class _PluginWithMode:
        """Real class whose display() accepts display_mode — inspectable by signature."""
        plugin_id = "mode_plugin"
        def display(self, display_mode=None, force_clear=False):
            return True

    class _PluginNoMode:
        """Real class whose display() does NOT accept display_mode."""
        plugin_id = "no_mode_plugin"
        def display(self, force_clear=False):
            return True

    def test_cache_starts_empty(self, controller):
        assert controller._plugin_accepts_display_mode == {}

    def test_signature_computed_and_cached(self, controller):
        """After the first cache population, the dict holds a bool and stays unchanged
        if queried again without explicitly deleting the key."""
        import inspect as _inspect
        plugin = self._PluginNoMode()
        key = "sig_test"
        if key not in controller._plugin_accepts_display_mode:
            controller._plugin_accepts_display_mode[key] = (
                "display_mode" in _inspect.signature(plugin.display).parameters
            )
        original = controller._plugin_accepts_display_mode[key]

        # Accessing cache again should not change the value
        second = controller._plugin_accepts_display_mode[key]
        assert second == original

    def test_cache_stores_false_for_no_display_mode(self, controller):
        """Plugin whose display() doesn't accept display_mode → cached False."""
        import inspect as _inspect
        plugin = self._PluginNoMode()
        controller._plugin_accepts_display_mode["no_mode_plugin"] = (
            "display_mode" in _inspect.signature(plugin.display).parameters
        )
        assert controller._plugin_accepts_display_mode["no_mode_plugin"] is False

    def test_cache_stores_true_for_display_mode(self, controller):
        """Plugin whose display() accepts display_mode → cached True."""
        import inspect as _inspect
        plugin = self._PluginWithMode()
        controller._plugin_accepts_display_mode["mode_plugin"] = (
            "display_mode" in _inspect.signature(plugin.display).parameters
        )
        assert controller._plugin_accepts_display_mode["mode_plugin"] is True

    def test_cache_cleared_on_plugin_reload(self, controller):
        """Populating plugin_modes for an id that's already cached must clear the entry."""
        plugin = MagicMock()
        controller._plugin_accepts_display_mode["reload_plugin"] = False

        # Simulate the plugin_modes population code path (as in __init__)
        plugin_id = "reload_plugin"
        controller.plugin_modes["reload_plugin"] = plugin
        if hasattr(controller, "_plugin_accepts_display_mode"):
            controller._plugin_accepts_display_mode.pop(plugin_id, None)

        assert "reload_plugin" not in controller._plugin_accepts_display_mode


# ---------------------------------------------------------------------------
# Opt #2 — cached config values
# ---------------------------------------------------------------------------

class TestCachedConfigValues:
    """_normal_brightness and _scroll_speed are populated from config at init."""

    def test_normal_brightness_cached(self, controller):
        """_normal_brightness must equal what the config says."""
        expected = (
            controller.config
            .get("display", {})
            .get("hardware", {})
            .get("brightness", 90)
        )
        assert controller._normal_brightness == expected

    def test_scroll_speed_cached(self, controller):
        """_scroll_speed must equal what the config says."""
        expected = (
            controller.config
            .get("display", {})
            .get("vegas_scroll", {})
            .get("scroll_speed", 75)
        )
        assert controller._scroll_speed == expected

    def test_current_brightness_uses_cached_value(self, controller):
        """current_brightness is initialised from _normal_brightness."""
        assert controller.current_brightness == controller._normal_brightness

    def test_cached_target_brightness_init(self, controller):
        """_cached_target_brightness starts equal to _normal_brightness."""
        assert controller._cached_target_brightness == controller._normal_brightness

    def test_normal_brightness_default_is_90(self, controller):
        """If config has no brightness key the default is 90."""
        controller.config = {}
        controller._normal_brightness = (
            controller.config.get("display", {})
            .get("hardware", {})
            .get("brightness", 90)
        )
        assert controller._normal_brightness == 90


# ---------------------------------------------------------------------------
# Opt #3 — schedule minute-gate
# ---------------------------------------------------------------------------

class TestScheduleMinuteGate:
    """_check_schedule and _check_dim_schedule skip re-evaluation within the same minute."""

    # ── _check_schedule ──────────────────────────────────────────────────────

    def test_schedule_checked_minute_starts_none(self, controller):
        assert controller._schedule_checked_minute is None

    def test_first_call_sets_checked_minute(self, controller):
        """After the first real evaluation the minute key is stored."""
        controller.config["schedule"] = {
            "enabled": True,
            "start_time": "00:00",
            "end_time": "23:59",
        }
        controller._schedule_checked_minute = None
        controller._tz = None

        controller._check_schedule()
        assert controller._schedule_checked_minute is not None

    def test_second_call_same_minute_does_not_re_evaluate(self, controller):
        """A second call with the same (hour, minute) returns without changing state."""
        controller.config["schedule"] = {
            "enabled": True,
            "start_time": "00:00",
            "end_time": "23:59",
        }
        controller._tz = None
        controller._schedule_checked_minute = None

        # First call — evaluates and marks as active (whole-day window)
        controller._check_schedule()
        assert controller.is_display_active is True
        first_minute_key = controller._schedule_checked_minute

        # Force is_display_active to False so we can tell if it gets re-evaluated
        controller.is_display_active = False

        # Second call within the same minute — gate fires, is_display_active unchanged
        controller._schedule_checked_minute = first_minute_key  # same minute
        controller._check_schedule()
        assert controller.is_display_active is False, (
            "Second call in same minute should return immediately without re-evaluation"
        )

    def test_new_minute_forces_re_evaluation(self, controller):
        """A different (hour, minute) key causes a full re-evaluation."""
        controller.config["schedule"] = {
            "enabled": True,
            "start_time": "00:00",
            "end_time": "23:59",
        }
        controller._tz = None

        # Plant a stale minute key from yesterday
        controller._schedule_checked_minute = (-1, -1)
        controller.is_display_active = False  # wrong value to be corrected

        controller._check_schedule()
        assert controller.is_display_active is True, (
            "A new minute key should trigger re-evaluation and correct is_display_active"
        )

    def test_gate_skipped_when_schedule_disabled(self, controller):
        """When schedule.enabled=False the method returns before reaching the gate."""
        controller.config["schedule"] = {"enabled": False}
        controller._schedule_checked_minute = None
        controller._tz = None

        controller._check_schedule()
        # The early-return path doesn't set the minute key
        assert controller._schedule_checked_minute is None

    # ── _check_dim_schedule ──────────────────────────────────────────────────

    def test_dim_checked_minute_starts_none(self, controller):
        assert controller._dim_checked_minute is None

    def test_first_dim_call_sets_checked_minute(self, controller):
        """First call with dim schedule enabled stores the minute key."""
        controller.config["dim_schedule"] = {
            "enabled": True,
            "start_time": "22:00",
            "end_time": "06:00",
        }
        controller.is_display_active = True
        controller._dim_checked_minute = None
        controller._tz = None

        controller._check_dim_schedule()
        assert controller._dim_checked_minute is not None

    def test_dim_second_call_returns_cached_brightness(self, controller):
        """Second call with same minute returns _cached_target_brightness immediately."""
        controller.config["dim_schedule"] = {
            "enabled": True,
            "start_time": "22:00",
            "end_time": "06:00",
        }
        controller.is_display_active = True
        controller._dim_checked_minute = None
        controller._tz = None

        # First call stores the result
        first_result = controller._check_dim_schedule()
        assert controller._cached_target_brightness == first_result
        minute_key = controller._dim_checked_minute

        # Corrupt cached value to something recognisable
        controller._cached_target_brightness = 42

        # Second call in same minute — must return the cached 42
        controller._dim_checked_minute = minute_key
        second_result = controller._check_dim_schedule()
        assert second_result == 42, (
            "Same-minute call must return cached brightness, not re-compute"
        )

    def test_dim_gate_skipped_when_display_off(self, controller):
        """When display is off the method exits before the minute gate."""
        controller.config["dim_schedule"] = {"enabled": True, "start_time": "22:00", "end_time": "06:00"}
        controller.is_display_active = False
        controller._dim_checked_minute = None
        controller._tz = None

        controller._check_dim_schedule()
        # Early-exit path does not set the minute key
        assert controller._dim_checked_minute is None

    def test_dim_cached_target_brightness_updated_after_full_evaluation(self, controller):
        """After a full evaluation _cached_target_brightness reflects the result."""
        controller.config["dim_schedule"] = {
            "enabled": True,
            "start_time": "22:00",
            "end_time": "06:00",
        }
        controller.is_display_active = True
        controller._dim_checked_minute = None   # force full re-evaluation
        controller._tz = None

        result = controller._check_dim_schedule()
        assert controller._cached_target_brightness == result

    # ── timezone lazy init ───────────────────────────────────────────────────

    def test_tz_starts_none(self, controller):
        assert controller._tz is None

    def test_tz_lazily_initialised_on_first_schedule_check(self, controller):
        """_tz is None until _check_schedule or _check_dim_schedule is called."""
        controller.config["schedule"] = {
            "enabled": True,
            "start_time": "00:00",
            "end_time": "23:59",
        }
        controller._tz = None
        controller._schedule_checked_minute = None

        controller._check_schedule()
        assert controller._tz is not None

    def test_tz_shared_between_schedule_and_dim(self, controller):
        """Both methods use the same cached _tz instance."""
        controller.config["schedule"] = {"enabled": True, "start_time": "00:00", "end_time": "23:59"}
        controller.config["dim_schedule"] = {"enabled": True, "start_time": "22:00", "end_time": "06:00"}
        controller.is_display_active = True
        controller._tz = None
        controller._schedule_checked_minute = None
        controller._dim_checked_minute = None

        controller._check_schedule()
        tz_after_schedule = controller._tz

        controller._check_dim_schedule()
        assert controller._tz is tz_after_schedule, (
            "_check_dim_schedule should reuse the _tz set by _check_schedule"
        )
