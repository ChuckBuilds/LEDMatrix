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
         * Render the checkbox group widget
         * Note: This widget is currently server-side rendered via Jinja2 template.
         * This registration ensures the handlers are available globally.
         */
        render: function(container, config, value, options) {
            // For now, widgets are server-side rendered
            // This function is a placeholder for future client-side rendering
            console.log('[CheckboxGroupWidget] Render called (server-side rendered)');
        },
        
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
            
            // Update checkboxes
            const checkboxes = document.querySelectorAll(`input[type="checkbox"][data-checkbox-group="${fieldId}"]`);
            checkboxes.forEach(checkbox => {
                const optionValue = checkbox.getAttribute('data-option-value') || checkbox.value;
                checkbox.checked = values.includes(optionValue);
            });
            
            // Update hidden input
            updateCheckboxGroupData(fieldId);
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

    console.log('[CheckboxGroupWidget] Checkbox group widget registered');
})();
