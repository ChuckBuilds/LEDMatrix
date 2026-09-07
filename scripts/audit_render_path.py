#!/usr/bin/env python3
"""Find blocking work reachable from a plugin's render path.

`display()` runs on the render thread. Anything slow reached from it stalls the
panel for its whole duration, and on a vsync-paced loop that is immediately
visible: a single 15ms call on a 100Hz panel drops a frame, and a network round
trip freezes the marquee outright.

This has bitten twice already. odds-ticker called `_has_live_games()` every
frame, whose slow path read the scoreboard cache from disk and parsed JSON per
enabled league -- one stalled frame every few minutes. soccer-scoreboard timed
out inside `update()` during a cache refresh. Both were found by staring at
frame-time histograms, which is a slow way to find a bug that is visible in the
source.

The audit walks the call graph from `display()` through same-class `self.*`
methods and reports anything that reaches a known-blocking API. It is a
heuristic, not a proof: it cannot see through indirection, and a hit is not
automatically a bug -- a call guarded by an interval check may be fine. It is a
list of places worth a human look.

    python3 scripts/audit_render_path.py                    # all plugins
    python3 scripts/audit_render_path.py --dir plugin-repos # a specific tree
    python3 scripts/audit_render_path.py --plugin odds-ticker
"""
from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

#: Calls that can block for longer than a frame. Matched on the attribute or
#: function name, so `requests.get`, `self.session.get` and a bare `get` on a
#: requests-ish object all register.
BLOCKING = {
    "get": "network or cache read",
    "post": "network",
    "put": "network",
    "request": "network",
    "urlopen": "network",
    "read": "I/O",
    "open": "file I/O",
    "load": "JSON/file parse",
    "loads": "JSON parse",
    "dump": "file write",
    "dumps": "serialise",
    "sleep": "sleep on the render thread",
    "run": "subprocess",
    "check_output": "subprocess",
    "connect": "network",
    "download_logo": "network",
    "_fetch": "fetch",
}

#: Names that make a hit far more likely to matter.
HIGH_SIGNAL = ("requests", "urllib", "session", "cache_manager", "subprocess",
               "socket", "http")

RENDER_ENTRY = "display"


class Analyzer:
    def __init__(self, tree: ast.AST):
        self.methods: dict[str, ast.FunctionDef] = {}
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.methods.setdefault(node.name, node)

    def calls_in(self, fn: ast.AST):
        """(self-method names called, blocking hits) inside one function."""
        self_calls, hits = set(), []
        for node in ast.walk(fn):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute):
                name = func.attr
                base = ast.unparse(func.value) if hasattr(ast, "unparse") else ""
                if base == "self" and name in self.methods:
                    self_calls.add(name)
                    continue
                if name in BLOCKING:
                    hits.append((name, base, BLOCKING[name], node.lineno))
            elif isinstance(func, ast.Name) and func.id in BLOCKING:
                hits.append((func.id, "", BLOCKING[func.id], node.lineno))
        return self_calls, hits

    def reachable_from(self, entry: str, max_depth: int = 3):
        """Blocking hits reachable from `entry`, with the path that reaches them."""
        if entry not in self.methods:
            return []
        found, seen = [], set()
        stack = [(entry, [entry], 0)]
        while stack:
            name, path, depth = stack.pop()
            if name in seen or depth > max_depth:
                continue
            seen.add(name)
            self_calls, hits = self.calls_in(self.methods[name])
            for hit in hits:
                found.append((path, hit))
            for callee in sorted(self_calls):
                stack.append((callee, path + [callee], depth + 1))
        return found


def audit_file(path: Path):
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return []
    analyzer = Analyzer(tree)
    return analyzer.reachable_from(RENDER_ENTRY)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    root = Path(__file__).resolve().parent.parent
    ap.add_argument("--dir", default=str(root / "plugin-repos"))
    ap.add_argument("--plugin", help="only this plugin directory")
    ap.add_argument("--all-hits", action="store_true",
                    help="include low-signal hits (open/read/load on locals)")
    args = ap.parse_args()

    base = Path(args.dir)
    if not base.is_dir():
        sys.exit("not a directory: %s" % base)

    plugins = [base / args.plugin] if args.plugin else sorted(
        d for d in base.iterdir() if d.is_dir())

    total = 0
    for plugin in plugins:
        rows = []
        for src in sorted(plugin.glob("*.py")):
            if src.name.startswith("test_"):
                continue
            for path, (name, s_base, why, lineno) in audit_file(src):
                signal = any(h in s_base.lower() for h in HIGH_SIGNAL)
                if not signal and not args.all_hits:
                    continue
                rows.append((src.name, lineno, "->".join(path),
                             ("%s.%s" % (s_base, name)) if s_base else name, why))
        if rows:
            total += len(rows)
            print("\n%s" % plugin.name)
            for fname, lineno, chain, call, why in sorted(rows):
                print("  %s:%-5d %-34s via %s" % (fname, lineno, call + "  (" + why + ")", chain))

    print("\n%d blocking call(s) reachable from display() across %d plugin(s)"
          % (total, len(plugins)))
    print("Heuristic: a hit guarded by an interval check may be fine. Look, do "
          "not assume.")


if __name__ == "__main__":
    main()
