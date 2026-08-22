"""
Tests for the response builders in src/web_interface/error_handler.py and
the success path in src/web_interface/api_helpers.py.

describe_exception() in the same module is already covered by
test/test_web_error_detail.py and is not duplicated here.

Regression coverage for one fixed bug: create_success_response used
truthiness for `message` and `metadata` while using `is not None` for
`data`, so an explicitly-passed "" or {} was silently dropped —
api_helpers.success_response() repeated the same gate, which is the path
every api_v3 endpoint actually calls.
"""

import pytest
from flask import Flask

from src.web_interface.api_helpers import success_response
from src.web_interface.error_handler import (
    create_error_response,
    create_success_response,
)
from src.web_interface.errors import ErrorCode, WebInterfaceError


@pytest.fixture
def app():
    return Flask(__name__)


class TestCreateErrorResponse:
    def test_returns_response_and_status_tuple(self, app):
        with app.test_request_context():
            response, status = create_error_response(
                ErrorCode.CONFIG_SAVE_FAILED, "could not save")
        assert status == 500
        assert response.get_json()["message"] == "could not save"

    def test_status_code_passthrough(self, app):
        with app.test_request_context():
            _, status = create_error_response(
                ErrorCode.INVALID_INPUT, "bad", status_code=400)
        assert status == 400

    def test_body_matches_the_error_dataclass(self, app):
        with app.test_request_context():
            response, _ = create_error_response(
                ErrorCode.NETWORK_ERROR, "offline",
                details="connection refused", context={"url": "http://x"})
        expected = WebInterfaceError(
            error_code=ErrorCode.NETWORK_ERROR, message="offline",
            details="connection refused", context={"url": "http://x"}).to_dict()
        assert response.get_json() == expected

    def test_none_context_produces_no_context_key(self, app):
        with app.test_request_context():
            response, _ = create_error_response(ErrorCode.SYSTEM_ERROR, "boom")
        assert "context" not in response.get_json()

    def test_suggested_fixes_passed_through(self, app):
        with app.test_request_context():
            response, _ = create_error_response(
                ErrorCode.SYSTEM_ERROR, "boom", suggested_fixes=["Try again"])
        assert response.get_json()["suggested_fixes"] == ["Try again"]


class TestCreateSuccessResponse:
    def test_bare_success(self):
        assert create_success_response() == {"status": "success"}

    def test_data_included(self):
        assert create_success_response(data={"a": 1})["data"] == {"a": 1}

    @pytest.mark.parametrize("falsy", [0, "", False, {}, []])
    def test_falsy_data_is_still_included(self, falsy):
        assert create_success_response(data=falsy)["data"] == falsy

    def test_none_data_omitted(self):
        assert "data" not in create_success_response(data=None)

    def test_message_included(self):
        assert create_success_response(message="done")["message"] == "done"

    def test_empty_message_is_still_included(self):
        # Regression: `if message:` dropped an explicitly-passed "".
        assert create_success_response(message="")["message"] == ""

    def test_none_message_omitted(self):
        assert "message" not in create_success_response(message=None)

    def test_metadata_included(self):
        assert create_success_response(metadata={"v": 1})["metadata"] == {"v": 1}

    def test_empty_metadata_is_still_included(self):
        # Regression: `if metadata:` dropped an explicitly-passed {}.
        assert create_success_response(metadata={})["metadata"] == {}

    def test_none_metadata_omitted(self):
        assert "metadata" not in create_success_response(metadata=None)


class TestSuccessResponseHelper:
    """api_helpers.success_response — the wrapper every endpoint calls."""

    def test_plain_response_has_no_metadata_block(self, app):
        with app.test_request_context():
            body = success_response(data={"a": 1}).get_json()
        assert body == {"status": "success", "data": {"a": 1}}

    def test_explicit_empty_metadata_survives_the_wrapper(self, app):
        # Regression: the wrapper re-gated metadata on truthiness after
        # create_success_response had already included it, so {} was
        # dropped again on the way out.
        with app.test_request_context():
            body = success_response(data=None, metadata={}).get_json()
        assert body["metadata"] == {}

    def test_caller_metadata_preserved(self, app):
        with app.test_request_context():
            body = success_response(metadata={"version": "1.2"}).get_json()
        assert body["metadata"]["version"] == "1.2"

    def test_timing_added_when_request_has_start_time(self, app):
        with app.test_request_context() as ctx:
            ctx.request.start_time = 0.0
            body = success_response(data={"a": 1}).get_json()
        assert "response_time_ms" in body["metadata"]

    def test_timing_merges_with_caller_metadata(self, app):
        with app.test_request_context() as ctx:
            ctx.request.start_time = 0.0
            body = success_response(metadata={"version": "1.2"}).get_json()
        assert body["metadata"]["version"] == "1.2"
        assert "response_time_ms" in body["metadata"]

    def test_caller_metadata_dict_is_not_mutated(self, app):
        # The helper used to add response_time_ms straight into the dict the
        # caller passed, so a module-level or reused metadata dict would
        # accumulate timings from previous requests.
        caller_metadata = {"version": "1.2"}
        with app.test_request_context() as ctx:
            ctx.request.start_time = 0.0
            success_response(metadata=caller_metadata)
        assert caller_metadata == {"version": "1.2"}

    def test_message_passed_through(self, app):
        with app.test_request_context():
            body = success_response(message="saved").get_json()
        assert body["message"] == "saved"
