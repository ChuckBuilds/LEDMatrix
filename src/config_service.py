"""
Configuration Service

Provides centralized configuration management with hot-reload support,
versioning, and change notifications.

This service wraps ConfigManager and adds:
- File watching for automatic reload
- Configuration versioning
- Change notifications to subscribers
- Thread-safe configuration access
"""

import json
import os
import time
import threading
from pathlib import Path
from typing import Dict, Any, Optional, List, Callable, Set
from datetime import datetime
from collections import defaultdict
import logging
import hashlib

from src.exceptions import ConfigError
from src.logging_config import get_logger
from src.config_manager import ConfigManager


class ConfigVersion:
    """Represents a configuration version snapshot."""
    
    def __init__(self, config: Dict[str, Any], version: int, timestamp: datetime, checksum: str):
        """
        Initialize a configuration version.
        
        Args:
            config: Configuration dictionary
            version: Version number
            timestamp: When this version was created
            checksum: MD5 checksum of the config
        """
        self.config: Dict[str, Any] = config
        self.version: int = version
        self.timestamp: datetime = timestamp
        self.checksum: str = checksum
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert version to dictionary."""
        return {
            'version': self.version,
            'timestamp': self.timestamp.isoformat(),
            'checksum': self.checksum,
            'config_size': len(json.dumps(self.config))
        }


class ConfigService:
    """
    Centralized configuration service with hot-reload and versioning.
    
    Features:
    - Automatic file watching and reload
    - Configuration versioning with history
    - Change notifications to subscribers
    - Thread-safe access
    - Backward compatible with ConfigManager
    """
    
    def __init__(
        self,
        config_manager: Optional[ConfigManager] = None,
        enable_hot_reload: bool = True,
        max_versions: int = 10
    ) -> None:
        """
        Initialize the configuration service.
        
        Args:
            config_manager: Optional ConfigManager instance (creates new if None)
            enable_hot_reload: Whether to enable automatic file watching
            max_versions: Maximum number of versions to keep in history
        """
        self.logger: logging.Logger = get_logger(__name__)
        self.config_manager: ConfigManager = config_manager or ConfigManager()
        self.enable_hot_reload: bool = enable_hot_reload
        self.max_versions: int = max_versions
        
        # Thread safety
        self._lock: threading.RLock = threading.RLock()
        
        # Current configuration
        self._current_config: Dict[str, Any] = {}
        self._current_version: int = 0
        self._last_modified: Dict[str, float] = {}
        
        # Version history
        self._versions: List[ConfigVersion] = []
        
        # Subscribers for change notifications
        # Format: {plugin_id or component_name: [callbacks]}
        self._subscribers: Dict[str, List[Callable[[Dict[str, Any], Dict[str, Any]], None]]] = defaultdict(list)
        
        # File watching
        self._watch_thread: Optional[threading.Thread] = None
        self._watch_interval: float = 2.0  # Check every 2 seconds
        self._stop_watching: bool = False
        
        # Load initial configuration
        self._load_config()
        
        # Start file watching if enabled
        if self.enable_hot_reload:
            self._start_file_watching()
    
    def _calculate_checksum(self, config: Dict[str, Any]) -> str:
        """Calculate MD5 checksum of configuration."""
        config_str = json.dumps(config, sort_keys=True)
        return hashlib.md5(config_str.encode()).hexdigest()
    
    def _load_config(self) -> bool:
        """
        Load configuration from ConfigManager.
        
        Returns:
            True if config changed, False otherwise
        """
        try:
            new_config = self.config_manager.load_config()
            new_checksum = self._calculate_checksum(new_config)
            
            with self._lock:
                # Check if config actually changed
                if self._current_version > 0:
                    old_checksum = self._versions[-1].checksum if self._versions else ""
                    if new_checksum == old_checksum:
                        self.logger.debug("Configuration unchanged, skipping reload")
                        return False
                
                # Store old config for change detection
                old_config = self._current_config.copy()
                
                # Create new version
                self._current_version += 1
                version = ConfigVersion(
                    config=new_config.copy(),
                    version=self._current_version,
                    timestamp=datetime.now(),
                    checksum=new_checksum
                )
                
                # Add to history
                self._versions.append(version)
                
                # Trim history if needed
                if len(self._versions) > self.max_versions:
                    self._versions.pop(0)
                
                # Update current config
                self._current_config = new_config
                
                # Notify subscribers
                self._notify_subscribers(old_config, new_config)
                
                self.logger.info(
                    "Configuration reloaded (version %d, checksum: %s)",
                    self._current_version,
                    new_checksum[:8]
                )
                
                return True
                
        except ConfigError as e:
            self.logger.error("Error loading configuration: %s", e, exc_info=True)
            return False
        except Exception as e:
            self.logger.error("Unexpected error loading configuration: %s", e, exc_info=True)
            return False
    
    def _notify_subscribers(self, old_config: Dict[str, Any], new_config: Dict[str, Any]) -> None:
        """
        Notify all subscribers of configuration changes.
        
        Args:
            old_config: Previous configuration
            new_config: New configuration
        """
        # Notify global subscribers (key: '*')
        for callback in self._subscribers.get('*', []):
            try:
                callback(old_config, new_config)
            except Exception as e:
                self.logger.error("Error in global config change callback: %s", e, exc_info=True)
        
        # Notify plugin-specific subscribers
        for plugin_id in self._subscribers.keys():
            if plugin_id == '*':
                continue
            
            old_plugin_config = old_config.get(plugin_id, {})
            new_plugin_config = new_config.get(plugin_id, {})
            
            # Only notify if plugin config actually changed
            if old_plugin_config != new_plugin_config:
                for callback in self._subscribers[plugin_id]:
                    try:
                        callback(old_plugin_config, new_plugin_config)
                    except Exception as e:
                        self.logger.error(
                            "Error in config change callback for %s: %s",
                            plugin_id,
                            e,
                            exc_info=True
                        )
    
    def _check_file_changes(self) -> bool:
        """
        Check if configuration files have been modified.
        
        Returns:
            True if files changed, False otherwise
        """
        config_path = Path(self.config_manager.get_config_path())
        secrets_path = Path(self.config_manager.get_secrets_path())
        
        changed = False
        
        # Check main config file
        if config_path.exists():
            mtime = config_path.stat().st_mtime
            if mtime != self._last_modified.get(str(config_path), 0):
                self._last_modified[str(config_path)] = mtime
                changed = True
        
        # Check secrets file
        if secrets_path.exists():
            mtime = secrets_path.stat().st_mtime
            if mtime != self._last_modified.get(str(secrets_path), 0):
                self._last_modified[str(secrets_path)] = mtime
                changed = True
        
        return changed
    
    def _file_watcher_loop(self) -> None:
        """Main loop for file watching."""
        self.logger.info("Configuration file watcher started")
        
        # Initialize last modified times
        config_path = Path(self.config_manager.get_config_path())
        secrets_path = Path(self.config_manager.get_secrets_path())
        
        if config_path.exists():
            self._last_modified[str(config_path)] = config_path.stat().st_mtime
        if secrets_path.exists():
            self._last_modified[str(secrets_path)] = secrets_path.stat().st_mtime
        
        while not self._stop_watching:
            try:
                if self._check_file_changes():
                    self.logger.info("Configuration files changed, reloading...")
                    self._load_config()
                
                # Sleep with periodic checks for stop signal
                for _ in range(int(self._watch_interval)):
                    if self._stop_watching:
                        break
                    time.sleep(1)
                    
            except Exception as e:
                self.logger.error("Error in file watcher loop: %s", e, exc_info=True)
                time.sleep(self._watch_interval)
        
        self.logger.info("Configuration file watcher stopped")
    
    def _start_file_watching(self) -> None:
        """Start the file watching thread."""
        if self._watch_thread and self._watch_thread.is_alive():
            return
        
        self._stop_watching = False
        self._watch_thread = threading.Thread(
            target=self._file_watcher_loop,
            name="ConfigService-Watcher",
            daemon=True
        )
        self._watch_thread.start()
        self.logger.debug("File watching thread started")
    
    def _stop_file_watching(self) -> None:
        """Stop the file watching thread."""
        if self._watch_thread and self._watch_thread.is_alive():
            self._stop_watching = True
            self._watch_thread.join(timeout=5.0)
            if self._watch_thread.is_alive():
                self.logger.warning("File watching thread did not stop gracefully")
    
    def get_config(self) -> Dict[str, Any]:
        """
        Get current configuration (thread-safe).
        
        Returns:
            Current configuration dictionary
        """
        with self._lock:
            return self._current_config.copy()
    
    def get_plugin_config(self, plugin_id: str) -> Dict[str, Any]:
        """
        Get configuration for a specific plugin.
        
        Args:
            plugin_id: Plugin identifier
            
        Returns:
            Plugin configuration dictionary
        """
        config = self.get_config()
        return config.get(plugin_id, {})
    
    def subscribe(
        self,
        callback: Callable[[Dict[str, Any], Dict[str, Any]], None],
        plugin_id: Optional[str] = None
    ) -> None:
        """
        Subscribe to configuration changes.
        
        Args:
            callback: Function to call when config changes
                     Signature: callback(old_config, new_config)
            plugin_id: Optional plugin ID to subscribe to specific plugin changes
                      If None, subscribes to all changes
        """
        key = plugin_id or '*'
        with self._lock:
            if callback not in self._subscribers[key]:
                self._subscribers[key].append(callback)
                self.logger.debug("Subscribed to config changes for %s", key)
    
    def unsubscribe(
        self,
        callback: Callable[[Dict[str, Any], Dict[str, Any]], None],
        plugin_id: Optional[str] = None
    ) -> None:
        """
        Unsubscribe from configuration changes.
        
        Args:
            callback: Callback function to remove
            plugin_id: Optional plugin ID (must match subscription)
        """
        key = plugin_id or '*'
        with self._lock:
            if callback in self._subscribers[key]:
                self._subscribers[key].remove(callback)
                self.logger.debug("Unsubscribed from config changes for %s", key)
    
    def reload(self) -> bool:
        """
        Manually reload configuration.
        
        Returns:
            True if reloaded successfully, False otherwise
        """
        self.logger.info("Manual configuration reload requested")
        return self._load_config()
    
    def get_version(self) -> int:
        """
        Get current configuration version.
        
        Returns:
            Current version number
        """
        with self._lock:
            return self._current_version
    
    def get_version_history(self) -> List[Dict[str, Any]]:
        """
        Get configuration version history.
        
        Returns:
            List of version dictionaries
        """
        with self._lock:
            return [v.to_dict() for v in self._versions]
    
    def get_version_config(self, version: int) -> Optional[Dict[str, Any]]:
        """
        Get configuration for a specific version.
        
        Args:
            version: Version number
            
        Returns:
            Configuration dictionary or None if version not found
        """
        with self._lock:
            for v in self._versions:
                if v.version == version:
                    return v.config.copy()
            return None
    
    def rollback(self, version: int) -> bool:
        """
        Rollback to a previous configuration version.
        
        Args:
            version: Version number to rollback to
            
        Returns:
            True if rollback successful, False otherwise
        """
        config = self.get_version_config(version)
        if config is None:
            self.logger.error("Version %d not found in history", version)
            return False
        
        try:
            # Save the rolled-back config
            self.config_manager.save_config(config)
            
            # Reload
            return self._load_config()
            
        except Exception as e:
            self.logger.error("Error rolling back to version %d: %s", version, e, exc_info=True)
            return False
    
    def save_config(self, new_config: Dict[str, Any]) -> bool:
        """
        Save new configuration.
        
        Args:
            new_config: New configuration dictionary
            
        Returns:
            True if saved successfully, False otherwise
        """
        try:
            self.config_manager.save_config(new_config)
            return self._load_config()
        except Exception as e:
            self.logger.error("Error saving configuration: %s", e, exc_info=True)
            return False
    
    def shutdown(self) -> None:
        """Shutdown the configuration service."""
        self.logger.info("Shutting down configuration service")
        self._stop_file_watching()
        
        with self._lock:
            self._subscribers.clear()
    
    # Backward compatibility methods
    def load_config(self) -> Dict[str, Any]:
        """
        Load configuration (backward compatibility with ConfigManager).
        
        Returns:
            Current configuration dictionary
        """
        return self.get_config()
    
    def get_config_path(self) -> str:
        """Get config file path (backward compatibility)."""
        return self.config_manager.get_config_path()
    
    def get_secrets_path(self) -> str:
        """Get secrets file path (backward compatibility)."""
        return self.config_manager.get_secrets_path()

