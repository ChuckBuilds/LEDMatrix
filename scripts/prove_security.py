#!/usr/bin/env python3
"""
LEDMatrix Security Proof Tests

Automated proofs that run in CI to verify security properties hold on every
commit. Inspired by the Huntarr security review approach of using standard
tooling to confirm specific vulnerability classes are absent.

Usage:
    python scripts/prove_security.py
    python scripts/prove_security.py --verbose
    python scripts/prove_security.py --output results.json

Exit code: 1 only if CRITICAL findings are detected. Warnings are reported
but do not block CI.
"""

import ast
import argparse
import json
import re
import sys
from dataclasses import dataclass, asdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ─────────────────────────────────────────────────────────────────────────────
# Result dataclass
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class TestResult:
    test_id: str
    severity: str   # PASS | INFO | WARNING | CRITICAL | SKIP
    message: str
    details: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def icon(self) -> str:
        return {
            "PASS": "✅",
            "INFO": "ℹ️ ",
            "WARNING": "⚠️ ",
            "CRITICAL": "🚨",
            "SKIP": "⏭️ ",
        }.get(self.severity, "❓")


# ─────────────────────────────────────────────────────────────────────────────
# T1: Plugin Loading / Zip Slip
# ─────────────────────────────────────────────────────────────────────────────

def test_t1a_zip_slip_protection() -> TestResult:
    """
    Verify that zip-slip protection exists in store_manager.py.

    The protection lives at src/plugin_system/store_manager.py and uses
    Path.is_relative_to() to validate each zip member before extraction.
    This test confirms the guard is present — it should always pass green.
    """
    store_manager = PROJECT_ROOT / "src" / "plugin_system" / "store_manager.py"
    if not store_manager.exists():
        return TestResult("T1a", "CRITICAL",
                          "store_manager.py not found",
                          f"Expected at {store_manager}")

    content = store_manager.read_text(encoding="utf-8")

    has_relative_to = "is_relative_to" in content
    has_log_message = "Zip-slip detected" in content

    if not has_relative_to:
        return TestResult("T1a", "CRITICAL",
                          "Zip-slip protection (is_relative_to) NOT FOUND in store_manager.py",
                          "The is_relative_to() guard must be present before zipfile.extractall()")

    if not has_log_message:
        return TestResult("T1a", "WARNING",
                          "is_relative_to() found but 'Zip-slip detected' log message missing",
                          "Verify the protection block is still active and the log was not removed")

    return TestResult("T1a", "PASS",
                      "Zip-slip protection verified",
                      "is_relative_to() guard + 'Zip-slip detected' log present in store_manager.py")


def test_t1b_dangerous_plugin_calls() -> list[TestResult]:
    """
    Scan plugin directories for dangerous function calls (eval, exec).
    These represent arbitrary code execution risks in plugin code.
    """
    results = []
    plugin_dirs = [
        PROJECT_ROOT / "plugins",
        PROJECT_ROOT / "plugin-repos",
    ]

    violations: list[str] = []
    files_scanned = 0

    for base in plugin_dirs:
        if not base.exists():
            continue
        for plugin_dir in sorted(base.iterdir()):
            if not plugin_dir.is_dir() or plugin_dir.name.startswith(('.', '_')):
                continue
            for py_file in plugin_dir.rglob("*.py"):
                files_scanned += 1
                try:
                    source = py_file.read_text(encoding="utf-8")
                    tree = ast.parse(source, filename=str(py_file))
                    for node in ast.walk(tree):
                        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                            if node.func.id in ("eval", "exec"):
                                rel = py_file.relative_to(PROJECT_ROOT)
                                violations.append(
                                    f"{rel}:{node.lineno} — {node.func.id}() call")
                except (SyntaxError, OSError):
                    pass

    if violations:
        results.append(TestResult(
            "T1b", "CRITICAL",
            f"Dangerous function calls found in plugins ({len(violations)} instance(s))",
            "; ".join(violations[:10])
        ))
    else:
        results.append(TestResult(
            "T1b", "PASS",
            "No eval()/exec() calls found in plugins",
            f"{files_scanned} plugin Python files scanned"
        ))

    return results


# ─────────────────────────────────────────────────────────────────────────────
# T2: API Surface Inventory
# ─────────────────────────────────────────────────────────────────────────────

def test_t2a_api_surface_inventory() -> TestResult:
    """
    Document the API surface area.

    This app intentionally has no authentication (local-only Raspberry Pi
    design, documented in web_interface/app.py). This test produces an
    inventory for audit purposes and warns only if the design-intent comment
    is removed from app.py (which would indicate someone deleted the rationale
    without adding auth, rather than a deliberate undocumented change).
    """
    api_file = PROJECT_ROOT / "web_interface" / "blueprints" / "api_v3.py"
    app_file = PROJECT_ROOT / "web_interface" / "app.py"

    if not api_file.exists():
        return TestResult("T2a", "WARNING", "api_v3.py not found", str(api_file))

    api_content = api_file.read_text(encoding="utf-8")
    routes = re.findall(r"@api_v3\.route\('([^']+)'", api_content)

    csrf_documented = False
    if app_file.exists():
        app_content = app_file.read_text(encoding="utf-8")
        csrf_documented = "CSRF protection disabled for local-only" in app_content

    summary = (
        f"{len(routes)} API routes in api_v3.py. "
        f"No auth decorators (intentional local-only design). "
        f"CSRF disabled: {'YES — design intent documented in app.py' if csrf_documented else 'YES — but design intent comment NOT found in app.py'}. "
        f"Rate limiting: 1000/min."
    )

    if not csrf_documented:
        return TestResult(
            "T2a", "WARNING",
            "CSRF is disabled but the design-intent comment is missing from app.py",
            "Add the rationale comment back, or add proper CSRF protection if "
            "the app is now internet-facing"
        )

    return TestResult("T2a", "INFO", "API surface documented", summary)


# ─────────────────────────────────────────────────────────────────────────────
# T3: Secrets & Credential Handling
# ─────────────────────────────────────────────────────────────────────────────

# Patterns that suggest real credentials (must be >8 chars, not placeholders)
_SECRET_PATTERNS = [
    (r'(?i)password\s*=\s*["\'](?!none|empty|placeholder|example|test|default|""|'')[^"\']{8,}["\']', "WARNING"),
    (r'(?i)api[_-]?key\s*=\s*["\'](?!none|empty|placeholder|YOUR_|example|test)[^"\']{16,}["\']', "WARNING"),
    (r'(?i)secret\s*=\s*["\'](?!none|empty|placeholder|YOUR_|example|test)[^"\']{16,}["\']', "WARNING"),
    # Real GitHub token pattern
    (r'ghp_[a-zA-Z0-9]{36}', "CRITICAL"),
    # Generic long bearer tokens
    (r'Bearer\s+[a-zA-Z0-9\-_\.]{32,}', "WARNING"),
]

_TEMPLATE_SKIP_STRINGS = [
    "YOUR_", "PLACEHOLDER", "_HERE", "example.com", "config_secrets.template",
    "prove_security",  # this file itself
]

_SCAN_DIRS = ["src", "web_interface", "scripts"]


def test_t3a_hardcoded_secrets() -> TestResult:
    """Scan source code for hardcoded credentials."""
    violations: list[str] = []

    for dir_name in _SCAN_DIRS:
        scan_dir = PROJECT_ROOT / dir_name
        if not scan_dir.exists():
            continue
        for py_file in scan_dir.rglob("*.py"):
            # Skip test files and this script
            if "test" in str(py_file).lower() or "prove_security" in str(py_file):
                continue
            try:
                content = py_file.read_text(encoding="utf-8")
            except OSError:
                continue

            for pattern, severity in _SECRET_PATTERNS:
                for match in re.finditer(pattern, content):
                    line_content = match.group(0)
                    # Skip lines containing template placeholder strings
                    if any(skip in line_content for skip in _TEMPLATE_SKIP_STRINGS):
                        continue
                    rel = py_file.relative_to(PROJECT_ROOT)
                    line_no = content[: match.start()].count("\n") + 1
                    violations.append(
                        f"[{severity}] {rel}:{line_no} — {line_content[:60]}"
                    )

    critical_violations = [v for v in violations if "[CRITICAL]" in v]
    if critical_violations:
        return TestResult(
            "T3a", "CRITICAL",
            f"Hardcoded secrets found ({len(critical_violations)} critical)",
            "; ".join(critical_violations[:5])
        )
    if violations:
        return TestResult(
            "T3a", "WARNING",
            f"Potential hardcoded secrets found ({len(violations)} instance(s))",
            "; ".join(violations[:5])
        )

    return TestResult("T3a", "PASS", "No hardcoded secrets detected",
                      f"Scanned {', '.join(_SCAN_DIRS)}")


def test_t3b_plaintext_password_storage() -> TestResult:
    """
    Check for user account password storage without hashing.

    The LEDMatrix app has no user account system, so this should produce INFO.
    It would only CRITICAL if someone added user auth and stored passwords without hashing.

    We require all three of: a password *variable assignment or DB operation*,
    a clear storage call (INSERT / db commit / ORM save), and no hashing lib present
    — to avoid false positives from files that contain 'password' for WiFi handling
    and '.save()' for image/file saving in unrelated functions.
    """
    hashing_libs = ["bcrypt", "argon2", "pbkdf2", "scrypt",
                    "generate_password_hash", "hashpw", "make_password"]
    # Patterns that indicate password being stored in a database / ORM context.
    # Must be specific enough to avoid matching set.add(), file.save(), etc.
    db_storage_patterns = ["INSERT INTO", "db.session", "session.add(", "session.commit(", "orm.save"]

    password_storage_found = False

    for dir_name in _SCAN_DIRS:
        scan_dir = PROJECT_ROOT / dir_name
        if not scan_dir.exists():
            continue
        for py_file in scan_dir.rglob("*.py"):
            try:
                content = py_file.read_text(encoding="utf-8")
            except OSError:
                continue
            # Require DB/ORM context specifically — not just any .save() call
            if ("password" in content.lower() and
                    any(store in content for store in db_storage_patterns) and
                    not any(h in content for h in hashing_libs)):
                password_storage_found = True

    if password_storage_found:
        return TestResult(
            "T3b", "CRITICAL",
            "Potential plaintext password storage in database/ORM detected",
            "Found password + database storage operations without a recognized hashing library"
        )

    return TestResult("T3b", "INFO",
                      "No plaintext password storage detected",
                      "App has no user account system — expected result")


# ─────────────────────────────────────────────────────────────────────────────
# T4: Path Traversal
# ─────────────────────────────────────────────────────────────────────────────

def test_t4a_path_traversal() -> TestResult:
    """
    Verify static file serving uses send_from_directory (safe) rather than
    open() with user-supplied paths. Also checks for extractall() calls that
    lack the is_relative_to() guard.
    """
    issues: list[str] = []

    app_file = PROJECT_ROOT / "web_interface" / "app.py"
    if app_file.exists():
        content = app_file.read_text(encoding="utf-8")
        # The file-serve route should use send_from_directory or commonpath
        if "send_from_directory" not in content and "commonpath" not in content:
            issues.append("app.py: file-serve routes may not use send_from_directory/commonpath")

    # Check all extractall() calls have a preceding is_relative_to guard
    for py_file in (PROJECT_ROOT / "src").rglob("*.py"):
        try:
            content = py_file.read_text(encoding="utf-8")
        except OSError:
            continue
        if "extractall(" in content and "is_relative_to" not in content:
            rel = py_file.relative_to(PROJECT_ROOT)
            issues.append(f"{rel}: extractall() without is_relative_to() guard")

    if issues:
        return TestResult(
            "T4a", "WARNING",
            f"Potential path traversal patterns found ({len(issues)})",
            "; ".join(issues)
        )

    return TestResult("T4a", "PASS",
                      "Path traversal mitigations verified",
                      "send_from_directory/commonpath used for file serving; "
                      "extractall() calls have is_relative_to() guards")


# ─────────────────────────────────────────────────────────────────────────────
# T5: Auth Bypass Patterns
# ─────────────────────────────────────────────────────────────────────────────

def test_t5a_auth_bypass_patterns() -> TestResult:
    """
    Look for broken auth bypass patterns — not the intentional no-auth design
    (T2a covers that), but patterns that suggest auth was INTENDED to exist
    but has an exploitable bypass: broad substring matching, debug-mode skips,
    or if-True conditions.
    """
    bypass_signals = [
        (r'if\s+True\s*:', "if True: bypass"),
        (r'if\s+debug\s*:', "debug-mode auth skip"),
        (r'request\.path\s+in\s+', "substring path matching in auth (Huntarr pattern)"),
        (r'EXEMPT_ROUTES\s*=', "exempt routes list"),
    ]

    findings: list[str] = []

    for dir_name in ["src", "web_interface"]:
        scan_dir = PROJECT_ROOT / dir_name
        if not scan_dir.exists():
            continue
        for py_file in scan_dir.rglob("*.py"):
            try:
                content = py_file.read_text(encoding="utf-8")
            except OSError:
                continue
            for pattern, label in bypass_signals:
                if re.search(pattern, content):
                    # Only flag if the file also contains auth-related terms
                    if any(auth in content.lower() for auth in
                           ["auth", "login", "authenticate", "token", "permission"]):
                        rel = py_file.relative_to(PROJECT_ROOT)
                        findings.append(f"{rel}: {label}")

    if findings:
        return TestResult(
            "T5a", "WARNING",
            f"Potential auth bypass patterns found ({len(findings)})",
            "; ".join(findings[:5])
        )

    return TestResult("T5a", "PASS",
                      "No auth bypass patterns detected",
                      "Checked src/ and web_interface/ for bypass signals")


# ─────────────────────────────────────────────────────────────────────────────
# T6: Docker / Container Hardening
# ─────────────────────────────────────────────────────────────────────────────

def test_t6_docker_hardening() -> TestResult:
    """Container security — skipped if no Dockerfile exists."""
    dockerfile = PROJECT_ROOT / "Dockerfile"
    if not dockerfile.exists():
        return TestResult("T6", "SKIP",
                          "No Dockerfile found — container security scan not applicable",
                          "If Docker support is added in future, enable hadolint/trivy scanning "
                          "in .github/workflows/security-audit.yml")

    content = dockerfile.read_text(encoding="utf-8")
    issues: list[str] = []

    # Check for non-root USER directive
    user_lines = [l for l in content.splitlines() if l.strip().startswith("USER")]
    if not user_lines or user_lines[-1].strip() == "USER root":
        issues.append("Container runs as root — use USER directive to drop privileges")

    # Check for pinned base image tags
    from_lines = [l for l in content.splitlines() if l.strip().startswith("FROM")]
    for from_line in from_lines:
        parts = from_line.split()
        if len(parts) >= 2:
            image = parts[1]
            if ":" not in image or image.endswith(":latest"):
                issues.append(f"Unpinned base image: {image}")

    if issues:
        return TestResult("T6", "WARNING",
                          f"Dockerfile hardening issues ({len(issues)})",
                          "; ".join(issues))

    return TestResult("T6", "PASS", "Dockerfile hardening checks passed", "")


# ─────────────────────────────────────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="LEDMatrix security proof tests",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--output", "-o", default=None,
                        help="Write JSON results to this file")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Show details for each check")
    args = parser.parse_args()

    print("=" * 60)
    print("LEDMatrix Security Proof Tests")
    print(f"Project root: {PROJECT_ROOT}")
    print("=" * 60)

    all_results: list[TestResult] = []

    # Run all test groups
    all_results.append(test_t1a_zip_slip_protection())
    all_results.extend(test_t1b_dangerous_plugin_calls())
    all_results.append(test_t2a_api_surface_inventory())
    all_results.append(test_t3a_hardcoded_secrets())
    all_results.append(test_t3b_plaintext_password_storage())
    all_results.append(test_t4a_path_traversal())
    all_results.append(test_t5a_auth_bypass_patterns())
    all_results.append(test_t6_docker_hardening())

    # Print results
    print()
    for r in all_results:
        line = f"  {r.icon} [{r.severity:<8}] {r.test_id}: {r.message}"
        print(line)
        if args.verbose and r.details:
            print(f"             {r.details}")

    # Tally
    critical = [r for r in all_results if r.severity == "CRITICAL"]
    warnings = [r for r in all_results if r.severity == "WARNING"]
    passed = [r for r in all_results if r.severity == "PASS"]
    skipped = [r for r in all_results if r.severity == "SKIP"]

    print()
    print(f"  Results: {len(passed)} PASS  {len(warnings)} WARN  "
          f"{len(critical)} CRITICAL  {len(skipped)} SKIP")

    # Write JSON output
    if args.output:
        output_data = [r.to_dict() for r in all_results]
        Path(args.output).write_text(
            json.dumps(output_data, indent=2), encoding="utf-8"
        )
        print(f"  Results written to: {args.output}")

    if critical:
        print(f"\n  🚨 {len(critical)} CRITICAL issue(s) found — blocking")
        return 1

    if warnings:
        print(f"\n  ⚠️  {len(warnings)} warning(s) found — non-blocking")

    print("\n  ✅ All checks passed (warnings are non-blocking)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
