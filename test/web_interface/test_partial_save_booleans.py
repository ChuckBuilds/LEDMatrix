"""A save must only switch off checkboxes the caller actually saw.

An HTML checkbox posts nothing when unchecked, so the save route walks the
schema and forces every boolean missing from the form to ``False``. That is
right for the rendered form and wrong for everything else: a caller that posts
four fields -- a script, the MQTT bridge, a curl against the documented
endpoint -- never rendered a checkbox, and reading its silence as "all off"
turns a one-field save into a mass disable.

That is not hypothetical. Posting four ``customization.*`` keys to a live
device switched off ``nfl.enabled``, ``ncaa_fb.enabled`` and every display-mode
toggle in one request.

The fix keeps both halves working, so both are pinned here:

* the rendered form reports the sections it drew (``__rendered_section``), and
  inside those, an absent checkbox still means unchecked -- including a section
  whose only fields are checkboxes that are all off, which no heuristic could
  recover;
* a post with no such marker only touches objects it actually posted a field
  from.
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from flask import Flask
from werkzeug.datastructures import MultiDict

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

SCHEMA = {
    "type": "object",
    "properties": {
        "enabled": {"type": "boolean", "default": True},
        "nfl": {
            "type": "object",
            "properties": {
                "enabled": {"type": "boolean", "default": True},
                "live_priority": {"type": "boolean", "default": True},
                "update_interval": {"type": "integer", "default": 3600},
                # A section with nothing but checkboxes: when they are all
                # unchecked it posts no keys at all.
                "display_modes": {
                    "type": "object",
                    "properties": {
                        "show_live": {"type": "boolean", "default": True},
                        "show_recent": {"type": "boolean", "default": True},
                    },
                },
            },
        },
        "customization": {
            "type": "object",
            "properties": {
                "score_text": {
                    "type": "object",
                    "properties": {
                        "visible": {"type": "boolean", "default": True},
                        "text_color": {
                            "type": "array",
                            "items": {"type": "integer"},
                            "minItems": 3, "maxItems": 3,
                            "default": [255, 255, 255],
                        },
                    },
                },
            },
        },
    },
}

STORED = {
    "enabled": True,
    "nfl": {
        "enabled": True,
        "live_priority": True,
        "update_interval": 3600,
        "display_modes": {"show_live": True, "show_recent": True},
    },
    "customization": {"score_text": {"visible": True,
                                     "text_color": [255, 255, 255]}},
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

    store = {"demo": json.loads(json.dumps(STORED))}

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
        # A list of pairs, not a dict: __rendered_section repeats, and that
        # repetition is the whole point of the marker.
        data = MultiDict(form) if isinstance(form, list) else form
        resp = client.post("/api/v3/plugins/config?plugin_id=demo", data=data)
        return resp, store.get("demo", {})

    try:
        yield _post
    finally:
        for k, v in originals.items():
            setattr(a.api_v3, k, v)


# What the style editor posts when someone changes one colour and nothing else.
PARTIAL = [
    ("customization.score_text.text_color.0", "255"),
    ("customization.score_text.text_color.1", "0"),
    ("customization.score_text.text_color.2", "255"),
]


class TestAPartialPost:
    def test_it_does_not_disable_another_section(self, post):
        """The live-device regression, in one assertion."""
        _post = post
        resp, cfg = _post(list(PARTIAL))
        assert resp.status_code == 200, resp.get_json()
        assert cfg["nfl"]["enabled"] is True
        assert cfg["nfl"]["live_priority"] is True
        assert cfg["nfl"]["display_modes"] == {"show_live": True,
                                               "show_recent": True}

    def test_it_still_applies_what_was_posted(self, post):
        _post = post
        _resp, cfg = _post(list(PARTIAL))
        assert cfg["customization"]["score_text"]["text_color"] == [255, 0, 255]

    def test_it_leaves_a_sibling_checkbox_it_never_mentioned(self, post):
        """`visible` lives beside the colour, but the post carried no field
        from that object -- only from the colour array inside it."""
        _post = post
        _resp, cfg = _post(list(PARTIAL))
        assert cfg["customization"]["score_text"]["visible"] is True

    def test_it_does_clear_a_box_in_an_object_it_posted_to(self, post):
        """Evidence, not guesswork: a field from `display_modes` was posted,
        so the checkbox missing from that same object really is unchecked."""
        _post = post
        _resp, cfg = _post([("nfl.display_modes.show_live", "true")])
        assert cfg["nfl"]["display_modes"] == {"show_live": True,
                                               "show_recent": False}
        assert cfg["nfl"]["enabled"] is True


class TestTheRenderedForm:
    """With section markers the old behaviour must be exactly preserved."""

    def test_an_unchecked_box_is_still_cleared(self, post):
        _post = post
        resp, cfg = _post([("__rendered_section", "nfl"),
                           ("__rendered_section", "customization"),
                           ("enabled", "true"),
                           ("nfl.update_interval", "3600"),
                           ("nfl.display_modes.show_live", "true")])
        assert resp.status_code == 200, resp.get_json()
        assert cfg["nfl"]["enabled"] is False
        assert cfg["nfl"]["live_priority"] is False
        assert cfg["nfl"]["display_modes"]["show_recent"] is False

    def test_a_section_of_only_checkboxes_all_unchecked_is_cleared(self, post):
        """It posts no keys of its own, so only the marker can vouch for it."""
        _post = post
        _resp, cfg = _post([("__rendered_section", "nfl"),
                            ("nfl.update_interval", "3600")])
        assert cfg["nfl"]["display_modes"] == {"show_live": False,
                                               "show_recent": False}

    def test_a_section_the_form_did_not_draw_is_untouched(self, post):
        _post = post
        _resp, cfg = _post([("__rendered_section", "nfl"),
                            ("nfl.update_interval", "3600")])
        assert cfg["customization"]["score_text"]["visible"] is True

    def test_the_marker_is_not_written_into_the_config(self, post):
        """It describes the submission; it is not a config path. Unknown form
        keys are otherwise stored verbatim."""
        _post = post
        _resp, cfg = _post([("__rendered_section", "nfl"),
                            ("nfl.update_interval", "3600")])
        assert "__rendered_section" not in cfg
        assert not [k for k in cfg if k.startswith("__")]


class TestTheFormEmitsTheMarker:
    """The fix only works if the rendered form actually reports its sections."""

    def test_every_top_level_section_is_reported(self, tmp_path):
        import re

        from jinja2 import Environment, FileSystemLoader

        templates = PROJECT_ROOT / "web_interface" / "templates"
        env = Environment(loader=FileSystemLoader(str(templates)))
        source = (templates / "v3" / "partials" / "plugin_config.html").read_text(
            encoding="utf-8")
        assert '__rendered_section' in source, (
            "the form must tell the save route which sections it drew")
        # It has to cover the advanced tier too, or collapsing a section into
        # Advanced Settings would quietly stop its checkboxes clearing.
        marker = re.search(r"\{% for key in ([^%]+) %\}\s*"
                           r"<input type=\"hidden\" name=\"__rendered_section\"",
                           source)
        assert marker, "marker loop not found"
        assert "tiers.basic" in marker.group(1)
        assert "tiers.advanced" in marker.group(1)
