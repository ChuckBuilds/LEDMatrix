#!/usr/bin/env python3
"""
Security Report Generator

Aggregates JSON output from all CI security audit jobs into a single
Markdown report suitable for PR comments and artifact storage.

Expected artifact layout (from actions/download-artifact@v4):
    <artifact-dir>/
        sast-results/
            bandit-results.json
            semgrep-results.json
        dependency-audit-results/
            pip-audit-results.json
            safety-results.json
        secrets-scan-results/
            gitleaks-results.json
        security-proofs-results/
            security-proofs-results.json
        plugin-audit-results/
            plugin-audit-results.json

Usage:
    python scripts/generate_report.py --artifact-dir audit-artifacts/ --output report.md
    python scripts/generate_report.py --artifact-dir audit-artifacts/ --output report.md --verbose
"""

import argparse
import json
import sys
from pathlib import Path
from datetime import datetime, timezone

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Gitleaks matches exactly equal to one of these (not a substring match -- a
# real secret that merely contains one of these words as part of its actual
# value must still be reported) are known template placeholders.
_GITLEAKS_SUPPRESS_EXACT_VALUES = {
    "YOUR_YOUTUBE_API_KEY",
    "YOUR_YOUTUBE_CHANNEL_ID",
    "YOUR_GITHUB_PERSONAL_ACCESS_TOKEN",
}

# Findings in these files are suppressed regardless of value -- they are
# template/example files that are expected to only ever contain placeholders.
_GITLEAKS_SUPPRESS_PATHS = [
    "config_secrets.template.json",
    "config.template.json",
]


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _load(path: Path) -> tuple[dict | list | None, str | None]:
    """Load a JSON artifact file.

    Returns (data, error): error is None on success (data is whatever was
    parsed, which may legitimately be an empty list/dict for a clean scan);
    otherwise error is a human-readable reason the artifact is unavailable,
    distinguishing "missing/malformed artifact" from "valid empty result" so
    callers don't silently treat a broken CI job as a clean pass.
    """
    if not path.exists():
        return None, f"artifact not found: {path}"
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except (json.JSONDecodeError, OSError) as exc:
        return None, f"could not read/parse {path}: {exc}"


def _md_sanitize_cell(value) -> str:
    """Escape/normalize a value so scanner-controlled content (a matched
    secret, a bandit issue_text, a file path) can't alter the Markdown
    table's structure: pipes would add bogus columns, newlines would break
    out of the row (or forge a fake header/separator line)."""
    text = str(value)
    text = text.replace("\\", "\\\\").replace("|", "\\|")
    text = text.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    return text


def _md_table_row(*cells: str) -> str:
    return "| " + " | ".join(_md_sanitize_cell(c) for c in cells) + " |"


# ─────────────────────────────────────────────────────────────────────────────
# Per-tool summarizers
# Returns: (markdown_lines: list[str], critical_count: int, available: bool)
# `available=False` means the artifact was missing or malformed -- distinct
# from a valid scan that simply found nothing -- so the caller can report
# INCOMPLETE instead of silently counting it as a clean pass.
# ─────────────────────────────────────────────────────────────────────────────

def _summarize_bandit(artifact_dir: Path) -> tuple[list[str], int, bool]:
    data, error = _load(artifact_dir / "sast-results" / "bandit-results.json")
    if error:
        return [f"_bandit results unavailable: {error}_"], 0, False

    results = data.get("results", [])
    high = [r for r in results if r.get("issue_severity") == "HIGH"]
    medium = [r for r in results if r.get("issue_severity") == "MEDIUM"]
    low = [r for r in results if r.get("issue_severity") == "LOW"]

    lines = [
        f"**Bandit**: {len(high)} HIGH · {len(medium)} MEDIUM · {len(low)} LOW"
    ]

    if high:
        lines += [
            "",
            "| Severity | File | Line | Issue |",
            "| --- | --- | --- | --- |",
        ]
        for r in high[:10]:
            fname = Path(r.get("filename", "")).name
            lines.append(_md_table_row(
                "HIGH", f"`{fname}`",
                str(r.get("line_number", "?")),
                r.get("issue_text", "")
            ))
        if len(high) > 10:
            lines.append(f"_… and {len(high) - 10} more HIGH findings_")

    return lines, len(high), True


def _summarize_pip_audit(artifact_dir: Path) -> tuple[list[str], int, bool]:
    data, error = _load(artifact_dir / "dependency-audit-results" / "pip-audit-results.json")
    if error:
        return [f"_pip-audit results unavailable: {error}_"], 0, False

    # pip-audit JSON format: {"dependencies": [{"name": ..., "vulns": [...]}]}
    vulns: list[dict] = []
    for dep in data.get("dependencies", []):
        for v in dep.get("vulns", []):
            vulns.append({"package": dep.get("name", "?"), **v})

    lines = [f"**pip-audit**: {len(vulns)} vulnerabilities found"]

    if vulns:
        lines += ["", "| Package | ID | Fix |", "| --- | --- | --- |"]
        for v in vulns[:10]:
            fix = v.get("fix_versions", ["none"])
            fix_str = ", ".join(fix) if fix else "none"
            lines.append(_md_table_row(
                v.get("package", "?"),
                v.get("id", "?"),
                fix_str,
            ))

    # Treat known vulnerabilities as warnings, not critical (they may be unavoidable)
    return lines, 0, True


def _summarize_gitleaks(artifact_dir: Path) -> tuple[list[str], int, bool]:
    data, error = _load(artifact_dir / "secrets-scan-results" / "gitleaks-results.json")
    if error:
        return [f"_gitleaks results unavailable: {error}_"], 0, False

    if not isinstance(data, list):
        data = []

    real_findings = []
    suppressed = 0
    for finding in data:
        secret_val = str(finding.get("Secret", "") or finding.get("Match", ""))
        file_name = Path(finding.get("File", "")).name
        if (secret_val in _GITLEAKS_SUPPRESS_EXACT_VALUES
                or file_name in _GITLEAKS_SUPPRESS_PATHS):
            suppressed += 1
        else:
            real_findings.append(finding)

    lines = [
        f"**Gitleaks**: {len(real_findings)} finding(s) "
        f"({suppressed} suppressed as template placeholders)"
    ]

    if real_findings:
        lines += ["", "| Rule | File | Line | Description |", "| --- | --- | --- | --- |"]
        for f in real_findings[:10]:
            fname = Path(f.get("File", "")).name
            lines.append(_md_table_row(
                f.get("RuleID", "?"),
                f"`{fname}`",
                str(f.get("StartLine", "?")),
                f.get("Description", ""),
            ))

    critical = len(real_findings)  # any real secret is critical
    return lines, critical, True


def _summarize_security_proofs(artifact_dir: Path) -> tuple[list[str], int, bool]:
    data, error = _load(artifact_dir / "security-proofs-results" / "security-proofs-results.json")
    if error:
        return [f"_security proofs results unavailable: {error}_"], 0, False

    if not isinstance(data, list):
        data = []

    critical = [r for r in data if r.get("severity") == "CRITICAL"]
    warnings = [r for r in data if r.get("severity") == "WARNING"]
    passed = [r for r in data if r.get("severity") == "PASS"]
    skipped = [r for r in data if r.get("severity") == "SKIP"]

    lines = [
        f"**Security Proofs**: "
        f"{len(passed)} PASS · {len(warnings)} WARN · "
        f"{len(critical)} CRITICAL · {len(skipped)} SKIP",
        "",
    ]

    _icon = {"PASS": "✅", "INFO": "ℹ️", "WARNING": "⚠️",  # nosec B105 - severity labels, not credentials
             "CRITICAL": "🚨", "SKIP": "⏭️"}
    for r in data:
        icon = _icon.get(r.get("severity", ""), "❓")
        lines.append(
            f"- {icon} **{r.get('test_id', '?')}**: {r.get('message', '')}"
        )
        if r.get("details") and r.get("severity") in ("CRITICAL", "WARNING"):
            lines.append(f"  - _{r['details']}_")

    return lines, len(critical), True


def _summarize_plugin_audit(artifact_dir: Path) -> tuple[list[str], int, bool]:
    data, error = _load(artifact_dir / "plugin-audit-results" / "plugin-audit-results.json")
    if error:
        return [f"_plugin audit results unavailable: {error}_"], 0, False

    summary = data.get("summary", {})
    findings = data.get("findings", [])
    critical_findings = [f for f in findings if f.get("severity") == "CRITICAL"]
    warning_findings = [f for f in findings if f.get("severity") == "WARNING"]

    lines = [
        f"**Plugin Audit**: {data.get('plugins_scanned', '?')} plugins scanned — "
        f"{summary.get('critical', 0)} CRITICAL · {summary.get('warnings', 0)} WARNINGS"
    ]

    if critical_findings:
        lines += ["", "| Plugin | File | Line | Rule | Message |",
                  "| --- | --- | --- | --- | --- |"]
        for f in critical_findings[:10]:
            fname = Path(f.get("file", "")).name
            lines.append(_md_table_row(
                f.get("plugin_id", "?"),
                f"`{fname}`",
                str(f.get("line", "?")),
                f.get("rule", "?"),
                f.get("message", ""),
            ))

    if warning_findings and not critical_findings:
        lines.append(f"\n_{len(warning_findings)} warning(s) found — see artifact for details_")

    return lines, summary.get("critical", 0), True


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate consolidated security audit report",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--artifact-dir", required=True,
                        help="Directory containing downloaded CI artifacts")
    parser.add_argument("--output", "-o", required=True,
                        help="Output Markdown file path")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    artifact_dir = Path(args.artifact_dir)
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    bandit_lines, bandit_crit, bandit_ok = _summarize_bandit(artifact_dir)
    pip_audit_lines, pip_audit_crit, pip_audit_ok = _summarize_pip_audit(artifact_dir)
    gitleaks_lines, gitleaks_crit, gitleaks_ok = _summarize_gitleaks(artifact_dir)
    proofs_lines, proofs_crit, proofs_ok = _summarize_security_proofs(artifact_dir)
    plugins_lines, plugins_crit, plugins_ok = _summarize_plugin_audit(artifact_dir)

    unavailable_tools = [
        name for name, ok in [
            ("bandit", bandit_ok), ("pip-audit", pip_audit_ok),
            ("gitleaks", gitleaks_ok), ("security-proofs", proofs_ok),
            ("plugin-audit", plugins_ok),
        ] if not ok
    ]

    total_critical = bandit_crit + pip_audit_crit + gitleaks_crit + proofs_crit + plugins_crit
    if unavailable_tools:
        # A missing/malformed artifact means that tool's checks never
        # actually ran -- this must not be reported as a clean PASS just
        # because the *artifacts that did load* found nothing.
        overall = "INCOMPLETE ⚠️"
    elif total_critical > 0:
        overall = "ACTION REQUIRED 🚨"
    else:
        overall = "PASSED ✅"

    def section(title: str, lines: list[str]) -> str:
        return f"### {title}\n\n" + "\n".join(lines) + "\n"

    incomplete_note = (
        f"\n_⚠️ Incomplete: results unavailable for {', '.join(unavailable_tools)} "
        f"— see the corresponding section(s) below for details_\n"
        if unavailable_tools else ""
    )

    report = f"""## 🔒 Security Audit — {overall}

_Generated: {timestamp}_
{incomplete_note}
| Critical | High/Warn | Overall |
| :---: | :---: | :---: |
| {'🚨 ' + str(total_critical) if total_critical else '✅ 0'} | ⚠️ see below | {overall} |

---

{section('SAST — Bandit', bandit_lines)}
{section('Dependencies — pip-audit', pip_audit_lines)}
{section('Secrets — Gitleaks', gitleaks_lines)}
{section('LEDMatrix Security Proofs', proofs_lines)}
{section('Plugin Security Audit', plugins_lines)}
---

_Total critical findings: **{total_critical}**_
"""

    output_path = Path(args.output)
    output_path.write_text(report, encoding="utf-8")

    if args.verbose:
        print(f"  Report written to: {output_path}")
        print(f"  Status: {overall}")
        print(f"  Critical findings: {total_critical}")
        print(f"    bandit={bandit_crit}  pip-audit={pip_audit_crit}  "
              f"gitleaks={gitleaks_crit}  proofs={proofs_crit}  plugins={plugins_crit}")
        if unavailable_tools:
            print(f"  Unavailable: {', '.join(unavailable_tools)}")

    if unavailable_tools:
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
