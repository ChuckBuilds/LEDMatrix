"""
Tests for src.common.permission_utils.

Covers two things:

* URL-credential redaction -- the fix for a CodeQL clear-text-logging-of-secrets
  alert: install_requirements_file() must never let a private index URL's
  embedded user:pass@ credentials reach logs or its returned CompletedProcess,
  since pip can echo that URL back verbatim in its own stderr/stdout on failure.
* ensure_shared_group_ownership() staying a silent no-op on platforms without
  the POSIX ownership APIs.
"""

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.common.permission_utils import (
    _redact_url_credentials,
    ensure_shared_group_ownership,
    install_requirements_file,
)


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


class TestEnsureSharedGroupOwnership:
    """The chgrp self-heal must stay a no-op wherever it cannot apply.

    ``ConfigManager.load_config()`` calls this on every load that finds a
    secrets file, and its callers only ever catch ``OSError``. An
    ``AttributeError`` from looking up a POSIX-only name on Windows therefore
    escaped all the way out of ``load_config``, and took the import of
    ``web_interface.app`` with it on any Windows checkout that had a
    ``config/config_secrets.json``.
    """

    def test_no_geteuid_is_a_silent_no_op(self, monkeypatch, tmp_path):
        secrets = tmp_path / "config_secrets.json"
        secrets.write_text("{}", encoding='utf-8')
        monkeypatch.delattr(os, "geteuid", raising=False)
        monkeypatch.delattr(os, "chown", raising=False)

        ensure_shared_group_ownership(secrets)  # must not raise

    def test_non_root_never_chowns(self, monkeypatch, tmp_path):
        chown = MagicMock()
        monkeypatch.setattr(os, "geteuid", lambda: 1000, raising=False)
        monkeypatch.setattr(os, "chown", chown, raising=False)

        ensure_shared_group_ownership(tmp_path / "config_secrets.json")

        chown.assert_not_called()

    def test_root_chowns_a_file_whose_group_is_wrong(self, monkeypatch, tmp_path):
        secrets = tmp_path / "config_secrets.json"
        secrets.write_text("{}", encoding='utf-8')
        # Any gid the file does not already have, so the chgrp is due.
        wanted = secrets.stat().st_gid + 1
        chown = MagicMock()
        monkeypatch.setattr(os, "geteuid", lambda: 0, raising=False)
        monkeypatch.setattr(os, "chown", chown, raising=False)
        monkeypatch.setattr('src.common.permission_utils.get_shared_group_gid',
                            lambda: wanted)

        ensure_shared_group_ownership(secrets)

        chown.assert_called_once_with(secrets, -1, wanted)
