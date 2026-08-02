"""
Shared per-element style resolution for plugins (the x-style-elements system).

Plugins expose user-customizable text styling — font, size, color, and x/y
pixel offsets per named element — through their ``config_schema.json``. Two
declaration forms exist in the plugin ecosystem:

- The compact ``x-style-elements`` map on the ``customization`` object
  (of-the-day is the reference). ``expand_style_elements()`` turns it into
  the full per-element property blocks the web-UI config form renders.
- The manual ``customization`` block: hand-written per-element objects with
  ``font`` / ``font_size`` / ``text_color`` defaults (the scoreboards,
  ledmatrix-music). No expansion needed — the defaults are read as-is.

At render time a plugin builds an ``ElementStyleResolver`` from its config
and the schema-file defaults, then asks for each element's resolved style::

    from src.element_style import ElementStyleResolver, defaults_from_schema_file

    resolver = ElementStyleResolver(config, defaults_from_schema_file(schema_path))
    title = resolver.style('title_text', classic_font='PressStart2P-Regular.ttf',
                           classic_size=8, classic_color=(255, 255, 255))
    # title.font (PIL font / freetype.Face), title.color (RGB tuple),
    # title.offset ((dx, dy)), title.user_forced, title.user_forced_color

The central subtlety is what "the user set it" means. The web UI's save flow
(``schema_manager.merge_with_defaults``) writes the FULL schema-default
object into ``config.json`` on every save, whether or not the user touched
the styling section — so a value merely being *present* in config is not an
override. A value only counts as user-forced when it genuinely differs from
the schema default for that element. When nothing is forced, ``style()``
returns exactly the ``classic_*`` values the caller passes (the plugin's
pre-customization styling), so an untouched config renders byte-identically
to the classic code path. Note the classic values and the schema defaults
may legitimately differ (e.g. football's status_text: schema declares 4x6,
the classic loader fell back to PressStart) — the schema default is the
override *reference*, the classic values are the *fallback*.

``style()`` never raises: any malformed config value degrades to the classic
style with a logged warning. Font faces are cached module-wide by
(resolved path, size), and font files resolve independently of the caller's
cwd (cwd ``assets/fonts/`` first for compatibility, then the core install
root derived from this module's own location).
"""

import copy
import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple, Union

from PIL import ImageFont

try:
    import freetype
except ImportError:  # pragma: no cover - freetype ships with the core
    freetype = None

logger = logging.getLogger(__name__)

# Core install root (the directory that contains src/ and assets/fonts/),
# derived from this file so fonts resolve regardless of the caller's cwd.
_CORE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_FONTS_SUBDIR = os.path.join('assets', 'fonts')

# Last-resort font when a requested file can't be found or loaded.
_FALLBACK_FONT_NAME = 'PressStart2P-Regular.ttf'

# (resolved absolute path, size) -> loaded font face. BDF faces are stateful
# in principle, but the core's own FontManager shares faces the same way.
_font_cache: Dict[Tuple[str, int], Any] = {}

# Config keys a style element block carries, in schema/UI order.
_STYLE_KEYS = ('font', 'font_size', 'text_color')


@dataclass(frozen=True)
class ElementStyle:
    """A fully resolved style for one named element."""

    font: Any                       # PIL ImageFont or freetype.Face
    color: Tuple[int, int, int]     # resolved RGB
    offset: Tuple[int, int]         # user layout (x, y) offset, default (0, 0)
    font_name: str                  # resolved font filename
    font_size: int                  # resolved pixel size
    user_forced: bool               # font or size genuinely overridden
    user_forced_color: bool         # color genuinely overridden


# ---------------------------------------------------------------------------
# Font loading (cwd-independent, cached)
# ---------------------------------------------------------------------------

def resolve_font_path(font_name: str) -> Optional[str]:
    """Locate a font file by name, independent of the caller's cwd.

    Tries, in order: an absolute path as given; ``assets/fonts/<name>``
    relative to the cwd (the classic loaders' behavior, kept first so a
    process running from a different checkout keeps its own fonts); then
    ``assets/fonts/<name>`` under the core install root. Returns an
    absolute path, or None when the file doesn't exist anywhere.
    """
    if not font_name or not isinstance(font_name, str):
        return None
    if os.path.isabs(font_name):
        return font_name if os.path.isfile(font_name) else None
    # A relative name must be a bare filename. font_name comes from plugin
    # config, which the web UI writes; a value like "../../config/config.json"
    # would otherwise escape assets/fonts/ once joined and let a config probe
    # arbitrary paths for existence. os.path.basename collapses any such value
    # to its last component, so a name that isn't already bare is rejected.
    if os.path.basename(font_name) != font_name:
        return None
    candidates = (
        os.path.join(os.getcwd(), _FONTS_SUBDIR, font_name),
        os.path.join(_CORE_ROOT, _FONTS_SUBDIR, font_name),
    )
    for candidate in candidates:
        if os.path.isfile(candidate):
            return os.path.abspath(candidate)
    return None


def load_font(font_name: str, size: int) -> Any:
    """Load a font by filename at a pixel size, with caching and fallback.

    ``.bdf`` files load as ``freetype.Face`` (matching FontManager), other
    files through ``PIL.ImageFont.truetype``. A missing or unloadable font
    degrades to ``PressStart2P-Regular.ttf`` at the requested size, then to
    PIL's built-in default — this function never raises.
    """
    try:
        size = max(1, int(size))
    except (TypeError, ValueError):
        size = 8

    path = resolve_font_path(font_name)
    if path is None:
        logger.warning("Font file not found: %s, using fallback", font_name)
        return _load_fallback_font(size)

    cache_key = (path, size)
    cached = _font_cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        if path.lower().endswith('.bdf'):
            if freetype is None:
                raise RuntimeError("freetype not available for BDF fonts")
            face = freetype.Face(path)
            # Character size in 1/64th points at 72dpi == pixel size.
            face.set_char_size(size * 64, size * 64, 72, 72)
            font: Any = face
        else:
            font = ImageFont.truetype(path, size)
    except Exception as e:
        logger.warning("Error loading font %s at %spx: %s, using fallback",
                       path, size, e)
        return _load_fallback_font(size)

    _font_cache[cache_key] = font
    return font


def _load_fallback_font(size: int) -> Any:
    """PressStart2P at the requested size, else PIL's built-in default."""
    path = resolve_font_path(_FALLBACK_FONT_NAME)
    if path is not None:
        cache_key = (path, size)
        cached = _font_cache.get(cache_key)
        if cached is not None:
            return cached
        try:
            font = ImageFont.truetype(path, size)
            _font_cache[cache_key] = font
            return font
        except Exception as e:
            logger.error("Error loading fallback font: %s", e)
    return ImageFont.load_default()


# ---------------------------------------------------------------------------
# Schema parsing
# ---------------------------------------------------------------------------

def expand_style_elements(schema: Dict[str, Any]) -> Dict[str, Any]:
    """Expand a ``customization.x-style-elements`` declaration into the full
    per-element property blocks the web-UI config form renders.

    Each declared element becomes an object with ``font`` / ``font_size`` /
    ``text_color`` properties (only the sub-fields the declaration carries),
    tagged ``x-style-managed: true``; elements declaring ``offsets: true``
    additionally get an entry under ``customization.layout`` with
    ``x_offset`` / ``y_offset`` integers defaulting to 0. Hand-written
    element blocks with the same key are left untouched.

    Returns the schema unchanged (same object) when there is nothing to
    expand; otherwise returns an expanded deep copy. Never raises.
    """
    try:
        customization = schema.get('properties', {}).get('customization')
        if not isinstance(customization, dict):
            return schema
        declaration = customization.get('x-style-elements')
        if not isinstance(declaration, dict) or not declaration:
            return schema

        expanded = copy.deepcopy(schema)
        customization = expanded['properties']['customization']
        customization.setdefault('type', 'object')
        props = customization.setdefault('properties', {})
        layout_props: Dict[str, Any] = {}

        for element_key, spec in declaration.items():
            if not isinstance(spec, dict):
                continue
            if element_key not in props:
                props[element_key] = _element_block_from_spec(element_key, spec)
            if spec.get('offsets'):
                layout_props[element_key] = _offset_block_from_spec(
                    element_key, spec)

        if layout_props:
            layout = props.setdefault('layout', {
                'type': 'object',
                'title': 'Layout Offsets',
                'description': 'Pixel offsets applied to each element '
                               '(positive x moves right, positive y moves down)',
                'x-advanced': True,
                'properties': {},
                'additionalProperties': False,
            })
            layout.setdefault('properties', {})
            for element_key, block in layout_props.items():
                layout['properties'].setdefault(element_key, block)

        return expanded
    except Exception as e:
        logger.warning("Error expanding x-style-elements: %s", e)
        return schema


def _element_block_from_spec(element_key: str,
                             spec: Dict[str, Any]) -> Dict[str, Any]:
    """Build one expanded per-element schema block from its declaration."""
    properties: Dict[str, Any] = {}
    order = []

    font_spec = spec.get('font')
    if isinstance(font_spec, dict):
        font_prop: Dict[str, Any] = {
            'type': 'string',
            'title': 'Font Family',
            'x-advanced': True,
        }
        if 'default' in font_spec:
            font_prop['default'] = font_spec['default']
        if isinstance(font_spec.get('enum'), list):
            font_prop['enum'] = list(font_spec['enum'])
        properties['font'] = font_prop
        order.append('font')

    size_spec = spec.get('size')
    if isinstance(size_spec, dict):
        size_prop: Dict[str, Any] = {
            'type': 'integer',
            'title': 'Font Size',
            'description': 'Font size in pixels',
            'x-advanced': True,
        }
        if 'default' in size_spec:
            size_prop['default'] = size_spec['default']
        if 'min' in size_spec:
            size_prop['minimum'] = size_spec['min']
        if 'max' in size_spec:
            size_prop['maximum'] = size_spec['max']
        properties['font_size'] = size_prop
        order.append('font_size')

    color_spec = spec.get('color')
    if isinstance(color_spec, dict):
        color_prop: Dict[str, Any] = {
            'type': 'array',
            'title': 'Text Color',
            'items': {'type': 'integer', 'minimum': 0, 'maximum': 255},
            'minItems': 3,
            'maxItems': 3,
            'x-widget': 'color-picker',
        }
        if 'default' in color_spec:
            color_prop['default'] = list(color_spec['default'])
        properties['text_color'] = color_prop
        order.append('text_color')

    return {
        'type': 'object',
        'title': spec.get('title', element_key),
        'x-style-managed': True,
        'x-propertyOrder': order,
        'additionalProperties': False,
        'properties': properties,
    }


def _offset_block_from_spec(element_key: str,
                            spec: Dict[str, Any]) -> Dict[str, Any]:
    """Build one layout.<element> offset block (x/y, default 0)."""
    axis = {
        'type': 'integer',
        'default': 0,
        'x-advanced': True,
    }
    return {
        'type': 'object',
        'title': spec.get('title', element_key),
        'x-style-managed': True,
        'additionalProperties': False,
        'properties': {
            'x_offset': dict(axis, title='X Offset'),
            'y_offset': dict(axis, title='Y Offset'),
        },
    }


def defaults_from_schema(schema: Dict[str, Any]) -> Dict[str, Any]:
    """Extract per-element style defaults from a config schema dict.

    Understands both declaration forms: the compact ``x-style-elements``
    map, and hand-written per-element blocks under
    ``customization.properties`` (their ``font`` / ``font_size`` /
    ``text_color`` property defaults). Returns a config-shaped dict::

        {"customization": {"<element>": {"font": ..., "font_size": ...,
                                         "text_color": [...]}, ...}}

    Elements with no declared defaults are omitted. Never raises.
    """
    elements: Dict[str, Dict[str, Any]] = {}
    try:
        customization = schema.get('properties', {}).get('customization')
        if not isinstance(customization, dict):
            return {'customization': elements}

        declaration = customization.get('x-style-elements')
        if isinstance(declaration, dict):
            for element_key, spec in declaration.items():
                if not isinstance(spec, dict):
                    continue
                defaults: Dict[str, Any] = {}
                font_spec = spec.get('font')
                if isinstance(font_spec, dict) and 'default' in font_spec:
                    defaults['font'] = font_spec['default']
                size_spec = spec.get('size')
                if isinstance(size_spec, dict) and 'default' in size_spec:
                    defaults['font_size'] = size_spec['default']
                color_spec = spec.get('color')
                if isinstance(color_spec, dict) and 'default' in color_spec:
                    defaults['text_color'] = list(color_spec['default'])
                if defaults:
                    elements[element_key] = defaults

        properties = customization.get('properties')
        if isinstance(properties, dict):
            for element_key, block in properties.items():
                if element_key == 'layout' or element_key in elements:
                    continue
                if not isinstance(block, dict):
                    continue
                block_props = block.get('properties')
                if not isinstance(block_props, dict):
                    continue
                defaults = {}
                for style_key in _STYLE_KEYS:
                    prop = block_props.get(style_key)
                    if isinstance(prop, dict) and 'default' in prop:
                        defaults[style_key] = prop['default']
                if defaults:
                    elements[element_key] = defaults
    except Exception as e:
        logger.warning("Error extracting style defaults from schema: %s", e)
    return {'customization': elements}


def defaults_from_schema_file(schema_path: Union[str, os.PathLike]) -> Dict[str, Any]:
    """``defaults_from_schema`` for a schema file on disk. A missing or
    malformed file yields empty defaults (with a logged warning) — every
    configured value then counts as a user override, which is the safe
    degradation. Never raises."""
    try:
        with open(schema_path, 'r', encoding='utf-8') as f:
            schema = json.load(f)
        if not isinstance(schema, dict):
            raise ValueError("schema is not a JSON object")
    except Exception as e:
        logger.warning("Could not read style defaults from %s: %s",
                       schema_path, e)
        return {'customization': {}}
    return defaults_from_schema(schema)


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------

def _normalize_color(value: Any) -> Optional[Tuple[int, int, int]]:
    """An (r, g, b) tuple of ints in 0..255, or None for anything else."""
    if isinstance(value, (list, tuple)) and len(value) == 3:
        try:
            rgb = tuple(int(c) for c in value)
        except (TypeError, ValueError):
            return None
        if all(0 <= c <= 255 for c in rgb):
            return rgb  # type: ignore[return-value]
    return None


class ElementStyleResolver:
    """Resolves per-element user styling against schema defaults.

    Built from a plugin's live config dict and the defaults extracted from
    its own ``config_schema.json`` (``defaults_from_schema_file``). The
    config dict is held by reference as ``_config`` — consumers compare
    identity (``resolver._config is not self.config``) to decide when a
    resolver must be rebuilt after ``on_config_change`` swaps the dict.

    A configured font/size/color counts as user-forced only when it differs
    from the schema default (see module docstring); otherwise ``style()``
    returns the caller's classic values verbatim, keeping untouched configs
    byte-identical to pre-customization rendering.
    """

    def __init__(self, config: Optional[Dict[str, Any]],
                 defaults: Optional[Dict[str, Any]] = None):
        # Keep the exact object for identity-based invalidation, even if the
        # caller hands us something odd; reads are guarded.
        self._config = config
        if isinstance(defaults, dict):
            element_defaults = defaults.get('customization', {})
        else:
            element_defaults = {}
        self._defaults: Dict[str, Any] = (
            element_defaults if isinstance(element_defaults, dict) else {})
        self._memo: Dict[Any, ElementStyle] = {}

    # -- internal accessors -------------------------------------------------

    def _customization(self) -> Dict[str, Any]:
        config = self._config if isinstance(self._config, dict) else {}
        customization = config.get('customization', {})
        return customization if isinstance(customization, dict) else {}

    def _element_config(self, element_key: str) -> Dict[str, Any]:
        element = self._customization().get(element_key, {})
        return element if isinstance(element, dict) else {}

    def _element_defaults(self, element_key: str) -> Dict[str, Any]:
        defaults = self._defaults.get(element_key, {})
        return defaults if isinstance(defaults, dict) else {}

    # -- public API ---------------------------------------------------------

    def style(self, element_key: str,
              classic_font: str = _FALLBACK_FONT_NAME,
              classic_size: int = 8,
              classic_color: Optional[Tuple[int, int, int]] = None) -> ElementStyle:
        """Resolve one element's style. Never raises.

        Args:
            element_key: Key under ``config['customization']`` (e.g.
                ``'title_text'``).
            classic_font: Font filename the plugin's classic (pre-
                customization) code used for this element.
            classic_size: Classic pixel size.
            classic_color: Classic RGB color, or None when the caller only
                cares about the font (``.color`` then falls back to the
                schema default color, else white).

        Returns:
            ElementStyle with the loaded font face, RGB color, (x, y)
            offset, and the ``user_forced`` / ``user_forced_color`` flags.
        """
        try:
            memo_key = (element_key, classic_font, classic_size,
                        _normalize_color(classic_color) or classic_color)
            memoized = self._memo.get(memo_key)
            if memoized is not None:
                return memoized
        except Exception:
            memo_key = None

        try:
            resolved = self._resolve(element_key, classic_font,
                                     classic_size, classic_color)
        except Exception as e:
            logger.warning("Error resolving style for element '%s': %s — "
                           "using classic style", element_key, e)
            resolved = self._classic_style(classic_font, classic_size,
                                           classic_color)
        if memo_key is not None:
            self._memo[memo_key] = resolved
        return resolved

    def offset(self, element_key: str) -> Tuple[int, int]:
        """The user's ``customization.layout.<element>`` (x, y) pixel
        offset, defaulting to (0, 0). Never raises."""
        return (self.offset_value(element_key, 'x_offset', 0),
                self.offset_value(element_key, 'y_offset', 0))

    def offset_value(self, element_key: str, axis: str, default: int = 0) -> int:
        """One ``customization.layout.<element>.<axis>`` value as an int.

        ``axis`` is usually ``'x_offset'`` / ``'y_offset'`` but any key is
        honored (e.g. the scoreboards' ``'away_x_offset'``). Numeric
        strings are coerced; anything else degrades to ``default``. Never
        raises.
        """
        try:
            layout = self._customization().get('layout', {})
            if not isinstance(layout, dict):
                return int(default)
            element = layout.get(element_key, {})
            if not isinstance(element, dict):
                return int(default)
            value = element.get(axis, default)
            if isinstance(value, bool):
                return int(default)
            if isinstance(value, (int, float)):
                return int(value)
            if isinstance(value, str):
                try:
                    return int(float(value))
                except (TypeError, ValueError):
                    logger.warning(
                        "Invalid layout offset for %s.%s: %r, using %s",
                        element_key, axis, value, default)
                    return int(default)
            return int(default)
        except Exception as e:
            logger.warning("Error reading layout offset %s.%s: %s",
                           element_key, axis, e)
            try:
                return int(default)
            except (TypeError, ValueError):
                return 0

    # -- resolution internals -----------------------------------------------

    def _resolve(self, element_key: str, classic_font: str,
                 classic_size: int,
                 classic_color: Optional[Tuple[int, int, int]]) -> ElementStyle:
        element_config = self._element_config(element_key)
        element_defaults = self._element_defaults(element_key)

        # Font family: forced only when it differs from the schema default
        # (falling back to the classic font as the reference when the
        # schema declares none).
        default_font = element_defaults.get('font', classic_font)
        configured_font = element_config.get('font')
        font_forced = (isinstance(configured_font, str) and configured_font
                       and configured_font != default_font)

        # Font size: same rule, with defensive int coercion.
        default_size = self._coerce_size(
            element_defaults.get('font_size'), None)
        if default_size is None:
            default_size = self._coerce_size(classic_size, 8)
        configured_size = self._coerce_size(element_config.get('font_size'),
                                            None)
        size_forced = (configured_size is not None
                       and configured_size != default_size)

        font_name = configured_font if font_forced else classic_font
        font_size = configured_size if size_forced else self._coerce_size(
            classic_size, 8)
        user_forced = bool(font_forced or size_forced)

        # Color: forced only when it differs from the schema default (or,
        # absent one, from the classic color).
        default_color = _normalize_color(element_defaults.get('text_color'))
        configured_color = _normalize_color(element_config.get('text_color'))
        reference_color = (default_color if default_color is not None
                           else _normalize_color(classic_color))
        color_forced = (configured_color is not None
                        and configured_color != reference_color)
        if color_forced:
            color = configured_color
        else:
            color = (_normalize_color(classic_color) or default_color
                     or (255, 255, 255))

        return ElementStyle(
            font=load_font(font_name, font_size),
            color=color,
            offset=self.offset(element_key),
            font_name=font_name,
            font_size=font_size,
            user_forced=user_forced,
            user_forced_color=bool(color_forced),
        )

    def _classic_style(self, classic_font: str, classic_size: int,
                       classic_color: Optional[Tuple[int, int, int]]) -> ElementStyle:
        """The untouched fallback style — used when resolution itself
        fails, so ``style()`` can keep its never-raises promise."""
        size = self._coerce_size(classic_size, 8)
        return ElementStyle(
            font=load_font(classic_font, size),
            color=_normalize_color(classic_color) or (255, 255, 255),
            offset=(0, 0),
            font_name=classic_font,
            font_size=size,
            user_forced=False,
            user_forced_color=False,
        )

    @staticmethod
    def _coerce_size(value: Any, default: Optional[int]) -> Optional[int]:
        """An int pixel size, or ``default`` for None/garbage."""
        if value is None or isinstance(value, bool):
            return default
        try:
            size = int(value)
        except (TypeError, ValueError):
            return default
        return size if size > 0 else default
