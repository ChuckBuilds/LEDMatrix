"""Small web-backend fixes: BDF font preview, the SSE broadcaster restart
window, start.py's werkzeug log filter, and the backup routes' errors."""

import base64
import io
import shutil
import sys
import threading
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from test._api_v3_test_helpers import api_v3_client, api_v3_module  # noqa: F401,E402

REPO = Path(__file__).resolve().parents[2]


class TestBdfFontPreview:
    """The route refused every BDF font ("needs complex rendering") although
    src/common/bdf_font.py draws them for the panel."""

    @pytest.fixture
    def fonts_root(self, tmp_path, api_v3_module, monkeypatch):
        import web_interface.blueprints.api_v3.fonts as fonts_module
        fonts_dir = tmp_path / "assets" / "fonts"
        fonts_dir.mkdir(parents=True)
        shutil.copy(REPO / "assets" / "fonts" / "6x10.bdf", fonts_dir / "6x10.bdf")
        monkeypatch.setattr(fonts_module, "PROJECT_ROOT", tmp_path)
        return fonts_dir

    def _preview(self, client, **params):
        query = {"font": "6x10.bdf", "text": "Hi", "size": 10,
                 "bg": "000000", "fg": "ffffff"}
        query.update(params)
        return client.get("/api/v3/fonts/preview", query_string=query)

    def test_a_bdf_font_renders(self, api_v3_client, fonts_root):
        response = self._preview(api_v3_client)
        assert response.status_code == 200, response.get_json()
        data = response.get_json()["data"]
        assert data["image"].startswith("data:image/png;base64,")
        image = Image.open(io.BytesIO(base64.b64decode(data["image"].split(",", 1)[1])))
        assert image.size == (data["width"], data["height"])
        colors = {c for _, c in image.convert("RGB").getcolors(10**6)}
        assert (255, 255, 255) in colors

    def test_the_glyphs_are_the_panels(self, api_v3_client, fonts_root):
        # The same rasterizer as the panel: exactly the pixels draw_bdf_text
        # lights for this string, nothing anti-aliased.
        from PIL import ImageDraw
        from src.common.bdf_font import draw_bdf_text, load_bdf_face
        data = self._preview(api_v3_client, text="A").get_json()["data"]
        preview = Image.open(io.BytesIO(base64.b64decode(data["image"].split(",", 1)[1]))).convert("RGB")

        face, _ = load_bdf_face(str(fonts_root / "6x10.bdf"), 10)
        glyph = Image.new("RGB", (20, 20))
        draw_bdf_text(ImageDraw.Draw(glyph), "A", 0, 0, face, color=(255, 255, 255))
        lit = sum(1 for p in glyph.getdata() if p == (255, 255, 255))
        assert lit > 0
        assert sum(1 for p in preview.getdata() if p == (255, 255, 255)) == lit
        assert set(preview.getdata()) <= {(0, 0, 0), (255, 255, 255)}

    def test_multi_line_text_is_taller(self, api_v3_client, fonts_root):
        one = self._preview(api_v3_client, text="Hi", size=10).get_json()["data"]
        # Tall enough to exceed the 30px minimum.
        three = self._preview(api_v3_client, text="a\nb\nc", size=10).get_json()["data"]
        assert three["height"] > one["height"]


class TestStreamBroadcasterRestart:
    """Between the broadcast thread's break and its exit, is_alive() was
    still True, so a client subscribing in that window got no thread."""

    def test_a_subscriber_during_shutdown_gets_a_new_thread(self):
        import web_interface.app as web_app

        closing = threading.Event()
        release = threading.Event()
        produced = threading.Event()
        started = []

        def factory():
            started.append(threading.current_thread())

            def gen():
                try:
                    while True:
                        produced.set()
                        yield {"n": 1}
                finally:
                    # Closing the generator after the break: hold the thread
                    # alive here, which is the window in question.
                    closing.set()
                    release.wait(5)
            return gen()

        broadcaster = web_app._StreamBroadcaster(factory)
        first = broadcaster.subscribe()
        assert produced.wait(5)
        broadcaster.unsubscribe(first)
        assert closing.wait(5), "the broadcast thread never noticed it had no clients"
        try:
            second = broadcaster.subscribe()
            assert second.get(timeout=5) == {"n": 1}
            assert len(started) == 2
        finally:
            release.set()
            broadcaster.unsubscribe(second)


class TestStartLogFilterExcInfo:
    """logging takes exc_info as a tuple, an exception, or True; the filter
    unpacked it as a 3-tuple, so the other two raised TypeError."""

    def test_every_form_yields_the_exception(self):
        from web_interface.start import _exc_info_value
        err = BrokenPipeError(32, "Broken pipe")
        assert _exc_info_value(err) is err
        assert _exc_info_value((type(err), err, None)) is err
        try:
            raise err
        except BrokenPipeError:
            assert _exc_info_value(True) is err

    def test_true_outside_an_except_is_none(self):
        from web_interface.start import _exc_info_value
        assert _exc_info_value(True) is None


class TestBackupErrors:
    """The backup routes' own catch-alls are gone; the blueprint handler
    answers with the fields backup_restore.html reads."""

    def test_a_failed_export_is_a_500_with_a_message(self, api_v3_client):
        with patch("src.backup_manager.create_backup", side_effect=OSError(28, "No space left")):
            response = api_v3_client.post("/api/v3/backup/export")
        assert response.status_code == 500
        body = response.get_json()
        assert body["status"] == "error"
        assert body["message"]
