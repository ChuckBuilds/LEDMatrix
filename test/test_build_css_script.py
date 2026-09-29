"""scripts/build_css.py: the pinned Tailwind CLI and what it builds.

The build itself needs the CLI download, so CI runs it in its own job
(`build_css.py --check`); these check the parts that must hold without it.
"""

import importlib.util
import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "build_css", PROJECT_ROOT / "scripts" / "build_css.py"
)
build_css = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build_css)


def test_every_asset_is_pinned_to_a_sha256():
    assert re.fullmatch(r"3\.\d+\.\d+", build_css.TAILWIND_VERSION)
    assert build_css.TAILWIND_ASSETS
    for name, digest in build_css.TAILWIND_ASSETS.items():
        assert name.startswith("tailwindcss-"), name
        assert re.fullmatch(r"[0-9a-f]{64}", digest), name


@pytest.mark.parametrize("system,machine,expected", [
    ("Linux", "x86_64", "tailwindcss-linux-x64"),
    ("Linux", "aarch64", "tailwindcss-linux-arm64"),
    ("Linux", "armv7l", "tailwindcss-linux-armv7"),
    ("Darwin", "arm64", "tailwindcss-macos-arm64"),
    ("Darwin", "x86_64", "tailwindcss-macos-x64"),
    ("Windows", "AMD64", "tailwindcss-windows-x64.exe"),
    ("Windows", "ARM64", "tailwindcss-windows-arm64.exe"),
])
def test_asset_name_maps_each_platform(monkeypatch, system, machine, expected):
    monkeypatch.setattr(build_css.platform, "system", lambda: system)
    monkeypatch.setattr(build_css.platform, "machine", lambda: machine)
    assert build_css.asset_name() == expected
    assert expected in build_css.TAILWIND_ASSETS


def test_unsupported_cpu_is_a_clear_error(monkeypatch):
    monkeypatch.setattr(build_css.platform, "system", lambda: "Linux")
    monkeypatch.setattr(build_css.platform, "machine", lambda: "armv6l")
    with pytest.raises(SystemExit, match="armv6l"):
        build_css.asset_name()


def test_every_build_input_exists_and_its_output_is_committed():
    for input_css, config, output in build_css.BUILDS:
        assert (PROJECT_ROOT / input_css).is_file(), input_css
        assert (PROJECT_ROOT / config).is_file(), config
        assert (PROJECT_ROOT / output).is_file(), output


def test_the_cli_is_fed_an_lf_copy_of_a_crlf_input(tmp_path, monkeypatch):
    """Tailwind's minifier merges rules differently when the input CSS has
    CRLF line endings, so a Windows checkout built bytes CI's Linux build
    didn't, and --check failed. The CLI must always see LF."""
    monkeypatch.setattr(build_css, "PROJECT_ROOT", tmp_path)
    (tmp_path / "in.css").write_bytes(b"@tailwind base;\r\n@tailwind utilities;\r\n")
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["input"] = Path(cmd[cmd.index("--input") + 1]).read_bytes()
        Path(cmd[cmd.index("--output") + 1]).write_text(".a{b:c}", encoding="utf-8")

        class Done:
            returncode = 0
            stdout = stderr = ""
        return Done()

    monkeypatch.setattr(build_css.subprocess, "run", fake_run)
    work = tmp_path / "work"
    work.mkdir()
    out = tmp_path / "out.css"
    build_css.run_build(Path("cli"), "in.css", "cfg.js", out, work)

    assert seen["input"] == b"@tailwind base;\n@tailwind utilities;\n"
    assert out.read_bytes() == b".a{b:c}\n"
    assert (tmp_path / "in.css").read_bytes().count(b"\r\n") == 2  # source untouched


def test_a_corrupt_cached_cli_is_replaced(tmp_path, monkeypatch):
    """A cached binary that fails its hash is deleted and fetched again,
    and the fresh download is hash-checked too."""
    monkeypatch.setenv("LEDMATRIX_TAILWIND_CACHE", str(tmp_path))
    name = build_css.asset_name()
    cached = tmp_path / f"v{build_css.TAILWIND_VERSION}" / name
    cached.parent.mkdir(parents=True)
    cached.write_bytes(b"not the cli")

    class FakeResponse:
        def __init__(self, data):
            self.data = data

        def read(self, n=-1):
            data, self.data = self.data, b""
            return data

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(build_css.urllib.request, "urlopen",
                        lambda url, timeout: FakeResponse(b"tampered"))
    with pytest.raises(SystemExit, match="SHA-256 mismatch"):
        build_css.ensure_cli()
    assert not cached.exists()
    assert not any(p.name.startswith(".download-") for p in cached.parent.iterdir())
