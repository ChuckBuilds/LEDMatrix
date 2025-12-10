"""
State reconciliation system.

Detects and fixes inconsistencies between:
- Config file state
- Plugin manager state
- Disk state (installed plugins)
- State manager state
"""

from typing import Dict, Any, List, Optional
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import time
import os

from src.plugin_system.state_manager import PluginStateManager, PluginState, PluginStateStatus
from src.logging_config import get_logger

def _get_debug_log_path():
    """Get path to debug log file, creating directory if needed."""
    try:
        # Try to find project root
        project_root = os.environ.get('LEDMATRIX_ROOT')
        if not project_root:
            # Try to find project root by looking for config directory
            current = Path(__file__).resolve().parent.parent.parent
            if (current / 'config').exists():
                project_root = str(current)
            else:
                # Fallback to current working directory
                project_root = os.getcwd()
        
        log_dir = Path(project_root) / '.cursor'
        log_dir.mkdir(exist_ok=True, mode=0o755)
        return log_dir / 'debug.log'
    except Exception:
        # Ultimate fallback
        return Path('/tmp/ledmatrix_debug.log')


class InconsistencyType(Enum):
    """Types of state inconsistencies."""
    PLUGIN_MISSING_IN_CONFIG = "plugin_missing_in_config"
    PLUGIN_MISSING_ON_DISK = "plugin_missing_on_disk"
    PLUGIN_ENABLED_MISMATCH = "plugin_enabled_mismatch"
    PLUGIN_VERSION_MISMATCH = "plugin_version_mismatch"
    PLUGIN_STATE_CORRUPTED = "plugin_state_corrupted"


class FixAction(Enum):
    """Actions that can be taken to fix inconsistencies."""
    AUTO_FIX = "auto_fix"
    MANUAL_FIX_REQUIRED = "manual_fix_required"
    NO_ACTION = "no_action"


@dataclass
class Inconsistency:
    """Represents a state inconsistency."""
    plugin_id: str
    inconsistency_type: InconsistencyType
    description: str
    fix_action: FixAction
    current_state: Dict[str, Any]
    expected_state: Dict[str, Any]
    can_auto_fix: bool = False


@dataclass
class ReconciliationResult:
    """Result of state reconciliation."""
    inconsistencies_found: List[Inconsistency]
    inconsistencies_fixed: List[Inconsistency]
    inconsistencies_manual: List[Inconsistency]
    reconciliation_successful: bool
    message: str


class StateReconciliation:
    """
    State reconciliation system.
    
    Compares state from multiple sources and detects/fixes inconsistencies.
    """
    
    def __init__(
        self,
        state_manager: PluginStateManager,
        config_manager,
        plugin_manager,
        plugins_dir: Path
    ):
        """
        Initialize reconciliation system.
        
        Args:
            state_manager: PluginStateManager instance
            config_manager: ConfigManager instance
            plugin_manager: PluginManager instance
            plugins_dir: Path to plugins directory
        """
        self.state_manager = state_manager
        self.config_manager = config_manager
        self.plugin_manager = plugin_manager
        self.plugins_dir = Path(plugins_dir)
        self.logger = get_logger(__name__)
    
    def reconcile_state(self) -> ReconciliationResult:
        """
        Perform state reconciliation.
        
        Compares state from all sources and fixes safe inconsistencies.
        
        Returns:
            ReconciliationResult with findings and fixes
        """
        self.logger.info("Starting state reconciliation")
        
        inconsistencies = []
        fixed = []
        manual_fix_required = []
        
        try:
            # Get state from all sources
            config_state = self._get_config_state()
            disk_state = self._get_disk_state()
            manager_state = self._get_manager_state()
            state_manager_state = self._get_state_manager_state()
            
            # Find all unique plugin IDs
            all_plugin_ids = set()
            all_plugin_ids.update(config_state.keys())
            all_plugin_ids.update(disk_state.keys())
            all_plugin_ids.update(manager_state.keys())
            all_plugin_ids.update(state_manager_state.keys())
            
            # Check each plugin for inconsistencies
            for plugin_id in all_plugin_ids:
                plugin_inconsistencies = self._check_plugin_consistency(
                    plugin_id,
                    config_state,
                    disk_state,
                    manager_state,
                    state_manager_state
                )
                inconsistencies.extend(plugin_inconsistencies)
            
            # Attempt to fix auto-fixable inconsistencies
            for inconsistency in inconsistencies:
                if inconsistency.can_auto_fix and inconsistency.fix_action == FixAction.AUTO_FIX:
                    if self._fix_inconsistency(inconsistency):
                        fixed.append(inconsistency)
                    else:
                        manual_fix_required.append(inconsistency)
                elif inconsistency.fix_action == FixAction.MANUAL_FIX_REQUIRED:
                    manual_fix_required.append(inconsistency)
            
            # Build result
            success = len(manual_fix_required) == 0
            
            message = (
                f"Reconciliation complete: {len(inconsistencies)} inconsistencies found, "
                f"{len(fixed)} fixed automatically, {len(manual_fix_required)} require manual attention"
            )
            
            return ReconciliationResult(
                inconsistencies_found=inconsistencies,
                inconsistencies_fixed=fixed,
                inconsistencies_manual=manual_fix_required,
                reconciliation_successful=success,
                message=message
            )
            
        except Exception as e:
            self.logger.error(f"Error during state reconciliation: {e}", exc_info=True)
            return ReconciliationResult(
                inconsistencies_found=inconsistencies,
                inconsistencies_fixed=fixed,
                inconsistencies_manual=manual_fix_required,
                reconciliation_successful=False,
                message=f"Reconciliation failed: {str(e)}"
            )
    
    def _get_config_state(self) -> Dict[str, Dict[str, Any]]:
        """Get plugin state from config file."""
        state = {}
        try:
            config = self.config_manager.load_config()
            for plugin_id, plugin_config in config.items():
                if isinstance(plugin_config, dict):
                    state[plugin_id] = {
                        'enabled': plugin_config.get('enabled', False),
                        'version': plugin_config.get('version'),
                        'exists_in_config': True
                    }
        except Exception as e:
            self.logger.warning(f"Error reading config state: {e}")
        return state
    
    def _get_disk_state(self) -> Dict[str, Dict[str, Any]]:
        """Get plugin state from disk (installed plugins)."""
        state = {}
        try:
            if self.plugins_dir.exists():
                for plugin_dir in self.plugins_dir.iterdir():
                    if plugin_dir.is_dir():
                        plugin_id = plugin_dir.name
                        manifest_path = plugin_dir / "manifest.json"
                        if manifest_path.exists():
                            import json
                            try:
                                with open(manifest_path, 'r') as f:
                                    manifest = json.load(f)
                                state[plugin_id] = {
                                    'exists_on_disk': True,
                                    'version': manifest.get('version'),
                                    'name': manifest.get('name')
                                }
                            except Exception:
                                pass
        except Exception as e:
            self.logger.warning(f"Error reading disk state: {e}")
        return state
    
    def _get_manager_state(self) -> Dict[str, Dict[str, Any]]:
        """Get plugin state from plugin manager."""
        state = {}
        try:
            if self.plugin_manager:
                # Get discovered plugins
                if hasattr(self.plugin_manager, 'plugin_manifests'):
                    for plugin_id in self.plugin_manager.plugin_manifests.keys():
                        state[plugin_id] = {
                            'exists_in_manager': True,
                            'loaded': plugin_id in getattr(self.plugin_manager, 'plugins', {})
                        }
        except Exception as e:
            self.logger.warning(f"Error reading manager state: {e}")
        return state
    
    def _get_state_manager_state(self) -> Dict[str, Dict[str, Any]]:
        """Get plugin state from state manager."""
        state = {}
        try:
            all_states = self.state_manager.get_all_states()
            for plugin_id, plugin_state in all_states.items():
                state[plugin_id] = {
                    'enabled': plugin_state.enabled,
                    'status': plugin_state.status.value,
                    'version': plugin_state.version,
                    'exists_in_state_manager': True
                }
        except Exception as e:
            self.logger.warning(f"Error reading state manager state: {e}")
        return state
    
    def _check_plugin_consistency(
        self,
        plugin_id: str,
        config_state: Dict[str, Dict[str, Any]],
        disk_state: Dict[str, Dict[str, Any]],
        manager_state: Dict[str, Dict[str, Any]],
        state_manager_state: Dict[str, Dict[str, Any]]
    ) -> List[Inconsistency]:
        """Check consistency for a single plugin."""
        inconsistencies = []
        
        config = config_state.get(plugin_id, {})
        disk = disk_state.get(plugin_id, {})
        manager = manager_state.get(plugin_id, {})
        state_mgr = state_manager_state.get(plugin_id, {})
        
        # Check: Plugin exists on disk but not in config
        if disk.get('exists_on_disk') and not config.get('exists_in_config'):
            inconsistencies.append(Inconsistency(
                plugin_id=plugin_id,
                inconsistency_type=InconsistencyType.PLUGIN_MISSING_IN_CONFIG,
                description=f"Plugin {plugin_id} exists on disk but not in config",
                fix_action=FixAction.AUTO_FIX,
                current_state={'exists_in_config': False},
                expected_state={'exists_in_config': True, 'enabled': False},
                can_auto_fix=True
            ))
        
        # Check: Plugin in config but not on disk
        if config.get('exists_in_config') and not disk.get('exists_on_disk'):
            inconsistencies.append(Inconsistency(
                plugin_id=plugin_id,
                inconsistency_type=InconsistencyType.PLUGIN_MISSING_ON_DISK,
                description=f"Plugin {plugin_id} in config but not on disk",
                fix_action=FixAction.MANUAL_FIX_REQUIRED,
                current_state={'exists_on_disk': False},
                expected_state={'exists_on_disk': True},
                can_auto_fix=False
            ))
        
        # Check: Enabled state mismatch
        config_enabled = config.get('enabled', False)
        state_mgr_enabled = state_mgr.get('enabled')
        
        if state_mgr_enabled is not None and config_enabled != state_mgr_enabled:
            inconsistencies.append(Inconsistency(
                plugin_id=plugin_id,
                inconsistency_type=InconsistencyType.PLUGIN_ENABLED_MISMATCH,
                description=f"Plugin {plugin_id} enabled state mismatch: config={config_enabled}, state_manager={state_mgr_enabled}",
                fix_action=FixAction.AUTO_FIX,
                current_state={'enabled': config_enabled},
                expected_state={'enabled': state_mgr_enabled},
                can_auto_fix=True
            ))
        
        return inconsistencies
    
    def _fix_inconsistency(self, inconsistency: Inconsistency) -> bool:
        """Attempt to fix an inconsistency."""
        try:
            if inconsistency.inconsistency_type == InconsistencyType.PLUGIN_MISSING_IN_CONFIG:
                # Add plugin to config with default disabled state
                config = self.config_manager.load_config()
                config[inconsistency.plugin_id] = {
                    'enabled': False
                }
                self.config_manager.save_config(config)
                self.logger.info(f"Fixed: Added {inconsistency.plugin_id} to config")
                return True
            
            elif inconsistency.inconsistency_type == InconsistencyType.PLUGIN_ENABLED_MISMATCH:
                # Sync enabled state from state manager to config
                expected_enabled = inconsistency.expected_state.get('enabled')
                # #region agent log
                import json
                try:
                    log_path = _get_debug_log_path()
                    with open(log_path, 'a') as f:
                        f.write(json.dumps({
                            'sessionId': 'debug-session',
                            'runId': 'run1',
                            'hypothesisId': 'B',
                            'location': 'state_reconciliation.py:306',
                            'message': 'reconcile: fixing enabled mismatch',
                            'data': {'plugin_id': inconsistency.plugin_id, 'current_enabled': inconsistency.current_state.get('enabled'), 'expected_enabled': expected_enabled},
                            'timestamp': int(time.time() * 1000)
                        }) + '\n')
                except Exception as e:
                    self.logger.debug(f"Debug log write failed: {e}")
                # #endregion
                config = self.config_manager.load_config()
                if inconsistency.plugin_id not in config:
                    config[inconsistency.plugin_id] = {}
                config[inconsistency.plugin_id]['enabled'] = expected_enabled
                self.config_manager.save_config(config)
                self.logger.info(f"Fixed: Synced enabled state for {inconsistency.plugin_id}")
                return True
            
        except Exception as e:
            self.logger.error(f"Error fixing inconsistency: {e}", exc_info=True)
            return False
        
        return False

