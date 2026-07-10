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
    manager's schema manager (absent under mocks). Returns {} on any error.
    """
    try:
        with open(schema_path, "r", encoding="utf-8") as f:
            schema = json.load(f)
        if isinstance(schema, dict):
            return extract_schema_defaults(schema)
    except Exception as e:
        logger.debug("Could not load schema defaults from %s: %s",
                     schema_path, e)
    return {}


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
    #: None means "not customized" — keep the plugin's hardcoded color.
    color: Optional[Tuple[int, int, int]]
    #: Additive (dx, dy) translation from customization.layout offsets.
    offset: Tuple[int, int]
    #: True when the configured value genuinely differs from the schema
    #: default (NOT merely present — saved configs always contain defaults).
    user_forced_font: bool
    user_forced_size: bool

    @property
    def user_forced(self) -> bool:
        """True when the user pinned this element's font or size; adaptive
        layouts must use the font as-is instead of ladder-fitting."""
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

        color = _as_color(element_cfg.get("text_color"))
        if color is None:
            color = classic_color

        resolved = ElementStyle(
            font=font, font_name=font_name, font_size=font_size,
            color=color, offset=self.offset(element_key),
            user_forced_font=user_forced_font, user_forced_size=user_forced_size,
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
