"""A plugin update must keep the files the plugin wrote beside itself.

Field incident, 2026-10-04: updating calendar 1.2.9 -> 1.2.12 from the web UI
replaced plugin-repos/calendar/ with the fresh download and deleted the old
copy -- and with it token.pickle and credentials.json, the plugin's Google
OAuth files. No release contains them (the repo gitignores them), so the hot
reload logged "Credentials file not found" and the calendar stayed broken
until the files were restored by hand.

Both update routes are covered: a monorepo plugin (registry ``plugin_path``),
which is reinstalled into a fresh directory, and a plugin installed from its
own git repository, which is updated with ``git pull`` after an auto-stash.
"""

import json
import shutil
import subprocess

import pytest

from src.plugin_system.plugin_local_files import (
    is_known_state_file, local_files_to_keep,
)
from src.plugin_system.store_manager import PluginStoreManager

PLUGIN_ID = "calendar"


def _manifest(version):
    return {"id": PLUGIN_ID, "name": "Calendar", "class_name": "CalendarPlugin",
            "display_modes": ["calendar"], "version": version}


def _write_release(target, version):
    """What a download of ``version`` puts on disk."""
    target.mkdir(parents=True, exist_ok=True)
    (target / "manifest.json").write_text(json.dumps(_manifest(version)))
    (target / "manager.py").write_text(f"VERSION = {version!r}\n")
    (target / ".gitignore").write_text("credentials.json\ntoken.pickle\ncache/\n")


def _drop_local_files(plugin_dir):
    """What the plugin writes at runtime: OAuth files plus cached state."""
    (plugin_dir / "token.pickle").write_bytes(b"\x80\x04oauth-token")
    (plugin_dir / "credentials.json").write_text('{"installed": {}}')
    (plugin_dir / "cache").mkdir()
    (plugin_dir / "cache" / "events.json").write_text("[]")


def _assert_local_files_kept(plugin_dir):
    assert (plugin_dir / "token.pickle").read_bytes() == b"\x80\x04oauth-token"
    assert (plugin_dir / "credentials.json").read_text() == '{"installed": {}}'
    assert (plugin_dir / "cache" / "events.json").read_text() == "[]"


def _leftover_backups(plugins_dir):
    return [p.name for p in plugins_dir.iterdir() if "standalone-backup" in p.name]


@pytest.fixture
def store(tmp_path, monkeypatch):
    mgr = PluginStoreManager(
        plugins_dir=str(tmp_path / "plugin-repos"),
        uninstalled_registry_path=str(tmp_path / "uninstalled.json"))
    mgr.plugins_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(mgr, "_install_dependencies", lambda *a, **k: True)
    monkeypatch.setattr(mgr, "fetch_registry", lambda *a, **k: {"plugins": []})
    return mgr


class TestMonorepoUpdate:
    @pytest.fixture
    def installed(self, store, monkeypatch):
        registry_entry = {
            "id": PLUGIN_ID, "repo": "https://github.com/ChuckBuilds/ledmatrix-plugins",
            "plugin_path": "plugins/calendar", "branch": "main",
            "latest_version": "1.2.9",
        }
        monkeypatch.setattr(store, "get_plugin_info", lambda *a, **k: registry_entry)
        release = {"version": "1.2.9"}

        def fake_monorepo_download(download_url, plugin_subpath, target):
            assert plugin_subpath == "plugins/calendar"
            _write_release(target, release["version"])
            return True

        monkeypatch.setattr(store, "_install_from_monorepo", fake_monorepo_download)
        assert store.install_plugin(PLUGIN_ID) is True

        def publish(version):
            registry_entry["latest_version"] = release["version"] = version
        return store, store.plugins_dir / PLUGIN_ID, publish

    def test_update_keeps_token_and_gitignored_files(self, installed):
        store, plugin_dir, publish = installed
        _drop_local_files(plugin_dir)

        publish("1.2.12")
        assert store.update_plugin(PLUGIN_ID) is True

        assert json.loads((plugin_dir / "manifest.json").read_text())["version"] == "1.2.12"
        _assert_local_files_kept(plugin_dir)
        assert _leftover_backups(store.plugins_dir) == []

    def test_token_is_kept_even_when_the_release_does_not_gitignore_it(self, installed):
        store, plugin_dir, publish = installed
        (plugin_dir / ".gitignore").unlink()
        (plugin_dir / "token.pickle").write_bytes(b"tok")
        (plugin_dir / "config_secrets.json").write_text("{}")

        publish("1.2.12")
        assert store.update_plugin(PLUGIN_ID) is True

        assert (plugin_dir / "token.pickle").read_bytes() == b"tok"
        assert (plugin_dir / "config_secrets.json").read_text() == "{}"

    def test_release_content_wins_and_old_code_is_not_carried(self, installed):
        store, plugin_dir, publish = installed
        # A file the old copy had that the new release dropped, byte code, and
        # an old copy of a file the new release also ships.
        (plugin_dir / "removed_module.py").write_text("OLD = True\n")
        (plugin_dir / "__pycache__").mkdir()
        (plugin_dir / "__pycache__" / "manager.cpython-313.pyc").write_bytes(b"pyc")

        publish("1.2.12")
        assert store.update_plugin(PLUGIN_ID) is True

        assert not (plugin_dir / "removed_module.py").exists()
        assert not (plugin_dir / "__pycache__").exists()
        assert "1.2.12" in (plugin_dir / "manager.py").read_text()

    def test_reinstall_over_an_existing_copy_keeps_them_too(self, installed):
        store, plugin_dir, publish = installed
        _drop_local_files(plugin_dir)

        assert store.install_plugin(PLUGIN_ID) is True

        _assert_local_files_kept(plugin_dir)
        assert _leftover_backups(store.plugins_dir) == []


class TestInstallFromUrlReplace:
    def test_replacing_an_installed_copy_keeps_the_token(self, store, monkeypatch):
        plugin_dir = store.plugins_dir / PLUGIN_ID
        _write_release(plugin_dir, "1.0.0")
        _drop_local_files(plugin_dir)

        def fake_clone(repo_url, target, branches):
            _write_release(target, "2.0.0")
            return "main"

        monkeypatch.setattr(store, "_install_via_git", fake_clone)
        result = store.install_from_url(
            "https://github.com/example/ledmatrix-calendar", plugin_id=PLUGIN_ID)

        assert result["success"] is True
        assert json.loads((plugin_dir / "manifest.json").read_text())["version"] == "2.0.0"
        _assert_local_files_kept(plugin_dir)


def _git(*args, cwd):
    subprocess.run(["git", "-c", "user.email=t@example.com", "-c", "user.name=t",
                    "-c", "core.autocrlf=false", *args],
                   cwd=cwd, check=True, capture_output=True)


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
class TestGitRepoUpdate:
    @pytest.fixture
    def cloned(self, store, tmp_path, monkeypatch):
        monkeypatch.setattr(store, "get_plugin_info", lambda *a, **k: None)
        upstream = tmp_path / "upstream"
        _write_release(upstream, "1.0.0")
        # This repo does NOT gitignore the token: an untracked, non-ignored
        # file is exactly what `git stash push -u` used to sweep away.
        (upstream / ".gitignore").write_text("cache/\n")
        _git("init", "-q", "-b", "main", cwd=upstream)
        _git("add", ".", cwd=upstream)
        _git("commit", "-qm", "1.0.0", cwd=upstream)

        plugin_dir = store.plugins_dir / PLUGIN_ID
        _git("clone", "-q", str(upstream), str(plugin_dir), cwd=tmp_path)

        def publish(version):
            (upstream / "manifest.json").write_text(json.dumps(_manifest(version)))
            _git("commit", "-qam", version, cwd=upstream)
        return store, plugin_dir, publish

    def test_pull_update_keeps_untracked_token(self, cloned):
        store, plugin_dir, publish = cloned
        _drop_local_files(plugin_dir)
        # An unrelated untracked file, so the update really does stash.
        (plugin_dir / "notes.txt").write_text("scratch")

        publish("1.1.0")
        assert store.update_plugin(PLUGIN_ID) is True

        assert json.loads((plugin_dir / "manifest.json").read_text())["version"] == "1.1.0"
        _assert_local_files_kept(plugin_dir)

    def test_token_alone_does_not_trigger_a_stash(self, cloned):
        store, plugin_dir, publish = cloned
        (plugin_dir / "token.pickle").write_bytes(b"tok")

        publish("1.1.0")
        assert store.update_plugin(PLUGIN_ID) is True

        assert (plugin_dir / "token.pickle").read_bytes() == b"tok"
        stashes = subprocess.run(["git", "-C", str(plugin_dir), "stash", "list"],
                                 capture_output=True, text=True, check=True)
        assert stashes.stdout.strip() == ""


class TestWhatIsKept:
    @pytest.mark.parametrize("path,expected", [
        ("token.pickle", True),
        ("data/session.pickle", True),
        ("credentials.json", True),
        ("token.json", True),
        ("config_secrets.json", True),
        (".pkce_code_verifier", True),
        ("manager.py", False),
        ("config.json", False),
    ])
    def test_known_state_files(self, path, expected):
        assert is_known_state_file(path) is expected

    def test_gitignore_rules(self, tmp_path):
        old, new = tmp_path / "old", tmp_path / "new"
        new.mkdir()
        for rel in ["a.log", "logs/x.txt", "sub/deep/b.log", "keep.log",
                    "anchored.txt", "sub/anchored.txt", "assets/x/y_backup/z.png",
                    "manager.py", "shipped.log"]:
            (old / rel).parent.mkdir(parents=True, exist_ok=True)
            (old / rel).write_text("x")
        (new / "shipped.log").write_text("new")
        (old / ".gitignore").write_text(
            "# comment\n*.log\n!keep.log\nlogs/\n/anchored.txt\n"
            "assets/**/*_backup/\n")

        assert local_files_to_keep(old, new) == [
            "a.log", "anchored.txt", "assets/x/y_backup/z.png",
            "logs/x.txt", "sub/deep/b.log",
        ]
