"""scripts/fix_perms/safe_pip_install.sh must accept what the updaters install.

Update Code and the automatic update's health check install the core's own
requirement files through that root wrapper, and the wrapper refuses any path
it does not list. It used to list only requirements.txt, so an update that
changed web_interface/requirements.txt failed its dependency install -- and
the automatic updater rolls back any update whose dependencies did not
install, on every device, every week.

The real script runs here with only its final pip command swapped for an
echo, so the path checks under test are the shipped ones.
"""
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

WRAPPER_REL = Path('scripts') / 'fix_perms' / 'safe_pip_install.sh'
PIP_LINE = 'exec "$PYTHON_PATH" -m pip install'


def _bash():
    """A bash whose realpath understands --canonicalize-missing, or None."""
    candidates = [shutil.which('bash')]
    if os.name == 'nt':
        candidates.insert(0, r'C:\Program Files\Git\bin\bash.exe')
    for bash in candidates:
        if not bash or not os.path.exists(bash):
            continue
        try:
            probe = subprocess.run([bash, '-c', 'realpath --canonicalize-missing /no/such/x'],
                                   capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            continue
        if probe.returncode == 0:
            return bash
    return None


BASH = _bash()
pytestmark = pytest.mark.skipif(BASH is None, reason='no bash with GNU realpath')


@pytest.fixture
def project(tmp_path):
    """A project tree holding the real wrapper, with pip stubbed out."""
    root = tmp_path / 'LEDMatrix'
    wrapper = root / WRAPPER_REL
    wrapper.parent.mkdir(parents=True)
    text = (ROOT / WRAPPER_REL).read_text(encoding='utf-8').replace('\r\n', '\n')
    lines = text.split('\n')
    pip = [i for i, line in enumerate(lines) if line.startswith(PIP_LINE)]
    assert len(pip) == 1, 'the wrapper no longer ends in the pip install this test stubs'
    lines[pip[0]] = 'echo "WOULD INSTALL $RESOLVED_TARGET"; exit 0'
    wrapper.write_text('\n'.join(lines), encoding='utf-8', newline='\n')
    for rel in ('requirements.txt', 'web_interface/requirements.txt',
                'plugin-repos/clock/requirements.txt', 'src/requirements.txt',
                'web_interface/extra/requirements.txt'):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text('requests\n', encoding='utf-8')
    return root


def run_wrapper(root, rel):
    """Run the wrapper on ``rel``, with the path spelled the way bash sees the tree."""
    return subprocess.run(
        [BASH, '-c', 'cd "$1" && bash scripts/fix_perms/safe_pip_install.sh "$PWD/$2"',
         '_', str(root), rel],
        capture_output=True, text=True, timeout=60)


def _core_requirement_files():
    from web_interface.blueprints.api_v3 import system
    spec = importlib.util.spec_from_file_location(
        'auto_update_verify_for_allowlist', ROOT / 'scripts' / 'utils' / 'auto_update_verify.py')
    verifier = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verifier)
    assert set(verifier.REQUIREMENT_FILES) == set(system.CORE_REQUIREMENT_FILES), (
        'the rollback must reinstall the same files Update Code installs')
    return system.CORE_REQUIREMENT_FILES


@pytest.mark.parametrize('rel', _core_requirement_files())
def test_every_core_requirement_file_the_updaters_install_is_allowed(project, rel):
    result = run_wrapper(project, rel)
    assert result.returncode == 0, result.stderr
    assert 'WOULD INSTALL' in result.stdout


def test_plugin_requirements_are_still_allowed(project):
    assert run_wrapper(project, 'plugin-repos/clock/requirements.txt').returncode == 0


@pytest.mark.parametrize('rel', [
    'src/requirements.txt',                    # repo-owned folder, but not listed
    'web_interface/extra/requirements.txt',    # below an allowed file's folder
    'web_interface/requirements.txt.bak',      # not a requirements.txt
])
def test_other_paths_are_still_refused(project, rel):
    if rel.endswith('.bak'):
        (project / rel).write_text('requests\n', encoding='utf-8')
    result = run_wrapper(project, rel)
    assert result.returncode == 2, result.stdout + result.stderr
    assert 'DENIED' in result.stderr


@pytest.mark.parametrize('rel', ['requirements.txt', 'web_interface/requirements.txt'])
def test_a_core_requirement_file_symlinked_out_of_the_project_is_refused(project, tmp_path, rel):
    """Only the allowed files' folders are resolved, so the link's target is
    compared, and refused, rather than allowed as the file itself."""
    outside = tmp_path / 'elsewhere' / 'requirements.txt'
    outside.parent.mkdir()
    outside.write_text('evil\n', encoding='utf-8')
    link = project / rel
    link.unlink()
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip('symlinks unavailable')
    result = run_wrapper(project, rel)
    assert result.returncode == 2, result.stdout + result.stderr
