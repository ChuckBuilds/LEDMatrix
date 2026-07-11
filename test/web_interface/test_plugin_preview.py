"""Tests for POST /api/v3/plugins/preview — the config-page live preview.

The endpoint renders a plugin headlessly (pure PIL, no hardware, no pip)
with a CANDIDATE config: either the current form state (parsed by the same
parse_plugin_config_form used by save, so preview and save can never
disagree) or a JSON config body.
"""

import base64
import io
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from flask import Flask
from PIL import Image

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from web_interface.blueprints import api_v3 as api_v3_module  # noqa: E402
from web_interface.blueprints.api_v3 import api_v3  # noqa: E402

PLUGIN_ID = "preview-test-plugin"

MANAGER_PY = '''
from PIL import ImageFont
from src.plugin_system.base_plugin import BasePlugin


class PreviewTestPlugin(BasePlugin):
    def update(self):
        return True

    def display(self, force_clear=False):
        if force_clear:
            self.display_manager.clear()
        text = self.config.get("message", "hello")
        self.display_manager.draw.text((1, 1), text, fill=(255, 255, 255))
        self.display_manager.update_display()
'''

MANIFEST = {
    "id": PLUGIN_ID,
    "name": "Preview Test Plugin",
    "version": "1.0.0",
    "class_name": "PreviewTestPlugin",
    "entry_point": "manager.py",
    "display_modes": ["preview_test"],
}

SCHEMA = {
    "type": "object",
    "properties": {
        "enabled": {"type": "boolean", "default": True},
        "message": {"type": "string", "default": "hello"},
    },
}


@pytest.fixture
def plugin_dir(tmp_path):
    plugin = tmp_path / PLUGIN_ID
    plugin.mkdir()
    (plugin / "manager.py").write_text(MANAGER_PY)
    (plugin / "manifest.json").write_text(json.dumps(MANIFEST))
    (plugin / "config_schema.json").write_text(json.dumps(SCHEMA))
    return plugin


@pytest.fixture
def client(plugin_dir, tmp_path):
    from src.plugin_system.schema_manager import SchemaManager

    test_app = Flask(__name__)
    test_app.register_blueprint(api_v3, url_prefix="/api/v3")

    config_manager = MagicMock()
    config_manager.load_config.return_value = {
        "display": {"hardware": {"cols": 64, "chain_length": 2,
                                 "rows": 32, "parallel": 1}},
        PLUGIN_ID: {"enabled": False, "message": "saved"},
    }

    plugin_manager = MagicMock()
    plugin_manager.plugins_dir = str(tmp_path)

    old = (getattr(api_v3_module.api_v3, "config_manager", None),
           getattr(api_v3_module.api_v3, "plugin_manager", None),
           getattr(api_v3_module.api_v3, "schema_manager", None))
    api_v3_module.api_v3.config_manager = config_manager
    api_v3_module.api_v3.plugin_manager = plugin_manager
    api_v3_module.api_v3.schema_manager = SchemaManager(plugins_dir=tmp_path)

    with test_app.test_client() as c:
        yield c

    (api_v3_module.api_v3.config_manager,
     api_v3_module.api_v3.plugin_manager,
     api_v3_module.api_v3.schema_manager) = old


def _decode_image(data_url):
    assert data_url.startswith("data:image/png;base64,")
    raw = base64.b64decode(data_url.split(",", 1)[1])
    return Image.open(io.BytesIO(raw))


class TestPreviewEndpoint:
    def test_json_body_renders_at_default_panel_size(self, client):
        """No width/height -> the user's real panel (64*2 x 32*1)."""
        resp = client.post(f"/api/v3/plugins/preview?plugin_id={PLUGIN_ID}",
                           json={"config": {"message": "hi"}})
        assert resp.status_code == 200
        data = resp.get_json()["data"]
        img = _decode_image(data["image"])
        assert img.size == (128, 32)
        assert data["errors"] == []

    def test_explicit_size(self, client):
        resp = client.post(
            f"/api/v3/plugins/preview?plugin_id={PLUGIN_ID}&width=64&height=64",
            json={"config": {}})
        assert resp.status_code == 200
        img = _decode_image(resp.get_json()["data"]["image"])
        assert img.size == (64, 64)

    def test_form_encoding_matches_json(self, client):
        """The form path (what HTMX posts) and the JSON path must render
        the same candidate config identically."""
        via_json = client.post(
            f"/api/v3/plugins/preview?plugin_id={PLUGIN_ID}&width=128&height=32",
            json={"config": {"message": "same"}})
        via_form = client.post(
            f"/api/v3/plugins/preview?plugin_id={PLUGIN_ID}&width=128&height=32",
            data={"message": "same"})
        a = _decode_image(via_json.get_json()["data"]["image"])
        b = _decode_image(via_form.get_json()["data"]["image"])
        assert list(a.getdata()) == list(b.getdata())

    def test_candidate_config_wins_over_saved(self, client):
        """The preview must show the UNSAVED form state, not the saved
        config ('saved' vs 'candidate' render differently)."""
        saved = client.post(
            f"/api/v3/plugins/preview?plugin_id={PLUGIN_ID}&width=128&height=32",
            json={"config": {}})
        candidate = client.post(
            f"/api/v3/plugins/preview?plugin_id={PLUGIN_ID}&width=128&height=32",
            json={"config": {"message": "candidate"}})
        a = _decode_image(saved.get_json()["data"]["image"])
        b = _decode_image(candidate.get_json()["data"]["image"])
        assert list(a.getdata()) != list(b.getdata())

    def test_disabled_plugin_still_previews(self, client):
        """Saved config has enabled: False — preview forces enabled."""
        resp = client.post(f"/api/v3/plugins/preview?plugin_id={PLUGIN_ID}",
                           json={"config": {}})
        assert resp.status_code == 200
        assert resp.get_json()["data"]["errors"] == []

    def test_htmx_gets_html_fragment(self, client):
        resp = client.post(
            f"/api/v3/plugins/preview?plugin_id={PLUGIN_ID}&width=64&height=32",
            data={"message": "hi"}, headers={"HX-Request": "true"})
        assert resp.status_code == 200
        assert resp.mimetype == "text/html"
        body = resp.get_data(as_text=True)
        assert "<img" in body and "data:image/png;base64," in body

    def test_unknown_plugin_404(self, client):
        resp = client.post("/api/v3/plugins/preview?plugin_id=nope",
                           json={"config": {}})
        assert resp.status_code == 404

    def test_missing_plugin_id_400(self, client):
        resp = client.post("/api/v3/plugins/preview", json={"config": {}})
        assert resp.status_code == 400

    def test_absurd_size_rejected(self, client):
        resp = client.post(
            f"/api/v3/plugins/preview?plugin_id={PLUGIN_ID}&width=99999&height=32",
            json={"config": {}})
        assert resp.status_code == 400


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
