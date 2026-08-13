"""
Regression tests: POST endpoints whose body is optional must accept a
request that has no body at all.

Six handlers in api_v3 read their body as ``request.get_json() or {}``.
The ``or {}`` states the intent plainly — every field is optional, so a
bodyless POST should fall back to defaults. But ``get_json()`` without
``silent=True`` raises ``UnsupportedMediaType`` when the request carries
no JSON Content-Type, and it raises *before* ``or {}`` is evaluated. Each
handler's catch-all then turned that into a 500.

So the natural way to call these endpoints — a POST with no body, which
is what curl, a fetch() without options, and most HTTP clients send by
default — failed on every one of them. The shipped UI always sends a JSON
object, which is why this went unnoticed.

This file covers the endpoints whose bodyless behaviour is not already
tested in their own suite.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from test._api_v3_test_helpers import api_v3_client, api_v3_module  # noqa: F401,E402


class TestOnDemandStart:
    URL = "/api/v3/display/on-demand/start"

    def test_bodyless_post_is_not_a_server_error(self, api_v3_client, api_v3_module):
        response = api_v3_client.post(self.URL)
        # The endpoint may still reject the request on its own terms (no
        # plugin_id, nothing to display); what it must not do is fail with
        # a 500 raised out of body parsing.
        assert response.status_code != 500

    def test_json_body_still_works(self, api_v3_client, api_v3_module):
        assert api_v3_client.post(self.URL, json={}).status_code != 500


class TestResetPluginConfig:
    URL = "/api/v3/plugins/config/reset"

    def test_bodyless_post_is_not_a_server_error(self, api_v3_client, api_v3_module):
        assert api_v3_client.post(self.URL).status_code != 500

    def test_json_body_still_works(self, api_v3_client, api_v3_module):
        assert api_v3_client.post(self.URL, json={}).status_code != 500


class TestDeleteOfTheDayJson:
    URL = "/api/v3/plugins/of-the-day/json/delete"

    def test_bodyless_post_is_not_a_server_error(self, api_v3_client, api_v3_module):
        assert api_v3_client.post(self.URL).status_code != 500

    def test_json_body_still_works(self, api_v3_client, api_v3_module):
        assert api_v3_client.post(self.URL, json={}).status_code != 500


class TestPluginLimits:
    URL = "/api/v3/plugins/clock/limits"

    def test_bodyless_post_is_not_a_server_error(self, api_v3_client, api_v3_module):
        assert api_v3_client.post(self.URL).status_code != 500


class TestNoToleratedBodyReadIsUnguarded:
    def test_every_or_default_body_read_uses_silent(self):
        """`get_json() or <default>` is a contradiction without silent=True.

        Writing `or {}` declares the body optional; omitting silent=True
        means the call raises before the default can apply. Catch the
        combination here rather than waiting for each endpoint to be
        exercised by hand.
        """
        source = Path(__file__).parent.parent.joinpath(
            "web_interface/blueprints/api_v3.py").read_text()
        offenders = [
            line.strip() for line in source.splitlines()
            if "request.get_json()" in line and " or " in line
        ]
        assert offenders == [], (
            "these reads declare a default but raise before reaching it; "
            f"use get_json(silent=True): {offenders}")
