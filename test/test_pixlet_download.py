"""
Tests for scripts/download_pixlet.sh -- release-tag resolution and download guards.

Background: Starlark apps render through the pixlet binary, and the installer
that fetches it failed silently. It resolved the release tag by grepping the
GitHub API response for '"tag_name"' and taking the last quoted token on the
match with a greedy sed. When the response arrives on one line that token is
"mentions_count", not the tag, so the script built a URL for a release that
cannot exist -- and `curl -L -o` without -f wrote the 404 body to the file and
exited 0, so the first sign of trouble was tar reporting "not in gzip format"
about a page of HTML.

The API is pretty-printed by default, which is exactly why this needs a test:
by hand the old command looks correct, and the failure only appears when the
formatting changes. These drive the real script with a stubbed curl on PATH, so
both response shapes are covered without touching the network.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "download_pixlet.sh"

PRETTY = """{
  "url": "https://api.github.com/repos/tronbyt/pixlet/releases/12345",
  "id": 12345,
  "tag_name": "v0.53.1",
  "name": "v0.53.1",
  "draft": false,
  "prerelease": false,
  "mentions_count": 3
}
"""

# The shape that broke it: one line, and the last quoted token is not the tag.
MINIFIED = (
    '{"url":"https://api.github.com/repos/tronbyt/pixlet/releases/12345",'
    '"id":12345,"tag_name":"v0.53.1","name":"v0.53.1","draft":false,'
    '"prerelease":false,"mentions_count":3}'
)


def run_script(tmp_path, api_body, download=None):
    """Run the real script against a stubbed curl.

    Args:
        api_body: what the stub returns for the api.github.com request.
        download: bytes to write for a release-asset request, or None to make
            that request fail the way `curl -f` does on an HTTP error.
    """
    root = tmp_path / "project"
    (root / "scripts").mkdir(parents=True)
    shutil.copy(SCRIPT, root / "scripts" / "download_pixlet.sh")

    api_file = tmp_path / "api.json"
    api_file.write_text(api_body)

    stub_dir = tmp_path / "stub"
    stub_dir.mkdir()
    asset_file = tmp_path / "asset.bin"
    if download is not None:
        asset_file.write_bytes(download)

    # Stands in for curl, including the -f semantics the fix turns on: without
    # -f, real curl writes the error body to the output file and exits 0, which
    # is what let a 404 masquerade as a successful download. The stub has to
    # honour that or a test of the fix would pass against the old script too.
    (stub_dir / "curl").write_text(f"""#!/bin/bash
out=""
url=""
fail_on_error=0
while [ $# -gt 0 ]; do
    case "$1" in
        -o) out="$2"; shift 2 ;;
        -*f*) fail_on_error=1; shift ;;
        -*) shift ;;
        *) url="$1"; shift ;;
    esac
done
if [[ "$url" == *api.github.com* ]]; then
    cat {api_file}
    exit 0
fi
if [ -f "{asset_file}" ]; then
    cp "{asset_file}" "$out"
    exit 0
fi
# No asset: stand in for an HTTP 404.
if [ "$fail_on_error" = "1" ]; then
    exit 22
fi
printf '<!DOCTYPE html><html>404 Not Found</html>' > "$out"
exit 0
""")
    (stub_dir / "curl").chmod(0o755)

    return subprocess.run(
        ["bash", str(root / "scripts" / "download_pixlet.sh")],
        capture_output=True, text=True,
        env={"PATH": f"{stub_dir}:/usr/bin:/bin:/usr/sbin:/sbin",
             "PIXLET_VERSION": "latest"},
    )


def resolved_version(result):
    match = re.search(r"^Version: (.+)$", result.stdout, re.M)
    assert match, f"no version line in output:\n{result.stdout}"
    return match.group(1).strip()


def test_script_is_syntactically_valid():
    result = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("body,label", [(PRETTY, "pretty"), (MINIFIED, "minified")])
def test_tag_is_resolved_from_either_response_shape(tmp_path, body, label):
    """The minified case is the regression: the last quoted token there is
    "mentions_count", which is what the old greedy sed captured."""
    result = run_script(tmp_path, body)
    assert resolved_version(result) == "v0.53.1", f"{label}: {result.stdout}"
    assert "mentions_count" not in result.stdout


@pytest.mark.parametrize(
    "tag",
    ["mentions_count", "v0.53garbage", "0.53", "v0.5", "", "v0.53.1 ; echo pwned"],
)
def test_a_tag_that_is_not_a_release_falls_back(tmp_path, tag):
    """A wrong-but-non-empty value is what made the original bug silent, so the
    check is on the shape. Partial matches must not pass: "v0.53garbage" and
    "0.53" would build a URL for a release that cannot exist."""
    result = run_script(tmp_path, '{"tag_name": "%s"}' % tag)
    assert resolved_version(result) == "v0.50.2", result.stdout
    assert "using fallback" in result.stdout


@pytest.mark.parametrize("tag", ["v0.53.1", "v1.0.0", "v0.54.0-rc.1", "v1.2.3+build.4"])
def test_real_release_tag_shapes_are_accepted(tmp_path, tag):
    assert resolved_version(run_script(tmp_path, '{"tag_name": "%s"}' % tag)) == tag


def test_an_http_error_is_reported_as_a_failed_download(tmp_path):
    """Without curl -f the 404 body lands in the file and curl exits 0, so the
    failure surfaced two steps later as tar complaining about gzip -- about
    what was really a page of HTML. It has to be reported where it happened.

    Both versions end at 0/1, so asserting only on the count would pass against
    the old script; the discriminating part is which layer reports it.
    """
    result = run_script(tmp_path, PRETTY, download=None)
    assert "Download complete: 0/1 succeeded" in result.stdout
    assert "✓ Downloaded" not in result.stdout
    assert "Failed to download" in result.stdout
    assert "Failed to extract" not in result.stdout, (
        "an HTTP error should not surface as an extraction failure")


def test_a_non_archive_response_is_rejected_before_extraction(tmp_path):
    result = run_script(tmp_path, PRETTY, download=b"<!DOCTYPE html><html>502 Bad Gateway")
    assert "not a gzip archive" in result.stdout
    assert "Download complete: 0/1 succeeded" in result.stdout


def test_the_diagnostic_cannot_smuggle_terminal_escapes(tmp_path):
    """Those bytes come from whatever answered the request. An error page
    carrying escapes must not be able to rewrite the output or bury it."""
    hostile = b"<!DOCTYPE html>\x1b[2J\x1b[31mgone\x1b[0m\rHTTP 200 OK\x08\x08"
    result = run_script(tmp_path, PRETTY, download=hostile)
    assert "not a gzip archive" in result.stdout
    printed = re.search(r"^\s*\(first bytes: (.*)\)$", result.stdout, re.M)
    assert printed, f"no diagnostic line:\n{result.stdout}"
    assert "DOCTYPE" in printed.group(1), "the useful part of the page was dropped"
    for forbidden in ("\x1b", "\r", "\x08", "\x00"):
        assert forbidden not in printed.group(1), (
            f"control byte {forbidden!r} reached the terminal")
