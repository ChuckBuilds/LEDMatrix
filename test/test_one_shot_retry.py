"""retry() in scripts/install/one-shot-install.sh retries, and reports failure.

It used `if ! "$@"; then status=$?`, where $? is the status of the negation,
always 0: a failed command was never retried and retry() returned success,
so a failed `git clone` carried on until a later check noticed the missing
checkout. The apt steps now retry for real but stay non-fatal, as they
effectively were; a clone that keeps failing stops the install.
"""
import re
import subprocess
import sys
from pathlib import Path

import pytest

ONE_SHOT = Path(__file__).resolve().parent.parent / "scripts" / "install" / "one-shot-install.sh"

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="runs the installer's bash under Linux"
)


def _function(name):
    text = ONE_SHOT.read_text(encoding="utf-8")
    m = re.search(rf"^{name}\(\) \{{\n.*?^\}}\n", text, re.S | re.M)
    assert m, f"{name}() not found in one-shot-install.sh"
    return m.group(0)


def _run(snippet):
    script = (
        "set -Eeuo pipefail\n"
        "trap 'echo ERR_TRAP_FIRED >&2; exit 99' ERR\n"
        "print_error() { echo \"E: $*\" >&2; }\n"
        "print_warning() { echo \"W: $*\" >&2; }\n"
        "sleep() { :; }\n"
        f"{_function('retry')}\n"
        f"{snippet}\n"
    )
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True)


def test_failure_is_retried_and_reported():
    r = _run("n=0; f() { n=$((n+1)); return 7; }\n"
             "if retry f; then echo OK; else echo \"FAILED $? after $n\"; fi")
    assert r.stdout.strip() == "FAILED 7 after 3", r.stdout + r.stderr


def test_success_on_a_later_attempt():
    r = _run("n=0; f() { n=$((n+1)); [ $n -ge 2 ]; }\n"
             "retry f && echo \"OK after $n\"")
    assert r.stdout.strip() == "OK after 2", r.stdout + r.stderr


def test_a_plain_call_that_keeps_failing_stops_the_script():
    r = _run("retry false\necho SHOULD_NOT_RUN")
    assert "SHOULD_NOT_RUN" not in r.stdout
    assert "ERR_TRAP_FIRED" in r.stderr


def test_apt_steps_stay_non_fatal():
    text = ONE_SHOT.read_text(encoding="utf-8")
    for line in text.splitlines():
        if re.search(r"\bretry (sudo )?apt-get ", line):
            assert "||" in line, f"apt step would now abort the install: {line.strip()}"
