"""
State reconciliation system.

Detects and fixes inconsistencies between:
- Config file state
- Plugin manager state
- Disk state (installed plugins)
- State manager state
"""

import json
from typing import Dict, Any, List, Set
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from src.plugin_system.state_manager import PluginStateManager
from src.logging_config import get_logger


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


def secrets_top_level_keys(config_manager) -> Set[str]:
    """Top-level keys load_config() merges in from the secrets file.

    Deliberately fail-safe: an unreadable, absent, malformed or non-path
    secrets location narrows this set rather than raising, because a failure
    here must never break reconciliation.
    """
    try:
        path = config_manager.get_secrets_path()
        with open(path, 'r') as f:
            secrets = json.load(f)
    except (AttributeError, OSError, TypeError, ValueError):
        return set()
    return set(secrets) if isinstance(secrets, dict) else set()


def ignored_config_keys(config_manager) -> Set[str]:
    """Config keys that are not plugin ids: system keys plus secrets keys."""
    return set(StateReconciliation._SYSTEM_CONFIG_KEYS) | secrets_top_level_keys(config_manager)


def config_plugin_ids(config: Dict[str, Any], ignored_keys: Set[str]) -> Set[str]:
    """Plugin ids a loaded config declares.

    Shared with the web interface so a stored verdict is re-checked against the
    same definition that produced it. ``set(config)`` is NOT equivalent: it also
    contains system keys, the secrets-file keys load_config() merges in, and
    non-dict values. Counting any of those as a plugin is exactly what turned a
    'data' key in the secrets file into a phantom plugin, and using the loose
    set to re-check findings would clear ones that are still true.
    """
    return {k for k, v in (config or {}).items()
            if isinstance(v, dict) and k not in ignored_keys}


def disk_plugin_ids(plugins_dir) -> Set[str]:
    """Plugin ids actually installed on disk.

    A directory counts only when it is not a standalone backup and its
    manifest.json parses. A corrupt manifest must not read as installed, or a
    live "in config but not on disk" finding gets cleared on the strength of an
    unreadable file.
    """
    ids: Set[str] = set()
    root = Path(plugins_dir)
    try:
        if not root.exists():
            return ids
        for entry in root.iterdir():
            if not entry.is_dir() or '.standalone-backup-' in entry.name:
                continue
            manifest = entry / "manifest.json"
            if not manifest.exists():
                continue
            try:
                with open(manifest, 'r') as f:
                    json.load(f)
            except (OSError, ValueError):
                continue
            ids.add(entry.name)
    except OSError:
        return ids
    return ids


def still_unresolved(entries: List[Dict[str, Any]],
                     config_keys: Set[str],
                     installed_ids: Set[str]) -> List[Dict[str, Any]]:
    """Drop stored reconciliation findings that are no longer true.

    The verdict is written once to a status file and served to the web UI from
    there, and a run that fails to apply a fix also declares it "will not retry
    automatically". Together those froze a single moment forever: a device whose
    plugins were all present in config kept being told, for hours, that four of
    them were missing and should be removed from config.json.

    Entry kinds this cannot re-check are kept, so filtering only ever removes
    findings that are provably stale.
    """
    live: List[Dict[str, Any]] = []
    for entry in entries:
        kind = entry.get('type')
        plugin_id = entry.get('plugin_id')
        if kind == InconsistencyType.PLUGIN_MISSING_IN_CONFIG.value:
            if plugin_id not in config_keys:
                live.append(entry)
        elif kind == InconsistencyType.PLUGIN_MISSING_ON_DISK.value:
            if plugin_id not in installed_ids:
                live.append(entry)
        else:
            live.append(entry)
    return live


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
        plugins_dir: Path,
        store_manager=None
    ):
        """
        Initialize reconciliation system.

        Args:
            state_manager: PluginStateManager instance
            config_manager: ConfigManager instance
            plugin_manager: PluginManager instance
            plugins_dir: Path to plugins directory
            store_manager: Optional PluginStoreManager for auto-repair
        """
        self.state_manager = state_manager
        self.config_manager = config_manager
        self.plugin_manager = plugin_manager
        self.plugins_dir = Path(plugins_dir)
        self.store_manager = store_manager
        self.logger = get_logger(__name__)

        # Plugin IDs that failed auto-repair and should NOT be retried this
        # process lifetime. Prevents the infinite "attempt to reinstall missing
        # plugin" loop when a config entry references a plugin that isn't in
        # the registry (e.g. legacy 'github', 'youtube' entries). A process
        # restart — or an explicit user-initiated reconcile with force=True —
        # clears this so recovery is possible after the underlying issue is
        # fixed.
        self._unrecoverable_missing_on_disk: Set[str] = set()
    
    def reconcile_state(self, force: bool = False) -> ReconciliationResult:
        """
        Perform state reconciliation.

        Compares state from all sources and fixes safe inconsistencies.

        Args:
            force: If True, clear the unrecoverable-plugin cache before
                reconciling so previously-failed auto-repairs are retried.
                Intended for user-initiated reconcile requests after the
                underlying issue (e.g. registry update) has been fixed.

        Returns:
            ReconciliationResult with findings and fixes
        """
        if force and self._unrecoverable_missing_on_disk:
            self.logger.info(
                "Force reconcile requested; clearing %d cached unrecoverable plugin(s)",
                len(self._unrecoverable_missing_on_disk),
            )
            self._unrecoverable_missing_on_disk.clear()

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
    
    # Top-level config keys that are NOT plugins.
    # Includes both config.json structural keys and config_secrets.json top-level
    # keys (load_config() deep-merges secrets in, so secrets keys appear here too).
    _SYSTEM_CONFIG_KEYS = frozenset({
        'web_display_autostart', 'timezone', 'location', 'display',
        'plugin_system', 'vegas_scroll_speed', 'vegas_separator_width',
        'vegas_target_fps', 'vegas_buffer_ahead', 'vegas_plugin_order',
        'vegas_excluded_plugins', 'vegas_scroll_enabled', 'logging',
        'dim_schedule', 'network', 'system', 'schedule',
        # Multi-display sync config (config.json structural key)
        'sync',
        # Secrets file top-level keys (merged in by load_config)
        'github', 'youtube',
    })

    def _secrets_top_level_keys(self) -> Set[str]:
        """Top-level keys that load_config() merges in from the secrets file.

        load_config() merges config_secrets.json into the config it returns, so
        those keys sit alongside plugin ids. _SYSTEM_CONFIG_KEYS named them
        individually ('github', 'youtube'), which broke the moment anything else
        was written there: a 'data' key became a phantom plugin, permanently
        reported as "in config but not on disk". Reading the file keeps this
        correct no matter what it holds.
        """
        return secrets_top_level_keys(self.config_manager)

    def _get_config_state(self) -> Dict[str, Dict[str, Any]]:
        """Get plugin state from config file."""
        state = {}
        try:
            config = self.config_manager.load_config()
            ignored = self._SYSTEM_CONFIG_KEYS | self._secrets_top_level_keys()
            for plugin_id in config_plugin_ids(config, ignored):
                plugin_config = config[plugin_id]
                state[plugin_id] = {
                    'enabled': plugin_config.get('enabled', True),
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
            # Membership comes from the shared extractor so the web interface
            # re-checks stored findings against this same definition; the
            # manifest is then re-read here only for version/name.
            for plugin_id in disk_plugin_ids(self.plugins_dir):
                manifest_path = self.plugins_dir / plugin_id / "manifest.json"
                try:
                    with open(manifest_path, 'r') as f:
                        manifest = json.load(f)
                except (OSError, ValueError):  # nosec B112 - raced or corrupt; skip
                    continue
                state[plugin_id] = {
                    'exists_on_disk': True,
                    'version': manifest.get('version'),
                    'name': manifest.get('name')
                }
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
            # Skip plugins that previously failed auto-repair in this process.
            # Re-attempting wastes CPU (network + git clone each request) and
            # spams the logs with the same "Plugin not found in registry"
            # error. The entry is still surfaced as MANUAL_FIX_REQUIRED so the
            # UI can show it, but no auto-repair will run.
            previously_unrecoverable = plugin_id in self._unrecoverable_missing_on_disk
            # Also refuse to re-install a plugin that the user just uninstalled
            # through the UI — prevents a race where the reconciler fires
            # between file removal and config cleanup and resurrects the
            # plugin the user just deleted.
            recently_uninstalled = (
                self.store_manager is not None
                and hasattr(self.store_manager, 'was_recently_uninstalled')
                and self.store_manager.was_recently_uninstalled(plugin_id)
            )
            # Also refuse to resurrect a plugin the user has persistently
            # uninstalled. Unlike the in-memory race guard above, this record
            # survives restarts, so the user's removal sticks across updates.
            persistently_uninstalled = (
                self.store_manager is not None
                and hasattr(self.store_manager, 'is_plugin_uninstalled')
                and self.store_manager.is_plugin_uninstalled(plugin_id)
            )
            can_repair = (
                self.store_manager is not None
                and not previously_unrecoverable
                and not recently_uninstalled
                and not persistently_uninstalled
            )
            inconsistencies.append(Inconsistency(
                plugin_id=plugin_id,
                inconsistency_type=InconsistencyType.PLUGIN_MISSING_ON_DISK,
                description=f"Plugin {plugin_id} in config but not on disk",
                fix_action=FixAction.AUTO_FIX if can_repair else FixAction.MANUAL_FIX_REQUIRED,
                current_state={'exists_on_disk': False},
                expected_state={'exists_on_disk': True},
                can_auto_fix=can_repair
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
                current_state={'enabled': state_mgr_enabled},
                expected_state={'enabled': config_enabled},
                can_auto_fix=True
            ))
        
        return inconsistencies
    
    def _fix_inconsistency(self, inconsistency: Inconsistency) -> bool:
        """Attempt to fix an inconsistency."""
        try:
            if inconsistency.inconsistency_type == InconsistencyType.PLUGIN_MISSING_IN_CONFIG:
                config = self.config_manager.load_config()
                if inconsistency.plugin_id in config:
                    # Detection said "not in config" but it is there -- the
                    # config changed under us, or the id came from a key merged
                    # in from elsewhere. Assigning the stub below would replace
                    # the real entry: one reported case would have traded 4.9KB
                    # of league settings for {'enabled': False}. Nothing to fix.
                    self.logger.info(
                        "Skipped: %s is already in config; not overwriting it",
                        inconsistency.plugin_id)
                    return True
                # Add plugin to config with default disabled state
                config[inconsistency.plugin_id] = {
                    'enabled': False
                }
                self.config_manager.save_config(config)
                self.logger.info(f"Fixed: Added {inconsistency.plugin_id} to config")
                return True
            
            elif inconsistency.inconsistency_type == InconsistencyType.PLUGIN_MISSING_ON_DISK:
                return self._auto_repair_missing_plugin(inconsistency.plugin_id)

            elif inconsistency.inconsistency_type == InconsistencyType.PLUGIN_ENABLED_MISMATCH:
                # config.json is the user-editable source of truth for enabled state.
                # Bring the state manager in sync with config rather than the reverse,
                # so that manual config edits (or the state left behind after an
                # uninstall+reinstall cycle) don't silently override the user's intent.
                config_enabled = inconsistency.expected_state.get('enabled')
                success = self.state_manager.set_plugin_enabled(inconsistency.plugin_id, config_enabled)
                if success:
                    self.logger.info(
                        f"Fixed: Synced state manager enabled={config_enabled} for "
                        f"{inconsistency.plugin_id} to match config"
                    )
                else:
                    self.logger.warning(
                        f"Failed to sync state manager enabled={config_enabled} for "
                        f"{inconsistency.plugin_id}"
                    )
                return success
            
        except Exception as e:
            self.logger.error(f"Error fixing inconsistency: {e}", exc_info=True)
            return False

        return False

    def _auto_repair_missing_plugin(self, plugin_id: str) -> bool:
        """Attempt to reinstall a missing plugin from the store.

        On failure, records plugin_id in ``_unrecoverable_missing_on_disk`` so
        subsequent reconciliation passes within this process do not retry and
        spam the log / CPU. A process restart (or an explicit ``force=True``
        reconcile) is required to clear the cache.
        """
        if not self.store_manager:
            return False

        # Try the plugin_id as-is, then without 'ledmatrix-' prefix
        candidates = [plugin_id]
        if plugin_id.startswith('ledmatrix-'):
            candidates.append(plugin_id[len('ledmatrix-'):])

        # Cheap pre-check: is any candidate actually present in the registry
        # at all? If not, we know up-front this is unrecoverable and can skip
        # the expensive install_plugin path (which does a forced GitHub fetch
        # before failing).
        #
        # IMPORTANT: we must pass raise_on_failure=True here. The default
        # fetch_registry() silently falls back to a stale cache or an empty
        # dict on network failure, which would make it impossible to tell
        # "plugin genuinely not in registry" from "I can't reach the
        # registry right now" — in the second case we'd end up poisoning
        # _unrecoverable_missing_on_disk with every config entry on a fresh
        # boot with no cache.
        registry_has_candidate = False
        try:
            registry = self.store_manager.fetch_registry(raise_on_failure=True)
            registry_ids = {
                p.get('id') for p in (registry.get('plugins', []) or []) if p.get('id')
            }
            registry_has_candidate = any(c in registry_ids for c in candidates)
        except Exception as e:
            # If we can't reach the registry, treat this as transient — don't
            # mark unrecoverable, let the next pass try again.
            self.logger.warning(
                "[AutoRepair] Could not read registry to check %s: %s", plugin_id, e
            )
            return False

        if not registry_has_candidate:
            self.logger.warning(
                "[AutoRepair] %s not present in registry; marking unrecoverable "
                "(will not retry this session). Reinstall from the Plugin Store "
                "or remove the stale config entry to clear this warning.",
                plugin_id,
            )
            self._unrecoverable_missing_on_disk.add(plugin_id)
            return False

        for candidate_id in candidates:
            try:
                self.logger.info("[AutoRepair] Attempting to reinstall missing plugin: %s", candidate_id)
                result = self.store_manager.install_plugin(candidate_id)
                if isinstance(result, dict):
                    success = result.get('success', False)
                else:
                    success = bool(result)

                if success:
                    self.logger.info("[AutoRepair] Successfully reinstalled plugin: %s (config key: %s)", candidate_id, plugin_id)
                    return True
            except Exception as e:
                self.logger.error("[AutoRepair] Error reinstalling %s: %s", candidate_id, e, exc_info=True)

        self.logger.warning(
            "[AutoRepair] Could not reinstall %s from store; marking unrecoverable "
            "(will not retry this session).",
            plugin_id,
        )
        self._unrecoverable_missing_on_disk.add(plugin_id)
        return False

