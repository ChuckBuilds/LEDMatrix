"""
Base test class for LEDMatrix plugins.

Provides common fixtures and helper methods for plugin testing.
"""

import unittest
import sys
from pathlib import Path
from typing import Dict, Any, Optional

# Add project root to path for imports
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.plugin_system.testing.mocks import (
    MockDisplayManager,
    MockCacheManager,
    MockConfigManager,
    MockPluginManager
)


class PluginTestCase(unittest.TestCase):
    """
    Base test case for plugin testing.
    
    Provides common fixtures and helper methods.
    """
    
    def setUp(self):
        """Set up test fixtures."""
        # Create mock managers
        self.display_manager = MockDisplayManager(width=128, height=32)
        self.cache_manager = MockCacheManager()
        self.config_manager = MockConfigManager()
        self.plugin_manager = MockPluginManager()
        
        # Default plugin configuration
        self.plugin_config = {
            'enabled': True,
            'display_duration': 15.0
        }
        
        # Plugin ID for tests
        self.plugin_id = 'test-plugin'
    
    def tearDown(self):
        """Clean up after tests."""
        # Reset all mocks
        self.display_manager.reset()
        self.cache_manager.reset()
        self.config_manager.reset()
        self.plugin_manager.reset()
    
    def create_plugin_instance(self, plugin_class, plugin_id: Optional[str] = None, 
                              config: Optional[Dict[str, Any]] = None):
        """
        Create a plugin instance with mock dependencies.
        
        Args:
            plugin_class: Plugin class to instantiate
            plugin_id: Optional plugin ID (defaults to self.plugin_id)
            config: Optional config dict (defaults to self.plugin_config)
        
        Returns:
            Plugin instance
        """
        pid = plugin_id or self.plugin_id
        cfg = config or self.plugin_config.copy()
        
        return plugin_class(
            plugin_id=pid,
            config=cfg,
            display_manager=self.display_manager,
            cache_manager=self.cache_manager,
            plugin_manager=self.plugin_manager
        )
    
    def assert_plugin_initialized(self, plugin):
        """Assert that plugin was initialized correctly."""
        self.assertIsNotNone(plugin)
        self.assertEqual(plugin.plugin_id, self.plugin_id)
        self.assertEqual(plugin.config, self.plugin_config)
        self.assertEqual(plugin.display_manager, self.display_manager)
        self.assertEqual(plugin.cache_manager, self.cache_manager)
        self.assertEqual(plugin.plugin_manager, self.plugin_manager)
    
    def assert_display_cleared(self):
        """Assert that display was cleared."""
        self.assertTrue(self.display_manager.clear_called)
    
    def assert_display_updated(self):
        """Assert that display was updated."""
        self.assertTrue(self.display_manager.update_called)
    
    def assert_text_drawn(self, text: Optional[str] = None):
        """
        Assert that text was drawn on display.
        
        Args:
            text: Optional text to check for
        """
        text_calls = [c for c in self.display_manager.draw_calls if c['type'] == 'text']
        self.assertGreater(len(text_calls), 0, "No text was drawn")
        if text:
            texts = [c['text'] for c in text_calls]
            self.assertIn(text, texts, f"Text '{text}' not found in drawn texts: {texts}")
    
    def assert_image_drawn(self):
        """Assert that an image was drawn on display."""
        image_calls = [c for c in self.display_manager.draw_calls if c['type'] == 'image']
        self.assertGreater(len(image_calls), 0, "No image was drawn")
    
    def assert_cache_get(self, key: Optional[str] = None):
        """
        Assert that cache.get was called.
        
        Args:
            key: Optional key to check for
        """
        self.assertGreater(len(self.cache_manager.get_calls), 0, "cache.get was not called")
        if key:
            keys = [c['key'] for c in self.cache_manager.get_calls]
            self.assertIn(key, keys, f"Key '{key}' not found in cache.get calls: {keys}")
    
    def assert_cache_set(self, key: Optional[str] = None):
        """
        Assert that cache.set was called.
        
        Args:
            key: Optional key to check for
        """
        self.assertGreater(len(self.cache_manager.set_calls), 0, "cache.set was not called")
        if key:
            keys = [c['key'] for c in self.cache_manager.set_calls]
            self.assertIn(key, keys, f"Key '{key}' not found in cache.set calls: {keys}")
    
    def get_mock_config(self, **overrides) -> Dict[str, Any]:
        """
        Get mock configuration with optional overrides.
        
        Args:
            **overrides: Config values to override
        
        Returns:
            Configuration dictionary
        """
        config = self.plugin_config.copy()
        config.update(overrides)
        return config

