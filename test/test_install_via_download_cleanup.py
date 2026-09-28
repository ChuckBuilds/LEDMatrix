"""_install_via_download removes its extraction directory on failure too.

The temp extract dir was only removed on the success path (and on a zip-slip
abort), so an extract or move that raised left a full copy of the plugin in
the system temp dir on every failed attempt. Cleanup now lives in ``finally``.
"""

import io
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.plugin_system.store_manager import PluginStoreManager


def _zip_bytes():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        zf.writestr('demo-main/manifest.json', '{"id": "demo"}')
        zf.writestr('demo-main/manager.py', '')
    return buf.getvalue()


def _response(payload):
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.iter_content.return_value = [payload]
    return response


def _run(tmp_path, move_side_effect=None):
    plugins_dir = tmp_path / "plugin-repos"
    plugins_dir.mkdir()
    sm = PluginStoreManager(plugins_dir=str(plugins_dir))
    created = []
    real_mkdtemp = tempfile.mkdtemp

    def tracking_mkdtemp(*args, **kwargs):
        path = real_mkdtemp(*args, **kwargs)
        created.append(Path(path))
        return path

    with patch.object(sm, '_http_get_with_retries', return_value=_response(_zip_bytes())), \
            patch('src.plugin_system.store_install.tempfile.mkdtemp', side_effect=tracking_mkdtemp), \
            patch('src.plugin_system.store_install.shutil.move', side_effect=move_side_effect):
        ok = sm._install_via_download('https://example.invalid/demo.zip', plugins_dir / 'demo')
    return ok, created


def test_extract_dir_removed_when_the_move_fails(tmp_path):
    ok, created = _run(tmp_path, move_side_effect=OSError("disk full"))

    assert ok is False
    assert len(created) == 1
    assert not created[0].exists()


def test_extract_dir_removed_on_success(tmp_path):
    # shutil.move patched to a no-op: the extracted tree stays in temp and
    # must still be cleaned up.
    ok, created = _run(tmp_path, move_side_effect=lambda *a, **k: None)

    assert ok is True
    assert len(created) == 1
    assert not created[0].exists()
