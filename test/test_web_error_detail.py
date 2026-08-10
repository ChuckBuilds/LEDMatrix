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
        # requests quotes the URL it failed on, and both of these forms turn
        # up in real client exceptions.
        ("401 for https://user:hunter2@example.com/api", "hunter2"),
        ("headers: {'Authorization': 'Bearer eyJ.SECRET.sig'}", "eyJ.SECRET.sig"),
        ("Authorization: Basic dXNlcjpwYXNzd29yZA==", "dXNlcjpwYXNzd29yZA=="),
        ("Proxy-Authorization: Bearer ptok999", "ptok999"),
    ])
    def test_credentials_never_reach_the_response(self, secret_text, leaked):
        detail = describe_exception(RuntimeError(secret_text))
        assert leaked not in detail
        assert "<redacted>" in detail

    def test_the_parameter_name_survives_redaction(self):
        # Knowing *which* credential was involved is part of the diagnosis.
        detail = describe_exception(RuntimeError("https://x/y?api_key=SEC123"))
        assert "api_key" in detail

    def test_auth_scheme_and_username_survive(self):
        # Which kind of credential, and whose, without the credential itself.
        assert "Bearer" in describe_exception(
            RuntimeError("Authorization: Bearer eyJ.SECRET.sig"))
        assert "user" in describe_exception(
            RuntimeError("https://user:hunter2@example.com"))

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
        """Every generic-message handler must log a traceback and return detail.

        Nine of them bound `e` and never used it, so the promised log entry was
        never written either. Checking merely that *something* was logged is
        too weak -- a `logger.info("failed")` would satisfy it while throwing
        the exception away just as completely, so this asserts the two things
        that actually make the failure diagnosable: an error-level record with
        the traceback, and the sanitized detail in the response.
        """
        import ast

        src = open("web_interface/blueprints/api_v3.py").read()
        tree = ast.parse(src)
        generic = "An error occurred; see logs for details"

        def logs_a_traceback(handler):
            """An error/exception-level log call carrying exc_info."""
            for call in [n for n in ast.walk(handler) if isinstance(n, ast.Call)]:
                func = call.func
                if not isinstance(func, ast.Attribute):
                    continue
                if func.attr == "exception":       # implies exc_info
                    return True
                if func.attr not in ("error", "critical"):
                    continue
                if any(kw.arg == "exc_info" and getattr(kw.value, "value", False) is True
                       for kw in call.keywords):
                    return True
            return False

        def returns_the_detail(handler):
            """describe_exception() called on this handler's bound exception."""
            for call in [n for n in ast.walk(handler) if isinstance(n, ast.Call)]:
                name = call.func.id if isinstance(call.func, ast.Name) else None
                if name != "describe_exception":
                    continue
                if handler.name is None:
                    return True     # bare `except:` cannot name it; accept
                if any(isinstance(a, ast.Name) and a.id == handler.name
                       for a in call.args):
                    return True
            return False

        offenders = []
        for h in [n for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler)]:
            seg = ast.get_source_segment(src, h) or ""
            if generic not in seg:
                continue
            missing = []
            if not logs_a_traceback(h):
                missing.append("error-level log with exc_info")
            if not returns_the_detail(h):
                missing.append("describe_exception(e) in the response")
            if missing:
                offenders.append((h.lineno, missing))

        assert not offenders, (
            "handlers returning the generic message without %s: %r"
            % ("both a traceback log and the detail", offenders))

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
