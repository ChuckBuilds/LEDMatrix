"""
Pytest configuration and fixtures for LEDMatrix tests.

Provides common fixtures for mocking core components and test setup.
"""

import pytest
import sys
from pathlib import Path
from unittest.mock import Mock, MagicMock, NonCallableMock
from typing import Dict, Any, Optional

# Add project root to path
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))


def pytest_configure(config):
    """Point the emulator at a per-process config that binds no socket.

    Six test modules set EMULATOR=true and build a real DisplayManager. The
    repo's emulator_config.json selects the "browser" adapter, which binds a
    fixed TCP port (8888) to serve the dev preview. That port is a machine-wide
    singleton, so a second pytest process -- a CI shard, another worktree, an
    agent running the suite alongside -- loses the bind. RGBMatrix construction
    then raises, DisplayManager falls back to ``self.matrix = None``, and every
    test that touches the matrix dies with a misleading
    ``AttributeError: 'NoneType' object has no attribute 'SwapOnVSync'``.

    The "raw" adapter renders in memory and binds nothing, so concurrent runs
    stop fighting over the port. The tests wrap SwapOnVSync on the matrix object
    itself, so they are indifferent to which adapter sits underneath. Only the
    adapter is overridden -- every other key is inherited from the repo config,
    which stays on "browser" for ``run.py -e``.
    """
    try:
        from RGBMatrixEmulator.internal.emulator_config import RGBMatrixEmulatorConfig
    except ImportError:
        return  # No emulator installed; the EMULATOR=true modules can't run anyway.

    import json
    import tempfile

    settings = {}
    try:
        settings = json.loads((project_root / "emulator_config.json").read_text())
    except (OSError, ValueError):
        pass  # Fall back to the emulator's own defaults; only the adapter matters.
    if not isinstance(settings, dict):
        settings = {}
    settings["display_adapter"] = "raw"
    # Falling back would land us back on the browser adapter and its fixed port.
    settings["allow_adapter_fallback"] = False

    tmp_dir = Path(tempfile.mkdtemp(prefix="ledmatrix-emulator-"))
    tmp_config = tmp_dir / "emulator_config.json"
    tmp_config.write_text(json.dumps(settings))
    # CONFIG_PATH is a bare relative filename resolved against the CWD; an
    # absolute path makes it independent of where pytest was invoked from.
    RGBMatrixEmulatorConfig.CONFIG_PATH = str(tmp_config)
    config._ledmatrix_emulator_tmp = tmp_dir


def pytest_unconfigure(config):
    """Remove the throwaway emulator config written by pytest_configure."""
    tmp_dir = getattr(config, "_ledmatrix_emulator_tmp", None)
    if tmp_dir is not None:
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)


# DisplayManager is a process-wide singleton, and the RGBMatrix /
# RGBMatrixOptions names it builds through are module globals bound once at
# import to either the emulator or the hardware library. All three are shared
# by every test module in the run, so a module that leaves a live instance in
# _instance -- or leaves patch('src.display_manager.RGBMatrix') standing --
# changes what the NEXT module constructs. None of that is visible when the
# affected file is run on its own; it surfaces as a full-suite failure that
# does not reproduce. The full-run failure diff is how a change is confirmed
# non-regressive, so it has to mean the same thing on every run.
_PRISTINE_MATRIX_BINDINGS = {}


@pytest.fixture(autouse=True, scope="module")
def _reset_display_manager_globals():
    """Undo any DisplayManager global state a test module leaves behind.

    Autouse fixtures are set up ahead of the fixtures a test asks for, so this
    one is finalised after them -- including after a module-scoped fixture that
    owns a real DisplayManager (test_display_dirty_tracking.py's ``dm``).
    Module-scoped rather than per-test: files that deliberately share one
    manager across their own tests keep doing so; only the leak across the
    module boundary is cut.
    """
    # Record the real bindings on the way in, while no patch of this module's
    # is active yet -- reading them on the way out would record the leak.
    _remember_matrix_bindings()
    yield

    dm_mod = sys.modules.get("src.display_manager")
    if dm_mod is None:
        return  # Module never imported it; nothing to reset.

    # An instance left here is what the next module's DisplayManager() call
    # gets back -- potentially one built against a MagicMock matrix.
    dm_mod.DisplayManager._instance = None
    dm_mod.DisplayManager._initialized = False

    # Put a binding back if a patch outlived the module that started it.
    # Restoring rather than failing: a leak reported against an innocent module
    # later in the run is the diagnosis problem, not the fix for it.
    for name, pristine in _PRISTINE_MATRIX_BINDINGS.items():
        if isinstance(getattr(dm_mod, name, None), NonCallableMock):
            setattr(dm_mod, name, pristine)


def _remember_matrix_bindings():
    dm_mod = sys.modules.get("src.display_manager")
    if dm_mod is None:
        return
    for name in ("RGBMatrix", "RGBMatrixOptions"):
        current = getattr(dm_mod, name, None)
        if current is not None and not isinstance(current, NonCallableMock):
            _PRISTINE_MATRIX_BINDINGS.setdefault(name, current)


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
    
    def mock_get(key: str, max_age: Optional[int] = 300,
                 memory_ttl: Optional[int] = None) -> Optional[Dict]:
        # Signature mirrors CacheManager.get — keep in sync or callers
        # passing keyword args (health tracker, resource monitor) break
        # only in tests, hiding real-API compatibility.
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


@pytest.fixture
def mock_plugin_instance(mock_display_manager, mock_cache_manager, mock_config_manager):
    """Create a mock plugin instance with all required methods."""
    from unittest.mock import MagicMock
    
    mock_plugin = MagicMock()
    mock_plugin.plugin_id = "test_plugin"
    mock_plugin.config = {"enabled": True, "display_duration": 30}
    mock_plugin.display_manager = mock_display_manager
    mock_plugin.cache_manager = mock_cache_manager
    mock_plugin.plugin_manager = MagicMock()
    mock_plugin.enabled = True
    
    # Required methods
    mock_plugin.update = MagicMock(return_value=None)
    mock_plugin.display = MagicMock(return_value=True)
    mock_plugin.get_display_duration = MagicMock(return_value=30.0)
    
    # Optional methods
    mock_plugin.supports_dynamic_duration = MagicMock(return_value=False)
    mock_plugin.get_dynamic_duration_cap = MagicMock(return_value=None)
    mock_plugin.is_cycle_complete = MagicMock(return_value=True)
    mock_plugin.reset_cycle_state = MagicMock(return_value=None)
    mock_plugin.has_live_priority = MagicMock(return_value=False)
    mock_plugin.has_live_content = MagicMock(return_value=False)
    mock_plugin.get_live_modes = MagicMock(return_value=[])
    mock_plugin.on_config_change = MagicMock(return_value=None)
    
    return mock_plugin


@pytest.fixture
def mock_plugin_with_live(mock_plugin_instance):
    """Create a mock plugin with live priority enabled."""
    mock_plugin_instance.has_live_priority = MagicMock(return_value=True)
    mock_plugin_instance.has_live_content = MagicMock(return_value=True)
    mock_plugin_instance.get_live_modes = MagicMock(return_value=["test_plugin_live"])
    mock_plugin_instance.config["live_priority"] = True
    return mock_plugin_instance


@pytest.fixture
def mock_plugin_with_dynamic(mock_plugin_instance):
    """Create a mock plugin with dynamic duration enabled."""
    mock_plugin_instance.supports_dynamic_duration = MagicMock(return_value=True)
    mock_plugin_instance.get_dynamic_duration_cap = MagicMock(return_value=180.0)
    mock_plugin_instance.is_cycle_complete = MagicMock(return_value=False)
    mock_plugin_instance.reset_cycle_state = MagicMock(return_value=None)
    mock_plugin_instance.config["dynamic_duration"] = {
        "enabled": True,
        "max_duration_seconds": 180
    }
    return mock_plugin_instance


@pytest.fixture
def test_config_with_plugins(test_config):
    """Provide a test configuration with multiple plugins enabled."""
    config = test_config.copy()
    config.update({
        "plugin1": {
            "enabled": True,
            "display_duration": 30,
            "update_interval": 300
        },
        "plugin2": {
            "enabled": True,
            "display_duration": 45,
            "update_interval": 600,
            "live_priority": True
        },
        "plugin3": {
            "enabled": False,
            "display_duration": 20
        },
        "display": {
            **config.get("display", {}),
            "display_durations": {
                "plugin1": 30,
                "plugin2": 45,
                "plugin3": 20
            },
            "dynamic_duration": {
                "max_duration_seconds": 180
            }
        }
    })
    return config


@pytest.fixture
def test_plugin_manager(mock_config_manager, mock_display_manager, mock_cache_manager):
    """Create a test PluginManager instance."""
    from unittest.mock import patch, MagicMock
    import tempfile
    from pathlib import Path
    
    # Create temporary plugin directory
    with tempfile.TemporaryDirectory() as tmpdir:
        plugin_dir = Path(tmpdir) / "plugins"
        plugin_dir.mkdir()
        
        with patch('src.plugin_system.plugin_manager.PluginManager') as MockPM:
            pm = MagicMock()
            pm.plugins = {}
            pm.plugin_manifests = {}
            pm.loaded_plugins = {}
            pm.plugin_last_update = {}
            pm.discover_plugins = MagicMock(return_value=[])
            pm.load_plugin = MagicMock(return_value=True)
            pm.unload_plugin = MagicMock(return_value=True)
            pm.get_plugin = MagicMock(return_value=None)
            pm.plugin_executor = MagicMock()
            pm.health_tracker = None
            pm.resource_monitor = None
            MockPM.return_value = pm
            yield pm


@pytest.fixture
def test_display_controller(mock_config_manager, mock_display_manager, mock_cache_manager, 
                            test_config_with_plugins, emulator_mode):
    """Create a test DisplayController instance with mocked dependencies."""
    from unittest.mock import patch, MagicMock
    from src.display_controller import DisplayController
    
    # Set up config manager to return test config
    mock_config_manager.get_config.return_value = test_config_with_plugins
    mock_config_manager.load_config.return_value = test_config_with_plugins
    
    with patch('src.display_controller.ConfigManager', return_value=mock_config_manager), \
         patch('src.display_controller.DisplayManager', return_value=mock_display_manager), \
         patch('src.display_controller.CacheManager', return_value=mock_cache_manager), \
         patch('src.display_controller.FontManager'), \
         patch('src.plugin_system.PluginManager') as mock_pm_class:
        
        # Set up plugin manager mock
        mock_pm = MagicMock()
        mock_pm.discover_plugins = MagicMock(return_value=[])
        mock_pm.load_plugin = MagicMock(return_value=True)
        mock_pm.get_plugin = MagicMock(return_value=None)
        mock_pm.plugins = {}
        mock_pm.loaded_plugins = {}
        mock_pm.plugin_manifests = {}
        mock_pm.plugin_last_update = {}
        mock_pm.plugin_executor = MagicMock()
        mock_pm.health_tracker = None
        mock_pm_class.return_value = mock_pm
        
        # Create controller
        controller = DisplayController()
        yield controller
        
        # Cleanup
        try:
            controller.cleanup()
        except Exception:
            pass

