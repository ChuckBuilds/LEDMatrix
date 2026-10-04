"""POST /api/v3/plugins/action hands ``params`` to the plugin's script intact.

The route runs the script through a generated wrapper, and the params went
into that wrapper as Python source: ``params = {json.dumps(params)}``. JSON is
not Python. ``true``, ``false`` and ``null`` are undefined names there, so any
params holding a boolean or a null died with a NameError before the script
ran. The plugin file manager's category toggle sends ``{"category_name": ...,
"enabled": true}``, so of-the-day's category toggle failed every time with
"Action failed".

The script's side of the contract is unchanged and pinned here too: the
params arrive on stdin as one JSON document, LEDMATRIX_ROOT is set, and what
the script prints to stdout is what the route parses.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from test._api_v3_test_helpers import api_v3_client, api_v3_module  # noqa: F401,E402

ACTION_URL = "/api/v3/plugins/action"

# The action script: report what it was handed, as JSON on stdout.
ECHO_SCRIPT = (
    "import json, os, sys\n"
    "raw = sys.stdin.read()\n"
    "print(json.dumps({'status': 'success', 'got': json.loads(raw),\n"
    "                  'root': os.environ.get('LEDMATRIX_ROOT')}))\n"
)


@pytest.fixture
def echo_plugin(tmp_path, api_v3_module, monkeypatch):
    plugin_dir = tmp_path / "demo"
    plugin_dir.mkdir()
    (plugin_dir / "manifest.json").write_text(json.dumps({
        "id": "demo",
        "web_ui_actions": [{"id": "toggle", "type": "script", "script": "echo.py"}],
    }), encoding="utf-8")
    (plugin_dir / "echo.py").write_text(ECHO_SCRIPT, encoding="utf-8")
    api_v3_module.api_v3.plugin_catalog.get_plugin_directory.return_value = str(plugin_dir)

    # The route runs `python3`; use this interpreter, so the test does not
    # depend on what that name resolves to here.
    real_run = subprocess.run

    def run(cmd, *args, **kwargs):
        if isinstance(cmd, list) and cmd and cmd[0] == "python3":
            cmd = [sys.executable] + cmd[1:]
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    return plugin_dir


@pytest.mark.parametrize("params", [
    {"category_name": "jokes", "enabled": True},   # the file manager's toggle
    {"category_name": "jokes", "enabled": False},
    {"filename": None},
    {"nested": {"list": [1, None, True, 2.5], "empty": {}}},
    {"text": "café ✓ \U0001F600"},
    {"text": "he said \"hi\" and 'bye' \\ ''' \"\"\" \n\t end"},
], ids=["true", "false", "null", "nested", "unicode", "quotes"])
def test_the_script_receives_the_params_it_was_sent(api_v3_client, echo_plugin, params):
    response = api_v3_client.post(ACTION_URL, json={
        "plugin_id": "demo", "action_id": "toggle", "params": params})
    body = response.get_json()
    assert response.status_code == 200, body
    assert body["got"] == params


def test_a_param_cannot_run_code_in_the_wrapper(api_v3_client, echo_plugin, tmp_path):
    marker = tmp_path / "PWNED"
    hostile = "\"}\nopen(%r, 'w').write('ran')\n#" % str(marker)
    params = {"name": hostile, "flag": True}
    response = api_v3_client.post(ACTION_URL, json={
        "plugin_id": "demo", "action_id": "toggle", "params": params})
    assert response.status_code == 200, response.get_json()
    assert response.get_json()["got"] == params
    assert not marker.exists(), "a param value ran as code"


def test_the_script_still_gets_ledmatrix_root(api_v3_client, echo_plugin, api_v3_module):
    response = api_v3_client.post(ACTION_URL, json={
        "plugin_id": "demo", "action_id": "toggle", "params": {"enabled": True}})
    assert response.status_code == 200, response.get_json()
    assert response.get_json()["root"] == str(api_v3_module.PROJECT_ROOT)
