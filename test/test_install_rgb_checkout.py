"""
Tests for the rpi-rgb-led-matrix checkout helpers in first_time_install.sh, and
for the contract between scripts/install/one-shot-install.sh and that script.

Background: the submodule was pinned to a commit whose RP1 backend emits the
ARMv7-only `dmb ishst` instruction, so the build failed on every ARMv6 board
(Pi Zero / Zero W / Pi 1). Bumping the pin fixes fresh installs, but `git pull`
never moves an existing submodule checkout, and earlier installers ran the
submodule git commands as root, leaving .git/modules/<submodule> root-owned and
the user locked out of their own checkout.

These cover everything that runs without root. The ownership-repair path
(root, a second user, chown) mutates the system, so -- as with the swap helpers
in test_install_lowmem.py -- it is exercised manually instead.
"""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
INSTALLER = ROOT / "first_time_install.sh"
ONE_SHOT = ROOT / "scripts" / "install" / "one-shot-install.sh"

BEGIN = "# --- rpi-rgb-led-matrix checkout helpers"
END = "# --- end rpi-rgb-led-matrix checkout helpers"
SUB = "rpi-rgb-led-matrix-master"

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="exercises the installer's bash under Linux"
)


def helper_block() -> str:
    text = INSTALLER.read_text(encoding="utf-8")
    assert text.count(BEGIN) == 1 and text.count(END) == 1, "helper block markers missing or duplicated"
    return text[text.index(BEGIN): text.index(END)]


def git(*args: str, cwd: Path, env: dict) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True)
    assert result.returncode == 0, f"git {' '.join(args)} failed: {result.stderr}"
    return result.stdout.strip()


@pytest.fixture
def git_env(tmp_path):
    config = tmp_path / "gitconfig"
    config.write_text(
        "[user]\n\tname = t\n\temail = t@t\n"
        "[protocol \"file\"]\n\tallow = always\n"
        "[init]\n\tdefaultBranch = master\n"
        "[advice]\n\tdetachedHead = false\n",
        encoding="utf-8",
    )
    return {
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "HOME": str(tmp_path),
        "GIT_CONFIG_GLOBAL": str(config),
        "GIT_CONFIG_NOSYSTEM": "1",
    }


@pytest.fixture
def upstream(tmp_path, git_env):
    """A stand-in for hzeller/rpi-rgb-led-matrix with commits A -> B -> C."""
    up = tmp_path / "upstream"
    up.mkdir()
    git("init", "-q", ".", cwd=up, env=git_env)
    commits = {}
    for name, filename in (("A", "Makefile"), ("B", "b"), ("C", "c")):
        (up / filename).write_text(name, encoding="utf-8")
        git("add", ".", cwd=up, env=git_env)
        git("commit", "-qm", name, cwd=up, env=git_env)
        commits[name] = git("rev-parse", "HEAD", cwd=up, env=git_env)
    return up, commits


@pytest.fixture
def make_project(tmp_path, git_env, upstream):
    """make_project(checkout) -> project pinned at B, submodule checked out at `checkout`."""
    up, commits = upstream

    def _make(checkout: str) -> Path:
        project = tmp_path / f"project-{checkout}"
        project.mkdir()
        git("init", "-q", ".", cwd=project, env=git_env)
        git("submodule", "add", "-q", str(up), SUB, cwd=project, env=git_env)
        git("checkout", "-q", commits["B"], cwd=project / SUB, env=git_env)
        git("add", SUB, cwd=project, env=git_env)
        git("commit", "-qm", "pin", cwd=project, env=git_env)
        git("checkout", "-q", commits[checkout], cwd=project / SUB, env=git_env)
        return project

    return _make


def run_helpers(snippet: str, project: Path, env: dict) -> subprocess.CompletedProcess:
    """Run a snippet against the helpers under the installer's own strict mode."""
    script = (
        "set -Eeuo pipefail\n"
        "trap 'echo ERR_TRAP_FIRED >&2; exit 99' ERR\n"
        f"{helper_block()}\n"
        f'PROJECT_ROOT_DIR="{project}"\n'
        f"{snippet}\n"
    )
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env)


def head_of(project: Path, env: dict) -> str:
    return git("rev-parse", "HEAD", cwd=project / SUB, env=env)


class TestInstallerStructure:
    def test_installer_is_syntactically_valid(self):
        result = subprocess.run(["bash", "-n", str(INSTALLER)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr

    def test_helper_block_defines_the_helpers(self):
        block = helper_block()
        for name in ("_rgb_repo_owner", "_git_as_repo_owner", "_reclaim_rgb_checkout", "_sync_rgb_submodule"):
            assert re.search(rf"^{name}\(\) \{{", block, re.M), f"{name} is not defined in the helper block"

    def test_every_called_underscore_function_is_defined_before_use(self):
        # A renamed helper with a stale call site aborts the installer with
        # "command not found" under set -e -- the users this protects are the
        # ones least able to recover from that.
        lines = INSTALLER.read_text(encoding="utf-8").splitlines()
        defined_at = {}
        for number, line in enumerate(lines, 1):
            match = re.match(r"\s*(_[A-Za-z0-9_]+)\(\) \{", line)
            if match:
                defined_at.setdefault(match.group(1), number)
        # A name in command position; `_name=value` is an assignment and
        # `_name() {` a definition, so neither counts as a call.
        call = re.compile(r"(?:^\s*|\bretry\s+|\bif\s+!?\s*|&&\s*|\|\|\s*|\$\()(_[A-Za-z0-9_]+)\b(?!\(\)|=|\[)")
        for number, line in enumerate(lines, 1):
            if line.lstrip().startswith("#"):
                continue
            for name in call.findall(line):
                if name.startswith("_") and not re.match(r"_[A-Z0-9_]+$", name):  # skip $_VARS
                    assert name in defined_at, f"line {number} calls undefined function {name}"
                    # Functions defined inside another function body run later;
                    # top-level calls must come after the definition.
                    if not line.startswith((" ", "\t")):
                        assert defined_at[name] < number, f"line {number} calls {name} before it is defined"


class TestSyncSubmodule:
    def test_checkout_behind_the_pin_is_moved_forward(self, make_project, upstream, git_env):
        project = make_project("A")
        result = run_helpers("_reclaim_rgb_checkout; _sync_rgb_submodule", project, git_env)
        assert result.returncode == 0, result.stderr
        assert head_of(project, git_env) == upstream[1]["B"]

    def test_checkout_newer_than_the_pin_is_left_alone(self, make_project, upstream, git_env):
        # What `git submodule update --remote` -- advice the installer itself
        # prints for Pi 5 -- leaves behind. Must never be rolled back.
        project = make_project("C")
        result = run_helpers("_sync_rgb_submodule", project, git_env)
        assert result.returncode == 0, result.stderr
        assert head_of(project, git_env) == upstream[1]["C"]
        assert "leaving it as is" in result.stdout

    def test_checkout_at_the_pin_is_a_silent_no_op(self, make_project, upstream, git_env):
        project = make_project("B")
        result = run_helpers("_sync_rgb_submodule", project, git_env)
        assert result.returncode == 0, result.stderr
        assert result.stdout == ""
        assert head_of(project, git_env) == upstream[1]["B"]

    def test_unreachable_pin_warns_and_keeps_the_checkout(self, make_project, upstream, git_env):
        project = make_project("A")
        ghost = "0123456789abcdef0123456789abcdef01234567"
        git("update-index", "--cacheinfo", f"160000,{ghost},{SUB}", cwd=project, env=git_env)
        git("commit", "-qm", "ghost pin", cwd=project, env=git_env)
        result = run_helpers("_sync_rgb_submodule", project, git_env)
        assert result.returncode == 0, result.stderr
        assert "ERR_TRAP_FIRED" not in result.stderr
        assert "Could not update" in result.stdout
        assert head_of(project, git_env) == upstream[1]["A"]

    def test_local_files_blocking_the_checkout_are_not_clobbered(self, make_project, git_env):
        project = make_project("A")
        (project / SUB / "b").write_text("mine", encoding="utf-8")  # B adds a tracked `b`
        result = run_helpers("_sync_rgb_submodule", project, git_env)
        assert result.returncode == 0, result.stderr
        assert (project / SUB / "b").read_text(encoding="utf-8") == "mine"

    def test_directory_without_git_metadata_is_a_no_op(self, make_project, git_env):
        project = make_project("A")
        subprocess.run(["rm", "-rf", str(project / SUB / ".git"), str(project / ".git" / "modules")], check=True)
        result = run_helpers("_sync_rgb_submodule", project, git_env)
        assert result.returncode == 0, result.stderr
        assert result.stdout == ""

    def test_project_without_the_submodule_configured_is_a_no_op(self, tmp_path, git_env):
        project = tmp_path / "plain"
        (project / SUB).mkdir(parents=True)
        result = run_helpers("_sync_rgb_submodule", project, git_env)
        assert result.returncode == 0, result.stderr
        assert result.stdout == ""


class TestOwnershipHelpersWithoutRoot:
    @pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="covers the non-root path")
    def test_reclaim_is_a_no_op_and_git_runs_directly(self, make_project, git_env):
        project = make_project("B")
        result = run_helpers("_reclaim_rgb_checkout; _git_as_repo_owner --version", project, git_env)
        assert result.returncode == 0, result.stderr
        assert result.stdout.startswith("git version")


class TestOneShotContract:
    """one-shot-install.sh clones the repo and hands off to first_time_install.sh."""

    def test_one_shot_invokes_the_installer_at_the_repo_root(self):
        one_shot = ONE_SHOT.read_text(encoding="utf-8")
        assert "first_time_install.sh -y" in one_shot
        assert INSTALLER.is_file()

    def test_installer_accepts_everything_the_one_shot_passes(self):
        one_shot = ONE_SHOT.read_text(encoding="utf-8")
        installer = INSTALLER.read_text(encoding="utf-8")
        assert re.search(r"^\s*-y\|--yes\)", installer, re.M), "installer no longer parses -y"
        for var in sorted(set(re.findall(r"\b(LEDMATRIX_[A-Z_]+)=", one_shot))):
            assert var in installer, f"one-shot passes {var}, which first_time_install.sh never reads"

    def test_installer_initialises_the_submodule_itself(self):
        # The one-shot clones without --recurse-submodules, so the installer is
        # the only thing that ever fetches the matrix library.
        assert "--recurse-submodules" not in ONE_SHOT.read_text(encoding="utf-8")
        installer = INSTALLER.read_text(encoding="utf-8")
        assert re.search(rf"submodule update --init --recursive {SUB}", installer)
