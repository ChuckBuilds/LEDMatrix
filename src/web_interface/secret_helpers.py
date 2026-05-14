"""
Secret handling helpers for the web interface.

Provides functions for identifying, masking, separating, and filtering
secret fields in plugin configurations based on JSON Schema x-secret markers.
"""

from typing import Any, Dict, Set, Tuple


def find_secret_fields(properties: Dict[str, Any], prefix: str = '') -> Set[str]:
    """Find all fields marked with ``x-secret: true`` in a JSON Schema properties dict.

    Recurses into nested objects and array items to discover secrets at any
    depth (e.g. ``accounts[].token``).

    Args:
        properties: The ``properties`` dict from a JSON Schema.
        prefix: Dot-separated prefix for nested field paths (used in recursion).

    Returns:
        A set of dot-separated field paths (e.g. ``{"api_key", "auth.token"}``).
    """
    fields: Set[str] = set()
    if not isinstance(properties, dict):
        return fields
    for field_name, field_props in properties.items():
        if not isinstance(field_props, dict):
            continue
        full_path = f"{prefix}.{field_name}" if prefix else field_name
        if field_props.get('x-secret', False):
            fields.add(full_path)
        if field_props.get('type') == 'object' and 'properties' in field_props:
            fields.update(find_secret_fields(field_props['properties'], full_path))
        # Recurse into array items (e.g. accounts[].token)
        if field_props.get('type') == 'array' and isinstance(field_props.get('items'), dict):
            items_schema = field_props['items']
            if items_schema.get('x-secret', False):
                fields.add(f"{full_path}[]")
            if items_schema.get('type') == 'object' and 'properties' in items_schema:
                fields.update(find_secret_fields(items_schema['properties'], f"{full_path}[]"))
    return fields


def separate_secrets(
    config: Dict[str, Any], secret_paths: Set[str], prefix: str = ''
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Split a config dict into regular and secret portions.

    Uses the set of dot-separated secret paths (from :func:`find_secret_fields`)
    to partition values.  Empty nested dicts are dropped from the regular
    portion to match the original inline behavior.  Handles array-item secrets
    using ``[]`` notation in paths (e.g. ``accounts[].token``).

    Args:
        config: The full plugin config dict.
        secret_paths: Set of dot-separated paths identifying secret fields.
        prefix: Dot-separated prefix for nested paths (used in recursion).

    Returns:
        A ``(regular, secrets)`` tuple of dicts.
    """
    regular: Dict[str, Any] = {}
    secrets: Dict[str, Any] = {}
    for key, value in config.items():
        full_path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            nested_regular, nested_secrets = separate_secrets(value, secret_paths, full_path)
            if nested_regular:
                regular[key] = nested_regular
            if nested_secrets:
                secrets[key] = nested_secrets
        elif isinstance(value, list):
            # Check if array elements themselves are secrets
            array_path = f"{full_path}[]"
            if array_path in secret_paths:
                secrets[key] = value
            else:
                # Check if array items have nested secret fields
                has_nested = any(p.startswith(f"{array_path}.") for p in secret_paths)
                if has_nested:
                    reg_items = []
                    sec_items = []
                    for item in value:
                        if isinstance(item, dict):
                            r, s = separate_secrets(item, secret_paths, array_path)
                            reg_items.append(r)
                            sec_items.append(s)
                        else:
                            reg_items.append(item)
                            sec_items.append({})
                    regular[key] = reg_items
                    if any(sec_items):
                        secrets[key] = sec_items
                else:
                    regular[key] = value
        elif full_path in secret_paths:
            secrets[key] = value
        else:
            regular[key] = value
    return regular, secrets


def mask_secret_fields(config: Dict[str, Any], schema_properties: Dict[str, Any]) -> Dict[str, Any]:
    """Mask config values for fields marked ``x-secret: true`` in the schema.

    Replaces each present secret value with an empty string so that API
    responses never expose plain-text secrets.  Non-secret values are
    returned unchanged.  Recurses into nested objects and array items.

    Args:
        config: The plugin config dict (may contain secret values).
        schema_properties: The ``properties`` dict from the plugin's JSON Schema.

    Returns:
        A copy of *config* with secret values replaced by ``''``.
        Nested dicts containing secrets are also copied (not mutated in place).
    """
    result = dict(config)
    for fname, fprops in schema_properties.items():
        if not isinstance(fprops, dict):
            continue
        if fprops.get('x-secret', False):
            # Mask any present value — including falsey ones like 0 or False
            if fname in result and result[fname] is not None and result[fname] != '':
                result[fname] = ''
        elif fprops.get('type') == 'object' and 'properties' in fprops:
            if fname in result and isinstance(result[fname], dict):
                result[fname] = mask_secret_fields(result[fname], fprops['properties'])
        elif fprops.get('type') == 'array' and isinstance(fprops.get('items'), dict):
            items_schema = fprops['items']
            if fname in result and isinstance(result[fname], list):
                if items_schema.get('x-secret', False):
                    # Entire array elements are secrets — mask each
                    result[fname] = ['' for _ in result[fname]]
                elif items_schema.get('type') == 'object' and 'properties' in items_schema:
                    # Recurse into each array element's properties
                    result[fname] = [
                        mask_secret_fields(item, items_schema['properties'])
                        if isinstance(item, dict) else item
                        for item in result[fname]
                    ]
    return result


def mask_all_secret_values(config: Dict[str, Any]) -> Dict[str, Any]:
    """Blanket-mask every non-empty value in a secrets config dict.

    Used by the ``GET /config/secrets`` endpoint where all values are secret
    by definition.  Placeholder strings (``YOUR_*``) and empty/None values are
    left as-is so the UI can distinguish "not set" from "set".

    Args:
        config: A raw secrets config dict (e.g. from ``config_secrets.json``).

    Returns:
        A copy with all real values replaced by ``'••••••••'``.
    """
    masked: Dict[str, Any] = {}
    for k, v in config.items():
        if isinstance(v, dict):
            masked[k] = mask_all_secret_values(v)
        elif v not in (None, '') and not (isinstance(v, str) and v.startswith('YOUR_')):
            masked[k] = '••••••••'
        else:
            masked[k] = v
    return masked


def remove_empty_secrets(secrets: Dict[str, Any]) -> Dict[str, Any]:
    """Remove empty / whitespace-only / None values from a secrets dict.

    When the GET endpoint masks secret values to ``''``, a subsequent POST
    will send those empty strings back.  This filter strips them so that
    existing stored secrets are not overwritten with blanks.

    Args:
        secrets: A secrets dict that may contain masked empty values.

    Returns:
        A copy with empty entries removed.  Empty nested dicts are pruned.
    """
    result: Dict[str, Any] = {}
    for k, v in secrets.items():
        if isinstance(v, dict):
            nested = remove_empty_secrets(v)
            if nested:
                result[k] = nested
        elif v is not None and not (isinstance(v, str) and v.strip() == ''):
            result[k] = v
    return result
