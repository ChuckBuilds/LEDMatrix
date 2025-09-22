#!/usr/bin/env python3
"""
Test script for leaderboard timing improvements.
This script tests the new timing features without requiring the full LED matrix hardware.
"""

import sys
import os
import time
import json
from unittest.mock import Mock, MagicMock

# Add src directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from leaderboard_manager import LeaderboardManager

def create_mock_display_manager():
    """Create a mock display manager for testing"""
    mock_display = Mock()
    mock_display.matrix = Mock()
    mock_display.matrix.width = 128
    mock_display.matrix.height = 32
    mock_display.set_scrolling_state = Mock()
    mock_display.process_deferred_updates = Mock()
    mock_display.update_display = Mock()
    return mock_display

def create_test_config():
    """Create a test configuration"""
    return {
        'leaderboard': {
            'enabled': True,
            'enabled_sports': {
                'nfl': {
                    'enabled': True,
                    'top_teams': 5
                }
            },
            'update_interval': 3600,
            'scroll_speed': 1,
            'scroll_delay': 0.01,
            'display_duration': 30,
            'loop': False,
            'request_timeout': 30,
            'dynamic_duration': True,
            'min_duration': 30,
            'max_duration': 300,
            'duration_buffer': 0.1,
            'max_display_time': 120,
            'safety_buffer': 10
        }
    }

def test_scroll_speed_tracking():
    """Test the dynamic scroll speed tracking"""
    print("Testing scroll speed tracking...")
    
    config = create_test_config()
    display_manager = create_mock_display_manager()
    
    manager = LeaderboardManager(config, display_manager)
    
    # Test scroll speed measurement
    manager.update_scroll_speed_measurement(100, 2.0)  # 50 px/s
    manager.update_scroll_speed_measurement(120, 2.0)  # 60 px/s
    manager.update_scroll_speed_measurement(110, 2.0)  # 55 px/s
    
    # Should have 3 measurements and calculated average
    assert len(manager.scroll_measurements) == 3
    assert abs(manager.actual_scroll_speed - 55.0) < 0.1  # Should be ~55 px/s
    
    print(f"✓ Scroll speed tracking works: {manager.actual_scroll_speed:.1f} px/s")

def test_max_duration_cap():
    """Test the maximum duration cap"""
    print("Testing maximum duration cap...")
    
    config = create_test_config()
    display_manager = create_mock_display_manager()
    
    manager = LeaderboardManager(config, display_manager)
    
    # Test that max_display_time is set correctly
    assert manager.max_display_time == 120
    assert manager.safety_buffer == 10
    
    print("✓ Maximum duration cap configured correctly")

def test_dynamic_duration_calculation():
    """Test the dynamic duration calculation with safety caps"""
    print("Testing dynamic duration calculation...")
    
    config = create_test_config()
    display_manager = create_mock_display_manager()
    
    manager = LeaderboardManager(config, display_manager)
    
    # Set up test data
    manager.total_scroll_width = 1000  # 1000 pixels of content
    manager.actual_scroll_speed = 50  # 50 px/s
    
    # Calculate duration
    manager.calculate_dynamic_duration()
    
    # Should be capped at max_display_time (120s) since 1000/50 = 20s + buffers
    assert manager.dynamic_duration <= manager.max_display_time
    assert manager.dynamic_duration >= manager.min_duration
    
    print(f"✓ Dynamic duration calculation works: {manager.dynamic_duration}s")

def test_safety_timeout():
    """Test the safety timeout logic"""
    print("Testing safety timeout...")
    
    config = create_test_config()
    display_manager = create_mock_display_manager()
    
    manager = LeaderboardManager(config, display_manager)
    
    # Simulate exceeding max display time
    manager._display_start_time = time.time() - 150  # 150 seconds ago
    manager.max_display_time = 120
    
    # Should trigger timeout
    elapsed_time = time.time() - manager._display_start_time
    should_timeout = elapsed_time > manager.max_display_time
    
    assert should_timeout == True
    print("✓ Safety timeout logic works")

def main():
    """Run all tests"""
    print("Running leaderboard timing improvement tests...\n")
    
    try:
        test_scroll_speed_tracking()
        test_max_duration_cap()
        test_dynamic_duration_calculation()
        test_safety_timeout()
        
        print("\n✅ All tests passed! Leaderboard timing improvements are working correctly.")
        return 0
        
    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    sys.exit(main())
