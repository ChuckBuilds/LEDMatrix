#!/usr/bin/env python3
"""
LEDMatrix Plugin Security Auditor

Performs AST-based security analysis of all Python files in plugin directories.
Designed to run in CI — exits non-zero on CRITICAL findings only.

Usage:
    python scripts/audit_plugins.py
    python scripts/audit_plugins.py --verbose
    python scripts/audit_plugins.py --plugin hello-world
    python scripts/audit_plugins.py --output results.json
"""

import ast
import argparse
import json
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from datetime import datetime, timezone

PROJECT_ROOT = Path(__file__).resolve().parent.parent

PLUGIN_BASE_DIRS = [
    PROJECT_ROOT / "plugins",
    PROJECT_ROOT / "plugin-repos",
]


# ─────────────────────────────────────────────────────────────────────────────
# Finding dataclass
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Finding:
    plugin_id: str
    file: str
    line: int
    severity: str   # CRITICAL | WARNING | INFO
    rule: str
    message: str

    def to_dict(self) -> dict:
        return asdict(self)


# ─────────────────────────────────────────────────────────────────────────────
# AST visitor
# ─────────────────────────────────────────────────────────────────────────────

class _PluginVisitor(ast.NodeVisitor):
    """Collect security findings from a single plugin Python file."""

    def __init__(self, filepath: Path, plugin_id: str):
        self.filepath = filepath
        self.plugin_id = plugin_id
        self.findings: list[Finding] = []
        # Local name -> real dotted path, so aliased imports and from-imports
        # of dangerous APIs (import subprocess as sp; from builtins import
        # eval as e) are still recognized in visit_Call below.
        self._aliases: dict[str, str] = {}

    def _add(self, node: ast.AST, severity: str, rule: str, message: str) -> None:
        self.findings.append(Finding(
            plugin_id=self.plugin_id,
            file=str(self.filepath.relative_to(PROJECT_ROOT)),
            line=getattr(node, "lineno", 0),
            severity=severity,
            rule=rule,
            message=message,
        ))

    def _resolve(self, local_name: str) -> str:
        """Resolve a local name through recorded import aliases to its real
        dotted path (e.g. "sp" -> "subprocess"); unresolved names pass through
        unchanged."""
        return self._aliases.get(local_name, local_name)

    def _resolve_call_target(self, func: ast.expr) -> str | None:
        """Resolve a Call's func node to a fully-qualified dotted target,
        covering a direct name (bare builtin, aliased import, or
        from-import: from builtins import eval as e; from subprocess
        import run; from os import system as s) and module-attribute
        access (subprocess.run, sp.run, os.system, o.system) uniformly.
        Returns None for call shapes this doesn't attempt to resolve."""
        if isinstance(func, ast.Name):
            return self._resolve(func.id)
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            base = self._resolve(func.value.id)
            return f"{base}.{func.attr}"
        return None

    def visit_Call(self, node: ast.Call) -> None:
        target = self._resolve_call_target(node.func)
        if target is None:
            self.generic_visit(node)
            return

        leaf = target.rsplit(".", 1)[-1]

        # eval() / exec() / compile() — arbitrary code execution, whether a
        # bare call, an aliased import, or a from-import
        # (from builtins import eval as e; e(...))
        if leaf == "eval":
            self._add(node, "CRITICAL", "PLUGIN-001",
                      "eval() call — arbitrary code execution risk")
        elif leaf == "exec":
            self._add(node, "CRITICAL", "PLUGIN-002",
                      "exec() call — arbitrary code execution risk")
        elif leaf == "compile":
            self._add(node, "WARNING", "PLUGIN-003",
                      "compile() call — dynamic code compilation")

        # subprocess.*(shell=True), whether subprocess.run(...), sp.run(...),
        # or a from-import (from subprocess import run; run(..., shell=True))
        if target in {
            "subprocess.run", "subprocess.call", "subprocess.Popen",
            "subprocess.check_call", "subprocess.check_output",
        }:
            for kw in node.keywords:
                if (kw.arg == "shell" and
                        isinstance(kw.value, ast.Constant) and
                        kw.value.value is True):
                    self._add(node, "WARNING", "PLUGIN-004",
                              f"subprocess.{leaf}(shell=True) — "
                              f"shell injection risk if args include user input")

        # os.system(), whether os.system(...), o.system(...), or a
        # from-import (from os import system as s; s(...))
        if target == "os.system":
            self._add(node, "WARNING", "PLUGIN-005",
                      "os.system() call — prefer subprocess with list args")

        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.asname:
                local, real = alias.asname, alias.name
            else:
                # `import os.path` binds the top-level name `os`, not `os.path`
                local = real = alias.name.split(".")[0]
            self._aliases[local] = real
            self._check_import(node, alias.name)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            for alias in node.names:
                local = alias.asname or alias.name
                self._aliases[local] = f"{node.module}.{alias.name}"
            self._check_import(node, node.module)
        self.generic_visit(node)

    def _check_import(self, node: ast.AST, module_name: str) -> None:
        dangerous = {
            "ctypes": ("WARNING", "PLUGIN-010", "ctypes import — native code execution"),
            "cffi": ("WARNING", "PLUGIN-011", "cffi import — native code execution"),
            "pickle": ("WARNING", "PLUGIN-012",
                       "pickle import — deserialization can execute arbitrary code"),
            "marshal": ("WARNING", "PLUGIN-013",
                        "marshal import — deserialization risk"),
        }
        for mod, (severity, rule, msg) in dangerous.items():
            if module_name == mod or module_name.startswith(mod + "."):
                self._add(node, severity, rule, msg)


# ─────────────────────────────────────────────────────────────────────────────
# Per-plugin audit
# ─────────────────────────────────────────────────────────────────────────────

def audit_plugin(plugin_dir: Path) -> list[Finding]:
    """Audit a single plugin directory. Returns all findings."""
    findings: list[Finding] = []
    plugin_id = plugin_dir.name

    # Check for required files
    for required_file, rule, msg in [
        ("manifest.json", "PLUGIN-020",
         "manifest.json missing — plugin may be incomplete"),
        ("config_schema.json", "PLUGIN-021",
         "config_schema.json missing — no input validation schema declared"),
    ]:
        if not (plugin_dir / required_file).exists():
            findings.append(Finding(
                plugin_id=plugin_id,
                file=str((plugin_dir / required_file).relative_to(PROJECT_ROOT)),
                line=0,
                severity="WARNING",
                rule=rule,
                message=msg,
            ))

    # AST analysis of all Python files
    for py_file in sorted(plugin_dir.rglob("*.py")):
        try:
            source = py_file.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(py_file))
            visitor = _PluginVisitor(py_file, plugin_id)
            visitor.visit(tree)
            findings.extend(visitor.findings)
        except SyntaxError as exc:
            # A file the visitor can't even parse is a file we can't verify
            # is safe -- this must block the audit, not just warn.
            findings.append(Finding(
                plugin_id=plugin_id,
                file=str(py_file.relative_to(PROJECT_ROOT)),
                line=getattr(exc, "lineno", 0) or 0,
                severity="CRITICAL",
                rule="PLUGIN-030",
                message=f"Python syntax error — cannot be parsed: {exc}",
            ))
        except OSError as exc:
            # Same reasoning as SyntaxError: an unreadable file was never
            # actually scanned, so it must block rather than pass silently.
            findings.append(Finding(
                plugin_id=plugin_id,
                file=str(py_file.relative_to(PROJECT_ROOT)),
                line=0,
                severity="CRITICAL",
                rule="PLUGIN-031",
                message=f"Could not read file: {exc}",
            ))

    return findings


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="LEDMatrix plugin security auditor",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--plugin", "-p", default=None,
                        help="Audit a specific plugin ID only")
    parser.add_argument("--output", "-o", default=None,
                        help="Write JSON results to this file")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Show all findings, not just summary")
    args = parser.parse_args()

    print("=" * 60)
    print("LEDMatrix Plugin Security Audit")
    print(f"Project root: {PROJECT_ROOT}")
    print("=" * 60)

    all_findings: list[Finding] = []
    plugins_scanned = 0
    plugin_found = args.plugin is None

    for base_dir in PLUGIN_BASE_DIRS:
        if not base_dir.exists():
            if args.verbose:
                print(f"  ⏭️  Skipping {base_dir.name}/ (directory not found)")
            continue

        base_label = base_dir.relative_to(PROJECT_ROOT)
        print(f"\n  Scanning {base_label}/")

        for plugin_dir in sorted(base_dir.iterdir()):
            if not plugin_dir.is_dir():
                continue
            if plugin_dir.name.startswith((".", "_")):
                continue
            if args.plugin and plugin_dir.name != args.plugin:
                continue
            if args.plugin:
                plugin_found = True

            findings = audit_plugin(plugin_dir)
            all_findings.extend(findings)
            plugins_scanned += 1

            critical = [f for f in findings if f.severity == "CRITICAL"]
            warnings = [f for f in findings if f.severity == "WARNING"]

            if critical:
                icon, label = "🚨", "CRITICAL"
            elif warnings:
                icon, label = "⚠️ ", "WARN   "
            else:
                icon, label = "✅", "PASS   "

            print(f"    {icon} [{label}] {plugin_dir.name}"
                  f" — {len(critical)} critical, {len(warnings)} warnings")

            if args.verbose:
                for f in findings:
                    severity_icon = {"CRITICAL": "🚨", "WARNING": "⚠️ ", "INFO": "ℹ️ "}.get(
                        f.severity, "  "
                    )
                    print(f"           {severity_icon} {f.rule} {f.file}:{f.line} — {f.message}")

    if args.plugin and not plugin_found:
        print(f"\n  🚨 Plugin '{args.plugin}' not found in any of "
              f"{[str(d.relative_to(PROJECT_ROOT)) for d in PLUGIN_BASE_DIRS]} — "
              f"nothing was audited")
        return 1

    # Summary
    critical_findings = [f for f in all_findings if f.severity == "CRITICAL"]
    warning_findings = [f for f in all_findings if f.severity == "WARNING"]

    print(f"\n{'=' * 60}")
    print(f"  Plugins scanned : {plugins_scanned}")
    print(f"  CRITICAL        : {len(critical_findings)}")
    print(f"  WARNING         : {len(warning_findings)}")

    if critical_findings:
        print("\n  🚨 CRITICAL findings:")
        for f in critical_findings:
            print(f"    {f.plugin_id} | {Path(f.file).name}:{f.line} | {f.message}")

    # Write JSON output
    if args.output:
        output_data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "plugins_scanned": plugins_scanned,
            "summary": {
                "critical": len(critical_findings),
                "warnings": len(warning_findings),
            },
            "findings": [f.to_dict() for f in all_findings],
        }
        Path(args.output).write_text(
            json.dumps(output_data, indent=2), encoding="utf-8"
        )
        print(f"\n  Results written to: {args.output}")

    if critical_findings:
        print("\n  🚨 Blocking — CRITICAL issues must be resolved")
        return 1

    print("\n  ✅ No critical issues found")
    return 0


if __name__ == "__main__":
    sys.exit(main())
