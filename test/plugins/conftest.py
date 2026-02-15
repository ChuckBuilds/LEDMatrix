"""
Pytest fixtures for plugin integration tests.
"""

import pytest
import os
import sys
import json
from pathlib import Path
from unittest.mock import MagicMock, Mock
from typing import Dict, Any

# Add project root to path
project_root = Path(__file__).parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

# Set emulator mode
os.environ['EMULATOR'] = 'true'


@pytest.fixture
def plugins_dir():
    """Get the plugins directory path.

    Checks plugins/ first, then falls back to plugin-repos/
    for monorepo development environments.
    """
    plugins_path = project_root / 'plugins'
    plugin_repos_path = project_root / 'plugin-repos'

    # Prefer plugins/ if it has actual plugin directories
    if plugins_path.exists() and any(
        p for p in plugins_path.iterdir()
        if p.is_dir() and not p.name.startswith('.')
    ):
        return plugins_path
    elif plugin_repos_path.exists():
        return plugin_repos_path
    return plugins_path


@pytest.fixture
def mock_display_manager():
    """Create a mock DisplayManager for plugin tests."""
    mock = MagicMock()
    mock.width = 128
    mock.height = 32
    mock.clear = Mock()
    mock.draw_text = Mock()
    mock.draw_image = Mock()
    mock.update_display = Mock()
    mock.get_font = Mock(return_value=None)
    # Some plugins access matrix.width/height
    mock.matrix = MagicMock()
    mock.matrix.width = 128
    mock.matrix.height = 32
    return mock


@pytest.fixture
def mock_cache_manager():
    """Create a mock CacheManager for plugin tests."""
    mock = MagicMock()
    mock._memory_cache = {}
    
    def mock_get(key: str, max_age: int = 300) -> Any:
        return mock._memory_cache.get(key)
    
    def mock_set(key: str, data: Any, ttl: int = None) -> None:
        mock._memory_cache[key] = data
    
    def mock_clear(key: str = None) -> None:
        if key:
            mock._memory_cache.pop(key, None)
        else:
            mock._memory_cache.clear()
    
    mock.get = Mock(side_effect=mock_get)
    mock.set = Mock(side_effect=mock_set)
    mock.clear = Mock(side_effect=mock_clear)
    return mock


@pytest.fixture
def mock_plugin_manager():
    """Create a mock PluginManager for plugin tests."""
    mock = MagicMock()
    mock.plugins = {}
    mock.plugin_manifests = {}
    return mock


@pytest.fixture
def base_plugin_config():
    """Base configuration for plugins."""
    return {
        'enabled': True,
        'update_interval': 300
    }


def load_plugin_manifest(plugin_id: str, plugins_dir: Path) -> Dict[str, Any]:
    """Load plugin manifest.json."""
    manifest_path = plugins_dir / plugin_id / 'manifest.json'
    if not manifest_path.exists():
        pytest.skip(f"Manifest not found for {plugin_id}")
    
    with open(manifest_path, 'r') as f:
        return json.load(f)


def get_plugin_config_schema(plugin_id: str, plugins_dir: Path) -> Dict[str, Any]:
    """Load plugin config_schema.json if it exists."""
    schema_path = plugins_dir / plugin_id / 'config_schema.json'
    if schema_path.exists():
        with open(schema_path, 'r') as f:
            return json.load(f)
    return None


@pytest.fixture
def visual_display_manager():
    """Create a VisualTestDisplayManager that renders real pixels for visual testing."""
    from src.plugin_system.testing import VisualTestDisplayManager
    return VisualTestDisplayManager(width=128, height=32)
