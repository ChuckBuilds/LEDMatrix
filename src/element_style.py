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
import math
import os
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple, Union

from PIL import ImageFont
from src.common.font_layout import load_truetype

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

# (resolved absolute path, requested size) -> (font face, realised size).
# BDF faces are stateful in principle, but the core's own FontManager shares
# faces the same way.
#
# Bounded LRU rather than the unbounded dict this started as: the display
# process runs for weeks, and every config save can introduce a new
# (font, size) pair. 256 is far above the working set -- a panel draws from a
# handful of faces -- while still having a ceiling. Matches the house style of
# every other hot cache (display_manager, font_manager, adaptive_layout).
_FONT_CACHE_MAX = 256
_font_cache: 'OrderedDict[Tuple[str, int], Tuple[Any, int]]' = OrderedDict()


def _cache_put(key: Tuple[str, int], value: Tuple[Any, int]) -> None:
    """Insert, evicting the least recently used entry past the bound."""
    _font_cache[key] = value
    _font_cache.move_to_end(key)
    while len(_font_cache) > _FONT_CACHE_MAX:
        _font_cache.popitem(last=False)

# Config keys a style element block carries, in schema/UI order.
_STYLE_KEYS = ('font', 'font_size', 'text_color', 'visible', 'align')


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

    # The three below default to "change nothing", so a caller that ignores
    # them renders exactly as it did before they existed, and a caller that
    # honours them sees a neutral value until the user actually asks for
    # something. That is what keeps an untouched config byte-identical.
    visible: bool = True            # False hides the element entirely
    align: Optional[str] = None     # 'left'|'center'|'right'; None = caller's own
    scale: float = 1.0              # size multiplier for images/logos


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


def native_bdf_size(font_name: str) -> Optional[int]:
    """The one pixel size a BDF font can render at, or None.

    None means "not a BDF, not found, or unreadable" — i.e. the size is a
    free choice. The web UI uses this to lock the size field for a bitmap
    font instead of offering a number that cannot take effect.
    """
    path = resolve_font_path(font_name)
    if path is None or not path.lower().endswith('.bdf'):
        return None
    return _read_bdf_native_size(path)


def _read_bdf_native_size(path: str) -> Optional[int]:
    """A BDF file's own pixel size, delegated to FontManager.

    Deliberately not reimplemented: FontManager's reader prefers PIXEL_SIZE
    over the SIZE line's point-size (they differ on the several bundled
    fonts defined at 75dpi) and stops at the first STARTCHAR. Core always
    ships it; the guard is for the plugin test harnesses that stub the
    module out.
    """
    try:
        from src.font_manager import FontManager
        return FontManager._read_bdf_native_size(path)
    except Exception:  # pragma: no cover - defensive
        return None


def _load_bdf(path: str, size: int) -> Tuple[Any, int]:
    """A ``freetype.Face`` for a BDF file at the closest size it can do.

    BDF fonts are fixed-size bitmap strikes, not scalable outlines:
    FreeType accepts only the exact pixel size baked into the file and
    raises for anything else. 32 of the 35 shipped fonts are BDF, so a
    size the user picked in the web UI usually is not a valid strike.

    Retrying at the file's native size is the behaviour SportsCore already
    has (``_load_custom_font_from_element_config``). Without it this
    function fell through to the generic except below and returned
    *PressStart2P* — so choosing 5x7.bdf at size 10 silently rendered a
    completely different typeface rather than 5x7 at 7px.
    """
    if freetype is None:
        raise RuntimeError("freetype not available for BDF fonts")

    def _face_at(px: int) -> Any:
        face = freetype.Face(path)
        # Character size in 1/64th points at 72dpi == pixel size.
        face.set_char_size(px * 64, px * 64, 72, 72)
        return face

    try:
        return _face_at(size), size
    except Exception:
        native = _read_bdf_native_size(path)
        if not native or native == size:
            raise
        # A fresh Face: the first one already took a failed set_char_size.
        face = _face_at(native)
        logger.debug("BDF font %s loaded at its native size %s "
                     "(requested %s is not a strike in this file)",
                     path, native, size)
        return face, native


def load_font(font_name: str, size: int) -> Any:
    """Load a font by filename at a pixel size, with caching and fallback.

    ``.bdf`` files load as ``freetype.Face`` (matching FontManager), other
    files through the pinned ``load_truetype``. A BDF asked for a size it
    has no strike for falls back to its own native size (see
    :func:`_load_bdf`), not to a different font. A missing or unloadable
    font degrades to ``PressStart2P-Regular.ttf`` at the requested size,
    then to PIL's built-in default — this function never raises.
    """
    return _load_font_sized(font_name, size)[0]


def _load_font_sized(font_name: str, size: int) -> Tuple[Any, int]:
    """``load_font`` plus the pixel size actually realised.

    The two differ only for a BDF snapped to its native strike. Callers
    that lay out by size (line heights, ladders) need the realised value,
    or they reserve space for a size nothing was drawn at.
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
        _font_cache.move_to_end(cache_key)
        return cached

    try:
        if path.lower().endswith('.bdf'):
            font, effective = _load_bdf(path, size)
        else:
            font, effective = load_truetype(path, size), size
    except Exception as e:
        logger.warning("Error loading font %s at %spx: %s, using fallback",
                       path, size, e)
        return _load_fallback_font(size)

    _cache_put(cache_key, (font, effective))
    return font, effective


def _load_fallback_font(size: int) -> Tuple[Any, int]:
    """PressStart2P at the requested size, else PIL's built-in default."""
    path = resolve_font_path(_FALLBACK_FONT_NAME)
    if path is not None:
        cache_key = (path, size)
        cached = _font_cache.get(cache_key)
        if cached is not None:
            _font_cache.move_to_end(cache_key)
            return cached
        try:
            entry = (load_truetype(path, size), size)
            _cache_put(cache_key, entry)
            return entry
        except Exception as e:
            logger.error("Error loading fallback font: %s", e)
    return ImageFont.load_default(), size


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
            # No compact declaration: the plugin may still have hand-written
            # its style blocks longhand, which nineteen of them do.
            return _adopt_handwritten_block(schema, customization)

        expanded = copy.deepcopy(schema)
        customization = expanded['properties']['customization']
        customization.setdefault('type', 'object')
        # One composite editor for the whole block. Rendered element by
        # element, a realistic scoreboard is 65 nested accordions and five
        # levels of clicking to reach one per-mode font size; the widget
        # collapses that to a row per element. setdefault, so a plugin that
        # names its own widget keeps it.
        customization.setdefault('x-widget', 'style-editor')
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

        modes = customization.get('x-style-modes')
        if isinstance(modes, list) and modes:
            props.setdefault('modes',
                             _modes_block(declaration, modes))

        # Declaration order, stated explicitly. Python preserves it in the
        # dict, but the config form serialises the schema to JSON with
        # Flask's provider, which sorts keys -- so without this the elements
        # reach the browser alphabetised, and a scoreboard lists Detail and
        # Odds above Score.
        order = [k for k in declaration if isinstance(declaration.get(k), dict)]
        order += [k for k in ('layout', 'modes') if k in props]
        customization.setdefault('x-propertyOrder', order)

        return expanded
    except Exception as e:
        logger.warning("Error expanding x-style-elements: %s", e)
        return schema


def _element_block_from_spec(element_key: str,
                             spec: Dict[str, Any]) -> Dict[str, Any]:
    """Build one expanded per-element schema block from its declaration."""
    properties: Dict[str, Any] = {}
    order = []

    size_spec = spec.get('size') if isinstance(spec.get('size'), dict) else None
    font_spec = spec.get('font')
    if isinstance(font_spec, dict):
        font_prop: Dict[str, Any] = {
            'type': 'string',
            'title': 'Font Family',
            'x-advanced': True,
            # The core already ships this widget and the config form already
            # allowlists it; without the hint the field rendered as a bare
            # text box the user had to type a filename into.
            'x-widget': 'font-selector',
        }
        # A bitmap font ignores font_size and renders at its own baked-in
        # size, so the size ceiling has to be enforced when picking the
        # font, not when setting the size.
        max_size = (size_spec or {}).get('max') if isinstance(
            spec.get('size'), dict) else None
        if isinstance(max_size, (int, float)):
            font_prop['x-options'] = {'maxFixedSize': max_size}
        if 'default' in font_spec:
            font_prop['default'] = font_spec['default']
        if isinstance(font_spec.get('enum'), list):
            font_prop['enum'] = list(font_spec['enum'])
        properties['font'] = font_prop
        order.append('font')

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

    # ``"visible": true`` is accepted as shorthand for
    # ``{"default": true}`` -- the common case is a plugin saying only that
    # the element can be hidden.
    visible_spec = spec.get('visible')
    if visible_spec is True or isinstance(visible_spec, dict):
        default = True
        if isinstance(visible_spec, dict):
            default = bool(visible_spec.get('default', True))
        properties['visible'] = {
            'type': 'boolean',
            'title': 'Show',
            'default': default,
            'x-widget': 'toggle-switch',
        }
        order.append('visible')

    align_spec = spec.get('align')
    if align_spec is True or isinstance(align_spec, dict):
        align_prop: Dict[str, Any] = {
            'type': 'string',
            'title': 'Align',
            'enum': list(_ALIGNMENTS),
            'x-advanced': True,
        }
        if isinstance(align_spec, dict) and 'default' in align_spec:
            align_prop['default'] = align_spec['default']
        properties['align'] = align_prop
        order.append('align')

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
    """Build one layout.<element> block: x/y offsets, and scale if declared."""
    axis = {
        'type': 'integer',
        'default': 0,
        'x-advanced': True,
    }
    properties: Dict[str, Any] = {
        'x_offset': dict(axis, title='X Offset'),
        'y_offset': dict(axis, title='Y Offset'),
    }

    # scale sits here rather than in the element block because it is
    # geometry, like the offsets: a logo has a scale and no font, and the
    # renderer applies both when it places the thing.
    scale_spec = spec.get('scale')
    if scale_spec is True or isinstance(scale_spec, dict):
        scale_prop: Dict[str, Any] = {
            'type': 'number',
            'title': 'Scale',
            'description': 'Size multiplier; 1 is the shipped size.',
            'default': 1.0,
            'minimum': 0.1,
            'maximum': 10.0,
            'x-advanced': True,
        }
        if isinstance(scale_spec, dict):
            for key, prop_key in (('default', 'default'),
                                  ('min', 'minimum'), ('max', 'maximum')):
                if key in scale_spec:
                    scale_prop[prop_key] = scale_spec[key]
        properties['scale'] = scale_prop

    return {
        'type': 'object',
        'title': spec.get('title', element_key),
        'x-style-managed': True,
        'additionalProperties': False,
        'properties': properties,
    }


def _nullable(prop: Dict[str, Any]) -> Dict[str, Any]:
    """The same property, retyped as "this or unset".

    A mode field defaults to null, meaning inherit the base element. The
    default has to be null rather than the base value: the save flow writes
    schema defaults into config.json wholesale, so a concrete default here
    would turn every mode into a copy of the base the moment a user pressed
    Save, and the base would stop reaching them.
    """
    out = dict(prop)
    declared = out.get('type', 'string')
    types = declared if isinstance(declared, list) else [declared]
    if 'null' not in types:
        types = list(types) + ['null']
    out['type'] = types
    out['default'] = None
    # An enum constrains the value independently of the type, so widening
    # the type is not enough: null has to be an allowed choice too, or the
    # default this function just set fails its own schema. That is not a
    # corner case -- the save flow writes the default into config, so a
    # plugin declaring an enum field with modes could not save at all.
    if isinstance(out.get('enum'), list) and None not in out['enum']:
        out['enum'] = list(out['enum']) + [None]
    return out


def _mode_element_block(element_key: str, spec: Dict[str, Any]) -> Dict[str, Any]:
    """One element's override block for one mode: every field nullable."""
    base = _element_block_from_spec(element_key, spec)
    base['properties'] = {k: _nullable(v)
                          for k, v in base.get('properties', {}).items()}
    base['description'] = ('Leave blank to use the settings above for this '
                           'mode.')
    return base


def _mode_offset_block(element_key: str, spec: Dict[str, Any]) -> Dict[str, Any]:
    """One element's offset overrides for one mode: both axes nullable."""
    base = _offset_block_from_spec(element_key, spec)
    base['properties'] = {k: _nullable(v)
                          for k, v in base.get('properties', {}).items()}
    return base


def _modes_block(declaration: Dict[str, Any],
                 modes: Any) -> Dict[str, Any]:
    """``customization.modes`` — one override group per declared mode."""
    mode_props: Dict[str, Any] = {}
    for mode in modes:
        if not isinstance(mode, str) or not mode:
            continue
        element_props: Dict[str, Any] = {}
        layout_props: Dict[str, Any] = {}
        for element_key, spec in declaration.items():
            if not isinstance(spec, dict):
                continue
            element_props[element_key] = _mode_element_block(element_key, spec)
            if spec.get('offsets'):
                layout_props[element_key] = _mode_offset_block(element_key, spec)
        if layout_props:
            element_props['layout'] = {
                'type': 'object',
                'title': 'Layout Offsets',
                'x-advanced': True,
                'additionalProperties': False,
                'properties': layout_props,
            }
        mode_props[mode] = {
            'type': 'object',
            'title': mode.replace('_', ' ').title(),
            'x-style-managed': True,
            'additionalProperties': False,
            'properties': element_props,
        }
    return {
        'type': 'object',
        'title': 'Per-Mode Overrides',
        'description': 'Override the settings above for one display mode. '
                       'Anything left blank follows the settings above.',
        'x-advanced': True,
        'additionalProperties': False,
        'properties': mode_props,
    }


#: The sub-fields that make a customization sub-object a style element.
#: Checked against every published schema: 68 blocks across 19 plugins match
#: exactly, and nothing else does -- favorite_result_colors, baseball's
#: bases/outs/player_card, jellyfin's progress_bar and the stocks blocks all
#: carry other fields and are correctly left alone.
_STYLE_BLOCK_FIELDS = frozenset(_STYLE_KEYS)


def _looks_like_style_block(block: Any) -> bool:
    """Whether a hand-written customization sub-object is a style element.

    Deliberately strict: every field must be one this system understands.
    A looser rule ("has at least one style field") would sweep in blocks
    like baseball's ``count``, which happens to carry a text_color next to
    geometry that means nothing here.
    """
    if not isinstance(block, dict):
        return False
    props = block.get('properties')
    if not isinstance(props, dict) or not props:
        return False
    return set(props) <= _STYLE_BLOCK_FIELDS


def _detect_style_blocks(customization: Dict[str, Any]) -> list:
    """Element keys in a hand-written customization block, in declared order."""
    props = customization.get('properties')
    if not isinstance(props, dict):
        return []
    return [key for key, block in props.items()
            if key not in ('layout', 'modes') and _looks_like_style_block(block)]


def _upgrade_font_property(block: Dict[str, Any]) -> None:
    """Point a hand-written font field at the font picker, in place.

    These fields ship a hardcoded ``enum`` -- football lists five of the
    thirty-five installed fonts -- which is why a font a user uploads can
    never appear in one. The enum is replaced rather than extended: it is
    not a curated safe set (it omits some twenty other faces that fit
    just as well), it is the fonts that happened to exist when it was
    written.

    The size ceiling the block already declares is carried across as
    ``maxFixedSize``, because a bitmap font ignores font_size and renders
    at its own baked-in size -- so widening the list without that would
    offer faces that overflow the panel no matter what size is set.
    """
    props = block.get('properties')
    if not isinstance(props, dict):
        return
    font_prop = props.get('font')
    if not isinstance(font_prop, dict):
        return

    font_prop.pop('enum', None)
    font_prop['x-widget'] = 'font-selector'

    size_prop = props.get('font_size')
    maximum = size_prop.get('maximum') if isinstance(size_prop, dict) else None
    if isinstance(maximum, (int, float)):
        options = font_prop.setdefault('x-options', {})
        if isinstance(options, dict):
            options.setdefault('maxFixedSize', maximum)


def _nullable_block(block: Dict[str, Any], title: Optional[str] = None,
                    description: Optional[str] = None) -> Dict[str, Any]:
    """A copy of an element block with every field optional.

    The per-mode counterpart of a hand-written block: same fields, all
    nullable and defaulting to null, which is this system's "inherit".
    """
    out = copy.deepcopy(block)
    out['properties'] = {k: _nullable(v)
                         for k, v in (out.get('properties') or {}).items()}
    out['x-style-managed'] = True
    if title:
        out['title'] = title
    if description is not None:
        out['description'] = description
    return out


def _modes_block_from_properties(props: Dict[str, Any], element_keys: list,
                                 modes: Any) -> Dict[str, Any]:
    """``customization.modes`` built from already-expanded element blocks.

    The compact declaration has ``_modes_block``; this is the same thing for
    a plugin that hand-wrote its blocks, so both forms get per-mode overrides
    from one declaration line.
    """
    layout_source = (props.get('layout') or {}).get('properties') or {}
    mode_props: Dict[str, Any] = {}
    for mode in modes:
        if not isinstance(mode, str) or not mode:
            continue
        element_props: Dict[str, Any] = {}
        layout_props: Dict[str, Any] = {}
        for key in element_keys:
            block = props.get(key)
            if isinstance(block, dict):
                element_props[key] = _nullable_block(
                    block,
                    description='Leave blank to use the settings above for '
                                'this mode.')
        # Every layout element, not just those with a style block. The two
        # namespaces do not line up in a hand-written schema -- football
        # styles 'score_text' but positions 'score', and positions logos,
        # timeouts and possession that have no style block at all. Keying
        # this off the style elements would have given six of its eleven
        # positionable things no per-mode offset.
        for key, layout_block in layout_source.items():
            if isinstance(layout_block, dict):
                layout_props[key] = _nullable_block(layout_block)
        if layout_props:
            element_props['layout'] = {
                'type': 'object',
                'title': 'Layout Offsets',
                'x-advanced': True,
                'additionalProperties': False,
                'properties': layout_props,
            }
        mode_props[mode] = {
            'type': 'object',
            'title': mode.replace('_', ' ').title(),
            'x-style-managed': True,
            'additionalProperties': False,
            'properties': element_props,
        }
    return {
        'type': 'object',
        'title': 'Per-Mode Overrides',
        'description': 'Override the settings above for one display mode. '
                       'Anything left blank follows the settings above.',
        'x-advanced': True,
        'additionalProperties': False,
        'properties': mode_props,
    }


def _adopt_handwritten_block(schema: Dict[str, Any],
                             customization: Dict[str, Any]) -> Dict[str, Any]:
    """Give a hand-written customization block the same treatment as a
    declared one, without the plugin rewriting its schema.

    Nineteen plugins spell their style elements out longhand -- football's
    block is 701 lines for seven elements -- and predate every part of this
    system. Recognising that shape lets them pick up the row-per-element
    editor and the real font picker on a core update, with no plugin
    release. What they do not get for free is per-mode overrides and the
    visible/align/scale fields, because core cannot invent a plugin's list
    of display modes: adding ``x-style-modes`` is the one line that unlocks
    the rest.
    """
    element_keys = _detect_style_blocks(customization)
    if not element_keys:
        return schema

    expanded = copy.deepcopy(schema)
    customization = expanded['properties']['customization']
    customization.setdefault('x-widget', 'style-editor')
    props = customization['properties']

    for key in element_keys:
        _upgrade_font_property(props[key])

    modes = customization.get('x-style-modes')
    if isinstance(modes, list) and modes:
        props.setdefault('modes',
                         _modes_block_from_properties(props, element_keys,
                                                      modes))

    # Stated explicitly because the config form serialises the schema with
    # Flask's JSON provider, which sorts keys -- without this the elements
    # reach the browser alphabetised.
    order = list(element_keys)
    order += [k for k in props if k not in order]
    customization.setdefault('x-propertyOrder', order)
    return expanded


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
    # Layout defaults live alongside the elements under the reserved
    # 'layout' key, mirroring the config shape, so one dict carries both.
    layout: Dict[str, Dict[str, Any]] = {}
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
                visible_spec = spec.get('visible')
                if visible_spec is True:
                    defaults['visible'] = True
                elif isinstance(visible_spec, dict) and 'default' in visible_spec:
                    defaults['visible'] = bool(visible_spec['default'])
                align_spec = spec.get('align')
                if isinstance(align_spec, dict) and 'default' in align_spec:
                    defaults['align'] = align_spec['default']
                scale_spec = spec.get('scale')
                if isinstance(scale_spec, dict) and 'default' in scale_spec:
                    layout.setdefault(element_key, {})['scale'] =                         scale_spec['default']
                elif scale_spec is True:
                    layout.setdefault(element_key, {})['scale'] = 1.0
                if defaults:
                    elements[element_key] = defaults

        properties = customization.get('properties')
        if isinstance(properties, dict):
            layout_block = properties.get('layout')
            if isinstance(layout_block, dict):
                for element_key, block in (
                        layout_block.get('properties') or {}).items():
                    if not isinstance(block, dict):
                        continue
                    scale_prop = (block.get('properties') or {}).get('scale')
                    if isinstance(scale_prop, dict) and 'default' in scale_prop:
                        layout.setdefault(element_key, {})['scale'] =                             scale_prop['default']
            for element_key, block in properties.items():
                if element_key in ('layout', 'modes') or element_key in elements:
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
    if layout:
        elements['layout'] = layout
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
    """An (r, g, b) tuple of ints in 0..255, or None for anything else.

    ``"#RRGGBB"`` is accepted as well as ``[r, g, b]``: the scoreboards'
    own colour readers have always taken both, and this is the function
    they now share.
    """
    if isinstance(value, str):
        text = value.strip()
        if len(text) == 7 and text.startswith('#'):
            try:
                return (int(text[1:3], 16), int(text[3:5], 16),
                        int(text[5:7], 16))
            except ValueError:
                return None
        return None
    if isinstance(value, (list, tuple)) and len(value) == 3:
        try:
            rgb = tuple(int(c) for c in value)
        except (TypeError, ValueError):
            return None
        if all(0 <= c <= 255 for c in rgb):
            return rgb  # type: ignore[return-value]
    return None


#: Element names that drifted between plugins, beyond what the ``_text``
#: suffix rule below covers. Counted across the published schemas: the
#: layout block spells it ``records`` in seven plugins and ``record`` in
#: two, ``status_text`` in seven and ``status`` in two.
_ELEMENT_ALIASES: Dict[str, Tuple[str, ...]] = {
    'records': ('record',),
    'record': ('records',),
    'rank_text': ('ranking', 'rank'),
    'ranking': ('rank_text', 'rank'),
    'team_name': ('team',),
    'team': ('team_name',),
}


def alias_keys(element_key: str) -> Tuple[str, ...]:
    """The names one element may be stored under, exact match first.

    Two conventions collided as the scoreboards grew. The style block names
    elements with a ``_text`` suffix (``score_text``, ``status_text``) while
    the layout block mostly uses the bare noun (``score``, ``date``,
    ``odds``) -- except ``status_text``, which kept the suffix in seven
    plugins and lost it in two. Plugins also disagree on ``records`` vs
    ``record``.

    Rather than make every plugin rename its config keys -- which would
    orphan whatever offsets its users had already dialled in -- a lookup
    tries the exact name first and then the spellings that mean the same
    thing. Exact-first is what keeps this from changing any behaviour for a
    config that already matches.

    This also covers the compact declaration form, which uses one key for
    both blocks: a plugin moving to it can still find offsets its users
    saved under the old bare-noun layout key.
    """
    if not isinstance(element_key, str) or not element_key:
        return ()
    explicit = _ELEMENT_ALIASES.get(element_key)
    if explicit:
        # An explicit entry replaces the suffix rule rather than adding to
        # it, so 'records' does not also generate 'records_text'.
        return (element_key,) + tuple(a for a in explicit if a != element_key)

    if element_key.endswith('_text'):
        stem = element_key[:-len('_text')]
        return (element_key, stem) if stem else (element_key,)
    return (element_key, element_key + '_text')


def _lookup_element(block: Any, element_key: str) -> Dict[str, Any]:
    """``block[element]`` under any of its names, or {}."""
    if not isinstance(block, dict):
        return {}
    for key in alias_keys(element_key):
        value = block.get(key)
        if isinstance(value, dict):
            return value
    return {}


def _coerce_bool(value: Any, default: bool) -> bool:
    """A real bool, or ``default``. Accepts the strings a form may post."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ('true', 'yes', 'on', '1'):
            return True
        if lowered in ('false', 'no', 'off', '0'):
            return False
    return default


_ALIGNMENTS = ('left', 'center', 'right')


def _coerce_align(value: Any) -> Optional[str]:
    """One of left/center/right, or None for anything else.

    None means "no preference", which is what an unset value resolves to --
    the caller keeps whatever alignment it already did.
    """
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in _ALIGNMENTS:
            return lowered
        if lowered in ('centre', 'middle'):   # the spelling users try
            return 'center'
    return None


def _coerce_scale(value: Any, default: float) -> float:
    """A positive size multiplier, or ``default``.

    Clamped rather than merely validated: a scale of 0 or a negative one is
    a zero-or-inverted image, and the panel is 32 pixels tall -- a typo
    should cost a wrong size, not a crash inside PIL.
    """
    if isinstance(value, bool) or value is None:
        return default
    try:
        scale = float(value)
    except (TypeError, ValueError):
        return default
    if scale <= 0:
        return default
    return min(scale, 10.0)


def _coerce_offset(value: Any, default: int, element_key: str,
                   axis: str) -> int:
    """A pixel offset as an int; anything nonsensical is ``default``.

    A bool degrades rather than counting as 1/0 -- the more correct reading
    of a pixel offset, and what the shared resolver has always done
    relative to the classic inline read. Non-finite floats degrade too:
    the scroll-card reader guarded against those explicitly and this is now
    the one implementation.
    """
    if isinstance(value, bool):
        return int(default)
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            return int(default)
        return int(value)
    if isinstance(value, str):
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            logger.warning("Invalid layout offset for %s.%s: %r, using %s",
                           element_key, axis, value, default)
            return int(default)
        if not math.isfinite(parsed):
            return int(default)
        return int(parsed)
    return int(default)


def layout_offset(config: Any, element_key: str, axis: str,
                  default: int = 0, mode: Optional[str] = None) -> int:
    """One ``customization.layout.<element>.<axis>`` value, as an int.

    The stateless form of :meth:`ElementStyleResolver.offset_value`, for the
    scoreboard helpers that are handed a config rather than holding one.
    Both go through here, so the alias handling and the per-mode lookup
    cannot drift between them -- there were three separate readers of this
    block before, and the scroll-card one had already been found ignoring
    offsets the schema advertised.
    """
    try:
        block = config.get('customization') if isinstance(config, dict) else None
        block = block if isinstance(block, dict) else {}
        base = _lookup_element(block.get('layout'), element_key).get(axis)
        base_value = (int(default) if base is None
                      else _coerce_offset(base, default, element_key, axis))

        if mode:
            modes = block.get('modes')
            mode_block = modes.get(mode) if isinstance(modes, dict) else None
            if isinstance(mode_block, dict):
                override = _lookup_element(mode_block.get('layout'),
                                           element_key).get(axis)
                if override is not None:
                    return _coerce_offset(override, base_value,
                                          element_key, axis)
        return base_value
    except Exception as e:
        logger.warning("Error reading layout offset %s.%s: %s",
                       element_key, axis, e)
        try:
            return int(default)
        except (TypeError, ValueError):
            return 0


def element_color(config: Any, element_key: str,
                  default: Tuple[int, int, int] = (255, 255, 255),
                  mode: Optional[str] = None) -> Tuple[int, int, int]:
    """``customization.<element>.text_color``, or ``default``.

    The stateless colour lookup the scoreboards share. Unlike
    :meth:`ElementStyleResolver.style` this does not compare against a
    schema default -- the callers have no schema to hand -- so any
    configured colour counts, which is what their own readers did.
    """
    try:
        block = config.get('customization') if isinstance(config, dict) else None
        block = block if isinstance(block, dict) else {}
        if mode:
            modes = block.get('modes')
            mode_block = modes.get(mode) if isinstance(modes, dict) else None
            if isinstance(mode_block, dict):
                override = _normalize_color(
                    _lookup_element(mode_block, element_key).get('text_color'))
                if override is not None:
                    return override
        value = _normalize_color(
            _lookup_element(block, element_key).get('text_color'))
        return value if value is not None else default
    except Exception as e:
        logger.warning("Error reading colour for %s: %s", element_key, e)
        return default


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

    **Modes.** A plugin that displays the same element in more than one
    situation — a scoreboard's live / upcoming / recent cards, weather's
    current / hourly / daily screens — can let the user style each one
    separately under ``customization.modes.<mode>``. The mode is normally
    bound once at construction rather than passed per call, because the
    natural owner already knows it: SportsUpcoming and SportsRecent are
    distinct instances with distinct ``SKIN_MODE`` values, so binding here
    makes every existing call site mode-aware without touching one of them.

    A mode layer is pure override. Its fields default to ``None``, which
    means *inherit*, and any non-None value wins over the base element.
    That is why ``None`` and a real value must stay distinguishable: a mode
    offset of ``0`` means "sit at the base position", not "no preference"
    — the same distinction ``scroll_card.switch_*`` draws with its
    ``"inherit"`` sentinel.
    """

    def __init__(self, config: Optional[Dict[str, Any]],
                 defaults: Optional[Dict[str, Any]] = None,
                 mode: Optional[str] = None):
        # Keep the exact object for identity-based invalidation, even if the
        # caller hands us something odd; reads are guarded.
        self._config = config
        if isinstance(defaults, dict):
            element_defaults = defaults.get('customization', {})
        else:
            element_defaults = {}
        self._defaults: Dict[str, Any] = (
            element_defaults if isinstance(element_defaults, dict) else {})
        self._mode = mode if isinstance(mode, str) and mode else None
        self._memo: Dict[Any, ElementStyle] = {}

    @property
    def mode(self) -> Optional[str]:
        """The mode bound at construction, if any."""
        return self._mode

    # -- internal accessors -------------------------------------------------

    def _customization(self) -> Dict[str, Any]:
        config = self._config if isinstance(self._config, dict) else {}
        customization = config.get('customization', {})
        return customization if isinstance(customization, dict) else {}

    def _element_config(self, element_key: str) -> Dict[str, Any]:
        return _lookup_element(self._customization(), element_key)

    def _element_defaults(self, element_key: str) -> Dict[str, Any]:
        return _lookup_element(self._defaults, element_key)

    def _mode_block(self, mode: Optional[str]) -> Dict[str, Any]:
        """``customization.modes.<mode>``, or {} when there is no such block."""
        if not mode:
            return {}
        modes = self._customization().get('modes', {})
        if not isinstance(modes, dict):
            return {}
        block = modes.get(mode, {})
        return block if isinstance(block, dict) else {}

    def _mode_element_config(self, element_key: str,
                             mode: Optional[str]) -> Dict[str, Any]:
        return _lookup_element(self._mode_block(mode), element_key)

    def _effective_mode(self, mode: Any) -> Optional[str]:
        """A per-call mode overrides the bound one; anything else uses it."""
        if isinstance(mode, str) and mode:
            return mode
        return self._mode

    # -- public API ---------------------------------------------------------

    def style(self, element_key: str,
              classic_font: str = _FALLBACK_FONT_NAME,
              classic_size: int = 8,
              classic_color: Optional[Tuple[int, int, int]] = None,
              mode: Optional[str] = None) -> ElementStyle:
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
            mode: Overrides the mode bound at construction for this call.
                Rarely needed — a host that renders one mode should bind it
                once instead.

        Returns:
            ElementStyle with the loaded font face, RGB color, (x, y)
            offset, and the ``user_forced`` / ``user_forced_color`` flags.
        """
        effective_mode = self._effective_mode(mode)
        try:
            memo_key = (element_key, classic_font, classic_size,
                        _normalize_color(classic_color) or classic_color,
                        effective_mode)
            memoized = self._memo.get(memo_key)
            if memoized is not None:
                return memoized
        except Exception:
            memo_key = None

        try:
            resolved = self._resolve(element_key, classic_font,
                                     classic_size, classic_color,
                                     effective_mode)
        except Exception as e:
            logger.warning("Error resolving style for element '%s': %s — "
                           "using classic style", element_key, e)
            resolved = self._classic_style(classic_font, classic_size,
                                           classic_color)
        if memo_key is not None:
            self._memo[memo_key] = resolved
        return resolved

    def offset(self, element_key: str,
               mode: Optional[str] = None) -> Tuple[int, int]:
        """The user's ``customization.layout.<element>`` (x, y) pixel
        offset, defaulting to (0, 0). Never raises."""
        return (self.offset_value(element_key, 'x_offset', 0, mode),
                self.offset_value(element_key, 'y_offset', 0, mode))

    def offset_value(self, element_key: str, axis: str, default: int = 0,
                     mode: Optional[str] = None) -> int:
        """One ``customization.layout.<element>.<axis>`` value as an int.

        ``axis`` is usually ``'x_offset'`` / ``'y_offset'`` but any key is
        honored (e.g. the scoreboards' ``'away_x_offset'``). Numeric
        strings are coerced; anything else degrades to ``default``. Never
        raises.

        When a mode is in play, ``customization.modes.<mode>.layout`` is
        consulted first and wins if it carries a non-None value for this
        axis — ``None`` there means inherit the base offset, which is what
        lets a mode nudge one element without restating the rest.
        """
        return layout_offset(self._config, element_key, axis, default,
                             self._effective_mode(mode))

    @staticmethod
    def _layout_element(block: Dict[str, Any],
                        element_key: str) -> Dict[str, Any]:
        """``block['layout'][element]``, or {} if absent anywhere.

        The layout block is where the naming drift lives, so the lookup
        goes through the aliases: a plugin asking for ``score_text``
        offsets still finds the ``score`` its users configured.
        """
        if not isinstance(block, dict):
            return {}
        return _lookup_element(block.get('layout'), element_key)

    @classmethod
    def _layout_axis(cls, block: Dict[str, Any], element_key: str,
                     axis: str) -> Any:
        """``block['layout'][element][axis]``, or None if absent anywhere."""
        return cls._layout_element(block, element_key).get(axis)


    # -- resolution internals -----------------------------------------------

    def _forced(self, element_config: Dict[str, Any],
                element_defaults: Dict[str, Any],
                mode_config: Dict[str, Any], key: str) -> Any:
        """A field's value if the user genuinely chose one, else None.

        Same two-layer rule the font/size/colour resolution uses: a mode
        value counts whenever it is set, a base value only when it differs
        from the schema default (the save flow writes that default in
        whether or not the user touched it). Returning None for "not
        chosen" lets the caller substitute a neutral value, which is how
        an untouched config keeps rendering exactly as before.
        """
        mode_value = mode_config.get(key)
        if mode_value is not None:
            return mode_value
        configured = element_config.get(key)
        if configured is None:
            return None
        if key in element_defaults and configured == element_defaults[key]:
            return None
        return configured


    def _resolve(self, element_key: str, classic_font: str,
                 classic_size: int,
                 classic_color: Optional[Tuple[int, int, int]],
                 mode: Optional[str] = None) -> ElementStyle:
        element_config = self._element_config(element_key)
        element_defaults = self._element_defaults(element_key)
        mode_config = self._mode_element_config(element_key, mode)

        # The two layers answer different questions. The base layer asks
        # "does this differ from the schema default?", because the save flow
        # writes the full default object into config.json whether or not the
        # user touched it. The mode layer asks only "is it set?", because its
        # schema default is None -- there is nothing for a stray write to
        # make look deliberate.

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

        # Mode overrides sit on top of whatever the base layer settled on.
        mode_font = mode_config.get('font')
        if isinstance(mode_font, str) and mode_font:
            font_name, font_forced = mode_font, True
        mode_size = self._coerce_size(mode_config.get('font_size'), None)
        if mode_size is not None:
            font_size, size_forced = mode_size, True

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

        mode_color = _normalize_color(mode_config.get('text_color'))
        if mode_color is not None:
            color, color_forced = mode_color, True

        visible = self._forced(element_config, element_defaults,
                               mode_config, 'visible')
        align = self._forced(element_config, element_defaults,
                             mode_config, 'align')
        # scale is geometry, so it lives with the offsets rather than in the
        # element block -- a logo has a scale and no font.
        layout_defaults = self._defaults.get('layout', {})
        scale = self._forced(
            self._layout_element(self._customization(), element_key),
            layout_defaults.get(element_key, {})
            if isinstance(layout_defaults, dict) else {},
            self._layout_element(self._mode_block(mode), element_key),
            'scale')

        # font_size reports what was actually realised, which differs from
        # the request only when a BDF snapped to its native strike. Callers
        # lay out from this value; reporting the request would reserve space
        # for a size nothing was drawn at.
        font, realised_size = _load_font_sized(font_name, font_size)
        return ElementStyle(
            font=font,
            color=color,
            offset=self.offset(element_key, mode),
            font_name=font_name,
            font_size=realised_size,
            user_forced=user_forced,
            user_forced_color=bool(color_forced),
            visible=_coerce_bool(visible, True),
            align=_coerce_align(align),
            scale=_coerce_scale(scale, 1.0),
        )

    def _classic_style(self, classic_font: str, classic_size: int,
                       classic_color: Optional[Tuple[int, int, int]]) -> ElementStyle:
        """The untouched fallback style — used when resolution itself
        fails, so ``style()`` can keep its never-raises promise."""
        size = self._coerce_size(classic_size, 8)
        font, realised_size = _load_font_sized(classic_font, size)
        return ElementStyle(
            font=font,
            color=_normalize_color(classic_color) or (255, 255, 255),
            offset=(0, 0),
            font_name=classic_font,
            font_size=realised_size,
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
