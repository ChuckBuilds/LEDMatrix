"""Non-finite JSON numbers must be rejected, not raise.

json.loads accepts Infinity/-Infinity/NaN by default (they are not valid JSON,
but Python's parser emits them) and Flask's get_json passes them straight
through. int(float('inf')) raises OverflowError, which is neither ValueError
nor TypeError -- so validation blocks that carefully caught those let it
through and Flask turned it into a 500.

The damage was not the status code. /config/dim-schedule answered with
CONFIG_SAVE_FAILED and suggested "Check file permissions on config directory"
and "Check available disk space" for what was actually an invalid number.

NaN already returned 400 (int(nan) raises ValueError), which is why this only
showed up for the infinities.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from test._api_v3_test_helpers import api_v3_client, api_v3_module  # noqa: F401,E402


#: (route, body) pairs that returned 500 before OverflowError was caught.
NON_FINITE_CASES = [
    ('/api/v3/config/dim-schedule', '{"dim_brightness": Infinity}'),
    ('/api/v3/config/dim-schedule', '{"dim_brightness": -Infinity}'),
    ('/api/v3/errors/clear', '{"max_age_hours": Infinity}'),
    ('/api/v3/config/main', '{"multiplexing": Infinity}'),
    ('/api/v3/config/main', '{"row_address_type": Infinity}'),
]


@pytest.mark.parametrize("route,body", NON_FINITE_CASES)
def test_infinity_is_a_client_error_not_a_server_error(api_v3_client, route, body):
    response = api_v3_client.post(route, data=body, content_type='application/json')
    assert response.status_code != 500, (
        f"{route} with {body} raised instead of validating"
    )
    assert 400 <= response.status_code < 500, (
        f"{route} answered {response.status_code}; expected a 4xx"
    )


@pytest.mark.parametrize("route,body", [
    ('/api/v3/config/dim-schedule', '{"dim_brightness": NaN}'),
    ('/api/v3/errors/clear', '{"max_age_hours": NaN}'),
])
def test_nan_is_also_a_client_error(api_v3_client, route, body):
    """int(nan) raises ValueError so this path already worked -- pinned so a
    refactor that narrows the except tuple cannot quietly break it."""
    response = api_v3_client.post(route, data=body, content_type='application/json')
    assert 400 <= response.status_code < 500


def test_a_valid_number_is_not_rejected_by_the_guard(api_v3_client):
    """The widened except must not start swallowing ordinary input.

    Asserting on 2xx is not possible here: every manager is a MagicMock, so
    the save path fails downstream whatever is posted. What this can show is
    that a valid number gets past *validation* -- it is not answered with a
    400, and nothing in the response mentions the coercion failing.
    """
    response = api_v3_client.post(
        '/api/v3/config/dim-schedule',
        data='{"dim_brightness": 30}',
        content_type='application/json',
    )
    assert response.status_code != 400, "a valid brightness was rejected"
    assert b'must be an integer' not in response.get_data()
    assert b'OverflowError' not in response.get_data()
