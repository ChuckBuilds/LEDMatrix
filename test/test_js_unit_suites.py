"""Run every web-interface JS unit suite (test/js/unit/*.js) under pytest.

They need nothing but node, but CI ran only one of them, from
test/web_interface/test_update_all_plugins.py. The DOM suites need jsdom and
a running server, so they stay with test/js/run_all.js.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

UNIT_DIR = Path(__file__).resolve().parent / 'js' / 'unit'
SUITES = sorted(UNIT_DIR.glob('test_*.js'))


def test_suites_found():
    assert SUITES, f"no JS unit suites under {UNIT_DIR}"


@pytest.mark.skipif(shutil.which('node') is None, reason='node is not installed')
@pytest.mark.parametrize('suite', SUITES, ids=[s.name for s in SUITES])
def test_js_unit_suite(suite):
    result = subprocess.run([shutil.which('node'), str(suite)], capture_output=True,
                            text=True, timeout=120, cwd=str(UNIT_DIR.parent))
    assert result.returncode == 0, result.stdout + result.stderr
