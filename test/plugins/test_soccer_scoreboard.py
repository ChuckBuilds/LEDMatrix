"""
Integration tests for soccer-scoreboard plugin.
"""

import pytest
from test.plugins.test_plugin_base import PluginTestBase


class TestSoccerScoreboardPlugin(PluginTestBase):
    """Test soccer-scoreboard plugin integration."""
    
    @pytest.fixture
    def plugin_id(self):
        return 'soccer-scoreboard'
    
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
        super().test_plugin_update_method(plugin_id)
    
    def test_plugin_display_method(self, plugin_id):
        """Test that plugin display() method works."""
        super().test_plugin_display_method(plugin_id)
    
    def test_plugin_has_display_modes(self, plugin_id):
        """Test that plugin has display modes."""
        manifest = self.load_plugin_manifest(plugin_id)
        assert 'display_modes' in manifest
        assert 'soccer_live' in manifest['display_modes']
        assert 'soccer_recent' in manifest['display_modes']
        assert 'soccer_upcoming' in manifest['display_modes']
    
    def test_plugin_has_get_display_modes(self, plugin_id):
        """Test that plugin can return display modes."""
        manifest = self.load_plugin_manifest(plugin_id)
        plugin_dir = self.plugins_dir / plugin_id
        entry_point = manifest['entry_point']
        class_name = manifest['class_name']
        
        module = self.plugin_loader.load_module(
            plugin_id=plugin_id,
            plugin_dir=plugin_dir,
            entry_point=entry_point
        )
        
        plugin_class = self.plugin_loader.get_plugin_class(
            plugin_id=plugin_id,
            module=module,
            class_name=class_name
        )
        
        config = self.base_config.copy()
        plugin_instance = self.plugin_loader.instantiate_plugin(
            plugin_id=plugin_id,
            plugin_class=plugin_class,
            config=config,
            display_manager=self.mock_display_manager,
            cache_manager=self.mock_cache_manager,
            plugin_manager=self.mock_plugin_manager
        )
        
        # Check if plugin has get_display_modes method
        if hasattr(plugin_instance, 'get_display_modes'):
            modes = plugin_instance.get_display_modes()
            assert isinstance(modes, list)
            assert len(modes) > 0
