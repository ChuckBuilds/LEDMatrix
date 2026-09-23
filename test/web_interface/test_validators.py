"""
Tests for validate_file_upload in src/web_interface/validators.py.

dedup_unique_arrays is covered by test_dedup_unique_arrays.py.

Regression coverage: validate_file_upload lowercased the filename's
extension but not the caller's allowed_extensions list, so ['.TTF']
rejected 'font.ttf'.
"""

import pytest

from src.web_interface.validators import validate_file_upload


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
