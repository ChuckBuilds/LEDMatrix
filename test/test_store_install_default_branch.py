"""A repository whose only branch is neither main nor master still installs.

_install_via_git tries the candidate branches, then the repository's default
branch -- but it returned None both for "every clone failed" and for "the
default-branch clone succeeded". install_from_url took the None as failure,
fell through to the archive download of main/master (which does not exist),
and reported "Failed to clone or download repository" for a repository it had
just cloned.
"""

import json
import shutil
import subprocess

import pytest

from src.plugin_system.store_manager import PluginStoreManager

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")

MANIFEST = {
    "id": "develop-only", "name": "Develop Only", "class_name": "P",
    "display_modes": ["develop_only"], "version": "1.0.0",
}


def _git(*args, cwd):
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def develop_only_repo(tmp_path):
    repo = tmp_path / "upstream"
    repo.mkdir()
    _git("init", "-q", "-b", "develop", cwd=repo)
    (repo / "manifest.json").write_text(json.dumps(MANIFEST))
    (repo / "manager.py").write_text("class P: pass\n")
    _git("add", ".", cwd=repo)
    _git("commit", "-q", "-m", "init", cwd=repo)
    return repo.as_uri()


@pytest.fixture
def store(tmp_path, monkeypatch):
    mgr = PluginStoreManager(
        plugins_dir=str(tmp_path / "plugins"),
        uninstalled_registry_path=str(tmp_path / "uninstalled.json"))
    monkeypatch.setattr(mgr, "_install_dependencies", lambda *a, **k: True)
    downloads = []
    monkeypatch.setattr(mgr, "_install_via_download",
                        lambda url, *a, **k: downloads.append(url) or False)
    mgr.downloads = downloads
    return mgr


def test_a_default_branch_clone_reports_its_branch(store, develop_only_repo, tmp_path):
    target = tmp_path / "clone"
    assert store._install_via_git(develop_only_repo, target, ["main", "master"]) == "develop"
    assert (target / "manifest.json").exists()


def test_a_failed_clone_reports_none(store, tmp_path):
    missing = (tmp_path / "no-such-repo").as_uri()
    target = tmp_path / "clone"
    assert store._install_via_git(missing, target, ["main"]) is None
    assert not target.exists()


def test_install_from_url_installs_a_develop_only_repository(store, develop_only_repo):
    result = store.install_from_url(develop_only_repo)

    assert result == {"success": True, "plugin_id": "develop-only",
                      "name": "Develop Only", "branch": "develop"}
    assert (store.plugins_dir / "develop-only" / "manifest.json").exists()
    assert store.downloads == []
