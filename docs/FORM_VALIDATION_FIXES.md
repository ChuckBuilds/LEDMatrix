# Form Validation Fixes - Preventing "Invalid Form Control" Errors

## Problem

Browser was throwing errors: "An invalid form control with name='...' is not focusable" when:
- Number inputs had values outside their min/max constraints
- These fields were in collapsed/hidden nested sections
- Browser couldn't focus hidden invalid fields to show validation errors

## Root Cause

1. **Value Clamping Missing**: Number inputs were generated with values that didn't respect min/max constraints
2. **HTML5 Validation on Hidden Fields**: Browser validation tried to validate hidden fields but couldn't focus them
3. **No Pre-Submit Validation**: Forms didn't fix invalid values before submission

## Fixes Applied

### 1. Plugin Configuration Form (`plugins.html`)

**File**: `web_interface/templates/v3/partials/plugins.html`

**Changes**:
- ✅ Added value clamping in `generateFieldHtml()` (lines 1825-1844)
  - Clamps values to min/max when generating number inputs
  - Uses default value if provided
  - Ensures all generated fields have valid values
- ✅ Added `novalidate` attribute to form (line 1998)
- ✅ Added pre-submit validation fix in `handlePluginConfigSubmit()` (lines 1518-1533)
  - Fixes any invalid values before processing form data
  - Prevents "invalid form control is not focusable" errors

### 2. Plugin Config in Base Template (`base.html`)

**File**: `web_interface/templates/v3/base.html`

**Changes**:
- ✅ Added value clamping in number input generation (lines 1386-1407)
  - Same logic as plugins.html
  - Clamps values to min/max constraints
- ✅ Fixed display_duration input (line 1654)
  - Uses `Math.max(5, Math.min(300, value))` to clamp value
- ✅ Added global `fixInvalidNumberInputs()` function (lines 2409-2425)
  - Can be called from any form's onsubmit handler
  - Fixes invalid number inputs before submission

### 3. Display Settings Form (`display.html`)

**File**: `web_interface/templates/v3/partials/display.html`

**Changes**:
- ✅ Added `novalidate` attribute to form (line 13)
- ✅ Added `onsubmit="fixInvalidNumberInputs(this); return true;"` (line 14)
- ✅ Added local `fixInvalidNumberInputs()` function as fallback (lines 260-278)

### 4. Durations Form (`durations.html`)

**File**: `web_interface/templates/v3/partials/durations.html`

**Changes**:
- ✅ Added `novalidate` attribute to form (line 13)
- ✅ Added `onsubmit="fixInvalidNumberInputs(this); return true;"` (line 14)

## Implementation Details

### Value Clamping Logic

```javascript
// Ensure value respects min/max constraints
let fieldValue = value !== undefined ? value : (prop.default !== undefined ? prop.default : '');
if (fieldValue !== '' && fieldValue !== undefined && fieldValue !== null) {
    const numValue = typeof fieldValue === 'string' ? parseFloat(fieldValue) : fieldValue;
    if (!isNaN(numValue)) {
        // Clamp value to min/max if constraints exist
        if (prop.minimum !== undefined && numValue < prop.minimum) {
            fieldValue = prop.minimum;
        } else if (prop.maximum !== undefined && numValue > prop.maximum) {
            fieldValue = prop.maximum;
        } else {
            fieldValue = numValue;
        }
    }
}
```

### Pre-Submit Validation Fix

```javascript
// Fix invalid hidden fields before submission
const allInputs = form.querySelectorAll('input[type="number"]');
allInputs.forEach(input => {
    const min = parseFloat(input.getAttribute('min'));
    const max = parseFloat(input.getAttribute('max'));
    const value = parseFloat(input.value);
    
    if (!isNaN(value)) {
        if (!isNaN(min) && value < min) {
            input.value = min;
        } else if (!isNaN(max) && value > max) {
            input.value = max;
        }
    }
});
```

## Files Modified

1. ✅ `web_interface/templates/v3/partials/plugins.html`
   - Value clamping in field generation
   - `novalidate` on forms
   - Pre-submit validation fix

2. ✅ `web_interface/templates/v3/base.html`
   - Value clamping in field generation
   - Fixed display_duration input
   - Global `fixInvalidNumberInputs()` function

3. ✅ `web_interface/templates/v3/partials/display.html`
   - `novalidate` on form
   - `onsubmit` handler
   - Local fallback function

4. ✅ `web_interface/templates/v3/partials/durations.html`
   - `novalidate` on form
   - `onsubmit` handler

## Prevention Strategy

### For Future Forms

1. **Always clamp number input values** when generating forms:
   ```javascript
   // Clamp value to min/max
   if (min !== undefined && value < min) value = min;
   if (max !== undefined && value > max) value = max;
   ```

2. **Add `novalidate` to forms** that use custom validation:
   ```html
   <form novalidate onsubmit="fixInvalidNumberInputs(this); return true;">
   ```

3. **Use the global helper** for pre-submit validation:
   ```javascript
   window.fixInvalidNumberInputs(form);
   ```

4. **Check for hidden fields** - If fields can be hidden (collapsed sections), ensure:
   - Values are valid when fields are generated
   - Pre-submit validation fixes any remaining issues
   - Form has `novalidate` to prevent HTML5 validation

## Testing

### Test Cases

1. ✅ Number input with value=0, min=60 → Should clamp to 60
2. ✅ Number input with value=1000, max=600 → Should clamp to 600
3. ✅ Hidden field with invalid value → Should be fixed on submit
4. ✅ Form submission with invalid values → Should fix before submit
5. ✅ Nested sections with number inputs → Should work correctly

### Manual Testing

1. Open plugin configuration with nested sections
2. Collapse a section with number inputs
3. Try to submit form → Should work without errors
4. Check browser console → Should have no validation errors

## Related Issues

- **Issue**: "An invalid form control with name='...' is not focusable"
- **Cause**: Hidden fields with invalid values (outside min/max)
- **Solution**: Value clamping + pre-submit validation + `novalidate`

## Notes

- We use `novalidate` because we do server-side validation anyway
- The pre-submit fix is a safety net for any edge cases
- Value clamping at generation time prevents most issues
- All fixes are backward compatible

