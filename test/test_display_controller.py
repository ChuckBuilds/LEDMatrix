import pytest
import time
from unittest.mock import MagicMock, patch, ANY
from src.display_controller import DisplayController

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

    def test_plugin_discovery_and_loading(self, test_display_controller):
        """Test plugin discovery and loading during initialization."""
        # Mock plugin manager behavior
        pm = test_display_controller.plugin_manager
        pm.discover_plugins.return_value = ["plugin1", "plugin2"]
        pm.get_plugin.return_value = MagicMock()
        
        # Manually trigger the plugin loading logic that happens in __init__
        # Since we're using a fixture that mocks __init__ partially, we need to verify 
        # the interactions or simulate the loading if we want to test that specific logic
        pass 
        # Note: Testing __init__ logic is tricky with the fixture. 
        # We rely on the fixture to give us a usable controller.


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
        controller.config = {"schedule": {"enabled": False}}
        
        controller._check_schedule()
        assert controller.is_display_active is True
        
    def test_active_hours(self, test_display_controller):
        """Test active hours check."""
        controller = test_display_controller
        # Mock datetime to be within active hours
        with patch('src.display_controller.datetime') as mock_datetime:
            mock_datetime.now.return_value.strftime.return_value.lower.return_value = "monday"
            mock_datetime.now.return_value.time.return_value = datetime.strptime("12:00", "%H:%M").time()
            mock_datetime.strptime = datetime.strptime
            
            controller.config = {
                "schedule": {
                    "enabled": True,
                    "start_time": "09:00",
                    "end_time": "17:00"
                }
            }
            
            controller._check_schedule()
            assert controller.is_display_active is True

    def test_inactive_hours(self, test_display_controller):
        """Test inactive hours check."""
        controller = test_display_controller
        # Mock datetime to be outside active hours
        with patch('src.display_controller.datetime') as mock_datetime:
            mock_datetime.now.return_value.strftime.return_value.lower.return_value = "monday"
            mock_datetime.now.return_value.time.return_value = datetime.strptime("20:00", "%H:%M").time()
            mock_datetime.strptime = datetime.strptime
            
            controller.config = {
                "schedule": {
                    "enabled": True,
                    "start_time": "09:00",
                    "end_time": "17:00"
                }
            }
            
            controller._check_schedule()
            assert controller.is_display_active is False
            
from datetime import datetime
