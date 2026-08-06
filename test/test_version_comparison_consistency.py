"""
Drift guard: the repo has FOUR version-comparison implementations, and they
do not agree. This file pins each one's answer on the same inputs so any
future change to one of them (or a fifth copy appearing) surfaces here.

The four:
1. src/plugin_system/compatibility.py  — parse_semver / tuple comparison
   (install gate).
2. web_interface/blueprints/api_v3.py  — _is_plugin_update_available, uses
   packaging.version (update badge in the UI).
3. src/plugin_system/store_manager.py  — update_plugin's raw STRING EQUALITY
   for monorepo plugins ("local_version == remote_version").
4. src/skin_system/skin_runtime.py     — _major, int(major) gate for the
   skin API.

SUSPECTED BUG (characterized here, not fixed): #3 disagrees with #2. For
"v1.2.0" vs "1.2.0" the UI says "no update available" while update_plugin
performs a full reinstall; for a locally-ahead plugin ("2.0.0" installed,
registry "1.9.0") the UI says no update but update_plugin DOWNGRADES via
reinstall. Unifying on one comparator is tracked follow-up work; when that
lands, the expectations in TestStoreManagerStringEquality flip and this
file is the reminder to update them deliberately.
"""

import json
from unittest.mock import patch

import pytest
from packaging.version import parse as pkg_parse

from src.plugin_system.compatibility import parse_semver
from src.skin_system.skin_runtime import _major
from src.plugin_system.store_manager import PluginStoreManager
from web_interface.blueprints.api_v3 import _is_plugin_update_available


# (installed, registry) pairs and what each comparator concludes.
CASES = [
    # pair                  parse_semver equal?   api_v3 update?   store equal-string?
    (("1.2.0", "1.2.0"),    True,                 False,           True),
    (("v1.2.0", "1.2.0"),   True,                 False,           False),
    (("1.2", "1.2.0"),      True,                 False,           False),
    (("1.2.0", "1.2.0-rc1"), True,                False,           False),
    (("1.2.0", "1.3.0"),    False,                True,            False),
    (("2.0.0", "1.9.0"),    False,                False,           False),
]


class TestComparatorMatrix:
    @pytest.mark.parametrize("pair,semver_equal,api_update,store_equal", CASES)
    def test_parse_semver_equality(self, pair, semver_equal, api_update, store_equal):
        a, b = pair
        assert (parse_semver(a) == parse_semver(b)) is semver_equal

    @pytest.mark.parametrize("pair,semver_equal,api_update,store_equal", CASES)
    def test_api_v3_update_available(self, pair, semver_equal, api_update, store_equal):
        installed, latest = pair
        assert _is_plugin_update_available(installed, latest) is api_update

    @pytest.mark.parametrize("pair,semver_equal,api_update,store_equal", CASES)
    def test_store_manager_string_equality(self, pair, semver_equal, api_update, store_equal):
        # The literal comparison update_plugin performs at its
        # "already at latest version" check.
        a, b = pair
        assert (a == b) is store_equal


class TestStoreManagerStringEquality:
    """Drive update_plugin's real code path to its version check."""

    def _store(self, tmp_path, local_version, registry_version):
        plugin_dir = tmp_path / "plugins" / "demo-plugin"
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "manifest.json").write_text(json.dumps({
            "id": "demo-plugin", "version": local_version,
        }))
        store = PluginStoreManager(
            plugins_dir=str(tmp_path / "plugins"),
            uninstalled_registry_path=str(tmp_path / "uninstalled.json"),
        )
        registry_info = {
            "id": "demo-plugin",
            "repo": "https://github.com/example/ledmatrix-plugins",
            "latest_version": registry_version,
        }
        return store, registry_info

    def _run_update(self, store, registry_info):
        with patch.object(store, "fetch_registry", return_value={"plugins": [registry_info]}), \
             patch.object(store, "get_plugin_info", return_value=registry_info), \
             patch.object(store, "_reinstall_with_rollback", return_value=True) as reinstall:
            result = store.update_plugin("demo-plugin")
        return result, reinstall

    def test_equal_strings_skip_reinstall(self, tmp_path):
        store, info = self._store(tmp_path, "1.2.0", "1.2.0")
        result, reinstall = self._run_update(store, info)
        assert result is True
        reinstall.assert_not_called()

    def test_v_prefix_triggers_reinstall_despite_semantic_equality(self, tmp_path):
        # SUSPECTED BUG: packaging (and api_v3) treat these as equal; the
        # string comparison does not, so the user gets a full reinstall.
        store, info = self._store(tmp_path, "v1.2.0", "1.2.0")
        result, reinstall = self._run_update(store, info)
        reinstall.assert_called_once()
        assert result is True

    def test_locally_ahead_version_triggers_downgrade_reinstall(self, tmp_path):
        # SUSPECTED BUG: a plugin ahead of the registry (local dev build) is
        # "updated" — i.e. downgraded — because inequality is the only test.
        store, info = self._store(tmp_path, "2.0.0", "1.9.0")
        result, reinstall = self._run_update(store, info)
        reinstall.assert_called_once()
        assert result is True


class TestSkinRuntimeMajor:
    def test_plain_versions(self):
        assert _major("1.0.0") == 1
        assert _major("2.1") == 2

    def test_int_input_tolerated(self):
        assert _major(2) == 2

    def test_garbage_returns_none(self):
        assert _major("garbage") is None
        assert _major(None) is None

    def test_v_prefix_not_tolerated(self):
        # Unlike parse_semver, _major does NOT strip a leading 'v' —
        # a skin.json declaring "v1.0.0" fails the API gate. Characterized
        # so a manifest-format loosening elsewhere doesn't silently diverge.
        assert _major("v1.0.0") is None


class TestParseSemverAgreesWithPackaging:
    """parse_semver and packaging must agree on ordering for plain X.Y.Z —
    the region where the two ecosystems overlap and must never diverge."""

    PLAIN = ["0.1.0", "1.0.0", "1.2.0", "1.2.3", "1.10.0", "2.0.0", "10.0.1"]

    def test_pairwise_ordering_matches(self):
        for a in self.PLAIN:
            for b in self.PLAIN:
                ours = parse_semver(a) < parse_semver(b)
                theirs = pkg_parse(a) < pkg_parse(b)
                assert ours == theirs, f"ordering diverges on ({a}, {b})"
