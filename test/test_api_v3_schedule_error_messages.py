"""Regression test: per-day schedule validation errors must say "time", not
"_pkg.time".

The split into a package rewrote every bare `time` reference that needed to
read through the shared module as `_pkg.time` (see the package's own
docstring on why -- tests patch it, so it has to be read back live rather
than bound by value). That rewrite was mechanical and matched the substring
"time" inside string literals and comments too, so the user-facing message

    "Invalid start time for {day}: ..."

came out as

    "Invalid start _pkg.time for {day}: ..."

in both POST /config/schedule and POST /config/dim-schedule, for both the
start and end time of a per-day entry.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from test._api_v3_test_helpers import api_v3_client, api_v3_module  # noqa: F401,E402


@pytest.mark.parametrize("url", [
    "/api/v3/config/schedule",
    "/api/v3/config/dim-schedule",
])
class TestPerDayTimeErrorsAreNotCorrupted:
    def test_invalid_start_time_message(self, api_v3_client, api_v3_module, url):
        response = api_v3_client.post(url, json={
            "mode": "per_day",
            "monday_start": "not-a-time",
        })
        assert response.status_code == 400
        message = response.get_json()["message"]
        assert "_pkg" not in message, message
        assert message.startswith("Invalid start time for monday:"), message

    def test_invalid_end_time_message(self, api_v3_client, api_v3_module, url):
        response = api_v3_client.post(url, json={
            "mode": "per_day",
            "monday_start": "07:00",
            "monday_end": "not-a-time",
        })
        assert response.status_code == 400
        message = response.get_json()["message"]
        assert "_pkg" not in message, message
        assert message.startswith("Invalid end time for monday:"), message
