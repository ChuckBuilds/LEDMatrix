"""
Integration tests for odds-ticker plugin.
"""

import pytest
from test.plugins.test_plugin_base import PluginTestBase


class TestOddsTickerPlugin(PluginTestBase):
    """Test odds-ticker plugin integration."""
    
    @pytest.fixture
    def plugin_id(self):
        return 'odds-ticker'
    
    def test_manifest_exists(self, plugin_id):
        """Test that plugin manifest exists."""
        super().test_manifest_exists(plugin_id)
    
    def test_manifest_has_required_fields(self, plugin_id):
        """Test that manifest has all required fields."""
        super().test_manifest_has_required_fields(plugin_id)
    
    def test_plugin_can_be_loaded(self, plugin_id):
        """Test that plugin module can be loaded."""
        super().test_plugin_can_be_loaded(plugin_id)
    
    def test_plugin_class_exists(self, plugin_id):
        """Test that plugin class exists."""
        super().test_plugin_class_exists(plugin_id)
    
    def test_plugin_can_be_instantiated(self, plugin_id):
        """Test that plugin can be instantiated."""
        super().test_plugin_can_be_instantiated(plugin_id)
    
    def test_plugin_has_required_methods(self, plugin_id):
        """Test that plugin has required methods."""
        super().test_plugin_has_required_methods(plugin_id)
    
    def test_plugin_update_method(self, plugin_id):
        """Test that plugin update() method works."""
        # Odds ticker may need API access, but should handle gracefully
        super().test_plugin_update_method(plugin_id)
    
    def test_plugin_display_method(self, plugin_id):
        """Test that plugin display() method works."""
        super().test_plugin_display_method(plugin_id)
    
    def test_plugin_has_display_modes(self, plugin_id):
        """Test that plugin has display modes."""
        manifest = self.load_plugin_manifest(plugin_id)
        assert 'display_modes' in manifest
        assert 'odds_ticker' in manifest['display_modes']
    
    def test_config_schema_valid(self, plugin_id):
        """Test that config schema is valid."""
        super().test_config_schema_valid(plugin_id)
