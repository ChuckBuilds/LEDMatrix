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


#: (route, field) that returned 500 before OverflowError was caught. Both
#: infinity signs are exercised: int() raises OverflowError for either, but
#: only one of them was in the original report, and a guard that special-cased
#: the sign would pass a one-sided test.
NON_FINITE_ROUTES = [
    ('/api/v3/config/dim-schedule', 'dim_brightness'),
    ('/api/v3/errors/clear', 'max_age_hours'),
    ('/api/v3/config/main', 'multiplexing'),
    ('/api/v3/config/main', 'row_address_type'),
]
NON_FINITE_CASES = [
    (route, '{"%s": %s}' % (field, literal))
    for route, field in NON_FINITE_ROUTES
    for literal in ('Infinity', '-Infinity')
]


@pytest.mark.parametrize("route,body", NON_FINITE_CASES)
def test_infinity_is_a_client_error_not_a_server_error(api_v3_client, route, body):
    """Exactly 400, not merely "some 4xx".

    Accepting any 4xx would let a 404 pass, so renaming one of these routes
    would leave the test green while testing nothing -- the failure mode this
    whole file exists to catch.
    """
    response = api_v3_client.post(route, data=body, content_type='application/json')
    assert response.status_code == 400, (
        f"{route} with {body} answered {response.status_code}; expected 400"
    )


@pytest.mark.parametrize("route,body", [
    ('/api/v3/config/dim-schedule', '{"dim_brightness": NaN}'),
    ('/api/v3/errors/clear', '{"max_age_hours": NaN}'),
])
def test_nan_is_also_a_client_error(api_v3_client, route, body):
    """int(nan) raises ValueError so this path already worked -- pinned so a
    refactor that narrows the except tuple cannot quietly break it."""
    response = api_v3_client.post(route, data=body, content_type='application/json')
    assert response.status_code == 400


def test_a_valid_number_is_accepted(api_v3_client, api_v3_module, monkeypatch):
    """Prove the widened except did not start swallowing ordinary input.

    Asserting "not a 400" would not show that: the mocked save path fails for
    any input, so the assertion would hold even if validation had rejected the
    value. Give load_config a real dict and stub the atomic save, and the
    endpoint reaches its success response -- which only happens if 30 passed
    validation.
    """
    api_v3_module.api_v3.config_manager.load_config.return_value = {}
    monkeypatch.setattr(api_v3_module, '_save_config_atomic',
                        lambda *a, **k: (True, ''))
    response = api_v3_client.post(
        '/api/v3/config/dim-schedule',
        data='{"dim_brightness": 30}',
        content_type='application/json',
    )
    assert response.status_code == 200, response.get_data(as_text=True)[:200]
