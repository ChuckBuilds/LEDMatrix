# Impact Explanation: Config Schema Validation Fixes

## Current Problem (Before Fixes)

### What Users Experience Now

**Scenario**: User wants to configure a plugin (e.g., hockey-scoreboard)

1. User opens web interface → Plugins tab → hockey-scoreboard configuration
2. User changes some settings (e.g., favorite teams, display duration)
3. User clicks "Save Configuration" button
4. **ERROR**: "Configuration validation failed: Missing required field: 'enabled'"
5. **RESULT**: Configuration changes are NOT saved
6. User is frustrated - can't save their configuration

**Why This Happens**:
- Plugin schema has `"required": ["enabled"]`
- `enabled` field is system-managed (controlled by PluginManager/enable toggle)
- Config doesn't have `enabled` field (it's managed separately)
- Validation fails because `enabled` is required but missing

### Real-World Impact

- **186 validation errors** across all 20 plugins
- **ALL plugins** currently fail to save configs via web interface
- Users cannot configure plugins through the UI
- This is a **blocking issue** for plugin configuration

---

## Priority 1 Fix: Remove Core Properties from Required Array

### What Changes Technically

**File**: `src/plugin_system/schema_manager.py`

**Change**: After injecting core properties (`enabled`, `display_duration`, `live_priority`) into schema properties, also remove them from the `required` array.

**Before**:
```python
# Core properties added to properties (allowed)
enhanced_schema["properties"]["enabled"] = {...}

# But still in required array (validation fails if missing)
enhanced_schema["required"] = ["enabled", ...]  # ❌ Still requires enabled
```

**After**:
```python
# Core properties added to properties (allowed)
enhanced_schema["properties"]["enabled"] = {...}

# Removed from required array (not required for validation)
enhanced_schema["required"] = [...]  # ✅ enabled removed, validation passes
```

### Why This Is Correct

- `enabled` is managed by PluginManager (system-level concern)
- User doesn't set `enabled` in plugin config form (it's a separate toggle)
- Config validation should check user-provided config, not system-managed fields
- Core properties should be **allowed** but not **required**

### User Experience After Fix

**Scenario**: User configures hockey-scoreboard plugin

1. User opens web interface → Plugins tab → hockey-scoreboard configuration
2. User changes settings (favorite teams, display duration)
3. User clicks "Save Configuration" button
4. **SUCCESS**: Configuration saves without errors
5. Changes are persisted and plugin uses new configuration

**Impact**:
- ✅ All 20 plugins can now save configs successfully
- ✅ ~150 validation errors eliminated (all "enabled" related)
- ✅ Users can configure plugins normally
- ✅ This is the **primary fix** that unblocks plugin configuration

### Technical Details

- Issue count: 186 → ~30-40 (most issues resolved)
- No breaking changes - only affects validation logic
- Backward compatible - configs that work will continue to work
- Makes validation logic match the actual architecture

---

## Priority 2 Fix: Verify Default Merging Logic

### What This Addresses

Some plugins have required fields that have default values in their schemas. For example:
- `calendar` plugin requires `credentials_file` but schema provides default: `"credentials.json"`
- When user saves config without this field, the default should be applied automatically
- Then validation passes because the field is present (with default value)

### Current Behavior

The code already has default merging logic:
```python
defaults = schema_mgr.generate_default_config(plugin_id, use_cache=True)
plugin_config = schema_mgr.merge_with_defaults(plugin_config, defaults)
```

**But audit shows some plugins still fail**, which suggests either:
1. Default merging isn't working correctly for all cases, OR
2. Some required fields don't have defaults in schemas (schema design issue)

### What We'll Verify

1. Check if `merge_with_defaults()` handles nested objects correctly
2. Verify defaults are applied before validation runs
3. Test with problematic plugins to see why they still fail
4. Fix any issues found OR identify that schemas need defaults added

### User Experience Impact

**If defaults are working correctly**:
- Users don't need to manually add every field
- Fields with defaults "just work" automatically
- Easier plugin configuration

**If defaults aren't working**:
- After fix, plugins with schema defaults will validate correctly
- Fewer manual field entries required
- Better user experience

### Technical Details

- Issue count: ~30-40 → ~5-10 (after Priority 1)
- Addresses remaining validation failures
- May involve schema updates if defaults are missing
- Improves robustness of config system

---

## Priority 3 Fix: Calendar Plugin Cleanup

### What This Addresses

Calendar plugin has configuration fields that don't match its schema:
- `show_all_day` in config, but schema defines `show_all_day_events` (field name mismatch)
- `date_format` and `time_format` in config but not in schema (deprecated fields)

### Current Problems

1. **Field name mismatch**: `show_all_day` vs `show_all_day_events`
   - Schema filtering removes `show_all_day` during save
   - User's setting for all-day events doesn't actually work
   - This is a **bug** where the setting is ignored

2. **Deprecated fields**: `date_format` and `time_format`
   - Not used in plugin code
   - Confusing to see in config
   - Schema filtering removes them anyway (just creates warnings)

### What We'll Fix

1. **Fix field name**: Rename `show_all_day` → `show_all_day_events` in config
   - Makes config match schema
   - Fixes bug where all-day events setting doesn't work

2. **Remove deprecated fields**: Remove `date_format` and `time_format` from config
   - Cleans up config file
   - Removes confusion
   - No functional impact (fields weren't used)

### User Experience Impact

**Before**:
- User sets "show all day events" = true
- Setting doesn't work (field name mismatch)
- User confused why setting isn't applied

**After**:
- User sets "show all day events" = true
- Setting works correctly (field name matches schema)
- Config is cleaner and matches schema

### Technical Details

- Issue count: ~5-10 → ~3-5 (after Priority 1 & 2)
- Fixes a bug (show_all_day_events not working)
- Cleanup/improvement, not critical
- Only affects calendar plugin

---

## Summary: Real-World Impact

### Before All Fixes

**User tries to configure any plugin**:
- ❌ Config save fails with validation errors
- ❌ Cannot configure plugins via web interface
- ❌ 186 validation errors across all plugins
- ❌ System is essentially broken for plugin configuration

### After Priority 1 Fix

**User tries to configure any plugin**:
- ✅ Config saves successfully for most plugins
- ✅ Can configure plugins via web interface
- ✅ ~150 errors resolved (enabled field issues)
- ✅ System is functional for plugin configuration

**Remaining issues**: ~30-40 validation errors
- Mostly fields without defaults that need user input
- Still some plugins that can't save (but most work)

### After Priority 2 Fix

**User tries to configure any plugin**:
- ✅ Config saves successfully for almost all plugins
- ✅ Defaults applied automatically (easier configuration)
- ✅ ~30-40 errors → ~5-10 errors
- ✅ System is robust and user-friendly

**Remaining issues**: ~5-10 edge cases
- Complex validation scenarios
- Possibly some schema design issues

### After Priority 3 Fix

**All plugins**:
- ✅ Configs match schemas exactly
- ✅ No confusing warnings
- ✅ Calendar plugin bug fixed (show_all_day_events works)
- ✅ ~3-5 remaining edge cases only
- ✅ System is clean and fully functional

---

## Bottom Line

**Current State**: Plugin configuration saving is broken (186 errors, all plugins fail)

**After Fixes**: Plugin configuration saving works correctly (3-5 edge cases remain, all plugins functional)

**User Impact**: Users can configure plugins successfully instead of getting validation errors

**Technical Impact**: Validation logic correctly handles system-managed fields and schema defaults

