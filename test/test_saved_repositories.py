"""
Tests for src/plugin_system/saved_repositories.py — pins the
SavedRepositoriesManager contract.

Covers: the three accepted on-disk load shapes (bare list, wrapped
{"repositories": [...]}, anything else -> []) and that saves always write
the bare-list form; add/remove/has round trips through a fresh manager;
URL normalization post-fix (_clean_url strips only a TRAILING '.git' after
trailing slashes — the old unanchored .replace('.git', '') mangled URLs
like my.github.io); name derivation and registry-vs-single type
classification (the ledmatrix-plugins check is lowercased, the
plugins.json check is case-sensitive); and the post-fix rollback of the
in-memory list when _save_repositories() fails, so memory never diverges
from disk.
"""

import json

from src.plugin_system.saved_repositories import SavedRepositoriesManager


def make_manager(path):
    return SavedRepositoriesManager(config_path=str(path))


class TestLoading:
    def test_missing_file_empty_and_not_created(self, tmp_path):
        path = tmp_path / "repos.json"
        manager = make_manager(path)
        assert manager.get_all() == []
        assert not path.exists()

    def test_bare_list_shape(self, tmp_path):
        path = tmp_path / "repos.json"
        entries = [{'url': 'https://github.com/u/r', 'name': 'r', 'type': 'single'}]
        path.write_text(json.dumps(entries))
        assert make_manager(path).get_all() == entries

    def test_wrapped_dict_shape(self, tmp_path):
        path = tmp_path / "repos.json"
        entries = [{'url': 'https://github.com/u/r', 'name': 'r', 'type': 'single'}]
        path.write_text(json.dumps({'repositories': entries}))
        assert make_manager(path).get_all() == entries

    def test_other_shape_yields_empty(self, tmp_path):
        path = tmp_path / "repos.json"
        path.write_text(json.dumps({'x': 1}))
        assert make_manager(path).get_all() == []

    def test_malformed_json_yields_empty_no_raise(self, tmp_path):
        path = tmp_path / "repos.json"
        path.write_text("not json {{")
        assert make_manager(path).get_all() == []


class TestSaveFormat:
    def test_save_always_writes_bare_list(self, tmp_path):
        # Even when loaded from the wrapped {"repositories": [...]} form,
        # the next save normalizes the file to a bare JSON list.
        path = tmp_path / "repos.json"
        entries = [{'url': 'https://github.com/u/r', 'name': 'r', 'type': 'single'}]
        path.write_text(json.dumps({'repositories': entries}))
        manager = make_manager(path)
        assert manager.add("https://github.com/u/r2") is True
        on_disk = json.loads(path.read_text())
        assert isinstance(on_disk, list)
        assert len(on_disk) == 2


class TestAdd:
    def test_round_trip_creates_parents_and_reloads(self, tmp_path):
        path = tmp_path / "sub" / "repos.json"
        manager = make_manager(path)
        assert manager.add("https://github.com/user/repo") is True
        assert path.exists()
        entry = manager.get_all()[0]
        assert entry['url'] == "https://github.com/user/repo"
        assert entry['name'] == "repo"
        assert entry['type'] == "single"
        # A fresh manager on the same path sees the persisted entry.
        fresh = make_manager(path)
        assert fresh.get_all() == [entry]

    def test_duplicate_returns_false_file_unchanged(self, tmp_path):
        path = tmp_path / "repos.json"
        manager = make_manager(path)
        assert manager.add("https://github.com/user/repo") is True
        before = path.read_text()
        assert manager.add("https://github.com/user/repo") is False
        assert path.read_text() == before
        assert len(manager.get_all()) == 1

    def test_trailing_git_and_slash_stripped(self, tmp_path):
        manager = make_manager(tmp_path / "repos.json")
        assert manager.add("https://github.com/user/repo.git/") is True
        assert manager.get_all()[0]['url'] == "https://github.com/user/repo"

    def test_interior_dot_git_not_mangled(self, tmp_path):
        # Regression for the old unanchored .replace('.git', ''): a URL
        # merely CONTAINING '.git' must be stored verbatim.
        manager = make_manager(tmp_path / "repos.json")
        url = "https://github.com/user/my.github.io"
        assert manager.add(url) is True
        assert manager.get_all()[0]['url'] == url


class TestNameExtraction:
    def test_name_derived_from_last_path_segment(self, tmp_path):
        manager = make_manager(tmp_path / "repos.json")
        manager.add("https://github.com/user/football-scoreboard")
        assert manager.get_all()[0]['name'] == "football-scoreboard"

    def test_explicit_name_preserved(self, tmp_path):
        manager = make_manager(tmp_path / "repos.json")
        manager.add("https://github.com/user/repo", name="My Repo")
        assert manager.get_all()[0]['name'] == "My Repo"

    def test_url_without_slash_uses_whole_url(self, tmp_path):
        manager = make_manager(tmp_path / "repos.json")
        manager.add("standalone")
        assert manager.get_all()[0]['name'] == "standalone"


class TestTypeClassification:
    def _type_of(self, tmp_path, url):
        manager = make_manager(tmp_path / "repos.json")
        assert manager.add(url) is True
        return manager.get_all()[0]['type']

    def test_plugins_json_url_is_registry(self, tmp_path):
        url = "https://raw.githubusercontent.com/x/main/plugins.json"
        assert self._type_of(tmp_path, url) == "registry"

    def test_ledmatrix_plugins_check_is_case_insensitive(self, tmp_path):
        url = "https://github.com/ChuckBuilds/LEDMATRIX-PLUGINS"
        assert self._type_of(tmp_path, url) == "registry"

    def test_plugins_json_check_is_case_sensitive(self, tmp_path):
        # Only the 'ledmatrix-plugins' check is lowercased; the
        # 'plugins.json' substring check is case-sensitive. Pinned.
        url = "https://example.com/PLUGINS.JSON"
        assert self._type_of(tmp_path, url) == "single"

    def test_plain_repo_is_single(self, tmp_path):
        assert self._type_of(tmp_path, "https://github.com/user/repo") == "single"

    def test_get_registry_repositories_filters(self, tmp_path):
        manager = make_manager(tmp_path / "repos.json")
        manager.add("https://github.com/user/repo")
        manager.add("https://raw.githubusercontent.com/x/main/plugins.json")
        registries = manager.get_registry_repositories()
        assert len(registries) == 1
        assert registries[0]['type'] == "registry"


class TestRemove:
    def test_remove_present_persists(self, tmp_path):
        path = tmp_path / "repos.json"
        manager = make_manager(path)
        manager.add("https://github.com/user/repo")
        assert manager.remove("https://github.com/user/repo") is True
        assert manager.get_all() == []
        assert make_manager(path).get_all() == []

    def test_remove_absent_false_no_write(self, tmp_path):
        path = tmp_path / "repos.json"
        manager = make_manager(path)
        manager.add("https://github.com/user/repo")
        before = path.read_text()
        assert manager.remove("https://github.com/user/other") is False
        assert path.read_text() == before

    def test_remove_with_dirty_url_matches_clean_stored(self, tmp_path):
        manager = make_manager(tmp_path / "repos.json")
        manager.add("https://github.com/user/repo")
        assert manager.remove("https://github.com/user/repo.git/") is True
        assert manager.get_all() == []


class TestHas:
    def test_has_applies_url_cleaning(self, tmp_path):
        manager = make_manager(tmp_path / "repos.json")
        manager.add("https://x/y")
        assert manager.has("https://x/y.git/") is True
        assert manager.has("https://x/z") is False


class TestSaveFailureRollback:
    def test_add_rolls_back_on_save_failure(self, tmp_path, monkeypatch):
        # Post-fix: a failed save must not leave a phantom in-memory entry.
        path = tmp_path / "repos.json"
        manager = make_manager(path)
        monkeypatch.setattr(manager, "_save_repositories", lambda: False)
        assert manager.add("https://github.com/user/repo") is False
        assert manager.get_all() == []
        assert not path.exists()

    def test_remove_rolls_back_on_save_failure(self, tmp_path, monkeypatch):
        path = tmp_path / "repos.json"
        manager = make_manager(path)
        manager.add("https://github.com/user/repo")  # real save
        monkeypatch.setattr(manager, "_save_repositories", lambda: False)
        assert manager.remove("https://github.com/user/repo") is False
        assert manager.get_all() == [
            {'url': 'https://github.com/user/repo', 'name': 'repo', 'type': 'single'}
        ]
        # Disk still has the entry too — memory and disk stay in sync.
        assert len(json.loads(path.read_text())) == 1


class TestGetAllCopy:
    def test_get_all_is_shallow_copy(self, tmp_path):
        # Characterization: get_all() copies the LIST but not the entry
        # dicts, so mutating a returned entry mutates internal state.
        # Appending to the returned list, however, does not. Do not "fix"
        # without auditing callers that rely on list-copy semantics.
        manager = make_manager(tmp_path / "repos.json")
        manager.add("https://github.com/user/repo")
        returned = manager.get_all()
        returned.append({'url': 'x'})
        assert len(manager.get_all()) == 1  # list itself is copied
        manager.get_all()[0]['name'] = 'hacked'
        assert manager.get_all()[0]['name'] == 'hacked'  # dicts are shared
