#!/usr/bin/env python3
"""Who still calls or overrides the core methods marked ``@deprecated``?

A deprecated plugin-facing method may only be removed once nothing uses it,
and plugins live in other repositories. This script answers the question for
every method ``src/deprecation.py``'s decorator marks in core:

1. it lists the markers by parsing ``src/`` (so the list can never drift from
   the code);
2. it scans, with the ``ast`` module, core itself (``src/``,
   ``web_interface/``, ``scripts/``, the top-level ``*.py``; ``test/``
   separately), the official monorepo's ``plugins/`` directory, and every
   third-party plugin the monorepo's ``plugins.json`` lists with its own repo
   URL (shallow-cloned read-only into a cache directory);
3. it reports, per method and per plugin, the calls and overrides it found,
   and a verdict: unused (safe to remove in the marker's release), still used
   (keep or migrate those plugins first), or needs review.

Matching is by method name, so it has to separate real uses from unrelated
methods that happen to share the name (the weather plugin's own ``draw_sun``,
say). Each hit is classified by what it is attached to:

* **call** -- ``<receiver>.name`` where the receiver is named like the owning
  object (``self.cache_manager``, ``display_manager``, ``plugin_manager`` ...,
  or a local alias assigned from one), or ``self``/``super()`` inside a class
  that subclasses the owner. Attribute references that are not called
  (``callback=cm.get_cache_metrics``) count too.
* **override** -- ``def name`` in a class that subclasses the owner.
* **review** -- ``<receiver>.name`` where the receiver says nothing about its
  type, or ``getattr(obj, "name")``. Possibly a real use; read the listed line.
* **unrelated** -- ``self.name`` inside a class that defines ``name`` itself
  and does not subclass the owner, ``Klass.name`` where the same tree defines
  ``Klass.name``, or ``def name`` in such a class: a name collision, not a use.
* **internal** -- a hit inside the body of another deprecated core method
  (``draw_rain`` calling ``draw_cloud``): it keeps the method only as long as
  that caller is kept.

Only calls and overrides make a method "still used"; review hits make it
"needs review"; hits in test files are listed but never block removal (a test
that mocks a method does not need it to exist).

    python3 scripts/plugin_api_usage.py                      # clone everything, print Markdown
    python3 scripts/plugin_api_usage.py --monorepo ../ledmatrix-plugins
    python3 scripts/plugin_api_usage.py --output docs/DEPRECATIONS_3.8.md
    python3 scripts/plugin_api_usage.py --format json

Nothing is ever written to the repositories it scans: the monorepo path is only
read, and clones live in ``--cache-dir``.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess  # nosec B404 - list-form argv only, no shell  # nosemgrep
import sys
import tempfile
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Set, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
MONOREPO_URL = "https://github.com/ChuckBuilds/ledmatrix-plugins"
MONOREPO_SLUG = "chuckbuilds/ledmatrix-plugins"

#: Receiver names that mean "this is the owning core object". Compared against
#: the last name in the receiver (``self.plugin_manager.cache_manager`` ->
#: ``cache_manager``), lower-cased with leading underscores stripped.
OWNER_RECEIVERS: Dict[str, Tuple[str, ...]] = {
    "CacheManager": ("cache_manager", "cache_mgr", "cachemanager", "cache", "cm"),
    "DisplayManager": ("display_manager", "display_mgr", "displaymanager", "display", "dm"),
    "FontManager": ("font_manager", "font_mgr", "fontmanager", "fonts", "fm"),
    "PluginManager": ("plugin_manager", "plugin_mgr", "pluginmanager", "pm"),
    "PluginStateManager": ("state_manager", "plugin_state", "state_mgr"),
    "ConfigManager": ("config_manager", "config_mgr", "configmanager"),
    "LogoDownloader": ("logo_downloader", "downloader", "logodownloader"),
    "APIHelper": ("api_helper", "apihelper", "api"),
    "BackgroundDataService": ("background_service", "background_data_service", "bg_service",
                              "data_service"),
    "BaseOddsManager": ("odds_manager", "oddsmanager", "odds"),
    "DynamicTeamResolver": ("dynamic_resolver", "team_resolver", "resolver"),
}

#: Directories never scanned (vendored environments, VCS metadata, caches).
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", "env",
             "site-packages", ".tox", ".mypy_cache", ".pytest_cache"}

CORE_DIRS = ("src", "web_interface", "scripts")
CORE_TEST_DIRS = ("test",)


# --------------------------------------------------------------------------
# Markers


@dataclass
class Marker:
    owner: str          # class name, e.g. "CacheManager"
    method: str
    removal: str
    alternative: Optional[str]
    module: str         # e.g. "src.cache_manager"
    line: int

    @property
    def key(self) -> str:
        return f"{self.owner}.{self.method}"


def _decorator_name(node: ast.expr) -> Optional[str]:
    target = node.func if isinstance(node, ast.Call) else node
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute):
        return target.attr
    return None


def find_markers(core_root: Path) -> List[Marker]:
    """Every ``@deprecated(...)`` method under ``core_root/src``."""
    markers: List[Marker] = []
    for path in sorted((core_root / "src").rglob("*.py")):
        if path.name == "deprecation.py":
            continue
        tree = _parse(path)
        if tree is None:
            continue
        module = ".".join(path.relative_to(core_root).with_suffix("").parts)
        for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
            for fn in cls.body:
                if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for dec in fn.decorator_list:
                    if _decorator_name(dec) != "deprecated" or not isinstance(dec, ast.Call):
                        continue
                    args = [a.value if isinstance(a, ast.Constant) else None for a in dec.args]
                    kw = {k.arg: k.value.value for k in dec.keywords
                          if isinstance(k.value, ast.Constant)}
                    removal = args[0] if args else kw.get("removal")
                    alternative = args[1] if len(args) > 1 else kw.get("alternative")
                    markers.append(Marker(cls.name, fn.name, str(removal), alternative,
                                          module, fn.lineno))
    return markers


# --------------------------------------------------------------------------
# Scanning


@dataclass
class Hit:
    kind: str           # call | override | review | unrelated | internal
    path: str
    line: int
    code: str
    test: bool
    via: Optional[str] = None   # internal: the deprecated core method it sits in


@dataclass
class Source:
    """One plugin (or core) tree to scan."""
    name: str
    group: str          # core | core-tests | monorepo | third-party
    root: Optional[Path]
    error: Optional[str] = None
    hits: Dict[str, List[Hit]] = field(default_factory=lambda: defaultdict(list))
    files: int = 0      # Python files scanned


def _parse(path: Path) -> Optional[ast.AST]:
    try:
        return ast.parse(path.read_text(encoding="utf-8", errors="replace"), str(path))
    except (SyntaxError, ValueError):
        return None


def _iter_py(root: Path) -> Iterator[Path]:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            if name.endswith(".py"):
                yield Path(dirpath) / name


def _is_test_path(rel: Path) -> bool:
    parts = [p.lower() for p in rel.parts]
    return (any(p in ("test", "tests") for p in parts[:-1])
            or parts[-1].startswith("test_") or parts[-1].endswith("_test.py")
            or parts[-1] == "conftest.py")


def _terminal(node: ast.expr) -> Optional[str]:
    """The last name in a receiver expression, or None if it has none."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Call):
        return _terminal(node.func)
    if isinstance(node, ast.Subscript):
        return _terminal(node.value)
    return None


def _norm(name: Optional[str]) -> str:
    return (name or "").lstrip("_").lower()


def _base_names(cls: ast.ClassDef) -> List[str]:
    return [t for t in (_terminal(b) for b in cls.bases) if t]


class _Scanner(ast.NodeVisitor):
    """Collect hits for every marked method name in one file."""

    def __init__(self, markers: Dict[str, List[Marker]], lines: List[str],
                 rel: str, test: bool, core_modules: Dict[str, str], module: Optional[str],
                 local_definers: Dict[str, Set[str]], built: Dict[str, str]):
        self.markers = markers              # method name -> markers with that name
        self.local_definers = local_definers  # method name -> this tree's own classes/modules defining it
        self.built = built                  # ``x``/``self.x`` -> class it was built from in this file
        self.lines = lines
        self.rel = rel
        self.test = test
        self.core_modules = core_modules    # owner class -> defining module (core only)
        self.module = module                # this file's module when scanning core
        self.classes: List[ast.ClassDef] = []
        self.scope: List[ast.AST] = []      # enclosing classes and functions
        self.aliases: List[Dict[str, str]] = [{}]   # local name -> owner class
        self.out: Dict[str, List[Hit]] = defaultdict(list)

    # -- helpers
    def _code(self, node: ast.AST) -> str:
        line = self.lines[node.lineno - 1] if 0 < node.lineno <= len(self.lines) else ""
        return line.strip()[:160]

    def _add(self, marker: Marker, kind: str, node: ast.AST) -> None:
        via = self._inside_deprecated()
        if via and kind != "unrelated":
            # Only reached through another deprecated method: goes when that does.
            kind = "internal"
        self.out[marker.key].append(Hit(kind, self.rel, node.lineno, self._code(node),
                                        self.test, via if kind == "internal" else None))

    def _inside_deprecated(self) -> Optional[str]:
        """``Owner.method`` when this node sits in a deprecated core method's body."""
        for i in range(len(self.scope) - 2, -1, -1):
            cls, fn = self.scope[i], self.scope[i + 1]
            if isinstance(cls, ast.ClassDef):
                if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    for m in self.markers.get(fn.name, ()):
                        if self._is_owner_class(cls, m.owner):
                            return m.key
                return None
        return None

    def _owner_for_receiver(self, name: Optional[str]) -> Optional[str]:
        n = _norm(name)
        for scope in reversed(self.aliases):
            if name in scope:
                return scope[name]
        for owner, receivers in OWNER_RECEIVERS.items():
            if n in receivers:
                return owner
        return None

    def _is_owner_class(self, cls: ast.ClassDef, owner: str) -> bool:
        """True for the real core class (only when scanning its own module)."""
        return (self.module is not None and cls.name == owner
                and self.core_modules.get(owner) == self.module)

    def _subclasses(self, cls: ast.ClassDef, owner: str) -> bool:
        return owner in _base_names(cls)

    def _class_defines(self, cls: ast.ClassDef, name: str) -> bool:
        return any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name
                   for n in cls.body)

    # -- scopes
    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for fn in node.body:
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and fn.name in self.markers:
                for m in self.markers[fn.name]:
                    if self._is_owner_class(node, m.owner):
                        continue                      # the definition itself
                    kind = "override" if self._subclasses(node, m.owner) else "unrelated"
                    self._add(m, kind, fn)
        self.classes.append(node)
        self.scope.append(node)
        self.generic_visit(node)
        self.scope.pop()
        self.classes.pop()

    def _visit_function(self, node) -> None:
        self.aliases.append({})
        self.scope.append(node)
        self.generic_visit(node)
        self.scope.pop()
        self.aliases.pop()

    visit_FunctionDef = _visit_function
    visit_AsyncFunctionDef = _visit_function

    def visit_Assign(self, node: ast.Assign) -> None:
        # ``dm = self.display_manager`` makes ``dm.draw_sun()`` a call.
        owner = self._owner_for_receiver(_terminal(node.value))
        for target in node.targets:
            if isinstance(target, ast.Name) and owner:
                self.aliases[-1][target.id] = owner
        self.generic_visit(node)

    # -- uses
    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in self.markers:
            for m in self.markers[node.attr]:
                self._add(m, self._classify(node, m), node)
        self.generic_visit(node)

    def _classify(self, node: ast.Attribute, m: Marker) -> str:
        recv = node.value
        cls = self.classes[-1] if self.classes else None
        is_self = isinstance(recv, ast.Name) and recv.id in ("self", "cls")
        is_super = (isinstance(recv, ast.Call) and isinstance(recv.func, ast.Name)
                    and recv.func.id == "super")
        if is_self or is_super:
            if cls is not None and (self._is_owner_class(cls, m.owner) or self._subclasses(cls, m.owner)):
                return "call"
            if cls is not None and self._class_defines(cls, m.method):
                return "unrelated"
            return "review"
        name = _terminal(recv)
        if self._owner_for_receiver(name) == m.owner:
            return "call"
        definers = self.local_definers.get(m.method, ())
        if name in definers or self.built.get(name or "") in definers:
            # e.g. the weather plugin's WeatherIcons.draw_sun
            return "unrelated"
        return "review"

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if (isinstance(func, ast.Name) and func.id in ("getattr", "hasattr", "setattr", "delattr")
                and len(node.args) >= 2 and isinstance(node.args[1], ast.Constant)
                and node.args[1].value in self.markers):
            for m in self.markers[node.args[1].value]:
                owner = self._owner_for_receiver(_terminal(node.args[0]))
                self._add(m, "call" if owner == m.owner else "review", node)
        self.generic_visit(node)


def _built_from(tree: ast.AST) -> Dict[str, str]:
    """``{name: Class}`` for every ``name = Class(...)`` / ``self.name = Class(...)``."""
    built: Dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            cls = _terminal(node.value.func)
            for target in node.targets:
                name = _terminal(target) if isinstance(target, (ast.Name, ast.Attribute)) else None
                if name and cls:
                    built[name] = cls
    return built


def scan_tree(source: Source, roots: Iterable[Path], base: Path, markers: List[Marker],
              core: bool, test_override: Optional[bool] = None,
              definer_roots: Iterable[Path] = ()) -> None:
    by_name: Dict[str, List[Marker]] = defaultdict(list)
    for m in markers:
        by_name[m.method].append(m)
    core_modules = {m.owner: m.module for m in markers}
    owners = {m.owner for m in markers}
    files: List[Tuple[Path, str, Optional[ast.AST]]] = []
    for root in roots:
        if not root.exists():
            continue
        for path in ([root] if root.is_file() else sorted(_iter_py(root))):
            source.files += 1
            text = path.read_text(encoding="utf-8", errors="replace")
            if any(name in text for name in by_name):
                files.append((path, text, _parse(path)))

    # Classes (and modules) in this tree with their own method of a marked
    # name, so ``WeatherIcons.draw_sun()`` is recognised as theirs.
    local_definers: Dict[str, Set[str]] = defaultdict(set)
    definer_files = list(files)
    for root in definer_roots:
        for path in (sorted(_iter_py(root)) if root.is_dir() else ()):
            text = path.read_text(encoding="utf-8", errors="replace")
            if any(name in text for name in by_name):
                definer_files.append((path, text, _parse(path)))
    for path, _, tree in definer_files:
        for node in (tree.body if tree is not None else ()):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in by_name:
                local_definers[node.name].add(path.stem)
        for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)) if tree else ():
            if cls.name in owners and core:
                continue
            if owners & set(_base_names(cls)):
                continue
            for fn in cls.body:
                if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and fn.name in by_name:
                    local_definers[fn.name].add(cls.name)

    for path, text, tree in files:
        rel = path.relative_to(base)
        test = _is_test_path(rel) if test_override is None else test_override
        if tree is None:
            # Unparseable (Python 2, a template ...): fall back to text, as review.
            for no, line in enumerate(text.splitlines(), 1):
                for name in by_name:
                    if re.search(rf"{re.escape(name)}", line):
                        for m in by_name[name]:
                            source.hits[m.key].append(
                                Hit("review", rel.as_posix(), no, line.strip()[:160], test))
            continue
        module = ".".join(rel.with_suffix("").parts) if core else None
        scanner = _Scanner(by_name, text.splitlines(), rel.as_posix(), test,
                           core_modules, module, local_definers, _built_from(tree))
        scanner.visit(tree)
        for key, hits in scanner.out.items():
            source.hits[key].extend(hits)


# --------------------------------------------------------------------------
# Fetching plugin trees (read-only)


def _git(*args: str, cwd: Optional[Path] = None) -> subprocess.CompletedProcess:
    # Never stop to ask for credentials: a deleted or private plugin repo
    # should be reported as not scanned, not hang the scan.
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    return subprocess.run(  # nosec B603 B607 - list-form git argv, no shell; URLs follow "--"  # nosemgrep
        ["git", *args], cwd=cwd, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=300, env=env)


def _rmtree(path: Path) -> None:
    """Delete a clone; git marks pack files read-only, which Windows refuses to delete."""
    import shutil
    import stat

    def retry(func, target, _exc):
        os.chmod(target, stat.S_IWRITE)
        func(target)

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=retry)
    else:
        shutil.rmtree(path, onerror=retry)


def shallow_clone(url: str, branch: Optional[str], dest: Path, reuse: bool) -> Optional[str]:
    """Clone ``url`` into ``dest`` (depth 1), replacing any earlier clone.

    Returns an error string, or None on success.
    """
    if reuse and (dest / ".git").exists():
        return None
    if dest.exists():
        _rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    args = ["-c", "core.longpaths=true", "clone", "--quiet", "--depth", "1"]
    if branch:
        args += ["--branch", branch]
    # "--" ends option parsing: a registry URL starting with "-" (for example
    # "--upload-pack=...") is then only ever a repository argument.
    result = _git(*args, "--", url, str(dest))
    if result.returncode != 0 and branch:
        result = _git("-c", "core.longpaths=true", "clone", "--quiet", "--depth", "1",
                      "--", url, str(dest))
    if result.returncode != 0:
        lines = (result.stderr or result.stdout).strip().splitlines()
        return lines[-1] if lines else "git clone failed"
    return None


def _head(path: Path, branch: bool = True) -> str:
    """Commit (and branch) of a checkout, via read-only git calls."""
    rev = _git("--no-optional-locks", "rev-parse", "--short=8", "HEAD", cwd=path)
    if rev.returncode != 0:
        return "unknown revision"
    if not branch:
        return rev.stdout.strip()
    ref = _git("--no-optional-locks", "rev-parse", "--abbrev-ref", "HEAD", cwd=path)
    return f"{ref.stdout.strip()} @ {rev.stdout.strip()}"


def _plugin_id(plugin_dir: Path) -> str:
    try:
        return json.loads((plugin_dir / "manifest.json").read_text(encoding="utf-8"))["id"]
    except (OSError, ValueError, KeyError, TypeError):
        return plugin_dir.name


def _is_monorepo(url: str) -> bool:
    return MONOREPO_SLUG in url.lower().rstrip("/").removesuffix(".git")


# --------------------------------------------------------------------------
# Report


def verdicts(markers: List[Marker], sources: List[Source]) -> Dict[str, Tuple[str, str]]:
    """``{Owner.method: (status, text)}`` across every source.

    An *internal* hit (a call from inside another deprecated method) keeps a
    method only while that caller is itself kept, so statuses are resolved
    until they stop changing.
    """
    failed = [s.name for s in sources if s.error]
    status: Dict[str, Tuple[str, str]] = {}
    for _ in range(len(markers) + 1):
        changed = False
        for m in markers:
            used, review = [], []
            for s in sources:
                live = [h for h in s.hits.get(m.key, []) if not h.test]
                if any(h.kind in ("call", "override") for h in live) or any(
                        h.kind == "internal" and status.get(h.via, ("",))[0] == "used"
                        for h in live):
                    used.append(s.name)
                elif any(h.kind == "review" for h in live) or any(
                        h.kind == "internal" and status.get(h.via, ("",))[0] == "review"
                        for h in live):
                    review.append(s.name)
            if used:
                new = ("used", f"still used by {', '.join(used)} — keep or migrate first")
            elif review:
                new = ("review", f"needs review: possible use in {', '.join(review)}")
            elif failed:
                new = ("unknown", f"not proven unused: {len(failed)} plugin(s) could not be scanned")
            else:
                new = ("unused", f"unused — safe to remove in {m.removal}")
            if status.get(m.key) != new:
                status[m.key] = new
                changed = True
        if not changed:
            break
    return status


def _counts(hits: List[Hit]) -> Dict[str, int]:
    c: Dict[str, int] = defaultdict(int)
    for h in hits:
        c[("test " if h.test else "") + h.kind] += 1
    return c


def _usage_cell(marker: Marker, sources: List[Source], kinds: Tuple[str, ...]) -> str:
    parts = []
    for s in sources:
        c = _counts(s.hits.get(marker.key, []))
        bits = [f"{c[k]} {k}{'s' if c[k] != 1 else ''}" for k in kinds if c[k]]
        if bits:
            parts.append(f"{s.name} ({', '.join(bits)})")
    return "; ".join(parts) or "—"


def render_markdown(markers: List[Marker], sources: List[Source], meta: Dict[str, str]) -> str:
    out: List[str] = []
    w = out.append
    w("# Deprecated plugin APIs: usage scan")
    w("")
    w("Generated by `scripts/plugin_api_usage.py` — do not edit by hand; re-run it "
      "(see [How to re-run](#how-to-re-run)).")
    w("")
    w(f"- Scanned: {meta['date']}, core {meta['core_version']}")
    w(f"- Monorepo: {meta['monorepo']}")
    w(f"- Third-party plugins: {meta['third_party']}")
    failed = [s for s in sources if s.error]
    if failed:
        w("- **Not scanned:** " + "; ".join(f"{s.name} ({s.error})" for s in failed))
    w("")
    status = verdicts(markers, sources)
    tally: Dict[str, int] = defaultdict(int)
    for st, _ in status.values():
        tally[st] += 1
    w(f"**{len(markers)} deprecated methods: {tally['unused']} unused, "
      f"{tally['used']} still used, {tally['review']} need review"
      + (f", {tally['unknown']} not proven" if tally["unknown"] else "") + ".**")
    w("")
    w("Counted per plugin: a *call* is `<receiver>.method` on an object named like "
      "the owner (`cache_manager`, `display_manager`, `font_manager`, `plugin_manager`), "
      "or on `self`/`super()` in a subclass; an *override* is `def method` in a subclass "
      "of the owner. *Review* hits are `.method` on a receiver whose type the scan cannot "
      "tell. *Internal* hits sit inside another deprecated core method and go with it. "
      "*Unrelated* hits are a different class's own method with the same name "
      "(a name collision), and never block removal; neither do hits in test files.")
    w("")
    w("| Method | Removal | Core | Plugins (calls / overrides) | Name collisions & tests | Verdict |")
    w("|---|---|---|---|---|---|")
    core_sources = [s for s in sources if s.group in ("core", "core-tests")]
    plugin_sources = [s for s in sources if s.group not in ("core", "core-tests")]
    for m in markers:
        core = _usage_cell(m, core_sources, ("call", "override", "review", "internal",
                                             "test call", "test override", "test review",
                                             "test internal"))
        plugins = _usage_cell(m, plugin_sources, ("call", "override", "review"))
        other = _usage_cell(m, plugin_sources, ("unrelated", "test call", "test override",
                                                "test review", "test unrelated"))
        w(f"| `{m.key}` | {m.removal} | {core} | {plugins} | {other} | {status[m.key][1]} |")
    w("")

    groups = [("unused", "Unused — safe to remove"), ("used", "Still used — keep or migrate first"),
              ("review", "Needs review"), ("unknown", "Not proven unused")]
    for st, title in groups:
        names = [m.key for m in markers if status[m.key][0] == st]
        if names:
            w(f"## {title} ({len(names)})")
            w("")
            w(", ".join(f"`{n}`" for n in names))
            w("")

    detail = [(m, s, h) for m in markers for s in sources
              for h in s.hits.get(m.key, []) if h.kind != "unrelated" or not h.test]
    if detail:
        w("## Every hit")
        w("")
        w("File paths are relative to the plugin's directory (core: the repo root).")
        w("")
        w("| Method | Where | File:line | Kind | Code |")
        w("|---|---|---|---|---|")
        for m, s, h in detail:
            kind = ("test " if h.test else "") + h.kind
            if h.via:
                kind += f" (in `{h.via}`)"
            code = h.code.replace("|", "\\|").replace("`", "'")
            w(f"| `{m.key}` | {s.name} | {h.path}:{h.line} | {kind} | `{code}` |")
        w("")

    w("## Sources scanned")
    w("")
    w("| Source | Group | Python files | Hits |")
    w("|---|---|---|---|")
    for s in sources:
        n = sum(len(v) for v in s.hits.values())
        files = f"not scanned: {s.error}" if s.error else str(s.files)
        w(f"| {s.name} | {s.group} | {files} | {n} |")
    w("")

    w("## How to re-run")
    w("")
    w("```bash")
    w("# Clones the monorepo and each third-party plugin (depth 1) into a temp cache:")
    w("python3 scripts/plugin_api_usage.py --output docs/DEPRECATIONS_3.8.md")
    w("# Or scan a local monorepo checkout (read only) instead of cloning it:")
    w("python3 scripts/plugin_api_usage.py --monorepo ../ledmatrix-plugins")
    w("```")
    w("")
    w("Before removing a method in its release, re-run the scan against the current "
      "monorepo and registry: a plugin added since this file was generated may have "
      "started calling it. Remove only methods the fresh scan reports unused; move "
      "the rest to a later release (the test in `test/test_deprecation.py` fails "
      "while a marker names a release at or below `src.__version__`).")
    w("")
    return "\n".join(out)


def render_json(markers: List[Marker], sources: List[Source], meta: Dict[str, str]) -> str:
    data = {"meta": meta, "sources": [{"name": s.name, "group": s.group, "error": s.error}
                                      for s in sources], "methods": []}
    status = verdicts(markers, sources)
    for m in markers:
        st, text = status[m.key]
        data["methods"].append({
            "method": m.key, "module": m.module, "removal": m.removal,
            "alternative": m.alternative, "status": st, "verdict": text,
            "hits": [{"source": s.name, **h.__dict__} for s in sources
                     for h in s.hits.get(m.key, [])],
        })
    return json.dumps(data, indent=2)


# --------------------------------------------------------------------------


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--monorepo", type=Path,
                        help="local ledmatrix-plugins checkout to scan (read only); "
                             "default: shallow-clone its main branch")
    parser.add_argument("--registry", type=Path,
                        help="plugins.json to read third-party plugins from "
                             "(default: the monorepo's)")
    parser.add_argument("--cache-dir", type=Path,
                        default=Path(tempfile.gettempdir()) / "ledmatrix-plugin-api-usage",
                        help="where clones go (default: %(default)s)")
    parser.add_argument("--reuse-cache", action="store_true",
                        help="scan clones already in --cache-dir instead of re-cloning "
                             "(offline re-runs; the report may then be stale)")
    parser.add_argument("--no-third-party", action="store_true",
                        help="skip third-party plugins (the report then cannot prove anything unused)")
    parser.add_argument("--format", choices=("md", "json"), default="md")
    parser.add_argument("--output", type=Path, help="write the report here instead of stdout")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    markers = find_markers(REPO_ROOT)
    if not markers:
        print("No @deprecated markers found in src/.", file=sys.stderr)
        return 0

    sys.path.insert(0, str(REPO_ROOT))
    try:
        from src import __version__ as core_version
    except Exception:  # noqa: BLE001 -- reporting only
        core_version = "unknown"

    sources: List[Source] = []
    core = Source("core", "core", REPO_ROOT)
    scan_tree(core, [REPO_ROOT / d for d in CORE_DIRS] + sorted(REPO_ROOT.glob("*.py")),
              REPO_ROOT, markers, core=True)
    core_tests = Source("core tests", "core-tests", REPO_ROOT)
    scan_tree(core_tests, [REPO_ROOT / d for d in CORE_TEST_DIRS], REPO_ROOT, markers,
              core=True, test_override=True,
              definer_roots=[REPO_ROOT / d for d in CORE_DIRS])
    sources += [core, core_tests]

    # Monorepo
    if args.monorepo:
        mono = args.monorepo.resolve()
        mono_desc = f"local checkout `{mono.name}` ({_head(mono)})"
    else:
        mono = args.cache_dir / "ledmatrix-plugins"
        err = shallow_clone(MONOREPO_URL, "main", mono, args.reuse_cache)
        if err:
            print(f"Could not clone the monorepo: {err}", file=sys.stderr)
            return 1
        mono_desc = f"[ChuckBuilds/ledmatrix-plugins]({MONOREPO_URL}) ({_head(mono)})"
    plugins_dir = mono / "plugins"
    mono_dirs = sorted(p for p in plugins_dir.iterdir() if p.is_dir()) if plugins_dir.is_dir() else []
    for d in mono_dirs:
        s = Source(_plugin_id(d), "monorepo", d)
        scan_tree(s, [d], d, markers, core=False)
        sources.append(s)
    mono_desc += f", {len(mono_dirs)} plugins"

    # Third-party plugins from the registry
    registry = args.registry or (mono / "plugins.json")
    third: List[dict] = []
    try:
        reg = json.loads(registry.read_text(encoding="utf-8"))
        entries = reg["plugins"] if isinstance(reg, dict) else reg
        third = [e for e in entries if e.get("repo") and not _is_monorepo(e["repo"])]
    except (OSError, ValueError, KeyError) as exc:
        print(f"Could not read {registry}: {exc}", file=sys.stderr)
        return 1
    if args.no_third_party:
        tp_desc = "skipped (--no-third-party)"
    else:
        for e in third:
            dest = args.cache_dir / "third-party" / re.sub(r"[^\w.-]", "_", e["id"])
            err = shallow_clone(e["repo"], e.get("branch") or None, dest, args.reuse_cache)
            root = dest / e["plugin_path"] if e.get("plugin_path") else dest
            s = Source(e["id"], "third-party", root, error=err)
            if not err:
                scan_tree(s, [root], root, markers, core=False)
            sources.append(s)
        tp_desc = (f"{len(third)} with their own repo in `plugins.json` "
                   f"({', '.join(e['id'] for e in third)})")

    meta = {
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "core_version": core_version,
        "core_rev": _head(REPO_ROOT, branch=False),
        "monorepo": mono_desc,
        "third_party": tp_desc,
    }
    report = (render_json if args.format == "json" else render_markdown)(markers, sources, meta)
    if args.output:
        args.output.write_text(report, encoding="utf-8", newline="\n")
        print(f"Wrote {args.output}", file=sys.stderr)
    else:
        sys.stdout.write(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
