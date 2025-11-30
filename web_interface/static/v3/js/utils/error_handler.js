/**
 * Frontend error handling utilities.
 * 
 * Provides user-friendly error formatting and display.
 */

/**
 * Format error message for display to user.
 * 
 * @param {Object} error - Error object from API response
 * @returns {string} Formatted error message
 */
function formatError(error) {
    if (!error) {
        return 'An unknown error occurred';
    }
    
    // If error is a string, return it
    if (typeof error === 'string') {
        return error;
    }
    
    // If error has a message, use it
    if (error.message) {
        return error.message;
    }
    
    // If error has error_code, format it
    if (error.error_code) {
        const errorMessages = {
            'CONFIG_SAVE_FAILED': 'Failed to save configuration',
            'CONFIG_LOAD_FAILED': 'Failed to load configuration',
            'CONFIG_VALIDATION_FAILED': 'Configuration validation failed',
            'PLUGIN_NOT_FOUND': 'Plugin not found',
            'PLUGIN_INSTALL_FAILED': 'Failed to install plugin',
            'PLUGIN_UPDATE_FAILED': 'Failed to update plugin',
            'PLUGIN_UNINSTALL_FAILED': 'Failed to uninstall plugin',
            'PLUGIN_OPERATION_CONFLICT': 'Plugin operation conflict',
            'VALIDATION_ERROR': 'Validation error',
            'INVALID_INPUT': 'Invalid input',
            'PERMISSION_DENIED': 'Permission denied',
            'NETWORK_ERROR': 'Network error',
            'TIMEOUT': 'Operation timed out'
        };
        
        const message = errorMessages[error.error_code] || error.message || 'An error occurred';
        
        // Add details if available
        if (error.details) {
            return `${message}: ${error.details}`;
        }
        
        return message;
    }
    
    return 'An error occurred';
}

/**
 * Get suggested fixes for an error.
 * 
 * @param {Object} error - Error object from API response
 * @returns {Array<string>} Array of suggested fixes
 */
function getSuggestedFixes(error) {
    if (!error || !error.suggested_fixes) {
        return [];
    }
    
    return error.suggested_fixes;
}

/**
 * Display error with suggestions.
 * 
 * @param {Object} error - Error object from API response
 * @param {string} context - Optional context about what was being done
 */
function displayError(error, context = null) {
    const message = formatError(error);
    const suggestions = getSuggestedFixes(error);
    
    let fullMessage = message;
    if (context) {
        fullMessage = `${context}: ${message}`;
    }
    
    if (suggestions.length > 0) {
        fullMessage += '\n\nSuggested fixes:\n' + suggestions.map(s => `• ${s}`).join('\n');
    }
    
    if (typeof showNotification === 'function') {
        showNotification(fullMessage, 'error');
    } else {
        console.error('Error:', fullMessage);
        alert(fullMessage);
    }
}

/**
 * Copy error details to clipboard.
 * 
 * @param {Object} error - Error object from API response
 */
function copyErrorDetails(error) {
    const errorText = JSON.stringify(error, null, 2);
    
    if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(errorText).then(() => {
            if (typeof showNotification === 'function') {
                showNotification('Error details copied to clipboard', 'success');
            }
        }).catch(err => {
            console.error('Failed to copy error details:', err);
        });
    } else {
        // Fallback for older browsers
        const textArea = document.createElement('textarea');
        textArea.value = errorText;
        textArea.style.position = 'fixed';
        textArea.style.opacity = '0';
        document.body.appendChild(textArea);
        textArea.select();
        try {
            document.execCommand('copy');
            if (typeof showNotification === 'function') {
                showNotification('Error details copied to clipboard', 'success');
            }
        } catch (err) {
            console.error('Failed to copy error details:', err);
        }
        document.body.removeChild(textArea);
    }
}

// Export functions
if (typeof module !== 'undefined' && module.exports) {
    module.exports = {
        formatError,
        getSuggestedFixes,
        displayError,
        copyErrorDetails
    };
} else {
    // Make available globally
    window.errorHandler = {
        formatError,
        getSuggestedFixes,
        displayError,
        copyErrorDetails
    };
}

