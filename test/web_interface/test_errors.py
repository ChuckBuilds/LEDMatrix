"""
Tests for src/web_interface/errors.py — the structured error type behind
every API error response (category inference, default suggestions, the
JSON shape, and exception conversion).

Pure logic; no Flask context needed.

Regression coverage for one fixed bug: suggested_fixes used `or`, so a
caller passing [] to mean "no suggestions" silently got the default list.
"""

import pytest

from src.web_interface.errors import ErrorCategory, ErrorCode, WebInterfaceError


class TestCategoryInference:
    @pytest.mark.parametrize("code,expected", [
        (ErrorCode.CONFIG_SAVE_FAILED, ErrorCategory.CONFIGURATION),
        (ErrorCode.CONFIG_ROLLBACK_FAILED, ErrorCategory.CONFIGURATION),
        (ErrorCode.PLUGIN_NOT_FOUND, ErrorCategory.PLUGIN),
        (ErrorCode.PLUGIN_OPERATION_CONFLICT, ErrorCategory.PLUGIN),
        (ErrorCode.VALIDATION_ERROR, ErrorCategory.VALIDATION),
        (ErrorCode.SCHEMA_VALIDATION_FAILED, ErrorCategory.VALIDATION),
        (ErrorCode.INVALID_INPUT, ErrorCategory.VALIDATION),
        (ErrorCode.NETWORK_ERROR, ErrorCategory.NETWORK),
        (ErrorCode.API_ERROR, ErrorCategory.NETWORK),
        (ErrorCode.TIMEOUT, ErrorCategory.NETWORK),
        (ErrorCode.PERMISSION_DENIED, ErrorCategory.PERMISSION),
        (ErrorCode.FILE_PERMISSION_ERROR, ErrorCategory.PERMISSION),
        (ErrorCode.SYSTEM_ERROR, ErrorCategory.SYSTEM),
        (ErrorCode.SERVICE_UNAVAILABLE, ErrorCategory.SYSTEM),
        (ErrorCode.UNKNOWN_ERROR, ErrorCategory.UNKNOWN),
    ])
    def test_every_code_prefix_maps_to_its_category(self, code, expected):
        assert WebInterfaceError(code, "msg").category is expected

    def test_explicit_category_overrides_inference(self):
        error = WebInterfaceError(
            ErrorCode.CONFIG_SAVE_FAILED, "msg", category=ErrorCategory.SYSTEM)
        assert error.category is ErrorCategory.SYSTEM

    def test_every_error_code_gets_a_category(self):
        # No code may fall through uncategorized as the enum grows.
        for code in ErrorCode:
            assert isinstance(WebInterfaceError(code, "msg").category, ErrorCategory)


class TestDefaultSuggestions:
    def test_mapped_code_gets_specific_suggestions(self):
        fixes = WebInterfaceError(ErrorCode.CONFIG_SAVE_FAILED, "msg").suggested_fixes
        assert "Check available disk space" in fixes

    def test_unmapped_code_gets_generic_fallback(self):
        # PLUGIN_UPDATE_FAILED has no entry in suggestions_map.
        fixes = WebInterfaceError(ErrorCode.PLUGIN_UPDATE_FAILED, "msg").suggested_fixes
        assert fixes == ["Review error details and try again"]

    def test_explicit_suggestions_win(self):
        error = WebInterfaceError(
            ErrorCode.CONFIG_SAVE_FAILED, "msg", suggested_fixes=["Do the thing"])
        assert error.suggested_fixes == ["Do the thing"]

    def test_explicit_empty_list_is_respected(self):
        # Regression: `suggested_fixes or default` treated [] as "unset",
        # so a caller could not express "I have no suggestions".
        error = WebInterfaceError(
            ErrorCode.CONFIG_SAVE_FAILED, "msg", suggested_fixes=[])
        assert error.suggested_fixes == []

    def test_none_still_gets_defaults(self):
        error = WebInterfaceError(
            ErrorCode.CONFIG_SAVE_FAILED, "msg", suggested_fixes=None)
        assert len(error.suggested_fixes) > 0


class TestToDict:
    def test_base_keys_always_present(self):
        result = WebInterfaceError(ErrorCode.SYSTEM_ERROR, "boom").to_dict()
        assert result["status"] == "error"
        assert result["error_code"] == "SYSTEM_ERROR"
        assert result["error_category"] == "system"
        assert result["message"] == "boom"

    def test_details_included_when_set(self):
        result = WebInterfaceError(
            ErrorCode.SYSTEM_ERROR, "boom", details="disk full").to_dict()
        assert result["details"] == "disk full"

    def test_details_omitted_when_absent(self):
        assert "details" not in WebInterfaceError(ErrorCode.SYSTEM_ERROR, "boom").to_dict()

    def test_context_included_when_non_empty(self):
        result = WebInterfaceError(
            ErrorCode.SYSTEM_ERROR, "boom", context={"path": "/tmp/x"}).to_dict()
        assert result["context"] == {"path": "/tmp/x"}

    def test_empty_context_is_omitted(self):
        # Pinned as intentional, not a bug: __init__ normalizes context to
        # {}, and an empty context carries no information, so it is left out
        # rather than padding every error body with "context": {}.
        result = WebInterfaceError(ErrorCode.SYSTEM_ERROR, "boom", context={}).to_dict()
        assert "context" not in result

    def test_empty_suggestions_omitted(self):
        result = WebInterfaceError(
            ErrorCode.SYSTEM_ERROR, "boom", suggested_fixes=[]).to_dict()
        assert "suggested_fixes" not in result

    def test_is_json_serializable(self):
        import json
        error = WebInterfaceError(
            ErrorCode.NETWORK_ERROR, "boom",
            details="timeout", context={"url": "http://x"})
        assert json.loads(json.dumps(error.to_dict()))["error_code"] == "NETWORK_ERROR"


class TestFromException:
    @pytest.mark.parametrize("exc_name,expected", [
        ("ConfigError", ErrorCode.CONFIG_LOAD_FAILED),
        ("PluginError", ErrorCode.PLUGIN_LOAD_FAILED),
        ("PermissionError", ErrorCode.PERMISSION_DENIED),
        ("AccessDenied", ErrorCode.PERMISSION_DENIED),
        ("ValidationError", ErrorCode.VALIDATION_ERROR),
        ("SchemaError", ErrorCode.VALIDATION_ERROR),
        ("NetworkError", ErrorCode.NETWORK_ERROR),
        ("ConnectionError", ErrorCode.NETWORK_ERROR),
        ("TimeoutError", ErrorCode.TIMEOUT),
        ("SomethingElse", ErrorCode.UNKNOWN_ERROR),
    ])
    def test_code_inferred_from_exception_class_name(self, exc_name, expected):
        exc = type(exc_name, (Exception,), {})("boom")
        assert WebInterfaceError.from_exception(exc).error_code is expected

    def test_explicit_code_skips_inference(self):
        error = WebInterfaceError.from_exception(
            ValueError("boom"), error_code=ErrorCode.PLUGIN_NOT_FOUND)
        assert error.error_code is ErrorCode.PLUGIN_NOT_FOUND

    def test_message_is_the_safe_one_not_the_exception_text(self):
        # The raw exception text is not echoed into `message`; that field is
        # a fixed, user-facing string per code.
        error = WebInterfaceError.from_exception(ValueError("secret-ish detail"))
        assert error.message == "An unexpected error occurred"
        assert "secret-ish" not in error.message

    def test_exception_type_recorded_in_context(self):
        error = WebInterfaceError.from_exception(ValueError("boom"))
        assert error.context["exception_type"] == "ValueError"

    def test_caller_context_is_preserved_alongside_type(self):
        error = WebInterfaceError.from_exception(
            ValueError("boom"), context={"plugin_id": "clock"})
        assert error.context["plugin_id"] == "clock"
        assert error.context["exception_type"] == "ValueError"

    def test_caller_supplied_exception_type_is_overwritten(self):
        error = WebInterfaceError.from_exception(
            ValueError("boom"), context={"exception_type": "Fake"})
        assert error.context["exception_type"] == "ValueError"

    def test_original_error_retained(self):
        exc = ValueError("boom")
        assert WebInterfaceError.from_exception(exc).original_error is exc

    def test_every_code_has_a_safe_message(self):
        for code in ErrorCode:
            assert WebInterfaceError._safe_message(code)


class TestExceptionDetails:
    def test_context_dict_is_flattened(self):
        exc = ValueError("boom")
        exc.context = {"config_path": "/etc/x.json", "line": 4}
        details = WebInterfaceError._get_exception_details(exc)
        assert "config_path: /etc/x.json" in details
        assert "line: 4" in details
        assert "; " in details

    def test_exception_type_key_excluded(self):
        exc = ValueError("boom")
        exc.context = {"exception_type": "ValueError", "path": "/tmp/x"}
        details = WebInterfaceError._get_exception_details(exc)
        assert "exception_type" not in details
        assert details == "path: /tmp/x"

    def test_context_with_only_exception_type_gives_none(self):
        exc = ValueError("boom")
        exc.context = {"exception_type": "ValueError"}
        assert WebInterfaceError._get_exception_details(exc) is None

    def test_no_context_attribute_gives_none(self):
        assert WebInterfaceError._get_exception_details(ValueError("boom")) is None

    def test_non_dict_context_gives_none(self):
        exc = ValueError("boom")
        exc.context = "not a dict"
        assert WebInterfaceError._get_exception_details(exc) is None

    def test_empty_context_gives_none(self):
        exc = ValueError("boom")
        exc.context = {}
        assert WebInterfaceError._get_exception_details(exc) is None

    def test_details_flow_into_from_exception(self):
        exc = ValueError("boom")
        exc.context = {"config_path": "/etc/x.json"}
        assert "config_path" in WebInterfaceError.from_exception(exc).details
