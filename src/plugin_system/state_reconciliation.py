"""
State reconciliation system.

Compares what the user wants with what is there and what runs:

- desired: config.json (which plugins are configured, and enabled) plus the
  plugins directory on disk (which are installed, at which version);
- observed: the runtime snapshot the display publishes
  (src/plugin_system/plugin_runtime.py) -- which plugins it has loaded, at
  which version, and why one failed. Only a live snapshot is compared; a
  stale, stopped or missing one is unknown and yields no findings.

Desired-state gaps (on disk but not in config, in config but not on disk)
are fixed here. Observed-state gaps (enabled but not loaded, loaded at an
older version) are reported, never "fixed": the display reconciles its own
loaded set against config, and a version gap needs a display restart.

There is no third, persisted record any more. ``data/plugin_state.json``
held a copy of config's enabled flags and the disk's versions, and this
module mostly synced it back to config; it is no longer read or written.
"""

import json
from typing import Any, Callable, Dict, List, Optional, Set
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from src.core_config_keys import CORE_CONFIG_KEYS, CORE_SECRETS_KEYS
from src.plugin_system.plugin_dirs import PluginDirectoryIndex
from src.plugin_system.plugin_runtime import PluginRuntimeView, UNKNOWN
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


def ignored_config_keys(config_manager, installed_ids=frozenset()) -> Set[str]:
    """Config keys that are not plugin ids: core keys plus secrets keys.

    A secrets key that is also an installed plugin's id is not ignored: plugin
    secrets are namespaced by plugin id, so that key is the plugin's own entry.
    Ignoring it made every installed plugin with secrets read as "installed but
    missing from config" on each run.
    """
    secrets_only = secrets_top_level_keys(config_manager) - set(installed_ids)
    return set(StateReconciliation._SYSTEM_CONFIG_KEYS) | secrets_only


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

    A directory counts only when it is not a standalone backup (or hidden)
    and its manifest.json parses. A corrupt manifest must not read as
    installed, or a live "in config but not on disk" finding gets cleared on
    the strength of an unreadable file.

    The id is the manifest's ``id`` -- what discovery registers and what the
    config is keyed by -- and the directory name only when the manifest has
    none. Directory names alone made a plugin living in ``ledmatrix-stocks/``
    with id ``stocks`` read as both "stocks in config but not on disk" and
    "ledmatrix-stocks on disk but not in config".
    """
    try:
        return _disk_index(plugins_dir).installed_ids(require_parseable_manifest=True)
    except OSError:
        return set()


def _disk_index(plugins_dir) -> PluginDirectoryIndex:
    return PluginDirectoryIndex.scan(Path(plugins_dir))


def still_unresolved(entries: List[Dict[str, Any]],
                     config_keys: Set[str],
                     installed_ids: Set[str]) -> List[Dict[str, Any]]:
    """Drop stored reconciliation findings that are no longer true.

    The verdict is written once to a status file and served to the web UI from
    there, and a run that fails to apply a fix also declares it "will not retry
    automatically". Together those froze a single moment forever: a device whose
    plugins were all present in config kept being told, for hours, that four of
    them were missing and should be removed from config.json.

    An "in config but not on disk" finding is stale once the plugin is
    installed, and equally once the id is no longer a plugin entry in config --
    whether the user removed it or it was never a plugin (a core key such as
    ``auto_update`` that an older build misread). Without the second check a
    verdict written by such a build kept telling users to delete a real core
    setting until the next full reconciliation run.

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
            if plugin_id in config_keys and plugin_id not in installed_ids:
                live.append(entry)
        else:
            live.append(entry)
    return live


RuntimeSource = Callable[[], PluginRuntimeView]


class StateReconciliation:
    """
    State reconciliation system.

    Compares desired state (config + disk) with observed state (the
    display's runtime snapshot) and fixes what can safely be fixed.
    """

    def __init__(
        self,
        *,
        config_manager,
        plugins_dir: Path,
        store_manager=None,
        runtime_source: Optional[RuntimeSource] = None,
    ):
        """
        Initialize reconciliation system.

        Args:
            config_manager: ConfigManager instance
            plugins_dir: Path to plugins directory
            store_manager: Optional PluginStoreManager for auto-repair
            runtime_source: Returns the display's runtime snapshot as a
                PluginRuntimeView (plugin_runtime.read_plugin_runtime bound to
                a cache manager). None: observed state is unknown.
        """
        self.config_manager = config_manager
        self.plugins_dir = Path(plugins_dir)
        self.store_manager = store_manager
        self.runtime_source = runtime_source
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
            # Desired: config + disk. Observed: the display's snapshot.
            config_state = self._get_config_state()
            disk_state = self._get_disk_state()
            observed = self._get_observed_state()

            # Plugins the display reports but neither config nor disk knows
            # (removed while it still runs them) are not a finding of their
            # own: the display unloads them when their section goes.
            all_plugin_ids: Set[str] = set()
            all_plugin_ids.update(config_state.keys())
            all_plugin_ids.update(disk_state.keys())

            # Check each plugin for inconsistencies
            for plugin_id in all_plugin_ids:
                plugin_inconsistencies = self._check_plugin_consistency(
                    plugin_id,
                    config_state,
                    disk_state,
                    observed,
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
    
    # Top-level config keys that are NOT plugins. The core keys come from the
    # shared list in src/core_config_keys.py -- a private copy here missed
    # #581's 'auto_update' and reported it as a plugin missing from disk.
    # CORE_SECRETS_KEYS are the core's own secrets-file keys. The secrets file
    # itself is read at run time too (ignored_config_keys): load_config() merges
    # it in, and naming its keys one by one let a 'data' key become a phantom
    # plugin permanently reported as "in config but not on disk".
    _SYSTEM_CONFIG_KEYS = CORE_CONFIG_KEYS | CORE_SECRETS_KEYS

    def _get_config_state(self) -> Dict[str, Dict[str, Any]]:
        """Get plugin state from config file."""
        state = {}
        try:
            config = self.config_manager.load_config()
            ignored = ignored_config_keys(self.config_manager,
                                          disk_plugin_ids(self.plugins_dir))
            for plugin_id in config_plugin_ids(config, ignored):
                plugin_config = config[plugin_id]
                state[plugin_id] = {
                    # The display's rule: it runs a plugin only when its
                    # section says "enabled": true.
                    'enabled': bool(plugin_config.get('enabled', False)),
                    'exists_in_config': True
                }
        except Exception as e:
            self.logger.warning(f"Error reading config state: {e}")
        return state
    
    def _get_disk_state(self) -> Dict[str, Dict[str, Any]]:
        """Get plugin state from disk (installed plugins)."""
        state = {}
        try:
            # Membership uses the same index and rule as disk_plugin_ids, so
            # the web interface re-checks stored findings against this same
            # definition; each manifest is read once, by the scan.
            index = _disk_index(self.plugins_dir)
            for plugin_id in index.installed_ids(require_parseable_manifest=True):
                entry = index.entry_for_installed_id(plugin_id)
                manifest = entry.manifest if entry is not None else None
                if not isinstance(manifest, dict):
                    manifest = {}
                state[plugin_id] = {
                    'exists_on_disk': True,
                    'version': manifest.get('version'),
                    'name': manifest.get('name')
                }
        except Exception as e:
            self.logger.warning(f"Error reading disk state: {e}")
        return state
    
    def _get_observed_state(self) -> PluginRuntimeView:
        """The display's runtime snapshot; unknown when there is no source or
        it cannot be read. Only a live view is compared."""
        if self.runtime_source is None:
            return PluginRuntimeView(status=UNKNOWN)
        try:
            return self.runtime_source()
        except Exception as e:
            self.logger.warning(f"Error reading the display's runtime state: {e}")
            return PluginRuntimeView(status=UNKNOWN)

    def plugin_states(self) -> Dict[str, Dict[str, Any]]:
        """Desired and observed state for every plugin config or disk knows.

        Per plugin: ``installed`` and ``version`` (disk), ``in_config`` and
        ``enabled`` (config, by the display's rule), and the display's
        ``loaded`` / ``state`` / ``error_info`` / ``loaded_version`` /
        ``loaded_at`` (None unless its snapshot is live). What
        /api/v3/plugins/state serves, in place of plugin_state.json.
        """
        config_state = self._get_config_state()
        disk_state = self._get_disk_state()
        observed = self._get_observed_state()
        states: Dict[str, Dict[str, Any]] = {}
        for plugin_id in sorted(set(config_state) | set(disk_state)):
            if plugin_id in CORE_CONFIG_KEYS:
                continue
            config = config_state.get(plugin_id, {})
            disk = disk_state.get(plugin_id, {})
            states[plugin_id] = {
                'plugin_id': plugin_id,
                'installed': bool(disk.get('exists_on_disk')),
                'version': disk.get('version'),
                'in_config': bool(config.get('exists_in_config')),
                'enabled': bool(config.get('enabled', False)),
                **observed.plugin(plugin_id),
            }
        return states

    def _check_plugin_consistency(
        self,
        plugin_id: str,
        config_state: Dict[str, Dict[str, Any]],
        disk_state: Dict[str, Dict[str, Any]],
        observed: PluginRuntimeView,
    ) -> List[Inconsistency]:
        """Check consistency for a single plugin."""
        inconsistencies: List[Inconsistency] = []

        if plugin_id in CORE_CONFIG_KEYS:
            # A plugin whose id is a core setting's key ('display', 'sync',
            # ...) can never have a config section of its own: that section
            # is the core setting. Every check below would misfire -- "not in
            # config" forever, and an auto-fix that writes {'enabled': False}
            # where a core setting belongs -- so report it in the log instead.
            if disk_state.get(plugin_id, {}).get('exists_on_disk'):
                self.logger.warning(
                    "Plugin id %r is reserved for a core config setting; "
                    "the plugin cannot be configured and is skipped by "
                    "reconciliation. Rename the plugin.", plugin_id)
            return inconsistencies

        config = config_state.get(plugin_id, {})
        disk = disk_state.get(plugin_id, {})
        
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
            # Also refuse to resurrect a plugin the user has persistently
            # uninstalled. The record survives restarts, so the user's
            # removal sticks across updates.
            persistently_uninstalled = (
                self.store_manager is not None
                and hasattr(self.store_manager, 'is_plugin_uninstalled')
                and self.store_manager.is_plugin_uninstalled(plugin_id)
            )
            can_repair = (
                self.store_manager is not None
                and not previously_unrecoverable
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
        
        # Observed checks: only against a live snapshot, and only for a plugin
        # that is both configured and installed (the checks above cover the
        # rest). Reported, never fixed here: the display loads and unloads by
        # config on its own, so a gap is either transient (it is catching up)
        # or something only the user can act on (a failed load, a restart).
        if (observed.live and config.get('exists_in_config')
                and disk.get('exists_on_disk')):
            runtime = observed.plugin(plugin_id)
            config_enabled = bool(config.get('enabled', False))
            loaded = bool(runtime.get('loaded'))
            if config_enabled != loaded:
                error = runtime.get('error_info') or {}
                why = f" ({error.get('message')})" if error.get('message') else ""
                inconsistencies.append(Inconsistency(
                    plugin_id=plugin_id,
                    inconsistency_type=InconsistencyType.PLUGIN_ENABLED_MISMATCH,
                    description=(
                        f"Plugin {plugin_id} is {'enabled' if config_enabled else 'disabled'} "
                        f"in config but the display has it "
                        f"{'loaded' if loaded else 'not loaded'} "
                        f"(state {runtime.get('state')}){why}"),
                    fix_action=FixAction.NO_ACTION,
                    current_state={'loaded': loaded, 'state': runtime.get('state')},
                    expected_state={'loaded': config_enabled},
                    can_auto_fix=False
                ))
            loaded_version = runtime.get('loaded_version')
            disk_version = disk.get('version')
            if loaded and loaded_version and disk_version and loaded_version != disk_version:
                inconsistencies.append(Inconsistency(
                    plugin_id=plugin_id,
                    inconsistency_type=InconsistencyType.PLUGIN_VERSION_MISMATCH,
                    description=(
                        f"Plugin {plugin_id} {disk_version} is installed but the display "
                        f"is running {loaded_version}; restart the display to run it"),
                    fix_action=FixAction.NO_ACTION,
                    current_state={'version': loaded_version},
                    expected_state={'version': disk_version},
                    can_auto_fix=False
                ))

        return inconsistencies
    
    def _fix_inconsistency(self, inconsistency: Inconsistency) -> bool:
        """Attempt to fix an inconsistency."""
        try:
            if inconsistency.inconsistency_type == InconsistencyType.PLUGIN_MISSING_IN_CONFIG:
                if inconsistency.plugin_id in CORE_CONFIG_KEYS:
                    # Never create or replace a core setting with a plugin stub.
                    self.logger.warning(
                        "Refusing to add plugin entry %r: that key is a core "
                        "config setting", inconsistency.plugin_id)
                    return False
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

