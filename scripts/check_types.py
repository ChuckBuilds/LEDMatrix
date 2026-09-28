#!/usr/bin/env python3
"""Type-check the modules listed in mypy-clean.txt (the mypy ratchet).

Most of src/ still has mypy errors, so CI can't require a clean `mypy src`.
Instead mypy-clean.txt lists the modules that *are* clean, and this script
fails if any of them regresses. When you make another module clean, add it to
the list; nothing ever comes off it.

Imports are followed silently: a listed module is checked against the types of
everything it imports, but errors inside those imported modules are not
reported, so a clean file isn't failed by an unlisted neighbour.

Usage:
    python scripts/check_types.py            # check the listed modules
    python scripts/check_types.py --list     # print the list and exit

Extra arguments after ``--`` are passed to mypy.
Exit status: 0 clean, 1 mypy errors, 2 a bad list (missing file, duplicate,
unsorted, or empty).
"""

import argparse
import subprocess  # nosec B404 - list-form argv only, no shell  # nosemgrep
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIST_FILE = REPO_ROOT / "mypy-clean.txt"


def read_list(path: Path = LIST_FILE) -> list:
    """The listed paths, in file order, with comments and blank lines dropped."""
    entries = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            entries.append(line)
    return entries


def list_problems(entries: list, root: Path = REPO_ROOT) -> list:
    """Why the list can't be used as-is; empty when it is fine."""
    problems = []
    if not entries:
        problems.append(f"{LIST_FILE.name} lists no modules")
    seen = set()
    for entry in entries:
        if entry in seen:
            problems.append(f"listed twice: {entry}")
        seen.add(entry)
        if "\\" in entry:
            problems.append(f"use forward slashes: {entry}")
        elif not (root / entry).is_file():
            problems.append(f"listed but not found (renamed or deleted? update the list): {entry}")
    if entries != sorted(entries):
        problems.append(f"{LIST_FILE.name} is not sorted")
    return problems


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--list", action="store_true", help="print the listed modules and exit")
    parser.add_argument("mypy_args", nargs="*", help="extra mypy arguments (after --)")
    args = parser.parse_args(argv)

    entries = read_list()
    problems = list_problems(entries)
    if problems:
        for problem in problems:
            print(f"check_types: {problem}", file=sys.stderr)
        return 2
    if args.list:
        print("\n".join(entries))
        return 0

    cmd = [
        sys.executable, "-m", "mypy",
        "--config-file", str(REPO_ROOT / "mypy.ini"),
        "--follow-imports=silent",
        *args.mypy_args,
        *entries,
    ]
    print(f"check_types: mypy on {len(entries)} modules from {LIST_FILE.name}", flush=True)
    # This interpreter's mypy, fixed flags, and paths from the checked-in list.
    result = subprocess.run(cmd, cwd=REPO_ROOT)  # nosec B603 - list-form argv, no shell  # nosemgrep
    if result.returncode > 1:  # mypy itself failed (bad config, crash)
        return result.returncode
    if result.returncode != 0:
        print(
            "check_types: a module on the mypy ratchet has type errors. Fix them "
            f"(annotation-only where possible) rather than taking it off {LIST_FILE.name}.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
