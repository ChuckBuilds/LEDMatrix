"""POST /config/main refuses a malformed Vegas plugin order or exclusion list.

Both were parsed with ``except JSONDecodeError: ... = []``, so a bad value
cleared the saved list and answered 200. They now fail the save with a 400,
as plugin_rotation_order already did.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from test._api_v3_test_helpers import api_v3_client, api_v3_module  # noqa: F401,E402

URL = "/api/v3/config/main"


@pytest.fixture
def saved(api_v3_module):
    config = {"display": {"vegas_scroll": {"plugin_order": ["clock", "weather"],
                                            "excluded_plugins": ["stocks"]}}}
    cm = api_v3_module.api_v3.config_manager
    cm.load_config.return_value = config
    cm.save_config_atomic.return_value.status.value = 'success'
    return cm


@pytest.mark.parametrize("field", ["vegas_plugin_order", "vegas_excluded_plugins"])
@pytest.mark.parametrize("value", ["[not json", '{"a": 1}', "[1, 2]", 7])
def test_malformed_list_is_refused_and_nothing_is_saved(api_v3_client, saved, field, value):
    response = api_v3_client.post(URL, json={field: value})

    assert response.status_code == 400, response.get_json()
    assert field in response.get_json()["message"]
    saved.save_config_atomic.assert_not_called()
    saved.save_config.assert_not_called()


@pytest.mark.parametrize("value", ['["weather", "clock"]', ["weather", "clock"]])
def test_json_text_or_array_is_stored(api_v3_client, saved, value):
    response = api_v3_client.post(URL, json={"vegas_plugin_order": value})

    assert response.status_code == 200, response.get_json()
    stored = saved.save_config_atomic.call_args.args[0]
    assert stored["display"]["vegas_scroll"]["plugin_order"] == ["weather", "clock"]
    assert stored["display"]["vegas_scroll"]["excluded_plugins"] == ["stocks"]


def test_rotation_order_keeps_its_messages(api_v3_client, saved):
    response = api_v3_client.post(URL, json={"plugin_rotation_order": "[oops"})

    assert response.status_code == 400
    assert response.get_json()["message"] == "plugin_rotation_order must be valid JSON"
