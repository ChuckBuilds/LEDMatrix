"""The registry fields added after 3.7.0: ``ledmatrix_min_version``,
``aliases`` and ``commit``.

- ``ledmatrix_min_version`` lets the store refuse an install or update this
  core cannot run *before* downloading anything. The gate on the downloaded
  manifest stays as the fallback.
- ``aliases`` (or, for a registry without it, the ``plugin_path`` name and the
  ``ledmatrix-`` prefix) lets update, uninstall and reinstall find a plugin
  installed under its manifest id: registry ``weather`` lives in
  ``ledmatrix-weather/``. Before this, update by the registry id said "not
  installed", uninstall by it reported success and deleted nothing, and a
  reinstall backed up ``weather/`` -- which did not exist -- then deleted
  ``ledmatrix-weather/`` to make room, so a post-download refusal left the user
  with no plugin at all.
- ``commit`` is shown in the store; nothing installs from it.

Every field is optional: an older plugins.json must behave exactly as before.
"""

import json
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import pytest

from src.plugin_system import compatibility
from src.plugin_system.store_manager import PluginStoreManager
from src.plugin_system.store_registry import alternate_ids, declared_aliases
from test._api_v3_test_helpers import api_v3_client, api_v3_module  # noqa: F401

REPO = "https://github.com/ChuckBuilds/ledmatrix-plugins"
CORE = "3.7.0"

# Shaped like the real registry before and after the monorepo change.
OLD_WEATHER = {"id": "weather", "name": "Weather", "repo": REPO, "branch": "main",
               "plugin_path": "plugins/ledmatrix-weather", "latest_version": "2.0.0"}
NEW_WEATHER = {**OLD_WEATHER, "ledmatrix_min_version": "3.5.0",
               "aliases": ["ledmatrix-weather"],
               "commit": "843588025a81197056f8d96779ccb2be19337ab8"}


def manifest(version: str, floor: str = "3.0.0", plugin_id: str = "ledmatrix-weather") -> Dict[str, Any]:
    return {"id": plugin_id, "name": "Weather", "class_name": "Weather",
            "display_modes": ["weather"], "version": version,
            "versions": [{"version": version, "ledmatrix_min_version": floor}]}


def write_install(plugins_dir: Path, name: str, content: Dict[str, Any], marker: str) -> Path:
    path = plugins_dir / name
    path.mkdir(parents=True)
    (path / "manifest.json").write_text(json.dumps(content), encoding="utf-8")
    (path / "marker.txt").write_text(marker, encoding="utf-8")
    return path


def markers(plugins_dir: Path) -> Dict[str, Optional[str]]:
    out = {}
    for d in sorted(plugins_dir.iterdir()):
        m = d / "marker.txt"
        out[d.name] = m.read_text(encoding="utf-8") if m.exists() else None
    return out


class FakeStore:
    """A PluginStoreManager with the registry and the download faked."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                 entries: List[Dict[str, Any]], downloaded: Dict[str, Any]):
        self.plugins_dir = tmp_path / "plugin-repos"
        self.plugins_dir.mkdir()
        self.store = PluginStoreManager(plugins_dir=str(self.plugins_dir))
        self.store.logger = MagicMock()
        self.downloads: List[Path] = []
        # Tests may replace registry["plugins"]; the fakes read it per call.
        self.registry: Dict[str, Any] = {"plugins": entries}
        store = self.store

        def fetch_registry(*_a, **_k):
            store.registry_cache = self.registry
            return self.registry

        def get_plugin_info(pid, **_k):
            entry = store._match_registry_entry(fetch_registry()["plugins"], pid)
            return dict(entry) if entry else None

        monkeypatch.setattr(store, "fetch_registry", fetch_registry)
        monkeypatch.setattr(store, "get_plugin_info", get_plugin_info)
        monkeypatch.setattr(store, "_install_dependencies", lambda _p: True)

        def fake_download(_url, _subpath, target: Path) -> bool:
            self.downloads.append(target)
            target.mkdir(parents=True, exist_ok=True)
            (target / "manifest.json").write_text(json.dumps(downloaded), encoding="utf-8")
            (target / "marker.txt").write_text("new", encoding="utf-8")
            return True

        monkeypatch.setattr(store, "_install_from_monorepo", fake_download)
        monkeypatch.setattr(compatibility, "current_core_version", lambda: CORE)


@pytest.fixture
def make(tmp_path, monkeypatch):
    def _make(entries, downloaded=None):
        return FakeStore(tmp_path, monkeypatch, entries, downloaded or manifest("2.0.0"))
    return _make


class TestAliasHelpers:
    def test_declared_aliases_distinguishes_absent_from_empty(self):
        assert declared_aliases(OLD_WEATHER) is None
        assert declared_aliases({**OLD_WEATHER, "aliases": []}) == []
        assert declared_aliases(NEW_WEATHER) == ["ledmatrix-weather"]

    def test_declared_aliases_drops_junk_and_the_own_id(self):
        assert declared_aliases({"id": "a", "aliases": ["a", "", 3, None, "b"]}) == ["b"]
        assert declared_aliases({"id": "a", "aliases": "b"}) is None

    def test_only_registry_proof_counts(self):
        """aliases and the plugin_path name; never a bare ledmatrix-<id>."""
        assert alternate_ids(OLD_WEATHER) == ["ledmatrix-weather"]
        assert alternate_ids(NEW_WEATHER) == ["ledmatrix-weather"]
        assert alternate_ids({"id": "clock", "plugin_path": "plugins/clock-simple"}) == ["clock-simple"]
        assert alternate_ids({"id": "ext", "plugin_path": ""}) == []
        assert alternate_ids({"id": "foo", "plugin_path": "plugins/foo", "aliases": []}) == []
        assert alternate_ids({"id": "m", "plugin_path": "plugins/m", "aliases": ["x"]}) == ["x"]


class TestRegistryLookup:
    def test_alias_resolves_an_entry_whose_path_does_not_say_so(self):
        entry = {"id": "music", "plugin_path": "plugins/music", "aliases": ["ledmatrix-music"]}
        assert PluginStoreManager._match_registry_entry([entry], "ledmatrix-music") is entry

    def test_exact_id_still_beats_an_alias(self):
        decoy = {"id": "decoy", "aliases": ["weather"]}
        real = {"id": "weather"}
        assert PluginStoreManager._match_registry_entry([decoy, real], "weather") is real


class TestFindInstalledPlugin:
    def test_registry_id_finds_the_manifest_id_install(self, make):
        fake = make([NEW_WEATHER])
        path = write_install(fake.plugins_dir, "ledmatrix-weather", manifest("1.0.0"), "old")
        fake.store.fetch_registry()
        assert fake.store._find_plugin_path("weather") == path

    def test_old_registry_still_finds_it(self, make):
        fake = make([OLD_WEATHER])
        path = write_install(fake.plugins_dir, "ledmatrix-weather", manifest("1.0.0"), "old")
        fake.store.fetch_registry()
        assert fake.store._find_plugin_path("weather") == path

    def test_no_registry_loaded_is_no_proof(self, make):
        """Offline, a ledmatrix-weather/ folder is only named in the log."""
        fake = make([])
        path = write_install(fake.plugins_dir, "ledmatrix-weather", manifest("1.0.0"), "old")
        assert fake.store.registry_cache is None
        assert fake.store._find_plugin_path("weather") is None
        hint = " ".join(str(a) for c in fake.store.logger.warning.call_args_list for a in c.args)
        assert str(path) in hint and "ledmatrix-weather" in hint

    def test_a_folder_whose_manifest_declares_the_id_needs_no_proof(self, make):
        fake = make([])
        path = write_install(fake.plugins_dir, "ledmatrix-weather",
                             manifest("1.0.0", plugin_id="weather"), "old")
        assert fake.store._find_plugin_path("weather") == path

    def test_an_empty_aliases_list_means_no_guessing(self, make):
        fake = make([{"id": "foo", "repo": REPO, "plugin_path": "plugins/foo", "aliases": []}])
        write_install(fake.plugins_dir, "ledmatrix-foo", manifest("1.0.0", plugin_id="ledmatrix-foo"), "x")
        fake.store.fetch_registry()
        assert fake.store._find_plugin_path("foo") is None

    def test_the_exact_install_wins_over_an_alias(self, make):
        fake = make([NEW_WEATHER])
        exact = write_install(fake.plugins_dir, "weather", manifest("1.0.0", plugin_id="weather"), "a")
        write_install(fake.plugins_dir, "ledmatrix-weather", manifest("1.0.0"), "b")
        fake.store.fetch_registry()
        assert fake.store._find_plugin_path("weather") == exact

    def test_uninstall_by_registry_id_removes_the_install(self, make):
        fake = make([NEW_WEATHER])
        write_install(fake.plugins_dir, "ledmatrix-weather", manifest("1.0.0"), "old")
        fake.store.fetch_registry()
        assert fake.store.uninstall_plugin("weather") is True
        assert markers(fake.plugins_dir) == {}


# An unrelated plugin that happens to live in ledmatrix-foo/ (its manifest id
# is ledmatrix-foo) and that no registry entry names. Owner decision on #686:
# without registry proof the store must never replace or remove it.
FOO = {"id": "foo", "name": "Foo", "repo": REPO, "branch": "main", "plugin_path": "plugins/foo",
       "latest_version": "2.0.0"}


class TestUnprovenPrefixFolderIsLeftAlone:
    @pytest.fixture(params=["old registry", "empty aliases", "no registry"])
    def fake(self, request, make):
        entries = {"old registry": [FOO], "empty aliases": [{**FOO, "aliases": []}],
                   "no registry": []}[request.param]
        fake = make(entries, downloaded=manifest("2.0.0", plugin_id="foo"))
        write_install(fake.plugins_dir, "ledmatrix-foo",
                      manifest("1.0.0", plugin_id="ledmatrix-foo"), "unrelated")
        return fake

    def test_uninstall_foo_leaves_it(self, fake):
        if fake.registry["plugins"]:
            fake.store.fetch_registry()
        assert fake.store.uninstall_plugin("foo") is True  # "already uninstalled"
        assert markers(fake.plugins_dir) == {"ledmatrix-foo": "unrelated"}

    def test_install_foo_does_not_replace_it(self, fake):
        if fake.registry["plugins"]:
            fake.store.fetch_registry()
        else:
            # Nothing loaded when install_plugin looks for a copy to protect;
            # the entry to install from is fetched afterwards.
            fake.registry["plugins"] = [FOO]
        assert fake.store.install_plugin("foo") is True
        assert markers(fake.plugins_dir) == {"foo": "new", "ledmatrix-foo": "unrelated"}

    def test_update_foo_finds_nothing_to_update(self, fake):
        if fake.registry["plugins"]:
            fake.store.fetch_registry()
        assert fake.store.update_plugin("foo") is False
        assert markers(fake.plugins_dir) == {"ledmatrix-foo": "unrelated"}
        assert fake.downloads == []


class TestInstall:
    def test_registry_floor_refuses_before_downloading(self, make):
        fake = make([{**NEW_WEATHER, "ledmatrix_min_version": "9.0.0"}])
        write_install(fake.plugins_dir, "ledmatrix-weather", manifest("1.0.0"), "old")
        assert fake.store.install_plugin("weather") is False
        assert fake.downloads == [], "nothing may be downloaded for a refused install"
        assert markers(fake.plugins_dir) == {"ledmatrix-weather": "old"}
        reason = fake.store.pop_refusal("weather")
        assert reason and "requires LEDMatrix 9.0.0 or newer" in reason and CORE in reason
        assert fake.store.pop_refusal("weather") is None, "a refusal is reported once"

    def test_a_compatible_floor_installs(self, make):
        fake = make([NEW_WEATHER])
        assert fake.store.install_plugin("weather") is True
        assert markers(fake.plugins_dir) == {"ledmatrix-weather": "new"}

    def test_old_registry_downloads_and_the_manifest_gate_still_refuses(self, make):
        """No field means no early answer: the post-download gate decides,
        and the copy it would have replaced -- found under its alias -- is put
        back. This used to delete ledmatrix-weather/ outright."""
        fake = make([OLD_WEATHER], downloaded=manifest("2.0.0", floor="9.0.0"))
        write_install(fake.plugins_dir, "ledmatrix-weather", manifest("1.0.0"), "old")
        assert fake.store.install_plugin("weather") is False
        assert len(fake.downloads) == 1
        assert markers(fake.plugins_dir) == {"ledmatrix-weather": "old"}
        assert "9.0.0" in (fake.store.pop_refusal("weather") or "")

    def test_old_registry_reinstall_over_an_alias_succeeds(self, make):
        fake = make([OLD_WEATHER])
        write_install(fake.plugins_dir, "ledmatrix-weather", manifest("1.0.0"), "old")
        assert fake.store.install_plugin("weather") is True
        assert markers(fake.plugins_dir) == {"ledmatrix-weather": "new"}

    def test_another_branch_skips_the_registry_floor(self, make):
        """The floor describes the registry's branch; a named other branch is
        another release, left to the post-download gate."""
        fake = make([{**NEW_WEATHER, "ledmatrix_min_version": "9.0.0"}])
        assert fake.store.install_plugin("weather", branch="dev") is True
        assert len(fake.downloads) == 1

    @pytest.mark.parametrize("floor", [None, "", 3, ["9.0.0"]])
    def test_a_missing_or_malformed_floor_says_nothing(self, make, floor):
        entry = {**NEW_WEATHER, "ledmatrix_min_version": floor}
        assert make([entry]).store.registry_incompatibility("weather", entry) is None

    def test_an_untrustworthy_core_version_is_not_refused(self, make, monkeypatch):
        """Same leniency as the manifest gate: a core reporting 1.0.0 (the
        v3.1.0 release) is unknown, not old -- unless the floor is above 2.0.0,
        which that release cannot meet either."""
        entry = {**NEW_WEATHER, "ledmatrix_min_version": "2.0.0"}
        store = make([entry]).store
        monkeypatch.setattr(compatibility, "current_core_version", lambda: "1.0.0")
        assert store.registry_incompatibility("weather", entry) is None


class TestUpdate:
    def test_update_by_registry_id_finds_and_updates_the_install(self, make):
        fake = make([NEW_WEATHER])
        write_install(fake.plugins_dir, "ledmatrix-weather", manifest("1.0.0"), "old")
        assert fake.store.update_plugin("weather") is True
        assert markers(fake.plugins_dir) == {"ledmatrix-weather": "new"}

    def test_update_by_manifest_id_still_works(self, make):
        fake = make([NEW_WEATHER])
        write_install(fake.plugins_dir, "ledmatrix-weather", manifest("1.0.0"), "old")
        assert fake.store.update_plugin("ledmatrix-weather") is True
        assert markers(fake.plugins_dir) == {"ledmatrix-weather": "new"}

    def test_incompatible_update_is_refused_before_anything_moves(self, make, monkeypatch):
        fake = make([{**NEW_WEATHER, "ledmatrix_min_version": "9.0.0"}])
        write_install(fake.plugins_dir, "ledmatrix-weather", manifest("1.0.0"), "old")
        rollback = MagicMock()
        monkeypatch.setattr(fake.store, "_reinstall_with_rollback", rollback)
        assert fake.store.update_plugin("ledmatrix-weather") is False
        rollback.assert_not_called()
        assert fake.downloads == []
        assert markers(fake.plugins_dir) == {"ledmatrix-weather": "old"}
        assert "9.0.0" in (fake.store.pop_refusal("ledmatrix-weather") or "")

    def test_up_to_date_plugin_is_not_refused(self, make):
        """The floor belongs to latest_version; a plugin already on it is
        running, so there is nothing to refuse."""
        fake = make([{**NEW_WEATHER, "ledmatrix_min_version": "9.0.0"}])
        write_install(fake.plugins_dir, "ledmatrix-weather", manifest("2.0.0"), "current")
        assert fake.store.update_plugin("weather") is True
        assert fake.store.pop_refusal("weather") is None

    def test_old_registry_update_falls_back_to_the_manifest_gate(self, make):
        fake = make([OLD_WEATHER], downloaded=manifest("2.0.0", floor="9.0.0"))
        write_install(fake.plugins_dir, "ledmatrix-weather", manifest("1.0.0"), "old")
        assert fake.store.update_plugin("weather") is False
        assert len(fake.downloads) == 1
        assert markers(fake.plugins_dir) == {"ledmatrix-weather": "old"}

    def test_git_checkout_is_not_pulled_when_the_registry_refuses(self, make, monkeypatch):
        fake = make([{**NEW_WEATHER, "ledmatrix_min_version": "9.0.0"}])
        write_install(fake.plugins_dir, "ledmatrix-weather", manifest("1.0.0"), "old")
        monkeypatch.setattr(fake.store, "_get_local_git_info", lambda _p: {
            "sha": "a" * 40, "branch": "main", "remote_url": REPO})
        calls = []
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: calls.append(a) or MagicMock(
            returncode=0, stdout="", stderr=""))
        assert fake.store.update_plugin("weather") is False
        assert not any("pull" in (a[0] if a else []) for a in calls), calls
        assert "9.0.0" in (fake.store.pop_refusal("weather") or "")


# -- web routes --------------------------------------------------------------

@pytest.fixture
def web_store(api_v3_module, tmp_path, monkeypatch):
    fake = FakeStore(tmp_path, monkeypatch, [], manifest("2.0.0"))
    api_v3_module.api_v3.plugin_store_manager = fake.store
    api_v3_module.api_v3.operation_queue = None
    # No plugin manager: the routes skip discovery and reload, and the update
    # route finds the directory through the store alone.
    api_v3_module.api_v3.plugin_manager = None
    return fake


def test_store_list_carries_the_new_fields(api_v3_client, web_store, monkeypatch):
    listing = [
        dict(NEW_WEATHER),
        {**NEW_WEATHER, "id": "later", "aliases": [], "ledmatrix_min_version": "9.0.0"},
        {"id": "old", "name": "Old", "repo": REPO, "plugin_path": "plugins/old"},
    ]
    monkeypatch.setattr(web_store.store, "search_plugins", lambda **_k: listing)
    resp = api_v3_client.get("/api/v3/plugins/store/list")
    assert resp.status_code == 200
    by_id = {p["id"]: p for p in resp.get_json()["data"]["plugins"]}
    assert by_id["weather"]["commit"] == NEW_WEATHER["commit"]
    assert by_id["weather"]["ledmatrix_min_version"] == "3.5.0"
    assert by_id["weather"]["aliases"] == ["ledmatrix-weather"]
    assert by_id["weather"]["incompatible_reason"] is None
    assert "9.0.0" in by_id["later"]["incompatible_reason"]
    # An older registry: every new key present, and empty.
    assert (by_id["old"]["commit"], by_id["old"]["ledmatrix_min_version"],
            by_id["old"]["aliases"], by_id["old"]["incompatible_reason"]) == (None, None, [], None)


def test_install_route_says_why_it_refused(api_v3_client, web_store):
    web_store.registry["plugins"] = [{**NEW_WEATHER, "ledmatrix_min_version": "9.0.0"}]
    resp = api_v3_client.post("/api/v3/plugins/install", data=json.dumps({"plugin_id": "weather"}),
                              content_type="application/json")
    assert resp.status_code == 409
    message = resp.get_json()["message"]
    assert "requires LEDMatrix 9.0.0 or newer" in message
    assert web_store.downloads == []


def test_update_route_says_why_it_refused(api_v3_client, web_store):
    web_store.registry["plugins"] = [{**NEW_WEATHER, "ledmatrix_min_version": "9.0.0"}]
    write_install(web_store.plugins_dir, "ledmatrix-weather", manifest("1.0.0"), "old")
    resp = api_v3_client.post("/api/v3/plugins/update", data=json.dumps({"plugin_id": "weather"}),
                              content_type="application/json")
    assert resp.status_code == 409
    assert "requires LEDMatrix 9.0.0 or newer" in resp.get_json()["message"]
    assert markers(web_store.plugins_dir) == {"ledmatrix-weather": "old"}
