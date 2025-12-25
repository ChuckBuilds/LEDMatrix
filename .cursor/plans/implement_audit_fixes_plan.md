# Implementation Plan: Fix Config Schema Validation Issues

Based on audit results showing 186 issues across 20 plugins.

## Overview

Three priority fixes identified from audit:
1. **Priority 1 (HIGH)**: Remove core properties from required array - will fix ~150 issues
2. **Priority 2 (MEDIUM)**: Verify default merging logic - will fix remaining required field issues
3. **Priority 3 (LOW)**: Calendar plugin schema cleanup - will fix 3 extra field warnings

## Priority 1: Remove Core Properties from Required Array

### Problem
Core properties (`enabled`, `display_duration`, `live_priority`) are system-managed but listed in schema `required` arrays. SchemaManager injects them into properties but doesn't remove them from `required`, causing validation failures.

### Solution
**File**: `src/plugin_system/schema_manager.py`
**Location**: `validate_config_against_schema()` method, after line 295

### Implementation Steps

1. **Add code to remove core properties from required array**:
   ```python
   # After injecting core properties (around line 295), add:
   # Remove core properties from required array (they're system-managed)
   if "required" in enhanced_schema:
       core_prop_names = list(core_properties.keys())
       enhanced_schema["required"] = [
           field for field in enhanced_schema["required"] 
           if field not in core_prop_names
       ]
   ```

2. **Add logging for debugging** (optional but helpful):
   ```python
   if "required" in enhanced_schema and core_prop_names:
       removed_from_required = [
           field for field in enhanced_schema.get("required", [])
           if field in core_prop_names
       ]
       if removed_from_required and plugin_id:
           self.logger.debug(
               f"Removed core properties from required array for {plugin_id}: {removed_from_required}"
           )
   ```

3. **Test the fix**:
   - Run audit script: `python scripts/audit_plugin_configs.py`
   - Expected: Issue count drops from 186 to ~30-40
   - All "enabled" related errors should be eliminated

### Expected Outcome
- All 20 plugins should no longer fail validation due to missing `enabled` field
- ~150 issues resolved (all enabled-related validation errors)

## Priority 2: Verify Default Merging Logic

### Problem
Some plugins have required fields with defaults that should be applied before validation. Need to verify the default merging happens correctly and handles nested objects.

### Solution
**File**: `web_interface/blueprints/api_v3.py`
**Location**: `save_plugin_config()` method, around lines 3218-3221

### Implementation Steps

1. **Review current default merging logic**:
   - Check that `merge_with_defaults()` is called before validation (line 3220)
   - Verify it's called after preserving enabled state but before validation

2. **Verify merge_with_defaults handles nested objects**:
   - Check `src/plugin_system/schema_manager.py` → `merge_with_defaults()` method
   - Ensure it recursively merges nested objects (it does use deep_merge)
   - Test with plugins that have nested required fields

3. **Check if defaults are applied for nested required fields**:
   - Review how `generate_default_config()` extracts defaults from nested schemas
   - Verify nested required fields with defaults are included

4. **Test with problematic plugins**:
   - `ledmatrix-weather`: required fields `api_key`, `location_city` (check if defaults exist)
   - `mqtt-notifications`: required field `mqtt` object (check if default exists)
   - `text-display`: required field `text` (check if default exists)
   - `ledmatrix-music`: required field `preferred_source` (check if default exists)

5. **If defaults don't exist in schemas**:
   - Either add defaults to schemas, OR
   - Make fields optional in schemas if they're truly optional

### Expected Outcome
- Plugins with required fields that have schema defaults should pass validation
- Issue count further reduced from ~30-40 to ~5-10

## Priority 3: Calendar Plugin Schema Cleanup

### Problem
Calendar plugin config has fields not in schema:
- `show_all_day` (config) but schema has `show_all_day_events` (field name mismatch)
- `date_format` (not in schema, not used in manager.py)
- `time_format` (not in schema, not used in manager.py)

### Investigation Results
- Schema defines: `show_all_day_events` (boolean, default: true)
- Manager.py uses: `show_all_day_events` (line 82: `config.get('show_all_day_events', True)`)
- Config has: `show_all_day` (wrong field name - should be `show_all_day_events`)
- `date_format` and `time_format` appear to be deprecated (not used in manager.py)

### Solution

**File**: `config/config.json` → `calendar` section

### Implementation Steps

1. **Fix field name mismatch**:
   - Rename `show_all_day` → `show_all_day_events` in config.json
   - This matches the schema and manager.py code

2. **Remove deprecated fields**:
   - Remove `date_format` from config (not used in code)
   - Remove `time_format` from config (not used in code)

3. **Alternative (if fields are needed)**: Add `date_format` and `time_format` to schema
   - Only if these fields should be supported
   - Check if they're used anywhere else in the codebase

4. **Test calendar plugin**:
   - Run audit for calendar plugin specifically
   - Verify no extra field warnings remain
   - Test calendar plugin functionality to ensure it still works

### Expected Outcome
- Calendar plugin shows 0 extra field warnings
- Final issue count: ~3-5 (only edge cases remain)

## Testing Strategy

### After Each Priority Fix

1. **Run local audit**:
   ```bash
   python scripts/audit_plugin_configs.py
   ```

2. **Check issue count reduction**:
   - Priority 1: Should drop from 186 to ~30-40
   - Priority 2: Should drop from ~30-40 to ~5-10
   - Priority 3: Should drop from ~5-10 to ~3-5

3. **Review specific plugin results**:
   ```bash
   python scripts/audit_plugin_configs.py --plugin <plugin-id>
   ```

### After All Fixes

1. **Full audit run**:
   ```bash
   python scripts/audit_plugin_configs.py
   ```

2. **Deploy to Pi**:
   ```bash
   ./scripts/deploy_to_pi.sh src/plugin_system/schema_manager.py web_interface/blueprints/api_v3.py
   ```

3. **Run audit on Pi**:
   ```bash
   ./scripts/run_audit_on_pi.sh
   ```

4. **Manual web interface testing**:
   - Access each problematic plugin's config page
   - Try saving configuration
   - Verify no validation errors appear
   - Check that configs save successfully

## Success Criteria

- [ ] Priority 1: All "enabled" related validation errors eliminated
- [ ] Priority 1: Issue count reduced from 186 to ~30-40
- [ ] Priority 2: Plugins with required fields + defaults pass validation
- [ ] Priority 2: Issue count reduced to ~5-10
- [ ] Priority 3: Calendar plugin extra field warnings resolved
- [ ] Priority 3: Final issue count at ~3-5 (only edge cases)
- [ ] All fixes work on Pi (not just local)
- [ ] Web interface saves configs without validation errors

## Files to Modify

1. `src/plugin_system/schema_manager.py` - Remove core properties from required array
2. `plugins/calendar/config_schema.json` OR `config/config.json` - Calendar cleanup (if needed)
3. `web_interface/blueprints/api_v3.py` - May need minor adjustments for default merging (if needed)

## Risk Assessment

**Priority 1**: Low risk - Only affects validation logic, doesn't change behavior
**Priority 2**: Low risk - Only ensures defaults are applied (already intended behavior)
**Priority 3**: Very low risk - Only affects calendar plugin, cosmetic issue

All changes are backward compatible and improve the system rather than changing core functionality.

