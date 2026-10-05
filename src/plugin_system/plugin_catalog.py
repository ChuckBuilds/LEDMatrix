"""
Plugin catalog: what the web process knows about installed plugins.

The web interface and the display run as two processes. Only the display
imports plugin code and runs it; the web process reads plugins as files --
manifest, config schema, the plugin's section of config.json, the installed
version -- and never imports a plugin module, instantiates a plugin class or
calls a plugin lifecycle hook. This class is that read side.

It keeps the method names of the read-only part of :class:`PluginManager`
(``discover_plugins``, ``plugin_manifests``, ``get_plugin_info``,
``get_plugin_directory``, ``get_plugin_display_modes``,
``find_plugin_for_mode``), so code that only ever read through a manager
reads through a catalog unchanged. It has nothing that runs a plugin: no
``load_plugin``, ``get_plugin`` or ``plugins``.

Runtime state -- whether the display has a plugin loaded, its health, its
errors -- is not here either, with one exception: given a ``runtime_source``,
the mode lookups prefer the modes the running display registered. The display process publishes what it knows to
the shared cache (health and resource metrics, the current mode, the error
aggregator snapshot), and the web routes read those publications. What the
display does not publish (which plugins it has loaded, its plugin state
machine) the web cannot know, and reports as unknown.

See docs/ARCHITECTURE.md ("Web and display processes").
"""

import json
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union, cast

from src.common.permission_utils import (
    ensure_directory_permissions, get_plugin_dir_mode,
)
from src.logging_config import get_logger
from src.plugin_system.plugin_dirs import (
    ManifestStatus, PluginDirectoryIndex, resolve_plugin_dir,
)

PathLike = Union[str, Path]

#: How long one read of the display's runtime view answers mode lookups. A
#: listing asks once per plugin; the cache copy is a file read each time.
_RUNTIME_VIEW_TTL_SECONDS = 1.0


class PluginCatalog:
    """Manifests, schemas, config and versions of the installed plugins.

    Discovery is explicit and cheap to repeat: :meth:`discover_plugins`
    rescans the plugins directory and replaces the manifest map, so an
    uninstalled plugin disappears and a new one appears.
    """

    def __init__(self, plugins_dir: PathLike, config_manager: Optional[Any] = None,
                 schema_manager: Optional[Any] = None,
                 runtime_source: Optional[Callable[[], Any]] = None) -> None:
        self.plugins_dir: Path = Path(plugins_dir)
        self.config_manager = config_manager
        self.schema_manager = schema_manager
        # Returns the display's PluginRuntimeView
        # (src/plugin_system/plugin_runtime.py). Its live view carries the
        # modes the display registered, which the mode lookups below prefer
        # to the manifest's. None: manifests only.
        self.runtime_source = runtime_source
        self._runtime_view_memo: Optional[tuple] = None
        self.logger = get_logger(__name__)

        # Guards plugin_manifests/plugin_directories: request threads read
        # them while another request (or startup reconciliation) rescans.
        self._lock = threading.RLock()
        self.plugin_manifests: Dict[str, Dict[str, Any]] = {}
        self.plugin_directories: Dict[str, Path] = {}
        self._skip_reported: set = set()

        # The Plugin Store installs into this directory, so it has to exist.
        # The display service logs its own error if it cannot use it; the web
        # interface stays up either way.
        try:
            ensure_directory_permissions(self.plugins_dir, get_plugin_dir_mode())
        except OSError as exc:
            self.logger.warning("Could not create plugins directory %s: %s",
                                self.plugins_dir, exc)

    # -- discovery --------------------------------------------------------

    def discover_plugins(self) -> List[str]:
        """Rescan the plugins directory; return the discovered plugin ids.

        The rules for what counts as a plugin and which directory wins for a
        duplicated id are :class:`PluginDirectoryIndex`'s, the same ones the
        display process loads by. Only the configured directory is scanned.
        """
        index = PluginDirectoryIndex.scan(self.plugins_dir)
        if index.error is not None:
            self.logger.error("Error scanning plugins directory %s: %s",
                              self.plugins_dir, index.error)
        for entry in index.entries:
            if entry.status in (ManifestStatus.UNREADABLE, ManifestStatus.NOT_OBJECT,
                                ManifestStatus.NO_ID):
                # The display logs these at load time; once per process is
                # enough here, since discovery runs on page loads.
                if entry.name not in self._skip_reported:
                    self._skip_reported.add(entry.name)
                    self.logger.info("Not listing %s: its manifest.json is unusable (%s)",
                                     entry.name, entry.status)

        plugins = index.plugins()
        manifests = {pid: entry.manifest for pid, entry in plugins.items()}
        directories = {pid: entry.path for pid, entry in plugins.items()}
        with self._lock:
            self.plugin_manifests.clear()
            self.plugin_manifests.update(manifests)
            self.plugin_directories.clear()
            self.plugin_directories.update(directories)
        return list(plugins)

    def discovered_plugin_ids(self) -> set:
        """Snapshot of the discovered ids, taken under the lock."""
        with self._lock:
            return set(self.plugin_manifests)

    # -- manifests --------------------------------------------------------

    def get_manifest(self, plugin_id: str) -> Optional[Dict[str, Any]]:
        """A copy of the manifest discovery read for ``plugin_id``, or None."""
        with self._lock:
            manifest = self.plugin_manifests.get(plugin_id)
        return dict(manifest) if manifest else None

    def get_plugin_info(self, plugin_id: str) -> Optional[Dict[str, Any]]:
        """The plugin's manifest, as a new dict -- metadata only.

        Unlike ``PluginManager.get_plugin_info`` there are no ``loaded``,
        ``runtime_info`` or ``state`` keys: those described plugin instances
        in this process, which no longer exist.
        """
        return self.get_manifest(plugin_id)

    def get_all_plugin_info(self) -> List[Dict[str, Any]]:
        """:meth:`get_plugin_info` for every discovered plugin."""
        with self._lock:
            ids = list(self.plugin_manifests)
        return [info for info in (self.get_plugin_info(pid) for pid in ids) if info]

    def read_manifest(self, plugin_id: str) -> Optional[Dict[str, Any]]:
        """The manifest as it is on disk now, not as discovery last saw it.

        For reads that must reflect a change made since the last scan -- the
        version just after an update, say. None when the plugin has no
        directory or its manifest is missing, unreadable or not an object.
        """
        plugin_dir = self.get_plugin_directory(plugin_id)
        if plugin_dir is None:
            return None
        try:
            with open(Path(plugin_dir) / 'manifest.json', 'r', encoding='utf-8') as f:
                manifest = json.load(f)
        except (OSError, ValueError) as exc:
            self.logger.debug("Could not read manifest for %s: %s", plugin_id, exc)
            return None
        return manifest if isinstance(manifest, dict) else None

    def get_installed_version(self, plugin_id: str) -> str:
        """The installed version from the on-disk manifest, or ''."""
        manifest = self.read_manifest(plugin_id) or {}
        version = manifest.get('version', '')
        return version if isinstance(version, str) else str(version)

    def get_plugin_directory(self, plugin_id: str) -> Optional[str]:
        """Where ``plugin_id`` is installed, or None.

        Same rules as ``PluginManager.get_plugin_directory``: the discovered
        directory, else ``<id>`` then ``ledmatrix-<id>`` by name within the
        plugins directory. An id that is not one plain path segment is
        refused rather than joined onto the plugins directory.
        """
        with self._lock:
            if plugin_id in self.plugin_directories:
                return str(self.plugin_directories[plugin_id])
        plugin_dir = resolve_plugin_dir(
            plugin_id, [self.plugins_dir], prefix=True, case_insensitive=False,
            by_manifest=False)
        return str(plugin_dir) if plugin_dir is not None else None

    def _runtime_view(self) -> Any:
        """The display's runtime view, read at most once a second; None
        without a source or when reading it fails."""
        if self.runtime_source is None:
            return None
        now = time.monotonic()
        memo = self._runtime_view_memo
        if memo is not None and now - memo[0] < _RUNTIME_VIEW_TTL_SECONDS:
            return memo[1]
        try:
            view = self.runtime_source()
        except Exception as exc:  # a lookup must still answer from manifests
            self.logger.debug("Could not read the display's runtime view: %s", exc)
            view = None
        self._runtime_view_memo = (now, view)
        return view

    def _live_display_modes(self, plugin_id: str) -> Optional[List[str]]:
        """The modes the running display registered for ``plugin_id``, or None."""
        view = self._runtime_view()
        lookup = getattr(view, 'display_modes', None)
        if not callable(lookup):
            return None
        try:
            modes = lookup(plugin_id)
        except Exception as exc:
            self.logger.debug("Could not read display modes for %s: %s", plugin_id, exc)
            return None
        return list(modes) if isinstance(modes, list) and modes else None

    def get_plugin_display_modes(self, plugin_id: str) -> List[str]:
        """The modes the display registered for the plugin, else the
        manifest's ``display_modes``, else [].

        A plugin may compute its modes at run time (``plugin.modes``): each
        league soccer-scoreboard's ``custom_leagues`` adds is a mode no
        manifest can list ahead of time (#668). The running display
        publishes what it registered, and that wins while the display is
        live and has the plugin loaded. Otherwise -- display stopped, plugin
        disabled -- the declared list is the best answer there is.
        """
        live = self._live_display_modes(plugin_id)
        if live is not None:
            return live
        with self._lock:
            manifest = self.plugin_manifests.get(plugin_id)
        modes = (manifest or {}).get('display_modes', [])
        return list(modes) if isinstance(modes, list) else []

    def find_plugin_for_mode(self, mode: str) -> Optional[str]:
        """The plugin that registered ``mode`` on the running display, else
        the one whose manifest declares it (case-insensitive both ways)."""
        wanted = mode.strip().lower()
        with self._lock:
            manifests = dict(self.plugin_manifests)
        for plugin_id in manifests:
            live = self._live_display_modes(plugin_id)
            if live and any(m.lower() == wanted for m in live):
                return plugin_id
        for plugin_id, manifest in manifests.items():
            modes = manifest.get('display_modes')
            if isinstance(modes, list) and any(
                    isinstance(m, str) and m.lower() == wanted for m in modes):
                return plugin_id
        return None

    # -- schema and config ------------------------------------------------

    def get_schema(self, plugin_id: str, use_cache: bool = True) -> Optional[Dict[str, Any]]:
        """The plugin's config schema through SchemaManager, or None."""
        if self.schema_manager is None:
            return None
        schema = self.schema_manager.load_schema(plugin_id, use_cache=use_cache)
        return cast(Optional[Dict[str, Any]], schema)

    def get_config(self, plugin_id: str) -> Dict[str, Any]:
        """The plugin's section of config.json (secrets merged), or {}."""
        if self.config_manager is None:
            return {}
        section = (self.config_manager.load_config() or {}).get(plugin_id)
        return section if isinstance(section, dict) else {}

    def is_enabled(self, plugin_id: str) -> bool:
        """Whether config.json enables the plugin, by the display's rule.

        The display loads a plugin only when its section says
        ``"enabled": true``; a missing flag or section means disabled
        (``DisplayController._reconcile_enabled_plugins``).
        """
        return bool(self.get_config(plugin_id).get('enabled', False))


def display_restart_required(action: str, plugin_enabled: bool, *,
                             changed: bool = True,
                             preserve_config: bool = False) -> bool:
    """Whether a store operation needs a display restart to reach the panel.

    The display loads and unloads plugins live only through its config
    watcher: when a plugin's ``enabled`` flag changes it reconciles the
    running set (``DisplayController._reconcile_enabled_plugins``), and
    loading reads the plugin fresh from disk. Nothing makes it reload a
    plugin it is already running, and nothing tells it about files changing
    under a plugin whose flag did not move. So:

    - ``install``: a plugin that is not enabled needs nothing -- enabling it
      later loads it. One already enabled in config (a reinstall, or a
      config carried over) is not picked up until a restart.
    - ``update``: the display keeps running the code it loaded until it
      restarts, if it runs the plugin at all -- only when it is enabled.
      ``changed=False`` (already up to date) needs nothing. The update route
      first asks the display to reload it over the control socket
      (``_reload_after_store_update``); this answer stands when it cannot.
    - ``uninstall``: removing the plugin's config section flips its enabled
      flag, and the reconcile unloads it. With ``preserve_config`` the flag
      stays, and an enabled plugin keeps running until a restart.

    ``plugin_enabled`` is the config flag as it was before the operation.
    """
    if not plugin_enabled:
        return False
    if action == 'install':
        return True
    if action == 'update':
        return changed
    if action == 'uninstall':
        return preserve_config
    raise ValueError(f"unknown store action: {action!r}")
