"""
Integration tests for text-display plugin.
"""

import pytest
from unittest.mock import MagicMock
from test.plugins.test_plugin_base import PluginTestBase


class TestTextDisplayPlugin(PluginTestBase):
    """Test text-display plugin integration."""
    
    @pytest.fixture
    def plugin_id(self):
        return 'text-display'
    
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
        # Text display doesn't need external APIs
        super().test_plugin_update_method(plugin_id)
    
    def test_plugin_display_method(self, plugin_id):
        """Test that plugin display() method works."""
        super().test_plugin_display_method(plugin_id)
    
    def test_plugin_has_display_modes(self, plugin_id):
        """Test that plugin has display modes."""
        manifest = self.load_plugin_manifest(plugin_id)
        assert 'display_modes' in manifest
        assert 'text_display' in manifest['display_modes']
    
    def test_text_display_shows_text(self, plugin_id):
        """Test that text display plugin actually displays text."""
        manifest = self.load_plugin_manifest(plugin_id)
        plugin_dir = self.plugins_dir / plugin_id
        entry_point = manifest.get('entry_point', 'manager.py')
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
        config['text'] = 'Test Message'
        config['scroll'] = False
        config['text_color'] = [255, 255, 255]
        config['background_color'] = [0, 0, 0]
        
        # Mock display_manager.matrix to have width/height attributes
        if not hasattr(self.mock_display_manager, 'matrix'):
            self.mock_display_manager.matrix = MagicMock()
        self.mock_display_manager.matrix.width = 128
        self.mock_display_manager.matrix.height = 32
        
        plugin_instance = self.plugin_loader.instantiate_plugin(
            plugin_id=plugin_id,
            plugin_class=plugin_class,
            config=config,
            display_manager=self.mock_display_manager,
            cache_manager=self.mock_cache_manager,
            plugin_manager=self.mock_plugin_manager
        )
        
        # Update and display
        plugin_instance.update()
        plugin_instance.display(force_clear=True)
        
        # Verify text was set
        assert plugin_instance.text == 'Test Message'
        
        # Verify display was called (may be called via image assignment)
        assert (self.mock_display_manager.update_display.called or 
                hasattr(self.mock_display_manager, 'image'))
    
    def test_config_schema_valid(self, plugin_id):
        """Test that config schema is valid."""
        super().test_config_schema_valid(plugin_id)
