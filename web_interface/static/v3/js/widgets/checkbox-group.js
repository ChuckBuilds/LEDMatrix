/**
 * Checkbox Group Widget
 * 
 * Handles multi-select checkbox groups for array fields with enum items.
 * Updates a hidden input with JSON array of selected values.
 * 
 * @module CheckboxGroupWidget
 */

(function() {
    'use strict';

    // Ensure LEDMatrixWidgets registry exists
    if (typeof window.LEDMatrixWidgets === 'undefined') {
        console.error('[CheckboxGroupWidget] LEDMatrixWidgets registry not found. Load registry.js first.');
        return;
    }

    /**
     * Register the checkbox-group widget
     */
    window.LEDMatrixWidgets.register('checkbox-group', {
        name: 'Checkbox Group Widget',
        version: '1.0.0',
        
        /**
         * No-op: plugin_config.html renders this widget server-side. The
         * registration exists for getValue/setValue and updateCheckboxGroupData.
         */
        render: function() {},
        
        /**
         * Get current value from widget
         * @param {string} fieldId - Field ID
         * @returns {Array} Array of selected values
         */
        getValue: function(fieldId) {
            const hiddenInput = document.getElementById(`${fieldId}_data`);
            if (hiddenInput && hiddenInput.value) {
                try {
                    return JSON.parse(hiddenInput.value);
                } catch (e) {
                    console.error('Error parsing checkbox group data:', e);
                    return [];
                }
            }
            return [];
        },
        
        /**
         * Set value in widget
         * @param {string} fieldId - Field ID
         * @param {Array} values - Array of values to select
         */
        setValue: function(fieldId, values) {
            if (!Array.isArray(values)) {
                console.error('[CheckboxGroupWidget] setValue expects an array');
                return;
            }
            
            // Normalize values to strings for consistent comparison
            const normalizedValues = values.map(String);
            
            // Update checkboxes
            const checkboxes = document.querySelectorAll(`input[type="checkbox"][data-checkbox-group="${fieldId}"]`);
            checkboxes.forEach(checkbox => {
                const optionValue = checkbox.getAttribute('data-option-value') || checkbox.value;
                // Normalize optionValue to string for comparison
                checkbox.checked = normalizedValues.includes(String(optionValue));
            });
            
            // Update hidden input
            window.updateCheckboxGroupData(fieldId);
        },
        
        handlers: {
            // Handlers are attached to window for backwards compatibility
        }
    });

    /**
     * Update checkbox group data in hidden input
     * Called when any checkbox in the group changes
     * @param {string} fieldId - Field ID
     */
    window.updateCheckboxGroupData = function(fieldId) {
        // Update hidden _data input with currently checked values
        const hiddenInput = document.getElementById(fieldId + '_data');
        if (!hiddenInput) {
            console.warn(`[CheckboxGroupWidget] Hidden input not found for fieldId: ${fieldId}`);
            return;
        }
        
        const checkboxes = document.querySelectorAll(`input[type="checkbox"][data-checkbox-group="${fieldId}"]`);
        const selectedValues = [];
        
        checkboxes.forEach(checkbox => {
            if (checkbox.checked) {
                const optionValue = checkbox.getAttribute('data-option-value') || checkbox.value;
                selectedValues.push(optionValue);
            }
        });
        
        hiddenInput.value = JSON.stringify(selectedValues);
        
        // Trigger change event for form validation
        const event = new CustomEvent('widget-change', {
            detail: { fieldId, value: selectedValues },
            bubbles: true,
            cancelable: true
        });
        hiddenInput.dispatchEvent(event);
    };
})();
