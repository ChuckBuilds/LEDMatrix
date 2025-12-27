"""
Tests for PluginLoader.

Tests plugin directory discovery, module loading, and class instantiation.
"""

import pytest
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, Mock, mock_open
from src.plugin_system.plugin_loader import PluginLoader
from src.exceptions import PluginError


class TestPluginLoader:
    """Test PluginLoader functionality."""
    
    @pytest.fixture
    def plugin_loader(self):
        """Create a PluginLoader instance."""
        return PluginLoader()
    
    @pytest.fixture
    def tmp_plugins_dir(self, tmp_path):
        """Create a temporary plugins directory."""
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        return plugins_dir
    
    def test_init(self):
        """Test PluginLoader initialization."""
        loader = PluginLoader()
        
        assert loader.logger is not None
        assert loader._loaded_modules == {}
    
    def test_find_plugin_directory_direct_path(self, plugin_loader, tmp_plugins_dir):
        """Test finding plugin directory by direct path."""
        plugin_dir = tmp_plugins_dir / "test_plugin"
        plugin_dir.mkdir()
        
        result = plugin_loader.find_plugin_directory(
            "test_plugin",
            tmp_plugins_dir
        )
        
        assert result == plugin_dir
    
    def test_find_plugin_directory_with_prefix(self, plugin_loader, tmp_plugins_dir):
        """Test finding plugin directory with ledmatrix- prefix."""
        plugin_dir = tmp_plugins_dir / "ledmatrix-test_plugin"
        plugin_dir.mkdir()
        
        result = plugin_loader.find_plugin_directory(
            "test_plugin",
            tmp_plugins_dir
        )
        
        assert result == plugin_dir
    
    def test_find_plugin_directory_from_mapping(self, plugin_loader, tmp_plugins_dir):
        """Test finding plugin directory from provided mapping."""
        plugin_dir = tmp_plugins_dir / "custom_plugin_name"
        plugin_dir.mkdir()
        
        plugin_directories = {
            "test_plugin": plugin_dir
        }
        
        result = plugin_loader.find_plugin_directory(
            "test_plugin",
            tmp_plugins_dir,
            plugin_directories=plugin_directories
        )
        
        assert result == plugin_dir
    
    def test_find_plugin_directory_not_found(self, plugin_loader, tmp_plugins_dir):
        """Test finding non-existent plugin directory."""
        result = plugin_loader.find_plugin_directory(
            "nonexistent_plugin",
            tmp_plugins_dir
        )
        
        assert result is None
    
    @patch('importlib.util.spec_from_file_location')
    @patch('importlib.util.module_from_spec')
    def test_load_module(self, mock_module_from_spec, mock_spec_from_file, plugin_loader, tmp_plugins_dir):
        """Test loading a plugin module."""
        plugin_dir = tmp_plugins_dir / "test_plugin"
        plugin_dir.mkdir()
        plugin_file = plugin_dir / "manager.py"
        plugin_file.write_text("# Plugin code")
        
        mock_spec = MagicMock()
        mock_spec.loader = MagicMock()
        mock_spec_from_file.return_value = mock_spec
        mock_module = MagicMock()
        mock_module_from_spec.return_value = mock_module
        
        result = plugin_loader.load_module("test_plugin", plugin_dir, "manager.py")
        
        assert result == mock_module
        mock_spec_from_file.assert_called_once()
        mock_module_from_spec.assert_called_once_with(mock_spec)
    
    def test_load_module_invalid_file(self, plugin_loader, tmp_plugins_dir):
        """Test loading invalid plugin module."""
        plugin_dir = tmp_plugins_dir / "test_plugin"
        plugin_dir.mkdir()
        # Don't create the entry file
        
        with pytest.raises(PluginError, match="Entry point file not found"):
            plugin_loader.load_module("test_plugin", plugin_dir, "nonexistent.py")
    
    def test_get_plugin_class(self, plugin_loader):
        """Test getting plugin class from module."""
        # Create a real class for testing
        class TestPlugin:
            pass
        
        mock_module = MagicMock()
        mock_module.Plugin = TestPlugin
        
        result = plugin_loader.get_plugin_class("test_plugin", mock_module, "Plugin")
        
        assert result == TestPlugin
    
    def test_get_plugin_class_not_found(self, plugin_loader):
        """Test getting non-existent plugin class from module."""
        mock_module = MagicMock()
        mock_module.__name__ = "test_module"
        # Use delattr to properly remove the attribute
        if hasattr(mock_module, 'Plugin'):
            delattr(mock_module, 'Plugin')
        
        with pytest.raises(PluginError, match="Class.*not found"):
            plugin_loader.get_plugin_class("test_plugin", mock_module, "Plugin")
    
    def test_instantiate_plugin(self, plugin_loader):
        """Test instantiating a plugin class."""
        mock_class = MagicMock()
        mock_instance = MagicMock()
        mock_class.return_value = mock_instance
        
        config = {"test": "config"}
        display_manager = MagicMock()
        cache_manager = MagicMock()
        plugin_manager = MagicMock()
        
        result = plugin_loader.instantiate_plugin(
            "test_plugin",
            mock_class,
            config,
            display_manager,
            cache_manager,
            plugin_manager
        )
        
        assert result == mock_instance
        # Plugin class is called with keyword arguments
        mock_class.assert_called_once_with(
            plugin_id="test_plugin",
            config=config,
            display_manager=display_manager,
            cache_manager=cache_manager,
            plugin_manager=plugin_manager
        )
    
    def test_instantiate_plugin_error(self, plugin_loader):
        """Test error handling when instantiating plugin class."""
        mock_class = MagicMock()
        mock_class.side_effect = Exception("Instantiation error")
        
        with pytest.raises(PluginError, match="Failed to instantiate"):
            plugin_loader.instantiate_plugin(
                "test_plugin",
                mock_class,
                {},
                MagicMock(),
                MagicMock(),
                MagicMock()
            )
    
    @patch('subprocess.run')
    def test_install_dependencies(self, mock_subprocess, plugin_loader, tmp_plugins_dir):
        """Test installing plugin dependencies."""
        plugin_dir = tmp_plugins_dir / "test_plugin"
        plugin_dir.mkdir()
        requirements_file = plugin_dir / "requirements.txt"
        requirements_file.write_text("package1==1.0.0\npackage2>=2.0.0\n")
        
        mock_subprocess.return_value = MagicMock(returncode=0)
        
        result = plugin_loader.install_dependencies(plugin_dir, "test_plugin")
        
        assert result is True
        mock_subprocess.assert_called_once()
    
    @patch('subprocess.run')
    def test_install_dependencies_no_requirements(self, mock_subprocess, plugin_loader, tmp_plugins_dir):
        """Test when no requirements.txt exists."""
        plugin_dir = tmp_plugins_dir / "test_plugin"
        plugin_dir.mkdir()
        
        result = plugin_loader.install_dependencies(plugin_dir, "test_plugin")
        
        assert result is True
        mock_subprocess.assert_not_called()
    
    @patch('subprocess.run')
    def test_install_dependencies_failure(self, mock_subprocess, plugin_loader, tmp_plugins_dir):
        """Test handling dependency installation failure."""
        plugin_dir = tmp_plugins_dir / "test_plugin"
        plugin_dir.mkdir()
        requirements_file = plugin_dir / "requirements.txt"
        requirements_file.write_text("package1==1.0.0\n")
        
        mock_subprocess.return_value = MagicMock(returncode=1)
        
        result = plugin_loader.install_dependencies(plugin_dir, "test_plugin")
        
        assert result is False
