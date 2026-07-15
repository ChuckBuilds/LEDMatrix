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
import hashlib
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
            "PASS": "✅",  # nosec B105 - severity label, not a credential
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
    Verify that zip-slip protection actually guards zip extraction in
    store_manager.py.

    A whole-file substring check for "is_relative_to"/"Zip-slip detected"
    would pass even if the guard existed somewhere unrelated, or covered
    only one of several extract()/extractall() call sites. Instead, this
    walks the AST: for every extract()/extractall() call, it confirms an
    is_relative_to() check (and the "Zip-slip detected" log) appears
    earlier in that same enclosing function -- validate-then-bulk-extract
    (validate every member, then call extractall() only after all passed)
    counts as protecting the call, since it covers the same member list.
    """
    store_manager = PROJECT_ROOT / "src" / "plugin_system" / "store_manager.py"
    if not store_manager.exists():
        return TestResult("T1a", "CRITICAL",
                          "store_manager.py not found",
                          f"Expected at {store_manager}")

    content = store_manager.read_text(encoding="utf-8")
    try:
        tree = ast.parse(content, filename=str(store_manager))
    except SyntaxError as exc:
        return TestResult("T1a", "CRITICAL",
                          "store_manager.py could not be parsed",
                          str(exc))

    extraction_sites = 0
    unprotected: list[str] = []

    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        extract_calls = [
            node for node in ast.walk(func)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr in ("extract", "extractall")
        ]
        if not extract_calls:
            continue
        extraction_sites += len(extract_calls)

        guard_lines = [
            n.lineno for n in ast.walk(func)
            if isinstance(n, ast.Attribute) and n.attr == "is_relative_to"
        ]
        has_zip_slip_log = any(
            isinstance(n, ast.Constant) and isinstance(n.value, str)
            and "Zip-slip detected" in n.value
            for n in ast.walk(func)
        )

        for call in extract_calls:
            guarded = has_zip_slip_log and any(g < call.lineno for g in guard_lines)
            if not guarded:
                unprotected.append(
                    f"{func.name}() line {call.lineno}: {call.func.attr}() call not "
                    f"clearly preceded by an is_relative_to() guard + Zip-slip log "
                    f"in the same function"
                )

    if extraction_sites == 0:
        return TestResult("T1a", "WARNING",
                          "No zipfile extract()/extractall() calls found in store_manager.py",
                          "Verify plugin installation no longer extracts zip archives, "
                          "or that this check still targets the right file")

    if unprotected:
        return TestResult("T1a", "CRITICAL",
                          f"{len(unprotected)} of {extraction_sites} zip extraction "
                          f"call(s) not clearly guarded",
                          "; ".join(unprotected))

    return TestResult("T1a", "PASS",
                      "Zip-slip protection verified",
                      f"All {extraction_sites} extract()/extractall() call(s) in "
                      f"store_manager.py are preceded by an is_relative_to() guard "
                      f"with a Zip-slip log in the same function")


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

    scan_errors: list[str] = []

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
                except (SyntaxError, OSError) as exc:
                    # A file we couldn't parse/read was never actually
                    # scanned for eval()/exec() -- that must block this
                    # test, not silently pass as if it were clean.
                    rel = py_file.relative_to(PROJECT_ROOT)
                    scan_errors.append(f"{rel} — {type(exc).__name__}: {exc}")

    if scan_errors:
        results.append(TestResult(
            "T1b", "CRITICAL",
            f"{len(scan_errors)} plugin file(s) could not be scanned for eval()/exec()",
            "; ".join(scan_errors[:10])
        ))

    if violations:
        results.append(TestResult(
            "T1b", "CRITICAL",
            f"Dangerous function calls found in plugins ({len(violations)} instance(s))",
            "; ".join(violations[:10])
        ))
    elif not scan_errors:
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

    # There is currently no config mechanism that actually enforces the
    # local-only boundary the design-intent comment describes -- app.py
    # hardcodes host='0.0.0.0' unconditionally, so nothing here can confirm
    # this deployment is in fact LAN-only. Reporting this as mere INFO
    # understates that: an unauthenticated, CSRF-disabled API surface is a
    # real risk the moment this ever runs somewhere other than a home LAN,
    # documented rationale or not.
    return TestResult(
        "T2a", "WARNING",
        "API surface has no auth and CSRF disabled; enforcement of the "
        "documented local-only boundary cannot be confirmed",
        summary
    )


# ─────────────────────────────────────────────────────────────────────────────
# T3: Secrets & Credential Handling
# ─────────────────────────────────────────────────────────────────────────────

# Patterns that suggest real credentials (must be >8 chars, not placeholders)
_SECRET_PATTERNS = [
    (r'(?i)password\s*=\s*["\'](?!none|empty|placeholder|example|test|default|""|'')[^"\']{8,}["\']', "WARNING", "password"),
    (r'(?i)api[_-]?key\s*=\s*["\'](?!none|empty|placeholder|YOUR_|example|test)[^"\']{16,}["\']', "WARNING", "api_key"),
    (r'(?i)secret\s*=\s*["\'](?!none|empty|placeholder|YOUR_|example|test)[^"\']{16,}["\']', "WARNING", "secret"),
    # Real GitHub token pattern
    (r'ghp_[a-zA-Z0-9]{36}', "CRITICAL", "github_token"),
    # Generic long bearer tokens
    (r'Bearer\s+[a-zA-Z0-9\-_\.]{32,}', "WARNING", "bearer_token"),
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

            for pattern, severity, pattern_type in _SECRET_PATTERNS:
                for match in re.finditer(pattern, content):
                    line_content = match.group(0)
                    # Skip lines containing template placeholder strings.
                    # line_content is only used for this in-memory check --
                    # it must never be stored or included in output below.
                    if any(skip in line_content for skip in _TEMPLATE_SKIP_STRINGS):
                        continue
                    rel = py_file.relative_to(PROJECT_ROOT)
                    line_no = content[: match.start()].count("\n") + 1
                    # Redacted fingerprint lets the same finding be recognized
                    # across scans without ever reporting the matched
                    # credential itself (which would otherwise get published
                    # into CI logs, JSON artifacts, and PR comments -- wider
                    # exposure than the original leak).
                    fingerprint = hashlib.sha256(line_content.encode()).hexdigest()[:12]
                    violations.append(
                        f"[{severity}] {rel}:{line_no} — {pattern_type} "
                        f"(fingerprint {fingerprint})"
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

    # Check for pinned base image tags. A tag (even a specific version, not
    # just :latest) is mutable -- the same tag can point to a different
    # image later. Only a @sha256 digest is truly immutable/reproducible.
    from_lines = [l for l in content.splitlines() if l.strip().startswith("FROM")]
    for from_line in from_lines:
        parts = from_line.split()
        if len(parts) >= 2:
            image = parts[1]
            if "@sha256:" not in image:
                issues.append(f"Base image not pinned to a digest: {image}")

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
