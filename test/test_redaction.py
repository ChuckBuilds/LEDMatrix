"""Tests for src/redaction.py: exact output, and time bounded by input length.

redact_credentials() runs on every message, stack trace and context value the
display service publishes in its error snapshot, and on web error responses.
re.sub holds the GIL, so a pattern that backtracks badly on some input stalls
the render loop for as long as the match takes. Which credentials must never
leak is covered in test_web_error_detail.py; this file pins what the output
is exactly, so a rewrite for speed cannot quietly change it.
"""

import time

import pytest

from src.redaction import redact_credentials


class TestAuthHeaderOutput:
    @pytest.mark.parametrize("text,expected", [
        ("Authorization: Bearer eyJ.SECRET.sig",
         "Authorization: Bearer <redacted>"),
        ("Proxy-Authorization: Basic dXNlcjpwYXNzd29yZA==",
         "Proxy-Authorization: Basic <redacted>"),
        ("headers: {'Authorization': 'Bearer eyJ.SECRET.sig'}",
         "headers: {'Authorization': 'Bearer <redacted>'}"),
        ('{"authorization" : "tok123"}',
         '{"authorization" : "<redacted>"}'),
        # Whitespace on both sides of the opening quote is kept verbatim.
        ("authorization=  '  Bearer\ttok123",
         "authorization=  '  Bearer\t<redacted>"),
        ("authorization:\n  tok123",
         "authorization:\n  <redacted>"),
        ("authorization:" + " " * 8 + "tok123",
         "authorization:" + " " * 8 + "<redacted>"),
        # A scheme with nothing after it is all there is to redact.
        ("Authorization: Bearer   ",
         "Authorization: <redacted>   "),
        # The auth pattern takes the credential up to the comma; the generic
        # key=value pattern then takes the signature.
        ("Authorization: AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE/20150830/"
         "us-east-1/iam/aws4_request, SignedHeaders=content-type;host, "
         "Signature=5d672d79c15b1316",
         "Authorization: AWS4-HMAC-SHA256 <redacted>, "
         "SignedHeaders=content-type;host, Signature=<redacted>"),
    ])
    def test_redacts_the_credential_and_nothing_else(self, text, expected):
        assert redact_credentials(text) == expected

    @pytest.mark.parametrize("text", [
        "authorization:" + " " * 8,
        "authorization: \t\r\n ",
        "authorization:   '   ",
        "authorization:   , next",
        "Proxy-Authorization:" + " " * 8 + "}",
    ])
    def test_a_separator_with_no_credential_after_it_is_left_alone(self, text):
        assert redact_credentials(text) == text


class TestAuthHeaderTime:
    """`authorization:` followed by a long whitespace run and no credential.

    The old pattern put an optional quote between two `\\s*`, so a failed
    match tried every way of splitting the run between them: 20,000 spaces
    took eight seconds, 50,000 would take close to a minute.
    """

    BUDGET_SECONDS = 1.0

    @pytest.mark.parametrize("text", [
        "authorization:" + " " * 50_000,
        "Authorization=" + " \t\r\n" * 12_500,
        "authorization:" + " " * 25_000 + "'" + " " * 25_000,
    ], ids=["spaces", "mixed-whitespace", "quote-mid-run"])
    def test_whitespace_with_no_credential_after_it(self, text):
        start = time.perf_counter()
        redacted = redact_credentials(text)
        elapsed = time.perf_counter() - start
        assert redacted == text
        assert elapsed < self.BUDGET_SECONDS, f"took {elapsed:.2f}s"

    def test_a_credential_after_a_long_run_is_still_redacted(self):
        text = "authorization:" + " " * 50_000 + "tok123"
        start = time.perf_counter()
        redacted = redact_credentials(text)
        elapsed = time.perf_counter() - start
        assert redacted == "authorization:" + " " * 50_000 + "<redacted>"
        assert elapsed < self.BUDGET_SECONDS, f"took {elapsed:.2f}s"
