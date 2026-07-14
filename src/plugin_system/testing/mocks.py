"""
Mock objects for plugin testing.

Provides mock implementations of display_manager, cache_manager, config_manager,
and plugin_manager for use in plugin unit tests.
"""

from typing import Dict, Any, Optional
from PIL import Image


class MockDisplayManager:
    """Mock display manager for testing."""
    
    def __init__(self, width: int = 128, height: int = 32):
        self.width = width
        self.display_width = width
        self.height = height
        self.display_height = height
        self.image = Image.new('RGB', (width, height), color=(0, 0, 0))
        self.clear_called = False
        self.update_called = False
        self.draw_calls = []
    
    def clear(self):
        """Clear the display."""
        self.clear_called = True
        self.image = Image.new('RGB', (self.width, self.height), color=(0, 0, 0))
    
    def update_display(self):
        """Update the display."""
        self.update_called = True
    
    def draw_text(self, text: str, x: int, y: int, color: tuple = (255, 255, 255), font=None):
        """Draw text on the display."""
        self.draw_calls.append({
            'type': 'text',
            'text': text,
            'x': x,
            'y': y,
            'color': color,
            'font': font
        })
    
    def draw_image(self, image: Image.Image, x: int, y: int):
        """Draw an image on the display."""
        self.draw_calls.append({
            'type': 'image',
            'image': image,
            'x': x,
            'y': y
        })
    
    def reset(self):
        """Reset mock state."""
        self.clear_called = False
        self.update_called = False
        self.draw_calls = []
        self.image = Image.new('RGB', (self.width, self.height), color=(0, 0, 0))


class MockCacheManager:
    """Mock cache manager for testing."""
    
    def __init__(self):
        import shutil
        import tempfile
        import weakref
        self._cache: Dict[str, Any] = {}
        self._cache_timestamps: Dict[str, float] = {}
        self.get_calls = []
        self.set_calls = []
        self.delete_calls = []
        self.get_cached_data_with_strategy_calls = []
        # Real temp dir for plugins that write/read files under cache_dir.
        # Registered for cleanup so each mock instance doesn't leak a tmp dir.
        self.cache_dir = tempfile.mkdtemp(prefix="ledmatrix-mock-cache-")
        self._finalizer = weakref.finalize(
            self, shutil.rmtree, self.cache_dir, ignore_errors=True)

    def cleanup(self) -> None:
        """Remove the temp cache directory created for this instance."""
        self._finalizer()
    
    def get(self, key: str, max_age: Optional[float] = None) -> Optional[Any]:
        """Get a value from cache."""
        import time
        self.get_calls.append({'key': key, 'max_age': max_age})
        
        if key not in self._cache:
            return None
        
        if max_age is not None:
            timestamp = self._cache_timestamps.get(key, 0)
            if time.time() - timestamp > max_age:
                return None
        
        return self._cache.get(key)
    
    def set(self, key: str, value: Any, ttl: Optional[float] = None) -> None:
        """Set a value in cache."""
        import time
        self.set_calls.append({'key': key, 'value': value, 'ttl': ttl})
        self._cache[key] = value
        self._cache_timestamps[key] = time.time()
    
    def delete(self, key: str) -> None:
        """Delete a value from cache."""
        self.delete_calls.append(key)
        if key in self._cache:
            del self._cache[key]

    def get_cached_data_with_strategy(self, key: str, data_type: str = 'default') -> Optional[Any]:
        """Mock of CacheManager.get_cached_data_with_strategy (src/cache_manager.py).

        The real method picks a max_age/memory_ttl strategy per data_type
        (and extends it during market-closed hours for market data) before
        delegating to get_cached_data(). None of that timing nuance matters
        for a mock -- plugins under test just need the method to exist and
        return whatever was cached, so this delegates straight to get().
        """
        self.get_cached_data_with_strategy_calls.append({'key': key, 'data_type': data_type})
        return self.get(key)

    def save_cache(self, key: str, data: Any) -> None:
        """Mock of CacheManager.save_cache (src/cache_manager.py) -- the
        write-side counterpart to get_cached_data_with_strategy, used by the
        same real-CacheManager-oriented plugins. Delegates to set()."""
        self.set(key, data)
        if key in self._cache_timestamps:
            del self._cache_timestamps[key]
    
    def reset(self):
        """Reset mock state."""
        self._cache.clear()
        self._cache_timestamps.clear()
        self.get_calls = []
        self.set_calls = []
        self.delete_calls = []
        self.get_cached_data_with_strategy_calls = []


class MockConfigManager:
    """Mock config manager for testing."""
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self._config = config or {}
        self.load_config_calls = []
        self.save_config_calls = []
    
    def load_config(self) -> Dict[str, Any]:
        """Load configuration."""
        self.load_config_calls.append({})
        return self._config.copy()
    
    def save_config(self, config: Dict[str, Any]) -> None:
        """Save configuration."""
        self.save_config_calls.append(config)
        self._config = config.copy()
    
    def get_config(self, key: str, default: Any = None) -> Any:
        """Get a config value."""
        return self._config.get(key, default)
    
    def set_config(self, key: str, value: Any) -> None:
        """Set a config value."""
        self._config[key] = value
    
    def reset(self):
        """Reset mock state."""
        self._config = {}
        self.load_config_calls = []
        self.save_config_calls = []


class MockPluginManager:
    """Mock plugin manager for testing."""
    
    def __init__(self):
        self.plugins: Dict[str, Any] = {}
        self.plugin_manifests: Dict[str, Dict] = {}
        self.get_plugin_calls = []
        self.get_all_plugins_calls = []
        # Real FontManager so BasePlugin.layout / draw_fit behave identically
        # under the harness (it only needs assets/fonts on disk).
        try:
            from src.font_manager import FontManager
            self.font_manager: Optional[Any] = FontManager({})
        except Exception:
            self.font_manager = None
    
    def get_plugin(self, plugin_id: str) -> Optional[Any]:
        """Get a plugin instance."""
        self.get_plugin_calls.append(plugin_id)
        return self.plugins.get(plugin_id)
    
    def get_all_plugins(self) -> Dict[str, Any]:
        """Get all plugin instances."""
        self.get_all_plugins_calls.append({})
        return self.plugins.copy()
    
    def get_plugin_info(self, plugin_id: str) -> Optional[Dict[str, Any]]:
        """Get plugin information."""
        manifest = self.plugin_manifests.get(plugin_id, {})
        plugin = self.plugins.get(plugin_id)
        if plugin:
            manifest['loaded'] = True
            manifest['runtime_info'] = getattr(plugin, 'get_info', lambda: {})()
        else:
            manifest['loaded'] = False
        return manifest
    
    def reset(self):
        """Reset mock state."""
        self.plugins.clear()
        self.plugin_manifests.clear()
        self.get_plugin_calls = []
        self.get_all_plugins_calls = []

