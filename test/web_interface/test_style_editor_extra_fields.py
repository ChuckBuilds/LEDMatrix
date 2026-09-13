"""visible / align / scale, from the form the widget renders to the style the
display resolves.

These three are the difference between "restyle the text" and "lay the card
out", and each has a way of going quietly wrong in a form post:

- ``visible`` is a boolean, and an unchecked checkbox posts nothing at all,
  so the widget carries the value in a hidden field. The save path has to
  turn those strings into real booleans, or config.json grows "false"
  strings that are truthy everywhere they are read.
- ``align`` is an enum, so an invalid value must not reach the renderer.
- ``scale`` is a float living in the layout block rather than the element
  block, because a logo has a scale and no font.
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from flask import Flask

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

SCHEMA = {
    "type": "object",
    "properties": {
        "enabled": {"type": "boolean", "default": True},
        "customization": {
            "type": "object",
            "x-style-modes": ["live"],
            "x-style-elements": {
                "score_text": {
                    "title": "Score",
                    "font": {"default": "PressStart2P-Regular.ttf"},
                    "size": {"default": 10, "min": 4, "max": 16},
                    "color": {"default": [255, 255, 255]},
                    "visible": True,
                    "align": {"default": "center"},
                    "offsets": True,
                },
                "home_logo": {
                    "title": "Home Logo",
                    "offsets": True,
                    "visible": True,
                    "scale": {"default": 1.0, "min": 0.25, "max": 4},
                },
            },
        },
    },
}


class _SaveOk:
    status = type("S", (), {"value": "success"})()
    message = None


@pytest.fixture
def post(tmp_path):
    from src.plugin_system.schema_manager import SchemaManager
    from web_interface.blueprints import api_v3 as a

    originals = {k: getattr(a.api_v3, k, None)
                 for k in ("config_manager", "schema_manager", "plugin_manager")}

    pdir = tmp_path / "plugins" / "demo"
    pdir.mkdir(parents=True)
    (pdir / "config_schema.json").write_text(json.dumps(SCHEMA), encoding="utf-8")

    store = {"demo": {"enabled": True}}
    cm = MagicMock()
    cm.load_config.side_effect = lambda: json.loads(json.dumps(store))
    cm.get_config_path.return_value = str(tmp_path / "config.json")

    def _save(cfg, **_kw):
        store.clear()
        store.update(cfg)
        return _SaveOk()

    cm.save_config_atomic.side_effect = _save

    a.api_v3.config_manager = cm
    a.api_v3.schema_manager = SchemaManager(plugins_dir=tmp_path / "plugins",
                                            project_root=tmp_path)
    pm = MagicMock()
    pm.plugins = {}
    pm.get_plugin.return_value = None
    a.api_v3.plugin_manager = pm

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(a.api_v3, url_prefix="/api/v3")
    client = app.test_client()

    def _post(extra):
        form = {
            "enabled": "true",
            "customization.score_text.font": "PressStart2P-Regular.ttf",
            "customization.score_text.font_size": "10",
            "customization.score_text.text_color.0": "255",
            "customization.score_text.text_color.1": "255",
            "customization.score_text.text_color.2": "255",
            "customization.score_text.visible": "true",
            "customization.score_text.align": "center",
            "customization.layout.score_text.x_offset": "0",
            "customization.layout.score_text.y_offset": "0",
            "customization.home_logo.visible": "true",
            "customization.layout.home_logo.x_offset": "0",
            "customization.layout.home_logo.y_offset": "0",
            "customization.layout.home_logo.scale": "1",
        }
        form.update(extra)
        resp = client.post("/api/v3/plugins/config?plugin_id=demo", data=form)
        return resp, store.get("demo", {})

    def _style(cfg, element="score_text", mode=None):
        from src.element_style import (ElementStyleResolver,
                                       defaults_from_schema_file)
        defaults = defaults_from_schema_file(str(pdir / "config_schema.json"))
        return ElementStyleResolver(cfg, defaults, mode=mode).style(
            element, classic_font="PressStart2P-Regular.ttf", classic_size=10,
            classic_color=(255, 255, 255))

    try:
        yield _post, _style
    finally:
        for k, v in originals.items():
            setattr(a.api_v3, k, v)


class TestVisible:
    def test_a_checked_box_stores_a_real_boolean(self, post):
        _post, _ = post
        resp, cfg = _post({})
        assert resp.status_code == 200, resp.get_json()
        stored = cfg["customization"]["score_text"]["visible"]
        assert stored is True, f"stored {stored!r}, not a bool"

    def test_hiding_an_element_survives_the_round_trip(self, post):
        _post, _style = post
        resp, cfg = _post({"customization.score_text.visible": "false"})
        assert resp.status_code == 200, resp.get_json()
        assert cfg["customization"]["score_text"]["visible"] is False
        assert _style(cfg).visible is False

    def test_the_default_is_not_read_as_a_choice(self, post):
        """The save flow writes the schema default in either way."""
        _post, _style = post
        _resp, cfg = _post({})
        assert _style(cfg).visible is True


class TestAlign:
    def test_a_choice_reaches_the_display(self, post):
        _post, _style = post
        resp, cfg = _post({"customization.score_text.align": "right"})
        assert resp.status_code == 200, resp.get_json()
        assert _style(cfg).align == "right"

    def test_the_schema_default_resolves_to_no_preference(self, post):
        """'center' is what the schema declares, so the plugin keeps doing
        whatever it already did rather than being told to centre."""
        _post, _style = post
        _resp, cfg = _post({})
        assert _style(cfg).align is None

    def test_an_invalid_alignment_is_rejected_on_save(self, post):
        _post, _ = post
        resp, _cfg = _post({"customization.score_text.align": "sideways"})
        assert resp.status_code == 400


class TestScale:
    def test_a_logo_scale_saves_into_the_layout_block(self, post):
        _post, _style = post
        resp, cfg = _post({"customization.layout.home_logo.scale": "2.5"})
        assert resp.status_code == 200, resp.get_json()
        assert cfg["customization"]["layout"]["home_logo"]["scale"] == 2.5
        assert _style(cfg, "home_logo").scale == 2.5

    def test_a_scale_outside_the_declared_range_is_rejected(self, post):
        _post, _ = post
        resp, _cfg = _post({"customization.layout.home_logo.scale": "99"})
        assert resp.status_code == 400

    def test_an_element_without_a_declared_scale_stays_neutral(self, post):
        _post, _style = post
        _resp, cfg = _post({})
        assert _style(cfg, "score_text").scale == 1.0


class TestPerMode:
    def test_a_mode_can_hide_what_the_base_shows(self, post):
        _post, _style = post
        resp, cfg = _post({"customization.modes.live.score_text.visible": "false",
                           "customization.modes.live.score_text.align": "",
                           "customization.modes.live.score_text.font": "",
                           "customization.modes.live.score_text.font_size": ""})
        assert resp.status_code == 200, resp.get_json()
        assert _style(cfg).visible is True
        assert _style(cfg, mode="live").visible is False

    def test_a_blank_mode_boolean_means_inherit(self, post):
        _post, _style = post
        resp, cfg = _post({"customization.score_text.visible": "false",
                           "customization.modes.live.score_text.visible": ""})
        assert resp.status_code == 200, resp.get_json()
        assert cfg["customization"]["modes"]["live"]["score_text"]["visible"] is None
        assert _style(cfg, mode="live").visible is False, "inherits the base"
