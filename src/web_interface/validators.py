"""
Input validation utilities for the web interface.
"""
from typing import Optional, Tuple, List
from pathlib import Path


def validate_file_upload(filename: str, max_size_mb: int = 10, 
                        allowed_extensions: Optional[List[str]] = None) -> Tuple[bool, Optional[str]]:
    """
    Validate file upload parameters.
    
    Args:
        filename: Name of the file
        max_size_mb: Maximum file size in MB
        allowed_extensions: List of allowed file extensions (e.g., ['.ttf', '.otf'])
    
    Returns:
        Tuple of (is_valid, error_message)
    """
    if not filename or not isinstance(filename, str):
        return False, "Filename must be a non-empty string"
    
    # Check for directory traversal
    if '..' in filename or '/' in filename or '\\' in filename:
        return False, "Filename contains invalid characters"
    
    # Check extension if specified. Both sides are lowercased: the caller's
    # list is as likely to hold '.TTF' as the filename is.
    if allowed_extensions:
        file_ext = Path(filename).suffix.lower()
        if file_ext not in [ext.lower() for ext in allowed_extensions]:
            return False, f"File extension must be one of: {', '.join(allowed_extensions)}"
    
    return True, None


def dedup_unique_arrays(cfg: dict, schema_node: dict) -> None:
    """Recursively deduplicate arrays with uniqueItems constraint.

    Walks the JSON Schema tree alongside the config dict and removes
    duplicate entries from any array whose schema specifies
    ``uniqueItems: true``, preserving insertion order (first occurrence
    kept).  Also recurses into:

    - Object properties containing nested objects or arrays
    - Array elements whose ``items`` schema is an object with its own
      properties (so nested uniqueItems constraints are enforced)

    This is intended to run **after** form-data normalisation but
    **before** JSON Schema validation, to prevent spurious validation
    failures when config merging introduces duplicates (e.g. a stock
    symbol already present in the saved config is submitted again from
    the web form).

    Args:
        cfg: The plugin configuration dict to mutate in-place.
        schema_node: The corresponding JSON Schema node (must contain
            a ``properties`` mapping at the current level).
    """
    props = schema_node.get('properties', {})
    for key, prop_schema in props.items():
        if key not in cfg:
            continue
        prop_type = prop_schema.get('type')
        if prop_type == 'array' and isinstance(cfg[key], list):
            # Deduplicate this array if uniqueItems is set
            if prop_schema.get('uniqueItems'):
                seen: list = []
                for item in cfg[key]:
                    if item not in seen:
                        seen.append(item)
                cfg[key] = seen
            # Recurse into array elements if items schema is an object
            items_schema = prop_schema.get('items', {})
            if isinstance(items_schema, dict) and items_schema.get('type') == 'object':
                for element in cfg[key]:
                    if isinstance(element, dict):
                        dedup_unique_arrays(element, items_schema)
        elif prop_type == 'object' and isinstance(cfg[key], dict):
            dedup_unique_arrays(cfg[key], prop_schema)

