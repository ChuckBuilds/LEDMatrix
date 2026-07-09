import time
from datetime import datetime
from unittest.mock import MagicMock, patch
import pytest

class TestDisplayControllerInitialization:
    """Test DisplayController initialization and setup."""
    
    def test_init_success(self, test_display_controller):
        """Test successful initialization."""
        assert test_display_controller.config_service is not None
        assert test_display_controller.display_manager is not None
        assert test_display_controller.cache_manager is not None
        assert test_display_controller.font_manager is not None
        assert test_display_controller.plugin_manager is not None
        assert test_display_controller.available_modes == []

    @pytest.mark.skip(reason="No assertions; init logic is covered by test_init_success and fixture setup")
    def test_plugin_discovery_and_loading(self, test_display_controller):
        """Test plugin discovery and loading during initialization."""
        pm = test_display_controller.plugin_manager
        pm.discover_plugins.return_value = ["plugin1", "plugin2"]
        pm.get_plugin.return_value = MagicMock()


class TestDisplayControllerModeRotation:
    """Test display mode rotation logic."""
    
    def test_basic_rotation(self, test_display_controller):
        """Test basic mode rotation."""
        controller = test_display_controller
        controller.available_modes = ["mode1", "mode2", "mode3"]
        controller.current_mode_index = 0
        controller.current_display_mode = "mode1"
        
        # Simulate rotation
        controller.current_mode_index = (controller.current_mode_index + 1) % len(controller.available_modes)
        controller.current_display_mode = controller.available_modes[controller.current_mode_index]
        
        assert controller.current_display_mode == "mode2"
        assert controller.current_mode_index == 1
        
        # Rotate again
        controller.current_mode_index = (controller.current_mode_index + 1) % len(controller.available_modes)
        controller.current_display_mode = controller.available_modes[controller.current_mode_index]
        
        assert controller.current_display_mode == "mode3"
        
        # Rotate back to start
        controller.current_mode_index = (controller.current_mode_index + 1) % len(controller.available_modes)
        controller.current_display_mode = controller.available_modes[controller.current_mode_index]
        
        assert controller.current_display_mode == "mode1"

    def test_rotation_with_single_mode(self, test_display_controller):
        """Test rotation with only one mode."""
        controller = test_display_controller
        controller.available_modes = ["mode1"]
        controller.current_mode_index = 0
        
        controller.current_mode_index = (controller.current_mode_index + 1) % len(controller.available_modes)
        
        assert controller.current_mode_index == 0


class TestDisplayControllerOnDemand:
    """Test on-demand request handling."""
    
    def test_activate_on_demand(self, test_display_controller):
        """Test activating on-demand mode."""
        controller = test_display_controller
        controller.available_modes = ["mode1", "mode2"]
        controller.plugin_modes = {"mode1": MagicMock(), "mode2": MagicMock(), "od_mode": MagicMock()}
        controller.mode_to_plugin_id = {"od_mode": "od_plugin"}
        
        request = {
            "action": "start",
            "plugin_id": "od_plugin",
            "mode": "od_mode",
            "duration": 60
        }
        
        controller._activate_on_demand(request)
        
        assert controller.on_demand_active is True
        assert controller.on_demand_mode == "od_mode"
        assert controller.on_demand_duration == 60.0
        assert controller.on_demand_schedule_override is True
        assert controller.force_change is True
        
    def test_on_demand_expiration(self, test_display_controller):
        """Test on-demand mode expiration."""
        controller = test_display_controller
        controller.on_demand_active = True
        controller.on_demand_mode = "od_mode"
        controller.on_demand_expires_at = time.time() - 10  # Expired
        
        controller._check_on_demand_expiration()
        
        assert controller.on_demand_active is False
        assert controller.on_demand_mode is None
        assert controller.on_demand_last_event == "expired"
        
    def test_on_demand_schedule_override(self, test_display_controller):
        """Test that on-demand overrides schedule."""
        controller = test_display_controller
        controller.is_display_active = False
        controller.on_demand_active = True
        
        # Logic in run() loop handles this, so we simulate it
        if controller.on_demand_active and not controller.is_display_active:
            controller.on_demand_schedule_override = True
            controller.is_display_active = True
            
        assert controller.is_display_active is True
        assert controller.on_demand_schedule_override is True


class TestDisplayControllerLivePriority:
    """Test live priority content switching."""
    
    def test_live_priority_detection(self, test_display_controller, mock_plugin_with_live):
        """Test detection of live priority content."""
        controller = test_display_controller
        # Set up plugin modes with proper mode name matching
        normal_plugin = MagicMock()
        normal_plugin.has_live_priority = MagicMock(return_value=False)
        normal_plugin.has_live_content = MagicMock(return_value=False)
        
        # The mode name needs to match what get_live_modes returns or end with _live
        controller.plugin_modes = {
            "test_plugin_live": mock_plugin_with_live,  # Match get_live_modes return value
            "normal_mode": normal_plugin
        }
        controller.mode_to_plugin_id = {"test_plugin_live": "test_plugin", "normal_mode": "normal_plugin"}
        
        live_mode = controller._check_live_priority()
        
        # Should return the mode name that has live content
        assert live_mode == "test_plugin_live"
        
    def test_live_priority_switch(self, test_display_controller, mock_plugin_with_live):
        """Test switching to live priority mode."""
        controller = test_display_controller
        controller.available_modes = ["normal_mode", "test_plugin_live"]
        controller.current_display_mode = "normal_mode"
        
        # Set up normal plugin without live content
        normal_plugin = MagicMock()
        normal_plugin.has_live_priority = MagicMock(return_value=False)
        normal_plugin.has_live_content = MagicMock(return_value=False)
        
        # Use mode name that matches get_live_modes return value
        controller.plugin_modes = {
            "test_plugin_live": mock_plugin_with_live,
            "normal_mode": normal_plugin
        }
        controller.mode_to_plugin_id = {"test_plugin_live": "test_plugin", "normal_mode": "normal_plugin"}
        
        # Simulate check loop logic
        live_priority_mode = controller._check_live_priority()
        if live_priority_mode and controller.current_display_mode != live_priority_mode:
            controller.current_display_mode = live_priority_mode
            controller.force_change = True
            
        # Should switch to live mode if detected
        assert controller.current_display_mode == "test_plugin_live"
        assert controller.force_change is True

    def test_live_priority_resume_continues_rotation(self, test_display_controller):
        """Regression: when live priority ends, rotation resumes where it was
        interrupted, not after the live plugin's mode.

        Without the fix, _apply_live_priority left current_mode_index pointing at
        the live plugin's slot, so the next rotation step skipped every mode
        between the interrupted position and the live plugin (e.g. elections,
        which sits just before a flights plugin in the order)."""
        controller = test_display_controller
        controller.available_modes = [
            "weather", "forecast", "almanac", "election_ticker", "flight_live"
        ]
        # Rotation is about to show the 3rd mode (index 2).
        controller.current_mode_index = 2
        controller.current_display_mode = "almanac"
        controller._live_resume_index = None

        # Live priority (e.g. planes overhead) preempts -> flight_live (index 4).
        controller._apply_live_priority("flight_live")
        assert controller.current_display_mode == "flight_live"
        assert controller.current_mode_index == 4
        assert controller._live_resume_index == 2  # saved rotation position

        # Re-checks while the hold continues must not move the saved position.
        controller._apply_live_priority("flight_live")
        assert controller._live_resume_index == 2

        # Live priority ends -> resume at the saved index (almanac), so the next
        # rotation step lands on election_ticker (index 3) rather than skipping it.
        controller._apply_live_priority(None)
        assert controller.current_mode_index == 2
        assert controller.current_display_mode == "almanac"
        assert controller._live_resume_index is None

    def test_live_priority_no_resume_when_idle(self, test_display_controller):
        """No saved position + no live content is a no-op (normal rotation)."""
        controller = test_display_controller
        controller.available_modes = ["a", "b", "c"]
        controller.current_mode_index = 1
        controller.current_display_mode = "b"
        controller._live_resume_index = None

        controller._apply_live_priority(None)

        assert controller.current_mode_index == 1
        assert controller.current_display_mode == "b"

    # --- Round-robin between multiple simultaneous live games --------------

    @staticmethod
    def _live_plugin(live_modes):
        """A mock plugin that is live and reports the given live mode names."""
        p = MagicMock()
        p.has_live_priority = MagicMock(return_value=True)
        p.has_live_content = MagicMock(return_value=True)
        p.get_live_modes = MagicMock(return_value=list(live_modes))
        return p

    def test_collect_live_modes_dedupes_multi_mode_plugin(self, test_display_controller):
        """A sports plugin registered under several mode keys (one per league)
        contributes each live mode once, in registration order; plugins with no
        live content are skipped."""
        controller = test_display_controller
        baseball = self._live_plugin(["baseball_live"])
        soccer = self._live_plugin(["soccer_fifa.world_live"])
        idle = MagicMock()
        idle.has_live_priority = MagicMock(return_value=True)
        idle.has_live_content = MagicMock(return_value=False)
        controller.plugin_modes = {
            "baseball_live": baseball,
            "baseball_recent": baseball,
            "soccer_fifa.world_live": soccer,
            "soccer_usa.1_live": soccer,
            "soccer_recent": soccer,
            "clock": idle,
        }
        assert controller._collect_live_modes() == [
            "baseball_live", "soccer_fifa.world_live"
        ]

    def test_round_robin_alternates_between_simultaneous_live_games(self, test_display_controller):
        """Regression: with two games live at once, the live-priority pick
        round-robins each dwell instead of pinning to the first plugin in
        registration order (the bug where a baseball game hid a live World Cup
        match)."""
        controller = test_display_controller
        baseball = self._live_plugin(["baseball_live"])
        soccer = self._live_plugin(["soccer_fifa.world_live"])
        controller.plugin_modes = {
            "baseball_live": baseball,
            "soccer_fifa.world_live": soccer,
        }
        # First entry into live priority from an ambient mode -> first live game.
        controller.current_display_mode = "clock"
        assert controller._check_live_priority(advance=True) == "baseball_live"
        # The controller switches to it; the next dwell advances to the other.
        controller.current_display_mode = "baseball_live"
        assert controller._check_live_priority(advance=True) == "soccer_fifa.world_live"
        # And wraps back again.
        controller.current_display_mode = "soccer_fifa.world_live"
        assert controller._check_live_priority(advance=True) == "baseball_live"

    def test_single_live_game_holds_without_flipping(self, test_display_controller):
        """One live game: advancing returns the same mode, so the hold is stable."""
        controller = test_display_controller
        controller.plugin_modes = {"baseball_live": self._live_plugin(["baseball_live"])}
        controller.current_display_mode = "baseball_live"
        assert controller._check_live_priority(advance=True) == "baseball_live"

    def test_non_advancing_peek_does_not_rotate(self, test_display_controller):
        """The default (advance=False) peek used by the Vegas coordinator must
        not spin the cursor: it returns the live mode already on screen."""
        controller = test_display_controller
        controller.plugin_modes = {
            "baseball_live": self._live_plugin(["baseball_live"]),
            "soccer_fifa.world_live": self._live_plugin(["soccer_fifa.world_live"]),
        }
        controller.current_display_mode = "soccer_fifa.world_live"
        assert controller._check_live_priority() == "soccer_fifa.world_live"
        assert controller._check_live_priority() == "soccer_fifa.world_live"
        # From an ambient mode the peek reports the first live game (truthy).
        controller.current_display_mode = "clock"
        assert controller._check_live_priority() == "baseball_live"

    def test_no_live_content_returns_none(self, test_display_controller):
        controller = test_display_controller
        idle = MagicMock()
        idle.has_live_priority = MagicMock(return_value=True)
        idle.has_live_content = MagicMock(return_value=False)
        controller.plugin_modes = {"clock": idle}
        controller.current_display_mode = "clock"
        assert controller._check_live_priority(advance=True) is None

    def test_fallback_to_mode_name_when_get_live_modes_unhelpful(self, test_display_controller):
        """A live plugin whose get_live_modes returns nothing registered falls
        back to its own '_live' mode name (legacy behavior preserved)."""
        controller = test_display_controller
        legacy = MagicMock()
        legacy.has_live_priority = MagicMock(return_value=True)
        legacy.has_live_content = MagicMock(return_value=True)
        legacy.get_live_modes = MagicMock(return_value=["unregistered_mode"])
        controller.plugin_modes = {"hockey_live": legacy}
        controller.current_display_mode = "clock"
        assert controller._check_live_priority(advance=True) == "hockey_live"


class TestDisplayControllerDynamicDuration:
    """Test dynamic duration handling."""
    
    def test_plugin_supports_dynamic(self, test_display_controller, mock_plugin_with_dynamic):
        """Test checking if plugin supports dynamic duration."""
        controller = test_display_controller
        assert controller._plugin_supports_dynamic(mock_plugin_with_dynamic) is True
        
        mock_normal = MagicMock()
        mock_normal.supports_dynamic_duration.side_effect = AttributeError
        assert controller._plugin_supports_dynamic(mock_normal) is False
        
    def test_get_dynamic_cap(self, test_display_controller, mock_plugin_with_dynamic):
        """Test retrieving dynamic duration cap."""
        controller = test_display_controller
        cap = controller._plugin_dynamic_cap(mock_plugin_with_dynamic)
        assert cap == 180.0
        
    def test_global_cap_fallback(self, test_display_controller):
        """Test global dynamic duration cap."""
        controller = test_display_controller
        controller.global_dynamic_config = {"max_duration_seconds": 120}
        assert controller._get_global_dynamic_cap() == 120.0
        
        controller.global_dynamic_config = {}
        assert controller._get_global_dynamic_cap() == 180.0  # Default


class TestDisplayControllerSchedule:
    """Test schedule management."""
    
    def test_schedule_disabled(self, test_display_controller):
        """Test when schedule is disabled."""
        controller = test_display_controller
        schedule_config = {"schedule": {"enabled": False}}
        with patch.object(controller.config_service, 'get_config', return_value=schedule_config):
            controller._check_schedule()
            assert controller.is_display_active is True

    def test_active_hours(self, test_display_controller):
        """Test active hours check."""
        controller = test_display_controller
        with patch('src.display_controller.datetime') as mock_datetime:
            mock_datetime.now.return_value.strftime.return_value.lower.return_value = "monday"
            mock_datetime.now.return_value.time.return_value = datetime.strptime("12:00", "%H:%M").time()
            mock_datetime.strptime = datetime.strptime

            schedule_config = {
                "schedule": {
                    "enabled": True,
                    "start_time": "09:00",
                    "end_time": "17:00"
                }
            }
            with patch.object(controller.config_service, 'get_config', return_value=schedule_config):
                controller._check_schedule()
                assert controller.is_display_active is True

    def test_inactive_hours(self, test_display_controller):
        """Test inactive hours check."""
        controller = test_display_controller
        # Inject schedule directly into self.config (what _check_schedule actually reads)
        # and reset the minute gate so the cached result from any prior call is cleared.
        controller.config['schedule'] = {
            "enabled": True,
            "start_time": "09:00",
            "end_time": "17:00",
        }
        controller._schedule_checked_minute = None
        controller._tz = None

        with patch('src.display_controller.datetime') as mock_datetime:
            mock_datetime.now.return_value.strftime.return_value.lower.return_value = "monday"
            mock_datetime.now.return_value.time.return_value = datetime.strptime("20:00", "%H:%M").time()
            mock_datetime.strptime = datetime.strptime

            controller._check_schedule()
            assert controller.is_display_active is False


class TestPluginHealthWiring:
    """Phase 1: DisplayController activates the dormant plugin health/metrics
    subsystem by wiring real tracker/monitor instances onto the plugin manager."""

    def test_health_tracker_and_resource_monitor_wired(self, test_display_controller):
        from src.plugin_system.plugin_health import PluginHealthTracker
        from src.plugin_system.resource_monitor import PluginResourceMonitor

        pm = test_display_controller.plugin_manager
        assert isinstance(pm.health_tracker, PluginHealthTracker)
        assert isinstance(pm.resource_monitor, PluginResourceMonitor)
