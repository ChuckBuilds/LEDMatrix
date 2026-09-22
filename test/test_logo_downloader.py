"""
Tests for src/logo_downloader.py

Focuses on the pure/static methods that don't require network calls:
normalize_abbreviation, get_logo_filename_variations, get_logo_directory,
ensure_logo_directory, and the download_missing_logo function path
(with HTTP mocked).
"""

import io
import os
import threading
import time

import pytest
import requests
from pathlib import Path
from unittest.mock import patch, Mock, MagicMock

from PIL import Image
from PIL.PngImagePlugin import PngInfo

import src.logo_downloader as logo_downloader_module
from src.logo_downloader import (
    PLACEHOLDER_BG,
    PLACEHOLDER_MARKER,
    PLACEHOLDER_RETRY_SECONDS,
    PLACEHOLDER_SIZE,
    LogoDownloader,
    download_missing_logo,
    is_placeholder_logo,
    placeholder_age_seconds,
    refresh_placeholder_timestamp,
    should_attempt_download,
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


class TestDownloadEligibility:
    """One rule, shared by every download site.

    The two bulk loops and the single-logo path each had their own idea of what
    counted as "already have it", which is how one of them ended up retrying
    fresh placeholders and the other skipping stale ones forever.
    """

    def _placeholder(self, tmp_path, abbrev="COLL"):
        assert LogoDownloader().create_placeholder_logo(abbrev, str(tmp_path))
        return tmp_path / f"{abbrev}.png"

    def _age(self, path, seconds):
        metadata = PngInfo()
        metadata.add_text(PLACEHOLDER_MARKER, str(time.time() - seconds))
        with Image.open(path) as img:
            img.copy().save(path, "PNG", pnginfo=metadata)

    def test_missing_file_is_eligible(self, tmp_path):
        assert should_attempt_download(tmp_path / "nope.png") is True

    def test_real_logo_is_not_eligible(self, tmp_path):
        real = tmp_path / "REAL.png"
        Image.new("RGBA", (500, 500), (1, 2, 3, 255)).save(real)
        assert should_attempt_download(real) is False

    def test_force_download_beats_a_real_logo(self, tmp_path):
        real = tmp_path / "REAL.png"
        Image.new("RGBA", (500, 500), (1, 2, 3, 255)).save(real)
        assert should_attempt_download(real, force_download=True) is True

    def test_fresh_placeholder_is_not_eligible(self, tmp_path):
        assert should_attempt_download(self._placeholder(tmp_path)) is False

    def test_stale_placeholder_is_eligible(self, tmp_path):
        path = self._placeholder(tmp_path)
        self._age(path, PLACEHOLDER_RETRY_SECONDS + 60)
        assert should_attempt_download(path) is True

    def test_league_bulk_loop_skips_a_fresh_placeholder(self, tmp_path):
        """A bulk pass honours the same back-off as everything else."""
        self._placeholder(tmp_path, "AAA")
        downloader = LogoDownloader()
        teams = [{"abbreviation": "AAA", "display_name": "A", "logo_url": "http://x/a.png"}]
        with patch.object(LogoDownloader, "get_logo_directory", return_value=str(tmp_path)):
            with patch.object(LogoDownloader, "fetch_teams_data", return_value={"sports": [{}]}):
                with patch.object(LogoDownloader, "extract_teams_from_data", return_value=teams):
                    with patch.object(LogoDownloader, "download_logo") as download:
                        downloader.download_missing_logos_for_league("nfl")
        download.assert_not_called()

    def test_league_bulk_loop_retries_a_stale_placeholder(self, tmp_path):
        path = self._placeholder(tmp_path, "AAA")
        self._age(path, PLACEHOLDER_RETRY_SECONDS + 60)
        downloader = LogoDownloader()
        teams = [{"abbreviation": "AAA", "display_name": "A", "logo_url": "http://x/a.png"}]
        with patch.object(LogoDownloader, "get_logo_directory", return_value=str(tmp_path)):
            with patch.object(LogoDownloader, "fetch_teams_data", return_value={"sports": [{}]}):
                with patch.object(LogoDownloader, "extract_teams_from_data", return_value=teams):
                    with patch.object(LogoDownloader, "download_logo", return_value=True) as download:
                        downloader.download_missing_logos_for_league("nfl")
        download.assert_called_once()

    def test_ncaa_bulk_loop_retries_a_stale_placeholder(self, tmp_path):
        """This loop skipped placeholders forever; it now shares the rule."""
        path = self._placeholder(tmp_path, "AAA")
        self._age(path, PLACEHOLDER_RETRY_SECONDS + 60)
        downloader = LogoDownloader()
        teams = [{"abbreviation": "AAA", "display_name": "A",
                  "logo_url": "http://x/a.png", "category": "FBS",
                  "conference": "SEC"}]
        with patch.object(LogoDownloader, "get_logo_directory", return_value=str(tmp_path)):
            with patch.object(LogoDownloader, "fetch_teams_data", return_value={"sports": [{}]}):
                with patch.object(LogoDownloader, "extract_teams_from_data", return_value=teams):
                    with patch.object(LogoDownloader, "download_logo", return_value=True) as download:
                        downloader.download_all_ncaa_football_logos()
        download.assert_called_once()

    def test_ncaa_bulk_loop_skips_a_fresh_placeholder(self, tmp_path):
        self._placeholder(tmp_path, "AAA")
        downloader = LogoDownloader()
        teams = [{"abbreviation": "AAA", "display_name": "A",
                  "logo_url": "http://x/a.png", "category": "FBS",
                  "conference": "SEC"}]
        with patch.object(LogoDownloader, "get_logo_directory", return_value=str(tmp_path)):
            with patch.object(LogoDownloader, "fetch_teams_data", return_value={"sports": [{}]}):
                with patch.object(LogoDownloader, "extract_teams_from_data", return_value=teams):
                    with patch.object(LogoDownloader, "download_logo") as download:
                        downloader.download_all_ncaa_football_logos()
        download.assert_not_called()


class TestRefreshPlaceholderTimestamp:
    def test_restarts_the_back_off(self, tmp_path):
        assert LogoDownloader().create_placeholder_logo("COLL", str(tmp_path))
        path = tmp_path / "COLL.png"
        metadata = PngInfo()
        metadata.add_text(PLACEHOLDER_MARKER, str(time.time() - (PLACEHOLDER_RETRY_SECONDS + 60)))
        with Image.open(path) as img:
            img.copy().save(path, "PNG", pnginfo=metadata)
        assert should_attempt_download(path) is True

        assert refresh_placeholder_timestamp(path) is True
        assert should_attempt_download(path) is False

    def test_refuses_to_touch_a_real_logo(self, tmp_path):
        real = tmp_path / "REAL.png"
        Image.new("RGBA", (500, 500), (1, 2, 3, 255)).save(real)
        before = real.read_bytes()
        assert refresh_placeholder_timestamp(real) is False
        assert real.read_bytes() == before

    def test_missing_file_is_not_an_error(self, tmp_path):
        assert refresh_placeholder_timestamp(tmp_path / "nope.png") is False


# ---------------------------------------------------------------------------
# download_logo: the download the scoreboard plugins actually use
#
# It used to read response.content with no size cap, write straight to the
# final path (so a failed or corrupt download could be left there and then be
# cached as the logo), and build a fresh Session for every logo. It now goes
# through fetch_logo, the hardened download LogoHelper also uses.
# ---------------------------------------------------------------------------

def _png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _stream(body: bytes, content_type: str = "image/png", chunk: int = 1024,
            die_after: int | None = None):
    """A streamed requests.Response stand-in.

    ``die_after`` makes the transfer fail after that many bytes, the way a
    reset connection does mid-body.
    """
    response = MagicMock()
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    response.raise_for_status = MagicMock()
    response.headers = {"content-type": content_type}

    def _iter_content(*_args, **_kwargs):
        sent = 0
        for i in range(0, len(body), chunk):
            if die_after is not None and sent >= die_after:
                raise requests.exceptions.ChunkedEncodingError("connection reset")
            piece = body[i:i + chunk]
            sent += len(piece)
            yield piece

    response.iter_content = _iter_content
    return response


@pytest.fixture
def fresh_thread_state(monkeypatch):
    """Each test gets its own per-thread downloader cache."""
    monkeypatch.setattr(logo_downloader_module, "_thread_state", threading.local())


@pytest.fixture
def downloader():
    return LogoDownloader()


@pytest.fixture
def old_logo(tmp_path):
    """A logo already on disk that a failed re-download must not damage."""
    path = tmp_path / "PHI.png"
    Image.new("RGBA", (30, 30), (1, 2, 3, 255)).save(path)
    return path, path.read_bytes()


def _leftovers(directory: Path, keep: str | None = None):
    return sorted(p.name for p in directory.iterdir() if p.name != keep)


class TestDownloadLogoHardening:
    def test_valid_logo_is_saved_as_rgba_png(self, downloader, tmp_path):
        target = tmp_path / "PHI.png"
        downloader.session.get = MagicMock(
            return_value=_stream(_png(Image.new("RGB", (20, 10), (9, 8, 7)))))
        assert downloader.download_logo("http://x/phi.png", target, "PHI") is True
        with Image.open(target) as img:
            assert img.format == "PNG" and img.mode == "RGBA"
            assert img.getpixel((0, 0)) == (9, 8, 7, 255)
        assert _leftovers(tmp_path, keep="PHI.png") == []
        # Streamed, so the size cap applies before the body is buffered.
        assert downloader.session.get.call_args.kwargs["stream"] is True

    def test_oversized_response_is_rejected_and_leaves_no_file(
            self, downloader, tmp_path, monkeypatch):
        monkeypatch.setattr(logo_downloader_module, "MAX_LOGO_BYTES", 4096)
        target = tmp_path / "BIG.png"
        body = _png(Image.new("RGB", (8, 8))) + b"\x00" * 8192
        downloader.session.get = MagicMock(return_value=_stream(body))
        assert downloader.download_logo("http://x/big.png", target, "BIG") is False
        assert list(tmp_path.iterdir()) == []

    def test_oversized_response_does_not_replace_an_existing_logo(
            self, downloader, old_logo, monkeypatch):
        path, before = old_logo
        monkeypatch.setattr(logo_downloader_module, "MAX_LOGO_BYTES", 4096)
        downloader.session.get = MagicMock(
            return_value=_stream(b"\x89PNG" + b"\x00" * 8192))
        assert downloader.download_logo("http://x/big.png", path, "PHI") is False
        assert path.read_bytes() == before
        assert _leftovers(path.parent, keep=path.name) == []

    def test_mid_download_failure_leaves_no_partial_file(self, downloader, tmp_path):
        target = tmp_path / "CUT.png"
        body = _png(Image.new("RGB", (200, 200), (5, 5, 5))) + b"\x00" * 4096
        downloader.session.get = MagicMock(
            return_value=_stream(body, chunk=256, die_after=512))
        assert downloader.download_logo("http://x/cut.png", target, "CUT") is False
        assert list(tmp_path.iterdir()) == []

    def test_mid_download_failure_keeps_the_previous_logo(self, downloader, old_logo):
        # The old code opened the final path for writing before the body
        # arrived, so a dropped connection truncated the logo it was replacing.
        path, before = old_logo
        body = _png(Image.new("RGB", (200, 200), (5, 5, 5))) + b"\x00" * 4096
        downloader.session.get = MagicMock(
            return_value=_stream(body, chunk=256, die_after=512))
        assert downloader.download_logo("http://x/cut.png", path, "PHI") is False
        assert path.read_bytes() == before
        assert _leftovers(path.parent, keep=path.name) == []

    def test_non_image_content_type_is_rejected(self, downloader, old_logo):
        # Rejected on the label alone, before the body is trusted: these bytes
        # would decode, so only the content-type check stops them.
        path, before = old_logo
        body = _png(Image.new("RGB", (8, 8), (250, 0, 0)))
        downloader.session.get = MagicMock(
            return_value=_stream(body, content_type="text/html"))
        assert downloader.download_logo("http://x/404", path, "PHI") is False
        assert path.read_bytes() == before
        assert _leftovers(path.parent, keep=path.name) == []

    def test_bytes_that_do_not_decode_are_rejected(self, downloader, old_logo):
        # A server that labels an error page image/png must not get it cached.
        path, before = old_logo
        downloader.session.get = MagicMock(
            return_value=_stream(b"<html>oops</html>", content_type="image/png"))
        assert downloader.download_logo("http://x/lie.png", path, "PHI") is False
        assert path.read_bytes() == before
        assert _leftovers(path.parent, keep=path.name) == []

    def test_http_error_keeps_the_previous_logo(self, downloader, old_logo):
        path, before = old_logo
        response = _stream(b"")
        response.raise_for_status.side_effect = requests.exceptions.HTTPError("503")
        downloader.session.get = MagicMock(return_value=response)
        assert downloader.download_logo("http://x/phi.png", path, "PHI") is False
        assert path.read_bytes() == before
        assert _leftovers(path.parent, keep=path.name) == []

    def test_unwritable_directory_returns_false(self, downloader, tmp_path):
        downloader.session.get = MagicMock()
        with patch("src.logo_downloader.tempfile.mkstemp",
                   side_effect=PermissionError("read-only")):
            assert downloader.download_logo(
                "http://x/phi.png", tmp_path / "PHI.png", "PHI") is False
        downloader.session.get.assert_not_called()


class TestDownloadLogoTransparency:
    """Plugins paste logos with the image as its own mask; alpha must survive."""

    def _download(self, downloader, tmp_path, body, content_type="image/png"):
        target = tmp_path / "LOGO.png"
        downloader.session.get = MagicMock(return_value=_stream(body, content_type))
        assert downloader.download_logo("http://x/logo", target, "LOGO") is True
        with Image.open(target) as img:
            img.load()
            return img.copy(), img.format

    def test_rgba_alpha_is_kept_exactly(self, downloader, tmp_path):
        src = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
        for x in range(16):
            src.putpixel((x, 3), (200, 100, 50, x * 16))
        out, _ = self._download(downloader, tmp_path, _png(src))
        assert out.mode == "RGBA"
        assert list(out.getdata()) == list(src.getdata())

    def test_palette_transparency_becomes_alpha(self, downloader, tmp_path):
        src = Image.new("P", (8, 8), 0)
        src.putpalette([0, 0, 0, 255, 0, 0] + [0] * (254 * 3))
        src.putpixel((4, 4), 1)
        buf = io.BytesIO()
        src.save(buf, "PNG", transparency=0)
        out, _ = self._download(downloader, tmp_path, buf.getvalue())
        assert out.mode == "RGBA"
        assert out.getpixel((0, 0))[3] == 0
        assert out.getpixel((4, 4)) == (255, 0, 0, 255)

    def test_greyscale_transparency_becomes_alpha(self, downloader, tmp_path):
        src = Image.new("L", (8, 8), 0)
        src.putpixel((2, 2), 255)
        buf = io.BytesIO()
        src.save(buf, "PNG", transparency=0)
        out, _ = self._download(downloader, tmp_path, buf.getvalue())
        assert out.getpixel((0, 0))[3] == 0
        assert out.getpixel((2, 2)) == (255, 255, 255, 255)

    def test_jpeg_is_stored_as_opaque_rgba_png(self, downloader, tmp_path):
        buf = io.BytesIO()
        Image.new("RGB", (8, 8), (10, 200, 30)).save(buf, "JPEG", quality=95)
        out, fmt = self._download(downloader, tmp_path, buf.getvalue(), "image/jpeg")
        assert fmt == "PNG" and out.mode == "RGBA"
        assert out.getchannel("A").getextrema() == (255, 255)


class TestSharedDownloader:
    def test_download_missing_logo_reuses_one_session(self, tmp_path, fresh_thread_state):
        sessions = []
        real_session = requests.Session

        def counting_session(*args, **kwargs):
            s = real_session(*args, **kwargs)
            sessions.append(s)
            return s

        with patch("src.logo_downloader.requests.Session", side_effect=counting_session):
            with patch.object(LogoDownloader, "download_logo", return_value=True) as dl:
                for abbr in ("AAA", "BBB", "CCC"):
                    assert download_missing_logo(
                        "nfl", "1", abbr, tmp_path / f"{abbr}.png",
                        logo_url=f"http://x/{abbr}.png",
                        create_placeholder=False) is True
        assert dl.call_count == 3
        assert len(sessions) == 1

    def test_each_thread_gets_its_own_downloader(self, fresh_thread_state):
        here = logo_downloader_module.shared_downloader()
        assert logo_downloader_module.shared_downloader() is here
        seen = []
        t = threading.Thread(target=lambda: seen.append(
            logo_downloader_module.shared_downloader()))
        t.start()
        t.join()
        assert seen and seen[0] is not here
        assert seen[0].session is not here.session


class TestPlaceholderWrite:
    def test_no_write_probe_file_is_created(self, tmp_path):
        created = []
        real_touch = Path.touch

        def spy_touch(self, *args, **kwargs):
            created.append(self.name)
            return real_touch(self, *args, **kwargs)

        with patch.object(Path, "touch", spy_touch):
            assert LogoDownloader().create_placeholder_logo("COLL", str(tmp_path)) is True
        assert "test_write.tmp" not in created
        assert sorted(p.name for p in tmp_path.iterdir()) == ["COLL.png"]

    def test_unwritable_directory_returns_false(self, tmp_path):
        with patch.object(LogoDownloader, "ensure_logo_directory", return_value=True), \
             patch("src.logo_downloader.tempfile.mkstemp",
                   side_effect=PermissionError("read-only")):
            assert LogoDownloader().create_placeholder_logo("COLL", str(tmp_path)) is False
        assert list(tmp_path.iterdir()) == []

    def test_failed_save_keeps_the_previous_file(self, tmp_path):
        path = tmp_path / "COLL.png"
        Image.new("RGBA", (30, 30), (1, 2, 3, 255)).save(path)
        before = path.read_bytes()
        with patch("src.logo_downloader.os.replace", side_effect=OSError("disk full")):
            assert LogoDownloader().create_placeholder_logo("COLL", str(tmp_path)) is False
        assert path.read_bytes() == before
        assert sorted(p.name for p in tmp_path.iterdir()) == ["COLL.png"]
