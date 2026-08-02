"""Version reporting must have exactly one answer.

`src.__version__` is the canonical core version. The plugin loader compares
plugin `ledmatrix_min_version` floors against it, and the plugin ecosystem
floors on the number recorded in `CHANGELOG.md` — so if those two disagree, a
plugin can declare a floor that is satisfied by a core which does not actually
ship the module it needs.

This has already gone wrong once. The `v3.1.0` tag was cut 2026-05-31, but
`src/__init__.py` was not bumped from `"1.0.0"` to `"3.1.0"` until 2026-07-12,
six weeks later. Every device installed from that release reports `1.0.0`,
which is below the `(2, 0, 0)` floor in `PluginLoader._warn_if_incompatible` —
so those users get no compatibility warning at all. See
`docs/SPORTS_UNIFICATION.md` (phase B4).

The matching tag check runs at release time in
`.github/workflows/release-version-check.yml`; a tag is not available here.

Note: `src.plugin_system.__version__` is deliberately NOT checked. That module
versions the *plugin API* (it sits beside `__api_version__` and is documented as
such), which moves independently of the core version.
"""

import re
from pathlib import Path

import pytest

import src

REPO_ROOT = Path(__file__).resolve().parents[1]
CHANGELOG = REPO_ROOT / "CHANGELOG.md"

SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
# Version headings look like "## 3.2.0". A leading "## Unreleased" section is
# allowed and skipped -- it is where module additions are staged before a bump.
HEADING = re.compile(r"^##\s+(?P<version>\d+\.\d+\.\d+)\s*$", re.MULTILINE)


def test_core_version_is_semver():
    """A floor comparison parses this string; it has to be parseable."""
    assert SEMVER.match(src.__version__), (
        f"src.__version__ is {src.__version__!r}, which is not X.Y.Z. "
        "The loader's floor comparison cannot parse it."
    )


def test_changelog_documents_the_current_version():
    """The newest versioned CHANGELOG heading is the version we claim to be.

    Plugins floor on the version recorded in the CHANGELOG as first shipping a
    module. If the code says 3.2.0 and the CHANGELOG's newest entry is 3.1.0,
    that record points at the wrong release.
    """
    text = CHANGELOG.read_text(encoding="utf-8")
    headings = HEADING.findall(text)
    assert headings, "CHANGELOG.md has no '## X.Y.Z' version headings"

    newest = headings[0]
    assert newest == src.__version__, (
        f"src.__version__ is {src.__version__!r} but the newest CHANGELOG "
        f"heading is {newest!r}. Bump one to match the other: the CHANGELOG is "
        "what plugin authors read to pick a ledmatrix_min_version floor."
    )


def test_changelog_versions_are_ordered_and_unique():
    """A duplicated or out-of-order heading makes 'first release shipping X'
    ambiguous, which is exactly the question the sunset rule asks."""
    text = CHANGELOG.read_text(encoding="utf-8")
    versions = [tuple(int(p) for p in v.split(".")) for v in HEADING.findall(text)]

    duplicates = {v for v in versions if versions.count(v) > 1}
    assert not duplicates, f"CHANGELOG.md has duplicate version headings: {duplicates}"

    assert versions == sorted(versions, reverse=True), (
        "CHANGELOG.md version headings are not in descending order; "
        f"got {['.'.join(map(str, v)) for v in versions]}"
    )


def test_web_interface_version_tracks_the_core():
    """web_interface used to carry its own hardcoded "3.0.0", a third answer to
    'what version is this'. It now re-exports the canonical one."""
    web_interface = pytest.importorskip(
        "web_interface", reason="web_interface needs Flask, which is optional here"
    )
    assert getattr(web_interface, "__version__", None) == src.__version__, (
        "web_interface.__version__ has drifted from src.__version__; it should "
        "re-export the canonical value rather than hardcode its own."
    )
