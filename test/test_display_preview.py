"""GET /api/v3/display/current passes the snapshot PNG through untouched.

It used to PIL-decode the snapshot and re-encode it, which cost CPU on the Pi
for no change in the picture, and it swallowed any read failure with
``except Exception: pass``. It now sends the file's own bytes, the payload the
/stream/display SSE stream sends, and logs a failed read.
"""

import base64
import io
import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from flask import Flask
from PIL import Image, PngImagePlugin

sys.path.insert(0, str(Path(__file__).parent.parent))

from web_interface import display_preview  # noqa: E402


@pytest.fixture
def client(monkeypatch):
    from web_interface.blueprints.api_v3 import api_v3
    monkeypatch.setattr(api_v3, 'config_manager', MagicMock(), raising=False)
    api_v3.config_manager.load_config.return_value = {}
    app = Flask(__name__)
    app.config['TESTING'] = True
    app.register_blueprint(api_v3, url_prefix='/api/v3')
    with app.test_client() as test_client:
        yield test_client


@pytest.fixture
def snapshot(tmp_path, monkeypatch):
    """A snapshot PNG carrying a text chunk, which a PIL re-encode drops."""
    info = PngImagePlugin.PngInfo()
    info.add_text('written-by', 'display_manager')
    buffer = io.BytesIO()
    Image.new('RGB', (4, 2), (255, 0, 0)).save(buffer, format='PNG', pnginfo=info)
    path = tmp_path / 'led_matrix_preview.png'
    path.write_bytes(buffer.getvalue())
    monkeypatch.setattr(display_preview, 'SNAPSHOT_PATH', str(path))
    return path


def _image(client):
    response = client.get('/api/v3/display/current')
    assert response.status_code == 200
    return response.get_json()['data']


def test_the_snapshot_bytes_are_sent_as_they_are(client, snapshot):
    data = _image(client)
    assert base64.b64decode(data['image']) == snapshot.read_bytes()


def test_route_and_stream_send_the_same_payload_keys(client, snapshot):
    data = _image(client)
    assert set(data) == set(display_preview.preview_payload(1, 1, None))


def test_no_snapshot_is_a_null_image_without_a_warning(client, tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(display_preview, 'SNAPSHOT_PATH', str(tmp_path / 'missing.png'))
    with caplog.at_level(logging.WARNING):
        assert _image(client)['image'] is None
    assert not [r for r in caplog.records if 'snapshot' in r.getMessage()]


def test_an_unreadable_snapshot_is_logged(client, snapshot, monkeypatch, caplog):
    def denied(_path=None):
        raise PermissionError('denied')
    monkeypatch.setattr(display_preview, 'read_snapshot_base64', denied)
    with caplog.at_level(logging.WARNING):
        assert _image(client)['image'] is None
    assert [r for r in caplog.records if 'snapshot' in r.getMessage() and r.exc_info]
