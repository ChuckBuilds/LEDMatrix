"""Reading and writing configuration, including schedules.

Routes decorate the shared `api_v3` Blueprint from ._common, so their
endpoint names are unchanged by living here.
"""
from web_interface.blueprints.api_v3 import (
    ErrorCode, Optional, PROJECT_ROOT, Path, _coerce_to_bool,
    _redact_credentials, _validate_time_format, api_v3, deep_merge,
    describe_exception, error_response, find_secret_fields, json, jsonify,
    logger, logging, mask_all_secret_values, merge_secrets, os,
    remove_empty_secrets, request, separate_secrets, strip_masked_values,
    success_response,
)
from src.common.path_safety import resolve_under
from src.display_geometry import ORIENTATION_ROTATE_DEGREES
from src.matrix_support import INT_SETTING_LIMITS, describe_range, library_refusals, refusal_message
from src.pi5_matrix_support import is_raspberry_pi_5
import web_interface.blueprints.api_v3 as _pkg

# Read through the module rather than bound by value: tests patch these
# as module attributes, and a value binding would not see the patch.
# Several are also called from helpers that live in __init__, so the
# package is the only patch point that covers every caller.

#: Hidden input the v3 settings forms (general.html, display.html,
#: durations.html) post to /config/main. Its presence tells save_main_config
#: that a missing checkbox was unchecked, not merely left out of an API call.
FORM_SECTION_FIELD = '__form_section'


def _day_setting(data, day, flat_key, nested_key):
    """(present, value) of one per-day schedule setting in a POST body.

    The schedule forms post flat keys (``monday_start``), while GET returns
    the stored shape, ``days.monday.start_time``. Accept both, so a client can
    post back what it read; a flat key wins when a body carries both.
    """
    if flat_key in data:
        return True, data[flat_key]
    days = data.get('days')
    day_config = days.get(day) if isinstance(days, dict) else None
    if isinstance(day_config, dict) and nested_key in day_config:
        return True, day_config[nested_key]
    return False, None


@api_v3.route('/config/main', methods=['GET'])
def get_main_config():
    """Get main configuration, with credentials redacted."""
    try:
        if not api_v3.config_manager:
            return jsonify({'status': 'error', 'message': 'Config manager not initialized'}), 500

        config = api_v3.config_manager.load_config()
        return jsonify({'status': 'success', 'data': _redact_credentials(config)})
    except Exception as e:
        logger.error('Unhandled exception', exc_info=True)
        return jsonify({'status': 'error', 'message': 'An error occurred; see logs for details', 'details': describe_exception(e)}), 500
@api_v3.route('/config/schedule', methods=['GET'])
def get_schedule_config():
    """Get current schedule configuration"""
    try:
        if not api_v3.config_manager:
            return error_response(
                ErrorCode.CONFIG_LOAD_FAILED,
                'Config manager not initialized',
                status_code=500
            )

        config = api_v3.config_manager.load_config()
        schedule_config = config.get('schedule', {})

        return success_response(data=schedule_config)
    except Exception as e:
        logger.error("%s failed", request.path, exc_info=True)
        return error_response(
            ErrorCode.CONFIG_LOAD_FAILED,
            "An error occurred; see logs for details",
            details=describe_exception(e),
            status_code=500
        )
@api_v3.route('/config/schedule', methods=['POST'])
def save_schedule_config():
    """Save schedule configuration"""
    try:
        if not api_v3.config_manager:
            return jsonify({'status': 'error', 'message': 'Config manager not initialized'}), 500

        data = request.get_json(silent=True)
        if not data:
            return jsonify({'status': 'error', 'message': 'No data provided'}), 400

        # Load current config
        current_config = api_v3.config_manager.load_config()

        # Build schedule configuration
        # Handle enabled checkbox - can be True, False, or 'on'
        enabled_value = data.get('enabled', False)
        if isinstance(enabled_value, str):
            enabled_value = enabled_value.lower() in ('true', 'on', '1')
        schedule_config = {
            'enabled': enabled_value
        }

        mode = data.get('mode', 'global')
        schedule_config['mode'] = mode

        if mode == 'global':
            # Simple global schedule
            start_time = data.get('start_time', '07:00')
            end_time = data.get('end_time', '23:00')

            # Validate time formats
            is_valid, error_msg = _validate_time_format(start_time)
            if not is_valid:
                return error_response(
                    ErrorCode.VALIDATION_ERROR,
                    error_msg,
                    status_code=400
                )

            is_valid, error_msg = _validate_time_format(end_time)
            if not is_valid:
                return error_response(
                    ErrorCode.VALIDATION_ERROR,
                    error_msg,
                    status_code=400
                )

            schedule_config['start_time'] = start_time
            schedule_config['end_time'] = end_time
            # Remove days config when switching to global mode
            schedule_config.pop('days', None)
        else:
            # Per-day schedule
            schedule_config['days'] = {}
            # Remove global times when switching to per-day mode
            schedule_config.pop('start_time', None)
            schedule_config.pop('end_time', None)
            days = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']
            enabled_days_count = 0

            for day in days:
                day_config = {}
                enabled_key = f'{day}_enabled'
                start_key = f'{day}_start'
                end_key = f'{day}_end'

                # Check if day is enabled
                has_enabled, enabled_val = _day_setting(data, day, enabled_key, 'enabled')
                if has_enabled:
                    # Handle checkbox values that may come as 'on', True, or False
                    if isinstance(enabled_val, str):
                        day_config['enabled'] = enabled_val.lower() in ('true', 'on', '1')
                    else:
                        day_config['enabled'] = bool(enabled_val)
                else:
                    # Default to enabled if not specified
                    day_config['enabled'] = True

                # Only add times if day is enabled
                if day_config.get('enabled', True):
                    enabled_days_count += 1
                    start_time = None
                    end_time = None

                    start_time = _day_setting(data, day, start_key, 'start_time')[1] or '07:00'
                    end_time = _day_setting(data, day, end_key, 'end_time')[1] or '23:00'

                    # Validate time formats
                    is_valid, error_msg = _validate_time_format(start_time)
                    if not is_valid:
                        return error_response(
                            ErrorCode.VALIDATION_ERROR,
                            f"Invalid start time for {day}: {error_msg}",
                            status_code=400
                        )

                    is_valid, error_msg = _validate_time_format(end_time)
                    if not is_valid:
                        return error_response(
                            ErrorCode.VALIDATION_ERROR,
                            f"Invalid end time for {day}: {error_msg}",
                            status_code=400
                        )

                    day_config['start_time'] = start_time
                    day_config['end_time'] = end_time

                schedule_config['days'][day] = day_config

            # Validate that at least one day is enabled in per-day mode
            if enabled_days_count == 0:
                return error_response(
                    ErrorCode.VALIDATION_ERROR,
                    "At least one day must be enabled in per-day schedule mode",
                    status_code=400
                )

        # Update and save config using atomic save
        current_config['schedule'] = schedule_config
        success, error_msg = _pkg._save_config_atomic(api_v3.config_manager, current_config, create_backup=True)
        if not success:
            return error_response(
                ErrorCode.CONFIG_SAVE_FAILED,
                f"Failed to save schedule configuration: {error_msg}",
                status_code=500
            )

        # Invalidate cache on config change
        try:
            from web_interface.cache import invalidate_cache
            invalidate_cache()
        except ImportError:
            pass

        return success_response(message='Schedule configuration saved successfully')
    except Exception as e:
        import logging
        logger.error("Error saving schedule config", exc_info=True)
        return error_response(
            ErrorCode.CONFIG_SAVE_FAILED,
            "An error occurred; see logs for details",

            status_code=500, details=describe_exception(e)
        )
@api_v3.route('/config/dim-schedule', methods=['GET'])
def get_dim_schedule_config():
    """Get current dim schedule configuration"""
    import logging
    import json

    if not api_v3.config_manager:
        logging.error("[DIM SCHEDULE] Config manager not initialized")
        return error_response(
            ErrorCode.CONFIG_LOAD_FAILED,
            'Config manager not initialized',
            status_code=500
        )

    try:
        config = api_v3.config_manager.load_config()
        dim_schedule_config = config.get('dim_schedule', {
            'enabled': False,
            'dim_brightness': 30,
            'mode': 'global',
            'start_time': '20:00',
            'end_time': '07:00',
            'days': {}
        })

        return success_response(data=dim_schedule_config)
    except FileNotFoundError as e:
        logging.error(f"[DIM SCHEDULE] Config file not found: {e}", exc_info=True)
        return error_response(
            ErrorCode.CONFIG_LOAD_FAILED,
            "Configuration file not found",
            status_code=500
        )
    except json.JSONDecodeError as e:
        logging.error(f"[DIM SCHEDULE] Invalid JSON in config file: {e}", exc_info=True)
        return error_response(
            ErrorCode.CONFIG_LOAD_FAILED,
            "Configuration file contains invalid JSON",
            status_code=500
        )
    except (IOError, OSError) as e:
        logging.error(f"[DIM SCHEDULE] Error reading config file: {e}", exc_info=True)
        return error_response(
            ErrorCode.CONFIG_LOAD_FAILED,
            "An error occurred; see logs for details",
            status_code=500, details=describe_exception(e)
        )
    except Exception as e:
        logging.error(f"[DIM SCHEDULE] Unexpected error loading config: {e}", exc_info=True)
        return error_response(
            ErrorCode.CONFIG_LOAD_FAILED,
            "An error occurred; see logs for details",
            status_code=500, details=describe_exception(e)
        )
@api_v3.route('/config/dim-schedule', methods=['POST'])
def save_dim_schedule_config():
    """Save dim schedule configuration"""
    try:
        if not api_v3.config_manager:
            return jsonify({'status': 'error', 'message': 'Config manager not initialized'}), 500

        data = request.get_json(silent=True)
        if not data:
            return jsonify({'status': 'error', 'message': 'No data provided'}), 400

        # Load current config
        current_config = api_v3.config_manager.load_config()

        # Build dim schedule configuration
        enabled_value = data.get('enabled', False)
        if isinstance(enabled_value, str):
            enabled_value = enabled_value.lower() in ('true', 'on', '1')

        # Validate and get dim_brightness
        dim_brightness_raw = data.get('dim_brightness', 30)
        try:
            # Handle empty string or None
            if dim_brightness_raw is None or dim_brightness_raw == '':
                dim_brightness = 30
            else:
                dim_brightness = int(dim_brightness_raw)
        except (ValueError, TypeError, OverflowError):
            return error_response(
                ErrorCode.VALIDATION_ERROR,
                "dim_brightness must be an integer between 0 and 100",
                status_code=400
            )

        if not 0 <= dim_brightness <= 100:
            return error_response(
                ErrorCode.VALIDATION_ERROR,
                "dim_brightness must be between 0 and 100",
                status_code=400
            )

        dim_schedule_config = {
            'enabled': enabled_value,
            'dim_brightness': dim_brightness
        }

        mode = data.get('mode', 'global')
        dim_schedule_config['mode'] = mode

        if mode == 'global':
            # Simple global schedule
            start_time = data.get('start_time', '20:00')
            end_time = data.get('end_time', '07:00')

            # Validate time formats
            is_valid, error_msg = _validate_time_format(start_time)
            if not is_valid:
                return error_response(
                    ErrorCode.VALIDATION_ERROR,
                    error_msg,
                    status_code=400
                )

            is_valid, error_msg = _validate_time_format(end_time)
            if not is_valid:
                return error_response(
                    ErrorCode.VALIDATION_ERROR,
                    error_msg,
                    status_code=400
                )

            dim_schedule_config['start_time'] = start_time
            dim_schedule_config['end_time'] = end_time
            # Remove days config when switching to global mode
            dim_schedule_config.pop('days', None)
        else:
            # Per-day schedule
            dim_schedule_config['days'] = {}
            # Remove global times when switching to per-day mode
            dim_schedule_config.pop('start_time', None)
            dim_schedule_config.pop('end_time', None)
            days = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']
            enabled_days_count = 0

            for day in days:
                day_config = {}
                enabled_key = f'{day}_enabled'
                start_key = f'{day}_start'
                end_key = f'{day}_end'

                # Check if day is enabled
                has_enabled, enabled_val = _day_setting(data, day, enabled_key, 'enabled')
                if has_enabled:
                    if isinstance(enabled_val, str):
                        day_config['enabled'] = enabled_val.lower() in ('true', 'on', '1')
                    else:
                        day_config['enabled'] = bool(enabled_val)
                else:
                    day_config['enabled'] = True

                # Only add times if day is enabled
                if day_config.get('enabled', True):
                    enabled_days_count += 1
                    start_time = _day_setting(data, day, start_key, 'start_time')[1] or '20:00'
                    end_time = _day_setting(data, day, end_key, 'end_time')[1] or '07:00'

                    # Validate time formats
                    is_valid, error_msg = _validate_time_format(start_time)
                    if not is_valid:
                        return error_response(
                            ErrorCode.VALIDATION_ERROR,
                            f"Invalid start time for {day}: {error_msg}",
                            status_code=400
                        )

                    is_valid, error_msg = _validate_time_format(end_time)
                    if not is_valid:
                        return error_response(
                            ErrorCode.VALIDATION_ERROR,
                            f"Invalid end time for {day}: {error_msg}",
                            status_code=400
                        )

                    day_config['start_time'] = start_time
                    day_config['end_time'] = end_time

                dim_schedule_config['days'][day] = day_config

            # Validate that at least one day is enabled in per-day mode
            if enabled_days_count == 0:
                return error_response(
                    ErrorCode.VALIDATION_ERROR,
                    "At least one day must be enabled in per-day dim schedule mode",
                    status_code=400
                )

        # Update and save config using atomic save
        current_config['dim_schedule'] = dim_schedule_config
        success, error_msg = _pkg._save_config_atomic(api_v3.config_manager, current_config, create_backup=True)
        if not success:
            return error_response(
                ErrorCode.CONFIG_SAVE_FAILED,
                f"Failed to save dim schedule configuration: {error_msg}",
                status_code=500
            )

        # Invalidate cache on config change
        try:
            from web_interface.cache import invalidate_cache
            invalidate_cache()
        except ImportError:
            pass

        return success_response(message='Dim schedule configuration saved successfully')
    except Exception as e:
        import logging
        logger.error("Error saving dim schedule config", exc_info=True)
        return error_response(
            ErrorCode.CONFIG_SAVE_FAILED,
            "An error occurred; see logs for details",

            status_code=500, details=describe_exception(e)
        )
@api_v3.route('/config/main', methods=['POST'])
def save_main_config():
    """Save main configuration"""
    try:
        if not api_v3.config_manager:
            return jsonify({'status': 'error', 'message': 'Config manager not initialized'}), 500

        # Try to get JSON data first, fallback to form data
        data = None
        if request.is_json:
            data = request.get_json()
            if data is not None and not isinstance(data, dict):
                return jsonify({'status': 'error', 'message': 'Request body must be a JSON object'}), 400
        else:
            # Handle form data
            data = request.form.to_dict()
            # Convert checkbox values
            for key in ['web_display_autostart']:
                if key in data:
                    data[key] = data[key] == 'on'

        if not data:
            return jsonify({'status': 'error', 'message': 'No data provided'}), 400

        # A missing checkbox means different things to the two kinds of caller.
        # The settings forms post every field, and a browser leaves an
        # unchecked box out entirely, so for them absent means False. A JSON
        # API client (the MQTT bridge's brightness slider, a curl call from the
        # REST docs) sends only what it is changing, and there absent means
        # "leave it alone": treating it as unchecked turned off
        # disable_hardware_pulsing and three other settings on every
        # brightness change, and weekly auto-updates on every timezone change.
        # The v3 forms post JSON too (htmx json-enc), so they identify
        # themselves with a hidden FORM_SECTION_FIELD input. A form-encoded
        # post is a form by definition.
        is_form_submission = bool(data.pop(FORM_SECTION_FIELD, None)) or not request.is_json

        def _set_checkbox(section, key, field):
            """Store checkbox ``field`` as ``section[key]``, if this request sets it."""
            if is_form_submission or field in data:
                section[key] = _coerce_to_bool(data.get(field))

        # What arrives here is the config itself, and the headers carry the
        # session cookie -- neither belongs in the journal, least of all at
        # ERROR on every save. The shape of the request is the part with
        # diagnostic value, so log that, at the level it deserves.
        logger.debug("save_main_config: %s, %d top-level key(s)",
                     request.content_type or 'no content-type', len(data))

        # Merge with existing config (similar to original implementation)
        current_config = api_v3.config_manager.load_config()
        was_auto_update_enabled = bool((current_config.get('auto_update') or {}).get('enabled'))

        # Handle general settings
        # Note: Checkboxes don't send data when unchecked, so we need to check if we're updating general settings
        # If any general setting is present, we're updating the general tab
        is_general_update = any(k in data for k in ['timezone', 'city', 'state', 'country', 'web_display_autostart',
                                                     'plugins_directory', 'auto_update_enabled'])

        if is_general_update:
            # For checkbox: if not present in data during a general *form*
            # update, it means unchecked (see _set_checkbox)
            _set_checkbox(current_config, 'web_display_autostart', 'web_display_autostart')
            if is_form_submission or 'auto_update_enabled' in data:
                if not isinstance(current_config.get('auto_update'), dict):
                    current_config['auto_update'] = {}
                _set_checkbox(current_config['auto_update'], 'enabled', 'auto_update_enabled')

        if 'timezone' in data:
            current_config['timezone'] = data['timezone']

        # Device-wide scroll frame rate, read by plugins via
        # BasePlugin.global_config. Bounds match ScrollHelper.set_target_fps,
        # which clamps silently -- rejecting here instead means a value that
        # would have been quietly altered is reported rather than appearing to
        # save and then behaving differently.
        if 'target_fps' in data and data['target_fps'] not in ('', None):
            raw_target_fps = data['target_fps']
            # A JSON body can carry real floats and bools, where int() would
            # silently truncate: 90.5 would save as 90, and true as 1. Reject
            # them rather than storing a value the user did not ask for. Form
            # posts arrive as strings, so '90.5' still fails in int() below.
            if isinstance(raw_target_fps, (bool, float)):
                return jsonify({
                    'status': 'error',
                    'message': "Invalid value for target_fps: must be an integer"
                }), 400
            try:
                target_fps = int(raw_target_fps)
            except (ValueError, TypeError, OverflowError):
                return jsonify({
                    'status': 'error',
                    'message': "Invalid value for target_fps: must be an integer"
                }), 400
            if not (30 <= target_fps <= 200):
                return jsonify({
                    'status': 'error',
                    'message': "Invalid value for target_fps: must be between 30 and 200"
                }), 400
            current_config['target_fps'] = target_fps

        # Handle location settings
        if 'city' in data or 'state' in data or 'country' in data:
            if 'location' not in current_config:
                current_config['location'] = {}
            if 'city' in data:
                current_config['location']['city'] = data['city']
            if 'state' in data:
                current_config['location']['state'] = data['state']
            if 'country' in data:
                current_config['location']['country'] = data['country']

        # Handle plugin system settings
        if 'auto_discover' in data or 'auto_load_enabled' in data or 'development_mode' in data or 'plugins_directory' in data:
            if 'plugin_system' not in current_config:
                current_config['plugin_system'] = {}

            # auto_discover / auto_load_enabled / development_mode are read by
            # nothing and no longer have General-tab toggles. The form still
            # posts plugins_directory, so treating a missing key as an
            # unchecked box would rewrite stored values to false on every
            # save; only store what a client actually sends.
            for legacy_flag in ['auto_discover', 'auto_load_enabled', 'development_mode']:
                if legacy_flag in data:
                    current_config['plugin_system'][legacy_flag] = _coerce_to_bool(data.get(legacy_flag))

            # Handle plugins_directory
            if 'plugins_directory' in data:
                current_config['plugin_system']['plugins_directory'] = data['plugins_directory']

        # Handle display settings
        display_fields = ['rows', 'cols', 'chain_length', 'parallel', 'brightness', 'hardware_mapping',
                         'gpio_slowdown', 'rp1_rio', 'scan_mode', 'disable_hardware_pulsing', 'inverse_colors', 'show_refresh_rate',
                         'pwm_bits', 'pwm_dither_bits', 'pwm_lsb_nanoseconds', 'limit_refresh_rate_hz', 'use_short_date_format',
                         'max_dynamic_duration_seconds', 'led_rgb_sequence', 'multiplexing', 'panel_type',
                         'row_address_type', 'pixel_mapper_config', 'orientation']

        if any(k in data for k in display_fields):
            if 'display' not in current_config:
                current_config['display'] = {}
            if 'hardware' not in current_config['display']:
                current_config['display']['hardware'] = {}
            if 'runtime' not in current_config['display']:
                current_config['display']['runtime'] = {}

            # Allowed values for validated string fields
            LED_RGB_ALLOWED = {'RGB', 'RBG', 'GRB', 'GBR', 'BRG', 'BGR'}
            PANEL_TYPE_ALLOWED = {'', 'FM6126A', 'FM6127'}

            # Validate led_rgb_sequence
            if 'led_rgb_sequence' in data and data['led_rgb_sequence'] not in LED_RGB_ALLOWED:
                return jsonify({'status': 'error', 'message': f"Invalid LED RGB sequence '{data['led_rgb_sequence']}'. Allowed values: {', '.join(sorted(LED_RGB_ALLOWED))}"}), 400

            # Validate panel_type
            if 'panel_type' in data and data['panel_type'] not in PANEL_TYPE_ALLOWED:
                return jsonify({'status': 'error', 'message': f"Invalid panel type '{data['panel_type']}'. Allowed values: Standard (empty), FM6126A, FM6127"}), 400

            # Validate pixel_mapper_config (free-form mapper string, e.g. "U-mapper;Rotate:90")
            if 'pixel_mapper_config' in data and not isinstance(data['pixel_mapper_config'], str):
                return jsonify({'status': 'error', 'message': 'pixel_mapper_config must be a string (e.g. "U-mapper;Rotate:90" or empty)'}), 400

            # Validate orientation (physical mounting rotation; composed onto pixel_mapper_config at runtime)
            ORIENTATION_ALLOWED = set(ORIENTATION_ROTATE_DEGREES)
            if 'orientation' in data and data['orientation'] not in ORIENTATION_ALLOWED:
                return jsonify({'status': 'error', 'message': f"Invalid orientation '{data['orientation']}'. Allowed values: {', '.join(sorted(ORIENTATION_ALLOWED))}"}), 400

            # Panel geometry, PWM and GPIO timing, held to what the rgbmatrix
            # library and its Python binding accept (src/matrix_support.py:
            # Options::Validate, the gpio_slowdown check, and the binding's
            # uint8_t setters, which cap chain_length at 255). Outside those
            # ranges the library returns no matrix and the display service
            # crash-loops rather than falling back, so they never save.
            def _hardware_int_error(field, low, high, even=False):
                """A 400 response if data[field] is not an allowed integer, else None."""
                raw = data[field]
                rejection = (jsonify({'status': 'error', 'message': f"Invalid {field} '{raw}'. Must be {describe_range(low, high, even)}."}), 400)
                # int() would quietly turn true into 1 and 48.5 into 48.
                if isinstance(raw, bool) or (isinstance(raw, float) and not raw.is_integer()):
                    return rejection
                try:
                    value = int(raw)
                except (ValueError, TypeError, OverflowError):
                    return rejection
                if value < low or value > high or (even and value % 2):
                    return rejection
                return None

            # rp1_rio has its own check below.
            for field, (_section, low, high, even) in INT_SETTING_LIMITS.items():
                if field in data and field != 'rp1_rio':
                    error = _hardware_int_error(field, low, high, even)
                    if error:
                        return error

            # Combinations the library can't start with: a hardware mapping it
            # doesn't have, more parallel chains than the mapping has outputs,
            # and on a Pi 5 its narrower RP1 support. Reported only when this
            # request sets one of the settings involved, so a problem already
            # stored doesn't block unrelated saves.
            combination_fields = ('hardware_mapping', 'parallel', 'row_address_type')
            if any(k in data for k in combination_fields):
                effective = dict(current_config['display']['hardware'])
                effective.update({k: v for k, v in data.items()
                                  if k in combination_fields or k in INT_SETTING_LIMITS})
                refusals = [r for r in library_refusals(effective, pi5=is_raspberry_pi_5())
                            if any(f in data for f in r.fields)]
                if refusals:
                    return jsonify({'status': 'error', 'message': refusal_message(refusals)}), 400

            # Handle hardware settings
            for field in ['rows', 'cols', 'chain_length', 'parallel', 'brightness', 'hardware_mapping', 'scan_mode',
                         'pwm_bits', 'pwm_dither_bits', 'pwm_lsb_nanoseconds', 'limit_refresh_rate_hz',
                         'led_rgb_sequence', 'multiplexing', 'panel_type', 'row_address_type',
                         'pixel_mapper_config', 'orientation']:
                if field in data:
                    if field in ['rows', 'cols', 'chain_length', 'parallel', 'brightness', 'scan_mode',
                               'pwm_bits', 'pwm_dither_bits', 'pwm_lsb_nanoseconds', 'limit_refresh_rate_hz',
                               'multiplexing', 'row_address_type']:
                        current_config['display']['hardware'][field] = int(data[field])
                    else:
                        current_config['display']['hardware'][field] = data[field]

            # Handle runtime settings
            if 'gpio_slowdown' in data:
                current_config['display']['runtime']['gpio_slowdown'] = int(data['gpio_slowdown'])
            if 'rp1_rio' in data:
                try:
                    rp1_val = int(data['rp1_rio'])
                    if rp1_val not in (0, 1):
                        return jsonify({'status': 'error', 'message': "rp1_rio must be 0 (PIO) or 1 (RIO)"}), 400
                    current_config['display']['runtime']['rp1_rio'] = rp1_val
                except (ValueError, TypeError, OverflowError):
                    return jsonify({'status': 'error', 'message': "rp1_rio must be 0 or 1"}), 400

            # Handle checkboxes - coerce to bool to ensure proper JSON types
            for checkbox in ['disable_hardware_pulsing', 'inverse_colors', 'show_refresh_rate']:
                _set_checkbox(current_config['display']['hardware'], checkbox, checkbox)

            # Handle display-level checkboxes (unchecked state on form saves)
            _set_checkbox(current_config['display'], 'use_short_date_format', 'use_short_date_format')

            # Handle dynamic duration settings
            if 'max_dynamic_duration_seconds' in data:
                if 'dynamic_duration' not in current_config['display']:
                    current_config['display']['dynamic_duration'] = {}
                current_config['display']['dynamic_duration']['max_duration_seconds'] = int(data['max_dynamic_duration_seconds'])

        # Handle double-sided display settings
        double_sided_fields = ['double_sided_enabled', 'double_sided_copies', 'double_sided_axis']
        if any(k in data for k in double_sided_fields):
            if 'display' not in current_config:
                current_config['display'] = {}
            if 'double_sided' not in current_config['display']:
                current_config['display']['double_sided'] = {}
            ds_config = current_config['display']['double_sided']

            # Enabled checkbox: omitted from the form when unchecked.
            # The Display form posts copies/axis on every save regardless of this
            # checkbox, so when the feature is off we accept the values without
            # rejecting the whole save — otherwise a stale copies/chain_length
            # mismatch locks the user out of every other display setting.
            if is_form_submission or 'double_sided_enabled' in data:
                enabled = _coerce_to_bool(data.get('double_sided_enabled'))
                ds_config['enabled'] = enabled
            else:
                enabled = _coerce_to_bool(ds_config.get('enabled'))

            def _copies_fits_hardware(copies: int) -> Optional[str]:
                """Error message if copies doesn't divide the panel evenly, else None."""
                # Use axis from this request if provided, else from stored config.
                hw = current_config.get('display', {}).get('hardware', {})
                effective_axis = (data.get('double_sided_axis')
                                  or current_config.get('display', {}).get('double_sided', {}).get('axis', 'horizontal'))
                if effective_axis == 'horizontal':
                    chain_length = int(hw.get('chain_length', 2) or 2)
                    if chain_length % copies != 0:
                        return f"Double-sided copies ({copies}) must divide chain length ({chain_length}) evenly"
                elif effective_axis == 'vertical':
                    parallel = int(hw.get('parallel', 1) or 1)
                    if parallel % copies != 0:
                        return f"Double-sided copies ({copies}) must divide parallel ({parallel}) evenly"
                return None

            if 'double_sided_copies' in data and data['double_sided_copies'] not in ('', None):
                copies = None
                try:
                    copies = int(data['double_sided_copies'])
                except (ValueError, TypeError, OverflowError):
                    if enabled:
                        return jsonify({'status': 'error', 'message': "Double-sided copies must be an integer"}), 400
                if copies is not None and not (2 <= copies <= 8):
                    if enabled:
                        return jsonify({'status': 'error', 'message': "Double-sided copies must be between 2 and 8"}), 400
                    # Disabled: leave the stored value alone rather than writing junk.
                    copies = None
                if copies is not None:
                    # Divisibility is a hardware-relational check — only meaningful
                    # when the feature is actually on.
                    if enabled:
                        fit_error = _copies_fits_hardware(copies)
                        if fit_error:
                            return jsonify({'status': 'error', 'message': fit_error}), 400
                    ds_config['copies'] = copies

            if 'double_sided_axis' in data:
                axis = data['double_sided_axis']
                if axis not in ('horizontal', 'vertical'):
                    if enabled:
                        return jsonify({'status': 'error', 'message': "Double-sided axis must be 'horizontal' or 'vertical'"}), 400
                else:
                    ds_config['axis'] = axis

        # Handle Vegas scroll mode settings
        vegas_fields = ['vegas_scroll_enabled', 'vegas_scroll_speed', 'vegas_separator_width',
                       'vegas_target_fps', 'vegas_buffer_ahead', 'vegas_plugin_order', 'vegas_excluded_plugins',
                       'vegas_auto_trim', 'vegas_trim_threshold', 'vegas_content_padding',
                       'vegas_min_plugin_width', 'vegas_lead_in_width', 'vegas_plugins_per_cycle',
                       'vegas_max_plugin_width_ratio', 'vegas_dynamic_duration_enabled',
                       'vegas_min_cycle_duration', 'vegas_max_cycle_duration',
                       'vegas_intra_plugin_gap', 'vegas_render_width_pct',
                       'vegas_min_content_separation', 'vegas_min_cut_gap',
                       'vegas_continuous_scroll', 'vegas_extend_threshold_screens',
                       'vegas_smooth_scroll', 'vegas_overflow_mode']

        if any(k in data for k in vegas_fields):
            if 'display' not in current_config:
                current_config['display'] = {}
            if 'vegas_scroll' not in current_config['display']:
                current_config['display']['vegas_scroll'] = {}

            vegas_config = current_config['display']['vegas_scroll']

            # Handle enabled checkbox
            # HTML checkboxes omit the key entirely when unchecked, so if the form
            # was submitted (any vegas field present) but enabled key is missing,
            # the checkbox was unchecked and we should set enabled=False.
            # A JSON API call only changes the checkboxes it sends.
            _set_checkbox(vegas_config, 'enabled', 'vegas_scroll_enabled')
            _set_checkbox(vegas_config, 'auto_trim', 'vegas_auto_trim')
            _set_checkbox(vegas_config, 'dynamic_duration_enabled', 'vegas_dynamic_duration_enabled')
            _set_checkbox(vegas_config, 'continuous_scroll', 'vegas_continuous_scroll')
            _set_checkbox(vegas_config, 'smooth_scroll', 'vegas_smooth_scroll')

            # max_plugin_width_ratio is the one fractional setting, so it is
            # handled outside the integer loop below.
            if data.get('vegas_overflow_mode') not in ('', None):
                mode = str(data['vegas_overflow_mode']).strip().lower()
                if mode not in ('rotate', 'truncate'):
                    return jsonify({
                        'status': 'error',
                        'message': "Invalid value for vegas_overflow_mode: "
                                   "must be 'rotate' or 'truncate'"
                    }), 400
                vegas_config['overflow_mode'] = mode

            if data.get('vegas_extend_threshold_screens') not in ('', None):
                try:
                    screens = float(data['vegas_extend_threshold_screens'])
                except (ValueError, TypeError, OverflowError):
                    return jsonify({
                        'status': 'error',
                        'message': "Invalid value for vegas_extend_threshold_screens: "
                                   "must be a number"
                    }), 400
                if not (1.0 <= screens <= 10.0):
                    return jsonify({
                        'status': 'error',
                        'message': "Invalid value for vegas_extend_threshold_screens: "
                                   "must be between 1.0 and 10.0"
                    }), 400
                vegas_config['extend_threshold_screens'] = screens

            if data.get('vegas_max_plugin_width_ratio') not in ('', None):
                try:
                    ratio = float(data['vegas_max_plugin_width_ratio'])
                except (ValueError, TypeError, OverflowError):
                    return jsonify({
                        'status': 'error',
                        'message': "Invalid value for vegas_max_plugin_width_ratio: "
                                   "must be a number"
                    }), 400
                if not (0 <= ratio <= 20):
                    return jsonify({
                        'status': 'error',
                        'message': "Invalid value for vegas_max_plugin_width_ratio: "
                                   "must be between 0 and 20 (0 disables the cap)"
                    }), 400
                vegas_config['max_plugin_width_ratio'] = ratio

            # Handle numeric settings with validation.
            #
            # These bounds must match VegasModeConfig.validate(), which is what
            # actually gates Vegas starting. Where they were looser, a value
            # saved with a 200 and then made VegasModeCoordinator.start() bail
            # out with only a log line, so the ticker silently never ran.
            # Where they were tighter (scroll_speed capped at 100 against a
            # slider that goes to 200), a legitimate value was rejected with a
            # 400. See test_vegas_api_bounds_match_validate.
            numeric_fields = {
                'vegas_scroll_speed': ('scroll_speed', 1, 200),
                'vegas_separator_width': ('separator_width', 0, 128),
                'vegas_intra_plugin_gap': ('intra_plugin_gap', 0, 128),
                'vegas_render_width_pct': ('render_width_pct', 10, 100),
                'vegas_min_content_separation': ('min_content_separation', 0, 256),
                'vegas_min_cut_gap': ('min_cut_gap', 1, 128),
                'vegas_target_fps': ('target_fps', 30, 200),
                'vegas_buffer_ahead': ('buffer_ahead', 1, 5),
                'vegas_trim_threshold': ('trim_threshold', 0, 254),
                'vegas_content_padding': ('content_padding', 0, 128),
                'vegas_min_plugin_width': ('min_plugin_width', 0, 512),
                'vegas_lead_in_width': ('lead_in_width', 0, 2048),
                'vegas_plugins_per_cycle': ('plugins_per_cycle', 1, 50),
                'vegas_min_cycle_duration': ('min_cycle_duration', 5, 3600),
                'vegas_max_cycle_duration': ('max_cycle_duration', 10, 3600),
            }
            for field_name, (config_key, min_val, max_val) in numeric_fields.items():
                if field_name in data:
                    raw_value = data[field_name]
                    # Skip empty strings (treat as "not provided")
                    if raw_value == '' or raw_value is None:
                        continue
                    try:
                        int_value = int(raw_value)
                    except (ValueError, TypeError, OverflowError):
                        return jsonify({
                            'status': 'error',
                            'message': f"Invalid value for {field_name}: must be an integer"
                        }), 400
                    if not (min_val <= int_value <= max_val):
                        return jsonify({
                            'status': 'error',
                            'message': f"Invalid value for {field_name}: must be between {min_val} and {max_val}"
                        }), 400
                    vegas_config[config_key] = int_value

            # Handle plugin order and exclusions (JSON arrays)
            if 'vegas_plugin_order' in data:
                try:
                    if isinstance(data['vegas_plugin_order'], str):
                        parsed = json.loads(data['vegas_plugin_order'])
                    else:
                        parsed = data['vegas_plugin_order']
                    # Ensure result is a list
                    vegas_config['plugin_order'] = list(parsed) if isinstance(parsed, (list, tuple)) else []
                except (json.JSONDecodeError, TypeError, ValueError):
                    vegas_config['plugin_order'] = []

            if 'vegas_excluded_plugins' in data:
                try:
                    if isinstance(data['vegas_excluded_plugins'], str):
                        parsed = json.loads(data['vegas_excluded_plugins'])
                    else:
                        parsed = data['vegas_excluded_plugins']
                    # Ensure result is a list
                    vegas_config['excluded_plugins'] = list(parsed) if isinstance(parsed, (list, tuple)) else []
                except (json.JSONDecodeError, TypeError, ValueError):
                    vegas_config['excluded_plugins'] = []

        # Handle multi-display sync settings
        sync_fields = ["sync_role", "sync_port", "sync_follower_position"]
        if any(k in data for k in sync_fields):
            if 'sync' not in current_config:
                current_config['sync'] = {}
            SYNC_ROLE_ALLOWED = {'standalone', 'leader', 'follower'}
            if 'sync_role' in data:
                role_val = str(data['sync_role']).lower()
                if role_val not in SYNC_ROLE_ALLOWED:
                    return jsonify({'status': 'error', 'message': f"Invalid sync role '{role_val}'. Must be one of: standalone, leader, follower"}), 400
                current_config['sync']['role'] = role_val
            if 'sync_port' in data:
                try:
                    port_val = int(data['sync_port'])
                    if not (1024 <= port_val <= 65535):
                        return jsonify({'status': 'error', 'message': "sync_port must be between 1024 and 65535"}), 400
                    current_config['sync']['port'] = port_val
                except (ValueError, TypeError, OverflowError):
                    return jsonify({'status': 'error', 'message': "sync_port must be an integer"}), 400

            if "sync_follower_position" in data:
                pos_val = str(data["sync_follower_position"]).lower()
                if pos_val not in {"left", "right"}:
                    return jsonify({"status": "error", "message": "sync_follower_position must be left or right"}), 400
                current_config["sync"]["follower_position"] = pos_val

        # Handle primary rotation order: must be a JSON array of plugin-id
        # strings. Reject anything else with a 400 rather than silently
        # coercing, so a buggy client can't clear or corrupt the saved order.
        if 'plugin_rotation_order' in data:
            raw_order = data.pop('plugin_rotation_order')
            try:
                parsed = json.loads(raw_order) if isinstance(raw_order, str) else raw_order
            except (json.JSONDecodeError, TypeError, ValueError):
                return jsonify({'status': 'error',
                                'message': 'plugin_rotation_order must be valid JSON'}), 400
            if not isinstance(parsed, list) or not all(isinstance(p, str) for p in parsed):
                return jsonify({'status': 'error',
                                'message': 'plugin_rotation_order must be a list of plugin-id strings'}), 400
            if 'display' not in current_config:
                current_config['display'] = {}
            current_config['display']['plugin_rotation_order'] = parsed

        # Handle display durations. Popped from `data` (not just read) so
        # they can never also fall through to the generic "remaining keys"
        # merge near the end of this function, which would otherwise write
        # them AGAIN as bogus top-level config keys (e.g. "clock_duration": 30
        # sitting at config root alongside the correct
        # display.display_durations.clock_duration).
        # The Vegas cycle-time fields (vegas_min_cycle_duration, ...) share the
        # suffix but are Vegas settings, already handled above: counting them
        # here wrote junk mode durations, and a blank one 400'd the save.
        duration_fields = [k for k in list(data.keys())
                           if (k.endswith('_duration') and k not in vegas_fields)
                           or k in ('default_duration', 'transition_duration')]
        if duration_fields:
            if 'display' not in current_config:
                current_config['display'] = {}
            if 'display_durations' not in current_config['display']:
                current_config['display']['display_durations'] = {}

            for field in duration_fields:
                raw_value = data.pop(field)
                try:
                    int_value = int(raw_value)
                except (ValueError, TypeError, OverflowError):
                    return jsonify({'status': 'error',
                                    'message': f"Invalid duration for {field}: must be an integer"}), 400
                current_config['display']['display_durations'][field] = int_value

        # Per-mode durations from the Rotation & Durations page, posted as
        # duration__<mode_key> (mode keys are arbitrary plugin mode names, so
        # they can't use the suffix convention above). Same pop-and-validate
        # treatment, for the same reason.
        mode_duration_fields = [k for k in list(data.keys()) if k.startswith('duration__')]
        if mode_duration_fields:
            if 'display' not in current_config:
                current_config['display'] = {}
            if 'display_durations' not in current_config['display']:
                current_config['display']['display_durations'] = {}

            for field in mode_duration_fields:
                raw_value = data.pop(field)
                mode_key = field[len('duration__'):]
                if not mode_key:
                    continue
                try:
                    int_value = int(raw_value)
                except (ValueError, TypeError, OverflowError):
                    return jsonify({'status': 'error',
                                    'message': f"Invalid duration for mode '{mode_key}': must be an integer"}), 400
                current_config['display']['display_durations'][mode_key] = int_value

        # Handle plugin configurations dynamically
        # Any key that matches a plugin ID is that plugin's settings. They go
        # through the same preparation as POST /plugins/config -- merged onto
        # the stored section, legacy booleans and schema defaults applied,
        # filtered, validated, secrets split out -- so this route can't store
        # a config that one rejects (stored verbatim, it left the plugin
        # flagged degraded at its next load).
        plugin_keys_to_remove = []
        plugin_secrets_updates = {}
        # Discovered first: a plugin key not recognised here skips secret
        # separation below and falls through to the generic merge, which wrote
        # the plugin's API key into config.json in plain text whenever nothing
        # had yet discovered plugins in this process (any save after a restart).
        plugin_manifests = _pkg._discovered_plugin_manifests()
        for key in data:
            # Check if this key is a plugin ID
            if api_v3.plugin_manager and key in plugin_manifests:
                plugin_id = key
                submitted_config = data[key]
                if not isinstance(submitted_config, dict):
                    return error_response(
                        ErrorCode.VALIDATION_ERROR,
                        f"Settings for plugin '{plugin_id}' must be a JSON object",
                        status_code=400
                    )

                # plugin_id is already known to be a loaded plugin (the
                # membership test above), so this cannot currently traverse --
                # but the path is built from a request key, and the guard and
                # the schema load are far enough apart that a later edit could
                # separate them. Refuse rather than save without knowing which
                # fields are secrets.
                schema_path = resolve_under(api_v3.plugin_manager.plugins_dir,
                                            plugin_id, 'config_schema.json')
                if schema_path is None:
                    return error_response(
                        ErrorCode.VALIDATION_ERROR,
                        f"Invalid plugin id '{plugin_id}'",
                        status_code=400
                    )

                schema_mgr = api_v3.schema_manager
                if not schema_mgr:
                    return error_response(
                        ErrorCode.SYSTEM_ERROR,
                        'Schema manager not initialized',
                        status_code=500
                    )
                schema = schema_mgr.load_schema(plugin_id, use_cache=False)

                from web_interface.blueprints.api_v3.plugins import (
                    _merge_onto_stored_plugin_config, _prepare_plugin_config_for_save,
                )
                plugin_config = _merge_onto_stored_plugin_config(
                    plugin_id, submitted_config, current_config)
                regular_config, secrets_config, error = _prepare_plugin_config_for_save(
                    plugin_id, plugin_config, schema, schema_mgr, is_json=True)
                if error:
                    return error

                # Deep merge regular config into main config, dropping
                # retired core keys (skin, skin_options) from the stored section
                from src.plugin_system.schema_manager import drop_retired_plugin_keys
                stored_section = current_config.get(plugin_id)
                current_config[plugin_id] = deep_merge(
                    drop_retired_plugin_keys(
                        stored_section if isinstance(stored_section, dict) else {}, schema),
                    regular_config)
                if secrets_config:
                    plugin_secrets_updates[plugin_id] = secrets_config

                # Mark for removal from data dict (already processed)
                plugin_keys_to_remove.append(key)

        # Deep merge secrets into secrets config, once every plugin section
        # has validated
        if plugin_secrets_updates:
            current_secrets = api_v3.config_manager.get_raw_file_content('secrets')
            for plugin_id, secrets_config in plugin_secrets_updates.items():
                if plugin_id not in current_secrets:
                    current_secrets[plugin_id] = {}
                # Lists merge by replacement, so deep_merge here wrote a
                # blanked array straight over the stored credentials.
                current_secrets[plugin_id] = merge_secrets(
                    current_secrets[plugin_id], secrets_config)
            # Save secrets file
            api_v3.config_manager.save_raw_file_content('secrets', current_secrets)

        # Remove processed plugin keys from data (they're already in current_config)
        for key in plugin_keys_to_remove:
            del data[key]

        # Handle any remaining config keys
        # System settings (timezone, city, etc.) are already handled above
        # Plugin configs should use /api/v3/plugins/config endpoint, but we'll handle them here too for flexibility
        for key in data:
            # Skip system settings that are already handled above
            if key in ['timezone', 'city', 'state', 'country',
                       'web_display_autostart', 'auto_discover',
                       'auto_load_enabled', 'development_mode',
                       'plugins_directory', 'target_fps', 'auto_update_enabled']:
                continue
            # Skip fields that are already handled above in their own named sections.
            # Without this, every form field name lands as a top-level config key too.
            if key in display_fields:
                continue
            if key in sync_fields:
                continue
            if key in vegas_fields:
                continue
            if key in double_sided_fields:
                continue
            # For any remaining keys (including plugin keys), use deep merge to preserve existing settings
            if key in current_config and isinstance(current_config[key], dict) and isinstance(data[key], dict):
                # Deep merge to preserve existing settings
                current_config[key] = deep_merge(current_config[key], data[key])
            else:
                current_config[key] = data[key]

        # Save the merged config using atomic save
        success, error_msg = _pkg._save_config_atomic(api_v3.config_manager, current_config, create_backup=True)
        if not success:
            return error_response(
                ErrorCode.CONFIG_SAVE_FAILED,
                f"Failed to save configuration: {error_msg}",
                status_code=500
            )

        # Invalidate cache on config change
        try:
            from web_interface.cache import invalidate_cache
            invalidate_cache()
        except ImportError:
            pass

        # Notify saved plugins of their new config (with secrets merged), now
        # that it is on disk.
        for plugin_id in plugin_keys_to_remove:
            try:
                plugin_instance = api_v3.plugin_manager.get_plugin(plugin_id)
                if plugin_instance and hasattr(plugin_instance, 'on_config_change'):
                    merged_config = api_v3.config_manager.load_config()
                    plugin_instance.on_config_change(_pkg._prepared_plugin_config(
                        plugin_id, merged_config.get(plugin_id, {})))
            except Exception as hook_err:
                # Don't fail the save if hook fails
                logger.warning("on_config_change failed: %s", hook_err)

        message = 'Configuration saved successfully'
        # Switching automatic updates on finishes their setup, which needs
        # the display service to restart (web_interface/auto_update.py).
        try:
            from web_interface import auto_update
            note = auto_update.start_setup_if_needed(was_auto_update_enabled, current_config)
            if note:
                message = f'{message}. {note}'
        except Exception:
            logger.warning("Automatic update setup could not be started", exc_info=True)
        return success_response(message=message)
    except Exception as e:
        logger.error("Error saving config", exc_info=True)
        return error_response(
            ErrorCode.CONFIG_SAVE_FAILED,
            "An error occurred; see logs for details",
            status_code=500, details=describe_exception(e)
        )
@api_v3.route('/config/secrets', methods=['GET'])
def get_secrets_config():
    """Get secrets configuration"""
    try:
        if not api_v3.config_manager:
            return jsonify({'status': 'error', 'message': 'Config manager not initialized'}), 500

        config = api_v3.config_manager.get_raw_file_content('secrets')
        # This interface has no authentication, and this file is nothing but
        # credentials. It was handing all of them to anyone who could reach
        # the port. Values are masked; empty and YOUR_* placeholders are left
        # alone so a client can still tell "set" from "not set".
        return jsonify({'status': 'success',
                        'data': mask_all_secret_values(config)})
    except Exception as e:
        logger.error('Unhandled exception', exc_info=True)
        return jsonify({'status': 'error', 'message': 'An error occurred; see logs for details', 'details': describe_exception(e)}), 500
@api_v3.route('/config/raw/main', methods=['POST'])
def save_raw_main_config():
    """Save raw main configuration JSON"""
    try:
        if not api_v3.config_manager:
            return jsonify({'status': 'error', 'message': 'Config manager not initialized'}), 500

        # silent=True so a malformed body returns None instead of raising
        # Werkzeug's own BadRequest, which would answer in a different
        # shape than this API's. Distinguish the two causes: a body that
        # was sent but does not parse is a different mistake from no body.
        data = request.get_json(silent=True)
        if data is None and request.get_data():
            return jsonify({'status': 'error', 'message': 'Invalid JSON in request body'}), 400
        if not data:
            return jsonify({'status': 'error', 'message': 'No data provided'}), 400

        was_auto_update_enabled = False
        try:
            previous = api_v3.config_manager.get_raw_file_content('main') or {}
            was_auto_update_enabled = bool((previous.get('auto_update') or {}).get('enabled'))
        except Exception:
            logger.debug("Could not read the previous auto_update setting", exc_info=True)

        # Save the raw config file
        api_v3.config_manager.save_raw_file_content('main', data)

        message = 'Main configuration saved successfully'
        # Same hook as save_main_config: switching automatic updates on here
        # must finish their setup too, not wait for the next service restart.
        try:
            from web_interface import auto_update
            note = auto_update.start_setup_if_needed(was_auto_update_enabled, data)
            if note:
                message = f'{message}. {note}'
        except Exception:
            logger.warning("Automatic update setup could not be started", exc_info=True)
        return jsonify({'status': 'success', 'message': message})
    except Exception as e:
        from src.exceptions import ConfigError
        logger.error("Error saving raw main config", exc_info=True)

        # Extract more specific error message if it's a ConfigError
        if isinstance(e, ConfigError):
            error_message = 'An error occurred; see logs for details'
            if hasattr(e, 'config_path') and e.config_path:
                error_message = f"{error_message} (config_path: {e.config_path})"
            return error_response(
                ErrorCode.CONFIG_SAVE_FAILED,
                error_message,
                details=describe_exception(e),

                context={'config_path': e.config_path} if hasattr(e, 'config_path') and e.config_path else None,
                status_code=500
            )
        else:
            error_message = 'An error occurred; see logs for details'
            return error_response(
                ErrorCode.UNKNOWN_ERROR,
                error_message,
                details=describe_exception(e),

                status_code=500
            )
@api_v3.route('/config/raw/secrets', methods=['POST'])
def save_raw_secrets_config():
    """Save raw secrets configuration JSON"""
    try:
        if not api_v3.config_manager:
            return jsonify({'status': 'error', 'message': 'Config manager not initialized'}), 500

        # See save_raw_main_config: silent parsing, with a sent-but-broken
        # body reported separately from a missing one.
        data = request.get_json(silent=True)
        if data is None and request.get_data():
            return jsonify({'status': 'error', 'message': 'Invalid JSON in request body'}), 400
        if not data:
            return jsonify({'status': 'error', 'message': 'No data provided'}), 400

        # The GET above masks what it returns, and this endpoint's only client
        # reads the whole file, edits one field and posts all of it back. So
        # most of what arrives here is the mask, echoed rather than changed --
        # storing it verbatim would replace every untouched credential with
        # eight bullets. Strip those, then merge onto what is already stored,
        # which makes "unchanged" mean unchanged.
        #
        # The cost is that a secret can no longer be cleared by blanking it.
        # That needs its own affordance; a control that erases credentials as
        # a side effect of saving an unrelated one is not it.
        current = api_v3.config_manager.get_raw_file_content('secrets') or {}
        merged = deep_merge(current, strip_masked_values(data))
        api_v3.config_manager.save_raw_file_content('secrets', merged)

        # Reload GitHub token in plugin store manager if it exists
        if api_v3.plugin_store_manager:
            api_v3.plugin_store_manager.github_token = api_v3.plugin_store_manager._load_github_token()

        return jsonify({'status': 'success', 'message': 'Secrets configuration saved successfully'})
    except Exception as e:
        from src.exceptions import ConfigError
        logger.error("Error saving raw secrets config", exc_info=True)

        # Extract more specific error message if it's a ConfigError
        if isinstance(e, ConfigError):
            # ConfigError has a message attribute and may have context
            error_message = 'An error occurred; see logs for details'
            if hasattr(e, 'config_path') and e.config_path:
                error_message = f"{error_message} (config_path: {e.config_path})"
        else:
            error_message = 'An error occurred; see logs for details'

        return jsonify({'status': 'error', 'message': error_message,
                        'details': describe_exception(e)}), 500
