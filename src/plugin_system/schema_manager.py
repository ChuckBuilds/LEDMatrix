"""
Schema Manager

Manages plugin configuration schemas with caching, validation, and reliable path resolution.
Provides utilities for extracting defaults, validating configurations, and managing schema lifecycle.
"""

import copy
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import jsonschema
from jsonschema import Draft7Validator, ValidationError


class SchemaManager:
    """
    Manages plugin configuration schemas with caching and validation.
    
    Features:
    - Schema loading and caching
    - Default value extraction from schemas
    - Configuration validation against schemas
    - Reliable path resolution for schema files
    - Cache invalidation on plugin changes
    """
    
    def __init__(self, plugins_dir: Optional[Path] = None, project_root: Optional[Path] = None, logger: Optional[logging.Logger] = None):
        """
        Initialize the Schema Manager.
        
        Args:
            plugins_dir: Base plugins directory path
            project_root: Project root directory path
            logger: Optional logger instance
        """
        self.logger = logger or logging.getLogger(__name__)
        self.plugins_dir = plugins_dir
        self.project_root = project_root or Path.cwd()
        
        # Schema cache: plugin_id -> schema dict
        self._schema_cache: Dict[str, Dict[str, Any]] = {}
        
        # Default config cache: plugin_id -> default config dict
        self._defaults_cache: Dict[str, Dict[str, Any]] = {}
    
    def get_schema_path(self, plugin_id: str) -> Optional[Path]:
        """
        Get the path to a plugin's config_schema.json file.
        
        Tries multiple locations in order:
        1. plugins_dir / plugin_id / config_schema.json
        2. PROJECT_ROOT / plugins / plugin_id / config_schema.json
        3. PROJECT_ROOT / plugin-repos / plugin_id / config_schema.json
        
        Args:
            plugin_id: Plugin identifier
            
        Returns:
            Path to schema file or None if not found
        """
        possible_paths = []
        
        # Try plugins_dir if set
        if self.plugins_dir:
            possible_paths.append(self.plugins_dir / plugin_id / 'config_schema.json')
        
        # Try standard locations relative to project root
        possible_paths.extend([
            self.project_root / 'plugins' / plugin_id / 'config_schema.json',
            self.project_root / 'plugin-repos' / plugin_id / 'config_schema.json',
        ])
        
        # Try case-insensitive directory matching
        for base_dir in [self.project_root / 'plugins', self.project_root / 'plugin-repos']:
            if base_dir.exists():
                for item in base_dir.iterdir():
                    if item.is_dir() and item.name.lower() == plugin_id.lower():
                        possible_paths.append(item / 'config_schema.json')
        
        # Try each path
        for path in possible_paths:
            if path.exists():
                self.logger.debug(f"Found schema for {plugin_id} at {path}")
                return path
        
        self.logger.warning(f"Schema file not found for plugin {plugin_id}")
        return None
    
    def load_schema(self, plugin_id: str, use_cache: bool = True) -> Optional[Dict[str, Any]]:
        """
        Load a plugin's configuration schema.
        
        Args:
            plugin_id: Plugin identifier
            use_cache: If True, return cached schema if available
            
        Returns:
            Schema dictionary or None if not found
        """
        # Check cache first
        if use_cache and plugin_id in self._schema_cache:
            return self._schema_cache[plugin_id]
        
        schema_path = self.get_schema_path(plugin_id)
        if not schema_path:
            return None
        
        try:
            with open(schema_path, 'r', encoding='utf-8') as f:
                schema = json.load(f)
            
            # Validate schema structure (basic check)
            if not isinstance(schema, dict):
                self.logger.error(f"Invalid schema format for {plugin_id}: not a dictionary")
                return None
            
            # Cache the schema
            self._schema_cache[plugin_id] = schema
            
            # Invalidate defaults cache when schema changes
            if plugin_id in self._defaults_cache:
                del self._defaults_cache[plugin_id]
            
            return schema
            
        except json.JSONDecodeError as e:
            self.logger.error(f"Invalid JSON in schema file for {plugin_id}: {e}")
            return None
        except Exception as e:
            self.logger.error(f"Error loading schema for {plugin_id}: {e}")
            return None
    
    def invalidate_cache(self, plugin_id: Optional[str] = None) -> None:
        """
        Invalidate schema cache for a plugin or all plugins.
        
        Args:
            plugin_id: Plugin identifier to invalidate, or None to clear all
        """
        if plugin_id:
            self._schema_cache.pop(plugin_id, None)
            self._defaults_cache.pop(plugin_id, None)
            self.logger.debug(f"Invalidated cache for plugin {plugin_id}")
        else:
            self._schema_cache.clear()
            self._defaults_cache.clear()
            self.logger.debug("Invalidated all schema caches")
    
    def extract_defaults_from_schema(self, schema: Dict[str, Any], prefix: str = '') -> Dict[str, Any]:
        """
        Recursively extract default values from a JSON Schema.
        
        Handles nested objects, arrays, and all schema types.
        
        Args:
            schema: JSON Schema dictionary
            prefix: Optional prefix for logging/debugging
            
        Returns:
            Dictionary of default values
        """
        defaults = {}
        
        # Handle schema with properties
        properties = schema.get('properties', {})
        if not properties:
            return defaults
        
        for key, prop_schema in properties.items():
            field_path = f"{prefix}.{key}" if prefix else key
            
            # If property has a default, use it
            if 'default' in prop_schema:
                defaults[key] = prop_schema['default']
                self.logger.debug(f"Found default for {field_path}: {prop_schema['default']}")
                continue
            
            # Handle nested objects
            if prop_schema.get('type') == 'object' and 'properties' in prop_schema:
                nested_defaults = self.extract_defaults_from_schema(prop_schema, field_path)
                if nested_defaults:
                    defaults[key] = nested_defaults
            
            # Handle arrays with object items
            elif prop_schema.get('type') == 'array' and 'items' in prop_schema:
                items_schema = prop_schema['items']
                if items_schema.get('type') == 'object' and 'properties' in items_schema:
                    # For arrays of objects, use empty array as default
                    # Individual objects will use their defaults when created
                    defaults[key] = []
                elif 'default' in items_schema:
                    # Array with default item value
                    defaults[key] = [items_schema['default']]
                else:
                    # Empty array as default
                    defaults[key] = []
            
            # For other types without defaults, don't add to defaults dict
            # This allows plugins to handle missing values as needed
        
        return defaults
    
    def generate_default_config(self, plugin_id: str, use_cache: bool = True) -> Dict[str, Any]:
        """
        Generate default configuration for a plugin from its schema.
        
        Args:
            plugin_id: Plugin identifier
            use_cache: If True, return cached defaults if available
            
        Returns:
            Dictionary of default configuration values
        """
        # Check cache first
        if use_cache and plugin_id in self._defaults_cache:
            return self._defaults_cache[plugin_id].copy()
        
        schema = self.load_schema(plugin_id, use_cache=use_cache)
        if not schema:
            # Return minimal defaults if no schema
            return {
                'enabled': False,
                'display_duration': 15
            }
        
        # Extract defaults from schema
        defaults = self.extract_defaults_from_schema(schema)
        
        # Ensure enabled is present (common required field)
        if 'enabled' not in defaults:
            defaults['enabled'] = schema.get('properties', {}).get('enabled', {}).get('default', False)
        
        # Ensure display_duration is present (common field)
        if 'display_duration' not in defaults:
            defaults['display_duration'] = schema.get('properties', {}).get('display_duration', {}).get('default', 15)
        
        # Cache the defaults
        self._defaults_cache[plugin_id] = defaults.copy()
        
        return defaults
    
    def validate_config_against_schema(self, config: Dict[str, Any], schema: Dict[str, Any], 
                                      plugin_id: Optional[str] = None) -> Tuple[bool, List[str]]:
        """
        Validate configuration against a JSON Schema.
        
        Uses jsonschema library for comprehensive validation.
        Automatically injects core plugin properties (enabled, display_duration, etc.)
        into the schema before validation to ensure they're always allowed.
        
        Args:
            config: Configuration dictionary to validate
            schema: JSON Schema dictionary
            plugin_id: Optional plugin ID for error messages
            
        Returns:
            Tuple of (is_valid, list_of_error_messages)
        """
        errors = []
        
        try:
            # Core plugin properties that should always be allowed
            # These are handled by the base plugin system and should not cause validation failures
            core_properties = {
                "enabled": {
                    "type": "boolean",
                    "description": "Enable or disable this plugin"
                },
                "display_duration": {
                    "type": "number",
                    "minimum": 1,
                    "maximum": 300,
                    "description": "How long to display this plugin in seconds"
                },
                "live_priority": {
                    "type": "boolean",
                    "description": "Enable live priority takeover when plugin has live content"
                },
                "high_performance_transitions": {
                    "type": "boolean",
                    "description": "Use high-performance transitions (120 FPS) instead of standard (30 FPS)"
                },
                "transition": {
                    "type": "object",
                    "description": "Transition configuration for this plugin"
                }
            }
            
            # Create a deep copy of the schema to modify (to avoid mutating the original)
            enhanced_schema = copy.deepcopy(schema)
            if "properties" not in enhanced_schema:
                enhanced_schema["properties"] = {}
            
            # Inject core properties if they're not already defined in the schema
            # This ensures core properties are always allowed even if not in the plugin's schema
            properties_added = []
            for prop_name, prop_def in core_properties.items():
                if prop_name not in enhanced_schema["properties"]:
                    enhanced_schema["properties"][prop_name] = copy.deepcopy(prop_def)
                    properties_added.append(prop_name)
            
            # Log if we added any core properties (for debugging)
            if properties_added and plugin_id:
                self.logger.debug(
                    f"Injected core properties into schema for {plugin_id}: {properties_added}"
                )
            
            # Create validator with enhanced schema
            validator = Draft7Validator(enhanced_schema)
            
            # Collect all validation errors
            for error in validator.iter_errors(config):
                error_msg = self._format_validation_error(error, plugin_id)
                errors.append(error_msg)
            
            # Check required fields
            required_fields = enhanced_schema.get('required', [])
            for field in required_fields:
                if field not in config:
                    errors.append(f"Missing required field: '{field}'")
            
            if errors:
                return False, errors
            
            return True, []
            
        except jsonschema.SchemaError as e:
            error_msg = f"Schema error{' for ' + plugin_id if plugin_id else ''}: {str(e)}"
            self.logger.error(error_msg)
            return False, [error_msg]
        
        except Exception as e:
            error_msg = f"Validation error{' for ' + plugin_id if plugin_id else ''}: {str(e)}"
            self.logger.error(error_msg)
            return False, [error_msg]
    
    def _format_validation_error(self, error: ValidationError, plugin_id: Optional[str] = None) -> str:
        """
        Format a validation error into a readable message.
        
        Args:
            error: ValidationError from jsonschema
            plugin_id: Optional plugin ID for context
            
        Returns:
            Formatted error message
        """
        path = '.'.join(str(p) for p in error.path)
        field_path = f"'{path}'" if path else "root"
        
        if error.validator == 'required':
            missing = error.validator_value
            return f"Field {field_path}: Missing required property '{missing}'"
        elif error.validator == 'type':
            expected = error.validator_value
            actual = type(error.instance).__name__
            return f"Field {field_path}: Expected type {expected}, got {actual}"
        elif error.validator == 'enum':
            allowed = error.validator_value
            return f"Field {field_path}: Value '{error.instance}' not in allowed values {allowed}"
        elif error.validator in ['minimum', 'maximum']:
            limit = error.validator_value
            return f"Field {field_path}: Value {error.instance} violates {error.validator} constraint ({limit})"
        elif error.validator in ['minLength', 'maxLength']:
            limit = error.validator_value
            return f"Field {field_path}: Length {len(error.instance)} violates {error.validator} constraint ({limit})"
        elif error.validator in ['minItems', 'maxItems']:
            limit = error.validator_value
            return f"Field {field_path}: Array length {len(error.instance)} violates {error.validator} constraint ({limit})"
        else:
            return f"Field {field_path}: {error.message}"
    
    def merge_with_defaults(self, config: Dict[str, Any], defaults: Dict[str, Any]) -> Dict[str, Any]:
        """
        Merge configuration with defaults, preserving user values.
        
        Args:
            config: User configuration
            defaults: Default values from schema
            
        Returns:
            Merged configuration with defaults applied where missing
        """
        merged = defaults.copy()
        
        def deep_merge(target: Dict[str, Any], source: Dict[str, Any]) -> None:
            """Recursively merge source into target."""
            for key, value in source.items():
                if key in target and isinstance(target[key], dict) and isinstance(value, dict):
                    deep_merge(target[key], value)
                else:
                    target[key] = value
        
        deep_merge(merged, config)
        return merged

