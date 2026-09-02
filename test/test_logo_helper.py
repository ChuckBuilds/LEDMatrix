"""
Tests for src/common/logo_helper.py — logo loading, LRU caching, resizing,
and download-with-fallback. Previously untested: nothing in test/ referenced
this module at all.

Real PIL images under tmp_path are used rather than mocked ones, since
load_logo() does real Path.exists() and Image.open() calls; only the HTTP
session and the permission helpers are patched.

Regression coverage for two fixed bugs:
- _download_logo wrote response.content to disk with no size cap and no
  check that the bytes decoded as an image, so a hostile or broken URL
  could leave arbitrary/oversized content cached in the assets directory.
- get_cache_stats() divided by self.cache_size unguarded, raising
  ZeroDivisionError for a helper constructed with cache_size=0.
"""

import logging
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests
from PIL import Image, UnidentifiedImageError

from src.common.logo_helper import MAX_LOGO_BYTES, LogoHelper


@pytest.fixture(autouse=True)
def _no_real_chmod(monkeypatch):
    # Keep the permission helpers out of the way: their own env detection
    # is not what these tests are about.
    monkeypatch.setattr("src.common.logo_helper.ensure_directory_permissions", MagicMock())
    monkeypatch.setattr("src.common.logo_helper.ensure_file_permissions", MagicMock())


@pytest.fixture
def helper():
    return LogoHelper(display_width=64, display_height=32,
                      logger=logging.getLogger("test.logo_helper"))


def write_logo(path: Path, size=(20, 20), color=(255, 0, 0), fmt="PNG") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path, format=fmt)
    return path


def fake_response(content: bytes, chunk_size: int = 64 * 1024):
    """Stand-in for a streamed requests.Response.

    _download_logo opens `with session.get(..., stream=True)` and reads
    through iter_content(), so the fake has to be a context manager that
    yields the body in pieces rather than exposing it as .content.
    Chunking is the fake's own, not the caller's, so a test can dribble a
    body out in small pieces.
    """
    response = MagicMock()
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    response.raise_for_status = MagicMock()

    def _iter_content(*_args, **_kwargs):
        for i in range(0, len(content), chunk_size):
            yield content[i:i + chunk_size]

    response.iter_content = _iter_content
    return response


def endless_response(chunk: bytes = b"\x00" * 65536):
    """A server that declares no length and never stops sending.

    This is the case response.content could not survive: it buffers to
    completion, so the size check never got a chance to run.
    """
    response = MagicMock()
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    response.raise_for_status = MagicMock()

    def _iter_content(*_args, **_kwargs):
        while True:
            yield chunk

    response.iter_content = _iter_content
    return response


def png_bytes(size=(20, 20), color=(0, 128, 0)) -> bytes:
    import io
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


class TestLoadLogo:
    def test_loads_and_converts_to_rgba(self, helper, tmp_path):
        path = write_logo(tmp_path / "PHI.png")
        logo = helper.load_logo("PHI", path)
        assert logo is not None
        assert logo.mode == "RGBA"

    def test_missing_file_returns_none(self, helper, tmp_path, caplog):
        with caplog.at_level(logging.WARNING):
            assert helper.load_logo("NOPE", tmp_path / "missing.png") is None
        assert "Logo not found" in caplog.text

    def test_second_load_is_served_from_cache(self, helper, tmp_path):
        path = write_logo(tmp_path / "PHI.png")
        first = helper.load_logo("PHI", path)
        path.unlink()  # cache hit must not touch the filesystem
        assert helper.load_logo("PHI", path) is first

    def test_cache_key_includes_requested_size(self, helper, tmp_path):
        # A panel-size change must not hand back a logo sized for the old
        # dimensions, so the two sizes get separate cache entries.
        path = write_logo(tmp_path / "PHI.png", size=(100, 100))
        small = helper.load_logo("PHI", path, max_width=10, max_height=10)
        large = helper.load_logo("PHI", path, max_width=50, max_height=50)
        assert small is not large
        assert small.size != large.size
        assert len(helper._logo_cache) == 2

    def test_default_size_is_one_and_a_half_display(self, helper, tmp_path):
        path = write_logo(tmp_path / "PHI.png", size=(500, 500))
        logo = helper.load_logo("PHI", path)
        assert logo.width <= int(64 * 1.5)
        assert logo.height <= int(32 * 1.5)

    def test_smaller_image_is_not_upscaled(self, helper, tmp_path):
        path = write_logo(tmp_path / "PHI.png", size=(8, 8))
        assert helper.load_logo("PHI", path, max_width=64, max_height=64).size == (8, 8)

    def test_larger_image_is_downscaled_preserving_aspect(self, helper, tmp_path):
        path = write_logo(tmp_path / "PHI.png", size=(200, 100))
        logo = helper.load_logo("PHI", path, max_width=50, max_height=50)
        assert logo.width <= 50 and logo.height <= 50
        assert logo.width == 50 and logo.height == 25  # 2:1 preserved

    def test_string_path_accepted(self, helper, tmp_path):
        path = write_logo(tmp_path / "PHI.png")
        assert helper.load_logo("PHI", str(path)) is not None

    def test_corrupt_file_returns_none(self, helper, tmp_path, caplog):
        bad = tmp_path / "bad.png"
        bad.write_bytes(b"not an image")
        with caplog.at_level(logging.ERROR):
            assert helper.load_logo("BAD", bad) is None
        assert "Error loading logo" in caplog.text


class TestCacheManagement:
    def test_lru_evicts_oldest(self, tmp_path):
        helper = LogoHelper(64, 32, cache_size=2, logger=MagicMock())
        paths = [write_logo(tmp_path / f"T{i}.png") for i in range(3)]
        for i, path in enumerate(paths):
            helper.load_logo(f"T{i}", path)
        assert len(helper._logo_cache) == 2
        assert not any(k.startswith("T0_") for k in helper._logo_cache)

    def test_cache_hit_refreshes_lru_position(self, tmp_path):
        helper = LogoHelper(64, 32, cache_size=2, logger=MagicMock())
        a, b, c = [write_logo(tmp_path / f"{n}.png") for n in ("A", "B", "C")]
        helper.load_logo("A", a)
        helper.load_logo("B", b)
        helper.load_logo("A", a)   # A is now most-recently used
        helper.load_logo("C", c)   # evicts B, not A
        assert any(k.startswith("A_") for k in helper._logo_cache)
        assert not any(k.startswith("B_") for k in helper._logo_cache)

    def test_clear_cache_empties_both_structures(self, helper, tmp_path):
        helper.load_logo("PHI", write_logo(tmp_path / "PHI.png"))
        helper.clear_cache()
        assert helper._logo_cache == {}
        assert helper._cache_order == []

    def test_cache_stats(self, tmp_path):
        helper = LogoHelper(64, 32, cache_size=4, logger=MagicMock())
        helper.load_logo("PHI", write_logo(tmp_path / "PHI.png"))
        stats = helper.get_cache_stats()
        assert stats["cached_logos"] == 1
        assert stats["cache_size_limit"] == 4
        assert stats["cache_usage_percent"] == 25

    def test_zero_cache_size_does_not_divide_by_zero(self):
        # Regression: this raised ZeroDivisionError.
        stats = LogoHelper(64, 32, cache_size=0, logger=MagicMock()).get_cache_stats()
        assert stats["cache_usage_percent"] == 0
        assert stats["cache_size_limit"] == 0


class TestLoadLogoWithDownload:
    def test_existing_file_skips_download(self, helper, tmp_path):
        path = write_logo(tmp_path / "PHI.png")
        helper.session.get = MagicMock()
        assert helper.load_logo_with_download("PHI", path, "http://x/logo.png") is not None
        helper.session.get.assert_not_called()

    def test_downloads_then_loads(self, helper, tmp_path):
        path = tmp_path / "PHI.png"
        helper.session.get = MagicMock(return_value=fake_response(png_bytes()))
        logo = helper.load_logo_with_download("PHI", path, "http://x/logo.png")
        assert logo is not None
        assert path.exists()
        # stream=True is load-bearing: it is what lets the size cap apply
        # before the body is buffered.
        helper.session.get.assert_called_once_with(
            "http://x/logo.png", timeout=30, stream=True)

    def test_download_failure_falls_back_to_placeholder(self, helper, tmp_path):
        helper.session.get = MagicMock(
            side_effect=requests.RequestException("connection reset"))
        logo = helper.load_logo_with_download(
            "PHI", tmp_path / "PHI.png", "http://x/logo.png",
            max_width=20, max_height=20)
        assert logo is not None and logo.size == (20, 20)  # placeholder

    def test_http_error_falls_back_to_placeholder(self, helper, tmp_path):
        response = fake_response(b"")
        response.raise_for_status.side_effect = requests.HTTPError("404")
        helper.session.get = MagicMock(return_value=response)
        logo = helper.load_logo_with_download(
            "PHI", tmp_path / "PHI.png", "http://x/logo.png",
            max_width=20, max_height=20)
        assert logo is not None and logo.size == (20, 20)

    def test_no_url_and_no_file_gives_placeholder(self, helper, tmp_path):
        logo = helper.load_logo_with_download(
            "PHI", tmp_path / "missing.png", None, max_width=16, max_height=16)
        assert logo is not None and logo.size == (16, 16)


class TestDownloadLogo:
    def test_writes_file_and_sets_permissions(self, helper, tmp_path):
        path = tmp_path / "assets" / "PHI.png"
        # Directory creation is ensure_directory_permissions' job, and the
        # autouse fixture stubs it out — so make the directory here.
        path.parent.mkdir()
        helper.session.get = MagicMock(return_value=fake_response(png_bytes()))
        with patch("src.common.logo_helper.ensure_directory_permissions") as dirs, \
             patch("src.common.logo_helper.ensure_file_permissions") as files:
            helper._download_logo("http://x/logo.png", path)
        assert path.exists()
        dirs.assert_called_once()
        files.assert_called_once()
        assert dirs.call_args[0][0] == path.parent

    def test_oversized_response_is_rejected_without_writing(self, helper, tmp_path):
        # Regression: an unbounded response.content was written straight to
        # disk, so a hostile URL chose how many bytes landed in assets/.
        path = tmp_path / "huge.png"
        helper.session.get = MagicMock(
            return_value=fake_response(b"\x00" * (MAX_LOGO_BYTES + 1)))
        with pytest.raises(ValueError, match="exceeds the"):
            helper._download_logo("http://x/huge.png", path)
        assert not path.exists()

    def test_unbounded_response_is_aborted_at_the_cap(self, helper, tmp_path):
        # Regression: the cap used to be checked against response.content,
        # which buffers the whole body first — so a server that omits
        # Content-Length and never stops sending exhausted memory before
        # the check could run. Streaming counts bytes as they arrive, so
        # this terminates instead of hanging.
        path = tmp_path / "endless.png"
        helper.session.get = MagicMock(return_value=endless_response())
        with pytest.raises(ValueError, match="exceeds the"):
            helper._download_logo("http://x/endless.png", path)
        assert not path.exists()

    def test_no_partial_file_is_left_when_the_stream_dies(self, helper, tmp_path):
        # A transfer that fails midway must not leave a truncated logo
        # where the real one belongs — load_logo() would cache it.
        path = tmp_path / "cut.png"
        real = png_bytes()

        def _dies_midway(*_args, **_kwargs):
            yield real[:20]
            raise OSError("connection reset")

        response = MagicMock()
        response.__enter__.return_value = response
        response.__exit__.return_value = False
        response.raise_for_status = MagicMock()
        response.iter_content = _dies_midway
        helper.session.get = MagicMock(return_value=response)

        with pytest.raises(OSError):
            helper._download_logo("http://x/cut.png", path)
        assert not path.exists()
        assert list(tmp_path.glob("*.part")) == []

    def test_concurrent_downloads_do_not_share_a_temp_file(self, helper, tmp_path):
        # Two plugins can ask for the same logo at once. A fixed
        # "<name>.part" would let them interleave writes into one file and
        # publish the mixture; each download gets its own temp name.
        path = tmp_path / "PHI.png"
        seen = []
        real_mkstemp = tempfile.mkstemp

        def record(*args, **kwargs):
            fd, name = real_mkstemp(*args, **kwargs)
            seen.append(name)
            return fd, name

        with patch("src.common.logo_helper.tempfile.mkstemp", side_effect=record):
            helper.session.get = MagicMock(return_value=fake_response(png_bytes()))
            helper._download_logo("http://x/logo.png", path)
            helper.session.get = MagicMock(return_value=fake_response(png_bytes()))
            helper._download_logo("http://x/logo.png", path)

        assert len(seen) == 2 and seen[0] != seen[1]
        assert path.exists()
        assert list(tmp_path.glob("*.part")) == []  # both cleaned up

    def test_request_failure_leaves_no_temp_file(self, helper, tmp_path):
        # mkstemp creates the file up front, so an error before any bytes
        # arrive still has something to clean up.
        helper.session.get = MagicMock(
            side_effect=requests.RequestException("connection reset"))
        with pytest.raises(requests.RequestException):
            helper._download_logo("http://x/logo.png", tmp_path / "PHI.png")
        assert list(tmp_path.glob("*")) == []

    def test_non_image_response_is_deleted_and_raises(self, helper, tmp_path):
        # Regression: undecodable bytes stayed on disk, so every later
        # load_logo() call hit the corrupt file instead of re-downloading.
        path = tmp_path / "bad.png"
        helper.session.get = MagicMock(return_value=fake_response(b"<html>404</html>"))
        # Specifically Pillow's identify failure, not any OSError: the
        # point is that the bytes did not decode, and OSError alone would
        # also admit unrelated filesystem faults.
        with pytest.raises(UnidentifiedImageError):
            helper._download_logo("http://x/bad.png", path)
        assert not path.exists()
        assert list(tmp_path.glob("*.part")) == []

    def test_decompression_bomb_is_deleted_and_raises(self, helper, tmp_path, monkeypatch):
        path = tmp_path / "bomb.png"
        helper.session.get = MagicMock(return_value=fake_response(png_bytes()))

        class Bomb:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def load(self):
                raise Image.DecompressionBombError("too many pixels")

        monkeypatch.setattr("src.common.logo_helper.Image.open", lambda *a, **kw: Bomb())
        with pytest.raises(Image.DecompressionBombError):
            helper._download_logo("http://x/bomb.png", path)
        assert not path.exists()

    def test_bad_download_surfaces_as_placeholder_not_crash(self, helper, tmp_path):
        # The new guards raise, and load_logo_with_download's existing
        # broad except turns that into the placeholder path.
        helper.session.get = MagicMock(return_value=fake_response(b"garbage"))
        logo = helper.load_logo_with_download(
            "PHI", tmp_path / "PHI.png", "http://x/bad.png",
            max_width=12, max_height=12)
        assert logo is not None and logo.size == (12, 12)


class TestLogoVariations:
    def test_plain_abbreviation_returns_itself(self, helper):
        assert helper.get_logo_variations("PHI") == ["PHI"]

    def test_ampersand_expanded(self, helper):
        assert "TAAND M" in helper.get_logo_variations("TA& M")

    def test_and_contracted(self, helper):
        assert "T&M" in helper.get_logo_variations("TANDM")

    def test_special_case_appends_known_aliases(self, helper):
        variations = helper.get_logo_variations("TA&M")
        assert "TAMU" in variations and "TEXASAM" in variations
        assert "TAANDM" in variations  # the generic & rule still applies


class TestNormalizeAbbreviation:
    def test_uppercases_and_strips(self, helper):
        assert helper.normalize_abbreviation("  phi  ") == "PHI"

    def test_ampersand_becomes_and(self, helper):
        assert helper.normalize_abbreviation("TA&M") == "TAANDM"

    def test_internal_spaces_removed(self, helper):
        assert helper.normalize_abbreviation("New York") == "NEWYORK"

    def test_deliberately_differs_from_logo_downloader(self, helper):
        # Pinned, not a bug: LogoDownloader.normalize_abbreviation replaces
        # filesystem-unsafe characters but keeps spaces, and plugins call
        # that one. Changing either changes which logo filenames resolve on
        # existing installs. Both docstrings say so explicitly.
        from src.logo_downloader import LogoDownloader
        assert helper.normalize_abbreviation("New York") == "NEWYORK"
        assert LogoDownloader.normalize_abbreviation("New York") == "NEW YORK"


class TestPlaceholderLogo:
    def test_uses_requested_dimensions(self, helper):
        assert helper._create_placeholder_logo("PHI", 30, 20).size == (30, 20)

    def test_defaults_to_one_and_a_half_display(self, helper):
        assert helper._create_placeholder_logo("PHI").size == (96, 48)

    def test_is_rgba(self, helper):
        assert helper._create_placeholder_logo("PHI", 10, 10).mode == "RGBA"

    def test_invalid_dimensions_return_none(self, helper, caplog):
        with caplog.at_level(logging.ERROR):
            assert helper._create_placeholder_logo("PHI", -5, -5) is None
        assert "Error creating placeholder" in caplog.text


class TestSessionConfiguration:
    def test_user_agent_and_accept_headers(self, helper):
        assert helper.session.headers["User-Agent"] == "LEDMatrix-Common/1.0"
        assert helper.session.headers["Accept"] == "image/*"


class TestStalePlaceholderHandling:
    """load_logo_with_download must not be fooled by a cached placeholder.

    A placeholder wears the real logo's filename, so both the file cache and
    the in-memory cache can hold one and look like a hit.
    """

    def _placeholder(self, tmp_path, abbrev="COLL"):
        from src.logo_downloader import LogoDownloader
        assert LogoDownloader().create_placeholder_logo(abbrev, str(tmp_path))
        return tmp_path / f"{abbrev}.png"

    def _make_stale(self, path):
        import time
        from PIL.PngImagePlugin import PngInfo
        from src.logo_downloader import PLACEHOLDER_MARKER, PLACEHOLDER_RETRY_SECONDS
        metadata = PngInfo()
        metadata.add_text(PLACEHOLDER_MARKER, str(time.time() - (PLACEHOLDER_RETRY_SECONDS + 60)))
        with Image.open(path) as img:
            img.copy().save(path, "PNG", pnginfo=metadata)

    def test_fresh_placeholder_is_served_without_a_download(self, helper, tmp_path):
        path = self._placeholder(tmp_path)
        with patch.object(LogoHelper, "_download_logo") as download:
            assert helper.load_logo_with_download("COLL", path, "http://x/c.png") is not None
        download.assert_not_called()

    def test_stale_placeholder_triggers_a_download(self, helper, tmp_path):
        path = self._placeholder(tmp_path)
        self._make_stale(path)
        with patch.object(LogoHelper, "_download_logo") as download:
            helper.load_logo_with_download("COLL", path, "http://x/c.png")
        download.assert_called_once()

    def test_replacement_logo_is_not_masked_by_the_cached_placeholder(self, helper, tmp_path):
        """The bug this guards: load_logo answers from cache before the disk.

        Without invalidation the freshly downloaded logo would not appear until
        the process restarted.
        """
        path = self._placeholder(tmp_path)
        first = helper.load_logo_with_download("COLL", path, "http://x/c.png")
        assert first is not None

        self._make_stale(path)

        def fake_download(_self, _url, file_path):
            Image.new("RGB", (500, 500), (7, 8, 9)).save(file_path, format="PNG")

        with patch.object(LogoHelper, "_download_logo", fake_download):
            second = helper.load_logo_with_download("COLL", path, "http://x/c.png")

        assert second is not None
        from src.logo_downloader import is_placeholder_logo
        assert is_placeholder_logo(path) is False
        assert second.getpixel((0, 0))[:3] == (7, 8, 9)

    def test_failed_retry_restarts_the_back_off(self, helper, tmp_path):
        """Otherwise a stale placeholder means a download attempt per call."""
        from src.logo_downloader import should_attempt_download
        path = self._placeholder(tmp_path)
        self._make_stale(path)
        assert should_attempt_download(path) is True

        def boom(_self, _url, _file_path):
            raise OSError("network down")

        with patch.object(LogoHelper, "_download_logo", boom):
            helper.load_logo_with_download("COLL", path, "http://x/c.png")

        assert should_attempt_download(path) is False
