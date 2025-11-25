"""
Pytest configuration and fixtures for LEDMatrix tests.

Provides common fixtures for mocking core components and test setup.
"""

import pytest
import os
import sys
from pathlib import Path
from unittest.mock import Mock, MagicMock
from typing import Dict, Any, Optional

# Add project root to path
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))


@pytest.fixture
def mock_display_manager():
    """Create a mock DisplayManager for testing."""
    mock = MagicMock()
    mock.width = 128
    mock.height = 32
    mock.clear = Mock()
    mock.draw_text = Mock()
    mock.draw_image = Mock()
    mock.update_display = Mock()
    mock.get_font = Mock(return_value=None)
    return mock


@pytest.fixture
def mock_cache_manager():
    """Create a mock CacheManager for testing."""
    mock = MagicMock()
    mock._memory_cache = {}
    mock._memory_cache_timestamps = {}
    mock.cache_dir = "/tmp/test_cache"
    
    def mock_get(key: str, max_age: int = 300) -> Optional[Dict]:
        return mock._memory_cache.get(key)
    
    def mock_set(key: str, data: Dict, ttl: Optional[int] = None) -> None:
        mock._memory_cache[key] = data
    
    def mock_clear(key: Optional[str] = None) -> None:
        if key:
            mock._memory_cache.pop(key, None)
        else:
            mock._memory_cache.clear()
    
    mock.get = Mock(side_effect=mock_get)
    mock.set = Mock(side_effect=mock_set)
    mock.clear = Mock(side_effect=mock_clear)
    mock.get_cached_data = Mock(side_effect=mock_get)
    mock.save_cache = Mock(side_effect=mock_set)
    mock.load_cache = Mock(side_effect=mock_get)
    mock.get_cache_dir = Mock(return_value=mock.cache_dir)
    
    return mock


@pytest.fixture
def mock_config_manager():
    """Create a mock ConfigManager for testing."""
    mock = MagicMock()
    mock.config = {}
    mock.config_path = "config/config.json"
    mock.secrets_path = "config/config_secrets.json"
    mock.template_path = "config/config.template.json"
    
    def mock_load_config() -> Dict[str, Any]:
        return mock.config
    
    def mock_get_config() -> Dict[str, Any]:
        return mock.config
    
    def mock_get_secret(key: str) -> Optional[Any]:
        secrets = mock.config.get('_secrets', {})
        return secrets.get(key)
    
    mock.load_config = Mock(side_effect=mock_load_config)
    mock.get_config = Mock(side_effect=mock_get_config)
    mock.get_secret = Mock(side_effect=mock_get_secret)
    mock.get_config_path = Mock(return_value=mock.config_path)
    mock.get_secrets_path = Mock(return_value=mock.secrets_path)
    
    return mock


@pytest.fixture
def mock_plugin_manager():
    """Create a mock PluginManager for testing."""
    mock = MagicMock()
    mock.plugins = {}
    mock.plugin_manifests = {}
    mock.get_plugin = Mock(return_value=None)
    mock.load_plugin = Mock(return_value=True)
    mock.unload_plugin = Mock(return_value=True)
    return mock


@pytest.fixture
def test_config():
    """Provide a test configuration dictionary."""
    return {
        'display': {
            'hardware': {
                'rows': 32,
                'cols': 64,
                'chain_length': 2,
                'parallel': 1,
                'hardware_mapping': 'adafruit-hat-pwm',
                'brightness': 90
            },
            'runtime': {
                'gpio_slowdown': 2
            }
        },
        'timezone': 'UTC',
        'plugin_system': {
            'plugins_directory': 'plugins'
        }
    }


@pytest.fixture
def test_cache_dir(tmp_path):
    """Provide a temporary cache directory for testing."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    return str(cache_dir)


@pytest.fixture
def emulator_mode(monkeypatch):
    """Set emulator mode for testing."""
    monkeypatch.setenv("EMULATOR", "true")
    return True


@pytest.fixture(autouse=True)
def reset_logging():
    """Reset logging configuration before each test."""
    import logging
    logging.root.handlers = []
    logging.root.setLevel(logging.WARNING)
    yield
    logging.root.handlers = []
    logging.root.setLevel(logging.WARNING)

