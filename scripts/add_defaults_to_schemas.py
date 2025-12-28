#!/usr/bin/env python3
"""
Script to add default values to plugin config schemas where missing.

This ensures that configs never start with None values, improving user experience
and preventing validation errors.
"""

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


def get_default_for_field(prop: Dict[str, Any]) -> Any:
    """
    Determine a sensible default value for a field based on its type and constraints.
    
    Args:
        prop: Field property schema
        
    Returns:
        Default value or None if no default should be added
    """
    prop_type = prop.get('type')
    
    # Handle union types (array with multiple types)
    if isinstance(prop_type, list):
        # Use the first non-null type
        prop_type = next((t for t in prop_type if t != 'null'), prop_type[0] if prop_type else 'string')
    
    if prop_type == 'boolean':
        return False
    
    elif prop_type == 'number':
        # For numbers, use minimum if available, or a sensible default
        minimum = prop.get('minimum')
        maximum = prop.get('maximum')
        
        if minimum is not None:
            return minimum
        elif maximum is not None:
            # Use a reasonable fraction of max (like 30% or minimum 1)
            return max(1, int(maximum * 0.3))
        else:
            # No constraints, use 0
            return 0
    
    elif prop_type == 'integer':
        # Similar to number
        minimum = prop.get('minimum')
        maximum = prop.get('maximum')
        
        if minimum is not None:
            return minimum
        elif maximum is not None:
            return max(1, int(maximum * 0.3))
        else:
            return 0
    
    elif prop_type == 'string':
        # Only add default for strings if it makes sense
        # Check if there's an enum - use first value
        enum_values = prop.get('enum')
        if enum_values:
            return enum_values[0]
        
        # For optional string fields, empty string might be okay, but be cautious
        # We'll skip adding defaults for strings unless explicitly needed
        return None
    
    elif prop_type == 'array':
        # Empty array as default
        return []
    
    elif prop_type == 'object':
        # Empty object - but we'll handle nested objects separately
        return {}
    
    return None


def should_add_default(prop: Dict[str, Any], field_path: str) -> bool:
    """
    Determine if we should add a default value to this field.
    
    Args:
        prop: Field property schema
        field_path: Dot-separated path to the field
        
    Returns:
        True if default should be added
    """
    # Skip if already has a default
    if 'default' in prop:
        return False
    
    # Skip secret fields (they should be user-provided)
    if prop.get('x-secret', False):
        return False
    
    # Skip API keys and similar sensitive fields
    field_name = field_path.split('.')[-1].lower()
    sensitive_keywords = ['key', 'password', 'secret', 'token', 'auth', 'credential']
    if any(keyword in field_name for keyword in sensitive_keywords):
        return False
    
    prop_type = prop.get('type')
    if isinstance(prop_type, list):
        prop_type = next((t for t in prop_type if t != 'null'), prop_type[0] if prop_type else None)
    
    # Only add defaults for certain types
    if prop_type in ('boolean', 'number', 'integer', 'array'):
        return True
    
    # For strings, only if there's an enum
    if prop_type == 'string' and 'enum' in prop:
        return True
    
    return False


def add_defaults_recursive(schema: Dict[str, Any], path: str = "", modified: List[str] = None) -> bool:
    """
    Recursively add default values to schema fields.
    
    Args:
        schema: Schema dictionary to modify
        path: Current path in the schema (for logging)
        modified: List to track which fields were modified
        
    Returns:
        True if any modifications were made
    """
    if modified is None:
        modified = []
    
    if not isinstance(schema, dict) or 'properties' not in schema:
        return False
    
    changes_made = False
    
    for key, prop in schema['properties'].items():
        if not isinstance(prop, dict):
            continue
        
        current_path = f"{path}.{key}" if path else key
        
        # Check nested objects
        if prop.get('type') == 'object' and 'properties' in prop:
            if add_defaults_recursive(prop, current_path, modified):
                changes_made = True
        
        # Add default if appropriate
        if should_add_default(prop, current_path):
            default_value = get_default_for_field(prop)
            if default_value is not None:
                prop['default'] = default_value
                modified.append(current_path)
                changes_made = True
                print(f"  Added default to {current_path}: {default_value} (type: {prop.get('type')})")
    
    return changes_made


def process_schema_file(schema_path: Path) -> bool:
    """
    Process a single schema file to add defaults.
    
    Args:
        schema_path: Path to the schema file
        
    Returns:
        True if file was modified
    """
    print(f"\nProcessing: {schema_path}")
    
    try:
        with open(schema_path, 'r', encoding='utf-8') as f:
            schema = json.load(f)
    except Exception as e:
        print(f"  Error reading schema: {e}")
        return False
    
    modified_fields = []
    changes_made = add_defaults_recursive(schema, modified=modified_fields)
    
    if changes_made:
        # Write back with pretty formatting
        with open(schema_path, 'w', encoding='utf-8') as f:
            json.dump(schema, f, indent=2, ensure_ascii=False)
            f.write('\n')  # Add trailing newline
        
        print(f"  ✓ Modified {len(modified_fields)} fields")
        return True
    else:
        print(f"  ✓ No changes needed")
        return False


def main():
    """Main entry point."""
    project_root = Path(__file__).parent.parent
    plugins_dir = project_root / 'plugins'
    
    if not plugins_dir.exists():
        print(f"Error: Plugins directory not found: {plugins_dir}")
        sys.exit(1)
    
    # Find all config_schema.json files
    schema_files = list(plugins_dir.rglob('config_schema.json'))
    
    if not schema_files:
        print("No config_schema.json files found")
        sys.exit(0)
    
    print(f"Found {len(schema_files)} schema files")
    
    modified_count = 0
    for schema_file in sorted(schema_files):
        if process_schema_file(schema_file):
            modified_count += 1
    
    print(f"\n{'='*60}")
    print(f"Summary: Modified {modified_count} out of {len(schema_files)} schema files")
    print(f"{'='*60}")


if __name__ == '__main__':
    main()

