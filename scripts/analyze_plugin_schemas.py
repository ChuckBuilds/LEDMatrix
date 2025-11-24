#!/usr/bin/env python3
"""
Analyze all plugin config schemas to identify issues:
- Duplicate fields
- Inconsistencies
- Missing common fields
- Naming variations
- Formatting issues
"""

import json
import os
from pathlib import Path
from typing import Dict, List, Set, Any
import jsonschema
from jsonschema import Draft7Validator

# Standard common fields that should be in all plugins
STANDARD_COMMON_FIELDS = {
    "enabled": {
        "type": "boolean",
        "default": False,
        "description": "Enable or disable this plugin",
        "required": True,
        "order": 1
    },
    "display_duration": {
        "type": "number",
        "default": 15,
        "minimum": 1,
        "maximum": 300,
        "description": "How long to display this plugin in seconds",
        "order": 2
    },
    "live_priority": {
        "type": "boolean",
        "default": False,
        "description": "Enable live priority takeover when plugin has live content",
        "order": 3
    },
    "high_performance_transitions": {
        "type": "boolean",
        "default": False,
        "description": "Use high-performance transitions (120 FPS) instead of standard (30 FPS)",
        "order": 4
    },
    "update_interval": {
        "type": "integer",
        "default": 60,
        "minimum": 1,
        "description": "How often to refresh data in seconds",
        "order": 5
    },
    "transition": {
        "type": "object",
        "order": 6
    }
}

def find_duplicate_fields(schema: Dict[str, Any], path: str = "") -> List[str]:
    """Find duplicate field definitions within a schema."""
    duplicates = []
    seen_fields = {}
    
    def check_properties(props: Dict[str, Any], current_path: str):
        if not isinstance(props, dict):
            return
        
        for key, value in props.items():
            full_path = f"{current_path}.{key}" if current_path else key
            if key in seen_fields:
                duplicates.append(f"Duplicate field '{key}' at {full_path} (also at {seen_fields[key]})")
            else:
                seen_fields[key] = full_path
            
            # Recursively check nested objects
            if isinstance(value, dict):
                if "properties" in value:
                    check_properties(value["properties"], full_path)
                elif "items" in value and isinstance(value["items"], dict):
                    if "properties" in value["items"]:
                        check_properties(value["items"]["properties"], f"{full_path}[items]")
    
    if "properties" in schema:
        check_properties(schema["properties"], "")
    
    return duplicates

def validate_schema_syntax(schema_path: Path) -> tuple[bool, List[str]]:
    """Validate JSON Schema syntax."""
    errors = []
    try:
        with open(schema_path, 'r', encoding='utf-8') as f:
            schema = json.load(f)
        
        # Validate schema structure
        Draft7Validator.check_schema(schema)
        return True, []
    except json.JSONDecodeError as e:
        return False, [f"JSON syntax error: {str(e)}"]
    except jsonschema.SchemaError as e:
        return False, [f"Schema validation error: {str(e)}"]
    except Exception as e:
        return False, [f"Error: {str(e)}"]

def analyze_schema(schema_path: Path) -> Dict[str, Any]:
    """Analyze a single schema file."""
    plugin_id = schema_path.parent.name
    analysis = {
        "plugin_id": plugin_id,
        "path": str(schema_path),
        "valid": False,
        "errors": [],
        "warnings": [],
        "has_title": False,
        "has_description": False,
        "common_fields": {},
        "missing_common_fields": [],
        "naming_issues": [],
        "duplicates": [],
        "property_order": [],
        "update_interval_variant": None
    }
    
    try:
        with open(schema_path, 'r', encoding='utf-8') as f:
            schema = json.load(f)
        
        # Check for title and description
        analysis["has_title"] = "title" in schema
        analysis["has_description"] = "description" in schema
        
        if not analysis["has_title"]:
            analysis["warnings"].append("Missing 'title' field at root level")
        if not analysis["has_description"]:
            analysis["warnings"].append("Missing 'description' field at root level")
        
        # Validate schema syntax
        is_valid, errors = validate_schema_syntax(schema_path)
        analysis["valid"] = is_valid
        analysis["errors"].extend(errors)
        
        if not is_valid:
            return analysis
        
        # Check for duplicate fields
        duplicates = find_duplicate_fields(schema)
        analysis["duplicates"] = duplicates
        
        # Check properties
        if "properties" not in schema:
            analysis["errors"].append("Missing 'properties' field")
            return analysis
        
        properties = schema["properties"]
        
        # Check common fields
        for field_name, field_spec in STANDARD_COMMON_FIELDS.items():
            if field_name in properties:
                analysis["common_fields"][field_name] = properties[field_name]
            else:
                # Check for variants
                if field_name == "update_interval":
                    # Check for update_interval_seconds variant
                    if "update_interval_seconds" in properties:
                        analysis["update_interval_variant"] = "update_interval_seconds"
                        analysis["naming_issues"].append(
                            f"Uses 'update_interval_seconds' instead of 'update_interval'"
                        )
                    else:
                        analysis["missing_common_fields"].append(field_name)
                else:
                    analysis["missing_common_fields"].append(field_name)
        
        # Check property order (enabled should be first)
        prop_keys = list(properties.keys())
        analysis["property_order"] = prop_keys
        
        if prop_keys and prop_keys[0] != "enabled":
            analysis["warnings"].append(
                f"'enabled' is not first property. First property is '{prop_keys[0]}'"
            )
        
        # Check for required fields
        required = schema.get("required", [])
        if "enabled" not in required:
            analysis["warnings"].append("'enabled' is not in required fields")
        
    except Exception as e:
        analysis["errors"].append(f"Failed to analyze schema: {str(e)}")
    
    return analysis

def main():
    """Main analysis function."""
    project_root = Path(__file__).parent.parent
    plugins_dir = project_root / "plugins"
    
    if not plugins_dir.exists():
        print(f"Plugins directory not found: {plugins_dir}")
        return
    
    results = []
    
    # Find all config_schema.json files
    schema_files = list(plugins_dir.glob("*/config_schema.json"))
    
    print(f"Found {len(schema_files)} plugin schemas to analyze\n")
    
    for schema_path in sorted(schema_files):
        print(f"Analyzing {schema_path.parent.name}...")
        analysis = analyze_schema(schema_path)
        results.append(analysis)
    
    # Print summary
    print("\n" + "="*80)
    print("ANALYSIS SUMMARY")
    print("="*80)
    
    for result in results:
        print(f"\n{result['plugin_id']}:")
        print(f"  Valid: {result['valid']}")
        
        if result['errors']:
            print(f"  Errors ({len(result['errors'])}):")
            for error in result['errors']:
                print(f"    - {error}")
        
        if result['warnings']:
            print(f"  Warnings ({len(result['warnings'])}):")
            for warning in result['warnings']:
                print(f"    - {warning}")
        
        if result['duplicates']:
            print(f"  Duplicates ({len(result['duplicates'])}):")
            for dup in result['duplicates']:
                print(f"    - {dup}")
        
        if result['missing_common_fields']:
            print(f"  Missing common fields: {', '.join(result['missing_common_fields'])}")
        
        if result['naming_issues']:
            print(f"  Naming issues:")
            for issue in result['naming_issues']:
                print(f"    - {issue}")
        
        if result['property_order'] and result['property_order'][0] != 'enabled':
            print(f"  Property order: First is '{result['property_order'][0]}' (should be 'enabled')")
    
    # Overall statistics
    print("\n" + "="*80)
    print("OVERALL STATISTICS")
    print("="*80)
    
    valid_count = sum(1 for r in results if r['valid'])
    has_title_count = sum(1 for r in results if r['has_title'])
    has_description_count = sum(1 for r in results if r['has_description'])
    enabled_first_count = sum(1 for r in results if r['property_order'] and r['property_order'][0] == 'enabled')
    total_errors = sum(len(r['errors']) for r in results)
    total_warnings = sum(len(r['warnings']) for r in results)
    total_duplicates = sum(len(r['duplicates']) for r in results)
    
    print(f"Total plugins: {len(results)}")
    print(f"Valid schemas: {valid_count}/{len(results)}")
    print(f"Has title: {has_title_count}/{len(results)}")
    print(f"Has description: {has_description_count}/{len(results)}")
    print(f"'enabled' first: {enabled_first_count}/{len(results)}")
    print(f"Total errors: {total_errors}")
    print(f"Total warnings: {total_warnings}")
    print(f"Total duplicates: {total_duplicates}")
    
    # Save detailed report
    report_path = project_root / "plugin_schema_analysis.json"
    with open(report_path, 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2)
    
    print(f"\nDetailed report saved to: {report_path}")

if __name__ == "__main__":
    main()

