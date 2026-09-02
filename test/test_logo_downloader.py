"""
Tests for src/logo_downloader.py

Focuses on the pure/static methods that don't require network calls:
normalize_abbreviation, get_logo_filename_variations, get_logo_directory,
ensure_logo_directory, and the download_missing_logo function path
(with HTTP mocked).
"""

import os
import time

import pytest
from pathlib import Path
from unittest.mock import patch, Mock, MagicMock

from PIL import Image
from PIL.PngImagePlugin import PngInfo

from src.logo_downloader import (
    PLACEHOLDER_BG,
    PLACEHOLDER_MARKER,
    PLACEHOLDER_RETRY_SECONDS,
    PLACEHOLDER_SIZE,
    LogoDownloader,
    download_missing_logo,
    is_placeholder_logo,
    placeholder_age_seconds,
)


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


# ---------------------------------------------------------------------------
# Placeholder detection and retry
#
# A failed download used to be cached as a placeholder wearing the real logo's
# filename, and download_missing_logo returned early on "the file exists". One
# transient failure therefore pinned a team to a grey box permanently.
# ---------------------------------------------------------------------------

class TestPlaceholderLogos:
    def _placeholder(self, tmp_path, abbrev="COLL"):
        downloader = LogoDownloader()
        assert downloader.create_placeholder_logo(abbrev, str(tmp_path)) is True
        return tmp_path / f"{abbrev}.png"

    def test_generated_placeholder_is_recognised(self, tmp_path):
        assert is_placeholder_logo(self._placeholder(tmp_path)) is True

    def test_real_logo_is_not_a_placeholder(self, tmp_path):
        real = tmp_path / "REAL.png"
        Image.new("RGBA", (500, 500), (12, 34, 56, 255)).save(real)
        assert is_placeholder_logo(real) is False

    def test_legacy_unmarked_placeholder_is_recognised(self, tmp_path):
        """Placeholders written before the marker existed must still be caught.

        They are already sitting on users' disks; if they were not recognised
        those teams would stay grey boxes forever even after this fix.
        """
        legacy = tmp_path / "LEGACY.png"
        Image.new("RGBA", PLACEHOLDER_SIZE, PLACEHOLDER_BG).save(legacy)
        assert is_placeholder_logo(legacy) is True

    def test_same_size_but_different_colour_is_not_a_placeholder(self, tmp_path):
        real = tmp_path / "SMALL.png"
        Image.new("RGBA", PLACEHOLDER_SIZE, (10, 200, 10, 255)).save(real)
        assert is_placeholder_logo(real) is False

    def test_missing_file_is_not_a_placeholder(self, tmp_path):
        assert is_placeholder_logo(tmp_path / "nope.png") is False

    def test_existing_real_logo_short_circuits_without_downloading(self, tmp_path):
        real = tmp_path / "REAL.png"
        Image.new("RGBA", (500, 500), (1, 2, 3, 255)).save(real)
        with patch.object(LogoDownloader, "download_logo") as download:
            assert download_missing_logo(
                "afl", "1", "REAL", real, logo_url="http://example/x.png") is True
        download.assert_not_called()

    def _age_placeholder(self, path, seconds):
        """Rewrite a placeholder's marker so it reads as `seconds` old."""
        metadata = PngInfo()
        metadata.add_text(PLACEHOLDER_MARKER, str(time.time() - seconds))
        with Image.open(path) as img:
            img.copy().save(path, "PNG", pnginfo=metadata)

    def test_stale_placeholder_triggers_a_retry(self, tmp_path):
        path = self._placeholder(tmp_path)
        self._age_placeholder(path, PLACEHOLDER_RETRY_SECONDS + 60)
        assert placeholder_age_seconds(path) > PLACEHOLDER_RETRY_SECONDS

        with patch.object(LogoDownloader, "download_logo", return_value=True) as download:
            assert download_missing_logo(
                "afl", "1", "COLL", path,
                logo_url="http://example/coll.png") is True
        download.assert_called_once()

    def test_placeholder_age_survives_an_mtime_touch(self, tmp_path):
        """The age comes from the stamp, not the filesystem.

        Anything that rewrites file times -- a backup restore, an rsync, a
        permissions fix script -- would otherwise reset the retry clock.
        """
        path = self._placeholder(tmp_path)
        self._age_placeholder(path, PLACEHOLDER_RETRY_SECONDS + 60)
        now = time.time()
        os.utime(path, (now, now))
        assert placeholder_age_seconds(path) > PLACEHOLDER_RETRY_SECONDS

    def test_fresh_placeholder_does_not_retry(self, tmp_path):
        """Rate limiting: a placeholder written seconds ago must not re-download.

        Without this the fix would trade a permanent grey box for an ESPN
        request on every frame.
        """
        path = self._placeholder(tmp_path)
        with patch.object(LogoDownloader, "download_logo") as download:
            assert download_missing_logo(
                "afl", "1", "COLL", path,
                logo_url="http://example/coll.png") is True
        download.assert_not_called()
