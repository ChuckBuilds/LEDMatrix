"""
Tests for src/plugin_system/compatibility.py — the "can this plugin run on
this core?" gate used by both the plugin loader (advisory) and the store
manager (blocking at install/update time).

This module had zero direct test coverage despite guarding every install.
These tests pin the documented contract: refuse only on evidence, resolve
every uncertain case (unparseable versions, missing fields, untrustworthy
core) to compatible.
"""

import pytest

from src.plugin_system.compatibility import (
    TRUSTWORTHY_FLOOR,
    parse_semver,
    _parse_strict,
    _satisfies_range,
    satisfies_compatible_versions,
    declared_min_version,
    check,
)


class TestParseSemver:
    def test_plain_triplet(self):
        assert parse_semver("1.2.3") == (1, 2, 3)

    def test_leading_v_tolerated(self):
        assert parse_semver("v3.2.1") == (3, 2, 1)

    def test_prerelease_suffix_stripped(self):
        # "3.2.0-rc1" must NOT parse as (3, 2, 1) — a release candidate must
        # not rank above its own release.
        assert parse_semver("3.2.0-rc1") == (3, 2, 0)

    def test_build_suffix_stripped(self):
        # "3.2.0+build42" must NOT parse as (3, 2, 42).
        assert parse_semver("3.2.0+build42") == (3, 2, 0)

    def test_two_part_version_pads_zero(self):
        assert parse_semver("1.2") == (1, 2, 0)

    def test_one_part_version_pads_zeros(self):
        assert parse_semver("2") == (2, 0, 0)

    def test_extra_parts_ignored(self):
        assert parse_semver("1.2.3.4") == (1, 2, 3)

    def test_non_string_returns_none(self):
        assert parse_semver(None) is None
        assert parse_semver(123) is None
        assert parse_semver((1, 2, 3)) is None

    def test_garbage_with_no_digits_is_lenient_zero(self):
        # Documented leniency: digit-scraping yields (0, 0, 0) for pure
        # garbage. Fine for a floor (0.0.0 never blocks), wrong for ranges —
        # which is why ranges go through _parse_strict instead.
        assert parse_semver("garbage") == (0, 0, 0)

    def test_whitespace_stripped(self):
        assert parse_semver("  1.2.3  ") == (1, 2, 3)


class TestParseStrict:
    def test_accepts_real_versions(self):
        assert _parse_strict("1.2.3") == (1, 2, 3)
        assert _parse_strict("v1.2.3-rc1") == (1, 2, 3)
        assert _parse_strict("2.0") == (2, 0, 0)

    def test_rejects_garbage(self):
        assert _parse_strict("not-a-version") is None
        assert _parse_strict("") is None

    def test_rejects_non_string(self):
        assert _parse_strict(None) is None


class TestSatisfiesRange:
    CORE = (3, 1, 0)

    @pytest.mark.parametrize("spec,expected", [
        (">=3.0.0", True),
        (">=3.1.0", True),
        (">=3.2.0", False),
        ("<=3.1.0", True),
        ("<=3.0.9", False),
        (">3.0.9", True),
        (">3.1.0", False),
        ("<3.2.0", True),
        ("<3.1.0", False),
    ])
    def test_comparison_operators(self, spec, expected):
        assert _satisfies_range(self.CORE, spec) is expected

    def test_tilde_allows_patch_only(self):
        # ~3.1.0 means >=3.1.0, <3.2.0
        assert _satisfies_range((3, 1, 5), "~3.1.0") is True
        assert _satisfies_range((3, 2, 0), "~3.1.0") is False
        assert _satisfies_range((3, 0, 9), "~3.1.0") is False

    def test_caret_allows_minor_and_patch(self):
        # ^3.1.0 means >=3.1.0, <4.0.0
        assert _satisfies_range((3, 9, 9), "^3.1.0") is True
        assert _satisfies_range((4, 0, 0), "^3.1.0") is False
        assert _satisfies_range((3, 0, 0), "^3.1.0") is False

    def test_bare_exact_version(self):
        assert _satisfies_range((3, 1, 0), "3.1.0") is True
        assert _satisfies_range((3, 1, 1), "3.1.0") is False

    def test_inclusive_dash_range(self):
        assert _satisfies_range((2, 5, 0), "2.0.0 - 3.1.0") is True
        assert _satisfies_range((2, 0, 0), "2.0.0 - 3.1.0") is True
        assert _satisfies_range((3, 1, 0), "2.0.0 - 3.1.0") is True
        assert _satisfies_range((3, 1, 1), "2.0.0 - 3.1.0") is False

    def test_unparseable_spec_returns_none_not_false(self):
        # Garbage must read as "no evidence", never as a refusal — an
        # unrecognised spelling must not cost a user a working install.
        assert _satisfies_range(self.CORE, "banana") is None
        assert _satisfies_range(self.CORE, ">=banana") is None
        assert _satisfies_range(self.CORE, "") is None
        assert _satisfies_range(self.CORE, "banana - 3.0.0") is None


class TestSatisfiesCompatibleVersions:
    def test_any_entry_satisfying_wins(self):
        manifest = {"compatible_versions": ["<1.0.0", ">=3.0.0"]}
        assert satisfies_compatible_versions(manifest, (3, 1, 0)) is True

    def test_all_entries_failing_is_false(self):
        manifest = {"compatible_versions": ["<1.0.0", "2.0.0 - 2.9.9"]}
        assert satisfies_compatible_versions(manifest, (3, 1, 0)) is False

    def test_absent_field_returns_none(self):
        assert satisfies_compatible_versions({}, (3, 1, 0)) is None

    def test_empty_list_returns_none(self):
        assert satisfies_compatible_versions(
            {"compatible_versions": []}, (3, 1, 0)) is None

    def test_non_list_returns_none(self):
        assert satisfies_compatible_versions(
            {"compatible_versions": ">=2.0.0"}, (3, 1, 0)) is None

    def test_all_unparseable_entries_returns_none(self):
        manifest = {"compatible_versions": ["banana", 42, None]}
        assert satisfies_compatible_versions(manifest, (3, 1, 0)) is None

    def test_mixed_parseable_and_garbage_uses_parseable(self):
        manifest = {"compatible_versions": ["banana", ">=3.0.0"]}
        assert satisfies_compatible_versions(manifest, (3, 1, 0)) is True


class TestDeclaredMinVersion:
    def test_top_level_field(self):
        assert declared_min_version({"min_ledmatrix_version": "2.1.0"}) == "2.1.0"

    def test_requires_dict_fallback(self):
        manifest = {"requires": {"min_ledmatrix_version": "2.2.0"}}
        assert declared_min_version(manifest) == "2.2.0"

    def test_versions_array_fallback(self):
        manifest = {"versions": [{"ledmatrix_min_version": "2.3.0"}]}
        assert declared_min_version(manifest) == "2.3.0"

    def test_versions_array_deprecated_spelling(self):
        manifest = {"versions": [{"ledmatrix_min": "2.4.0"}]}
        assert declared_min_version(manifest) == "2.4.0"

    def test_top_level_wins_over_versions_array(self):
        manifest = {
            "min_ledmatrix_version": "2.1.0",
            "versions": [{"ledmatrix_min_version": "9.9.9"}],
        }
        assert declared_min_version(manifest) == "2.1.0"

    def test_requires_as_list_does_not_raise(self):
        # A hand-edited manifest can carry `requires` as a list; this used to
        # raise AttributeError and one malformed manifest would take down the
        # whole install path.
        assert declared_min_version({"requires": ["something"]}) is None

    def test_versions_as_dict_does_not_raise(self):
        # Same for `versions` as a mapping (used to raise KeyError).
        assert declared_min_version({"versions": {"0": {}}}) is None

    def test_nothing_declared_returns_none(self):
        assert declared_min_version({}) is None


class TestCheck:
    def test_compatible_when_nothing_declared(self):
        assert check({}, "3.1.0") == (True, None)

    def test_min_version_blocks_older_core(self):
        manifest = {"name": "Test Plugin", "min_ledmatrix_version": "3.2.0"}
        ok, reason = check(manifest, "3.1.0")
        assert ok is False
        assert "3.2.0" in reason and "3.1.0" in reason

    def test_min_version_allows_equal_core(self):
        manifest = {"min_ledmatrix_version": "3.1.0"}
        assert check(manifest, "3.1.0") == (True, None)

    def test_compatible_versions_upper_bound_blocks(self):
        # A range is the only field that can express "not compatible with
        # newer cores" — it must win even when the floor passes.
        manifest = {
            "name": "Old Plugin",
            "min_ledmatrix_version": "2.0.0",
            "compatible_versions": ["2.0.0 - 2.9.9"],
        }
        ok, reason = check(manifest, "3.1.0")
        assert ok is False
        assert "2.0.0 - 2.9.9" in reason

    def test_unparseable_core_with_high_floor_is_blocked(self):
        manifest = {"min_ledmatrix_version": "3.2.0",
                    "compatible_versions": [">=3.2.0"]}
        # An unparseable core version is "unknown", not "old"... but note
        # parse_semver("garbage") == (0,0,0) which is below TRUSTWORTHY_FLOOR,
        # so this rides the untrustworthy-core branch: floor > 2.0.0 blocks.
        ok, reason = check(manifest, "garbage")
        assert ok is False
        assert "too old to identify reliably" in reason

    def test_untrustworthy_core_allows_ecosystem_baseline_floor(self):
        # A core reporting 1.0.0 may really be v3.1.0 (which shipped with a
        # wrong __version__). Floors at or below TRUSTWORTHY_FLOOR must not
        # block, or that population could install nothing.
        manifest = {"min_ledmatrix_version": "2.0.0",
                    "compatible_versions": [">=2.0.0"]}
        assert check(manifest, "1.0.0") == (True, None)

    def test_untrustworthy_core_blocks_floor_above_baseline(self):
        # But a floor above 2.0.0 needs modules that no core reporting below
        # the floor can have — the one refusal on that branch.
        manifest = {"name": "New Plugin", "min_ledmatrix_version": "3.2.0"}
        ok, reason = check(manifest, "1.0.0")
        assert ok is False
        assert "too old to identify reliably" in reason

    def test_untrustworthy_core_ignores_compatible_versions(self):
        # On the untrustworthy branch only the declared floor is consulted;
        # ranges cannot be evaluated against a version that isn't evidence.
        manifest = {"compatible_versions": ["2.0.0 - 2.9.9"]}
        assert check(manifest, "1.0.0") == (True, None)

    def test_floor_exactly_at_trustworthy_floor_is_allowed(self):
        floor = ".".join(str(n) for n in TRUSTWORTHY_FLOOR)
        manifest = {"min_ledmatrix_version": floor}
        assert check(manifest, "1.0.0") == (True, None)

    def test_reason_uses_manifest_name(self):
        manifest = {"name": "Fancy Clock", "min_ledmatrix_version": "9.0.0"}
        ok, reason = check(manifest, "3.1.0")
        assert ok is False
        assert reason.startswith("Fancy Clock")

    def test_reason_falls_back_to_id(self):
        manifest = {"id": "fancy-clock", "min_ledmatrix_version": "9.0.0"}
        ok, reason = check(manifest, "3.1.0")
        assert ok is False
        assert reason.startswith("fancy-clock")

    def test_prerelease_core_compares_equal_to_release(self):
        # Documented: prereleases compare equal to their release.
        manifest = {"min_ledmatrix_version": "3.2.0"}
        assert check(manifest, "3.2.0-rc1") == (True, None)
