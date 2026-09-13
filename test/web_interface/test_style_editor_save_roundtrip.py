"""The style editor's form data must survive the real save route.

The widget posts ordinary dotted field names so the existing pipeline needs no
new parsing -- but per-mode override fields are typed ``["integer", "null"]``
and ``["array", "null"]``, and two places in that pipeline compared the
declared type to a bare string:

- the indexed-array recombiner (``text_color.0/.1/.2`` -> one list) skipped
  anything whose type was not exactly ``'array'``, so a per-mode colour was
  never reassembled;
- ``_parse_form_value_with_schema`` did the same, and turned a blank nullable
  field into ``[]`` rather than ``None`` -- which then failed the ``minItems``
  the colour array declares.

Both would have surfaced as "saving a scoreboard's per-mode colour fails
validation", with nothing in the widget to suggest why. This posts what the
widget really emits and asserts on what lands in config, then on what the
resolver makes of it.
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
            "x-style-modes": ["live", "recent"],
            "x-style-elements": {
                "score_text": {
                    "title": "Score",
                    "font": {"default": "PressStart2P-Regular.ttf"},
                    "size": {"default": 10, "min": 4, "max": 16},
                    "color": {"default": [255, 255, 255]},
                    "offsets": True,
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
    """POST form data to the real save route; yields the stored config."""
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

    def _post(form):
        resp = client.post("/api/v3/plugins/config?plugin_id=demo", data=form)
        return resp, store.get("demo", {})

    try:
        yield _post, pdir
    finally:
        for k, v in originals.items():
            setattr(a.api_v3, k, v)


BASE_FORM = {
    "enabled": "true",
    "customization.score_text.font": "PressStart2P-Regular.ttf",
    "customization.score_text.font_size": "12",
    "customization.score_text.text_color.0": "255",
    "customization.score_text.text_color.1": "200",
    "customization.score_text.text_color.2": "0",
    "customization.layout.score_text.x_offset": "0",
    "customization.layout.score_text.y_offset": "0",
}

# A mode the user left alone: every field blank. Blank colours are simply
# absent, because the widget disables those inputs rather than posting "".
BLANK_MODE = {
    "customization.modes.recent.score_text.font": "",
    "customization.modes.recent.score_text.font_size": "",
    "customization.modes.recent.layout.score_text.x_offset": "",
    "customization.modes.recent.layout.score_text.y_offset": "",
}


class TestSaveRoundTrip:
    def test_the_base_element_saves(self, post):
        _post, _ = post
        resp, cfg = _post(dict(BASE_FORM))
        assert resp.status_code == 200, resp.get_json()
        assert cfg["customization"]["score_text"] == {
            "font": "PressStart2P-Regular.ttf",
            "font_size": 12,
            "text_color": [255, 200, 0],
        }

    def test_a_blank_mode_saves_as_null_not_zero(self, post):
        """Null is the inherit sentinel; 0 or [] would pin the mode."""
        _post, _ = post
        resp, cfg = _post(dict(BASE_FORM, **BLANK_MODE))
        assert resp.status_code == 200, resp.get_json()
        recent = cfg["customization"]["modes"]["recent"]
        assert recent["score_text"] == {"font": None, "font_size": None,
                                        "text_color": None}
        assert recent["layout"]["score_text"] == {"x_offset": None,
                                                  "y_offset": None}

    def test_a_partly_set_mode_keeps_the_rest_inheriting(self, post):
        _post, _ = post
        resp, cfg = _post(dict(
            BASE_FORM,
            **{"customization.modes.live.score_text.font": "",
               "customization.modes.live.score_text.font_size": "16",
               "customization.modes.live.layout.score_text.x_offset": "",
               "customization.modes.live.layout.score_text.y_offset": "-3"}))
        assert resp.status_code == 200, resp.get_json()
        live = cfg["customization"]["modes"]["live"]
        assert live["score_text"]["font_size"] == 16
        assert live["score_text"]["font"] is None
        assert live["score_text"]["text_color"] is None
        assert live["layout"]["score_text"] == {"x_offset": None,
                                                "y_offset": -3}

    def test_a_per_mode_colour_is_reassembled_into_a_list(self, post):
        """The recombiner used to skip this: its type is ["array", "null"],
        not "array", so the three indexed inputs were never joined."""
        _post, _ = post
        resp, cfg = _post(dict(
            BASE_FORM,
            **{"customization.modes.live.score_text.text_color.0": "0",
               "customization.modes.live.score_text.text_color.1": "255",
               "customization.modes.live.score_text.text_color.2": "0"}))
        assert resp.status_code == 200, resp.get_json()
        assert (cfg["customization"]["modes"]["live"]["score_text"]["text_color"]
                == [0, 255, 0])

    def test_all_blank_colour_channels_mean_inherit(self, post):
        """A hand-written form (or an older widget) can still post three
        empty strings; they must not become [] and fail minItems."""
        _post, _ = post
        resp, cfg = _post(dict(
            BASE_FORM,
            **{"customization.modes.live.score_text.text_color.0": "",
               "customization.modes.live.score_text.text_color.1": "",
               "customization.modes.live.score_text.text_color.2": ""}))
        assert resp.status_code == 200, resp.get_json()
        assert (cfg["customization"]["modes"]["live"]["score_text"]["text_color"]
                is None)


class TestWhatTheDisplayThenRenders:
    """The point of the round trip: what the resolver makes of what was saved."""

    def _styles(self, cfg, pdir):
        from src.element_style import (ElementStyleResolver,
                                       defaults_from_schema_file)
        defaults = defaults_from_schema_file(str(pdir / "config_schema.json"))
        out = {}
        for mode in (None, "live", "recent"):
            r = ElementStyleResolver(cfg, defaults, mode=mode)
            style = r.style("score_text",
                            classic_font="PressStart2P-Regular.ttf",
                            classic_size=10, classic_color=(255, 255, 255))
            out[mode] = (style.font_size, style.color, r.offset("score_text"))
        return out

    def test_a_mode_override_reaches_the_display_and_the_rest_inherits(self, post):
        _post, pdir = post
        _resp, cfg = _post(dict(
            BASE_FORM, **BLANK_MODE,
            **{"customization.modes.live.score_text.font": "",
               "customization.modes.live.score_text.font_size": "16",
               "customization.modes.live.layout.score_text.x_offset": "",
               "customization.modes.live.layout.score_text.y_offset": "-3"}))
        styles = self._styles(cfg, pdir)
        assert styles[None] == (12, (255, 200, 0), (0, 0))
        # live overrides size and y, and inherits the colour it never set
        assert styles["live"] == (16, (255, 200, 0), (0, -3))
        assert styles["recent"] == (12, (255, 200, 0), (0, 0))
