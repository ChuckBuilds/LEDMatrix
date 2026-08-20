"""
Startup Validator

Validates system configuration, plugins, and dependencies on startup.
Fails fast with clear error messages to prevent runtime issues.
"""

import os
from typing import Any, List, Optional, Tuple
from pathlib import Path
from src.exceptions import ConfigError, PluginError, CacheError
from src.logging_config import get_logger


class StartupValidator:
    """Validates system state on startup."""
    
    def __init__(self, config_manager: Any, plugin_manager: Optional[Any] = None,
                 cache_manager: Optional[Any] = None) -> None:
        """
        Initialize the startup validator.

        Args:
            config_manager: ConfigManager instance
            plugin_manager: Optional PluginManager instance
            cache_manager: The CacheManager the application will actually use.
                Pass it. Without one this validator builds its own just to read
                a directory path, which reports on a cache the app does not
                use and leaves behind a cleanup thread that nothing stops --
                validation runs twice per startup, so that was two of them.
        """
        self.config_manager = config_manager
        self.plugin_manager = plugin_manager
        self.cache_manager = cache_manager
        self.logger = get_logger(__name__)
        self.errors: List[str] = []
        self.warnings: List[str] = []
    
    def validate_all(self) -> Tuple[bool, List[str], List[str]]:
        """
        Run all validation checks.
        
        Returns:
            Tuple of (is_valid, errors, warnings)
        """
        self.logger.info("Starting startup validation...")

        # Fresh lists each run — without this, calling validate_all() twice
        # duplicated every message.
        self.errors = []
        self.warnings = []

        # Validate configuration
        self._validate_config()
        
        # Validate cache directory
        self._validate_cache_directory()
        
        # Validate display configuration
        self._validate_display_config()
        
        # Validate plugins if plugin manager is available
        if self.plugin_manager:
            self._validate_plugins()

        # Warn when the running systemd unit no longer matches the repo's
        self._validate_systemd_units()
        
        is_valid = len(self.errors) == 0
        
        if is_valid:
            self.logger.info("Startup validation passed")
            if self.warnings:
                self.logger.warning(f"Startup validation completed with {len(self.warnings)} warning(s)")
        else:
            self.logger.error(f"Startup validation failed with {len(self.errors)} error(s)")
        
        return (is_valid, self.errors.copy(), self.warnings.copy())
    
    #: Units this project installs, and where each is installed to.
    _UNITS = (
        ("systemd/ledmatrix.service", "/etc/systemd/system/ledmatrix.service"),
        ("systemd/ledmatrix-web.service", "/etc/systemd/system/ledmatrix-web.service"),
    )

    def _validate_systemd_units(self) -> None:
        """Warn when an installed unit has drifted from the repo's template.

        Nothing re-applies these after the first install. `git pull` -- which is
        what the web UI's update button runs -- brings a new template into the
        checkout, but nothing copies it to /etc/systemd/system and nothing runs
        `systemctl daemon-reload`, so the unit that actually runs is whatever
        first_time_install.sh wrote on day one.

        That makes every hardening added to a unit inert on existing installs.
        Measured on one rig: the installed unit was thirteen days older than the
        repo's and differed in content, so a MemoryMax the repo had specified
        was not being enforced at all -- `systemctl show` reported
        MemoryMax=infinity.

        A warning rather than an error, and certainly not a silent rewrite:
        editing files under /etc and restarting services is the installer's job,
        not something a display process should do to a machine while it boots.
        The remedy is to re-run scripts/install/install_service.sh.
        """
        try:
            project_root = Path(__file__).resolve().parent.parent
            for template_rel, installed_path in self._UNITS:
                template = project_root / template_rel
                installed = Path(installed_path)
                if not template.is_file() or not installed.is_file():
                    continue

                # The template carries placeholders the installer substitutes,
                # so compare the substituted form rather than the raw file.
                expected = template.read_text(encoding="utf-8")
                expected = expected.replace("__PROJECT_ROOT_DIR__", str(project_root))
                expected = expected.replace("__USER__", "root")

                try:
                    actual = installed.read_text(encoding="utf-8")
                except PermissionError:
                    continue

                if self._unit_body(expected) != self._unit_body(actual):
                    self.warnings.append(
                        f"{installed.name} differs from {template_rel}; the "
                        "installed unit is not refreshed by an update, so "
                        "settings added to the template are not in effect. "
                        "Re-run scripts/install/install_service.sh to apply them."
                    )
        except OSError as e:
            self.logger.debug("Could not compare systemd units: %s", e)

    @staticmethod
    def _unit_body(text: str) -> str:
        """A unit's meaningful lines: no comments, no blanks, no ordering noise."""
        lines = []
        for line in text.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                lines.append(line)
        return "\n".join(sorted(lines))

    def _validate_config(self) -> None:
        """Validate configuration files."""
        try:
            config = self.config_manager.load_config()
            
            # Check for required top-level keys
            required_keys = ['display', 'timezone']
            for key in required_keys:
                if key not in config:
                    self.errors.append(f"Missing required configuration key: {key}")
            
            # Validate display configuration
            display_config = config.get('display', {})
            if not display_config:
                self.errors.append("Display configuration is missing or empty")
            
        except ConfigError as e:
            self.errors.append(f"Configuration error: {e}")
        except Exception as e:
            self.errors.append(f"Unexpected error validating configuration: {e}")
    
    def _validate_cache_directory(self) -> None:
        """Validate cache directory permissions."""
        try:
            cache_manager = self.cache_manager
            if cache_manager is None:
                # No caller supplied one (older embedders, direct use in a
                # script). Build one, but do not leave its cleanup thread
                # running behind us -- this instance is discarded on the next
                # line but the thread is a closure over it, so it would never
                # be collected.
                from src.cache_manager import CacheManager
                cache_manager = CacheManager()
                try:
                    cache_dir = cache_manager.get_cache_dir()
                finally:
                    cache_manager.stop_cleanup_thread()
            else:
                cache_dir = cache_manager.get_cache_dir()
            
            if not cache_dir:
                self.warnings.append("Cache directory not available - caching will be disabled")
                return
            
            # Check if directory exists and is writable
            if not os.path.exists(cache_dir):
                self.errors.append(f"Cache directory does not exist: {cache_dir}")
                return
            
            if not os.access(cache_dir, os.W_OK):
                self.errors.append(f"Cache directory is not writable: {cache_dir}")
                return
            
            # Test write access
            test_file = os.path.join(cache_dir, '.startup_test')
            try:
                with open(test_file, 'w') as f:
                    f.write('test')
                os.remove(test_file)
            except (IOError, OSError) as e:
                self.errors.append(f"Cannot write to cache directory {cache_dir}: {e}")
        
        except Exception as e:
            self.warnings.append(f"Could not validate cache directory: {e}")
    
    def _validate_display_config(self) -> None:
        """Validate display configuration."""
        try:
            config = self.config_manager.get_config()
            display_config = config.get('display', {})
            
            if not display_config:
                self.errors.append("Display configuration is missing")
                return
            
            hardware_config = display_config.get('hardware', {})
            if not hardware_config:
                self.errors.append("Display hardware configuration is missing")
                return
            
            # Check required hardware settings
            required_hardware = ['rows', 'cols']
            for key in required_hardware:
                if key not in hardware_config:
                    self.warnings.append(f"Display hardware setting '{key}' not specified, using default")
        
        except Exception as e:
            self.warnings.append(f"Could not validate display configuration: {e}")
    
    def _validate_plugins(self) -> None:
        """Validate plugin configurations and dependencies."""
        if not self.plugin_manager:
            return
        
        try:
            # Get enabled plugins from config
            config = self.config_manager.get_config()
            discovered_plugins = self.plugin_manager.discover_plugins()
            
            # Check for enabled plugins that don't exist
            for plugin_id, plugin_config in config.items():
                # Skip non-plugin config sections
                if plugin_id in ['display', 'schedule', 'timezone', 'plugin_system']:
                    continue
                
                if not isinstance(plugin_config, dict):
                    continue
                
                if plugin_config.get('enabled', False):
                    if plugin_id not in discovered_plugins:
                        self.warnings.append(f"Plugin '{plugin_id}' is enabled but not found in plugins directory")
            
            # Validate plugin configurations
            for plugin_id in discovered_plugins:
                plugin_config = config.get(plugin_id, {})
                if plugin_config.get('enabled', False):
                    # Check if plugin can be loaded (without actually loading it)
                    plugin_dir = self.plugin_manager.get_plugin_directory(plugin_id)
                    if plugin_dir:
                        manifest_path = Path(plugin_dir) / "manifest.json"
                        if not manifest_path.exists():
                            self.errors.append(f"Plugin '{plugin_id}' manifest.json not found")
        
        except Exception as e:
            self.warnings.append(f"Could not validate plugins: {e}")
    
    def raise_on_errors(self) -> None:
        """
        Raise exceptions if validation errors exist.
        
        Raises:
            ConfigError: If configuration validation fails
            CacheError: If cache validation fails
            PluginError: If plugin validation fails
        """
        if not self.errors:
            return
        
        # Group errors by type
        config_errors = [e for e in self.errors if 'configuration' in e.lower() or 'config' in e.lower()]
        cache_errors = [e for e in self.errors if 'cache' in e.lower()]
        plugin_errors = [e for e in self.errors if 'plugin' in e.lower()]
        other_errors = [e for e in self.errors if e not in config_errors + cache_errors + plugin_errors]
        
        # Raise appropriate exceptions
        if config_errors:
            raise ConfigError("Configuration validation failed", context={'errors': config_errors})
        
        if cache_errors:
            raise CacheError("Cache validation failed", context={'errors': cache_errors})
        
        if plugin_errors:
            raise PluginError("Plugin validation failed", context={'errors': plugin_errors})
        
        if other_errors:
            raise ConfigError("Startup validation failed", context={'errors': other_errors})

