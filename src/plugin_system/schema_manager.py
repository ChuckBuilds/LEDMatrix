"""
Schema Manager

Manages plugin configuration schemas with caching, validation, and reliable path resolution.
Provides utilities for extracting defaults, validating configurations, and managing schema lifecycle.
"""

import copy
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import jsonschema
from jsonschema import Draft7Validator, ValidationError

from src.core_config_keys import CORE_CONFIG_KEYS
from src.element_style import expand_style_elements


def _renders_as_object(prop: Dict[str, Any]) -> bool:
    """``field_type == 'object'`` as ``plugin_config.html`` computes it.

    The template takes a type list's *first* entry, so ``["object", "null"]``
    is an object and ``["boolean", "object"]`` is a checkbox.
    """
    field_type = prop.get('type')
    if isinstance(field_type, list):
        field_type = field_type[0] if field_type else None
    return field_type == 'object'


def legacy_bool_as_object(value: Any, prop: Any) -> Any:
    """Read a boolean stored where the schema now has an ``{enabled, ...}`` object.

    Plugins turn an on/off switch into a settings object (news'
    ``global.dynamic_duration: true`` became ``{enabled, min_duration_seconds,
    ...}``), but config.json keeps the boolean until the user next saves that
    plugin's form. The boolean was the switch the object's ``enabled`` now
    holds, so it becomes ``{"enabled": value}`` and schema defaults fill the rest.

    Returns ``value`` itself when the rule does not apply: anything that is not
    a real ``bool`` (``1``, ``"true"``, ``None``) or a schema property that is not
    an object with an ``enabled`` child. Those stay as they are, so validation
    still reports a genuine mismatch.

    This is the rule ``render_nested_section`` in
    ``web_interface/templates/v3/partials/plugin_config.html`` applies when it
    draws the form. ``test/test_legacy_boolean_config.py`` renders that macro
    against this function, so change both together.
    """
    if not isinstance(value, bool) or not isinstance(prop, dict):
        return value
    properties = prop.get('properties')
    if (_renders_as_object(prop) and isinstance(properties, dict)
            and 'enabled' in properties):
        return {'enabled': value}
    return value


def normalize_legacy_booleans(config: Any, schema: Any,
                              changed_paths: Optional[List[str]] = None,
                              _prefix: str = '') -> Any:
    """Apply :func:`legacy_bool_as_object` at every depth of a plugin config.

    Walks the config along the schema's ``properties`` the way the settings
    form does: into nested objects, not into array items (the form hands arrays
    to widgets and never applies the rule there).

    Never mutates ``config``. Returns the same object when nothing changed, and
    otherwise copies only the dicts on the path to each upgraded value. When
    ``changed_paths`` is given, the dotted path of each upgraded value is
    appended to it.
    """
    if not isinstance(config, dict) or not isinstance(schema, dict):
        return config
    properties = schema.get('properties')
    if not isinstance(properties, dict):
        return config

    result = config
    for key, value in config.items():
        prop = properties.get(key)
        if not isinstance(prop, dict) or not _renders_as_object(prop):
            continue
        path = f"{_prefix}.{key}" if _prefix else key
        new_value = legacy_bool_as_object(value, prop)
        if new_value is not value:
            if changed_paths is not None:
                changed_paths.append(path)
        elif isinstance(value, dict):
            new_value = normalize_legacy_booleans(value, prop, changed_paths, path)
        if new_value is not value:
            if result is config:
                result = dict(config)
            result[key] = new_value
    return result


#: Per-plugin settings the **core** owns: it reads them out of each plugin's
#: config section, so they are allowed in every plugin's config whether or not
#: the plugin's schema declares them. The one list for validation, for the web
#: save filter and for the load-time checks -- a private copy is how JSON saves
#: came to drop the ``vegas_*`` keys while the validator accepted them.
#:
#: Values are the schema used when the plugin does not declare the property.
CORE_PLUGIN_PROPERTIES: Dict[str, Dict[str, Any]] = {
    # Defaults match BasePlugin behavior: enabled=True, display_duration=15,
    # live_priority=False.
    "enabled": {
        "type": "boolean",
        "default": True,
        "description": "Enable or disable this plugin"
    },
    "display_duration": {
        "type": "number",
        "default": 15,
        "minimum": 1,
        "maximum": 300,
        "description": "How long to display this plugin in seconds"
    },
    "live_priority": {
        "type": "boolean",
        "default": False,
        "description": "Enable live priority takeover when plugin has live content"
    },
    # Vegas tuning read by vegas_mode/plugin_adapter.py and base_plugin.py.
    # Left untyped: the adapter validates them itself and ignores a bad
    # value with a log line, so a stored one must never block a save.
    "vegas_width_pct": {
        "description": "Vegas mode: width of this plugin's card, as a percentage of the panel"
    },
    "vegas_overflow": {
        "description": "Vegas mode: 'rotate' or 'truncate' when this plugin's content overflows"
    },
    "vegas_max_width_screens": {
        "description": "Vegas mode: widest this plugin's card may be, in screens"
    },
}

#: The keys of CORE_PLUGIN_PROPERTIES that are Vegas tuning rather than plugin
#: state. PluginManager strips these before its soft validation (see
#: PluginManager.CORE_OWNED_CONFIG_KEYS).
CORE_VEGAS_TUNING_KEYS = frozenset({
    'vegas_width_pct', 'vegas_overflow', 'vegas_max_width_screens',
})


#: Per-plugin keys the core used to own and no longer reads. ``skin`` and
#: ``skin_options`` belonged to the skin system, which was removed; a
#: config.json written before then can still carry them in any plugin section,
#: and most plugin schemas set ``additionalProperties: false``. They are
#: dropped wherever a section is prepared (prepare_plugin_config) or validated,
#: and the web saves drop them from the stored section, so an old config loads
#: and saves without a validation error and loses them on its next save.
RETIRED_PLUGIN_KEYS = frozenset({'skin', 'skin_options'})


def drop_retired_plugin_keys(config: Any, schema: Any) -> Any:
    """``config`` without the RETIRED_PLUGIN_KEYS its plugin's schema leaves undeclared.

    A plugin whose schema declares one of these names owns it and keeps it;
    without a schema nothing is dropped. Never mutates ``config``, and returns
    it unchanged when there is nothing to drop.
    """
    if not isinstance(config, dict) or not isinstance(schema, dict) \
            or RETIRED_PLUGIN_KEYS.isdisjoint(config):
        return config
    declared = schema.get('properties')
    declared = declared if isinstance(declared, dict) else {}
    return {key: value for key, value in config.items()
            if key not in RETIRED_PLUGIN_KEYS or key in declared}


def with_core_plugin_properties(schema: Dict[str, Any]) -> Dict[str, Any]:
    """A deep copy of a plugin schema with CORE_PLUGIN_PROPERTIES allowed.

    Properties the plugin declares itself are left as declared. Core
    properties are removed from ``required``: they are system-managed.
    """
    enhanced = copy.deepcopy(schema) if isinstance(schema, dict) else {}
    properties = enhanced.setdefault("properties", {})
    for name, definition in CORE_PLUGIN_PROPERTIES.items():
        if name not in properties:
            properties[name] = copy.deepcopy(definition)
    if "required" in enhanced:
        enhanced["required"] = [field for field in enhanced["required"]
                                if field not in CORE_PLUGIN_PROPERTIES]
    return enhanced


def extract_schema_defaults(schema: Dict[str, Any]) -> Dict[str, Any]:
    """Default values of a JSON Schema's properties, recursively.

    A property's own ``default`` wins; otherwise a nested object contributes
    its children's defaults, and an array contributes ``[]`` (or a one-item
    list of its ``items`` default). This is what a device runs with, so the
    dev tools use it too (src/plugin_system/testing/loading.py).
    """
    defaults: Dict[str, Any] = {}
    properties = schema.get('properties', {}) if isinstance(schema, dict) else {}
    if not isinstance(properties, dict):
        return defaults

    for key, prop_schema in properties.items():
        if not isinstance(prop_schema, dict):
            continue
        # If property has a default, use it
        if 'default' in prop_schema:
            defaults[key] = prop_schema['default']
            continue

        # Handle nested objects
        if prop_schema.get('type') == 'object' and 'properties' in prop_schema:
            nested_defaults = extract_schema_defaults(prop_schema)
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


def plugin_config_defaults(schema: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Every default a plugin's config gets: the schema's plus the core ones.

    A plugin with no schema gets the minimal ``enabled: False,
    display_duration: 15``. Device location is not applied here; that needs a
    config manager (SchemaManager.generate_default_config).
    """
    if not schema:
        return {
            'enabled': False,
            'display_duration': 15
        }

    defaults = extract_schema_defaults(schema)

    # Ensure core properties have defaults (they may not be in the schema)
    # These match BasePlugin behavior
    for name in ('enabled', 'display_duration', 'live_priority'):
        if name not in defaults:
            defaults[name] = CORE_PLUGIN_PROPERTIES[name]['default']
    return defaults


def merge_config_defaults(config: Dict[str, Any], defaults: Dict[str, Any]) -> Dict[str, Any]:
    """Merge configuration with defaults, preserving user values.

    Also replaces None values with defaults so a config never starts with
    None where a default exists. Neither argument is mutated.
    """
    merged = copy.deepcopy(defaults)

    def deep_merge(target: Dict[str, Any], source: Dict[str, Any], default_dict: Dict[str, Any]) -> None:
        """Recursively merge source into target, replacing None with defaults."""
        for key, value in source.items():
            default_value = default_dict.get(key)

            if key in target and isinstance(target[key], dict) and isinstance(value, dict):
                # Both are dicts, recursively merge
                if isinstance(default_value, dict):
                    deep_merge(target[key], value, default_value)
                else:
                    deep_merge(target[key], value, {})
            elif value is None and default_value is not None:
                # Value is None and we have a default, use the default
                target[key] = copy.deepcopy(default_value) if isinstance(default_value, (dict, list)) else default_value
            else:
                # Normal merge: user value takes precedence (copy if dict/list)
                if isinstance(value, (dict, list)):
                    target[key] = copy.deepcopy(value)
                else:
                    target[key] = value

    deep_merge(merged, config, defaults)

    # Final pass: replace any remaining None values at any level with defaults
    def replace_none_with_defaults(target: Dict[str, Any], default_dict: Dict[str, Any]) -> None:
        """Recursively replace None values with defaults."""
        for key in list(target.keys()):
            value = target[key]
            default_value = default_dict.get(key)

            if value is None and default_value is not None:
                # Replace None with default
                target[key] = copy.deepcopy(default_value) if isinstance(default_value, (dict, list)) else default_value
            elif isinstance(value, dict) and isinstance(default_value, dict):
                # Recursively process nested dicts
                replace_none_with_defaults(value, default_value)

    replace_none_with_defaults(merged, defaults)
    return merged


def prepare_plugin_config(config: Any, schema: Optional[Dict[str, Any]],
                          defaults: Dict[str, Any],
                          changed_paths: Optional[List[str]] = None) -> Dict[str, Any]:
    """The config a plugin runs with, from its stored (or submitted) section.

    Retired core keys are dropped (drop_retired_plugin_keys), legacy booleans
    are read as ``{"enabled": ...}`` objects (normalize_legacy_booleans), then
    schema defaults fill in whatever is missing. Loading a plugin, both config
    saves, GET /plugins/config, hot reload and the dev tools all go through
    this, so a plugin sees the same shape however its config reached it.
    """
    config = config if isinstance(config, dict) else {}
    if schema:
        config = drop_retired_plugin_keys(config, schema)
        config = normalize_legacy_booleans(config, schema, changed_paths)
    return merge_config_defaults(config, defaults)


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
    
    # Plugin config keys that mean "where this device is". A plugin declaring
    # any of these in its schema gets the device-wide ``location`` block from
    # config.json as the *default* for that field, instead of whatever city the
    # plugin author happened to ship. A value the user set on the plugin itself
    # always wins -- this only ever replaces the schema default, so an explicit
    # per-plugin location is still honoured.
    #
    # Only these fully-namespaced keys are substituted. A bare ``state`` or
    # ``city`` key is deliberately left alone: plugins use those for unrelated
    # things (ledmatrix-elections' ``state`` is a two-letter code, not a place
    # name), and silently rewriting them would break those plugins.
    DEVICE_LOCATION_KEYS: Dict[str, str] = {
        'location_city': 'city',
        'location_state': 'state',
        'location_country': 'country',
    }

    def __init__(self, plugins_dir: Optional[Path] = None, project_root: Optional[Path] = None,
                 logger: Optional[logging.Logger] = None, config_manager: Optional[Any] = None):
        """
        Initialize the Schema Manager.
        
        Args:
            plugins_dir: Base plugins directory path
            project_root: Project root directory path
            logger: Optional logger instance
            config_manager: Optional config manager, used to resolve the
                device-wide ``location`` that seeds plugin location defaults.
                Omitting it simply leaves schema defaults untouched.
        """
        self.logger = logger or logging.getLogger(__name__)
        self.plugins_dir = plugins_dir
        self.project_root = project_root or Path.cwd()
        self.config_manager = config_manager
        
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

            # Expand any customization.x-style-elements declaration into the
            # full per-element style blocks (font/size/color + layout
            # offsets) the web-UI config form renders. No-op for schemas
            # without the declaration; never raises.
            schema = expand_style_elements(schema)

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

        See :func:`extract_schema_defaults`; ``prefix`` is accepted for
        compatibility and unused.
        """
        return extract_schema_defaults(schema)

    def get_device_location(self) -> Optional[Dict[str, Any]]:
        """
        Return the device-wide ``location`` block from config.json, or None.

        This is the City/State/Country the user sets once under General
        settings. Returns None when there is no config manager wired, the
        config can't be read, or no location has been configured.
        """
        if self.config_manager is None:
            return None
        try:
            config = self.config_manager.load_config()
        except Exception as e:
            # A config that can't be read must never stop defaults being
            # generated -- the plugin's own schema defaults still apply.
            self.logger.debug(f"Could not read device location from config: {e}")
            return None
        if not isinstance(config, dict):
            return None
        location = config.get('location')
        return location if isinstance(location, dict) else None

    def apply_device_location(self, defaults: Dict[str, Any]) -> Dict[str, Any]:
        """
        Replace location-shaped schema defaults with the device's own location.

        Without this, a plugin that ships ``"location_city": "Dallas"`` as its
        schema default silently reports Dallas weather (and centres its radar
        there) for every user who never opened that plugin's config form --
        even though they set their real city under General settings. The
        substituted value is still only a *default*: ``merge_with_defaults``
        lets any per-plugin value the user saved win over it.

        Mutates and returns ``defaults`` for convenience.
        """
        if not defaults:
            return defaults
        if not any(key in defaults for key in self.DEVICE_LOCATION_KEYS):
            return defaults

        location = self.get_device_location()
        if not location:
            return defaults

        for key, field in self.DEVICE_LOCATION_KEYS.items():
            if key not in defaults:
                continue
            value = location.get(field)
            # Only a non-empty string is a real answer; a blank or missing
            # field means "not configured", which leaves the schema default.
            if isinstance(value, str) and value.strip():
                defaults[key] = value.strip()

        return defaults

    def generate_default_config(self, plugin_id: str, use_cache: bool = True) -> Dict[str, Any]:
        """
        Generate default configuration for a plugin from its schema.
        
        Location fields (see ``DEVICE_LOCATION_KEYS``) default to the device's
        configured location rather than the plugin author's. That substitution
        is applied on the way out rather than being cached, so changing the
        device location takes effect without invalidating the defaults cache.
        
        Args:
            plugin_id: Plugin identifier
            use_cache: If True, return cached defaults if available
            
        Returns:
            Dictionary of default configuration values
        """
        # Check cache first
        if use_cache and plugin_id in self._defaults_cache:
            return self.apply_device_location(self._defaults_cache[plugin_id].copy())
        
        schema = self.load_schema(plugin_id, use_cache=use_cache)
        if not schema:
            # Return minimal defaults if no schema
            return plugin_config_defaults(None)

        # Schema defaults plus the core properties' (they may not be in the
        # schema)
        defaults = plugin_config_defaults(schema)

        # Cache the defaults *before* the device location is layered on, so a
        # later change to the device location is picked up by the next call.
        self._defaults_cache[plugin_id] = defaults.copy()
        
        return self.apply_device_location(defaults)
    
    def prepare_plugin_config(self, plugin_id: str, config: Any,
                              schema: Optional[Dict[str, Any]] = None,
                              changed_paths: Optional[List[str]] = None) -> Dict[str, Any]:
        """
        The config a plugin runs with: see :func:`prepare_plugin_config`.

        Args:
            plugin_id: Plugin identifier
            config: The plugin's stored or submitted config section
            schema: The plugin's schema, when the caller already has it
            changed_paths: Receives the dotted path of each legacy boolean
                read as an object

        Returns:
            A new dict; ``config`` is not mutated
        """
        if schema is None:
            schema = self.load_schema(plugin_id, use_cache=True)
        defaults = self.generate_default_config(plugin_id, use_cache=True)
        return prepare_plugin_config(config, schema, defaults, changed_paths)

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
            # Core plugin properties (CORE_PLUGIN_PROPERTIES) are handled by
            # the base plugin system and should not cause validation failures:
            # they are allowed even when the plugin's schema doesn't declare
            # them, and never required. Retired ones are ignored.
            config = drop_retired_plugin_keys(config, schema)
            enhanced_schema = with_core_plugin_properties(schema)
            if plugin_id:
                declared = schema.get("properties", {}) if isinstance(schema, dict) else {}
                self.logger.debug(
                    "Injected core properties into schema for %s: %s", plugin_id,
                    [name for name in CORE_PLUGIN_PROPERTIES if name not in declared]
                )

            # iter_errors reports every violation, including one ``required``
            # error per missing field at every depth.
            validator = Draft7Validator(enhanced_schema)
            for error in validator.iter_errors(config):
                errors.append(self._format_validation_error(error, plugin_id))

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
            # validator_value is the schema's whole ``required`` list; the
            # error itself is about one field, which jsonschema names only in
            # its message ("'api_key' is a required property").
            missing = next(
                (name for name in error.validator_value
                 if error.message.startswith(f"{name!r} ")),
                None)
            if missing is None:
                return f"Field {field_path}: {error.message}"
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
        Also replaces None values with defaults to ensure config never has None from the start.

        Args:
            config: User configuration
            defaults: Default values from schema

        Returns:
            Merged configuration with defaults applied where missing or None
        """
        return merge_config_defaults(config, defaults)

    def detect_config_key_collisions(
        self,
        plugin_ids: List[str]
    ) -> List[Dict[str, Any]]:
        """
        Detect config key collisions between plugins.

        Checks for:
        1. Plugin IDs that collide with reserved system config keys
        2. Plugin IDs that might cause confusion or conflicts

        Args:
            plugin_ids: List of plugin identifiers to check

        Returns:
            List of collision warnings, each containing:
            - type: 'reserved_key_collision' or 'case_collision'
            - plugin_id: The plugin ID involved
            - message: Human-readable warning message
        """
        collisions = []

        # Reserved top-level config keys that plugins should not use as IDs:
        # every core section (src/core_config_keys.py), plus a few names that
        # read as core even though no current section uses them.
        reserved_keys = set(CORE_CONFIG_KEYS) | {
            'display_modes', 'hardware', 'debug',
            'log_level', 'emulator', 'web_interface'
        }

        # Track plugin IDs for case collision detection
        lowercase_ids: Dict[str, str] = {}

        for plugin_id in plugin_ids:
            # Check reserved key collision
            if plugin_id.lower() in {k.lower() for k in reserved_keys}:
                collisions.append({
                    "type": "reserved_key_collision",
                    "plugin_id": plugin_id,
                    "message": f"Plugin ID '{plugin_id}' conflicts with reserved config key. "
                               f"This may cause configuration issues."
                })

            # Check for case-insensitive collisions between plugins
            lower_id = plugin_id.lower()
            if lower_id in lowercase_ids:
                existing_id = lowercase_ids[lower_id]
                if existing_id != plugin_id:
                    collisions.append({
                        "type": "case_collision",
                        "plugin_id": plugin_id,
                        "conflicting_id": existing_id,
                        "message": f"Plugin ID '{plugin_id}' may conflict with '{existing_id}' "
                                   f"on case-insensitive file systems."
                    })
            else:
                lowercase_ids[lower_id] = plugin_id

        return collisions

