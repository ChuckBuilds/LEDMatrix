"""
Drift guard for version comparison.

There is now ONE shared "should this plugin update?" comparator —
`src.plugin_system.compatibility.is_update_available` — used by both the web
UI's update badge (`api_v3._is_plugin_update_available`) and the store's
`update_plugin` reinstall decision, so the badge and the actual reinstall
can never disagree. (Historically the store used raw string equality, which
reinstalled over cosmetic differences like "v1.2.0" vs "1.2.0" and even
DOWNGRADED locally-ahead plugins; this file's tests killed that.)

Two other version parsers legitimately remain and are pinned here so they
don't drift: `compatibility.parse_semver` (the install-compatibility gate,
range-spec oriented) and `skin_runtime._major` (skin API major gate).
"""

import json
from unittest.mock import patch

import pytest
from packaging.version import parse as pkg_parse

from src.plugin_system.compatibility import is_update_available, parse_semver
from src.skin_system.skin_runtime import _major
from src.plugin_system.store_manager import PluginStoreManager
from web_interface.blueprints.api_v3 import _is_plugin_update_available


# (installed, registry) -> update available?
CASES = [
    (("1.2.0", "1.2.0"), False),   # identical
    (("v1.2.0", "1.2.0"), False),  # cosmetic v-prefix, semantically equal
    (("1.2", "1.2.0"), False),     # short form, semantically equal
    (("1.2.0", "1.2.0-rc1"), False),  # rc of same release is not newer
    (("1.2.0", "1.3.0"), True),    # registry genuinely newer
    (("2.0.0", "1.9.0"), False),   # locally ahead — never downgrade
    (("abc.def", "1.0.0"), True),  # unparseable — surface the mismatch
    (("", "1.0.0"), False),        # missing either side — nothing to do
    (("1.0.0", ""), False),
]


class TestSharedComparator:
    @pytest.mark.parametrize("pair,expected", CASES)
    def test_is_update_available(self, pair, expected):
        installed, latest = pair
        assert is_update_available(installed, latest) is expected

    @pytest.mark.parametrize("pair,expected", CASES)
    def test_api_v3_helper_agrees(self, pair, expected):
        # The UI badge helper must be a pure alias of the shared comparator.
        installed, latest = pair
        assert _is_plugin_update_available(installed, latest) is expected


class TestStoreManagerUsesSharedComparator:
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

    def test_v_prefix_equivalent_skips_reinstall(self, tmp_path):
        # "v1.2.0" == "1.2.0" semantically — no pointless reinstall.
        store, info = self._store(tmp_path, "v1.2.0", "1.2.0")
        result, reinstall = self._run_update(store, info)
        assert result is True
        reinstall.assert_not_called()

    def test_locally_ahead_version_is_never_downgraded(self, tmp_path):
        # A plugin ahead of the registry (local dev build) must not be
        # "updated" — that would be a downgrade.
        store, info = self._store(tmp_path, "2.0.0", "1.9.0")
        result, reinstall = self._run_update(store, info)
        assert result is True
        reinstall.assert_not_called()

    def test_registry_newer_triggers_reinstall(self, tmp_path):
        store, info = self._store(tmp_path, "1.2.0", "1.3.0")
        result, reinstall = self._run_update(store, info)
        reinstall.assert_called_once()
        assert result is True

    def test_unparseable_version_surfaces_via_reinstall(self, tmp_path):
        # Direction unknowable → reconcile by reinstalling from the registry.
        store, info = self._store(tmp_path, "abc.def", "1.0.0")
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
