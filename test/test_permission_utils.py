"""
Tests for src.common.permission_utils's URL-credential redaction.

Covers the fix for a CodeQL clear-text-logging-of-secrets alert:
install_requirements_file() must never let a private index URL's embedded
user:pass@ credentials reach logs or its returned CompletedProcess, since
pip can echo that URL back verbatim in its own stderr/stdout on failure.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

from src.common.permission_utils import _redact_url_credentials, install_requirements_file


class TestRedactUrlCredentials:
    def test_redacts_embedded_basic_auth(self):
        text = "Could not fetch URL https://alice:s3cr3t@pypi.example.com/simple/: 403"
        redacted = _redact_url_credentials(text)
        assert "s3cr3t" not in redacted
        assert "alice" not in redacted
        assert "https://***:***@pypi.example.com/simple/" in redacted

    def test_leaves_credential_free_text_unchanged(self):
        text = "ERROR: Could not find a version that satisfies the requirement foo==1.0"
        assert _redact_url_credentials(text) == text

    def test_handles_none_and_empty(self):
        assert _redact_url_credentials(None) == ""
        assert _redact_url_credentials("") == ""

    def test_does_not_touch_denied_check_phrases(self):
        """The fixed phrases install_requirements_file greps for must survive
        redaction untouched -- they don't overlap with URL syntax, but this
        pins that assumption so a regex change can't silently break it."""
        text = "sudo: a password is required"
        assert _redact_url_credentials(text) == text


class TestInstallRequirementsFileRedaction:
    @patch('src.common.permission_utils.subprocess.run')
    def test_wrapper_path_redacts_stderr_and_stdout(self, mock_run, tmp_path):
        """safe_pip_install.sh exists in this repo, so install_requirements_file
        takes the sudo-wrapper branch; a failing result must come back
        with any embedded index-URL credentials already redacted."""
        req_file = tmp_path / "requirements.txt"
        req_file.write_text("requests\n")

        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="Looking in indexes: https://bob:hunter2@pypi.internal/simple\n",
            stderr="ERROR https://bob:hunter2@pypi.internal/simple/foo: 401",
        )

        result = install_requirements_file(req_file, timeout=5)

        assert "hunter2" not in result.stdout
        assert "hunter2" not in result.stderr
        assert "https://***:***@pypi.internal" in result.stdout
        assert "https://***:***@pypi.internal" in result.stderr

    @patch('src.common.permission_utils.subprocess.run')
    @patch('src.common.permission_utils.Path.exists', return_value=False)
    def test_no_wrapper_fallback_path_redacts_stderr_and_stdout(self, mock_exists, mock_run, tmp_path):
        """No safe_pip_install.sh wrapper -> falls straight to the
        sys.executable pip fallback (the second subprocess.run call site);
        its result must come back redacted too, independent of the wrapper
        branch's own redaction above."""
        req_file = tmp_path / "requirements.txt"
        req_file.write_text("requests\n")

        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="Looking in indexes: https://carol:swordfish@pypi.internal/simple\n",
            stderr="ERROR https://carol:swordfish@pypi.internal/simple/foo: 401",
        )

        result = install_requirements_file(req_file, timeout=5)

        assert "swordfish" not in result.stdout
        assert "swordfish" not in result.stderr
        assert "https://***:***@pypi.internal" in result.stdout
        assert "https://***:***@pypi.internal" in result.stderr
