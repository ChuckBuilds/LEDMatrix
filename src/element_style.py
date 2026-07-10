"""Universal per-element style resolution for plugin customization.

Plugins expose per-element user customization under ``config['customization']``:

    "customization": {
        "score_text": {"font": "PressStart2P-Regular.ttf", "font_size": 10,
                       "text_color": [255, 255, 255]},
        "layout": {"score": {"x_offset": 2, "y_offset": 0}}
    }

Before this module, every plugin re-implemented the same three pieces —
a font loader, an x/y-offset reader, and (for adaptive layout mode) a
"did the user actually override this?" check. The loaders diverged four
ways across the sports plugins and music, the offset reader was copied
twice, and the override check is subtle enough that it shipped broken
twice: the web UI's save flow (schema_manager.merge_with_defaults) writes
the FULL schema default object into config.json on every save, and the
plugin manager merges defaults into ``config`` again before instantiation,
so a key being *present* never means the user set it. The only correct
test is "present AND different from the schema default", which requires
knowing the schema defaults — previously a hand-maintained dict per plugin.

This module is that logic, once:

    resolver = ElementStyleResolver(config, schema_defaults)
    style = resolver.style('score_text', classic_font='PressStart2P-Regular.ttf',
                           classic_size=10)
    style.font          # loaded PIL font, ready for draw.text
    style.user_forced   # True only for a genuine user override
    dx, dy = resolver.offset('score')

``BasePlugin.element_style()`` wires this up automatically (schema defaults
come from the plugin's own config_schema.json via the schema manager).
Standalone helper classes (e.g. a plugin's GameRenderer) should receive a
resolver from their owning plugin rather than build one themselves.

Deliberately pure PIL + stdlib: no imports from the plugin system or web
layer, so it is usable from any renderer and trivially testable.
"""

import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple, Union

from PIL import ImageFont

logger = logging.getLogger(__name__)


# Font-family aliases accepted in customization configs. Filenames pass
# through unchanged. (Supersedes the per-plugin copies in the baseball
# plugin; keep names in sync with the web UI's /fonts/catalog so the
# font-selector widget and this loader agree.)
FONT_ALIASES: Dict[str, str] = {
    "press_start": "PressStart2P-Regular.ttf",
    "four_by_six": "4x6-font.ttf",
    "five_by_seven": "5x7.bdf",
}

DEFAULT_FONTS_DIR = os.path.join("assets", "fonts")
DEFAULT_FALLBACK_FONT = "PressStart2P-Regular.ttf"

PILFont = Union[ImageFont.FreeTypeFont, ImageFont.ImageFont]


def resolve_font_name(font_name: str) -> str:
    """Resolve a font family alias to its filename, leaving filenames as-is."""
    return FONT_ALIASES.get(font_name, font_name)


def extract_schema_defaults(schema: Dict[str, Any]) -> Dict[str, Any]:
    """Nested defaults dict from a JSON Schema (mirrors
    SchemaManager.extract_defaults_from_schema, kept here so this module
    stays importable without the plugin system).

    An object property carrying its own ``default`` short-circuits recursion,
    matching the schema manager's behavior.
    """
    defaults: Dict[str, Any] = {}
    for key, prop in (schema.get("properties") or {}).items():
        if not isinstance(prop, dict):
            continue
        if "default" in prop:
            defaults[key] = prop["default"]
        elif prop.get("type") == "object" and "properties" in prop:
            nested = extract_schema_defaults(prop)
            if nested:
                defaults[key] = nested
    return defaults


def defaults_from_schema_file(schema_path: str) -> Dict[str, Any]:
    """Schema defaults straight from a plugin's own config_schema.json.

    Plugins that hand a resolver to standalone helper classes should build
    it with this, pointed at their own schema file — it works identically
    in production, the test harness, and the dev server, unlike the plugin
    manager's schema manager (absent under mocks). x-style-elements
    declarations are expanded first, so declared elements' defaults are
    included exactly as the web UI's schema manager sees them. Returns {}
    on any error.
    """
    try:
        with open(schema_path, "r", encoding="utf-8") as f:
            schema = json.load(f)
        if isinstance(schema, dict):
            return extract_schema_defaults(expand_style_elements(schema))
    except Exception as e:
        logger.debug("Could not load schema defaults from %s: %s",
                     schema_path, e)
    return {}


# ---------------------------------------------------------------------------
# x-style-elements schema expansion
# ---------------------------------------------------------------------------
#
# A plugin declares its styleable display elements ONCE, compactly, on its
# customization object instead of hand-copying ~50-line property blocks:
#
#     "customization": {
#         "type": "object",
#         "x-style-elements": {
#             "score_text": {
#                 "title": "Game Score",
#                 "font": {"default": "PressStart2P-Regular.ttf"},
#                 "size": {"default": 10, "min": 4, "max": 16},
#                 "color": true,                  # or {"default": [r,g,b]}
#                 "offsets": true
#             }
#         }
#     }
#
# expand_style_elements() turns each declaration into full font/font_size/
# text_color/layout-offset property blocks (marked "x-style-managed": true)
# using widgets the web config form already renders. The declaration stays
# in the schema — it doubles as the element registry for tooling. Expansion
# is idempotent, and a hand-written property block for the same element
# always wins over the generated one.
#
# SchemaManager.load_schema() applies this at serve time (so the web form,
# save path, validation, and defaults generation all see the expanded
# shape), and defaults_from_schema_file() applies it when plugins read
# their own schema — one implementation, no drift.

def get_style_elements(schema: Dict[str, Any]) -> Dict[str, Any]:
    """The x-style-elements declaration from a schema ({} if none)."""
    try:
        decl = schema.get("properties", {}).get("customization", {}).get("x-style-elements")
        return decl if isinstance(decl, dict) else {}
    except AttributeError:
        return {}


def expand_style_elements(schema: Dict[str, Any]) -> Dict[str, Any]:
    """Expand x-style-elements into full customization property blocks.

    Returns the schema unchanged (same object) when there is nothing to
    expand; otherwise returns an expanded DEEP COPY, leaving the input
    untouched. Never raises — on any error the original schema is returned
    so a malformed declaration can't take a plugin down.
    """
    import copy

    try:
        declarations = get_style_elements(schema)
        if not declarations:
            return schema

        schema = copy.deepcopy(schema)
        customization = schema["properties"]["customization"]
        properties = customization.setdefault("properties", {})
        order = customization.get("x-propertyOrder")

        offset_elements = []
        for element_key, declaration in declarations.items():
            if not isinstance(declaration, dict):
                continue
            if declaration.get("offsets") is True:
                offset_elements.append((element_key, declaration))
            if element_key in properties:
                # Hand-written (or previously expanded) block wins.
                continue
            properties[element_key] = _style_element_block(element_key, declaration)
            if isinstance(order, list) and element_key not in order:
                # Keep generated elements ahead of the layout section.
                insert_at = order.index("layout") if "layout" in order else len(order)
                order.insert(insert_at, element_key)

        if offset_elements:
            _expand_offset_blocks(properties, order, offset_elements)

        return schema
    except Exception as e:
        logger.error("x-style-elements expansion failed: %s", e)
        return schema


def _style_element_block(element_key: str, declaration: Dict[str, Any]) -> Dict[str, Any]:
    """One generated customization.<element> property block."""
    title = declaration.get("title") or element_key.replace("_", " ").title()
    font_decl = declaration.get("font") if isinstance(declaration.get("font"), dict) else {}
    size_decl = declaration.get("size") if isinstance(declaration.get("size"), dict) else {}

    block_properties: Dict[str, Any] = {
        "font": {
            "type": "string",
            "title": "Font Family",
            "description": "Select the font to use",
            "x-widget": "font-selector",
            "default": font_decl.get("default", DEFAULT_FALLBACK_FONT),
        },
        "font_size": {
            "type": "integer",
            "title": "Font Size",
            "description": ("Font size in pixels (BDF fonts are fixed-size "
                            "and ignore this)"),
            "minimum": size_decl.get("min", 4),
            "maximum": size_decl.get("max", 32),
            "default": size_decl.get("default", 8),
        },
    }
    block_order = ["font", "font_size"]

    color_decl = declaration.get("color")
    if color_decl:
        default_color = [255, 255, 255]
        if isinstance(color_decl, dict) and isinstance(color_decl.get("default"), list):
            default_color = color_decl["default"]
        # The default doubles as the "untouched" sentinel: the resolver only
        # honors a color that DIFFERS from it, so untouched saves (the web
        # form always posts the RGB inputs) can't clobber a plugin's
        # semantic/state-dependent colors.
        block_properties["text_color"] = {
            "type": "array",
            "title": "Text Color",
            "description": "RGB color as [red, green, blue] (0-255 each)",
            "items": {"type": "integer", "minimum": 0, "maximum": 255},
            "minItems": 3,
            "maxItems": 3,
            "x-widget": "color-picker",
            "default": default_color,
        }
        block_order.append("text_color")

    return {
        "type": "object",
        "title": title,
        "description": f"Style settings for {title}",
        "x-style-managed": True,
        "properties": block_properties,
        "x-propertyOrder": block_order,
        "additionalProperties": False,
    }


def _expand_offset_blocks(properties: Dict[str, Any], order,
                          offset_elements) -> None:
    """Generate customization.layout.<element> x/y offset blocks."""
    layout = properties.get("layout")
    if not isinstance(layout, dict):
        layout = {
            "type": "object",
            "title": "Layout Positioning",
            "description": ("Adjust X,Y coordinate offsets for elements. "
                            "Values are relative to default positions; "
                            "negative moves left/up, positive right/down."),
            "x-style-managed": True,
            "properties": {},
            "additionalProperties": False,
        }
        properties["layout"] = layout
        if isinstance(order, list) and "layout" not in order:
            order.append("layout")

    layout_properties = layout.setdefault("properties", {})
    layout_order = layout.get("x-propertyOrder")
    for element_key, declaration in offset_elements:
        if element_key in layout_properties:
            continue  # hand-written layout entry wins
        title = declaration.get("title") or element_key.replace("_", " ").title()
        layout_properties[element_key] = {
            "type": "object",
            "title": title,
            "x-style-managed": True,
            "properties": {
                "x_offset": {
                    "type": "integer",
                    "title": "X Offset",
                    "description": "Horizontal offset in pixels (default: 0)",
                    "default": 0,
                },
                "y_offset": {
                    "type": "integer",
                    "title": "Y Offset",
                    "description": "Vertical offset in pixels (default: 0)",
                    "default": 0,
                },
            },
            "additionalProperties": False,
        }
        if isinstance(layout_order, list) and element_key not in layout_order:
            layout_order.append(element_key)


def load_font(font_name: str, size: int, *,
              fonts_dir: str = DEFAULT_FONTS_DIR,
              fallback_font: str = DEFAULT_FALLBACK_FONT) -> PILFont:
    """Load a font by name at a pixel size, never raising.

    Resolution order:
      1. alias -> filename (``FONT_ALIASES``)
      2. ``ImageFont.truetype`` — handles .ttf/.otf, and .bdf too (FreeType
         loads BDF strikes at their native size; a non-native size raises
         "invalid pixel size" and falls through)
      3. for .bdf: a pre-converted ``.pil`` sidecar via ``ImageFont.load``
      4. ``fallback_font`` at the requested size
      5. ``ImageFont.load_default()``
    """
    font_name = resolve_font_name(font_name or "")
    font_path = os.path.join(fonts_dir, font_name)
    lower = font_name.lower()

    if os.path.exists(font_path):
        try:
            return ImageFont.truetype(font_path, size)
        except Exception as e:
            logger.debug("truetype failed for %s@%s: %s", font_name, size, e)
        if lower.endswith(".bdf"):
            pil_path = font_path.rsplit(".", 1)[0] + ".pil"
            if os.path.exists(pil_path):
                try:
                    return ImageFont.load(pil_path)
                except Exception as e:
                    logger.debug("PIL sidecar failed for %s: %s", pil_path, e)
            logger.warning(
                "BDF font %s could not be loaded at size %s (BDF fonts are "
                "fixed-size; font_size must match the native size). Falling "
                "back to %s.", font_name, size, fallback_font)
    else:
        logger.warning("Font file not found: %s, falling back to %s",
                       font_path, fallback_font)

    fallback_path = os.path.join(fonts_dir, resolve_font_name(fallback_font))
    try:
        return ImageFont.truetype(fallback_path, size)
    except Exception as e:
        logger.warning("Fallback font %s failed (%s); using PIL default",
                       fallback_font, e)
    return ImageFont.load_default()


@dataclass(frozen=True)
class ElementStyle:
    """Resolved style for one display element."""
    font: PILFont
    font_name: str
    font_size: int
    #: The user's color when they genuinely changed it, else the plugin's
    #: classic color (which may be state-dependent — e.g. a score that turns
    #: gold on a touchdown — so an untouched schema default must never
    #: clobber it; the web form always posts the color inputs).
    color: Optional[Tuple[int, int, int]]
    #: Additive (dx, dy) translation from customization.layout offsets.
    offset: Tuple[int, int]
    #: True when the configured value genuinely differs from the schema
    #: default (NOT merely present — saved configs always contain defaults).
    user_forced_font: bool
    user_forced_size: bool
    user_forced_color: bool = False

    @property
    def user_forced(self) -> bool:
        """True when the user pinned this element's font or size; adaptive
        layouts must use the font as-is instead of ladder-fitting. (Color is
        deliberately excluded — it never affects sizing.)"""
        return self.user_forced_font or self.user_forced_size


def _as_int(value: Any, default: int) -> int:
    """Int coercion tolerant of floats and numeric strings from configs."""
    if value is None:
        return default
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return int(value)
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _as_color(value: Any) -> Optional[Tuple[int, int, int]]:
    """[r, g, b] list/tuple -> tuple; anything else -> None."""
    if isinstance(value, (list, tuple)) and len(value) == 3:
        try:
            return tuple(max(0, min(255, int(c))) for c in value)
        except (TypeError, ValueError):
            return None
    return None


class ElementStyleResolver:
    """Resolves per-element fonts, colors and offsets from a plugin config.

    ``schema_defaults`` is the nested defaults dict extracted from the
    plugin's config_schema.json (SchemaManager.extract_defaults_from_schema).
    It is the reference for the user-override check: a configured value
    equal to its schema default is treated as untouched, because the save
    flow persists all defaults. When ``schema_defaults`` is empty (older
    cores, unit tests), the check degrades to comparing against the
    ``classic_*`` values the caller supplies.
    """

    def __init__(self, config: Optional[Dict[str, Any]],
                 schema_defaults: Optional[Dict[str, Any]] = None, *,
                 fonts_dir: str = DEFAULT_FONTS_DIR,
                 fallback_font: str = DEFAULT_FALLBACK_FONT):
        self._config = config if isinstance(config, dict) else {}
        self._defaults = schema_defaults if isinstance(schema_defaults, dict) else {}
        self._fonts_dir = fonts_dir
        self._fallback_font = fallback_font
        self._cache: Dict[Any, ElementStyle] = {}

    # -- internals ----------------------------------------------------

    def _element_config(self, element_key: str) -> Dict[str, Any]:
        cust = self._config.get("customization")
        if not isinstance(cust, dict):
            return {}
        element = cust.get(element_key)
        return element if isinstance(element, dict) else {}

    def _element_defaults(self, element_key: str) -> Dict[str, Any]:
        cust = self._defaults.get("customization")
        if not isinstance(cust, dict):
            return {}
        element = cust.get(element_key)
        return element if isinstance(element, dict) else {}

    # -- public API ---------------------------------------------------

    def style(self, element_key: str, *, classic_font: str, classic_size: int,
              classic_color: Optional[Tuple[int, int, int]] = None) -> ElementStyle:
        """Resolve the style for one element.

        ``classic_font``/``classic_size``/``classic_color`` are the plugin's
        hardcoded defaults for this element — used when the config has no
        value, and as the override reference when schema defaults are
        unavailable.
        """
        cache_key = (element_key, classic_font, classic_size, classic_color)
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        element_cfg = self._element_config(element_key)
        element_defaults = self._element_defaults(element_key)

        configured_font = element_cfg.get("font")
        configured_size = element_cfg.get("font_size")

        # Reference for "did the user change it": schema default when known,
        # else the plugin's classic default.
        reference_font = element_defaults.get("font", classic_font)
        reference_size = _as_int(element_defaults.get("font_size"), classic_size)

        user_forced_font = (configured_font is not None
                            and configured_font != reference_font)
        user_forced_size = (configured_size is not None
                            and _as_int(configured_size, reference_size) != reference_size)

        font_name = configured_font if configured_font is not None else classic_font
        font_size = _as_int(configured_size, classic_size)
        font = load_font(font_name, font_size, fonts_dir=self._fonts_dir,
                         fallback_font=self._fallback_font)

        # Color follows the same provenance rule as fonts: the web form
        # always posts the RGB inputs, so a saved config carries the schema
        # default whether or not the user touched it — only a value that
        # DIFFERS from the schema default is a real override. Otherwise keep
        # classic_color, which may be state-dependent (semantic colors like
        # a gold touchdown score) and must not be clobbered by a default.
        configured_color = _as_color(element_cfg.get("text_color"))
        default_color = _as_color(element_defaults.get("text_color"))
        if configured_color is None:
            user_forced_color = False
        elif default_color is None:
            # no schema default to compare against — presence is intent
            user_forced_color = True
        else:
            user_forced_color = configured_color != default_color
        color = configured_color if user_forced_color else classic_color

        resolved = ElementStyle(
            font=font, font_name=font_name, font_size=font_size,
            color=color, offset=self.offset(element_key),
            user_forced_font=user_forced_font, user_forced_size=user_forced_size,
            user_forced_color=user_forced_color,
        )
        self._cache[cache_key] = resolved
        return resolved

    def offset_value(self, element_key: str, axis: str, default: int = 0) -> int:
        """One offset axis for an element (e.g. 'x_offset', 'away_x_offset').

        Reads ``customization.layout.<element>`` first (the deployed sports
        convention), falling back to ``customization.<element>`` for plugins
        that keep offsets on the element itself.
        """
        cust = self._config.get("customization")
        if not isinstance(cust, dict):
            return default
        layout = cust.get("layout")
        if isinstance(layout, dict):
            element = layout.get(element_key)
            if isinstance(element, dict) and axis in element:
                return _as_int(element.get(axis), default)
        element = cust.get(element_key)
        if isinstance(element, dict) and axis in element:
            return _as_int(element.get(axis), default)
        return default

    def offset(self, element_key: str) -> Tuple[int, int]:
        """(dx, dy) additive translation for an element; (0, 0) when unset."""
        return (self.offset_value(element_key, "x_offset"),
                self.offset_value(element_key, "y_offset"))

    def clear_cache(self) -> None:
        self._cache.clear()
