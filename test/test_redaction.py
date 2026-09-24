"""redact_credentials must stay linear in the length of its input.

Regression under test: the URL-userinfo pattern (`scheme://user:password@`)
could start a match at every letter of a run of scheme characters, and each
attempt read to the end of the run looking for `://`. A 20k-character run took
1.6s; the display service redacts every message, stack trace and context value
it publishes in the error snapshot, and re.sub holds the GIL throughout, so an
exception quoting a hex digest or a long ID stalled the render loop with it.
test_error_snapshot_cross_process.py's snapshot-size test spent 140s here.

The anchored pattern has to redact exactly what the old one did, including a
scheme that begins after digits or `+.-` in the same run.
"""

import time

import pytest

from src.redaction import redact_credentials


class TestUrlUserinfo:
    @pytest.mark.parametrize("text,expected", [
        ("401 for https://user:hunter2@example.com/api",
         "401 for https://user:<redacted>@example.com/api"),
        ("HTTPS://USER:HUNTER2@EXAMPLE.COM",
         "HTTPS://USER:<redacted>@EXAMPLE.COM"),
        ("git+ssh://deploy:hunter2@host/repo",
         "git+ssh://deploy:<redacted>@host/repo"),
        # The scheme starts after digits or +.- in the same run. Those
        # characters must survive, and the password must still go.
        ("1http://user:hunter2@host", "1http://user:<redacted>@host"),
        ("+.-http://user:hunter2@host", "+.-http://user:<redacted>@host"),
        ("a1+http://user:hunter2@host", "a1+http://user:<redacted>@host"),
        ("see a://u:first@b and c://v:second@d",
         "see a://u:<redacted>@b and c://v:<redacted>@d"),
    ])
    def test_password_is_redacted_and_the_rest_kept(self, text, expected):
        assert redact_credentials(text) == expected

    def test_a_url_without_a_password_is_untouched(self):
        text = "GET https://user@example.com/path failed"
        assert redact_credentials(text) == text


class TestLinearTime:
    # Each of these took seconds before the fix (letters ~10s at this size)
    # and takes about a millisecond after it; the bound leaves CI plenty of
    # headroom while still failing on a quadratic pattern.
    @pytest.mark.parametrize("unit", ["x", "0123456789abcdef", "1a", "a+", "1"])
    def test_long_scheme_character_runs(self, unit):
        text = (unit * 50_000)[:50_000]
        start = time.perf_counter()
        assert redact_credentials(text) == text
        elapsed = time.perf_counter() - start
        assert elapsed < 1.0, f"{elapsed:.2f}s to redact {len(text)} chars of {unit!r}"

    def test_a_credential_after_a_long_run_is_still_found(self):
        run = "ab12" * 10_000
        text = f"{run} https://user:hunter2@example.com"
        start = time.perf_counter()
        assert redact_credentials(text) == f"{run} https://user:<redacted>@example.com"
        assert time.perf_counter() - start < 1.0
