"""
Plugin Composer blueprint — drag-and-drop plugin builder for LEDMatrix.

Routes:
  GET  /composer/            — Composer page
  POST /composer/api/generate — Generate and return plugin ZIP
  POST /composer/api/install  — Write plugin directly to plugins_dir
  GET  /composer/api/fonts/<name> — Serve TTF font files for canvas rendering
  GET  /composer/api/validate-id/<id> — Check if a plugin ID is already taken
"""
import ast
import io
import json
import logging
import re
import zipfile
from datetime import datetime
from pathlib import Path

import jinja2
import jsonschema
from flask import Blueprint, jsonify, render_template, request, send_file

logger = logging.getLogger(__name__)

composer_bp = Blueprint('composer', __name__)

# Module-level attributes injected by app.py at registration time
composer_bp.config_manager = None
composer_bp.plugin_manager = None
composer_bp.plugins_dir = None
composer_bp.project_root = None

# Fonts safe to serve to the browser for canvas rendering
_ALLOWED_FONTS = frozenset({'PressStart2P-Regular.ttf', '4x6-font.ttf', '5by7.regular.ttf'})

# Map composer font keys → DisplayManager attribute names
_FONT_ATTR_MAP = {
    'press_start': 'regular_font',
    'four_by_six': 'extra_small_font',
    'five_by_seven': 'bdf_5x7_font',
}

# Font sizes in LED pixels (used to compute second-line Y offsets)
_FONT_SIZE_MAP = {
    'press_start': 8,
    'four_by_six': 6,
    'five_by_seven': 7,
}

_PLUGIN_ID_RE = re.compile(r'^[a-z][a-z0-9-]{0,62}$')
_PYTHON_IDENT_RE = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_]*$')

# ── Jinja2 environment (separate from Flask's; autoescape=False for code gen) ──

_jinja_env: jinja2.Environment | None = None


def _get_jinja_env() -> jinja2.Environment:
    global _jinja_env
    if _jinja_env is None:
        template_dir = Path(__file__).parent.parent / 'templates' / 'v3' / 'composer'
        _jinja_env = jinja2.Environment(
            loader=jinja2.FileSystemLoader(str(template_dir)),
            autoescape=False,
            trim_blocks=True,
            lstrip_blocks=True,
        )
        _jinja_env.filters['as_rgb'] = _as_rgb_filter
        _jinja_env.filters['as_fill'] = _as_fill_filter
    return _jinja_env


def _as_rgb_filter(val) -> str:
    """[r, g, b] → '(r, g, b)'"""
    if val is None:
        return 'None'
    return f'({int(val[0])}, {int(val[1])}, {int(val[2])})'


def _as_fill_filter(val) -> str:
    """[r, g, b] or None → '(r, g, b)' or 'None'"""
    if val is None:
        return 'None'
    return _as_rgb_filter(val)


# ── Helper functions ──────────────────────────────────────────────────────────

def _to_class_name(name: str) -> str:
    """'My Clock' → 'MyClockPlugin' (avoids double-suffix if name already ends with Plugin)"""
    words = re.sub(r'[^a-zA-Z0-9]', ' ', name).split()
    base = ''.join(w.capitalize() for w in words)
    return base if base.endswith('Plugin') else base + 'Plugin'


def _compute_pos_expr(val: int, anchor: str | None, dim_var: str) -> str:
    """Produce a Python expression string for an anchored or fixed position.

    anchor=None/'left'/'top' → fixed pixel value
    anchor='center'          → dim_var // 2 ± offset
    anchor='right'/'bottom'  → dim_var - offset
    """
    if not anchor or anchor in ('left', 'top'):
        return str(val)
    if anchor in ('center', 'middle'):
        if val == 0:
            return f"{dim_var} // 2"
        return f"{dim_var} // 2 + {val}" if val > 0 else f"{dim_var} // 2 - {abs(val)}"
    if anchor in ('right', 'bottom'):
        return dim_var if val == 0 else f"{dim_var} - {val}"
    return str(val)


# Character widths in LED pixels per font (for text-alignment x offset math)
_FONT_CHAR_W = {
    'press_start': 8,
    'four_by_six': 4,
    'five_by_seven': 5,
}


def _aligned_x_expr(x_base_expr: str, text_align: str, char_count: int, char_w: int) -> str:
    """Return Python x expression for text alignment.

    left   → x_base_expr (no change)
    center → x_base_expr - half_text_width
    right  → x_base_expr - text_width
    """
    if text_align == 'left' or not text_align:
        return x_base_expr
    text_px = char_count * char_w
    if text_align == 'center':
        offset = text_px // 2
        return f"({x_base_expr}) - {offset}" if offset else x_base_expr
    if text_align == 'right':
        return f"({x_base_expr}) - {text_px}" if text_px else x_base_expr
    return x_base_expr


def _preprocess_elements(elements: list) -> list:
    """Expand raw element dicts into template-ready dicts with anchor expressions.

    Invisible elements (visible=False) are excluded from generated code entirely.
    """
    result = []
    for el in elements:
        # Skip hidden elements — they exist only in the preview
        if el.get('visible') is False:
            continue

        p = dict(el)
        t = el.get('type', '')

        # Section elements are layer-list annotations only — no canvas output
        if t == 'section':
            continue

        x_anchor = el.get('xAnchor') or None
        y_anchor = el.get('yAnchor') or None
        p['min_width'] = int(el.get('minWidth', 0) or 0)

        if t in ('text', 'clock'):
            font_key = el.get('font', 'press_start')
            p['font_attr'] = _FONT_ATTR_MAP.get(font_key, 'regular_font')
            p['rgb_tuple'] = f"({el.get('r', 255)}, {el.get('g', 255)}, {el.get('b', 255)})"
            text_align = el.get('textAlign', 'left')
            raw_x = el.get('x', 0)
            x_base_expr = _compute_pos_expr(raw_x, x_anchor, 'width')
            p['y_expr'] = _compute_pos_expr(el.get('y', 0), y_anchor, 'height')
            font_size = _FONT_SIZE_MAP.get(font_key, 8)
            char_w = _FONT_CHAR_W.get(font_key, 8)
            line_spacing = int(el.get('lineSpacing', 2))
            y_expr = p['y_expr']
            p['y2_expr'] = f"({y_expr}) + {font_size + line_spacing}"
            if t == 'text':
                t1 = el.get('text', '') or ''
                t2 = el.get('text2', '') or ''
                p['text2'] = t2
                # Detect {variable} tokens — generate format_map() call instead of literal
                _var_re = re.compile(r'\{([a-zA-Z_]\w*)\}')
                p['text_is_template'] = bool(_var_re.search(t1) or _var_re.search(t2))
                ref_len = max(len(t1), len(t2)) if t2 else len(t1)
                p['x_expr'] = _aligned_x_expr(x_base_expr, text_align, ref_len, char_w)
                p['x2_expr'] = p['x_expr']  # second line uses same x
            else:  # clock
                fmt1 = el.get('format', '%H:%M') or '%H:%M'
                fmt2 = el.get('format2', '') or ''
                p['format2'] = fmt2
                ref_len = max(len(fmt1), len(fmt2)) if fmt2 else len(fmt1)
                p['x_expr'] = _aligned_x_expr(x_base_expr, text_align, ref_len, char_w)
                p['x2_expr'] = p['x_expr']
            p['blink'] = bool(el.get('blink', False))

        elif t == 'dynamic_text':
            binding = el.get('binding', {})
            p['binding_source'] = binding.get('source', 'config')
            p['binding_key'] = binding.get('key', '')
            p['binding_format'] = binding.get('format')
            font_key = el.get('font', 'press_start')
            p['font_attr'] = _FONT_ATTR_MAP.get(font_key, 'regular_font')
            p['rgb_tuple'] = f"({el.get('r', 255)}, {el.get('g', 200)}, {el.get('b', 100)})"
            x_base_expr = _compute_pos_expr(el.get('x', 0), x_anchor, 'width')
            p['x_expr'] = x_base_expr  # dynamic text: runtime content determines width; use raw pos
            p['y_expr'] = _compute_pos_expr(el.get('y', 0), y_anchor, 'height')
            p['blink'] = bool(el.get('blink', False))

        elif t == 'rectangle':
            x_expr = _compute_pos_expr(el.get('x', 0), x_anchor, 'width')
            y_expr = _compute_pos_expr(el.get('y', 0), y_anchor, 'height')
            w = el.get('width', 10)
            h = el.get('height', 8)
            p['x_expr'] = x_expr
            p['y_expr'] = y_expr
            # x2/y2 as runtime expressions to support anchored positions
            p['x2_expr'] = f"({x_expr}) + {w}"
            p['y2_expr'] = f"({y_expr}) + {h}"
            fill = (
                [el.get('fillR', 0), el.get('fillG', 0), el.get('fillB', 128)]
                if el.get('hasFill', True) else None
            )
            outline = (
                [el.get('outR', 255), el.get('outG', 255), el.get('outB', 255)]
                if el.get('hasOutline', True) else None
            )
            p['fill_tuple'] = _as_fill_filter(fill)
            p['outline_tuple'] = _as_fill_filter(outline)
            p['blink'] = bool(el.get('blink', False))

        elif t in ('line', 'divider'):
            if t == 'divider':
                orient = el.get('orientation', 'horizontal')
                if orient == 'horizontal':
                    y_val = el.get('y', 16)
                    y_expr = _compute_pos_expr(y_val, y_anchor, 'height')
                    p.update(x0_expr='0', y0_expr=y_expr, x1_expr='width - 1', y1_expr=y_expr)
                else:
                    x_val = el.get('x', 64)
                    x_expr = _compute_pos_expr(x_val, x_anchor, 'width')
                    p.update(x0_expr=x_expr, y0_expr='0', x1_expr=x_expr, y1_expr='height - 1')
            else:
                p['x0_expr'] = _compute_pos_expr(el.get('x0', 0), x_anchor, 'width')
                p['y0_expr'] = _compute_pos_expr(el.get('y0', 0), y_anchor, 'height')
                p['x1_expr'] = str(el.get('x1', 127))
                p['y1_expr'] = str(el.get('y1', 0))
            p['rgb_tuple'] = f"({el.get('r', 180)}, {el.get('g', 180)}, {el.get('b', 180)})"
            p['line_width'] = el.get('lineWidth', 1)
            p['blink'] = bool(el.get('blink', False))

        elif t == 'progress_bar':
            p['x_expr'] = _compute_pos_expr(el.get('x', 0), x_anchor, 'width')
            p['y_expr'] = _compute_pos_expr(el.get('y', 0), y_anchor, 'height')
            p['bar_width'] = int(el.get('barWidth', 40))
            p['bar_height'] = int(el.get('barHeight', 6))
            binding = el.get('binding', {})
            p['binding_key'] = binding.get('key', '')
            p['fill_tuple'] = f"({el.get('r', 100)}, {el.get('g', 200)}, {el.get('b', 100)})"
            bg = (
                [el.get('bgR', 30), el.get('bgG', 30), el.get('bgB', 30)]
                if el.get('hasBg', True) else None
            )
            outline = (
                [el.get('outR', 100), el.get('outG', 100), el.get('outB', 100)]
                if el.get('hasOutline', True) else None
            )
            p['bg_tuple'] = _as_fill_filter(bg)
            p['outline_tuple'] = _as_fill_filter(outline)
            p['blink'] = bool(el.get('blink', False))

        elif t == 'arc':
            x_expr = _compute_pos_expr(el.get('x', 0), x_anchor, 'width')
            y_expr = _compute_pos_expr(el.get('y', 0), y_anchor, 'height')
            w = el.get('width', 24)
            h = el.get('height', 24)
            p['x_expr'] = x_expr
            p['y_expr'] = y_expr
            p['x2_expr'] = f"({x_expr}) + {w}"
            p['y2_expr'] = f"({y_expr}) + {h}"
            p['start_angle'] = int(el.get('startAngle', 0))
            p['end_angle'] = int(el.get('endAngle', 270))
            p['line_width'] = max(1, int(el.get('lineWidth', 2)))
            p['rgb_tuple'] = f"({el.get('r', 255)}, {el.get('g', 200)}, {el.get('b', 0)})"
            p['blink'] = bool(el.get('blink', False))

        elif t == 'ellipse':
            x_expr = _compute_pos_expr(el.get('x', 0), x_anchor, 'width')
            y_expr = _compute_pos_expr(el.get('y', 0), y_anchor, 'height')
            w = el.get('width', 24)
            h = el.get('height', 12)
            p['x_expr'] = x_expr
            p['y_expr'] = y_expr
            p['x2_expr'] = f"({x_expr}) + {w}"
            p['y2_expr'] = f"({y_expr}) + {h}"
            fill = (
                [el.get('fillR', 0), el.get('fillG', 100), el.get('fillB', 200)]
                if el.get('hasFill', True) else None
            )
            outline = (
                [el.get('outR', 100), el.get('outG', 180), el.get('outB', 255)]
                if el.get('hasOutline', True) else None
            )
            p['fill_tuple'] = _as_fill_filter(fill)
            p['outline_tuple'] = _as_fill_filter(outline)
            p['blink'] = bool(el.get('blink', False))

        elif t == 'pixel':
            p['x_expr'] = _compute_pos_expr(el.get('x', 0), x_anchor, 'width')
            p['y_expr'] = _compute_pos_expr(el.get('y', 0), y_anchor, 'height')
            p['rgb_tuple'] = f"({el.get('r', 255)}, {el.get('g', 255)}, {el.get('b', 255)})"
            p['blink'] = bool(el.get('blink', False))

        elif t == 'rounded_rectangle':
            x_expr = _compute_pos_expr(el.get('x', 0), x_anchor, 'width')
            y_expr = _compute_pos_expr(el.get('y', 0), y_anchor, 'height')
            w = el.get('width', 24)
            h = el.get('height', 10)
            p['x_expr'] = x_expr
            p['y_expr'] = y_expr
            p['x2_expr'] = f"({x_expr}) + {w}"
            p['y2_expr'] = f"({y_expr}) + {h}"
            p['border_radius'] = int(el.get('borderRadius', 3))
            fill = (
                [el.get('fillR', 0), el.get('fillG', 80), el.get('fillB', 180)]
                if el.get('hasFill', True) else None
            )
            outline = (
                [el.get('outR', 120), el.get('outG', 180), el.get('outB', 255)]
                if el.get('hasOutline', True) else None
            )
            p['fill_tuple'] = _as_fill_filter(fill)
            p['outline_tuple'] = _as_fill_filter(outline)
            p['blink'] = bool(el.get('blink', False))

        elif t == 'countdown':
            font_key = el.get('font', 'four_by_six')
            p['font_attr'] = _FONT_ATTR_MAP.get(font_key, 'extra_small_font')
            p['rgb_tuple'] = f"({el.get('r', 255)}, {el.get('g', 180)}, {el.get('b', 0)})"
            binding = el.get('binding', {})
            p['binding_key'] = binding.get('key', '')
            p['countdown_format'] = el.get('countdownFormat', 'dh')
            x_base_expr = _compute_pos_expr(el.get('x', 0), x_anchor, 'width')
            p['x_expr'] = x_base_expr
            p['y_expr'] = _compute_pos_expr(el.get('y', 0), y_anchor, 'height')
            p['blink'] = bool(el.get('blink', False))

        elif t == 'pips':
            p['x_expr'] = _compute_pos_expr(el.get('x', 0), x_anchor, 'width')
            p['y_expr'] = _compute_pos_expr(el.get('y', 0), y_anchor, 'height')
            p['pip_count'] = max(1, int(el.get('count', 5)))
            p['pip_size'] = max(1, int(el.get('pipSize', 4)))
            p['pip_spacing'] = max(0, int(el.get('pipSpacing', 2)))
            p['show_empty'] = bool(el.get('showEmpty', True))
            binding = el.get('binding', {})
            p['binding_key'] = binding.get('key', '')
            p['fill_tuple'] = f"({el.get('r', 255)}, {el.get('g', 200)}, {el.get('b', 0)})"
            p['empty_tuple'] = f"({el.get('emptyR', 50)}, {el.get('emptyG', 50)}, {el.get('emptyB', 50)})"
            p['blink'] = bool(el.get('blink', False))

        elif t == 'sparkline':
            x_expr = _compute_pos_expr(el.get('x', 0), x_anchor, 'width')
            y_expr = _compute_pos_expr(el.get('y', 0), y_anchor, 'height')
            p['x_expr'] = x_expr
            p['y_expr'] = y_expr
            p['bar_width_px'] = int(el.get('width', 40))
            p['bar_height_px'] = int(el.get('height', 12))
            p['bar_count'] = max(1, int(el.get('barCount', 8)))
            p['bar_spacing'] = max(0, int(el.get('barSpacing', 1)))
            binding = el.get('binding', {})
            p['binding_key'] = binding.get('key', '')
            p['fill_tuple'] = f"({el.get('r', 80)}, {el.get('g', 200)}, {el.get('b', 120)})"
            bg = [el.get('bgR', 30), el.get('bgG', 30), el.get('bgB', 30)] if el.get('hasBg', False) else None
            p['bg_tuple'] = _as_fill_filter(bg)
            p['blink'] = bool(el.get('blink', False))

        elif t == 'gauge':
            x_expr = _compute_pos_expr(el.get('x', 0), x_anchor, 'width')
            y_expr = _compute_pos_expr(el.get('y', 0), y_anchor, 'height')
            w = el.get('width', 32)
            h = el.get('height', 32)
            p['x_expr'] = x_expr
            p['y_expr'] = y_expr
            p['x2_expr'] = f"({x_expr}) + {w}"
            p['y2_expr'] = f"({y_expr}) + {h}"
            p['start_angle'] = int(el.get('startAngle', 135))
            p['end_angle'] = int(el.get('endAngle', 45))
            p['line_width'] = max(1, int(el.get('lineWidth', 3)))
            p['rgb_tuple'] = f"({el.get('r', 80)}, {el.get('g', 220)}, {el.get('b', 80)})"
            track = (
                [el.get('trackR', 40), el.get('trackG', 40), el.get('trackB', 40)]
                if el.get('hasTrack', True) else None
            )
            p['track_tuple'] = _as_fill_filter(track)
            binding = el.get('binding', {})
            p['binding_key'] = binding.get('key', '')
            font_key = el.get('font', 'four_by_six')
            p['font_attr'] = _FONT_ATTR_MAP.get(font_key, 'extra_small_font')
            p['show_label'] = bool(el.get('showLabel', True))
            p['label_tuple'] = f"({el.get('labelR', 200)}, {el.get('labelG', 200)}, {el.get('labelB', 200)})"
            p['blink'] = bool(el.get('blink', False))

        elif t == 'marquee':
            font_key = el.get('font', 'press_start')
            p['font_attr'] = _FONT_ATTR_MAP.get(font_key, 'regular_font')
            p['rgb_tuple'] = f"({el.get('r', 255)}, {el.get('g', 255)}, {el.get('b', 255)})"
            p['y_expr'] = _compute_pos_expr(el.get('y', 0), y_anchor, 'height')
            p['text'] = el.get('text', 'Scrolling text')
            p['char_w'] = _FONT_CHAR_W.get(font_key, 8)
            p['gap'] = int(el.get('gap', 16))
            p['scroll_speed'] = max(1, int(el.get('scrollSpeed', 1)))
            p['direction'] = el.get('direction', 'left')
            # Data key stored in self._data for stateful scrolling across display() calls
            raw_id = str(el.get('id', 0)).replace('-', '_')
            p['data_key'] = f"mq_{raw_id}"
            p['blink'] = bool(el.get('blink', False))

        result.append(p)
    return result


def _generate_plugin_files(data: dict) -> dict:
    """
    Generate all plugin file contents as strings.

    Returns dict: {'manager.py', 'manifest.json', 'config_schema.json', 'requirements.txt'}
    Raises ValueError with a human-readable message on any validation failure.
    """
    metadata = data.get('metadata', {})
    elements = data.get('elements', [])
    data_model = data.get('dataModel', {})
    config_vars = data_model.get('configVars', [])

    plugin_id = metadata.get('id', '').strip()
    if not _PLUGIN_ID_RE.match(plugin_id):
        raise ValueError(
            'Plugin ID must start with a lowercase letter and contain only '
            'lowercase letters, numbers, and hyphens (max 63 chars).'
        )

    plugin_name = metadata.get('name', '').strip()
    if not plugin_name:
        raise ValueError('Plugin name is required.')

    author = metadata.get('author', '').strip()
    if not author:
        raise ValueError('Author is required.')

    version = metadata.get('version', '1.0.0').strip()

    # Validate config var keys are valid Python identifiers
    for cv in config_vars:
        key = cv.get('key', '')
        if not _PYTHON_IDENT_RE.match(key):
            raise ValueError(f'Config variable key "{key}" is not a valid Python identifier.')

    class_name = _to_class_name(plugin_name)
    # Only consider visible elements for code generation flags
    visible_elements = [e for e in elements if e.get('visible') is not False]
    processed = _preprocess_elements(elements)
    has_clock = any(e.get('type') == 'clock' for e in visible_elements)
    has_blink = any(e.get('blink') for e in visible_elements)
    has_countdown = any(e.get('type') == 'countdown' for e in visible_elements)
    _var_re = re.compile(r'\{[a-zA-Z_]\w*\}')
    has_text_template = any(
        e.get('type') == 'text' and (
            _var_re.search(e.get('text', '') or '') or
            _var_re.search(e.get('text2', '') or '')
        )
        for e in visible_elements
    )

    # Background fill color (None → don't render, use LED panel's native black)
    bg_color: str | None = None
    bg_raw = metadata.get('bgColor')
    if isinstance(bg_raw, dict):
        r, g, b = int(bg_raw.get('r', 0)), int(bg_raw.get('g', 0)), int(bg_raw.get('b', 0))
        if r or g or b:
            bg_color = f'({r}, {g}, {b})'

    # Render manager.py
    env = _get_jinja_env()
    try:
        tmpl = env.get_template('manager.py.j2')
    except jinja2.TemplateNotFound:
        raise ValueError('Code generation template not found. This is a server configuration issue.')

    manager_py = tmpl.render(
        plugin_name=plugin_name,
        class_name=class_name,
        plugin_id=plugin_id,
        generated_date=datetime.now().strftime('%Y-%m-%d'),
        config_vars=config_vars,
        elements=processed,
        has_clock=has_clock,
        has_blink=has_blink,
        has_countdown=has_countdown,
        has_text_template=has_text_template,
        bg_color=bg_color,
    )

    # Syntax-check the generated Python
    try:
        ast.parse(manager_py)
    except SyntaxError as exc:
        raise ValueError(f'Generated code has a syntax error: {exc}') from exc

    # Build manifest
    manifest = {
        'id': plugin_id,
        'name': plugin_name,
        'version': version,
        'author': author,
        'description': metadata.get('description', f'Custom plugin created with LEDMatrix Plugin Composer'),
        'category': metadata.get('category', 'custom'),
        'tags': ['composer', 'custom'],
        'entry_point': 'manager.py',
        'class_name': class_name,
        'display_modes': [plugin_id],
        'compatible_versions': ['>=2.0.0'],
        'last_updated': datetime.now().strftime('%Y-%m-%d'),
        'update_interval': int(metadata.get('update_interval', 60)),
        'default_duration': float(metadata.get('display_duration', 15)),
        'versions': [
            {'released': datetime.now().strftime('%Y-%m-%d'), 'version': version}
        ],
    }

    # Validate manifest against the project's schema
    if composer_bp.project_root:
        schema_path = Path(composer_bp.project_root) / 'schema' / 'manifest_schema.json'
        if schema_path.exists():
            schema = json.loads(schema_path.read_text())
            validator = jsonschema.Draft7Validator(schema)
            errors = list(validator.iter_errors(manifest))
            if errors:
                msgs = '; '.join(e.message for e in errors[:3])
                raise ValueError(f'Manifest validation failed: {msgs}')

    # Build config_schema
    type_map = {
        'string': {'type': 'string'},
        'number': {'type': 'number', 'minimum': 0},
        'boolean': {'type': 'boolean'},
        'color': {
            'type': 'array',
            'items': {'type': 'integer', 'minimum': 0, 'maximum': 255},
            'minItems': 3,
            'maxItems': 3,
        },
    }

    config_properties = {
        'enabled': {'type': 'boolean', 'default': True},
        'display_duration': {'type': 'number', 'minimum': 1, 'default': float(metadata.get('display_duration', 15))},
    }
    for cv in config_vars:
        cv_type = cv.get('type', 'string')
        prop = dict(type_map.get(cv_type, {'type': 'string'}))
        if cv.get('description'):
            prop['description'] = cv['description']
        if cv.get('label'):
            prop['title'] = cv['label']
        default = cv.get('default', '')
        if cv_type == 'number':
            try:
                prop['default'] = float(default) if default != '' else 0
            except (TypeError, ValueError):
                prop['default'] = 0
        elif cv_type == 'boolean':
            prop['default'] = bool(default)
        else:
            prop['default'] = default
        config_properties[cv['key']] = prop

    config_schema = {
        '$schema': 'http://json-schema.org/draft-07/schema#',
        'type': 'object',
        'properties': config_properties,
    }

    return {
        'manager.py': manager_py,
        'manifest.json': json.dumps(manifest, indent=2),
        'config_schema.json': json.dumps(config_schema, indent=2),
        'requirements.txt': '',
    }


def _save_composer_state(target_dir: Path, payload: dict) -> None:
    """Persist the raw composer payload alongside the generated plugin files."""
    (target_dir / '_composer_state.json').write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding='utf-8'
    )


def _pack_zip(files: dict, plugin_id: str) -> io.BytesIO:
    """Pack generated plugin files into an in-memory ZIP."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
        for filename, content in files.items():
            info = zipfile.ZipInfo(f'{plugin_id}/{filename}')
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, content.encode('utf-8') if isinstance(content, str) else content)
    buf.seek(0)
    return buf


# ── Routes ────────────────────────────────────────────────────────────────────

@composer_bp.route('/')
def index():
    return render_template('v3/composer.html')


@composer_bp.route('/api/generate', methods=['POST'])
def generate_zip():
    data = request.get_json(force=True, silent=True)
    if not data:
        return jsonify({'status': 'error', 'message': 'No JSON body'}), 400
    try:
        files = _generate_plugin_files(data)
    except ValueError as exc:
        return jsonify({'status': 'error', 'message': str(exc)}), 422

    plugin_id = data.get('metadata', {}).get('id', 'plugin')
    files['_composer_state.json'] = json.dumps(data, indent=2, ensure_ascii=False)
    zip_buf = _pack_zip(files, plugin_id)
    return send_file(
        zip_buf,
        mimetype='application/zip',
        as_attachment=True,
        download_name=f'{plugin_id}.zip',
    )


@composer_bp.route('/api/install', methods=['POST'])
def install_locally():
    if not composer_bp.plugins_dir:
        return jsonify({'status': 'error', 'message': 'Plugin directory not configured'}), 503

    data = request.get_json(force=True, silent=True)
    if not data:
        return jsonify({'status': 'error', 'message': 'No JSON body'}), 400

    try:
        files = _generate_plugin_files(data)
    except ValueError as exc:
        return jsonify({'status': 'error', 'message': str(exc)}), 422

    plugin_id = data.get('metadata', {}).get('id', '')
    target = Path(composer_bp.plugins_dir) / plugin_id
    force = bool(data.get('_force', False))

    if target.exists() and not force:
        return jsonify({
            'status': 'conflict',
            'message': f'Plugin "{plugin_id}" is already installed.',
        }), 409

    try:
        if target.exists() and force:
            import shutil as _shutil
            _shutil.rmtree(target)
        target.mkdir(parents=True, exist_ok=False)
        for filename, content in files.items():
            (target / filename).write_text(content, encoding='utf-8')
        _save_composer_state(target, data)
    except OSError as exc:
        return jsonify({'status': 'error', 'message': f'Failed to write plugin files: {exc}'}), 500

    # Trigger plugin discovery so it shows up in the Plugin Manager immediately
    if composer_bp.plugin_manager:
        try:
            composer_bp.plugin_manager.discover_plugins()
        except Exception as exc:
            logger.warning('discover_plugins() failed after composer install: %s', exc)

    return jsonify({
        'status': 'success',
        'message': f'Plugin "{plugin_id}" installed successfully.',
        'plugin_id': plugin_id,
    })


@composer_bp.route('/api/fonts/<font_name>')
def serve_font(font_name):
    """Serve an allowlisted font file for canvas FontFace loading."""
    if font_name not in _ALLOWED_FONTS:
        return '', 404
    if not composer_bp.project_root:
        return '', 503
    font_path = Path(composer_bp.project_root) / 'assets' / 'fonts' / font_name
    if not font_path.exists():
        return '', 404
    return send_file(str(font_path), mimetype='font/ttf')


@composer_bp.route('/api/validate-id/<plugin_id>')
def validate_id(plugin_id):
    """Check whether a plugin ID is valid and available."""
    if not _PLUGIN_ID_RE.match(plugin_id):
        return jsonify({'valid': False, 'available': False, 'reason': 'Invalid format'})
    if composer_bp.plugins_dir:
        taken = (Path(composer_bp.plugins_dir) / plugin_id).exists()
        if taken:
            return jsonify({'valid': True, 'available': False, 'reason': 'Already installed'})
    return jsonify({'valid': True, 'available': True})


@composer_bp.route('/api/plugins')
def list_plugins():
    """List installed plugins, flagging which ones have a saved composer state."""
    if not composer_bp.plugins_dir:
        return jsonify([])
    plugins_dir = Path(composer_bp.plugins_dir)
    results = []
    for entry in sorted(plugins_dir.iterdir()):
        if not entry.is_dir():
            continue
        manifest_path = entry / 'manifest.json'
        if not manifest_path.exists():
            continue
        try:
            manifest = json.loads(manifest_path.read_text())
        except Exception:
            continue
        has_state = (entry / '_composer_state.json').exists()
        results.append({
            'id': manifest.get('id', entry.name),
            'name': manifest.get('name', entry.name),
            'version': manifest.get('version', ''),
            'author': manifest.get('author', ''),
            'has_composer_state': has_state,
        })
    return jsonify(results)


@composer_bp.route('/api/preview', methods=['POST'])
def preview_code():
    """Generate plugin files and return them as JSON for the code preview modal."""
    data = request.get_json(force=True, silent=True)
    if not data:
        return jsonify({'status': 'error', 'message': 'No JSON body'}), 400
    try:
        files = _generate_plugin_files(data)
    except ValueError as exc:
        return jsonify({'status': 'error', 'message': str(exc)}), 422
    return jsonify({
        'status': 'ok',
        'files': {
            'manager.py': files['manager.py'],
            'manifest.json': files['manifest.json'],
            'config_schema.json': files['config_schema.json'],
        },
    })


@composer_bp.route('/api/load/<plugin_id>')
def load_plugin(plugin_id):
    """Load a plugin's composer state for editing.

    If a _composer_state.json exists, return it verbatim.
    Otherwise, extract config vars from config_schema.json for a partial import.
    """
    if not composer_bp.plugins_dir:
        return jsonify({'status': 'error', 'message': 'Plugin directory not configured'}), 503
    if not _PLUGIN_ID_RE.match(plugin_id):
        return jsonify({'status': 'error', 'message': 'Invalid plugin ID'}), 400

    plugin_dir = Path(composer_bp.plugins_dir) / plugin_id
    if not plugin_dir.exists():
        return jsonify({'status': 'error', 'message': 'Plugin not found'}), 404

    # Full composer state
    state_path = plugin_dir / '_composer_state.json'
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text())
            return jsonify({'status': 'ok', 'source': 'composer', 'state': state})
        except Exception as exc:
            return jsonify({'status': 'error', 'message': f'Failed to read state: {exc}'}), 500

    # Partial import from config_schema.json
    schema_path = plugin_dir / 'config_schema.json'
    manifest_path = plugin_dir / 'manifest.json'
    config_vars = []

    if schema_path.exists():
        try:
            schema = json.loads(schema_path.read_text())
            props = schema.get('properties', {})
            skip = {'enabled', 'display_duration', 'update_interval'}
            type_map = {'boolean': 'boolean', 'number': 'number', 'integer': 'number', 'string': 'string'}
            for key, prop in props.items():
                if key in skip:
                    continue
                prop_type = prop.get('type', 'string')
                if isinstance(prop_type, list):
                    prop_type = next((t for t in prop_type if t != 'null'), 'string')
                # Detect color arrays
                if prop_type == 'array' and prop.get('maxItems') == 3:
                    cv_type = 'color'
                else:
                    cv_type = type_map.get(prop_type, 'string')
                config_vars.append({
                    'key': key,
                    'label': prop.get('title', key.replace('_', ' ').title()),
                    'type': cv_type,
                    'default': prop.get('default', ''),
                    'description': prop.get('description', ''),
                })
        except Exception:
            pass

    manifest = {}
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text())
        except Exception:
            pass

    partial_state = {
        'composer_version': '1.0',
        'metadata': {
            'id': manifest.get('id', plugin_id),
            'name': manifest.get('name', plugin_id),
            'author': manifest.get('author', ''),
            'version': manifest.get('version', '1.0.0'),
            'description': manifest.get('description', ''),
            'category': manifest.get('category', 'custom'),
            'display_duration': manifest.get('default_duration', 15),
            'update_interval': manifest.get('update_interval', 60),
            'api_requirements': manifest.get('api_requirements', []),
        },
        'elements': [],
        'dataModel': {'configVars': config_vars, 'dataSources': [], 'computedVars': []},
    }
    return jsonify({'status': 'ok', 'source': 'schema_import', 'state': partial_state})
