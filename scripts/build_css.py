#!/usr/bin/env python3
"""Build the web UI's Tailwind CSS with the pinned standalone Tailwind CLI.

The generated files are committed, so the Pi never builds anything. Run this
on a dev machine (or let CI run it) after changing a template, a static JS
file, or anything under ``web_interface/tailwind/``:

    python3 scripts/build_css.py            # rebuild the committed CSS
    python3 scripts/build_css.py --check    # exit 1 if the committed CSS is stale

No Node or npm: the script downloads Tailwind's standalone CLI (a single
executable) for this OS and CPU from the Tailwind GitHub release, checks it
against the SHA-256 pinned below, and caches it outside the repo
(``$LEDMATRIX_TAILWIND_CACHE``, else the per-user cache directory).

Outputs (see ``BUILDS``):

- ``web_interface/static/v3/tailwind.css``: the utilities the templates and
  static JS use. Linked before ``app.css`` in ``base.html``.
- ``web_interface/static/v3/plugin-frame.css``: preflight plus a broad set of
  common utilities, for plugin ``web_ui/`` fragments served in an iframe.
  Their markup lives in plugin repos, so it can't be scanned; the safelist in
  ``plugin-frame.config.js`` stands in for it.

To move to a new Tailwind v3 release, change ``TAILWIND_VERSION`` and every
hash in ``TAILWIND_ASSETS`` (the release's ``sha256sums.txt``, or the digests
from ``gh api repos/tailwindlabs/tailwindcss/releases/tags/<tag>``), rebuild,
and review the diff of the generated CSS.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import platform
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TAILWIND_DIR = PROJECT_ROOT / "web_interface" / "tailwind"
STATIC_V3 = PROJECT_ROOT / "web_interface" / "static" / "v3"

TAILWIND_VERSION = "3.4.19"

# asset name -> SHA-256, from the v3.4.19 release.
TAILWIND_ASSETS = {
    "tailwindcss-linux-arm64": "e5b2d27694daa80cc52ec29553ba2c6bd43d86bd51a9d633ed24058b9c05a676",
    "tailwindcss-linux-armv7": "e3610b109a64720295e1c00a18dd2d6d79d3cddc618219aa0830de97a55429a4",
    "tailwindcss-linux-x64": "4af3198c015616ea7d6617974ec3d70d987ecc00c1ca8463b0a30fd65cc7c06e",
    "tailwindcss-macos-arm64": "7fdeb00818b6214a337383063282b2361ecb08bbc08f8c8a7ba97ee1e2eaa4fe",
    "tailwindcss-macos-x64": "a597f407e0f1f03535731f5b42f1576a8152cb5fffc2f38e754722bc0c280045",
    "tailwindcss-windows-arm64.exe": "f2b6b999747aa0ae31999d59db117b1ba1e4e15e17675d7108e30aac4b680686",
    "tailwindcss-windows-x64.exe": "a15158c4c5e0e7a75f7229bfe4986fe7710d2edc468b6f96c8981f78ab211347",
}

DOWNLOAD_URL = (
    "https://github.com/tailwindlabs/tailwindcss/releases/download/v{version}/{asset}"
)

# (input CSS, config, output) -- all relative to the project root.
BUILDS = (
    (
        "web_interface/tailwind/app.input.css",
        "web_interface/tailwind/tailwind.config.js",
        "web_interface/static/v3/tailwind.css",
    ),
    (
        "web_interface/tailwind/plugin-frame.input.css",
        "web_interface/tailwind/plugin-frame.config.js",
        "web_interface/static/v3/plugin-frame.css",
    ),
)


def asset_name() -> str:
    """The release asset for this OS and CPU."""
    system = platform.system()
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        arch = "x64"
    elif machine in ("aarch64", "arm64"):
        arch = "arm64"
    elif machine.startswith("armv7") or machine == "armv8l":
        arch = "armv7"
    else:
        raise SystemExit(f"No standalone Tailwind CLI for CPU {machine!r}.")

    if system == "Linux":
        name = f"tailwindcss-linux-{arch}"
    elif system == "Darwin":
        name = f"tailwindcss-macos-{arch}"
    elif system == "Windows":
        name = f"tailwindcss-windows-{arch}.exe"
    else:
        raise SystemExit(f"No standalone Tailwind CLI for {system!r}.")
    if name not in TAILWIND_ASSETS:
        raise SystemExit(f"No standalone Tailwind CLI for {system} {machine}.")
    return name


def cache_dir() -> Path:
    override = os.environ.get("LEDMATRIX_TAILWIND_CACHE")
    if override:
        return Path(override)
    if platform.system() == "Windows":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif platform.system() == "Darwin":
        base = Path.home() / "Library" / "Caches"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return base / "ledmatrix" / "tailwindcss"


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ensure_cli() -> Path:
    """Path to the verified CLI, downloading it on first use."""
    name = asset_name()
    expected = TAILWIND_ASSETS[name]
    target = cache_dir() / f"v{TAILWIND_VERSION}" / name

    if target.is_file():
        if sha256_of(target) == expected:
            return target
        print(f"Cached {target} fails its SHA-256 check; downloading it again.")
        target.unlink()

    target.parent.mkdir(parents=True, exist_ok=True)
    url = DOWNLOAD_URL.format(version=TAILWIND_VERSION, asset=name)
    print(f"Downloading Tailwind CLI v{TAILWIND_VERSION} ({name})...")
    fd, tmp_name = tempfile.mkstemp(dir=target.parent, prefix=".download-")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as out, urllib.request.urlopen(url, timeout=120) as resp:
            shutil.copyfileobj(resp, out)
        actual = sha256_of(tmp)
        if actual != expected:
            raise SystemExit(
                f"SHA-256 mismatch for {url}\n  expected {expected}\n  got      {actual}"
            )
        tmp.chmod(tmp.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        os.replace(tmp, target)
    finally:
        if tmp.exists():
            tmp.unlink()
    return target


def run_build(
    cli: Path, input_css: str, config: str, output: Path, work_dir: Path
) -> None:
    # The minifier's rule merging depends on the input file's line endings,
    # so a Windows checkout (core.autocrlf, CRLF) would build different bytes
    # than CI's Linux one and --check would fail. Feed the CLI an LF copy.
    # (Content files' line endings don't matter; @import isn't used, so the
    # copy's location doesn't either.)
    lf_input = work_dir / (Path(input_css).name)
    lf_input.write_bytes(
        (PROJECT_ROOT / input_css).read_bytes().replace(b"\r\n", b"\n")
    )
    cmd = [
        str(cli),
        "--input", str(lf_input),
        "--config", str(PROJECT_ROOT / config),
        "--output", str(output),
        "--minify",
    ]
    # NODE_ENV=production and no browserslist lookup keep the output the
    # same on every machine.
    env = dict(os.environ, NODE_ENV="production", BROWSERSLIST_IGNORE_OLD_DATA="1")
    result = subprocess.run(
        cmd, cwd=PROJECT_ROOT, env=env, capture_output=True, text=True
    )
    if result.returncode != 0:
        sys.stderr.write(result.stdout + result.stderr)
        raise SystemExit(f"Tailwind build failed for {input_css}")
    # The CLI writes without a trailing newline; add one so the committed
    # file is a well-formed text file and editors leave it alone.
    text = output.read_text(encoding="utf-8").replace("\r\n", "\n")
    if not text.endswith("\n"):
        text += "\n"
    output.write_bytes(text.encode("utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="build to a temp dir and fail if the committed CSS differs",
    )
    args = parser.parse_args(argv)

    cli = ensure_cli()
    stale = []
    with tempfile.TemporaryDirectory(prefix="ledmatrix-css-") as tmp:
        for input_css, config, output in BUILDS:
            committed = PROJECT_ROOT / output
            built = Path(tmp) / Path(output).name if args.check else committed
            run_build(cli, input_css, config, built, Path(tmp))
            if args.check:
                old = (
                    committed.read_bytes().replace(b"\r\n", b"\n")
                    if committed.is_file()
                    else None
                )
                if old != built.read_bytes():
                    stale.append(output)
            else:
                print(f"Wrote {output} ({committed.stat().st_size:,} bytes)")

    if stale:
        print(
            "The committed CSS is out of date: " + ", ".join(stale) + "\n"
            "Run `python3 scripts/build_css.py` and commit the result."
        )
        return 1
    if args.check:
        print("Committed CSS is up to date.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
