"""
One field model for a plugin's config form, built from its schema and config.

Today a plugin's settings form is drawn by the ``render_field`` macro in
``web_interface/templates/v3/partials/plugin_config.html``: about 1,100 lines
of Jinja that walk the schema, pick a control per property (or hand it to a
JS widget through an inline ``<script>``) and post flat dotted form keys that
the server rebuilds into JSON. The JS widgets duplicate much of that, and the
two drift.

:func:`build_field_model` walks the schema once, the way the macro does, and
returns a plain, JSON-serialisable tree describing every field: its dotted
path, label, help, widget, starting value, default, constraints and options,
and -- so that the model is provably complete before anything renders from
it -- the exact form controls the macro emits for it today (``inputs``) and
the JS widget it mounts (``mount``). ``test/test_field_model_parity.py``
renders the real macro for every plugin schema it can find and checks that
the names and starting values of those controls match the model exactly.

Nothing renders from this yet. The plan (docs/WEB_FRONTEND_ARCHITECTURE.md):
one ES-module renderer walks this model and mounts every field through the
widget registry, the form posts JSON to the existing JSON save path, and the
macro and the dotted-key reconstruction retire.

The model mirrors the macro's behaviour, quirks included (an enum check runs
before a number's, a list-typed ``type`` uses its first entry, a number whose
default is ``null`` renders the text ``None``), because parity is the point of
this stage. Fixing those is a renderer change for later, made once in one
place.

Shape (all keys always present unless noted)::

    {
      "version": 1,
      "plugin_id": "...",
      "rendered_sections": ["key", ...],   # the __rendered_section hidden inputs
      "fields": [Field, ...],              # basic tier, in form order
      "advanced_fields": [Field, ...],     # flat "x-advanced": true fields
      "schemaless": false,                 # true: no schema, fields from config
    }

    Field = {
      "key", "path", "id", "label", "help",
      "type":      the macro's field type (first entry of a list type),
      "widget":    what draws it: checkbox, select, number, text, csv-text,
                   section, schedule-picker, time-range, style-editor,
                   toggle-switch, slider, number-input, file-upload,
                   checkbox-group, google-calendar-picker, day-selector,
                   custom-feeds, array-table, color-picker, json-file-manager,
                   any string widget the macro mounts (text-input, ...), or a
                   plugin-supplied x-widget,
      "x_widget":  the schema's x-widget, or None,
      "value":     the value the form starts with,
      "default":   the schema default (key absent when the schema has none),
      "secret":    true for "x-secret" fields,
      "advanced":  true in the Advanced Settings section,
      "constraints": {minimum, maximum, ...} as declared,
      "options":   [{"value", "label"}] for selects and checkbox groups,
      "inputs":    [Input, ...] form controls the server renders,
      "mount":     {"widget", "name", "value", "config", "plugin_widget"} or None,
      "children":  [Field, ...] for sections (and a style-editor's fallback),
      "columns" / "rows" / "max_items" for array-table and custom-feeds,
      "stale_values" for checkbox groups, "error" for a mis-declared widget,
    }

    Input = {"name", "control", "value", "encoding"[, "checked"][, "options"]}
      control:  hidden | text | number | url | date | time | checkbox | select
      encoding: how ``value`` is written into the HTML today --
                text  str(value)                  json  JSON text
                csv   ", ".join(str(item))        bool  "true"/"false"
                For a checkbox, ``value`` is its value attribute and
                ``checked`` its state; for a select, ``value`` is the option
                the browser submits and ``options`` the option values.

Secret fields: pass the config *after* ``mask_secret_fields`` (as the route
does); the model copies values verbatim.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterator, List, Optional, Tuple

FIELD_MODEL_VERSION = 1

#: String x-widgets the macro mounts as JS widgets (plugin_config.html's
#: ``str_widget in [...]`` list). Any other x-widget on a string is a
#: plugin-supplied widget, loaded through ensureWidget over a text fallback.
STRING_WIDGETS = (
    'text-input', 'textarea', 'select-dropdown', 'toggle-switch', 'radio-group',
    'date-picker', 'time-picker', 'slider', 'color-picker', 'email-input',
    'url-input', 'password-input', 'font-selector', 'file-upload-single',
    'plugin-file-manager', 'google-oauth',
)

_CONSTRAINT_KEYS = (
    'minimum', 'maximum', 'exclusiveMinimum', 'exclusiveMaximum', 'multipleOf',
    'minLength', 'maxLength', 'pattern', 'format', 'minItems', 'maxItems',
    'uniqueItems',
)

_MISSING = object()


# ── Jinja semantics the macro relies on ─────────────────────────────────────

def _is_string(value: Any) -> bool:
    return isinstance(value, str)


def _is_iterable(value: Any) -> bool:
    """Jinja's ``is iterable``: anything ``iter()`` accepts (dicts included)."""
    try:
        iter(value)
    except TypeError:
        return False
    return True


def _is_list_like(value: Any) -> bool:
    """``value is iterable and value is not string``."""
    return value is not None and _is_iterable(value) and not _is_string(value)


_WORD_SPLIT = re.compile(r'([-\s({\[<]+)')


def _title(text: Any) -> str:
    """Jinja's ``title`` filter (not str.title: it keeps "2xl" lower case)."""
    return ''.join(
        item[0].upper() + item[1:].lower()
        for item in _WORD_SPLIT.split(str(text)) if item)


def _humanise(key: Any) -> str:
    """``key|replace('_', ' ')|title``."""
    return _title(str(key).replace('_', ' '))


def _x_options(prop: Dict[str, Any]) -> Dict[str, Any]:
    return prop.get('x-options') or prop.get('x_options') or {}


def _x_widget(prop: Dict[str, Any]) -> Optional[str]:
    return prop.get('x-widget') or prop.get('x_widget')


def is_hidden(prop: Any) -> bool:
    """The macro's ``prop_is_hidden``: "x-display": "hidden", or an object
    whose every child is hidden."""
    if not isinstance(prop, dict):
        return False
    if prop.get('x-display') == 'hidden':
        return True
    children = prop.get('properties')
    if isinstance(children, dict) and children:
        return all(is_hidden(child) for child in children.values())
    return False


def field_type(prop: Dict[str, Any]) -> Any:
    """The macro's field type: a string ``type``, the first entry of a list
    ``type`` (so ``["null", "integer"]`` is ``"null"``), else ``"string"``.

    Usually a string; a malformed list type hands back whatever its first
    entry is, as the macro does."""
    declared = prop.get('type')
    if _is_string(declared):
        return declared
    if declared and _is_list_like(declared):
        return next(iter(declared))
    return 'string'


def _column_type(col_def: Dict[str, Any]) -> Any:
    """array-table's column type: the first non-"null" entry of a list type."""
    raw = col_def.get('type', 'string')
    if _is_list_like(raw):
        rest = [entry for entry in raw if entry != 'null']
        return rest[0] if rest and rest[0] else 'string'
    return raw or 'string'


def _array_value(value: Any, prop: Dict[str, Any]) -> Any:
    """The value most array widgets draw: the stored list, else a list
    default, else []."""
    if _is_list_like(value):
        return value
    default = prop.get('default', _MISSING)
    if default is not _MISSING and _is_list_like(default):
        return default
    return []


def _constraints(prop: Dict[str, Any]) -> Dict[str, Any]:
    return {key: prop[key] for key in _CONSTRAINT_KEYS if key in prop}


def _input(name: str, control: str, value: Any, encoding: str = 'text',
           **extra: Any) -> Dict[str, Any]:
    item = {'name': name, 'control': control, 'value': value, 'encoding': encoding}
    item.update(extra)
    return item


def _select_value(options: List[Any], matches: List[Any]) -> Any:
    """What a single <select> submits: the last selected option, else the first."""
    if matches:
        return matches[-1]
    return options[0] if options else None


# ── fields ──────────────────────────────────────────────────────────────────

def _base_node(key: str, prop: Dict[str, Any], value: Any, full_key: str,
               plugin_id: str) -> Dict[str, Any]:
    node: Dict[str, Any] = {
        'key': key,
        'path': full_key,
        'id': f"{plugin_id}-{full_key}".replace('.', '-').replace('_', '-'),
        'label': prop.get('title') or _humanise(key),
        'help': prop.get('description') or '',
        'type': field_type(prop),
        'widget': None,
        'x_widget': _x_widget(prop),
        'value': value,
        'secret': bool(prop.get('x-secret')),
        'advanced': False,
        'constraints': _constraints(prop),
        'options': [],
        'inputs': [],
        'mount': None,
        'children': [],
    }
    if 'default' in prop:
        node['default'] = prop['default']
    return node


def _mount(widget: str, name: Optional[str], value: Any,
           config: Optional[Dict[str, Any]] = None,
           plugin_widget: bool = False) -> Dict[str, Any]:
    return {'widget': widget, 'name': name, 'value': value,
            'config': config or {}, 'plugin_widget': plugin_widget}


def _build_field(key: str, prop: Any, value: Any, prefix: str,
                 plugin_id: str) -> Optional[Dict[str, Any]]:
    """``render_field``: one property, or None when the macro draws nothing."""
    if not isinstance(prop, dict) or is_hidden(prop):
        return None
    # A key the saved config doesn't have renders its schema default.
    if value is None and 'default' in prop:
        value = prop['default']
    full_key = f"{prefix}.{key}" if prefix else key
    node = _base_node(key, prop, value, full_key, plugin_id)
    ftype = node['type']

    if ftype == 'object':
        return _object_field(node, key, prop, value, prefix, full_key, plugin_id)
    if ftype == 'boolean':
        _boolean_field(node, prop, value, full_key)
    elif prop.get('enum'):
        _enum_field(node, prop, value, full_key)
    elif ftype in ('number', 'integer'):
        _number_field(node, prop, value, full_key, ftype)
    elif ftype == 'array':
        _array_field(node, prop, value, full_key, plugin_id)
    else:
        _string_field(node, prop, value, full_key, ftype)
    return node


def _object_field(node: Dict[str, Any], key: str, prop: Dict[str, Any], value: Any,
                  prefix: str, full_key: str, plugin_id: str) -> Optional[Dict[str, Any]]:
    widget = _x_widget(prop)
    obj_value = value if value is not None else {}
    if widget in ('schedule-picker', 'time-range'):
        node['widget'] = widget
        node['value'] = obj_value
        node['inputs'].append(_input(full_key, 'hidden', obj_value, 'json'))
        node['mount'] = _mount(widget, None, obj_value, {'x-options': _x_options(prop)})
        return node
    if widget == 'style-editor':
        # The widget renders its own inputs under full_key; until it loads,
        # and wherever it declines a block, the nested section is the form.
        node['widget'] = 'style-editor'
        node['value'] = obj_value
        node['mount'] = _mount('style-editor', full_key, obj_value, {'schema': prop})
        node['children'] = [_section(key, prop, value, prefix, plugin_id)]
        return node
    if prop.get('properties'):
        return _section(key, prop, value, prefix, plugin_id)
    return None  # an object with no properties and no widget draws nothing


def _section(key: str, prop: Dict[str, Any], value: Any, prefix: str,
             plugin_id: str) -> Dict[str, Any]:
    """``render_nested_section``: a collapsible block of child fields."""
    full_key = f"{prefix}.{key}" if prefix else key
    # Only a dict can be looked into; a legacy boolean is the block's
    # `enabled` switch (schema_manager.legacy_bool_as_object).
    properties = prop.get('properties') or {}
    if isinstance(value, dict):
        nested_value = value
    elif isinstance(value, bool) and 'enabled' in properties:
        nested_value = {'enabled': value}
    else:
        nested_value = {}
    node = _base_node(key, prop, nested_value, full_key, plugin_id)
    node['widget'] = 'section'
    node['id'] = f"{plugin_id}-section-{full_key}".replace('.', '-').replace('_', '-')
    order = prop['x-propertyOrder'] if 'x-propertyOrder' in prop else list(properties.keys())
    for nested_key in order:
        if nested_key in properties and not is_hidden(properties[nested_key]):
            child = _build_field(nested_key, properties[nested_key],
                                 nested_value[nested_key] if nested_key in nested_value else None,
                                 full_key, plugin_id)
            if child is not None:
                node['children'].append(child)
    return node


def _boolean_field(node, prop, value, full_key):
    if _x_widget(prop) == 'toggle-switch':
        node['widget'] = 'toggle-switch'
        node['mount'] = _mount('toggle-switch', full_key,
                               value if value is not None else False,
                               {'type': 'boolean', 'x-options': _x_options(prop)})
    else:
        node['widget'] = 'checkbox'
        node['inputs'].append(_input(full_key, 'checkbox', 'true', checked=bool(value)))


def _enum_field(node, prop, value, full_key):
    options = list(prop['enum'])
    labels = _x_options(prop).get('labels') or {}
    node['widget'] = 'select'
    node['options'] = [{'value': option,
                        'label': labels.get(option, _humanise(option))
                        if _hashable(option) else _humanise(option)}
                       for option in options]
    posted = _select_value(options, [option for option in options if value == option])
    node['inputs'].append(_input(full_key, 'select', posted, options=options))


def _hashable(value: Any) -> bool:
    try:
        hash(value)
    except TypeError:
        return False
    return True


def _number_field(node, prop, value, full_key, ftype):
    widget = _x_widget(prop)
    if widget in ('slider', 'number-input'):
        node['widget'] = widget
        node['mount'] = _mount(widget, full_key, value, {
            'type': ftype,
            'minimum': prop.get('minimum'),
            'maximum': prop.get('maximum'),
            'x-options': _x_options(prop),
        })
    else:
        node['widget'] = 'number'
        node['inputs'].append(_input(full_key, 'number', _text_value(value, prop)))


def _text_value(value: Any, prop: Dict[str, Any]) -> Any:
    """``value if value is not none else (prop.default if defined else '')``."""
    if value is not None:
        return value
    return prop['default'] if 'default' in prop else ''


def _array_field(node, prop, value, full_key, plugin_id):
    items = prop.get('items') or {}
    widget = _x_widget(prop) or (
        'array-table' if (items.get('type') == 'object' and items.get('properties')) else None)

    if widget == 'file-upload':
        upload = prop.get('x-upload-config') or {}
        images = _array_value(value, prop)
        node.update(widget='file-upload', value=images)
        node['constraints'].update({
            'max_files': upload.get('max_files', 10),
            'allowed_types': upload.get('allowed_types',
                                        ['image/png', 'image/jpeg', 'image/bmp', 'image/gif']),
            'max_size_mb': upload.get('max_size_mb', 5),
            'plugin_id': upload.get('plugin_id', plugin_id),
            'endpoint': upload.get('endpoint', '/api/v3/plugins/assets/upload'),
            'file_type': upload.get('file_type', 'image'),
        })
        node['inputs'].append(_input(full_key, 'hidden', images, 'json'))
    elif widget == 'checkbox-group':
        _checkbox_group(node, prop, value, full_key)
    elif widget == 'google-calendar-picker':
        selected = _calendar_value(value, prop)
        node.update(widget=widget, value=selected)
        node['mount'] = _mount(widget, full_key, selected, {})
    elif widget == 'day-selector':
        days = _array_value(value, prop)
        node.update(widget=widget, value=days)
        node['mount'] = _mount(widget, full_key, days, {'x-options': _x_options(prop)})
    elif widget == 'custom-feeds':
        _custom_feeds(node, prop, value, full_key, items)
    elif widget == 'array-table':
        _array_table(node, prop, value, full_key, items)
    elif widget == 'color-picker':
        _color_picker(node, prop, value, full_key)
    else:
        # The comma-separated text input; any other x-widget lands here too.
        default = prop.get('default', _MISSING)
        values = value if value is not None else ([] if default is _MISSING else default)
        node.update(widget='csv-text', value=values)
        node['inputs'].append(_input(full_key, 'text',
                                     values if _is_list_like(values) else '',
                                     'csv' if _is_list_like(values) else 'text'))


def _checkbox_group(node, prop, value, full_key):
    selected = _array_value(value, prop)
    items = prop.get('items') or {}
    options = items.get('enum') or []
    labels = (prop.get('x-options') or {}).get('labels') or {}
    # A saved value that is no longer an option is dropped (and reported), so
    # the save does not fail validation on a value nobody can see.
    stale = [v for v in selected if v not in options] if options else []
    if options:
        selected = [v for v in selected if v in options]
    node.update(widget='checkbox-group', value=selected, stale_values=stale)
    node['options'] = [{'value': option,
                        'label': labels.get(option, _humanise(option))
                        if _hashable(option) else _humanise(option)}
                       for option in options]
    for option in options:
        node['inputs'].append(_input(f"{full_key}[]", 'checkbox', option,
                                     checked=option in selected))
    node['inputs'].append(_input(f"{full_key}_data", 'hidden', selected, 'json'))
    # Sentinel: posts the field even when every box is unchecked.
    node['inputs'].append(_input(f"{full_key}[]", 'hidden', ''))


def _calendar_value(value: Any, prop: Dict[str, Any]) -> Any:
    """google-calendar-picker accepts a legacy comma-separated string."""
    if value is not None and _is_string(value) and value:
        return [part.strip() for part in value.split(',')]
    if _is_list_like(value):
        return value
    default = prop.get('default', _MISSING)
    if default is not _MISSING and _is_string(default) and default:
        return [part.strip() for part in default.split(',')]
    if default is not _MISSING and _is_list_like(default):
        return default
    return []


def _custom_feeds(node, prop, value, full_key, items):
    node['widget'] = 'custom-feeds'
    item_properties = items.get('properties', {})
    if not (item_properties.get('name') and item_properties.get('url')):
        node['error'] = "Custom feeds widget requires 'name' and 'url' properties in items schema."
        return
    feeds = _array_value(value, prop)
    node.update(value=feeds, rows=feeds, max_items=prop.get('maxItems', 50))
    for index, item in enumerate(feeds):
        base = f"{full_key}.{index}"
        node['inputs'].append(_input(f"{base}.name", 'text', item.get('name', '')))
        node['inputs'].append(_input(f"{base}.url", 'url', item.get('url', '')))
        logo = item.get('logo') or {}
        logo_path = logo.get('path', '')
        if logo_path:
            node['inputs'].append(_input(f"{base}.logo.path", 'hidden', logo_path))
            if logo.get('id'):
                node['inputs'].append(_input(f"{base}.logo.id", 'hidden', logo.get('id')))
        enabled = bool(item.get('enabled', True))
        node['inputs'].append(_input(f"{base}.enabled", 'hidden', enabled, 'bool'))
        node['inputs'].append(_input(f"{base}.enabled", 'checkbox', 'true', checked=enabled))


def _table_columns(prop: Dict[str, Any], item_properties: Dict[str, Any]) -> List[str]:
    """x-columns minus hidden ones, else the first four simple properties."""
    x_columns = prop.get('x-columns')
    if x_columns:
        return [name for name in x_columns if not is_hidden(item_properties.get(name))]
    columns: List[str] = []
    for name, col_def in item_properties.items():
        if (col_def.get('type') not in ['object', 'array'] and len(columns) < 4
                and not is_hidden(col_def)):
            columns.append(name)
    return columns


def _array_table(node, prop, value, full_key, items):
    item_properties = items.get('properties', {})
    rows = _array_value(value, prop)
    columns = _table_columns(prop, item_properties)
    advanced = {k: v for k, v in item_properties.items()
                if k not in columns and k != 'id' and not is_hidden(v)}
    node.update(widget='array-table', value=rows, rows=rows,
                max_items=prop.get('maxItems', 50))
    node['columns'] = [{
        'key': name,
        'label': (item_properties.get(name) or {}).get('title', _humanise(name)),
        'type': _column_type(item_properties.get(name) or {}),
        'x_widget': ((item_properties.get(name) or {}).get('x-widget')
                     or (item_properties.get(name) or {}).get('x_widget', '')),
    } for name in columns]
    node['advanced_columns'] = list(advanced)

    for index, item in enumerate(rows):
        base = f"{full_key}.{index}"
        for name in columns:
            node['inputs'].extend(_table_cell(f"{base}.{name}", item_properties.get(name, {}),
                                              item.get(name, item_properties.get(name, {}).get('default', ''))))
        # Hidden item properties have no control, but a posted row replaces
        # the stored item wholesale, so their stored values are carried.
        for k, v in item_properties.items():
            if is_hidden(v) and k in item and item[k] is not None:
                node['inputs'].append(_input(f"{base}.{k}", 'hidden', item[k], 'json'))
        if advanced:
            node['inputs'].extend(_advanced_cells(base, advanced, item))


def _table_cell(name: str, col_def: Dict[str, Any], col_value: Any) -> List[Dict[str, Any]]:
    col_type = _column_type(col_def)
    col_widget = col_def.get('x-widget') or col_def.get('x_widget', '')
    col_enum = col_def.get('enum', [])
    if col_type == 'boolean':
        return [_input(name, 'hidden', bool(col_value), 'bool'),
                _input(name, 'checkbox', 'true', checked=bool(col_value))]
    if col_type in ('integer', 'number'):
        return [_input(name, 'number', col_value if col_value is not None else '')]
    if col_enum:
        options = [opt for opt in col_enum if opt is not None]
        matches = [opt for opt in options
                   if col_value == opt or (col_value is None and col_def.get('default') == opt)]
        return [_input(name, 'select', _select_value(options, matches), options=options)]
    if col_widget == 'date-picker':
        return [_input(name, 'date', col_value if col_value is not None else '')]
    if col_widget == 'time-picker':
        return [_input(name, 'time', col_value if col_value is not None else '00:00')]
    return [_input(name, 'text', col_value if col_value is not None else '')]


def _advanced_cells(base: str, advanced: Dict[str, Any], item: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The row's hidden inputs for properties edited in the row editor."""
    cells: List[Dict[str, Any]] = []
    for prop_name, prop_schema in advanced.items():
        if prop_schema.get('type', 'string') == 'object' and prop_schema.get('properties'):
            stored = item.get(prop_name)
            sub_obj = stored if isinstance(stored, dict) else {}
            container = item.get(prop_name, {})
            for sub_name, sub_schema in prop_schema.get('properties', {}).items():
                name = f"{base}.{prop_name}.{sub_name}"
                if is_hidden(sub_schema):
                    if sub_name in sub_obj and sub_obj[sub_name] is not None:
                        cells.append(_input(name, 'hidden', sub_obj[sub_name], 'json'))
                    continue
                sub_val = container.get(sub_name) if isinstance(container, dict) else None
                final = sub_val if sub_val is not None else sub_schema.get('default')
                cells.append(_input(name, 'hidden', final if final is not None else ''))
        else:
            stored = item.get(prop_name)
            final = stored if stored is not None else prop_schema.get('default')
            cells.append(_input(f"{base}.{prop_name}", 'hidden', final if final is not None else ''))
    return cells


def _color_picker(node, prop, value, full_key):
    default = prop.get('default', _MISSING)
    if _is_list_like(value):
        rgb = value
    elif default is not _MISSING and _is_list_like(default):
        rgb = default
    else:
        rgb = [255, 255, 255]
    channels = [rgb[i] if len(rgb) > i else 255 for i in range(3)]
    node.update(widget='color-picker', value=rgb)
    for index, channel in enumerate(channels):
        node['inputs'].append(_input(f"{full_key}.{index}", 'number', channel))


def _string_field(node, prop, value, full_key, ftype):
    widget = _x_widget(prop)
    text = _text_value(value, prop)
    node['value'] = text
    if widget == 'file-upload':
        upload = prop.get('x-upload-config') or {}
        node['widget'] = 'file-upload'
        node['constraints'].update({
            'upload_endpoint': upload.get('upload_endpoint', ''),
            'target_filename': upload.get('target_filename', 'file.json'),
            'max_size_mb': upload.get('max_size_mb', 1),
            'allowed_extensions': upload.get('allowed_extensions', ['.json']),
        })
        node['inputs'].append(_input(full_key, 'hidden', text))
    elif widget == 'json-file-manager':
        # An iframe of the plugin's own file manager; it saves on its own.
        node['widget'] = 'json-file-manager'
    elif widget in STRING_WIDGETS:
        node['widget'] = widget
        node['mount'] = _mount(widget, full_key, text, {
            'type': ftype,
            'enum': prop.get('enum') or [],
            'minimum': prop.get('minimum'),
            'maximum': prop.get('maximum'),
            'x-options': _x_options(prop),
            'x-upload-config': prop.get('x-upload-config') or prop.get('x_upload_config') or {},
            'x-widget-config': prop.get('x-widget-config') or prop.get('x_widget_config') or {},
        })
    else:
        node['widget'] = widget or 'text'
        node['inputs'].append(_input(full_key, 'text', text))
        if widget:
            # A plugin-supplied widget (manifest "widgets"); the text input
            # stays as the fallback until it renders.
            node['mount'] = _mount(widget, full_key, text, {
                'type': ftype,
                'enum': prop.get('enum') or [],
                'x-options': _x_options(prop),
                'x-widget-config': prop.get('x-widget-config') or prop.get('x_widget_config') or {},
            }, plugin_widget=True)


# ── the form ────────────────────────────────────────────────────────────────

def _schemaless_field(key: str, value: Any) -> Dict[str, Any]:
    """A plugin with no schema: one plain control per stored key."""
    node = _base_node(key, {}, value, key, '')
    node['id'] = 'fallback-field-' + str(key).replace(' ', '-')
    if value is True or value is False:
        node['widget'] = 'checkbox'
        node['type'] = 'boolean'
        # No value attribute, so a checked box posts "on".
        node['inputs'].append(_input(key, 'checkbox', 'on', checked=bool(value)))
    elif isinstance(value, (int, float, complex)):
        node['widget'] = 'number'
        node['type'] = 'number'
        node['inputs'].append(_input(key, 'number', value))
    else:
        node['widget'] = 'text'
        node['inputs'].append(_input(key, 'text', value))
    return node


def build_field_model(schema: Any, config: Any, plugin_id: str = '') -> Dict[str, Any]:
    """The field model for one plugin's config form.

    ``schema`` is the plugin's config schema as the route loads it
    (``SchemaManager.load_schema``, so style elements are expanded);
    ``config`` is the plugin's section after defaults are merged and secrets
    masked, exactly what ``plugin_config.html`` is rendered with.
    """
    config = config if isinstance(config, dict) else {}
    model: Dict[str, Any] = {
        'version': FIELD_MODEL_VERSION,
        'plugin_id': plugin_id,
        'rendered_sections': [],
        'fields': [],
        'advanced_fields': [],
        'schemaless': False,
    }
    properties = schema.get('properties') if isinstance(schema, dict) else None
    if not properties:
        model['schemaless'] = True
        model['fields'] = [_schemaless_field(key, value)
                           for key, value in config.items() if key not in ['enabled']]
        return model

    order = schema['x-propertyOrder'] if 'x-propertyOrder' in schema else list(properties.keys())
    basic: List[str] = []
    advanced: List[str] = []
    for key in order:
        if key in properties and key != 'enabled' and not is_hidden(properties[key]):
            prop = properties[key]
            declared = prop.get('type') if isinstance(prop, dict) else None
            is_object = declared is not None and _is_iterable(declared) and 'object' in declared
            if isinstance(prop, dict) and prop.get('x-advanced') and not is_object:
                advanced.append(key)
            else:
                basic.append(key)
    model['rendered_sections'] = basic + advanced

    for tier, keys in (('fields', basic), ('advanced_fields', advanced)):
        for key in keys:
            node = _build_field(key, properties[key], config[key] if key in config else None,
                                '', plugin_id)
            if node is not None:
                node['advanced'] = tier == 'advanced_fields'
                model[tier].append(node)
    return model


# ── walking the model ───────────────────────────────────────────────────────

def iter_fields(model: Dict[str, Any]) -> Iterator[Dict[str, Any]]:
    """Every field node, depth first, in form order."""
    def walk(nodes):
        for node in nodes:
            yield node
            yield from walk(node.get('children') or [])
    yield from walk(model.get('fields') or [])
    yield from walk(model.get('advanced_fields') or [])


def form_inputs(model: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Every server-rendered form control, in document order, starting with
    the ``__rendered_section`` hidden inputs."""
    inputs = [_input('__rendered_section', 'hidden', key)
              for key in model.get('rendered_sections') or []]
    for node in iter_fields(model):
        inputs.extend(node.get('inputs') or [])
    return inputs


def widget_mounts(model: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Every JS widget the form mounts, in document order.

    A style-editor's fallback section is drawn before the editor's own
    script, so a node's children come before its own mount.
    """
    mounts: List[Dict[str, Any]] = []

    def walk(nodes):
        for node in nodes:
            walk(node.get('children') or [])
            if node.get('mount'):
                mounts.append(node['mount'])
    walk(model.get('fields') or [])
    walk(model.get('advanced_fields') or [])
    return mounts


def field_names(model: Dict[str, Any]) -> List[Tuple[str, str]]:
    """(name, source) for every posted name: 'form' controls and named 'widget' mounts."""
    names = [(item['name'], 'form') for item in form_inputs(model)]
    names += [(mount['name'], 'widget') for mount in widget_mounts(model) if mount['name']]
    return names
