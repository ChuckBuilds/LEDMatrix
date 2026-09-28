"""Plugin configuration: reading, saving (with secrets split out), the
schema the form is built from, and resetting to defaults.

Routes decorate the shared `api_v3` Blueprint from the package `__init__`,
so their endpoint names do not depend on which module they live in.
"""
from web_interface.blueprints.api_v3 import (
    ErrorCode, _RENDERED_SECTION_FIELD, _SKIP_FIELD,
    _enhance_schema_with_core_properties, _non_plugin_id_error,
    _filter_config_by_schema, _get_schema_property,
    _hidden_array_item_property, _plugin_directory,
    _parse_form_value_with_schema, _schema_allows_null, _schema_type_is,
    _set_missing_booleans_to_false, _set_nested_value, api_v3, datetime,
    deep_merge, error_response, exception_error_response, find_secret_fields,
    json, jsonify, logger, merge_secrets, os, remove_empty_secrets, request,
    separate_secrets, success_response, validate_request_json,
)
from src.web_interface.config_arrays import coerce_array_shapes
from src.web_interface.validators import dedup_unique_arrays
import web_interface.blueprints.api_v3 as _pkg
# Read through the module rather than bound by value: tests patch these
# as module attributes, and a value binding would not see the patch.
# Several are also called from helpers that live in __init__, so the
# package is the only patch point that covers every caller.


@api_v3.route('/plugins/config', methods=['GET'])
def get_plugin_config():
    """Get plugin configuration"""
    try:
        if not api_v3.config_manager:
            return error_response(
                ErrorCode.SYSTEM_ERROR,
                'Config manager not initialized',
                status_code=500
            )

        plugin_id = request.args.get('plugin_id')
        if not plugin_id:
            return error_response(
                ErrorCode.INVALID_INPUT,
                'plugin_id required',
                context={'missing_params': ['plugin_id']},
                status_code=400
            )

        # Get plugin configuration from config manager
        main_config = api_v3.config_manager.load_config()
        plugin_config = main_config.get(plugin_id, {})

        # Merge with defaults from schema so form shows default values for
        # missing fields, reading legacy booleans as objects first: what the
        # plugin runs with, and what posts back through the JSON save
        schema_mgr = api_v3.schema_manager
        if schema_mgr:
            try:
                from src.plugin_system.schema_manager import prepare_plugin_config
                defaults = schema_mgr.generate_default_config(plugin_id, use_cache=True)
                plugin_config = prepare_plugin_config(
                    plugin_config, schema_mgr.load_schema(plugin_id, use_cache=True), defaults)
            except Exception as e:
                # Log but don't fail - defaults merge is best effort
                logger.warning("Could not merge defaults for %s: %s", plugin_id, e)

        # Special handling for of-the-day plugin: populate uploaded_files and categories from disk
        if plugin_id == 'of-the-day' or plugin_id == 'ledmatrix-of-the-day':
            # The manifest id is 'of-the-day'; the directory is usually
            # 'ledmatrix-of-the-day'.
            plugin_dir = (_plugin_directory('ledmatrix-of-the-day')
                          or _plugin_directory(plugin_id))
            if plugin_dir:
                data_dir = plugin_dir / 'of_the_day'
                if data_dir.exists():
                    # Scan for JSON files
                    uploaded_files = []
                    categories_from_files = {}

                    for json_file in data_dir.glob('*.json'):
                        try:
                            # Get file stats
                            stat = json_file.stat()

                            # Read JSON to count entries
                            with open(json_file, 'r', encoding='utf-8') as f:
                                json_data = json.load(f)
                                entry_count = len(json_data) if isinstance(json_data, dict) else 0

                            # Extract category name from filename
                            category_name = json_file.stem
                            filename = json_file.name

                            # Create file entry
                            file_entry = {
                                'id': category_name,
                                'category_name': category_name,
                                'filename': filename,
                                'original_filename': filename,
                                'path': f'of_the_day/{filename}',
                                'size': stat.st_size,
                                'uploaded_at': datetime.fromtimestamp(stat.st_mtime).isoformat() + 'Z',
                                'entry_count': entry_count
                            }
                            uploaded_files.append(file_entry)

                            # Create/update category entry if not in config
                            if category_name not in plugin_config.get('categories', {}):
                                display_name = category_name.replace('_', ' ').title()
                                categories_from_files[category_name] = {
                                    'enabled': False,  # Default to disabled, user can enable
                                    'data_file': f'of_the_day/{filename}',
                                    'display_name': display_name
                                }
                            else:
                                # Update with file info if needed
                                categories_from_files[category_name] = plugin_config['categories'][category_name]
                                # Ensure data_file is correct
                                categories_from_files[category_name]['data_file'] = f'of_the_day/{filename}'

                        except Exception as e:
                            logger.debug("Could not read json file: %s", e)
                            continue

                    # Update plugin_config with scanned files
                    if uploaded_files:
                        plugin_config['uploaded_files'] = uploaded_files

                    # Merge categories from files with existing config
                    # Start with existing categories (preserve user settings like enabled/disabled)
                    existing_categories = plugin_config.get('categories', {}).copy()

                    # Update existing categories with file info, add new ones from files
                    for cat_name, cat_data in categories_from_files.items():
                        if cat_name in existing_categories:
                            # Preserve existing enabled state and display_name, but update data_file path
                            existing_categories[cat_name]['data_file'] = cat_data['data_file']
                            if 'display_name' not in existing_categories[cat_name] or not existing_categories[cat_name]['display_name']:
                                existing_categories[cat_name]['display_name'] = cat_data['display_name']
                        else:
                            # Add new category from file (default to disabled)
                            existing_categories[cat_name] = cat_data

                    if existing_categories:
                        plugin_config['categories'] = existing_categories

                    # Update category_order to include all categories
                    category_order = plugin_config.get('category_order', []).copy()
                    all_category_names = set(existing_categories.keys())
                    for cat_name in all_category_names:
                        if cat_name not in category_order:
                            category_order.append(cat_name)
                    if category_order:
                        plugin_config['category_order'] = category_order

        # If no config exists, return defaults
        if not plugin_config:
            plugin_config = {
                'enabled': True,
                'display_duration': 30
            }

        return success_response(data=plugin_config)
    except Exception as e:
        return exception_error_response(e, ErrorCode.CONFIG_LOAD_FAILED)


@api_v3.route('/plugins/config', methods=['POST'])
def save_plugin_config():
    """Save plugin configuration, separating secrets from regular config"""
    try:
        if not api_v3.config_manager:
            return error_response(
                ErrorCode.SYSTEM_ERROR,
                'Config manager not initialized',
                status_code=500
            )

        # Support both JSON and form data (for HTMX submissions)
        content_type = request.content_type or ''

        if 'application/json' in content_type:
            # JSON request
            data, error = validate_request_json(['plugin_id'])
            if error:
                return error
            plugin_id = data['plugin_id']
            submitted_config = data.get('config', {})
            if not isinstance(submitted_config, dict):
                return error_response(
                    ErrorCode.INVALID_INPUT,
                    'config must be a JSON object',
                    status_code=400
                )
            plugin_config = _merge_onto_stored_plugin_config(plugin_id, submitted_config)
        else:
            # Form data (HTMX submission)
            # plugin_id comes from query string, config from form fields
            plugin_id = request.args.get('plugin_id')
            if not plugin_id:
                return error_response(
                    ErrorCode.INVALID_INPUT,
                    'plugin_id required in query string',
                    status_code=400
                )

            # Load existing config as base (partial form updates should merge, not replace)
            existing_config = {}
            if api_v3.config_manager:
                full_config = api_v3.config_manager.load_config()
                existing_config = full_config.get(plugin_id, {}).copy()

            # Get schema manager instance (needed for type conversion)
            schema_mgr = api_v3.schema_manager
            if not schema_mgr:
                return error_response(
                    ErrorCode.SYSTEM_ERROR,
                    'Schema manager not initialized',
                    status_code=500
                )

            # Load plugin schema BEFORE processing form data (needed for type conversion)
            schema = schema_mgr.load_schema(plugin_id, use_cache=False)

            # Start with existing config and apply form updates
            plugin_config = existing_config

            # Convert form data to config dict
            # Form fields can use dot notation for nested values (e.g., "transition.type")
            form_data = request.form.to_dict()
            # Meta fields describe the submission, they are not config paths.
            # Unknown keys are otherwise written straight into config.json by
            # the non-indexed pass below.
            form_data = {k: v for k, v in form_data.items()
                         if not k.startswith('__')}

            # First pass: handle bracket notation array fields (e.g., "field_name[]" from checkbox-group)
            # These fields use getlist() to preserve all values, then replace in form_data
            # Sentinel empty value ("") allows clearing array to [] when all checkboxes unchecked
            bracket_array_fields = {}  # Maps base field path to list of values
            for key in request.form.keys():
                # Check if key ends with "[]" (bracket notation for array fields)
                if key.endswith('[]'):
                    base_path = key[:-2]  # Remove "[]" suffix
                    values = request.form.getlist(key)
                    # Filter out sentinel empty string - if only sentinel present, array should be []
                    # If sentinel + values present, use the actual values
                    filtered_values = [v for v in values if v and v.strip()]
                    # If no non-empty values but key exists, it means all checkboxes unchecked (empty array)
                    bracket_array_fields[base_path] = filtered_values
                    # Remove the bracket notation key from form_data if present
                    if key in form_data:
                        del form_data[key]
            
            # Process bracket notation fields and set directly in plugin_config
            # Use JSON encoding instead of comma-join to handle values containing commas
            for base_path, values in bracket_array_fields.items():
                # Get schema property to verify it's an array
                base_prop = _get_schema_property(schema, base_path)
                if base_prop and base_prop.get('type') == 'array':
                    # Filter out empty values and sentinel empty strings
                    filtered_values = [v for v in values if v and v.strip()]
                    # Set directly in plugin_config (values are already strings, no need to parse)
                    # Empty array (all unchecked) is represented as []
                    _set_nested_value(plugin_config, base_path, filtered_values)
                    logger.debug(f"Processed bracket notation array field {base_path}: {values} -> {filtered_values}")
                    # Remove from form_data to avoid double processing
                    if base_path in form_data:
                        del form_data[base_path]

            # Second pass: detect and combine array index fields (e.g., "text_color.0", "text_color.1" -> "text_color" as array)
            # This handles cases where forms send array fields as indexed inputs
            array_fields = {}  # Maps base field path to list of (index, value) tuples
            processed_keys = set()
            indexed_base_paths = set()  # Track which base paths have indexed fields

            for key, value in form_data.items():
                # Check if this looks like an array index field (ends with .0, .1, .2, etc.)
                if '.' in key:
                    parts = key.rsplit('.', 1)  # Split on last dot
                    if len(parts) == 2:
                        base_path, last_part = parts
                        # Check if last part is a numeric string (array index)
                        if last_part.isdigit():
                            # Get schema property for the base path to verify it's an array
                            base_prop = _get_schema_property(schema, base_path)
                            if base_prop and _schema_type_is(base_prop, 'array'):
                                # This is an array index field
                                index = int(last_part)
                                if base_path not in array_fields:
                                    array_fields[base_path] = []
                                array_fields[base_path].append((index, value))
                                processed_keys.add(key)
                                indexed_base_paths.add(base_path)
                                continue

            # Process combined array fields
            for base_path, index_values in array_fields.items():
                # Sort by index and extract values
                index_values.sort(key=lambda x: x[0])
                values = [v for _, v in index_values]
                # Every channel blank on a nullable field means "unset", not
                # an empty array: joining them would produce ", , ", which
                # parses to [] and then fails the minItems the array
                # declares. This is how a per-mode colour override says
                # "inherit the base colour".
                base_prop_for_null = _get_schema_property(schema, base_path)
                if (_schema_allows_null(base_prop_for_null)
                        and all(str(v).strip() == '' for v in values)):
                    _set_nested_value(plugin_config, base_path, None)
                    continue
                # Combine values into comma-separated string for parsing
                combined_value = ', '.join(str(v) for v in values)
                # Parse as array using schema
                parsed_value = _parse_form_value_with_schema(combined_value, base_path, schema)
                # Debug logging
                logger.debug(f"Combined indexed array field {base_path}: {values} -> {combined_value} -> {parsed_value}")
                # Only set if not skipped
                if parsed_value is not _SKIP_FIELD:
                    _set_nested_value(plugin_config, base_path, parsed_value)
            
            # Process remaining (non-indexed) fields
            # Skip any base paths that were processed as indexed arrays
            for key, value in form_data.items():
                if key not in processed_keys:
                    # Skip if this key is a base path that was processed as indexed array
                    # (to avoid overwriting the combined array with a single value)
                    if key not in indexed_base_paths:
                        # Parse value using schema to determine correct type
                        parsed_value = _SKIP_FIELD
                        decoded = False
                        # A hidden property inside an array row is carried
                        # through the form JSON-encoded (a posted row replaces
                        # the stored item, so it must be posted at all). Decode
                        # it exactly: the generic parse would turn an id "1"
                        # into the integer 1 and fail validation.
                        if _hidden_array_item_property(schema, key) is not None:
                            try:
                                parsed_value = json.loads(value)
                                decoded = True
                            except (TypeError, ValueError):
                                pass
                        if not decoded:
                            parsed_value = _parse_form_value_with_schema(value, key, schema)
                        # Debug logging for array fields
                        if schema:
                            prop = _get_schema_property(schema, key)
                            if prop and prop.get('type') == 'array':
                                logger.debug(f"Array field {key}: form value='{value}' -> parsed={parsed_value}")
                        # Use helper to set nested values correctly (skips if _SKIP_FIELD)
                        if parsed_value is not _SKIP_FIELD:
                            _set_nested_value(plugin_config, key, parsed_value)
            
            # Before the booleans below: that walk replaces anything it
            # expects to be a list and finds is not one.
            if schema and 'properties' in schema:
                coerce_array_shapes(plugin_config, schema['properties'],
                                    short_lists_take_default=True)

            # Fix unchecked boolean checkboxes: HTML checkboxes don't submit values
            # when unchecked, so the existing config value (potentially True) persists.
            # Walk the schema and set any boolean fields missing from form data to False.
            if schema and 'properties' in schema:
                form_keys = set(request.form.keys())
                # The rendered form reports which top-level sections it drew, so
                # an unchecked box can be told apart from a field the caller
                # never had in front of it. A caller that sends none gets the
                # evidence-based fallback in _boolean_is_in_scope.
                rendered_sections = set(request.form.getlist(_RENDERED_SECTION_FIELD))
                _set_missing_booleans_to_false(
                    plugin_config, schema['properties'], form_keys,
                    sections=rendered_sections or None)

        # Get schema manager instance (for JSON requests)
        schema_mgr = api_v3.schema_manager
        if not schema_mgr:
            return error_response(
                ErrorCode.SYSTEM_ERROR,
                'Schema manager not initialized',
                status_code=500
            )

        # Load plugin schema using SchemaManager (force refresh to get latest schema)
        # For JSON requests, schema wasn't loaded yet
        if 'application/json' in content_type:
            schema = schema_mgr.load_schema(plugin_id, use_cache=False)

        regular_config, secrets_config, error = _prepare_plugin_config_for_save(
            plugin_id, plugin_config, schema, schema_mgr,
            is_json='application/json' in content_type)
        if error:
            return error

        # Get current configs
        current_config = api_v3.config_manager.load_config()
        current_secrets = api_v3.config_manager.get_raw_file_content('secrets')

        # Deep merge plugin configuration in main config (preserves nested structures)
        if plugin_id not in current_config:
            current_config[plugin_id] = {}

        # Retired core keys (skin, skin_options) leave the stored section here
        from src.plugin_system.schema_manager import drop_retired_plugin_keys
        current_config[plugin_id] = deep_merge(
            drop_retired_plugin_keys(current_config[plugin_id], schema), regular_config)

        # Deep merge plugin secrets in secrets config
        if secrets_config:
            if plugin_id not in current_secrets:
                current_secrets[plugin_id] = {}
            # See above -- secrets lists must merge element-wise.
            current_secrets[plugin_id] = merge_secrets(
                current_secrets[plugin_id], secrets_config)
            # Save secrets file
            try:
                api_v3.config_manager.save_raw_file_content('secrets', current_secrets)
            except PermissionError as e:
                secrets_path = api_v3.config_manager.secrets_path
                secrets_dir = os.path.dirname(secrets_path) if secrets_path else None
                
                # Check permissions
                dir_readable = os.access(secrets_dir, os.R_OK) if secrets_dir and os.path.exists(secrets_dir) else False
                dir_writable = os.access(secrets_dir, os.W_OK) if secrets_dir and os.path.exists(secrets_dir) else False
                file_writable = os.access(secrets_path, os.W_OK) if secrets_path and os.path.exists(secrets_path) else False
                
                logger.error(
                    f"Permission error saving secrets config for {plugin_id}: {e}\n"
                    f"Secrets path: {secrets_path}\n"
                    f"Directory readable: {dir_readable}, writable: {dir_writable}\n"
                    f"File writable: {file_writable}",
                    exc_info=True
                )
                return error_response(
                    ErrorCode.CONFIG_SAVE_FAILED,
                    f"Failed to save secrets configuration: Permission denied. Check file permissions on {secrets_path}",
                    status_code=500
                )
            except Exception:
                secrets_path = api_v3.config_manager.secrets_path
                # Logs the file path, not any secret value.
                logger.error("Error saving secrets config for %s (path=%s)", plugin_id, secrets_path, exc_info=True)  # nosemgrep
                return error_response(
                    ErrorCode.CONFIG_SAVE_FAILED,
                    "Failed to save secrets configuration; see logs for details",
                    status_code=500
                )

        # Save the updated main config using atomic save
        success, error_msg = _pkg._save_config_atomic(api_v3.config_manager, current_config, create_backup=True)
        if not success:
            return error_response(
                ErrorCode.CONFIG_SAVE_FAILED,
                f"Failed to save configuration: {error_msg}",
                status_code=500
            )

        # If the plugin is loaded, notify it of the config change with merged config
        try:
            if api_v3.plugin_manager:
                plugin_instance = api_v3.plugin_manager.get_plugin(plugin_id)
                if plugin_instance:
                    # Reload merged config (includes secrets) and pass the plugin-specific section
                    merged_config = api_v3.config_manager.load_config()
                    plugin_full_config = _pkg._prepared_plugin_config(
                        plugin_id, merged_config.get(plugin_id, {}))
                    if hasattr(plugin_instance, 'on_config_change'):
                        plugin_instance.on_config_change(plugin_full_config)

                    # Update plugin state manager and call lifecycle methods based on enabled state
                    # This ensures the plugin state is synchronized with the config
                    enabled = plugin_full_config.get('enabled', plugin_instance.enabled)

                    # Update state manager if available
                    if api_v3.plugin_state_manager:
                        api_v3.plugin_state_manager.set_plugin_enabled(plugin_id, enabled)

                    # Call lifecycle methods to ensure plugin state matches config
                    try:
                        if enabled:
                            if hasattr(plugin_instance, 'on_enable'):
                                plugin_instance.on_enable()
                        else:
                            if hasattr(plugin_instance, 'on_disable'):
                                plugin_instance.on_disable()
                    except Exception as lifecycle_error:
                        # Log the error but don't fail the save - config is already saved
                        logger.warning("Lifecycle method error for %s: %s", plugin_id, lifecycle_error, exc_info=True)
        except Exception as hook_err:
            # Do not fail the save if hook fails; just log
            logger.warning("on_config_change failed: %s", hook_err)

        secret_count = len(secrets_config)
        message = f'Plugin {plugin_id} configuration saved successfully'
        if secret_count > 0:
            message += f' ({secret_count} secret field(s) saved to config_secrets.json)'

        return success_response(message=message)
    except Exception as e:
        if api_v3.operation_history:
            api_v3.operation_history.record_operation(
                "configure",
                plugin_id=data.get('plugin_id') if 'data' in locals() else None,
                status="failed",
                error=str(e)
            )
        return exception_error_response(e, ErrorCode.CONFIG_SAVE_FAILED)


def _merge_onto_stored_plugin_config(plugin_id, submitted_config, current_config=None):
    """A JSON plugin-config body merged onto the plugin's stored section.

    A JSON body carries the settings being changed, as a form post does: the
    rest keep their stored values. Built from defaults alone, a body such as
    ``{"enabled": true}`` reset every unsent setting of the plugin.
    ``current_config`` is the loaded main config when the caller has one.
    """
    import copy
    if current_config is None:
        current_config = api_v3.config_manager.load_config()
    stored = current_config.get(plugin_id)
    base = copy.deepcopy(stored) if isinstance(stored, dict) else {}
    return deep_merge(base, submitted_config)


def _prepare_plugin_config_for_save(plugin_id, plugin_config, schema, schema_mgr, is_json):
    """Turn a submitted plugin config into what gets stored.

    Fixes array shapes, keeps the enabled state, reads legacy booleans and
    applies schema defaults, normalizes value types, filters to the schema plus
    the core-owned per-plugin properties, validates, and splits out secrets.
    Shared by POST /plugins/config and plugin sections posted to
    POST /config/main, so both store the same thing.

    Returns ``(regular_config, secrets_config, None)``, or
    ``(None, None, error_response)`` when validation fails.
    """
    # The form path has already done this, before its checkbox pass.
    if is_json and schema and 'properties' in schema:
        coerce_array_shapes(plugin_config, schema['properties'])

    # PRE-PROCESSING: Preserve 'enabled' state if not in request
    # This prevents overwriting the enabled state when saving config from a form that doesn't include the toggle
    if 'enabled' not in plugin_config:
        try:
            current_config = api_v3.config_manager.load_config()
            if plugin_id in current_config and 'enabled' in current_config[plugin_id]:
                plugin_config['enabled'] = current_config[plugin_id]['enabled']
            elif api_v3.plugin_manager:
                # Fallback to plugin instance if config doesn't have it
                plugin_instance = api_v3.plugin_manager.get_plugin(plugin_id)
                if plugin_instance:
                    plugin_config['enabled'] = plugin_instance.enabled
            # Final fallback: default to True if plugin is loaded (matches BasePlugin default)
            if 'enabled' not in plugin_config:
                plugin_config['enabled'] = True
        except Exception as e:
            logger.debug("Error preserving enabled state: %s", e)
            # Default to True on error to avoid disabling plugins
            plugin_config['enabled'] = True

    # Find secret fields (supports nested schemas and array-item secrets)
    secret_fields = set()

    if schema and 'properties' in schema:
        secret_fields = find_secret_fields(schema['properties'])

    # Apply defaults from schema to config BEFORE validation
    # This ensures required fields with defaults are present before validation
    # Store preserved enabled value before merge to protect it from defaults
    preserved_enabled = None
    if 'enabled' in plugin_config:
        preserved_enabled = plugin_config['enabled']

    if schema:
        # Legacy booleans read as {"enabled": ...} objects first (#588), the
        # same preparation loading applies, so a config the device reads
        # without complaint also saves.
        from src.plugin_system.schema_manager import prepare_plugin_config
        defaults = schema_mgr.generate_default_config(plugin_id, use_cache=True)
        plugin_config = prepare_plugin_config(plugin_config, schema, defaults)

    # The defaults merge replaces a None only where the schema has a default,
    # so an array the client sent as None, or left out, can still be one here.
    def _fix_none_arrays(cfg, props):
        for k, pschema in props.items():
            if pschema.get('type') == 'array':
                if isinstance(cfg, dict) and (k not in cfg or cfg[k] is None):
                    cfg[k] = pschema.get('default', [])
            elif pschema.get('type') == 'object' and 'properties' in pschema:
                if isinstance(cfg, dict) and isinstance(cfg.get(k), dict):
                    _fix_none_arrays(cfg[k], pschema['properties'])

    if schema and 'properties' in schema and isinstance(plugin_config, dict):
        _fix_none_arrays(plugin_config, schema['properties'])

    # Ensure enabled state is preserved after defaults merge
    # Defaults should not overwrite an explicitly preserved enabled value
    if preserved_enabled is not None:
        # Restore preserved value if it was changed by defaults merge
        if plugin_config.get('enabled') != preserved_enabled:
            plugin_config['enabled'] = preserved_enabled

    # Normalize config data: convert string numbers to integers/floats where schema expects numbers
    # This handles form data which sends everything as strings
    def normalize_config_values(config, schema_props, prefix=''):
        """Recursively normalize config values based on schema types"""
        if not isinstance(config, dict) or not isinstance(schema_props, dict):
            return config

        normalized = {}
        for key, value in config.items():
            field_path = f"{prefix}.{key}" if prefix else key

            if key not in schema_props:
                # Field not in schema, keep as-is (will be caught by additionalProperties check if needed)
                normalized[key] = value
                continue

            prop_schema = schema_props[key]
            prop_type = prop_schema.get('type')

            # Handle union types (e.g., ["integer", "null"])
            if isinstance(prop_type, list):
                # Check if null is allowed and value is empty/null
                if 'null' in prop_type:
                    # Handle various representations of null/empty
                    if value is None:
                        normalized[key] = None
                        continue
                    elif isinstance(value, str):
                        # Strip whitespace and check for null representations
                        value_stripped = value.strip()
                        if value_stripped == '' or value_stripped.lower() in ('null', 'none', 'undefined'):
                            normalized[key] = None
                            continue

                # Try to normalize based on non-null types in the union
                # Check integer first (more specific than number)
                if 'integer' in prop_type:
                    if isinstance(value, str):
                        try:
                            normalized[key] = int(value.strip())
                            continue
                        except (ValueError, TypeError, OverflowError):
                            pass
                    elif isinstance(value, (int, float)):
                        normalized[key] = int(value)
                        continue

                # Check number (less specific, but handles floats)
                if 'number' in prop_type:
                    if isinstance(value, str):
                        try:
                            normalized[key] = float(value.strip())
                            continue
                        except (ValueError, TypeError, OverflowError):
                            pass
                    elif isinstance(value, (int, float)):
                        normalized[key] = float(value)
                        continue

                # Check boolean
                if 'boolean' in prop_type:
                    if isinstance(value, str):
                        normalized[key] = value.strip().lower() in ('true', '1', 'on', 'yes')
                        continue

                # The scalar conversions above are the only ones this branch
                # knows, so a union naming a structural or string type fell
                # through here even when the value already matched it --
                # customization.modes.<mode> makes every override nullable
                # (see element_style._nullable), so every per-mode colour is
                # ['array', 'null'] and warned on a perfectly valid [r, g, b].
                # Worse than the noise: `continue` skipped the single-type
                # handling below, so a nullable array never had its items
                # normalized and form-posted ["0", "249", "0"] stayed strings
                # where a plain 'array' field would have become ints. Re-enter
                # that handling with the matched member instead.
                if isinstance(value, list) and 'array' in prop_type:
                    prop_type = 'array'
                elif isinstance(value, dict) and 'object' in prop_type:
                    prop_type = 'object'
                elif isinstance(value, str) and 'string' in prop_type:
                    normalized[key] = value
                    continue
                else:
                    # Nothing converted: keep the value for validation to report.
                    logger.warning(f"Could not normalize field {field_path}: value={repr(value)}, type={type(value)}, schema_type={prop_type}")
                    normalized[key] = value
                    continue

            if isinstance(value, dict) and prop_type == 'object' and 'properties' in prop_schema:
                # Recursively normalize nested objects
                normalized[key] = normalize_config_values(value, prop_schema['properties'], field_path)
            elif isinstance(value, list) and prop_type == 'array' and 'items' in prop_schema:
                # Normalize array items
                items_schema = prop_schema['items']
                item_type = items_schema.get('type')

                # Handle union types in array items
                if isinstance(item_type, list):
                    normalized_array = []
                    for v in value:
                        # Check if null is allowed
                        if 'null' in item_type:
                            if v is None or v == '' or (isinstance(v, str) and v.lower() in ('null', 'none')):
                                normalized_array.append(None)
                                continue

                        # Try to normalize based on non-null types
                        if 'integer' in item_type:
                            if isinstance(v, str):
                                try:
                                    normalized_array.append(int(v))
                                    continue
                                except (ValueError, TypeError, OverflowError):
                                    pass
                            elif isinstance(v, (int, float)):
                                # Only a genuinely integral value converts.
                                # int(2.5) == 2 would store a silently
                                # corrected number where the client sent a
                                # wrong one; leaving it lets the validator
                                # reject it. A whole float (2.0 out of JSON)
                                # is integral and still converts.
                                if isinstance(v, int) or float(v).is_integer():
                                    normalized_array.append(int(v))
                                else:
                                    normalized_array.append(v)
                                continue
                        elif 'number' in item_type:
                            if isinstance(v, str):
                                try:
                                    normalized_array.append(float(v))
                                    continue
                                except (ValueError, TypeError, OverflowError):
                                    pass
                            elif isinstance(v, (int, float)):
                                normalized_array.append(float(v))
                                continue

                        # If no conversion worked, keep original value
                        normalized_array.append(v)
                    normalized[key] = normalized_array
                elif item_type == 'integer':
                    # Convert string numbers to integers
                    normalized_array = []
                    for v in value:
                        if isinstance(v, str):
                            try:
                                normalized_array.append(int(v))
                            except (ValueError, TypeError, OverflowError):
                                normalized_array.append(v)
                        elif isinstance(v, (int, float)):
                            # Integral only -- see the union branch above.
                            if isinstance(v, int) or float(v).is_integer():
                                normalized_array.append(int(v))
                            else:
                                normalized_array.append(v)
                        else:
                            normalized_array.append(v)
                    normalized[key] = normalized_array
                elif item_type == 'number':
                    # Convert string numbers to floats
                    normalized_array = []
                    for v in value:
                        if isinstance(v, str):
                            try:
                                normalized_array.append(float(v))
                            except (ValueError, TypeError, OverflowError):
                                normalized_array.append(v)
                        else:
                            normalized_array.append(v)
                    normalized[key] = normalized_array
                elif item_type == 'object' and 'properties' in items_schema:
                    # Recursively normalize array of objects
                    normalized_array = []
                    for v in value:
                        if isinstance(v, dict):
                            normalized_array.append(
                                normalize_config_values(v, items_schema['properties'], f"{field_path}[]")
                            )
                        else:
                            normalized_array.append(v)
                    normalized[key] = normalized_array
                else:
                    normalized[key] = value
            elif prop_type == 'integer':
                # Convert string to integer
                if isinstance(value, str):
                    try:
                        normalized[key] = int(value)
                    except (ValueError, TypeError, OverflowError):
                        normalized[key] = value
                else:
                    normalized[key] = value
            elif prop_type == 'number':
                # Convert string to float
                if isinstance(value, str):
                    try:
                        normalized[key] = float(value)
                    except (ValueError, TypeError, OverflowError):
                        normalized[key] = value
                else:
                    normalized[key] = value
            elif prop_type == 'boolean':
                # Convert string booleans
                if isinstance(value, str):
                    normalized[key] = value.lower() in ('true', '1', 'on', 'yes')
                else:
                    normalized[key] = value
            else:
                normalized[key] = value

        return normalized

    # Normalize config before validation
    if schema and 'properties' in schema:
        plugin_config = normalize_config_values(plugin_config, schema['properties'])

    # Filter config to only include schema-defined fields (important when additionalProperties is false)
    # Use enhanced schema with core properties to ensure core properties are preserved during filtering
    if schema and 'properties' in schema:
        enhanced_schema_for_filtering = _enhance_schema_with_core_properties(schema)
        plugin_config = _filter_config_by_schema(plugin_config, enhanced_schema_for_filtering)

    # A uniqueItems array can arrive with a repeat -- the form merges onto
    # the stored list, so a stock symbol already saved and submitted again
    # appears twice -- and validation would refuse the whole save for it.
    if schema:
        dedup_unique_arrays(plugin_config, schema)

    if schema:
        is_valid, validation_errors = schema_mgr.validate_config_against_schema(
            plugin_config, schema, plugin_id
        )
        if not is_valid:
            # Schema keys including the injected core properties, for the error
            enhanced_schema = _enhance_schema_with_core_properties(schema)
            # Keys, never values: plugin_config still holds the submitted
            # secrets here (separate_secrets runs below), and logging it wrote
            # live credentials to the journal.
            logger.warning("Config validation failed for %s: %s (config keys: %s)",
                           plugin_id, validation_errors, list(plugin_config.keys()))
            return None, None, error_response(
                ErrorCode.CONFIG_VALIDATION_FAILED,
                'Configuration validation failed',
                details='; '.join(validation_errors) if validation_errors else 'Unknown validation error',
                context={
                    'plugin_id': plugin_id,
                    'validation_errors': validation_errors,
                    'config_keys': list(plugin_config.keys()),
                    'schema_keys': list(enhanced_schema.get('properties', {}).keys())
                },
                suggested_fixes=[
                    'Review validation errors above',
                    'Check config against schema',
                    'Verify all required fields are present'
                ],
                status_code=400
            )

    # Separate secrets from regular config (handles nested configs and
    # array-item secrets — see src/web_interface/secret_helpers.py)
    regular_config, secrets_config = separate_secrets(plugin_config, secret_fields)
    # The config form renders secrets masked, so every save posts
    # them back blank. Without this the blank is merged over the
    # stored value and the credential is destroyed by the act of
    # changing an unrelated setting. A blank means "unchanged".
    secrets_config = remove_empty_secrets(secrets_config)

    return regular_config, secrets_config, None


@api_v3.route('/plugins/schema', methods=['GET'])
def get_plugin_schema():
    """Get plugin configuration schema"""
    plugin_id = request.args.get('plugin_id')
    if not plugin_id:
        return jsonify({'status': 'error', 'message': 'plugin_id required'}), 400

    # Get schema manager instance
    schema_mgr = api_v3.schema_manager
    if not schema_mgr:
        return jsonify({'status': 'error', 'message': 'Schema manager not initialized'}), 500

    # Load schema using SchemaManager (uses caching)
    schema = schema_mgr.load_schema(plugin_id, use_cache=True)

    if schema:
        return jsonify({'status': 'success', 'data': {'schema': schema}})

    # Return a simple default schema if file not found
    default_schema = {
        'type': 'object',
        'properties': {
            'enabled': {
                'type': 'boolean',
                'title': 'Enable Plugin',
                'description': 'Enable or disable this plugin',
                'default': True
            },
            'display_duration': {
                'type': 'integer',
                'title': 'Display Duration',
                'description': 'How long to show content (seconds)',
                'minimum': 5,
                'maximum': 300,
                'default': 30
            }
        }
    }

    return jsonify({'status': 'success', 'data': {'schema': default_schema}})


@api_v3.route('/plugins/config/reset', methods=['POST'])
def reset_plugin_config():
    """Reset plugin configuration to schema defaults"""
    if not api_v3.config_manager:
        return jsonify({'status': 'error', 'message': 'Config manager not initialized'}), 500

    data = request.get_json(silent=True) or {}
    plugin_id = data.get('plugin_id')
    preserve_secrets = data.get('preserve_secrets', True)

    if not plugin_id:
        return jsonify({'status': 'error', 'message': 'plugin_id required'}), 400
    id_error = _non_plugin_id_error(plugin_id)
    if id_error:
        return id_error

    # Get schema manager instance
    schema_mgr = api_v3.schema_manager
    if not schema_mgr:
        return jsonify({'status': 'error', 'message': 'Schema manager not initialized'}), 500

    # Generate defaults from schema
    defaults = schema_mgr.generate_default_config(plugin_id, use_cache=True)

    # Get current configs
    current_config = api_v3.config_manager.load_config()
    current_secrets = api_v3.config_manager.get_raw_file_content('secrets')

    # Load schema to identify secret fields
    schema = schema_mgr.load_schema(plugin_id, use_cache=True)
    secret_fields = set()

    if schema and 'properties' in schema:
        secret_fields = find_secret_fields(schema['properties'])

    # Separate defaults into regular and secret configs
    default_regular, default_secrets = separate_secrets(defaults, secret_fields)

    # Update main config with defaults
    current_config[plugin_id] = default_regular

    # Update secrets config (preserve existing secrets if preserve_secrets=True)
    if preserve_secrets:
        # Keep existing secrets for this plugin
        if plugin_id in current_secrets:
            # Merge defaults with existing secrets
            existing_secrets = current_secrets[plugin_id]
            for key, value in default_secrets.items():
                if key not in existing_secrets or not existing_secrets[key]:
                    existing_secrets[key] = value
        else:
            current_secrets[plugin_id] = default_secrets
    else:
        # Replace all secrets with defaults
        current_secrets[plugin_id] = default_secrets

    success, error_msg = _pkg._save_config_atomic(api_v3.config_manager, current_config, create_backup=True)
    if not success:
        return error_response(
            ErrorCode.CONFIG_SAVE_FAILED,
            f"Failed to save configuration: {error_msg}",
            status_code=500
        )
    if default_secrets or not preserve_secrets:
        api_v3.config_manager.save_raw_file_content('secrets', current_secrets)

    # Notify plugin of config change if loaded
    try:
        if api_v3.plugin_manager:
            plugin_instance = api_v3.plugin_manager.get_plugin(plugin_id)
            if plugin_instance:
                merged_config = api_v3.config_manager.load_config()
                plugin_full_config = _pkg._prepared_plugin_config(
                    plugin_id, merged_config.get(plugin_id, {}))
                if hasattr(plugin_instance, 'on_config_change'):
                    plugin_instance.on_config_change(plugin_full_config)
    except Exception as hook_err:
        logger.warning("on_config_change failed: %s", hook_err)

    return jsonify({
        'status': 'success',
        'message': f'Plugin {plugin_id} configuration reset to defaults',
        'data': {'config': defaults}
    })
