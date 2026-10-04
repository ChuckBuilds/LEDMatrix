"""
Plugin Store Manager for LEDMatrix

Handles plugin discovery, installation, updates, and uninstallation
from both the official registry and custom GitHub repositories.
"""

import os
import re
import json
import stat
import shutil
import threading
import time
from pathlib import Path
from typing import List, Dict, Optional, Any, Tuple, Set

from src.logging_config import get_logger
from src.common.permission_utils import sudo_remove_directory
from src.plugin_system.plugin_dirs import (
    PluginDirectoryIndex, resolve_plugin_dir, store_search_dirs,
)
from src.plugin_system.store_install import _InstallMixin
from src.plugin_system.store_registry import _RegistryMixin, prefix_hint
from src.plugin_system.store_update import _UpdateMixin


class PluginStoreManager(_RegistryMixin, _InstallMixin, _UpdateMixin):
    """
    Manages plugin discovery, installation, and updates from GitHub.

    The methods are split by area across mixins: registry and GitHub
    metadata (store_registry.py), installs (store_install.py) and updates
    (store_update.py). This module holds the shared state, locks, the
    uninstall registry, directory lookup and uninstall.
    
    Supports two installation methods:
    1. From official registry (curated plugins)
    2. From custom GitHub URL (any repo)
    """
    
    REGISTRY_URL = "https://raw.githubusercontent.com/ChuckBuilds/ledmatrix-plugins/main/plugins.json"

    # A valid plugin id is a single path component: starts alphanumeric, then
    # alphanumerics / dot / dash / underscore. Used to keep the uninstall
    # registry from ever turning a corrupt or hand-edited entry (e.g. "",
    # "..", "../x") into a filesystem path that purge_uninstalled_plugins
    # would delete — an empty id resolves to the plugins root itself.
    _PLUGIN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

    @staticmethod
    def is_plugin_entry(entry) -> bool:
        """Whether a registry entry is a plugin core can install.

        A missing ``type`` means plugin. Anything else (registries used to
        carry ``"type": "skin"`` entries, and a custom registry still can) is
        hidden from the store and refused at install, rather than being
        unpacked into the plugins directory as if it were a plugin.
        """
        return isinstance(entry, dict) and (entry.get('type') or 'plugin') == 'plugin'
    
    def __init__(self, plugins_dir: str = "plugins",
                 uninstalled_registry_path: Optional[str] = None):
        """
        Initialize the plugin store manager.

        Args:
            plugins_dir: Directory where plugins are installed
            uninstalled_registry_path: Path to the JSON file recording plugins
                the user has uninstalled. Defaults to
                ``config/uninstalled_plugins.json`` under the project root.
        """
        self.plugins_dir = Path(plugins_dir)
        self.logger = get_logger(__name__)
        self.registry_cache = None
        self.registry_cache_time = None  # Timestamp of when registry was cached
        self.github_cache = {}  # Cache for GitHub API responses
        self.cache_timeout = 3600  # 1 hour cache timeout (repo info: stars, default_branch)
        # 15 minutes for registry cache. Long enough that the plugin list
        # endpoint on a warm cache never hits the network, short enough that
        # new plugins show up within a reasonable window. See also the
        # stale-cache fallback in fetch_registry for transient network
        # failures.
        self.registry_cache_timeout = 900
        self.commit_info_cache = {}  # Cache for latest commit info: {key: (timestamp, data)}
        # 30 minutes for commit/manifest caches. Plugin Store users browse
        # the catalog via /plugins/store/list, which fetches commit info per
        # plugin; with a 5-minute TTL nearly every browse on a Pi4 paid for
        # an HTTP request per plugin again. 30 minutes keeps the cache warm
        # across a realistic session while still picking up upstream updates
        # within a reasonable window.
        self.commit_cache_timeout = 1800
        self.manifest_cache = {}  # Cache for GitHub manifest fetches: {key: (timestamp, data)}
        self.manifest_cache_timeout = 1800
        self.github_token = self._load_github_token()
        self._token_validation_cache = {}  # Cache for token validation results: {token: (is_valid, timestamp, error_message)}
        self._token_validation_cache_timeout = 300  # 5 minutes cache for token validation

        # Persistent record of plugins the user has uninstalled. It survives
        # restarts so that a core ``git pull`` update cannot resurrect a
        # built-in plugin the user removed. Built-in plugins (e.g.
        # ``web-ui-info``, ``starlark-apps``) are committed into the repo under
        # ``plugin-repos/``, so a plain ``git pull`` restores their files even
        # after the user deleted them. ``purge_uninstalled_plugins`` re-removes
        # any such resurrected directory; ``install_plugin`` clears the record
        # when the user deliberately reinstalls. The file is gitignored.
        if uninstalled_registry_path is not None:
            self._uninstalled_registry_path = Path(uninstalled_registry_path)
        else:
            self._uninstalled_registry_path = (
                Path(__file__).parent.parent.parent / "config" / "uninstalled_plugins.json"
            )
        # Serializes read-modify-write of the registry file so concurrent
        # install/uninstall requests can't lose updates.
        self._uninstalled_registry_lock = threading.Lock()

        # Cache for _get_local_git_info: {plugin_path_str: (signature, data)}
        # where ``signature`` is a tuple of (head_mtime, resolved_ref_mtime,
        # head_contents) so a fast-forward update to the current branch
        # (which touches .git/refs/heads/<branch> but NOT .git/HEAD) still
        # invalidates the cache. Without it every /plugins/installed request
        # runs a git subprocess per plugin, which adds up on a Pi4 with a
        # dozen plugins. The cached
        # ``data`` dict is the same shape returned by ``_get_local_git_info``
        # itself (sha / short_sha / branch / optional remote_url, date_iso,
        # date) — all string-keyed strings.
        self._git_info_cache: Dict[str, Tuple[Tuple, Dict[str, str]]] = {}

        # How long to wait before re-attempting a failed GitHub metadata
        # fetch after we've already served a stale cache hit. Without this,
        # a single expired-TTL + network-error would cause every subsequent
        # request to re-hit the network (and fail again) until the network
        # actually came back — amplifying the failure and blocking request
        # handlers. Bumping the cached-entry timestamp on failure serves
        # the stale payload cheaply until the backoff expires.
        self._failure_backoff_seconds = 60
        # Prevents concurrent callers from each firing a network request when
        # the registry cache expires. Only one thread fetches; others wait and
        # then get the result from the warm cache (double-checked locking).
        self._registry_fetch_lock = threading.Lock()

        # Per-plugin locks for _reinstall_with_rollback: the web UI runs
        # Flask with threaded=True, so two overlapping requests for the
        # same plugin_id (double-click, two browser tabs) would otherwise
        # both rename the same directory aside — one succeeds, and the
        # loser can end up renaming the winner's in-progress install aside
        # mid-download, stealing its own rollback safety net. Keyed by
        # plugin_id so unrelated plugins still update concurrently.
        # Reentrant: install_plugin takes this lock, and _reinstall_with_rollback
        # holds it across its call to install_plugin. A plain Lock would
        # self-deadlock on that nesting.
        self._reinstall_locks: Dict[str, "threading.RLock"] = {}
        self._reinstall_locks_guard = threading.Lock()

        # Ensure plugins directory exists
        self.plugins_dir.mkdir(exist_ok=True)

    def _get_reinstall_lock(self, plugin_id: str):
        """Lazily create (or fetch) the per-plugin reinstall lock.

        Reentrant by necessity: `install_plugin` acquires it to protect its
        set-aside/restore, and `_reinstall_with_rollback` holds it across its
        own call to `install_plugin`. With a plain `Lock` that nesting
        deadlocks the request thread.
        """
        with self._reinstall_locks_guard:
            lock = self._reinstall_locks.get(plugin_id)
            if lock is None:
                lock = threading.RLock()
                self._reinstall_locks[plugin_id] = lock
            return lock

    def _record_cache_backoff(self, cache_dict: Dict, cache_key: str,
                              cache_timeout: int, payload: Any) -> None:
        """Bump a cache entry's timestamp so subsequent lookups hit the
        cache rather than re-failing over the network.

        Used by the stale-on-error fallbacks in the GitHub metadata fetch
        paths. Without this, a cache entry whose TTL just expired would
        cause every subsequent request to re-hit the network and fail
        again until the network actually came back. We write a synthetic
        timestamp ``(now + backoff - cache_timeout)`` so the cache-valid
        check ``(now - ts) < cache_timeout`` succeeds for another
        ``backoff`` seconds.
        """
        synthetic_ts = time.time() + self._failure_backoff_seconds - cache_timeout
        cache_dict[cache_key] = (synthetic_ts, payload)

    def _is_valid_plugin_id(self, plugin_id: Any) -> bool:
        """Return True if ``plugin_id`` is a safe single-component plugin id.

        Rejects empty strings, anything with a path separator, and traversal
        sequences like ``..`` so a registry entry can never escape (or target
        the root of) ``self.plugins_dir`` during a purge.
        """
        return isinstance(plugin_id, str) and bool(self._PLUGIN_ID_RE.match(plugin_id))

    def _read_uninstalled_registry(self) -> Set[str]:
        """Read the persistent set of uninstalled plugin IDs.

        Returns an empty set if the file is missing, unreadable, or corrupt —
        a broken registry must never block normal plugin operations. Invalid
        ids are dropped here so callers never turn them into paths.
        """
        try:
            if not self._uninstalled_registry_path.exists():
                return set()
            with open(self._uninstalled_registry_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if not isinstance(data, list):
                self.logger.warning(
                    "Uninstalled-plugin registry at %s is not a list; ignoring it",
                    self._uninstalled_registry_path,
                )
                return set()
            valid: Set[str] = set()
            for pid in data:
                if self._is_valid_plugin_id(pid):
                    valid.add(pid)
                else:
                    self.logger.warning(
                        "Ignoring invalid plugin id in uninstall registry: %r", pid
                    )
            return valid
        except (OSError, ValueError) as e:
            self.logger.warning(
                "Could not read uninstalled-plugin registry at %s: %s",
                self._uninstalled_registry_path, e,
            )
            return set()

    def _write_uninstalled_registry(self, plugin_ids: Set[str]) -> None:
        """Persist the set of uninstalled plugin IDs (sorted, atomically)."""
        path = self._uninstalled_registry_path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = path.with_suffix(path.suffix + ".tmp")
            with open(tmp_path, 'w', encoding='utf-8') as f:
                json.dump(sorted(plugin_ids), f, indent=2)
            os.replace(tmp_path, path)
        except OSError as e:
            self.logger.error(
                "Failed to write uninstalled-plugin registry at %s: %s", path, e
            )

    def record_uninstalled_plugin(self, plugin_id: str) -> None:
        """Persistently record that the user uninstalled ``plugin_id``.

        Survives restarts so a core update cannot resurrect the plugin.
        """
        if not self._is_valid_plugin_id(plugin_id):
            self.logger.error("Refusing to record invalid plugin id: %r", plugin_id)
            return
        with self._uninstalled_registry_lock:
            recorded = self._read_uninstalled_registry()
            if plugin_id not in recorded:
                recorded.add(plugin_id)
                self._write_uninstalled_registry(recorded)
                self.logger.info("Recorded %s as uninstalled (persistent)", plugin_id)

    def forget_uninstalled_plugin(self, *plugin_ids: str) -> None:
        """Drop ``plugin_ids`` from the persistent uninstall registry.

        Called when a plugin is deliberately (re)installed so future updates
        keep it.
        """
        with self._uninstalled_registry_lock:
            recorded = self._read_uninstalled_registry()
            to_remove = {pid for pid in plugin_ids if pid in recorded}
            if to_remove:
                self._write_uninstalled_registry(recorded - to_remove)
                self.logger.info(
                    "Cleared uninstall record for %s", ", ".join(sorted(to_remove))
                )

    def get_uninstalled_plugins(self) -> Set[str]:
        """Return the persistent set of user-uninstalled plugin IDs."""
        return self._read_uninstalled_registry()

    def is_plugin_uninstalled(self, plugin_id: str) -> bool:
        """Return True if ``plugin_id`` is in the persistent uninstall registry."""
        return plugin_id in self._read_uninstalled_registry()

    def purge_uninstalled_plugins(self) -> List[str]:
        """Remove on-disk directories for plugins the user has uninstalled.

        Built-in plugins committed into the repo are restored on disk by a
        core ``git pull``; this re-removes any that the user previously
        uninstalled. The registry entries are kept so the purge is idempotent
        across every future update (until the user reinstalls). Returns the
        list of plugin IDs whose directories were actually removed.
        """
        removed: List[str] = []
        plugins_root = self.plugins_dir.resolve()
        for plugin_id in sorted(self._read_uninstalled_registry()):
            plugin_path = self.plugins_dir / plugin_id
            # Defense in depth: ids are already validated on read, but never
            # remove anything that isn't a direct child of the plugins root.
            resolved = plugin_path.resolve()
            if resolved == plugins_root or resolved.parent != plugins_root:
                self.logger.error(
                    "Refusing to purge unsafe plugin path for id %r", plugin_id
                )
                continue
            if not plugin_path.exists():
                continue
            self.logger.info(
                "Purging resurrected uninstalled plugin: %s", plugin_id
            )
            if self._safe_remove_directory(plugin_path):
                removed.append(plugin_id)
            else:
                self.logger.error(
                    "Failed to purge resurrected plugin directory: %s", plugin_path
                )
        return removed

    
    

    
    
    
    
    
    

    

    

    

    
    

    
    def _safe_remove_directory(self, path: Path) -> bool:
        """
        Safely remove a directory, handling permission errors for root-owned files.

        Attempts removal in three stages:
        1. Normal shutil.rmtree()
        2. Fix permissions via os.chmod() then retry (works for same-owner files)
        3. Use sudo rm -rf as last resort (works for root-owned __pycache__, etc.)

        A symlink -- a dev plugin linked in by scripts/dev/dev_plugin_setup.sh
        -- is removed as a link, before any of that: rmtree refuses one, and
        stage 2 would walk through it and chmod the developer's checkout.

        Args:
            path: Path to directory to remove

        Returns:
            True if directory was removed successfully, False otherwise
        """
        if path.is_symlink():
            # Checked before exists(), which follows the link: a dangling one
            # would read as already removed and be left behind.
            try:
                path.unlink()
                return True
            except OSError as e:
                self.logger.error(f"Could not remove the symlink {path}: {e}")
                return False

        if not path.exists():
            return True  # Already removed

        # Stage 1: Try normal removal
        try:
            shutil.rmtree(path)
            return True
        except OSError:
            self.logger.warning(f"Permission error removing {path}, attempting chmod fix...")

        # Stage 2: Try chmod + retry (works when we own the files)
        try:
            for root, _dirs, files in os.walk(path):
                root_path = Path(root)
                try:
                    os.chmod(root_path, stat.S_IRWXU)
                except (OSError, PermissionError):
                    pass
                for file in files:
                    try:
                        os.chmod(root_path / file, stat.S_IRWXU)
                    except (OSError, PermissionError):
                        pass
            shutil.rmtree(path)
            self.logger.info(f"Removed {path} after fixing permissions")
            return True
        except (PermissionError, OSError):
            self.logger.warning(f"chmod fix failed for {path}, attempting sudo removal...")

        # Stage 3: Use sudo rm -rf (for root-owned __pycache__, data/.cache, etc.)
        if sudo_remove_directory(path):
            return True

        # Final check — maybe partial removal got everything
        if not path.exists():
            return True

        self.logger.error(f"All removal strategies failed for {path}")
        return False
    
    def _find_plugin_path(self, plugin_id: str) -> Optional[Path]:
        """
        Find the plugin path by checking the configured directory and standard plugins directory.

        Searches the configured directory, then a sibling ``plugins/`` (the
        case where plugins sit in plugins/ but config says plugin-repos/) --
        a store-only fallback; discovery scans the configured directory only.
        Each directory is searched completely before the next, by the shared
        rules in ``src/plugin_system/plugin_dirs.py``: a directory whose
        manifest declares the id wins, then a directory named exactly for it.

        The manifest match matters because a directory name can differ from
        the id its manifest declares (a hand-made or legacy layout such as
        `ledmatrix-stocks/` holding id `stocks`); a lookup by directory name
        alone reported such a plugin as not installed, so update_plugin()
        silently did nothing.

        When nothing answers to the id itself, the ids the registry proves
        are the same plugin are tried the same way
        (`_installed_id_candidates`): the entry's own id, its ``aliases`` and
        its ``plugin_path`` name. So the registry id `stocks` finds an
        installed `ledmatrix-stocks/` declaring `ledmatrix-stocks` (the
        monorepo's leaderboard, music, stocks and weather), and uninstalling
        by the registry id no longer reports success while leaving the
        plugin on disk.

        Never ``ledmatrix-<id>`` without that proof -- no registry loaded, or
        an entry that doesn't name it: a store operation may delete or
        replace what this returns, and an unrelated plugin can own that
        folder. Such a folder is only logged, so a person can act on it.
        Still no case folding.

        Args:
            plugin_id: Plugin identifier

        Returns:
            Path to plugin directory if found, None otherwise
        """
        return self._find_with_proof(plugin_id, fetch=False)

    def _find_with_proof(self, plugin_id: str, fetch: bool) -> Optional[Path]:
        """`_find_plugin_path`; with ``fetch``, a ``ledmatrix-<id>`` folder
        found while no registry is loaded makes it fetch the registry and
        look again, since only the registry can prove the folder is this
        plugin. Uninstall passes False (it must work offline); update, which
        needs the network anyway, passes True."""
        search_dirs = self._candidate_plugin_dirs()
        found = self._resolve_installed(plugin_id, search_dirs)
        if found is not None:
            return found
        folder = self._unproven_prefix_folder(plugin_id, search_dirs)
        if folder is not None and fetch and not getattr(self, 'registry_cache', None):
            try:
                self.fetch_registry()
            except Exception as e:  # noqa: BLE001 - fall through to "not found"
                self.logger.debug("Registry fetch while looking for %s failed: %s", plugin_id, e)
            found = self._resolve_installed(plugin_id, search_dirs)
            if found is not None:
                return found
        if folder is not None:
            self.logger.warning(
                "Plugin %s not found. %s may be it, but nothing in the plugin "
                "registry says so (no alias), so the store leaves it alone; "
                "if it is this plugin, manage it as %s.",
                plugin_id, folder, prefix_hint(plugin_id))
        return None

    @staticmethod
    def _unproven_prefix_folder(plugin_id: str, search_dirs: List[Path]) -> Optional[Path]:
        """A ``ledmatrix-<id>`` folder, which the store names but won't touch."""
        hint = prefix_hint(plugin_id)
        if hint is None:
            return None
        return resolve_plugin_dir(hint, search_dirs, prefix=False, by_manifest=False)

    def _resolve_installed(self, plugin_id: str, search_dirs: List[Path]) -> Optional[Path]:
        """The first of ``plugin_id``'s candidate ids found in ``search_dirs``.

        The id itself is looked for in every directory before any alias is,
        so an exact install anywhere beats an alias in the configured one.
        """
        for candidate in self._installed_id_candidates(plugin_id):
            found = resolve_plugin_dir(
                candidate, search_dirs, prefix=False, case_insensitive=False)
            if found is not None:
                return found
        return None

    def _candidate_plugin_dirs(self) -> List[Path]:
        """Directories that may hold installed plugins, configured one first."""
        return [d for d in store_search_dirs(self.plugins_dir) if d.exists()]

    def uninstall_plugin(self, plugin_id: str) -> bool:
        """
        Uninstall a plugin by removing its directory.

        Args:
            plugin_id: Plugin identifier

        Returns:
            True if uninstalled successfully (or already not installed)
        """
        plugin_path = self._find_plugin_path(plugin_id)

        if plugin_path is None or not plugin_path.exists():
            self.logger.info(f"Plugin {plugin_id} not found (already uninstalled)")
            return True  # Already uninstalled, consider this success
        
        try:
            self.logger.info(f"Uninstalling plugin: {plugin_id}")
            if self._safe_remove_directory(plugin_path):
                self.logger.info(f"Successfully uninstalled plugin: {plugin_id}")
                return True
            else:
                self.logger.error(f"Failed to remove plugin directory: {plugin_path}")
                return False
        except Exception as e:
            self.logger.error(f"Error uninstalling plugin {plugin_id}: {e}")
            return False
    

    
    def list_installed_plugins(self) -> List[str]:
        """
        Get list of installed plugin IDs.

        One entry per plugin directory in the configured directory that has a
        manifest.json, named by the manifest's id (the directory name when
        the manifest carries none, e.g. because it does not parse). Backup
        and hidden directories are not plugins.

        Returns:
            List of plugin IDs, sorted
        """
        if not self.plugins_dir.exists():
            return []
        index = PluginDirectoryIndex.scan(self.plugins_dir)
        return sorted(index.installed_ids(require_parseable_manifest=False))
