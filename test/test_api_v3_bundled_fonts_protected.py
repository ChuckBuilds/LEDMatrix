"""DELETE /fonts/<name> must refuse every font the repository ships.

The api's SYSTEM_FONTS was a hand-written list that had drifted from
backup_manager.BUNDLED_FONTS: MatrixChunky8X, MatrixLight6X, MatrixLight8X and
ic8x8u were missing, so deleting them removed git-tracked files (and the next
`git pull` either restored them or conflicted).
"""

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.backup_manager import BUNDLED_FONTS  # noqa: E402
from test._api_v3_test_helpers import api_v3_client, api_v3_module  # noqa: F401,E402
from web_interface.blueprints.api_v3 import SYSTEM_FONTS  # noqa: E402

SHIPPED_FONT_FILES = sorted(
    name for name in BUNDLED_FONTS if name.lower().endswith(('.ttf', '.otf', '.bdf')))


def test_every_bundled_font_is_a_system_font():
    stems = {os.path.splitext(name)[0].lower() for name in SHIPPED_FONT_FILES}
    assert stems <= SYSTEM_FONTS, stems - SYSTEM_FONTS


@pytest.fixture
def fonts_root(tmp_path):
    fonts = tmp_path / "assets" / "fonts"
    fonts.mkdir(parents=True)
    with patch("web_interface.blueprints.api_v3.fonts.PROJECT_ROOT", tmp_path):
        yield fonts


@pytest.mark.parametrize("filename", ["MatrixChunky8X.bdf", "MatrixLight6X.bdf",
                                      "MatrixLight8X.bdf", "ic8x8u.bdf"])
def test_the_previously_unprotected_fonts_cannot_be_deleted(api_v3_client, fonts_root, filename):
    (fonts_root / filename).write_text("BUNDLED")

    response = api_v3_client.delete(f"/api/v3/fonts/{Path(filename).stem}")

    assert response.status_code == 403
    assert (fonts_root / filename).exists()


def test_a_user_font_can_still_be_deleted(api_v3_client, fonts_root):
    (fonts_root / "my-upload.ttf").write_bytes(b"USER")

    response = api_v3_client.delete("/api/v3/fonts/my-upload")

    assert response.status_code == 200, response.get_json()
    assert not (fonts_root / "my-upload.ttf").exists()
