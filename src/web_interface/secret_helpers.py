"""
Secret handling helpers for the web interface.

Provides functions for identifying, masking, separating, and filtering
secret fields in plugin configurations based on JSON Schema x-secret markers.
"""

from typing import Any, Dict, Optional, Set, Tuple


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


#: What a masked secret looks like on the wire. Named because the write path
#: has to recognise it coming back: a client that renders the mask and posts
#: it unchanged must not store the mask as if it were the secret.
SECRET_MASK = '\u2022' * 8


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
    return {k: _mask_value(v) for k, v in config.items()}


def _mask_value(value: Any) -> Any:
    """Mask one value, recursing through dicts and lists.

    A list used to be masked as though it were a scalar, so
    ``accounts: [{"name": "a", "token": "..."}]`` came back as a single
    ``'••••••••'``. Nothing leaked, but the caller could no longer see how
    many entries there were or any of their non-secret fields, and the raw
    editor was shown a string where the file holds an array.
    """
    if isinstance(value, dict):
        return {k: _mask_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_mask_value(item) for item in value]
    if value in (None, '') or (isinstance(value, str) and value.startswith('YOUR_')):
        return value
    return SECRET_MASK


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
        elif isinstance(v, list):
            # Lists used to fall through to the scalar branch below and be
            # kept verbatim, blanks and all. Because lists merge by
            # *replacement*, saving any unrelated setting then wrote
            # [{"token": ""}, ...] straight over the stored list and
            # destroyed every credential in it.
            pruned = _prune_secret_list(v)
            if pruned is not None:
                result[k] = pruned
        elif v is not None and not (isinstance(v, str) and v.strip() == ''):
            result[k] = v
    return result


def _prune_secret_list(items: list) -> Optional[list]:
    """Strip blanks from inside a list of secrets, preserving every index.

    The rest of the system treats a secrets list as *parallel* to the regular
    one -- ``sec[i]`` holds the secret fields of item ``i``, and ``{}`` means
    "item i has none" (see ConfigManager._strip_secrets_recursive). So an
    emptied dict item stays ``{}``: putting ``None`` there makes that list stop
    looking parallel, and the stripper then drops the whole key from the main
    config, taking the non-secret fields with it.

    A blank *scalar* becomes ``None``, meaning "no update at this index" --
    :func:`merge_secrets` substitutes whatever is stored there. Returns
    ``None`` when nothing in the list carries a real value, so the caller drops
    the key and leaves the stored list untouched.
    """
    pruned: list = []
    has_real_value = False
    for item in items:
        if isinstance(item, dict):
            kept = remove_empty_secrets(item)
            pruned.append(kept)
            has_real_value = has_real_value or bool(kept)
        elif isinstance(item, list):
            sub = _prune_secret_list(item)
            pruned.append(sub if sub is not None else [])
            has_real_value = has_real_value or sub is not None
        elif item is not None and not (isinstance(item, str) and item.strip() == ''):
            pruned.append(item)
            has_real_value = True
        else:
            pruned.append(None)
    return pruned if has_real_value else None


def merge_secrets(stored: Any, incoming: Any) -> Any:
    """Merge submitted secrets over stored ones, element-wise inside lists.

    ``deep_merge`` replaces a list wholesale. For secrets that is destructive:
    an incoming list that carries a real value for one entry and ``None`` for
    the rest would drop the stored credentials of every other entry. Here a
    list merges by index, and ``None`` means "keep what is stored".

    Entries are matched by *position*, which is what the config form gives us
    -- there is no schema-declared identity to key on, and it is the same
    contract ConfigManager._strip_secrets_recursive already relies on. The
    incoming list's length wins, so deleting an item deletes its secrets;
    an item the client left blank keeps whatever is stored at that index.
    """
    if isinstance(stored, dict) and isinstance(incoming, dict):
        merged = dict(stored)
        for key, value in incoming.items():
            merged[key] = (merge_secrets(stored[key], value)
                           if key in stored else value)
        return merged
    if isinstance(stored, list) and isinstance(incoming, list):
        # The incoming list sets the length -- the regular config's list is
        # authoritative about how many items exist, and this one runs parallel
        # to it. Removing an entry must therefore remove its secrets too.
        merged_list = []
        for index, item in enumerate(incoming):
            stored_item = stored[index] if index < len(stored) else None
            merged_list.append(stored_item if item is None
                               else merge_secrets(stored_item, item))
        return merged_list
    if incoming is None:
        return stored
    return incoming


def strip_masked_values(secrets: Dict[str, Any]) -> Dict[str, Any]:
    """Remove values a client echoed back rather than changed.

    The counterpart to :func:`mask_all_secret_values`. A client that GETs the
    masked secrets, edits one field and POSTs the whole object back is sending
    ``SECRET_MASK`` for every field it did not touch. Storing those would
    replace each untouched credential with eight bullet characters.

    Drops the mask and, like :func:`remove_empty_secrets`, blank values -- so
    the caller can merge the result onto what is already stored and have
    "unchanged" mean unchanged. Empty nested dicts are pruned.
    """
    result: Dict[str, Any] = {}
    for k, v in secrets.items():
        if isinstance(v, dict):
            nested = strip_masked_values(v)
            if nested:
                result[k] = nested
        elif isinstance(v, list):
            # A list is merged by replacement, not element by element -- there
            # is no identity to match entries on -- so a list that still holds
            # a mask cannot be merged safely: keeping it would store bullets,
            # and keeping the submitted entries alone would drop whichever the
            # client did not send back. Dropping the key leaves the stored
            # list untouched, which is what an untouched list should do.
            #
            # The consequence, deliberately: editing one secret inside a list
            # through this endpoint requires sending real values for all of
            # them. Sending some masks leaves the whole list as it was.
            if not _contains_mask(v):
                result[k] = v
        elif v is None:
            continue
        elif isinstance(v, str) and (v.strip() == '' or v == SECRET_MASK):
            continue
        else:
            result[k] = v
    return result


def _contains_mask(value: Any) -> bool:
    """True when a mask sentinel survives anywhere inside ``value``."""
    if isinstance(value, dict):
        return any(_contains_mask(v) for v in value.values())
    if isinstance(value, list):
        return any(_contains_mask(item) for item in value)
    return value == SECRET_MASK
