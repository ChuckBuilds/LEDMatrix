"""
Tests for src/web_interface/validators.py.

dedup_unique_arrays is already covered by test_dedup_unique_arrays.py and
is not repeated here; this file covers the other eight functions, none of
which had any tests.

Regression coverage for three fixed bugs:
- validate_numeric_range accepted True/False, since bool subclasses int.
- validate_file_upload lowercased the filename's extension but not the
  caller's allowed_extensions list, so ['.TTF'] rejected 'font.ttf'.
- validate_image_url only checked for '..' inside the relative-path
  branch, so http://host/../secret passed validation untouched.
"""

import pytest

from src.web_interface.validators import (
    escape_html,
    sanitize_plugin_config,
    validate_file_upload,
    validate_font_awesome_class,
    validate_image_url,
    validate_mime_type,
    validate_numeric_range,
    validate_string_length,
)


class TestEscapeHtml:
    def test_escapes_all_five_entities(self):
        assert escape_html("""<a href="x">O'Neill & co</a>""") == (
            "&lt;a href=&quot;x&quot;&gt;O&#x27;Neill &amp; co&lt;/a&gt;")

    def test_ampersand_is_escaped_first_so_nothing_double_escapes(self):
        # If '<' were replaced before '&', the '&' of '&lt;' would be
        # escaped again into '&amp;lt;'.
        assert escape_html("<") == "&lt;"
        assert escape_html("&") == "&amp;"
        assert escape_html("&<") == "&amp;&lt;"

    def test_plain_text_unchanged(self):
        assert escape_html("hello world") == "hello world"

    def test_non_string_is_coerced(self):
        assert escape_html(42) == "42"
        assert escape_html(None) == "None"

    def test_script_tag_neutralized(self):
        assert "<script>" not in escape_html("<script>alert(1)</script>")


class TestValidateImageUrl:
    @pytest.mark.parametrize("url", [
        "javascript:alert(1)",
        "JavaScript:alert(1)",
        "JAVASCRIPT:alert(1)",
        "data:text/html;base64,PHNjcmlwdD4=",
        "vbscript:msgbox(1)",
        "file:///etc/passwd",
    ])
    def test_dangerous_protocols_rejected(self, url):
        valid, error = validate_image_url(url)
        assert valid is False and "protocol" in error.lower()

    @pytest.mark.parametrize("url", [
        "http://x/a.png?onerror=alert(1)",
        "http://x/a.png#onload=alert(1)",
        "http://x/onclick=alert(1).png",
    ])
    def test_event_handlers_rejected(self, url):
        valid, error = validate_image_url(url)
        assert valid is False and "Event handlers" in error

    @pytest.mark.parametrize("url", ["", None, 123, []])
    def test_empty_or_non_string_rejected(self, url):
        assert validate_image_url(url)[0] is False

    def test_http_and_https_allowed(self):
        assert validate_image_url("http://example.com/logo.png") == (True, None)
        assert validate_image_url("https://example.com/logo.png") == (True, None)

    def test_other_schemes_rejected(self):
        valid, error = validate_image_url("ftp://example.com/logo.png")
        assert valid is False and "http://" in error

    def test_relative_path_allowed(self):
        assert validate_image_url("/static/logo.png") == (True, None)

    def test_protocol_relative_url_rejected(self):
        assert validate_image_url("//evil.com/logo.png")[0] is False

    def test_relative_traversal_rejected(self):
        assert validate_image_url("/static/../../etc/passwd")[0] is False

    def test_absolute_url_traversal_rejected(self):
        # Regression: the '..' check used to sit inside the leading-slash
        # branch, so an absolute URL skipped it entirely.
        valid, error = validate_image_url("http://example.com/../secret")
        assert valid is False and "traversal" in error.lower()

    def test_bare_traversal_rejected(self):
        assert validate_image_url("../../etc/passwd")[0] is False


class TestValidateFontAwesomeClass:
    @pytest.mark.parametrize("cls", ["fa-star", "fas fa-star", "fa-solid fa-house"])
    def test_valid_classes_accepted(self, cls):
        assert validate_font_awesome_class(cls) == (True, None)

    @pytest.mark.parametrize("cls", ["star", "glyphicon-star", ""])
    def test_classes_without_fa_prefix_rejected(self, cls):
        assert validate_font_awesome_class(cls)[0] is False

    def test_injection_attempt_rejected(self):
        assert validate_font_awesome_class('fa-star" onload="alert(1)')[0] is False

    def test_angle_brackets_rejected(self):
        assert validate_font_awesome_class("<script>fa-star</script>")[0] is False

    def test_non_string_rejected(self):
        valid, error = validate_font_awesome_class(None)
        assert valid is False and "string" in error

    def test_explicit_fa_check_is_unreachable_but_harmless(self):
        # Characterized, not fixed: the regex already requires 'fa-', so the
        # follow-up `if 'fa-' not in class_name` can never fire. Anything
        # lacking 'fa-' is rejected by the pattern first, with the pattern's
        # own message.
        valid, error = validate_font_awesome_class("star")
        assert valid is False
        assert error == "Invalid Font Awesome class name format"


class TestValidateFileUpload:
    def test_plain_filename_accepted(self):
        assert validate_file_upload("logo.png") == (True, None)

    @pytest.mark.parametrize("filename", [
        "../etc/passwd", "dir/file.png", "dir\\file.png", "..\\..\\secrets",
    ])
    def test_traversal_characters_rejected(self, filename):
        valid, error = validate_file_upload(filename)
        assert valid is False and "invalid characters" in error

    @pytest.mark.parametrize("filename", ["", None, 123])
    def test_empty_or_non_string_rejected(self, filename):
        assert validate_file_upload(filename)[0] is False

    def test_allowed_extension_accepted(self):
        assert validate_file_upload("font.ttf", allowed_extensions=[".ttf", ".otf"]) == (True, None)

    def test_disallowed_extension_rejected(self):
        valid, error = validate_file_upload("evil.exe", allowed_extensions=[".ttf"])
        assert valid is False and "extension" in error

    def test_uppercase_filename_extension_matches(self):
        assert validate_file_upload("FONT.TTF", allowed_extensions=[".ttf"]) == (True, None)

    def test_uppercase_allowed_list_matches(self):
        # Regression: only the filename side was lowercased, so a caller
        # passing ['.TTF'] rejected every valid .ttf upload.
        assert validate_file_upload("font.ttf", allowed_extensions=[".TTF"]) == (True, None)

    def test_no_extension_list_skips_the_check(self):
        assert validate_file_upload("anything.xyz") == (True, None)


class TestValidateMimeType:
    def test_known_type_accepted(self):
        assert validate_mime_type("logo.png", ["image/png"]) == (True, None)

    def test_mismatched_type_rejected(self):
        valid, error = validate_mime_type("logo.png", ["image/jpeg"])
        assert valid is False and "not allowed" in error

    def test_undeterminable_type_rejected(self):
        valid, error = validate_mime_type("mystery.zzz", ["image/png"])
        assert valid is False and "Could not determine" in error

    def test_guess_type_failure_is_caught(self, monkeypatch):
        import mimetypes
        monkeypatch.setattr(mimetypes, "guess_type",
                            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")))
        valid, error = validate_mime_type("logo.png", ["image/png"])
        assert valid is False and "Error validating MIME type" in error


class TestValidateNumericRange:
    def test_value_in_range(self):
        assert validate_numeric_range(5, min_val=0, max_val=10) == (True, None)

    def test_boundaries_are_inclusive(self):
        assert validate_numeric_range(0, min_val=0, max_val=10) == (True, None)
        assert validate_numeric_range(10, min_val=0, max_val=10) == (True, None)

    def test_below_minimum_rejected(self):
        valid, error = validate_numeric_range(-1, min_val=0)
        assert valid is False and "at least" in error

    def test_above_maximum_rejected(self):
        valid, error = validate_numeric_range(11, max_val=10)
        assert valid is False and "at most" in error

    def test_floats_accepted(self):
        assert validate_numeric_range(2.5, min_val=0, max_val=10) == (True, None)

    def test_no_bounds_accepts_any_number(self):
        assert validate_numeric_range(-9999) == (True, None)

    @pytest.mark.parametrize("value", ["5", None, [], {}])
    def test_non_numeric_rejected(self, value):
        valid, error = validate_numeric_range(value, min_val=0, max_val=10)
        assert valid is False and error == "Value must be a number"

    @pytest.mark.parametrize("value", [True, False])
    def test_booleans_rejected(self, value):
        # Regression: bool subclasses int, so True passed the isinstance
        # check and then compared as 1 against the range.
        valid, error = validate_numeric_range(value, min_val=0, max_val=10)
        assert valid is False and error == "Value must be a number"


class TestValidateStringLength:
    def test_within_range(self):
        assert validate_string_length("hello", min_length=1, max_length=10) == (True, None)

    def test_boundaries_are_inclusive(self):
        assert validate_string_length("abc", min_length=3, max_length=3) == (True, None)

    def test_too_short_rejected(self):
        valid, error = validate_string_length("", min_length=1)
        assert valid is False and "at least" in error

    def test_too_long_rejected(self):
        valid, error = validate_string_length("abcdef", max_length=3)
        assert valid is False and "at most" in error

    def test_non_string_rejected(self):
        valid, error = validate_string_length(123, max_length=10)
        assert valid is False and "must be a string" in error

    def test_no_bounds_accepts_anything(self):
        assert validate_string_length("") == (True, None)


class TestSanitizePluginConfig:
    def test_valid_keys_and_scalars_kept(self):
        config = {"enabled": True, "count": 3, "ratio": 1.5, "name": "clock"}
        assert sanitize_plugin_config(config) == config

    @pytest.mark.parametrize("key", ["has space", "has-dash", "has.dot", "has/slash", ""])
    def test_invalid_key_names_dropped(self, key):
        assert sanitize_plugin_config({key: "value", "good": 1}) == {"good": 1}

    def test_non_string_keys_dropped(self):
        assert sanitize_plugin_config({1: "a", "good": 2}) == {"good": 2}

    def test_nested_dicts_recursed(self):
        result = sanitize_plugin_config({"outer": {"inner": 1, "bad key": 2}})
        assert result == {"outer": {"inner": 1}}

    def test_list_of_scalars_preserved(self):
        assert sanitize_plugin_config({"teams": ["PHI", "NYG"]})["teams"] == ["PHI", "NYG"]

    def test_list_of_dicts_recursed(self):
        result = sanitize_plugin_config({"items": [{"ok": 1, "bad key": 2}]})
        assert result["items"] == [{"ok": 1}]

    def test_unknown_value_types_dropped(self):
        assert sanitize_plugin_config({"weird": {1, 2, 3}, "good": 1}) == {"good": 1}

    def test_none_values_dropped(self):
        assert sanitize_plugin_config({"nothing": None, "good": 1}) == {"good": 1}

    def test_strings_are_not_html_escaped(self):
        # Pinned, not a bug: escaping here would persist the escaped form in
        # config.json. Output escaping belongs to the template layer, which
        # the function's docstring now says explicitly.
        payload = "<script>alert(1)</script>"
        assert sanitize_plugin_config({"title": payload})["title"] == payload

    def test_empty_config(self):
        assert sanitize_plugin_config({}) == {}
