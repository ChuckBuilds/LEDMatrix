"""
Endpoint tests for /plugins/authenticate/spotify and .../ytm.

The Spotify step-2 handler writes a Python wrapper script to a temp file
with the user's redirect URL embedded in it, then runs that file through
subprocess. That is the most dangerous shape in the blueprint and had no
tests: the URL is user input reaching generated source code.

The two endpoints are NOT symmetrical, despite the matching names. Only
Spotify has a two-step flow, a wrapper script, and a redirect_url; YTM
just runs its script directly.

Regression coverage for one fixed bug: the wrapper file was unlinked in
the success/failure branch and again in the TimeoutExpired handler, so
any other failure from subprocess.run — the interpreter missing, a fork
failure, an interrupted call — left a temp file containing the user's
redirect URL behind.
"""

import ast
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from test._api_v3_test_helpers import api_v3_client, api_v3_module  # noqa: F401,E402


@pytest.fixture
def plugin_dir(tmp_path, api_v3_module):
    """A plugin directory containing both auth scripts."""
    directory = tmp_path / "plugins" / "ledmatrix-music"
    directory.mkdir(parents=True)
    (directory / "authenticate_spotify.py").write_text("print('spotify')\n")
    (directory / "authenticate_ytm.py").write_text("print('ytm')\n")
    api_v3_module.api_v3.plugin_manager.get_plugin_directory.return_value = str(directory)
    return directory


def completed(returncode=0, stdout="ok", stderr=""):
    return subprocess.CompletedProcess(
        args=["python3"], returncode=returncode, stdout=stdout, stderr=stderr)


class TestSpotifyPreconditions:
    URL = "/api/v3/plugins/authenticate/spotify"

    def test_missing_plugin_directory_is_404(self, api_v3_client, api_v3_module, tmp_path):
        api_v3_module.api_v3.plugin_manager.get_plugin_directory.return_value = str(
            tmp_path / "not-installed")
        response = api_v3_client.post(self.URL, json={})
        assert response.status_code == 404
        assert response.get_json()["message"] == "Plugin not found"

    def test_none_plugin_directory_is_404(self, api_v3_client, api_v3_module):
        api_v3_module.api_v3.plugin_manager.get_plugin_directory.return_value = None
        assert api_v3_client.post(self.URL, json={}).status_code == 404

    def test_missing_auth_script_is_404(self, api_v3_client, plugin_dir):
        (plugin_dir / "authenticate_spotify.py").unlink()
        response = api_v3_client.post(self.URL, json={})
        assert response.status_code == 404
        assert "script not found" in response.get_json()["message"]


class TestSpotifyStepTwo:
    """redirect_url present — the wrapper-script path."""

    URL = "/api/v3/plugins/authenticate/spotify"

    def test_success(self, api_v3_client, plugin_dir):
        with patch.object(subprocess, "run", return_value=completed(0, "done")):
            response = api_v3_client.post(self.URL, json={"redirect_url": "http://cb/?code=x"})
        assert response.status_code == 200
        body = response.get_json()
        assert body["status"] == "success"
        assert body["output"] == "done"

    def test_script_failure_is_a_400_with_combined_output(self, api_v3_client, plugin_dir):
        with patch.object(subprocess, "run", return_value=completed(1, "out", "err")):
            response = api_v3_client.post(self.URL, json={"redirect_url": "http://cb/?code=x"})
        assert response.status_code == 400
        assert response.get_json()["output"] == "outerr"

    def test_timeout_is_a_408(self, api_v3_client, plugin_dir):
        with patch.object(subprocess, "run",
                          side_effect=subprocess.TimeoutExpired("python3", 120)):
            response = api_v3_client.post(self.URL, json={"redirect_url": "http://cb/?code=x"})
        assert response.status_code == 408
        assert "timed out" in response.get_json()["message"]

    def test_runs_a_list_argv_never_a_shell(self, api_v3_client, plugin_dir):
        with patch.object(subprocess, "run", return_value=completed()) as run:
            api_v3_client.post(self.URL, json={"redirect_url": "http://cb/?code=x"})
        args, kwargs = run.call_args
        assert isinstance(args[0], list)
        assert args[0][0] == "python3"
        assert kwargs.get("shell") in (None, False)

    def test_timeout_is_bounded(self, api_v3_client, plugin_dir):
        with patch.object(subprocess, "run", return_value=completed()) as run:
            api_v3_client.post(self.URL, json={"redirect_url": "http://cb/?code=x"})
        assert run.call_args.kwargs["timeout"] == 120


class TestSpotifyWrapperCleanup:
    URL = "/api/v3/plugins/authenticate/spotify"

    def _wrapper_paths_after(self, api_v3_client, run_mock):
        """Run the endpoint and return the wrapper path subprocess saw."""
        seen = {}

        def capture(args, **kwargs):
            seen["path"] = args[1]
            return run_mock(args, **kwargs)

        with patch.object(subprocess, "run", side_effect=capture):
            api_v3_client.post(self.URL, json={"redirect_url": "http://cb/?code=x"})
        return seen["path"]

    def test_removed_after_success(self, api_v3_client, plugin_dir):
        path = self._wrapper_paths_after(api_v3_client, lambda *a, **kw: completed())
        assert not os.path.exists(path)

    def test_removed_after_script_failure(self, api_v3_client, plugin_dir):
        path = self._wrapper_paths_after(
            api_v3_client, lambda *a, **kw: completed(1, "out", "err"))
        assert not os.path.exists(path)

    def test_removed_after_timeout(self, api_v3_client, plugin_dir):
        def raise_timeout(*a, **kw):
            raise subprocess.TimeoutExpired("python3", 120)
        path = self._wrapper_paths_after(api_v3_client, raise_timeout)
        assert not os.path.exists(path)

    def test_removed_when_subprocess_cannot_start(self, api_v3_client, plugin_dir):
        # Regression: cleanup lived in the success/failure branch and in the
        # TimeoutExpired handler only. An OSError from subprocess.run itself
        # — no interpreter, fork failure — skipped both and left the wrapper,
        # which contains the user's redirect URL, on disk.
        def raise_oserror(*a, **kw):
            raise OSError("[Errno 12] Cannot allocate memory")
        path = self._wrapper_paths_after(api_v3_client, raise_oserror)
        assert not os.path.exists(path)


class TestSpotifyRedirectUrlIsNotInjectable:
    """The wrapper embeds redirect_url into generated Python source."""

    URL = "/api/v3/plugins/authenticate/spotify"

    ADVERSARIAL = [
        '''http://cb/?code=x"''',
        """http://cb/?code=x'""",
        'http://cb/?code=x\\',
        'http://cb/?code=x\nimport os; os.system("id")',
        'http://cb/?code=x"""\nimport os\n"""',
        "http://cb/?code=x'''",
        'http://cb/?code=x\\"\\n',
        '"; import os; os.system("id"); "',
    ]

    def _wrapper_source(self, api_v3_client, redirect_url):
        captured = {}

        def capture(args, **kwargs):
            captured["source"] = Path(args[1]).read_text()
            return completed()

        with patch.object(subprocess, "run", side_effect=capture):
            api_v3_client.post(self.URL, json={"redirect_url": redirect_url})
        return captured["source"]

    @pytest.mark.parametrize("redirect_url", ADVERSARIAL)
    def test_wrapper_is_still_valid_python(self, api_v3_client, plugin_dir, redirect_url):
        # If escaping failed, the generated file would not parse at all.
        source = self._wrapper_source(api_v3_client, redirect_url)
        ast.parse(source)

    @pytest.mark.parametrize("redirect_url", ADVERSARIAL)
    def test_url_survives_as_one_string_literal(
            self, api_v3_client, plugin_dir, redirect_url):
        # Stronger than "it parses": the URL must still be a single string
        # assigned to redirect_url, not code that escaped into statements.
        source = self._wrapper_source(api_v3_client, redirect_url)
        tree = ast.parse(source)
        assigned = [
            node.value.value for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Constant)
            and any(getattr(t, "id", None) == "redirect_url" for t in node.targets)
        ]
        assert assigned == [redirect_url.strip()]

    def test_injected_call_does_not_become_a_statement(self, api_v3_client, plugin_dir):
        source = self._wrapper_source(
            api_v3_client, 'http://cb/\nimport os; os.system("id")')
        tree = ast.parse(source)
        imported = {
            alias.name for node in ast.walk(tree)
            if isinstance(node, ast.Import) for alias in node.names
        }
        # The wrapper legitimately imports sys, subprocess and os; what it
        # must not gain is a *call* smuggled in through the URL.
        calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "system"
        ]
        assert calls == []


class TestSpotifyStepOne:
    """No redirect_url — the OAuth-URL path, which imports the script."""

    URL = "/api/v3/plugins/authenticate/spotify"

    def test_script_without_credentials_helper_is_an_error(
            self, api_v3_client, plugin_dir):
        # The stub script defines neither get_auth_url nor
        # load_spotify_credentials, so no URL can be produced.
        response = api_v3_client.post(self.URL, json={})
        assert response.status_code in (400, 500)
        assert response.get_json()["status"] == "error"

    def test_unusable_credentials_do_not_leak_into_the_response(
            self, api_v3_client, plugin_dir):
        (plugin_dir / "authenticate_spotify.py").write_text(
            "def load_spotify_credentials():\n"
            "    return ('id-abc', 'super-secret-value', None)\n"
        )
        response = api_v3_client.post(self.URL, json={})
        assert "super-secret-value" not in response.get_data(as_text=True)

    def test_script_raising_on_import_is_handled(self, api_v3_client, plugin_dir):
        (plugin_dir / "authenticate_spotify.py").write_text("raise RuntimeError('boom')\n")
        response = api_v3_client.post(self.URL, json={})
        assert response.status_code == 500
        assert response.get_json()["status"] == "error"

    def test_bodyless_post_reaches_step_one(self, api_v3_client, plugin_dir):
        # Covered by the silent=True fix: previously a 500 from body parsing.
        response = api_v3_client.post(self.URL)
        assert response.status_code in (400, 500)
        assert response.get_json()["status"] == "error"

    def test_whitespace_redirect_url_is_treated_as_absent(
            self, api_v3_client, plugin_dir):
        with patch.object(subprocess, "run", return_value=completed()) as run:
            api_v3_client.post(self.URL, json={"redirect_url": "   "})
        # Step 2 never runs, so no wrapper is executed.
        run.assert_not_called()


class TestYouTubeMusic:
    """No wrapper script and no redirect_url — deliberately not symmetric."""

    URL = "/api/v3/plugins/authenticate/ytm"

    def test_missing_plugin_directory_is_404(self, api_v3_client, api_v3_module, tmp_path):
        api_v3_module.api_v3.plugin_manager.get_plugin_directory.return_value = str(
            tmp_path / "not-installed")
        assert api_v3_client.post(self.URL).status_code == 404

    def test_missing_script_is_404(self, api_v3_client, plugin_dir):
        (plugin_dir / "authenticate_ytm.py").unlink()
        response = api_v3_client.post(self.URL)
        assert response.status_code == 404
        assert "script not found" in response.get_json()["message"]

    def test_success(self, api_v3_client, plugin_dir):
        with patch.object(subprocess, "run", return_value=completed(0, "authorized")):
            response = api_v3_client.post(self.URL)
        assert response.status_code == 200
        assert response.get_json()["output"] == "authorized"

    def test_failure_is_a_400_with_combined_output(self, api_v3_client, plugin_dir):
        with patch.object(subprocess, "run", return_value=completed(1, "out", "err")):
            response = api_v3_client.post(self.URL)
        assert response.status_code == 400
        assert response.get_json()["output"] == "outerr"

    def test_timeout_is_a_408(self, api_v3_client, plugin_dir):
        with patch.object(subprocess, "run",
                          side_effect=subprocess.TimeoutExpired("python3", 60)):
            assert api_v3_client.post(self.URL).status_code == 408

    def test_runs_the_script_directly_without_a_shell(self, api_v3_client, plugin_dir):
        with patch.object(subprocess, "run", return_value=completed()) as run:
            api_v3_client.post(self.URL)
        args, kwargs = run.call_args
        assert args[0][0] == "python3"
        assert args[0][1].endswith("authenticate_ytm.py")
        assert kwargs.get("shell") in (None, False)
        assert kwargs["timeout"] == 60
