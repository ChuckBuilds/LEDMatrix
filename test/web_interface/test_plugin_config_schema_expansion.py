"""The config form must render the same schema the save route validates.

The form used to read config_schema.json with a raw json.load while
api_v3.save_plugin_config went through SchemaManager. That is not a stylistic
difference: SchemaManager applies expand_style_elements, which turns a compact
``customization.x-style-elements`` declaration into the per-element blocks the
form knows how to render. Without it the customization object has an
x-style-elements key and no ``properties``, so the template's object branch
matched nothing and the whole section rendered as empty space -- while saving
still validated against the expanded shape.

of-the-day ships the compact form, so this was live.
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from flask import Flask

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

COMPACT_SCHEMA = {
    "type": "object",
    "properties": {
        "enabled": {"type": "boolean", "default": False},
        "customization": {
            "type": "object",
            "title": "Display Customization",
            "x-style-modes": ["live", "recent"],
            "x-style-elements": {
                "title_text": {
                    "title": "Title",
                    "font": {"default": "PressStart2P-Regular.ttf"},
                    "size": {"default": 8, "min": 4, "max": 16},
                    "color": {"default": [255, 255, 255]},
                    "offsets": True,
                },
            },
        },
    },
}

MANUAL_SCHEMA = {
    "type": "object",
    "properties": {
        "enabled": {"type": "boolean", "default": False},
        "customization": {
            "type": "object",
            "title": "Display Customization",
            "properties": {
                "title_text": {
                    "type": "object",
                    "title": "Title",
                    "properties": {
                        "font_size": {"type": "integer", "default": 8},
                    },
                },
            },
        },
    },
}


@pytest.fixture
def render(tmp_path):
    """Render a plugin's config partial, with or without a SchemaManager."""
    from web_interface.blueprints import pages_v3 as pv
    from src.plugin_system.schema_manager import SchemaManager

    orig_pm = getattr(pv.pages_v3, "plugin_manager", None)
    orig_cm = getattr(pv.pages_v3, "config_manager", None)
    orig_sm = getattr(pv.pages_v3, "schema_manager", None)

    def _render(schema, with_schema_manager=True, plugin_id="demo"):
        pdir = tmp_path / "plugins" / plugin_id
        pdir.mkdir(parents=True, exist_ok=True)
        (pdir / "config_schema.json").write_text(json.dumps(schema),
                                                 encoding="utf-8")
        (pdir / "manifest.json").write_text(
            json.dumps({"id": plugin_id, "name": plugin_id}), encoding="utf-8")

        pm = MagicMock()
        pm.plugins_dir = str(tmp_path / "plugins")
        pm.get_plugin_info.return_value = {"id": plugin_id, "name": plugin_id}
        pm.get_plugin.return_value = None
        pv.pages_v3.plugin_manager = pm

        cm = MagicMock()
        cm.load_config.return_value = {plugin_id: {"enabled": True}}
        pv.pages_v3.config_manager = cm

        pv.pages_v3.schema_manager = (
            SchemaManager(plugins_dir=tmp_path / "plugins",
                          project_root=tmp_path)
            if with_schema_manager else None)

        base = PROJECT_ROOT / "web_interface"
        app = Flask(__name__,
                    template_folder=str(base / "templates"),
                    static_folder=str(base / "static"))
        app.config["TESTING"] = True
        app.register_blueprint(pv.pages_v3, url_prefix="")
        resp = app.test_client().get(f"/partials/plugin-config/{plugin_id}")
        assert resp.status_code == 200, resp.status_code
        return resp.get_data(as_text=True)

    try:
        yield _render
    finally:
        pv.pages_v3.plugin_manager = orig_pm
        pv.pages_v3.config_manager = orig_cm
        pv.pages_v3.schema_manager = orig_sm


class TestCompactDeclarationRenders:
    def test_the_element_fields_appear(self, render):
        """This is the regression: the section used to render empty."""
        body = render(COMPACT_SCHEMA)
        assert 'name="customization.title_text.font_size"' in body
        # The font field is widget-rendered, so its <select name=...> is
        # created client-side; what the server emits is the container and
        # the key handed to the widget.
        assert "name: 'customization.title_text.font'" in body

    def test_the_colour_field_renders_as_rgb_inputs(self, render):
        body = render(COMPACT_SCHEMA)
        for channel in range(3):
            assert f'name="customization.title_text.text_color.{channel}"' in body

    def test_the_font_field_uses_the_font_selector_widget(self, render):
        body = render(COMPACT_SCHEMA)
        assert "LEDMatrixWidgets.get('font-selector')" in body

    def test_layout_offsets_appear(self, render):
        body = render(COMPACT_SCHEMA)
        assert 'name="customization.layout.title_text.x_offset"' in body
        assert 'name="customization.layout.title_text.y_offset"' in body

    def test_declared_modes_appear(self, render):
        body = render(COMPACT_SCHEMA)
        assert 'name="customization.modes.live.title_text.font_size"' in body
        assert 'name="customization.modes.recent.title_text.font_size"' in body

    def test_mode_layout_offsets_appear(self, render):
        body = render(COMPACT_SCHEMA)
        assert ('name="customization.modes.live.layout.title_text.y_offset"'
                in body)


class TestWithoutASchemaManager:
    """Several callers register this blueprint without a schema_manager; the
    raw read stays as their fallback."""

    def test_a_manual_block_still_renders(self, render):
        body = render(MANUAL_SCHEMA, with_schema_manager=False)
        assert 'name="customization.title_text.font_size"' in body

    def test_a_compact_declaration_renders_nothing_without_expansion(self, render):
        """Documents precisely what the fix buys: the fallback path cannot
        expand, so this is what every plugin using the compact form saw."""
        body = render(COMPACT_SCHEMA, with_schema_manager=False)
        assert 'name="customization.title_text.font"' not in body


class TestRenderAndSaveAgree:
    def test_the_form_posts_names_the_save_path_recognises(self, render):
        """The rendered field names must resolve against the same expanded
        schema the save route looks them up in."""
        from web_interface.blueprints.api_v3 import _get_schema_property
        from src.element_style import expand_style_elements

        render(COMPACT_SCHEMA)  # ensure it renders at all
        schema = expand_style_elements(COMPACT_SCHEMA)
        for path in ("customization.title_text.font",
                     "customization.title_text.font_size",
                     "customization.layout.title_text.x_offset",
                     "customization.modes.live.title_text.font_size"):
            assert _get_schema_property(schema, path) is not None, path
