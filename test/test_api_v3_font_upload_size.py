"""Regression test: POST /fonts/upload must enforce its own stated size limit.

validate_file_upload(filename, max_size_mb=10, allowed_extensions=[...]) reads
like it checks the upload's size, but it only ever validated the filename
(traversal characters, extension) -- max_size_mb was accepted and silently
ignored. Nothing else in the handler checked the actual upload size either,
so it saved whatever was posted to assets/fonts/<family><ext> regardless of
size, unlike the sibling .star and plugin-asset upload routes, which check
`file.tell()` against a stated limit before saving.
"""

import io
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from test._api_v3_test_helpers import api_v3_client, api_v3_module  # noqa: F401,E402

URL = "/api/v3/fonts/upload"
TEN_MB = 10 * 1024 * 1024


@pytest.fixture
def fonts_root(tmp_path):
    # PROJECT_ROOT is bound by value in fonts.py, so it is patched there.
    with patch("web_interface.blueprints.api_v3.fonts.PROJECT_ROOT", tmp_path):
        yield tmp_path


def upload(client, content, filename="myfont.ttf", family="myfont"):
    data = {
        "font_file": (io.BytesIO(content), filename),
        "font_family": family,
    }
    return client.post(URL, data=data, content_type="multipart/form-data")


class TestFontUploadSizeLimit:
    def test_oversized_font_is_rejected(self, api_v3_client, fonts_root):
        response = upload(api_v3_client, b"x" * (TEN_MB + 1))
        assert response.status_code == 400
        assert "too large" in response.get_json()["message"].lower()
        assert not (fonts_root / "assets" / "fonts" / "myfont.ttf").exists(), (
            "an oversized font was saved to disk before being rejected")

    def test_a_font_right_at_the_limit_is_accepted(self, api_v3_client, fonts_root):
        response = upload(api_v3_client, b"x" * TEN_MB,
                          filename="atlimit.ttf", family="atlimit")
        assert response.status_code == 200, response.get_json()
        assert (fonts_root / "assets" / "fonts" / "atlimit.ttf").exists()

    def test_an_ordinary_small_font_is_still_accepted(self, api_v3_client, fonts_root):
        response = upload(api_v3_client, b"fake font bytes",
                          filename="small.ttf", family="small")
        assert response.status_code == 200, response.get_json()
        assert (fonts_root / "assets" / "fonts" / "small.ttf").read_bytes() == b"fake font bytes"
