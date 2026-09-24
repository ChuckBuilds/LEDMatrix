"""redact_credentials must stay linear in the length of its input.

Regressions under test, both quadratic regexes in src/redaction.py:

- The URL-userinfo pattern (`scheme://user:password@`) could start a match at
  every letter of a run of scheme characters, and each attempt read to the end
  of the run looking for `://`: 1.6s for a 20k-character run.
- The Authorization-header pattern had two `\\s*` separated only by an
  optional quote, so a header followed by whitespace and no credential tried
  every split of that whitespace between them: 8s for 20k spaces.

The display service redacts every message, stack trace and context value it
publishes in the error snapshot, and re.sub holds the GIL throughout, so an
exception quoting a hex digest or a long ID stalled the render loop with it.
test_error_snapshot_cross_process.py's snapshot-size test spent 140s here.

The fixed patterns have to redact exactly what the old ones did.
"""

import time

import pytest

from src.redaction import redact_credentials

# Each timed input took seconds before the fix and takes about a millisecond
# after it; the bound leaves CI plenty of headroom while still failing on a
# quadratic pattern.
_TIME_LIMIT = 1.0


def _timed(text):
    start = time.perf_counter()
    result = redact_credentials(text)
    return result, time.perf_counter() - start


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


class TestAuthorizationHeader:
    @pytest.mark.parametrize("text,expected", [
        ("Authorization: Bearer eyJ.SECRET.sig", "Authorization: Bearer <redacted>"),
        ("Proxy-Authorization: Basic dXNlcg==", "Proxy-Authorization: Basic <redacted>"),
        ("authorization: barecredential", "authorization: <redacted>"),
        # Whitespace and an opening quote around the value, in either order.
        ('authorization="  Bearer  tok"', 'authorization="  Bearer  <redacted>"'),
        ("authorization:  '  tok'", "authorization:  '  <redacted>'"),
        ("authorization:\n\tBearer tok", "authorization:\n\tBearer <redacted>"),
    ])
    def test_credential_is_redacted_and_the_rest_kept(self, text, expected):
        assert redact_credentials(text) == expected

    @pytest.mark.parametrize("text", ["authorization:   ", "authorization:   , next"])
    def test_a_header_without_a_credential_is_untouched(self, text):
        assert redact_credentials(text) == text


class TestLinearTime:
    @pytest.mark.parametrize("unit", ["x", "0123456789abcdef", "1a", "a+", "1"])
    def test_long_scheme_character_runs(self, unit):
        text = (unit * 50_000)[:50_000]
        result, elapsed = _timed(text)
        assert result == text
        assert elapsed < _TIME_LIMIT, f"{elapsed:.2f}s to redact {len(text)} chars of {unit!r}"

    def test_a_credential_after_a_long_run_is_still_found(self):
        run = "ab12" * 10_000
        result, elapsed = _timed(f"{run} https://user:hunter2@example.com")
        assert result == f"{run} https://user:<redacted>@example.com"
        assert elapsed < _TIME_LIMIT

    @pytest.mark.parametrize("header,whitespace", [
        ("authorization:", " "),
        ("Proxy-Authorization:", "\t"),
        ("authorization=", "\n"),
    ])
    def test_a_header_followed_by_long_whitespace(self, header, whitespace):
        text = header + whitespace * 20_000 + ","
        result, elapsed = _timed(text)
        assert result == text
        assert elapsed < _TIME_LIMIT, (
            f"{elapsed:.2f}s to redact {header!r} and {len(text) - len(header)} more chars")

    def test_a_credential_after_long_whitespace_is_still_found(self):
        gap = " " * 20_000
        result, elapsed = _timed(f"authorization:{gap}Bearer tok")
        assert result == f"authorization:{gap}Bearer <redacted>"
        assert elapsed < _TIME_LIMIT
