"""The repo-root display_controller.py goes through run.py.

It used to call src.display_controller.main() directly, skipping run.py's
sys.dont_write_bytecode, its -e/-d flags and its logging setup. run.py's
argument parser answering --help shows the shim now runs run.py.
"""

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_root_display_controller_runs_run_py():
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "display_controller.py"), "--help"],
        cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "--emulator" in result.stdout and "--debug" in result.stdout
