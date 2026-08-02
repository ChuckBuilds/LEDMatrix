#!/usr/bin/env python3
"""Assert that a release tag, the CHANGELOG, and `src.__version__` all agree.

Run it *before* creating a tag to check yourself:

    python scripts/check_release_version.py v3.2.0

CI runs it on every pushed `v*` tag and published release
(`.github/workflows/release-version-check.yml`), so a mismatch shows up as a
red check on the release rather than as a silent wrong answer on user devices.

Why this exists: `v3.1.0` was tagged 2026-05-31 while `src/__init__.py` still
said `"1.0.0"`; the bump to `"3.1.0"` did not land until 2026-07-12. Devices
installed from that release report `1.0.0`, which is below the `(2, 0, 0)` floor
in `PluginLoader._warn_if_incompatible`, so they are silently exempt from every
plugin compatibility warning. Plugin `ledmatrix_min_version` floors are only as
trustworthy as this agreement. See `docs/SPORTS_UNIFICATION.md`, phase B4.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
HEADING = re.compile(r"^##\s+(?P<version>\d+\.\d+\.\d+)\s*$", re.MULTILINE)


def normalize(tag: str) -> str:
    """`v3.2.0` and `3.2.0` are the same release; tags here carry the `v`."""
    return tag[1:] if tag.startswith("v") else tag


def newest_changelog_version(changelog: Path) -> str | None:
    headings = HEADING.findall(changelog.read_text(encoding="utf-8"))
    return headings[0] if headings else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "tag",
        help="Release tag to check, with or without the leading 'v' (e.g. v3.2.0)",
    )
    args = parser.parse_args()

    from src import __version__ as core_version

    tag_version = normalize(args.tag)
    changelog_version = newest_changelog_version(REPO_ROOT / "CHANGELOG.md")

    problems: list[str] = []

    if not SEMVER.match(tag_version):
        problems.append(
            f"tag {args.tag!r} is not vX.Y.Z. Older tags (v2.5) predate this "
            "check; new releases must be full semver so floors can parse them."
        )

    if not SEMVER.match(core_version):
        problems.append(f"src.__version__ is {core_version!r}, which is not X.Y.Z")

    if tag_version != core_version:
        problems.append(
            f"tag says {tag_version} but src.__version__ says {core_version}. "
            "Bump src/__init__.py to match the tag before releasing — devices "
            "report __version__, not the tag, and plugin floors compare "
            "against it."
        )

    if changelog_version is None:
        problems.append("CHANGELOG.md has no '## X.Y.Z' version heading")
    elif changelog_version != core_version:
        problems.append(
            f"CHANGELOG.md's newest heading is {changelog_version} but "
            f"src.__version__ is {core_version}. Plugin authors read the "
            "CHANGELOG to pick a ledmatrix_min_version floor."
        )

    if problems:
        print(f"Release version check FAILED for tag {args.tag}:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    print(
        f"OK: tag {args.tag}, src.__version__ {core_version}, and the CHANGELOG "
        "all agree."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
