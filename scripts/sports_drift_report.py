#!/usr/bin/env python3
"""Report how far apart the nine scoreboards' copies of each method are.

The sports consolidation (docs/SPORTS_UNIFICATION.md) moves shared code from
the scoreboard plugins into ``src/common``. Byte-identical copies have mostly
been moved; what is left has drifted, and is promoted one *method family* at a
time by first making every copy identical ("reconcile, then promote"). This
report is the progress measure for that: for every method in the tracked
files it counts the copies and the distinct bodies among them, so a stage can
say "``_is_game_really_over``: 5 variants -> 1" instead of remembering it.

It reads a ledmatrix-plugins checkout and never fails a build: it is a report,
not a gate. The monorepo's own ``scripts/check_sports_drift.py`` is the gate
(it fails when a function that agrees across the plugins starts to differ).

Definitions
-----------
family
    One method name in one tracked file, across every class that defines it
    and every plugin. ``sports.py::update`` covers ``SportsLive.update``,
    ``SportsRecent.update`` and ``SportsUpcoming.update`` in all nine plugins.
    Module-level functions are families too.
copies
    How many definitions the family has (plugin x class).
plugins
    How many of the nine plugins define it at least once.
variants
    Distinct bodies among the copies, compared as ASTs with docstrings,
    comments, formatting, decorators and annotations ignored. A family is
    reconciled when every class in it is down to one variant.
per-class variants
    The same count within one class role (``SportsLive.update`` across the
    plugins). Class names are folded the way the plugins name them
    (``SoccerScoreboardPlugin`` and ``UFCScoreboardPlugin`` are both
    ``SScoreboardPlugin``), so manager.py lines up across sports.
folded
    Variants left after sport and league names are folded to a placeholder
    (``self.nfl_live`` == ``self.nhl_live``, ``"NFL"`` == ``"NHL"``). The gap
    between ``variants`` and ``folded`` is drift that is only naming.

Usage
-----
    python scripts/sports_drift_report.py --plugins ../ledmatrix-plugins
    python scripts/sports_drift_report.py --markdown        # for a CI summary
    python scripts/sports_drift_report.py --json out.json   # machine-readable
    python scripts/sports_drift_report.py --family sports.py::update

``--plugins`` defaults to ``$LEDMATRIX_PLUGINS`` (a checkout root or its
``plugins/`` directory, the same variable the core parity tests read). With no
checkout it says so and exits 0.
"""

from __future__ import annotations

import argparse
import ast
import collections
import difflib
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

#: The nine scoreboards the consolidation covers, by directory prefix.
SPORTS = ("afl", "baseball", "basketball", "football", "hockey", "lacrosse",
          "nrl", "soccer", "ufc")

#: Files every scoreboard carries a copy of. sports.py and game_renderer.py
#: are the consolidation's subject; manager.py (the BasePlugin host, the
#: largest copy of all) joined the plan with the reconcile-then-promote
#: method. ufc has no game_renderer.py (it draws fights in fight_renderer.py).
DEFAULT_FILES = ("sports.py", "manager.py", "game_renderer.py")

#: A family is "drifted" when it is widespread and has several bodies. The
#: defaults match the review that introduced this report (at least 7 plugins,
#: at least 3 variants).
DEFAULT_MIN_PLUGINS = 7
DEFAULT_MIN_VARIANTS = 3

#: Sport, league and competition names that legitimately differ between the
#: plugins. Only used for the ``folded`` column.
SPORT_TOKENS = (
    "afl", "nrl", "baseball", "basketball", "football", "hockey", "soccer",
    "lacrosse", "ufc", "mma", "mlb", "milb", "nhl", "nfl", "nba", "wnba",
    "ncaa", "ncaafb", "ncaam", "ncaaw", "ncaa_fb", "ncaa_baseball",
    "ncaa_basketball", "ncaam_hockey", "ncaaw_hockey", "ncaam_lacrosse",
    "ncaaw_lacrosse", "ncaam_basketball", "ncaaw_basketball", "epl",
    "uefa", "mls", "laliga", "bundesliga", "seriea", "ligue1",
)
_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9])(" + "|".join(sorted(SPORT_TOKENS, key=len, reverse=True))
    + r")(?![A-Za-z0-9])", re.IGNORECASE)
_TOKEN_SET = {t.lower() for t in SPORT_TOKENS}
_CAMEL_RE = re.compile(r"[A-Z]+(?![a-z])|[A-Z][a-z0-9]*|[a-z0-9]+|_")


def fold(name: str) -> str:
    """Replace sport and league names in an identifier or string with ``S``.

    Both spellings the plugins use: snake_case (``nfl_live`` -> ``S_live``)
    and CamelCase (``UFCScoreboardPlugin`` -> ``SScoreboardPlugin``).
    """
    name = _TOKEN_RE.sub("S", name)
    # sub, not findall + join: characters between words (spaces, dots,
    # braces in a log string) must survive, or distinct text folds together.
    return _CAMEL_RE.sub(
        lambda m: "S" if m.group(0).lower() in _TOKEN_SET else m.group(0), name)


def _strip_docstring(body: List[ast.stmt]) -> List[ast.stmt]:
    if (body and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)):
        return body[1:] or [ast.Pass()]
    return body


class _Canonical(ast.NodeTransformer):
    """Drop what is not behaviour: docstrings, decorators, annotations."""

    def _func(self, node):
        self.generic_visit(node)
        node.body = _strip_docstring(node.body)
        node.decorator_list = []
        node.returns = None
        return node

    visit_FunctionDef = _func
    visit_AsyncFunctionDef = _func

    def visit_ClassDef(self, node):
        self.generic_visit(node)
        node.body = _strip_docstring(node.body)
        return node

    def visit_arg(self, node):
        node.annotation = None
        return node

    def visit_AnnAssign(self, node):
        # ``x: T = v`` is ``x = v``; a bare ``x: T`` does nothing at runtime.
        self.generic_visit(node)
        if node.value is None:
            return None
        return ast.copy_location(
            ast.Assign(targets=[node.target], value=node.value), node)


class _Folded(_Canonical):
    """Canonical, plus sport names folded out of identifiers and strings."""

    def visit_Name(self, node):
        node.id = fold(node.id)
        return node

    def visit_Attribute(self, node):
        self.generic_visit(node)
        node.attr = fold(node.attr)
        return node

    def visit_arg(self, node):
        node = super().visit_arg(node)
        node.arg = fold(node.arg)
        return node

    def visit_keyword(self, node):
        self.generic_visit(node)
        if node.arg:
            node.arg = fold(node.arg)
        return node

    def visit_Constant(self, node):
        if isinstance(node.value, str):
            node.value = fold(node.value)
        return node

    def _func(self, node):
        node = super()._func(node)
        node.name = fold(node.name)
        return node

    visit_FunctionDef = _func
    visit_AsyncFunctionDef = _func


def _digest(node: ast.AST, transformer: ast.NodeTransformer) -> str:
    # Re-parse a copy so the transformers never mutate the tree being walked.
    clone = ast.parse(ast.unparse(node)).body[0]
    clone = transformer.visit(clone)
    # The function's own name is the family key, not part of its body.
    if isinstance(clone, (ast.FunctionDef, ast.AsyncFunctionDef)):
        clone.name = "_"
    return hashlib.sha256(ast.dump(clone).encode()).hexdigest()[:12]


class Copy:
    """One definition of a method (or module-level function) in one plugin."""

    __slots__ = ("plugin", "cls", "name", "lines", "exact", "folded", "source")

    def __init__(self, plugin, cls, name, lines, exact, folded, source=""):
        self.plugin = plugin
        self.cls = cls
        self.name = name
        self.lines = lines
        self.exact = exact
        self.folded = folded
        self.source = source


def collect_file(path: Path, plugin: str) -> List[Copy]:
    """Every top-level function and class method in one file."""
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        print(f"  ! {path}: {exc}", file=sys.stderr)
        return []
    out = []

    def add(node, cls):
        out.append(Copy(plugin, cls, node.name,
                        node.end_lineno - node.lineno + 1,
                        _digest(node, _Canonical()), _digest(node, _Folded()),
                        ast.get_source_segment(text, node) or ""))

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            add(node, "<module>")
        elif isinstance(node, ast.ClassDef):
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    add(child, fold(node.name))
    return out


def resolve_plugins_dir(raw: Optional[str]) -> Optional[Path]:
    """A checkout root or its plugins/ directory; None when there is neither."""
    if not raw:
        return None
    root = Path(raw)
    if (root / "plugins").is_dir():
        root = root / "plugins"
    if not any((root / f"{s}-scoreboard").is_dir() for s in SPORTS):
        return None
    return root


def build(plugins_dir: Path, files: Iterable[str]) -> Dict[Tuple[str, str], List[Copy]]:
    """{(file, method name): [Copy, ...]} across the nine scoreboards."""
    families: Dict[Tuple[str, str], List[Copy]] = collections.defaultdict(list)
    for fname in files:
        for sport in SPORTS:
            path = plugins_dir / f"{sport}-scoreboard" / fname
            if path.is_file():
                for copy in collect_file(path, sport):
                    families[(fname, copy.name)].append(copy)
    return families


def summarise(key: Tuple[str, str], copies: List[Copy]) -> dict:
    """The numbers for one family."""
    per_class = collections.defaultdict(list)
    for c in copies:
        per_class[c.cls].append(c)
    classes = []
    for cls, members in sorted(per_class.items()):
        groups = collections.defaultdict(list)
        for c in members:
            groups[c.exact].append(c.plugin)
        classes.append({
            "class": cls,
            "copies": len(members),
            "variants": len(groups),
            "folded": len({c.folded for c in members}),
            "groups": sorted((sorted(p) for p in groups.values()),
                             key=lambda g: (-len(g), g)),
        })
    total_lines = sum(c.lines for c in copies)
    # What promotion would remove: every copy but one per class role.
    one_each = sum(max(c.lines for c in members) for members in per_class.values())
    return {
        "file": key[0],
        "family": key[1],
        "plugins": len({c.plugin for c in copies}),
        "copies": len(copies),
        "variants": len({(c.cls, c.exact) for c in copies}),
        "folded": len({(c.cls, c.folded) for c in copies}),
        "worst_class_variants": max(k["variants"] for k in classes),
        "lines": total_lines,
        "duplicated_lines": total_lines - one_each,
        "classes": classes,
    }


def report(families, min_plugins: int, min_variants: int) -> dict:
    rows = [summarise(k, v) for k, v in families.items()]
    by_file = collections.defaultdict(list)
    for r in rows:
        by_file[r["file"]].append(r)
    files = {}
    for fname, frows in sorted(by_file.items()):
        files[fname] = {
            "families": len(frows),
            "in_all_plugins": sum(1 for r in frows if r["plugins"] == len(SPORTS)),
            "lines": sum(r["lines"] for r in frows),
            "identical_duplicated_lines": sum(
                r["duplicated_lines"] for r in frows if r["worst_class_variants"] == 1),
        }
    drifted = sorted(
        (r for r in rows
         if r["plugins"] >= min_plugins and r["variants"] >= min_variants),
        key=lambda r: (-r["variants"], -r["lines"], r["file"], r["family"]))
    identical = sorted(
        (r for r in rows if r["copies"] >= 2 and r["worst_class_variants"] == 1),
        key=lambda r: (-r["duplicated_lines"], r["file"], r["family"]))
    # One body shared by every plugin but one: the cheapest reconciliations.
    # "One" across the whole family: a class role whose odd one out is a
    # different plugin from another role's is two outliers, not one.
    one_outlier = sorted(
        (r for r in rows
         if r["plugins"] >= min_plugins and r["worst_class_variants"] == 2
         and all(len(k["groups"]) < 2 or len(k["groups"][1]) == 1
                 for k in r["classes"])
         and len(_minorities(r)) == 1),
        key=lambda r: (-r["duplicated_lines"], r["file"], r["family"]))
    return {"files": files, "drifted": drifted, "identical": identical,
            "one_outlier": one_outlier,
            "rows": rows, "thresholds": {"min_plugins": min_plugins,
                                         "min_variants": min_variants}}


def _minorities(r) -> set:
    """Every plugin in a minority body, across the family's class roles."""
    return {p for k in r["classes"] for g in k["groups"][1:] for p in g}


def _outlier(r) -> str:
    """The plugin whose body differs, for a one-outlier family."""
    return ", ".join(sorted(_minorities(r)))


def _text(rep, top_identical: int) -> str:
    out = []
    out.append("Per file (all methods and module functions):")
    for fname, f in rep["files"].items():
        out.append(f"  {fname:<18} {f['families']:>4} families, "
                   f"{f['in_all_plugins']:>3} in all {len(SPORTS)} plugins, "
                   f"{f['lines']:>6} lines; identical copies beyond the first: "
                   f"{f['identical_duplicated_lines']} lines")
    t = rep["thresholds"]
    out.append("")
    out.append(f"Drifted families (in >= {t['min_plugins']} plugins, "
               f">= {t['min_variants']} variants): {len(rep['drifted'])}")
    out.append(f"  {'file::family':<58} {'plug':>4} {'copies':>6} {'var':>4} "
               f"{'fold':>4} {'worst':>5} {'lines':>6}")
    for r in rep["drifted"]:
        name = f"{r['file']}::{r['family']}"
        out.append(f"  {name:<58} {r['plugins']:>4} {r['copies']:>6} "
                   f"{r['variants']:>4} {r['folded']:>4} "
                   f"{r['worst_class_variants']:>5} {r['lines']:>6}")
    out.append("")
    out.append(f"One outlier (in >= {t['min_plugins']} plugins, every plugin but "
               f"one agrees): {len(rep['one_outlier'])}")
    for r in rep["one_outlier"]:
        name = f"{r['file']}::{r['family']}"
        out.append(f"  {name:<58} {r['plugins']:>4} plugins, differs in "
                   f"{_outlier(r)}; {r['lines']} lines")
    out.append("")
    out.append(f"Identical in every copy (promote as-is), top {top_identical} "
               f"by duplicated lines, of {len(rep['identical'])}:")
    for r in rep["identical"][:top_identical]:
        name = f"{r['file']}::{r['family']}"
        out.append(f"  {name:<58} {r['plugins']:>4} plugins "
                   f"{r['duplicated_lines']:>5} duplicated lines")
    return "\n".join(out)


def _markdown(rep, top_identical: int, source: str) -> str:
    t = rep["thresholds"]
    out = ["## Sports drift report", "",
           f"Scoreboard copies read from `{source}`. Report only: this never fails "
           "the build. See docs/SPORTS_UNIFICATION.md.", "",
           "| File | Families | In all 9 | Lines | Identical duplicated lines |",
           "|---|---:|---:|---:|---:|"]
    for fname, f in rep["files"].items():
        out.append(f"| `{fname}` | {f['families']} | {f['in_all_plugins']} | "
                   f"{f['lines']} | {f['identical_duplicated_lines']} |")
    out += ["", f"### Drifted families (in >= {t['min_plugins']} plugins, "
            f">= {t['min_variants']} variants): {len(rep['drifted'])}", "",
            "| Family | Plugins | Copies | Variants | Folded | Worst class | Lines |",
            "|---|---:|---:|---:|---:|---:|---:|"]
    for r in rep["drifted"]:
        out.append(f"| `{r['file']}::{r['family']}` | {r['plugins']} | {r['copies']} | "
                   f"{r['variants']} | {r['folded']} | {r['worst_class_variants']} | "
                   f"{r['lines']} |")
    out += ["", f"### One outlier (every plugin but one agrees): "
            f"{len(rep['one_outlier'])}", "",
            "| Family | Plugins | Differs in | Lines |", "|---|---:|---|---:|"]
    for r in rep["one_outlier"]:
        out.append(f"| `{r['file']}::{r['family']}` | {r['plugins']} | "
                   f"{_outlier(r)} | {r['lines']} |")
    out += ["", f"### Identical in every copy: {len(rep['identical'])} "
            f"(top {top_identical} by duplicated lines)", "",
            "| Family | Plugins | Duplicated lines |", "|---|---:|---:|"]
    for r in rep["identical"][:top_identical]:
        out.append(f"| `{r['file']}::{r['family']}` | {r['plugins']} | "
                   f"{r['duplicated_lines']} |")
    return "\n".join(out) + "\n"


def _family_detail(rep, families, wanted: str, show_diff: bool) -> str:
    """Which plugins share each body of one family; optionally the diffs.

    The diff is against the body most plugins share (the first group), which
    is where a reconciliation usually starts.
    """
    fname, _, family = wanted.partition("::")
    for r in rep["rows"]:
        if r["file"] == fname and r["family"] == family:
            out = [f"{wanted}: {r['plugins']} plugins, {r['copies']} copies, "
                   f"{r['variants']} variants ({r['folded']} after folding sport "
                   f"names), {r['lines']} lines"]
            copies = families[(fname, family)]
            for k in r["classes"]:
                out.append(f"  {k['class']}: {k['variants']} variant(s) "
                           f"({k['folded']} folded)")
                for g in k["groups"]:
                    out.append(f"      {', '.join(g)}")
                if not show_diff or len(k["groups"]) < 2:
                    continue
                by_plugin = {c.plugin: c for c in copies if c.cls == k["class"]}
                base = by_plugin[k["groups"][0][0]]
                for g in k["groups"][1:]:
                    other = by_plugin[g[0]]
                    out.extend(difflib.unified_diff(
                        base.source.splitlines(), other.source.splitlines(),
                        f"{base.plugin}-scoreboard/{fname}",
                        f"{other.plugin}-scoreboard/{fname}", lineterm="", n=2))
            return "\n".join(out)
    return f"{wanted}: no such family"


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--plugins", default=os.environ.get("LEDMATRIX_PLUGINS"),
                    help="ledmatrix-plugins checkout (default: $LEDMATRIX_PLUGINS)")
    ap.add_argument("--files", default=",".join(DEFAULT_FILES),
                    help="comma-separated files to compare (default: %(default)s)")
    ap.add_argument("--min-plugins", type=int, default=DEFAULT_MIN_PLUGINS)
    ap.add_argument("--min-variants", type=int, default=DEFAULT_MIN_VARIANTS)
    ap.add_argument("--top-identical", type=int, default=15)
    ap.add_argument("--markdown", action="store_true",
                    help="print a Markdown summary (for $GITHUB_STEP_SUMMARY)")
    ap.add_argument("--json", metavar="PATH",
                    help="also write the full report as JSON")
    ap.add_argument("--family", action="append", default=[],
                    help="show which plugins share each body, e.g. sports.py::update")
    ap.add_argument("--diff", action="store_true",
                    help="with --family, also diff each variant against the most common one")
    args = ap.parse_args(argv)

    plugins_dir = resolve_plugins_dir(args.plugins)
    if plugins_dir is None:
        msg = ("No ledmatrix-plugins checkout: pass --plugins or set "
               "LEDMATRIX_PLUGINS. Nothing to report.")
        print(f"_{msg}_\n" if args.markdown else msg)
        return 0

    files = [f.strip() for f in args.files.split(",") if f.strip()]
    families = build(plugins_dir, files)
    rep = report(families, args.min_plugins, args.min_variants)

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(rep, fh, indent=2)
            fh.write("\n")
    if args.markdown:
        print(_markdown(rep, args.top_identical, str(plugins_dir)), end="")
    else:
        print(_text(rep, args.top_identical))
    for wanted in args.family:
        print()
        print(_family_detail(rep, families, wanted, args.diff))
    return 0


if __name__ == "__main__":
    sys.exit(main())
