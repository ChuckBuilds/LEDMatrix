"""The validation logging ran before separate_secrets, so it logged credentials.

api_v3's plugin-config save logged `Full config: {plugin_config}` at INFO and
`Config that failed: {plugin_config}` at ERROR. Both run *before*
separate_secrets(), so plugin_config still held the values the user just typed
into the form -- API keys and tokens went to the journal in clear text.
"""
import re
from pathlib import Path

import pytest

SOURCE = (Path(__file__).resolve().parents[2]
          / "web_interface" / "blueprints" / "api_v3.py")

#: Objects that still hold submitted secret values at the point these log
#: calls run. Interpolating one whole into a log message leaks credentials.
UNREDACTED = ("plugin_config", "secrets_config", "current_secrets")


def _logging_lines():
    for number, line in enumerate(SOURCE.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if re.match(r"logger\.(debug|info|warning|error|critical|exception)\(", stripped):
            yield number, stripped


@pytest.mark.parametrize("name", UNREDACTED)
def test_no_log_call_interpolates_a_whole_secret_bearing_object(name):
    # {name} or {name['k']} leaks; {list(name.keys())} and {len(name)} do not.
    bare = re.compile(r"\{" + re.escape(name) + r"(\[[^\]]*\])*\}")
    offenders = [f"{n}: {text}" for n, text in _logging_lines() if bare.search(text)]
    assert not offenders, (
        f"{name} still holds submitted secrets where these log calls run:\n  "
        + "\n  ".join(offenders))


def test_the_guard_would_notice_a_reintroduced_leak():
    """Pin the detector itself, so a rewrite cannot silently stop matching."""
    bare = re.compile(r"\{" + re.escape("plugin_config") + r"(\[[^\]]*\])*\}")
    assert bare.search('logger.info(f"Full config: {plugin_config}")')
    assert bare.search("logger.error(f\"{plugin_config['api_key']}\")")
    assert not bare.search('logger.info(f"{list(plugin_config.keys())}")')
