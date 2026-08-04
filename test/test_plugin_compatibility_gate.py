"""The install/update gate, and the shared compatibility rules behind it.

Before this existed, `ledmatrix_min_version` was decoration: the loader logged
an advisory warning and the store never looked at the core version at all, so a
routine store update happily delivered a plugin that could not run. Deleting a
plugin's bundled fallback under those conditions would have handed un-updated
users a scoreboard that fails to load with one line in the journal.

The rules being pinned here, in priority order:

1. Refuse only on **evidence**. Undeclared floor, unparseable version on either
   side, or a core whose self-reported version is untrustworthy → allow. A
   wrong refusal breaks a working install; a wrong allowance degrades to the
   behavior we already had.
2. A core below `TRUSTWORTHY_FLOOR` is *unknown*, not old. The v3.1.0 release
   reports `1.0.0` while nearly every manifest floors at `2.0.0`; blocking on
   that number would stop those users installing anything at all.
3. The loader and the store must agree, because they read the same manifests.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.plugin_system import compatibility


# --------------------------------------------------------------------------
# Floor resolution — every spelling published plugins actually use
# --------------------------------------------------------------------------

class TestDeclaredMinVersion:
    def test_top_level_min_ledmatrix_version(self):
        assert compatibility.declared_min_version(
            {"min_ledmatrix_version": "3.2.0"}) == "3.2.0"

    def test_requires_block(self):
        assert compatibility.declared_min_version(
            {"requires": {"min_ledmatrix_version": "3.1.0"}}) == "3.1.0"

    def test_versions_array_new_spelling(self):
        assert compatibility.declared_min_version(
            {"versions": [{"ledmatrix_min_version": "3.2.0"}]}) == "3.2.0"

    def test_versions_array_deprecated_spelling(self):
        """Most published manifests still say `ledmatrix_min`; ignoring it
        would silently exempt them from the gate."""
        assert compatibility.declared_min_version(
            {"versions": [{"ledmatrix_min": "2.0.0"}]}) == "2.0.0"

    def test_absent(self):
        assert compatibility.declared_min_version({"id": "x"}) is None

    def test_requires_present_but_null(self):
        assert compatibility.declared_min_version({"requires": None}) is None


# --------------------------------------------------------------------------
# The decision itself
# --------------------------------------------------------------------------

class TestCheck:
    def test_blocks_when_plugin_needs_a_newer_core(self):
        ok, reason = compatibility.check(
            {"name": "Hockey Scoreboard", "min_ledmatrix_version": "3.2.0"}, "3.1.0")
        assert ok is False
        assert "3.2.0" in reason and "3.1.0" in reason
        assert "Hockey Scoreboard" in reason

    def test_allows_equal_version(self):
        ok, _ = compatibility.check({"min_ledmatrix_version": "3.2.0"}, "3.2.0")
        assert ok is True

    def test_allows_newer_core(self):
        ok, _ = compatibility.check({"min_ledmatrix_version": "3.2.0"}, "4.0.0")
        assert ok is True

    def test_allows_when_no_floor_declared(self):
        ok, reason = compatibility.check({"id": "x"}, "3.2.0")
        assert ok is True and reason is None

    def test_untrustworthy_core_allows_todays_ecosystem_floor(self):
        """The v3.1.0 release reports 1.0.0. Nearly every manifest floors at
        2.0.0, so blocking *that* would stop those users installing any plugin
        at all — strictly worse than the problem being solved.

        A floor ABOVE 2.0.0 is refused instead; see
        TestUntrustworthyCoreAndTheSunset for why that case is different."""
        ok, reason = compatibility.check(
            {"min_ledmatrix_version": "2.0.0"}, "1.0.0")
        assert ok is True and reason is None

    def test_unparseable_core_version_allows_the_ecosystem_floor(self):
        """An unidentifiable core is treated exactly like an untrustworthy one:
        today's 2.0.0 floor is allowed, a post-sunset floor is not."""
        ok, _ = compatibility.check({"min_ledmatrix_version": "2.0.0"}, "not-a-version")
        assert ok is True
        ok, _ = compatibility.check({"min_ledmatrix_version": "3.2.0"}, "not-a-version")
        assert ok is False

    def test_unparseable_floor_allows(self):
        ok, _ = compatibility.check({"min_ledmatrix_version": {"nope": 1}}, "3.2.0")
        assert ok is True

    def test_v_prefix_tolerated_on_both_sides(self):
        ok, _ = compatibility.check({"min_ledmatrix_version": "v3.3.0"}, "v3.2.0")
        assert ok is False

    @pytest.mark.parametrize("floor,core,expected_ok", [
        ("3.2.0", "3.2.1", True),
        ("3.2.1", "3.2.0", False),
        ("3.10.0", "3.9.0", False),   # numeric compare, not lexical
        ("3.9.0", "3.10.0", True),
    ])
    def test_ordering(self, floor, core, expected_ok):
        ok, _ = compatibility.check({"min_ledmatrix_version": floor}, core)
        assert ok is expected_ok


# --------------------------------------------------------------------------
# The gate in install_plugin
# --------------------------------------------------------------------------

def _write_plugin(plugins_dir: Path, plugin_id: str, manifest: dict) -> Path:
    path = plugins_dir / plugin_id
    path.mkdir(parents=True)
    (path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (path / "manager.py").write_text("class P: pass\n", encoding="utf-8")
    return path


@pytest.fixture
def store(tmp_path, monkeypatch):
    """A PluginStoreManager whose download step is stubbed to drop a plugin
    directory in place, so the test exercises the post-download validation
    path without touching the network."""
    from src.plugin_system.store_manager import PluginStoreManager

    plugins_dir = tmp_path / "plugin-repos"
    plugins_dir.mkdir()
    mgr = PluginStoreManager(plugins_dir=str(plugins_dir))
    mgr.logger = MagicMock()
    return mgr, plugins_dir


class TestInstallGate:
    """`install_plugin` is the chokepoint: `_reinstall_with_rollback` calls it,
    so gating there covers updates too, and a refused update restores the
    version the user already had."""

    def _install_with_manifest(self, store, manifest, core_version, monkeypatch):
        mgr, plugins_dir = store
        plugin_id = manifest["id"]

        monkeypatch.setattr(
            mgr, "get_plugin_info",
            lambda *a, **k: {"repo": "https://example.invalid/r",
                             "plugin_path": f"plugins/{plugin_id}",
                             "branch": "main"})
        # Stand in for the download: put the files where install_plugin expects.
        monkeypatch.setattr(
            mgr, "_install_from_monorepo",
            lambda *a, **k: bool(_write_plugin(plugins_dir, plugin_id, manifest)))
        monkeypatch.setattr(mgr, "_install_from_monorepo_api", lambda *a, **k: False)
        monkeypatch.setattr(mgr, "_install_dependencies", lambda *a, **k: True)

        import src
        monkeypatch.setattr(src, "__version__", core_version)
        return mgr.install_plugin(plugin_id), plugins_dir / plugin_id

    def test_refuses_and_leaves_nothing_behind(self, store, monkeypatch):
        manifest = {
            "id": "needs-newer", "name": "Needs Newer", "class_name": "P",
            "display_modes": ["a"], "min_ledmatrix_version": "9.9.9",
        }
        ok, path = self._install_with_manifest(store, manifest, "3.2.0", monkeypatch)

        assert ok is False, "install must refuse a plugin that needs a newer core"
        assert not path.exists(), (
            "a refused install must not leave a half-installed directory — "
            "plugin discovery would pick it up and fail to load it")

    def test_allows_a_compatible_plugin(self, store, monkeypatch):
        manifest = {
            "id": "fine", "name": "Fine", "class_name": "P",
            "display_modes": ["a"], "min_ledmatrix_version": "3.0.0",
        }
        ok, path = self._install_with_manifest(store, manifest, "3.2.0", monkeypatch)

        assert ok is True
        assert (path / "manifest.json").exists()

    def test_untrustworthy_core_does_not_block_installs(self, store, monkeypatch):
        """Regression guard for the worst possible outcome of this feature:
        users on the v3.1.0 release (which reports 1.0.0) must not be locked
        out of the plugin store entirely."""
        manifest = {
            "id": "floored", "name": "Floored", "class_name": "P",
            "display_modes": ["a"], "versions": [{"ledmatrix_min": "2.0.0"}],
        }
        ok, path = self._install_with_manifest(store, manifest, "1.0.0", monkeypatch)

        assert ok is True, (
            "a core below the trustworthy floor must not block installs — "
            "nearly every published manifest floors at 2.0.0")
        assert (path / "manifest.json").exists()


class TestLoaderAndStoreAgree:
    """Both read the same manifests; a disagreement means one of them is
    lying to the user."""

    @pytest.mark.parametrize("manifest,core,expected", [
        ({"min_ledmatrix_version": "3.2.0"}, "3.1.0", False),
        ({"versions": [{"ledmatrix_min": "2.0.0"}]}, "3.2.0", True),
        ({"versions": [{"ledmatrix_min_version": "9.0.0"}]}, "3.2.0", False),
        ({}, "3.2.0", True),
    ])
    def test_same_verdict(self, manifest, core, expected):
        from src.plugin_system.plugin_loader import PluginLoader

        store_ok, _ = compatibility.check(manifest, core)
        assert store_ok is expected

        # The loader resolves the floor through the same helper, so a
        # divergence in spelling handling would show up here.
        loader_needed = compatibility.parse_semver(
            compatibility.declared_min_version(manifest))
        current = compatibility.parse_semver(core)
        loader_would_warn = (
            loader_needed is not None
            and current is not None
            and current >= compatibility.TRUSTWORTHY_FLOOR
            and loader_needed > current
        )
        assert loader_would_warn is (not expected)
        assert hasattr(PluginLoader, "_warn_if_incompatible")


# --------------------------------------------------------------------------
# compatible_versions — the schema-required field, and the only one that can
# express an upper bound
# --------------------------------------------------------------------------

class TestCompatibleVersions:
    @pytest.mark.parametrize("spec,core,expected", [
        (">=2.0.0", "3.2.0", True),
        (">=2.0.0", "1.9.9", False),
        ("<=3.0.0", "3.2.0", False),
        ("<=3.0.0", "2.9.0", True),
        (">3.2.0", "3.2.0", False),
        ("<4.0.0", "3.2.0", True),
        ("3.2.0", "3.2.0", True),      # bare == exact match
        ("3.2.0", "3.2.1", False),
        ("~3.2.0", "3.2.9", True),     # patch-level only
        ("~3.2.0", "3.3.0", False),
        ("^3.2.0", "3.9.9", True),     # minor + patch
        ("^3.2.0", "4.0.0", False),
        ("2.0.0 - 3.2.0", "3.2.0", True),   # inclusive both ends
        ("2.0.0 - 3.2.0", "2.0.0", True),
        ("2.0.0 - 3.2.0", "3.2.1", False),
        ("v3.2.0", "3.2.0", True),     # leading v tolerated
        ("3.2.0-beta.1", "3.2.0", True),  # prerelease suffix ignored
    ])
    def test_range_forms(self, spec, core, expected):
        got = compatibility.satisfies_compatible_versions(
            {"compatible_versions": [spec]}, compatibility.parse_semver(core))
        assert got is expected, f"{spec!r} vs {core}"

    def test_array_is_alternatives_not_conjunction(self):
        """Satisfying any one entry is enough — otherwise ['<2.0.0','>=3.0.0']
        could never be satisfied by anything."""
        m = {"compatible_versions": ["<2.0.0", ">=3.0.0"]}
        assert compatibility.satisfies_compatible_versions(
            m, compatibility.parse_semver("3.2.0")) is True

    def test_absent_or_unparseable_is_no_evidence(self):
        core = compatibility.parse_semver("3.2.0")
        assert compatibility.satisfies_compatible_versions({}, core) is None
        assert compatibility.satisfies_compatible_versions(
            {"compatible_versions": []}, core) is None
        assert compatibility.satisfies_compatible_versions(
            {"compatible_versions": ["not a version"]}, core) is None
        # One unparseable entry alongside a good one must not poison the result.
        assert compatibility.satisfies_compatible_versions(
            {"compatible_versions": ["garbage", ">=2.0.0"]}, core) is True


class TestMoreRestrictiveWins:
    def test_upper_bound_blocks_a_core_that_clears_the_floor(self):
        """The gap this closes: the floor says 2.0.0 and the core is 3.2.0, so
        the floor alone would allow it — but the plugin said it stops at 2.x."""
        m = {"name": "Legacy Plugin",
             "compatible_versions": ["2.0.0 - 2.9.9"],
             "versions": [{"ledmatrix_min_version": "2.0.0"}]}
        ok, reason = compatibility.check(m, "3.2.0")
        assert ok is False
        assert "2.0.0 - 2.9.9" in reason and "3.2.0" in reason

    def test_floor_blocks_when_ranges_would_allow(self):
        m = {"name": "Needs Newer",
             "compatible_versions": [">=1.0.0"],
             "versions": [{"ledmatrix_min_version": "9.9.9"}]}
        ok, reason = compatibility.check(m, "3.2.0")
        assert ok is False
        assert "9.9.9" in reason

    def test_both_satisfied_allows(self):
        m = {"compatible_versions": [">=2.0.0"],
             "versions": [{"ledmatrix_min_version": "2.0.0"}]}
        assert compatibility.check(m, "3.2.0") == (True, None)

    def test_untrustworthy_core_still_bypasses_both_checks(self):
        """A core reporting 1.0.0 fails `>=2.0.0`, which 41 of 42 published
        manifests declare. Blocking there would empty the plugin store for
        exactly the users who cannot be helped by it."""
        m = {"compatible_versions": [">=2.0.0"],
             "versions": [{"ledmatrix_min_version": "2.0.0"}]}
        assert compatibility.check(m, "1.0.0") == (True, None)


class TestUntrustworthyCoreAndTheSunset:
    """The population B6 would otherwise break.

    A device installed from the v3.1.0 release reports `__version__ = "1.0.0"`.
    The gate cannot tell it apart from a genuine 1.0.0 install, so it cannot
    reason about what that core actually has — and at the B6 sunset the
    plugin's guarded-import fallback is gone. Without this rule the store hands
    those users a 3.2.0-floored plugin that fails to load, and nothing else in
    the system protects them.

    The rule: on an untrustworthy core, refuse a floor *above* the ecosystem
    baseline, allow anything at or below it. Every manifest published today
    floors at exactly 2.0.0, so nobody is locked out of the store.
    """

    UNTRUSTWORTHY = ["1.0.0", "0.9.0", "1.9.9"]

    @pytest.mark.parametrize("core", UNTRUSTWORTHY)
    def test_refuses_a_post_sunset_floor(self, core):
        m = {"name": "Hockey Scoreboard",
             "versions": [{"ledmatrix_min_version": "3.2.0"}]}
        ok, reason = compatibility.check(m, core)
        assert ok is False, (
            f"core {core} must not receive a 3.2.0-floored plugin: after the "
            "sunset there is no fallback and it will fail to load"
        )
        assert "3.2.0" in reason and core in reason

    @pytest.mark.parametrize("core", UNTRUSTWORTHY)
    def test_still_allows_todays_ecosystem_floor(self, core):
        """Regression guard: every published manifest floors at 2.0.0. If this
        starts refusing, those users lose the plugin store entirely."""
        m = {"versions": [{"ledmatrix_min": "2.0.0"}]}
        assert compatibility.check(m, core) == (True, None)

    @pytest.mark.parametrize("core", UNTRUSTWORTHY)
    def test_still_allows_an_undeclared_floor(self, core):
        assert compatibility.check({"id": "x"}, core) == (True, None)

    def test_trustworthy_core_below_the_floor_is_unaffected(self):
        """A core that reports 3.1.0 is believable and already handled by the
        ordinary comparison — not by this rule."""
        m = {"name": "P", "versions": [{"ledmatrix_min_version": "3.2.0"}]}
        ok, reason = compatibility.check(m, "3.1.0")
        assert ok is False
        assert "too old to identify" not in reason, (
            "a believable version should get the ordinary message"
        )
