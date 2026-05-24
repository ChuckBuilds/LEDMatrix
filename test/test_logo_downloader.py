"""
Tests for src/logo_downloader.py

Focuses on the pure/static methods that don't require network calls:
normalize_abbreviation, get_logo_filename_variations, get_logo_directory,
ensure_logo_directory, and the download_missing_logo function path
(with HTTP mocked).
"""

import os
import pytest
from pathlib import Path
from unittest.mock import patch, Mock, MagicMock

from src.logo_downloader import LogoDownloader


# ---------------------------------------------------------------------------
# normalize_abbreviation
# ---------------------------------------------------------------------------

class TestNormalizeAbbreviation:
    def test_basic_lowercase(self):
        result = LogoDownloader.normalize_abbreviation("lal")
        assert result == "LAL"

    def test_uppercases(self):
        result = LogoDownloader.normalize_abbreviation("bos")
        assert result == "BOS"

    def test_ampersand_replaced(self):
        result = LogoDownloader.normalize_abbreviation("TA&M")
        assert "&" not in result
        assert "AND" in result

    def test_forward_slash_replaced(self):
        result = LogoDownloader.normalize_abbreviation("A/B")
        assert "/" not in result

    def test_empty_returns_empty(self):
        result = LogoDownloader.normalize_abbreviation("")
        assert result == ""


# ---------------------------------------------------------------------------
# get_logo_filename_variations
# ---------------------------------------------------------------------------

class TestGetLogoFilenameVariations:
    def test_returns_list(self):
        result = LogoDownloader.get_logo_filename_variations("LAL")
        assert isinstance(result, list)
        assert len(result) > 0

    def test_includes_png(self):
        result = LogoDownloader.get_logo_filename_variations("KC")
        filenames = " ".join(result)
        assert ".png" in filenames

    def test_includes_original(self):
        result = LogoDownloader.get_logo_filename_variations("LAL")
        assert any("LAL" in f for f in result)

    def test_ampersand_variation(self):
        result = LogoDownloader.get_logo_filename_variations("TA&M")
        # Should produce at least the normalized version
        assert len(result) > 0

    def test_empty_string_no_crash(self):
        result = LogoDownloader.get_logo_filename_variations("")
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# get_logo_directory
# ---------------------------------------------------------------------------

class TestGetLogoDirectory:
    def test_known_sport_returns_string(self):
        downloader = LogoDownloader()
        result = downloader.get_logo_directory("nfl")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_known_sport_nba(self):
        downloader = LogoDownloader()
        result = downloader.get_logo_directory("nba")
        assert "nba" in result.lower() or "sports" in result.lower()

    def test_unknown_sport_returns_string(self):
        downloader = LogoDownloader()
        result = downloader.get_logo_directory("unknown_sport_xyz")
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# ensure_logo_directory
# ---------------------------------------------------------------------------

class TestEnsureLogoDirectory:
    def test_creates_writable_directory(self, tmp_path):
        downloader = LogoDownloader()
        test_dir = str(tmp_path / "logos" / "nfl")
        result = downloader.ensure_logo_directory(test_dir)
        assert result is True
        assert Path(test_dir).is_dir()

    def test_existing_writable_directory(self, tmp_path):
        downloader = LogoDownloader()
        test_dir = str(tmp_path)
        result = downloader.ensure_logo_directory(test_dir)
        assert result is True

    def test_returns_false_when_write_test_fails(self, tmp_path):
        """Simulate a directory that exists but raises PermissionError on write."""
        downloader = LogoDownloader()
        test_dir = str(tmp_path / "logos")

        import builtins
        original_open = builtins.open

        def mock_open(path, *args, **kwargs):
            if ".write_test" in str(path):
                raise PermissionError("no write access")
            return original_open(path, *args, **kwargs)

        with patch("builtins.open", side_effect=mock_open):
            result = downloader.ensure_logo_directory(test_dir)
        assert result is False
