"""
Configuration Service

Provides centralized configuration management with hot-reload support
and change notifications.

This service wraps ConfigManager and adds:
- File watching for automatic reload
- Change notifications to subscribers
- Thread-safe configuration access
"""

import json
import time
import threading
from pathlib import Path
from typing import Dict, Any, Optional, List, Callable, Tuple
from collections import defaultdict
import logging
import hashlib

from src.exceptions import ConfigError
from src.logging_config import get_logger
from src.config_manager import ConfigManager


class ConfigService:
    """
    Centralized configuration service with hot-reload.
    
    Features:
    - Automatic file watching and reload
    - Change notifications to subscribers
    - Thread-safe access
    """
    
    def __init__(
        self,
        config_manager: Optional[ConfigManager] = None,
        enable_hot_reload: bool = True
    ) -> None:
        """
        Initialize the configuration service.
        
        Args:
            config_manager: Optional ConfigManager instance (creates new if None)
            enable_hot_reload: Whether to enable automatic file watching
        """
        self.logger: logging.Logger = get_logger(__name__)
        self.config_manager: ConfigManager = config_manager or ConfigManager()
        self.enable_hot_reload: bool = enable_hot_reload
        
        # Thread safety
        self._lock: threading.RLock = threading.RLock()
        # Held across a whole reload -- read, swap, notify -- so one reload's
        # notifications finish before the next one's start. Subscribers run
        # under this lock and never under _lock: the display's per-plugin
        # subscriber can wait seconds for a busy plugin, and get_config(),
        # subscribe() and unsubscribe() -- called from the render thread --
        # must not wait behind it.
        self._notify_lock: threading.RLock = threading.RLock()
        # (key, callback, thread id) of the callback a notification is running,
        # so unsubscribe() can wait for that one call; signalled on its return.
        self._running_callback: Optional[Tuple[str, Callable[..., None], int]] = None
        self._callback_done = threading.Condition(self._lock)

        # Current configuration
        self._current_config: Dict[str, Any] = {}
        self._current_checksum: Optional[str] = None
        self._last_modified: Dict[str, float] = {}
        
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
        """Calculate checksum of configuration for change detection."""
        config_str = json.dumps(config, sort_keys=True)
        return hashlib.sha256(config_str.encode()).hexdigest()
    
    def _load_config(self) -> bool:
        """
        Load configuration from ConfigManager.
        
        Returns:
            True if config changed, False otherwise
        """
        try:
            with self._notify_lock:
                new_config = self.config_manager.load_config()
                new_checksum = self._calculate_checksum(new_config)

                with self._lock:
                    # Check if config actually changed
                    if new_checksum == self._current_checksum:
                        self.logger.debug("Configuration unchanged, skipping reload")
                        return False

                    # Store old config for change detection
                    old_config = self._current_config.copy()

                    # Update current config
                    self._current_config = new_config
                    self._current_checksum = new_checksum

                # Notify subscribers, outside _lock (see _notify_lock)
                self._notify_subscribers(old_config, new_config)

                self.logger.info(
                    "Configuration reloaded (checksum: %s)",
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

        Called without _lock held. The subscriber lists are copied under it,
        and each callback is checked against them again just before it runs.
        """
        with self._lock:
            subscribers = {key: list(callbacks) for key, callbacks in self._subscribers.items()}

        # Notify global subscribers (key: '*')
        for callback in subscribers.get('*', []):
            self._call_subscriber('*', callback, old_config, new_config)

        # Notify plugin-specific subscribers
        for plugin_id, callbacks in subscribers.items():
            if plugin_id == '*':
                continue

            old_plugin_config = old_config.get(plugin_id, {})
            new_plugin_config = new_config.get(plugin_id, {})

            # Only notify if plugin config actually changed
            if old_plugin_config != new_plugin_config:
                for callback in callbacks:
                    self._call_subscriber(plugin_id, callback,
                                          old_plugin_config, new_plugin_config)

    def _call_subscriber(
        self,
        key: str,
        callback: Callable[[Dict[str, Any], Dict[str, Any]], None],
        old_config: Dict[str, Any],
        new_config: Dict[str, Any],
    ) -> None:
        """Run one callback, unless it was unsubscribed since the snapshot.

        unsubscribe() promises that once it returns the callback is neither
        running nor will run: the display unloads the plugin straight after.
        """
        with self._lock:
            if callback not in self._subscribers.get(key, ()):
                return
            self._running_callback = (key, callback, threading.get_ident())
        try:
            callback(old_config, new_config)
        except Exception as e:
            if key == '*':
                self.logger.error("Error in global config change callback: %s", e, exc_info=True)
            else:
                self.logger.error(
                    "Error in config change callback for %s: %s",
                    key,
                    e,
                    exc_info=True
                )
        finally:
            with self._lock:
                self._running_callback = None
                self._callback_done.notify_all()

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
                        # Set from another thread; mypy keeps the while's narrowing.
                        break  # type: ignore[unreachable]
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
        
        Once this returns the callback is not running and will not be called
        again. A notification that is running this very callback is waited
        for (unless the callback is the caller); one running any other
        callback is not.

        Args:
            callback: Callback function to remove
            plugin_id: Optional plugin ID (must match subscription)
        """
        key = plugin_id or '*'
        with self._lock:
            if callback in self._subscribers[key]:
                self._subscribers[key].remove(callback)
                self.logger.debug("Unsubscribed from config changes for %s", key)
            while (self._running_callback is not None
                   and self._running_callback[:2] == (key, callback)
                   and self._running_callback[2] != threading.get_ident()):
                self._callback_done.wait()
    
    def shutdown(self) -> None:
        """Shutdown the configuration service."""
        self.logger.info("Shutting down configuration service")
        self._stop_file_watching()
        
        with self._lock:
            self._subscribers.clear()
