from src.exceptions import CacheError, ConfigError, PluginError, DisplayError


class TestCustomExceptions:
    """Test custom exception classes."""
    
    def test_cache_error(self):
        """Test CacheError initialization."""
        error = CacheError("Cache failed", cache_key="test_key")
        # CacheError includes context in string representation
        assert "Cache failed" in str(error)
        assert error.context.get('cache_key') == "test_key"
        
    def test_config_error(self):
        """Test ConfigError initialization."""
        error = ConfigError("Config invalid", config_path='config.json')
        # ConfigError includes context in string representation
        assert "Config invalid" in str(error)
        assert error.context.get('config_path') == 'config.json'
        
    def test_plugin_error(self):
        """Test PluginError initialization."""
        error = PluginError("Plugin crashed", plugin_id='weather')
        # PluginError includes context in string representation
        assert "Plugin crashed" in str(error)
        assert error.context.get('plugin_id') == 'weather'
        
    def test_display_error(self):
        """Test DisplayError initialization."""
        error = DisplayError("Display not found", display_mode='adafruit')
        # DisplayError includes context in string representation
        assert "Display not found" in str(error)
        assert error.context.get('display_mode') == 'adafruit'

    def test_callers_context_dict_is_not_mutated(self):
        """A caller reusing one context dict across raises used to have
        every error's own key written into it."""
        cases = [
            (CacheError, {'cache_key': 'k'}),
            (ConfigError, {'config_path': 'c.json', 'field': 'f'}),
            (PluginError, {'plugin_id': 'weather'}),
            (DisplayError, {'display_mode': 'm'}),
        ]
        for cls, kwargs in cases:
            shared = {'attempt': 1}
            error = cls("boom", context=shared, **kwargs)
            assert shared == {'attempt': 1}, cls.__name__
            assert error.context == {'attempt': 1, **kwargs}, cls.__name__
