"""A failed logo download is not retried on every call.

A logo that is simply absent (not a stale placeholder) had nothing to back
off on: every load_logo_with_download() call retried the download, each with
a 30s timeout, for as long as the host was down or the URL was dead.
"""

import io
import logging
import time
from unittest.mock import MagicMock

import pytest
import requests
from PIL import Image

from src.common.logo_helper import MISSING_LOGO_RECHECK_SECONDS, LogoHelper


@pytest.fixture(autouse=True)
def _no_real_chmod(monkeypatch):
    monkeypatch.setattr("src.common.logo_helper.ensure_directory_permissions", MagicMock())
    monkeypatch.setattr("src.logo_downloader.ensure_file_permissions", MagicMock())


@pytest.fixture
def helper():
    h = LogoHelper(display_width=64, display_height=32,
                   logger=logging.getLogger("test.logo_backoff"))
    h.session.get = MagicMock(side_effect=requests.ConnectionError("down"))
    return h


def _png_response():
    buf = io.BytesIO()
    Image.new("RGBA", (20, 20), (0, 128, 0, 255)).save(buf, "PNG")
    data = buf.getvalue()
    response = MagicMock()
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    response.raise_for_status = MagicMock()
    response.headers = {"content-type": "image/png"}
    response.iter_content = lambda *a, **k: iter([data])
    return response


def _load(helper, path):
    return helper.load_logo_with_download("PHI", path, "http://x/logo.png",
                                          max_width=20, max_height=20)


def test_a_second_call_does_not_retry(helper, tmp_path):
    path = tmp_path / "PHI.png"
    assert _load(helper, path) is not None  # placeholder
    assert _load(helper, path) is not None
    assert helper.session.get.call_count == 1


def test_it_retries_after_the_recheck_window(helper, tmp_path):
    path = tmp_path / "PHI.png"
    _load(helper, path)
    helper._download_failures[str(path)] -= MISSING_LOGO_RECHECK_SECONDS + 1
    _load(helper, path)
    assert helper.session.get.call_count == 2


def test_other_paths_are_not_held_back(helper, tmp_path):
    _load(helper, tmp_path / "PHI.png")
    _load(helper, tmp_path / "DAL.png")
    assert helper.session.get.call_count == 2


def test_clear_cache_forgets_failures(helper, tmp_path):
    path = tmp_path / "PHI.png"
    _load(helper, path)
    helper.clear_cache()
    _load(helper, path)
    assert helper.session.get.call_count == 2


def test_invalidate_forgets_the_failure(helper, tmp_path):
    path = tmp_path / "PHI.png"
    _load(helper, path)
    helper._invalidate_cached_logo("PHI", path)
    helper.session.get = MagicMock(return_value=_png_response())
    logo = _load(helper, path)
    helper.session.get.assert_called_once()
    assert path.exists()
    assert logo.getpixel((0, 0))[:3] == (0, 128, 0)
