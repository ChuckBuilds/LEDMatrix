"""
One answer to "which directory holds plugin X?".

Five places used to answer it, each with its own rules and each re-parsing
every manifest per lookup: ``PluginManager`` discovery and
``get_plugin_directory``, ``PluginLoader.find_plugin_directory``,
``PluginStoreManager._find_plugin_path`` / ``list_installed_plugins`` and
``state_reconciliation.disk_plugin_ids``. The rules now live here once; what
still legitimately differs between callers (which directories to search,
whether a ``ledmatrix-`` prefix or a case difference counts as a match) is a
keyword argument at the call site, so a difference is always a visible choice
rather than an accident of which copy you read.

The rules
---------
* A directory is a *candidate* when it is a directory (a symlink to one counts:
  dev plugins are symlinked in) and its name is neither hidden (leading ``.``)
  nor carries :data:`BACKUP_MARKER`. store_manager renames a plugin aside with
  that marker during install/rollback; the aside still holds a manifest, so
  treating it as a plugin would resurrect a ghost.
* A plugin's id is its manifest ``id``. The directory name is only a fallback,
  for callers that must still see a plugin whose manifest is missing an id.
* Resolving an id within one directory: a directory whose manifest declares
  the id wins; among several, the one named exactly for the id, then
  ``ledmatrix-<id>``, then by name. Only when no manifest claims the id do
  directory names count: ``<id>``, then ``ledmatrix-<id>`` (``prefix=True``),
  then either of those ignoring case (``case_insensitive=True``). The name
  fallback still returns a directory whose manifest is unreadable -- that is
  how a broken plugin gets uninstalled or reinstalled. ``by_manifest=False``
  (``PluginManager.get_plugin_directory``, whose discovery map already holds
  the manifest answer) skips straight to the names.
* Several directories are searched one at a time, in the order given: the
  first directory that resolves the id at all wins, by manifest or by name.
* The id must be one plain path segment (``safe_path_component``); anything
  else resolves to nothing rather than being joined or truncated.
* Returned paths are ``search_dir / name`` and are not resolved, so a
  symlinked dev plugin keeps the path that lies inside the search directory.

Manifests are read at most once per :class:`PluginDirectoryIndex`; one index is
one scan.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Union

from src.common.path_safety import safe_path_component

__all__ = [
    'BACKUP_MARKER',
    'PLUGIN_DIR_PREFIX',
    'ManifestStatus',
    'PluginDirEntry',
    'PluginDirectoryIndex',
    'is_ignored_dir_name',
    'resolve_plugin_dir',
    'store_search_dirs',
]

#: Substring store_manager embeds in a plugin directory it has set aside
#: (``<id>.standalone-backup-preinstall`` / ``-migrating``). Existing debris on
#: devices carries exactly this text, so it must never change.
BACKUP_MARKER = '.standalone-backup-'

#: Legacy repository naming (``ledmatrix-<id>``); some installs still use it
#: as the directory name.
PLUGIN_DIR_PREFIX = 'ledmatrix-'

PathLike = Union[str, Path]


class ManifestStatus:
    """What reading ``manifest.json`` in a candidate directory produced."""
    OK = 'ok'                    # a JSON object with a non-empty "id"
    MISSING = 'missing'          # no manifest.json
    UNREADABLE = 'unreadable'    # I/O error or invalid JSON
    NOT_OBJECT = 'not_object'    # valid JSON, but not an object
    NO_ID = 'no_id'              # an object without a usable "id"


def is_ignored_dir_name(name: str) -> bool:
    """True for names that are never a plugin: hidden, or set aside mid-install."""
    return name.startswith('.') or BACKUP_MARKER in name


@dataclass
class PluginDirEntry:
    """One candidate directory and its manifest, read once."""
    path: Path
    status: str
    manifest: Optional[Any] = None
    error: Optional[BaseException] = None

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def manifest_id(self) -> Optional[str]:
        """The manifest's ``id`` when the manifest is usable, else None."""
        if self.status != ManifestStatus.OK:
            return None
        return self.manifest['id']

    @property
    def manifest_parses(self) -> bool:
        """The manifest exists and is valid JSON (of any shape)."""
        return self.status in (ManifestStatus.OK, ManifestStatus.NOT_OBJECT,
                               ManifestStatus.NO_ID)

    @property
    def installed_id(self) -> str:
        """The manifest id, falling back to the directory name."""
        return self.manifest_id or self.name


def _read_entry(path: Path) -> PluginDirEntry:
    manifest_path = path / 'manifest.json'
    if not manifest_path.is_file():
        return PluginDirEntry(path, ManifestStatus.MISSING)
    try:
        with open(manifest_path, 'r', encoding='utf-8') as handle:
            manifest = json.load(handle)
    except (OSError, ValueError) as exc:  # ValueError covers JSON + decode errors
        return PluginDirEntry(path, ManifestStatus.UNREADABLE, error=exc)
    if not isinstance(manifest, dict):
        return PluginDirEntry(path, ManifestStatus.NOT_OBJECT, manifest)
    plugin_id = manifest.get('id')
    if not plugin_id or not isinstance(plugin_id, str):
        return PluginDirEntry(path, ManifestStatus.NO_ID, manifest)
    return PluginDirEntry(path, ManifestStatus.OK, manifest)


def _preference(plugin_id: str, name: str) -> tuple:
    """Sort key among directories that all claim ``plugin_id``."""
    if name == plugin_id:
        rank = 0
    elif name == PLUGIN_DIR_PREFIX + plugin_id:
        rank = 1
    else:
        rank = 2
    return (rank, name)


@dataclass
class PluginDirectoryIndex:
    """Every candidate directory directly under ``root``, manifests read once.

    Build one with :meth:`scan`. It is a snapshot: a directory added or
    removed afterwards is not seen until the next scan.
    """
    root: Path
    entries: List[PluginDirEntry] = field(default_factory=list)
    #: Set when ``root`` exists but could not be listed.
    error: Optional[BaseException] = None
    _plugins: Optional[Dict[str, PluginDirEntry]] = field(
        default=None, init=False, repr=False, compare=False)

    @classmethod
    def scan(cls, root: PathLike) -> 'PluginDirectoryIndex':
        root = Path(root)
        index = cls(root)
        try:
            children = sorted(root.iterdir(), key=lambda p: p.name)
        except FileNotFoundError:
            return index
        except OSError as exc:
            index.error = exc
            return index
        for child in children:
            if is_ignored_dir_name(child.name):
                continue
            try:
                if not child.is_dir():
                    continue
            except OSError:
                continue
            index.entries.append(_read_entry(child))
        return index

    # -- listing ----------------------------------------------------------

    def plugins(self) -> Dict[str, PluginDirEntry]:
        """Manifest id -> entry, one entry per id.

        When several directories declare the same id, the one named for it
        wins, then ``ledmatrix-<id>``, then the first by name; see
        :meth:`duplicates` for the losers.
        """
        if self._plugins is not None:
            return self._plugins
        chosen: Dict[str, PluginDirEntry] = {}
        for entry in self.entries:
            plugin_id = entry.manifest_id
            if plugin_id is None:
                continue
            current = chosen.get(plugin_id)
            if current is None or (_preference(plugin_id, entry.name)
                                   < _preference(plugin_id, current.name)):
                chosen[plugin_id] = entry
        self._plugins = chosen
        return chosen

    def duplicates(self) -> Dict[str, List[PluginDirEntry]]:
        """Ids declared by more than one directory -> every such entry."""
        seen: Dict[str, List[PluginDirEntry]] = {}
        for entry in self.entries:
            if entry.manifest_id is not None:
                seen.setdefault(entry.manifest_id, []).append(entry)
        return {k: v for k, v in seen.items() if len(v) > 1}

    def installed_ids(self, *, require_parseable_manifest: bool) -> Set[str]:
        """Ids of everything that counts as installed.

        A directory counts when it has a manifest.json -- which must also be
        valid JSON when ``require_parseable_manifest``. Its id is the manifest
        id, or the directory name when the manifest does not carry one.
        """
        ids: Set[str] = set()
        for entry in self.entries:
            if entry.status == ManifestStatus.MISSING:
                continue
            if require_parseable_manifest and not entry.manifest_parses:
                continue
            ids.add(entry.installed_id)
        return ids

    def entry_for_installed_id(self, plugin_id: str) -> Optional[PluginDirEntry]:
        """The entry :meth:`installed_ids` reported as ``plugin_id``."""
        entry = self.plugins().get(plugin_id)
        if entry is not None:
            return entry
        for entry in self.entries:
            if entry.manifest_id is None and entry.name == plugin_id:
                return entry
        return None

    # -- lookup -----------------------------------------------------------

    def find(self, plugin_id: str, *, prefix: bool, case_insensitive: bool,
             by_manifest: bool = True) -> Optional[Path]:
        """Resolve ``plugin_id`` within this directory (rules in the module doc)."""
        plugin_id = _lookup_id(plugin_id)
        if plugin_id is None:
            return None

        if by_manifest:
            entry = self.plugins().get(plugin_id)
            if entry is not None:
                return entry.path

        names = _candidate_names(plugin_id, prefix)
        by_name = {e.name: e for e in self.entries}
        for name in names:
            if name in by_name:
                return by_name[name].path
        if case_insensitive:
            for low in (n.lower() for n in names):
                for entry in self.entries:
                    if entry.name.lower() == low:
                        return entry.path
        return None


def _lookup_id(plugin_id: Any) -> Optional[str]:
    """``plugin_id`` if it can name a plugin directory at all, else None."""
    plugin_id = safe_path_component(plugin_id)
    if plugin_id is None or is_ignored_dir_name(plugin_id):
        return None
    return plugin_id


def _candidate_names(plugin_id: str, prefix: bool) -> List[str]:
    names = [plugin_id]
    if prefix:
        names.append(PLUGIN_DIR_PREFIX + plugin_id)
    return names


def _is_dir(path: Path) -> bool:
    try:
        return path.is_dir()
    except OSError:
        return False


def resolve_plugin_dir(plugin_id: Any, search_dirs: Iterable[PathLike], *,
                       prefix: bool, case_insensitive: bool = False,
                       by_manifest: bool = True) -> Optional[Path]:
    """The directory holding ``plugin_id``, searching ``search_dirs`` in order.

    Each search directory is scanned once and each manifest in it read once.
    ``by_manifest=False`` skips the manifest pass and matches directory names
    only, which reads no manifests at all.

    Names are compared against the directory listing, never by probing
    ``search_dir / name``: on a case-insensitive filesystem that probe says
    ``Demo`` exists when the directory is ``demo``, which made the answer
    depend on the platform.
    """
    plugin_id = _lookup_id(plugin_id)
    if plugin_id is None:
        return None
    for search_dir in search_dirs:
        search_dir = Path(search_dir)
        if by_manifest or case_insensitive:
            found = PluginDirectoryIndex.scan(search_dir).find(
                plugin_id, prefix=prefix, case_insensitive=case_insensitive,
                by_manifest=by_manifest)
        else:
            found = _find_by_name(search_dir, _candidate_names(plugin_id, prefix))
        if found is not None:
            return found
    return None


def _find_by_name(search_dir: Path, names: List[str]) -> Optional[Path]:
    try:
        present = {child.name for child in search_dir.iterdir()}
    except OSError:
        return None
    for name in names:
        if name in present and _is_dir(search_dir / name):
            return search_dir / name
    return None


def store_search_dirs(plugins_dir: PathLike) -> List[Path]:
    """Directories the plugin store searches: the configured one, then a
    sibling ``plugins/`` (the legacy/dev location) when that is a different
    directory. Discovery deliberately does NOT use this -- it scans only the
    configured directory (see CLAUDE.md, test_discovery_path_contract.py)."""
    plugins_dir = Path(plugins_dir)
    dirs = [plugins_dir]
    try:
        base = plugins_dir if plugins_dir.is_absolute() else plugins_dir.resolve()
        sibling = base.parent / 'plugins'
        if sibling != base:
            dirs.append(sibling)
    except (OSError, ValueError):
        pass
    return dirs
