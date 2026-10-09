"""Putting a submitted plugin config's lists back into list shape.

A list reaches a plugin-config save keyed by position more often than as a
list. The settings form posts one field per element (``feeds.custom_feeds.0.name``),
which ``_set_nested_value`` stores as ``{"0": {"name": ...}}``, and a JSON
save built by flattening then re-nesting dotted keys carries the same dict.
Validation expects an array there, so the save converts them first.
"""
from typing import Any, Dict


def _schema_type_is(prop: Any, wanted: str) -> bool:
    """Whether a schema property is of ``wanted`` type, unions included.

    The one copy: ``web_interface/blueprints/api_v3`` imports it from here
    (src/ must not import the Flask blueprint). A union such as
    ``["array", "null"]`` -- the per-element style overrides, where null means
    "inherit" -- is still an array for recombining position-keyed inputs.
    """
    if not isinstance(prop, dict):
        return False
    declared = prop.get('type')
    if isinstance(declared, list):
        return wanted in declared
    return declared == wanted


def _is_index_dict(value: Any) -> bool:
    """True for a dict keyed only by list positions ("0", "1", ...), or empty."""
    return isinstance(value, dict) and all(str(k).isdigit() for k in value)


def coerce_array_shapes(config: Any, schema_props: Dict[str, Any],
                        short_lists_take_default: bool = False) -> None:
    """Turn position-keyed dicts into lists wherever the schema has an array.

    Walks ``config`` alongside the schema's ``properties``, in place: into
    nested objects, and into the objects of an array's items. An empty dict
    where an array belongs becomes ``[]``.

    ``short_lists_take_default`` is for form posts. A form draws a fixed-length
    list (an RGB colour, say) as one input per element, and a blanked input
    drops out of the parsed list; the schema default then stands in, as long as
    it is itself long enough, instead of the save failing on ``minItems``.

    Element types are left alone: normalization after this converts numeric
    strings to the numbers the schema asks for.
    """
    if not isinstance(config, dict):
        return
    for key, prop_schema in schema_props.items():
        if key not in config or not isinstance(prop_schema, dict):
            continue
        value = config[key]

        if _schema_type_is(prop_schema, 'array'):
            if _is_index_dict(value):
                value = config[key] = [value[k] for k in sorted(value, key=lambda k: int(str(k)))]
            if not isinstance(value, list):
                continue
            min_items = prop_schema.get('minItems')
            default = prop_schema.get('default')
            if (short_lists_take_default and min_items is not None
                    and len(value) < min_items
                    and isinstance(default, list) and len(default) >= min_items):
                value = config[key] = list(default)
            items_schema: Any = prop_schema.get('items')
            if (_schema_type_is(items_schema, 'object')
                    and 'properties' in items_schema):
                for element in value:
                    coerce_array_shapes(element, items_schema['properties'],
                                        short_lists_take_default)

        elif _schema_type_is(prop_schema, 'object') and 'properties' in prop_schema:
            coerce_array_shapes(value, prop_schema['properties'], short_lists_take_default)
