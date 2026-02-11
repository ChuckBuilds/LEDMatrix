#!/usr/bin/env python3
"""
Update the ledmatrix-plugins monorepo by pulling latest changes.
"""

import subprocess
import sys
from pathlib import Path

MONOREPO_DIR = Path(__file__).parent.parent.parent / "ledmatrix-plugins"


def main():
    if not MONOREPO_DIR.exists():
        print(f"Error: Monorepo not found: {MONOREPO_DIR}")
        return 1

    if not (MONOREPO_DIR / ".git").exists():
        print(f"Error: {MONOREPO_DIR} is not a git repository")
        return 1

    print(f"Updating {MONOREPO_DIR}...")
    try:
        result = subprocess.run(
            ["git", "-C", str(MONOREPO_DIR), "pull"],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        print(f"Error: git pull timed out after 120 seconds for {MONOREPO_DIR}")
        return 1

    if result.returncode == 0:
        print(result.stdout.strip())
        return 0
    else:
        print(f"Error: {result.stderr.strip()}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
