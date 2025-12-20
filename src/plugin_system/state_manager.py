"""
Centralized plugin state management.

Provides a single source of truth for plugin state (installed, enabled, version, etc.)
with state change events and persistence.
"""

import json
import threading
from typing import Dict, Any, Optional, List, Callable
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, asdict
from enum import Enum

from src.logging_config import get_logger


class PluginStateStatus(Enum):
    """Status of a plugin."""
    INSTALLED = "installed"
    ENABLED = "enabled"
    DISABLED = "disabled"
    ERROR = "error"
    UNKNOWN = "unknown"


@dataclass
class PluginState:
    """Represents the state of a plugin."""
    plugin_id: str
    status: PluginStateStatus
    enabled: bool
    version: Optional[str] = None
    installed_at: Optional[datetime] = None
    last_updated: Optional[datetime] = None
    config_version: int = 1  # For detecting state corruption
    metadata: Dict[str, Any] = None
    
    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert state to dictionary for serialization."""
        result = asdict(self)
        # Convert enum to string
        result['status'] = self.status.value
        # Convert datetime to ISO string
        if result.get('installed_at'):
            result['installed_at'] = self.installed_at.isoformat()
        if result.get('last_updated'):
            result['last_updated'] = self.last_updated.isoformat()
        return result
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'PluginState':
        """Create state from dictionary."""
        # Parse enum
        if isinstance(data.get('status'), str):
            data['status'] = PluginStateStatus(data['status'])
        
        # Parse datetime
        if data.get('installed_at') and isinstance(data['installed_at'], str):
            data['installed_at'] = datetime.fromisoformat(data['installed_at'])
        if data.get('last_updated') and isinstance(data['last_updated'], str):
            data['last_updated'] = datetime.fromisoformat(data['last_updated'])
        
        return cls(**data)


class PluginStateManager:
    """
    Centralized plugin state manager.
    
    Provides:
    - Single source of truth for plugin state
    - State change events/notifications
    - State persistence
    - State versioning
    """
    
    def __init__(
        self,
        state_file: Optional[str] = None,
        auto_save: bool = True,
        lazy_load: bool = False
    ):
        """
        Initialize state manager.
        
        Args:
            state_file: Path to file for persisting state
            auto_save: Whether to automatically save state on changes
            lazy_load: If True, defer loading state file until first access
        """
        self.logger = get_logger(__name__)
        self.state_file = Path(state_file) if state_file else None
        self.auto_save = auto_save
        self._lazy_load = lazy_load
        self._state_loaded = False
        
        # State storage
        self._states: Dict[str, PluginState] = {}
        self._state_version = 1
        
        # State change callbacks
        self._callbacks: Dict[str, List[Callable[[str, PluginState, PluginState], None]]] = {}
        
        # Threading
        self._lock = threading.RLock()
        
        # Load state from file if it exists (unless lazy loading)
        if not self._lazy_load and self.state_file and self.state_file.exists():
            self._load_state()
            self._state_loaded = True
    
    def _ensure_loaded(self) -> None:
        """Ensure state is loaded (for lazy loading)."""
        if not self._state_loaded and self.state_file and self.state_file.exists():
            self._load_state()
            self._state_loaded = True
    
    def get_plugin_state(self, plugin_id: str) -> Optional[PluginState]:
        """
        Get state for a plugin.
        
        Args:
            plugin_id: Plugin identifier
        
        Returns:
            PluginState if found, None otherwise
        """
        self._ensure_loaded()
        with self._lock:
            return self._states.get(plugin_id)
    
    def get_all_states(self) -> Dict[str, PluginState]:
        """
        Get all plugin states.
        
        Returns:
            Dictionary mapping plugin_id to PluginState
        """
        self._ensure_loaded()
        with self._lock:
            return self._states.copy()
    
    def update_plugin_state(
        self,
        plugin_id: str,
        updates: Dict[str, Any],
        notify: bool = True
    ) -> bool:
        """
        Update plugin state.
        
        Args:
            plugin_id: Plugin identifier
            updates: Dictionary of state updates
            notify: Whether to notify callbacks of changes
        
        Returns:
            True if update successful
        """
        self._ensure_loaded()
        with self._lock:
            # Get current state or create new
            current_state = self._states.get(plugin_id)
            if not current_state:
                current_state = PluginState(
                    plugin_id=plugin_id,
                    status=PluginStateStatus.UNKNOWN,
                    enabled=False
                )
            
            # Create new state with updates
            old_state = PluginState(
                plugin_id=current_state.plugin_id,
                status=current_state.status,
                enabled=current_state.enabled,
                version=current_state.version,
                installed_at=current_state.installed_at,
                last_updated=current_state.last_updated,
                config_version=current_state.config_version,
                metadata=current_state.metadata.copy() if current_state.metadata else {}
            )
            
            # Apply updates
            if 'status' in updates:
                if isinstance(updates['status'], str):
                    current_state.status = PluginStateStatus(updates['status'])
                else:
                    current_state.status = updates['status']
            
            if 'enabled' in updates:
                current_state.enabled = bool(updates['enabled'])
            
            if 'version' in updates:
                current_state.version = updates['version']
            
            if 'installed_at' in updates:
                current_state.installed_at = updates['installed_at']
            
            if 'last_updated' in updates:
                current_state.last_updated = updates['last_updated']
            else:
                current_state.last_updated = datetime.now()
            
            if 'metadata' in updates:
                if current_state.metadata is None:
                    current_state.metadata = {}
                current_state.metadata.update(updates['metadata'])
            
            # Increment config version
            current_state.config_version += 1
            
            # Store updated state
            self._states[plugin_id] = current_state
            
            # Notify callbacks
            if notify:
                self._notify_callbacks(plugin_id, old_state, current_state)
            
            # Auto-save if enabled
            if self.auto_save:
                self._save_state()
            
            return True
    
    def set_plugin_enabled(self, plugin_id: str, enabled: bool) -> bool:
        """
        Set plugin enabled/disabled state.
        
        Args:
            plugin_id: Plugin identifier
            enabled: Whether plugin is enabled
        
        Returns:
            True if update successful
        """
        status = PluginStateStatus.ENABLED if enabled else PluginStateStatus.DISABLED
        return self.update_plugin_state(
            plugin_id,
            {
                'enabled': enabled,
                'status': status
            }
        )
    
    def set_plugin_installed(
        self,
        plugin_id: str,
        version: Optional[str] = None,
        installed_at: Optional[datetime] = None
    ) -> bool:
        """
        Mark plugin as installed.
        
        Args:
            plugin_id: Plugin identifier
            version: Plugin version
            installed_at: Installation timestamp
        
        Returns:
            True if update successful
        """
        return self.update_plugin_state(
            plugin_id,
            {
                'status': PluginStateStatus.INSTALLED,
                'version': version,
                'installed_at': installed_at or datetime.now()
            }
        )
    
    def set_plugin_error(self, plugin_id: str, error: Optional[str] = None) -> bool:
        """
        Mark plugin as having an error.
        
        Args:
            plugin_id: Plugin identifier
            error: Optional error message
        
        Returns:
            True if update successful
        """
        updates = {'status': PluginStateStatus.ERROR}
        if error:
            updates['metadata'] = {'last_error': error}
        
        return self.update_plugin_state(plugin_id, updates)
    
    def remove_plugin_state(self, plugin_id: str) -> bool:
        """
        Remove plugin state (e.g., after uninstall).
        
        Args:
            plugin_id: Plugin identifier
        
        Returns:
            True if removal successful
        """
        self._ensure_loaded()
        with self._lock:
            if plugin_id in self._states:
                old_state = self._states[plugin_id]
                del self._states[plugin_id]
                
                # Notify callbacks
                self._notify_callbacks(plugin_id, old_state, None)
                
                # Auto-save if enabled
                if self.auto_save:
                    self._save_state()
                
                return True
        
        return False
    
    def subscribe_to_state_changes(
        self,
        callback: Callable[[str, PluginState, Optional[PluginState]], None],
        plugin_id: Optional[str] = None
    ) -> str:
        """
        Subscribe to state changes.
        
        Args:
            callback: Callback function (plugin_id, old_state, new_state)
            plugin_id: Optional plugin ID to filter on (None = all plugins)
        
        Returns:
            Subscription ID
        """
        import uuid
        subscription_id = str(uuid.uuid4())
        
        with self._lock:
            key = plugin_id or '*'
            if key not in self._callbacks:
                self._callbacks[key] = []
            self._callbacks[key].append(callback)
        
        return subscription_id
    
    def _notify_callbacks(
        self,
        plugin_id: str,
        old_state: PluginState,
        new_state: Optional[PluginState]
    ) -> None:
        """Notify all relevant callbacks of state change."""
        # Get callbacks for this plugin and all plugins
        callbacks_to_notify = []
        
        if plugin_id in self._callbacks:
            callbacks_to_notify.extend(self._callbacks[plugin_id])
        
        if '*' in self._callbacks:
            callbacks_to_notify.extend(self._callbacks['*'])
        
        # Call each callback
        for callback in callbacks_to_notify:
            try:
                callback(plugin_id, old_state, new_state)
            except Exception as e:
                self.logger.error(
                    f"Error in state change callback: {e}",
                    exc_info=True
                )
    
    def _save_state(self) -> None:
        """Save state to file."""
        if not self.state_file:
            return
        
        try:
            with self._lock:
                # Convert states to dicts
                states_data = {
                    plugin_id: state.to_dict()
                    for plugin_id, state in self._states.items()
                }
                
                state_data = {
                    'version': self._state_version,
                    'states': states_data,
                    'last_updated': datetime.now().isoformat()
                }
            
            # Ensure directory exists with proper permissions
            from src.common.permission_utils import (
                ensure_directory_permissions,
                get_config_dir_mode
            )
            ensure_directory_permissions(self.state_file.parent, get_config_dir_mode())
            
            # Write to file
            with open(self.state_file, 'w') as f:
                json.dump(state_data, f, indent=2)
            
        except Exception as e:
            self.logger.error(f"Error saving plugin state: {e}", exc_info=True)
    
    def _load_state(self) -> None:
        """Load state from file."""
        if not self.state_file or not self.state_file.exists():
            return
        
        try:
            with open(self.state_file, 'r') as f:
                state_data = json.load(f)
            
            with self._lock:
                # Load state version
                self._state_version = state_data.get('version', 1)
                
                # Load states
                states_data = state_data.get('states', {})
                for plugin_id, state_dict in states_data.items():
                    try:
                        self._states[plugin_id] = PluginState.from_dict(state_dict)
                    except Exception as e:
                        self.logger.warning(
                            f"Error loading state for plugin {plugin_id}: {e}"
                        )
            
            self.logger.info(f"Loaded {len(self._states)} plugin states from file")
            
        except Exception as e:
            self.logger.error(f"Error loading plugin state: {e}", exc_info=True)
    
    def get_state_version(self) -> int:
        """Get current state version (for detecting corruption)."""
        return self._state_version

