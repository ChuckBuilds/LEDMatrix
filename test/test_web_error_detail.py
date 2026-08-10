"""Tests for surfacing the underlying error in web responses.

Regression under test: every failing endpoint returned "An error occurred; see
logs for details" and nothing else. On a device whose storage was failing that
sentence came back from the restart action, from /system/status, and from
/logs -- the log viewer itself -- because journalctl could not be executed. The
exception underneath said `[Errno 5] Input/output error: 'systemctl'`, which
names the fault outright, and nine handlers were discarding it entirely rather
than even logging it.
"""

import pytest

from src.web_interface.error_handler import describe_exception


class TestDescribeException:
    def test_names_the_type_and_message(self):
        detail = describe_exception(OSError(5, "Input/output error", "systemctl"))
        assert detail == "OSError: [Errno 5] Input/output error: 'systemctl'"

    def test_the_reported_failure_is_legible(self):
        # The whole point: this string is the diagnosis.
        assert "Input/output error" in describe_exception(
            OSError(5, "Input/output error", "systemctl"))

    def test_a_bare_exception_still_names_its_type(self):
        # A PermissionError with no message still says more than "unknown".
        assert describe_exception(PermissionError()) == "PermissionError"
        assert describe_exception(Exception()) == "Exception"

    def test_message_is_kept_when_present(self):
        assert describe_exception(ValueError("bad port")) == "ValueError: bad port"


class TestCredentialRedaction:
    """Exception text quotes URLs, and plugins authenticate by query string."""

    @pytest.mark.parametrize("secret_text,leaked", [
        ("failed: https://api.x.com/v1?api_key=SEC123&city=Tampa", "SEC123"),
        ("token=abcdef123456 was rejected", "abcdef123456"),
        ("connect failed password=hunter2", "hunter2"),
        ("GET /?access_token=zzz999", "zzz999"),
        ('{"secret": "topsecret"}', "topsecret"),
    ])
    def test_credentials_never_reach_the_response(self, secret_text, leaked):
        detail = describe_exception(RuntimeError(secret_text))
        assert leaked not in detail
        assert "<redacted>" in detail

    def test_the_parameter_name_survives_redaction(self):
        # Knowing *which* credential was involved is part of the diagnosis.
        detail = describe_exception(RuntimeError("https://x/y?api_key=SEC123"))
        assert "api_key" in detail

    def test_non_secret_context_is_preserved(self):
        detail = describe_exception(RuntimeError("https://api.x.com/v1?city=Tampa"))
        assert "city=Tampa" in detail
        assert "<redacted>" not in detail


class TestBounds:
    def test_long_messages_are_truncated(self):
        detail = describe_exception(ValueError("x" * 5000))
        assert len(detail) <= 400

    def test_newlines_are_collapsed_to_one_line(self):
        detail = describe_exception(ValueError("line one\nline two\tthree"))
        assert "\n" not in detail and "\t" not in detail
        assert detail == "ValueError: line one line two three"

    def test_custom_length_is_honoured(self):
        assert len(describe_exception(ValueError("y" * 500), max_length=50)) <= 50


class TestHandlersCarryDetail:
    """The response shape callers actually see."""

    def test_no_api_v3_handler_discards_its_exception(self):
        # Nine of them bound `e` and never used it, so the promised log entry
        # was never written either.
        import ast
        import re

        src = open("web_interface/blueprints/api_v3.py").read()
        tree = ast.parse(src)
        generic = "An error occurred; see logs for details"
        silent = []
        for h in [n for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler)]:
            seg = ast.get_source_segment(src, h) or ""
            if generic not in seg:
                continue
            if not re.search(
                    r"\b(logger|logging|current_app\.logger)\s*\.\s*"
                    r"(error|exception|warning|critical|info)\b", seg):
                silent.append(h.lineno)
        assert not silent, (
            "handlers returning the generic message without logging: %r" % silent)

    def test_global_handler_reports_the_underlying_error(self):
        from flask import Flask, jsonify

        app = Flask(__name__)

        @app.errorhandler(Exception)
        def handle(error):
            return jsonify({
                "status": "error",
                "error_code": "UNKNOWN_ERROR",
                "message": "An error occurred; see logs for details",
                "details": describe_exception(error),
            }), 500

        @app.route("/boom")
        def boom():
            raise OSError(5, "Input/output error", "systemctl")

        client = app.test_client()
        body = client.get("/boom").get_json()
        assert body["error_code"] == "UNKNOWN_ERROR"
        assert "Input/output error" in body["details"]
