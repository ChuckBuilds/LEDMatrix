/**
 * Frontend error handling utilities.
 * 
 * Provides user-friendly error formatting and display with enhanced UI.
 */

/**
 * Comprehensive error code to user-friendly message mapping.
 */
const ERROR_MESSAGES = {
    // Configuration errors
    'CONFIG_SAVE_FAILED': 'Failed to save configuration',
    'CONFIG_LOAD_FAILED': 'Failed to load configuration',
    'CONFIG_VALIDATION_FAILED': 'Configuration validation failed',
    'CONFIG_ROLLBACK_FAILED': 'Failed to rollback configuration',
    
    // Plugin errors
    'PLUGIN_NOT_FOUND': 'Plugin not found',
    'PLUGIN_INSTALL_FAILED': 'Failed to install plugin',
    'PLUGIN_UPDATE_FAILED': 'Failed to update plugin',
    'PLUGIN_UNINSTALL_FAILED': 'Failed to uninstall plugin',
    'PLUGIN_LOAD_FAILED': 'Failed to load plugin',
    'PLUGIN_OPERATION_CONFLICT': 'Plugin operation conflict - another operation is in progress',
    
    // Validation errors
    'VALIDATION_ERROR': 'Validation error',
    'SCHEMA_VALIDATION_FAILED': 'Configuration schema validation failed',
    'INVALID_INPUT': 'Invalid input provided',
    
    // Network errors
    'NETWORK_ERROR': 'Network error occurred',
    'API_ERROR': 'API request failed',
    'TIMEOUT': 'Operation timed out',
    
    // Permission errors
    'PERMISSION_DENIED': 'Permission denied',
    'FILE_PERMISSION_ERROR': 'File permission error',
    
    // System errors
    'SYSTEM_ERROR': 'System error occurred',
    'SERVICE_UNAVAILABLE': 'Service unavailable',
    
    // Unknown errors
    'UNKNOWN_ERROR': 'An unknown error occurred'
};

/**
 * Error code to troubleshooting documentation links.
 */
const ERROR_DOCS = {
    'CONFIG_SAVE_FAILED': 'https://github.com/your-repo/LEDMatrix/wiki/Troubleshooting#configuration-errors',
    'CONFIG_VALIDATION_FAILED': 'https://github.com/your-repo/LEDMatrix/wiki/Troubleshooting#validation-errors',
    'PLUGIN_INSTALL_FAILED': 'https://github.com/your-repo/LEDMatrix/wiki/Troubleshooting#plugin-installation',
    'PLUGIN_OPERATION_CONFLICT': 'https://github.com/your-repo/LEDMatrix/wiki/Troubleshooting#plugin-operations',
    'PERMISSION_DENIED': 'https://github.com/your-repo/LEDMatrix/wiki/Troubleshooting#permissions',
    'FILE_PERMISSION_ERROR': 'https://github.com/your-repo/LEDMatrix/wiki/Troubleshooting#permissions'
};

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
        const message = ERROR_MESSAGES[error.error_code] || error.message || 'An error occurred';
        
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
 * Display error with suggestions in a rich UI.
 * 
 * @param {Object} error - Error object from API response
 * @param {string} context - Optional context about what was being done
 * @param {Object} options - Display options
 * @param {boolean} options.showDetails - Whether to show detailed error modal
 * @param {boolean} options.showCopyButton - Whether to show copy button
 */
function displayError(error, context = null, options = {}) {
    const message = formatError(error);
    const suggestions = getSuggestedFixes(error);
    const errorCode = error?.error_code;
    const docLink = errorCode ? ERROR_DOCS[errorCode] : null;
    
    // Build full message
    let fullMessage = message;
    if (context) {
        fullMessage = `${context}: ${message}`;
    }
    
    if (suggestions.length > 0) {
        fullMessage += '\n\nSuggested fixes:\n' + suggestions.map(s => `• ${s}`).join('\n');
    }
    
    // If showDetails is true, show a rich error modal
    if (options.showDetails !== false && (suggestions.length > 0 || docLink || error.details)) {
        showErrorModal(error, context, message, suggestions, docLink);
    } else {
        // Simple notification
        if (typeof showNotification === 'function') {
            showNotification(fullMessage, 'error');
        } else {
            console.error('Error:', fullMessage);
            alert(fullMessage);
        }
    }
}

/**
 * Show a rich error modal with details, suggestions, and copy button.
 * 
 * @param {Object} error - Error object
 * @param {string} context - Context
 * @param {string} message - Formatted message
 * @param {Array<string>} suggestions - Suggested fixes
 * @param {string} docLink - Documentation link
 */
function showErrorModal(error, context, message, suggestions, docLink) {
    // Create modal container if it doesn't exist
    let modalContainer = document.getElementById('error-modal-container');
    if (!modalContainer) {
        modalContainer = document.createElement('div');
        modalContainer.id = 'error-modal-container';
        modalContainer.className = 'fixed inset-0 z-50 overflow-y-auto';
        modalContainer.style.display = 'none';
        document.body.appendChild(modalContainer);
    }
    
    // Build modal content
    const contextText = context ? `<div class="text-sm text-gray-600 mb-2">${escapeHtml(context)}</div>` : '';
    const suggestionsHtml = suggestions.length > 0 ? `
        <div class="mt-4">
            <h4 class="text-sm font-semibold text-gray-900 mb-2">Suggested fixes:</h4>
            <ul class="list-disc list-inside space-y-1 text-sm text-gray-700">
                ${suggestions.map(s => `<li>${escapeHtml(s)}</li>`).join('')}
            </ul>
        </div>
    ` : '';
    
    const docLinkHtml = docLink ? `
        <div class="mt-4">
            <a href="${docLink}" target="_blank" class="text-blue-600 hover:text-blue-800 text-sm underline">
                <i class="fas fa-book mr-1"></i>View troubleshooting guide
            </a>
        </div>
    ` : '';
    
    const detailsHtml = error.details ? `
        <div class="mt-4">
            <details class="cursor-pointer">
                <summary class="text-sm font-medium text-gray-700 hover:text-gray-900">Technical details</summary>
                <pre class="mt-2 text-xs bg-gray-100 p-3 rounded overflow-auto max-h-48 text-gray-800">${escapeHtml(error.details)}</pre>
            </details>
        </div>
    ` : '';
    
    const errorCodeHtml = error.error_code ? `
        <div class="mt-2 text-xs text-gray-500">
            Error code: <code class="bg-gray-100 px-1 py-0.5 rounded">${escapeHtml(error.error_code)}</code>
        </div>
    ` : '';
    
    modalContainer.innerHTML = `
        <div class="flex items-center justify-center min-h-screen px-4 pt-4 pb-20 text-center sm:block sm:p-0">
            <div class="fixed inset-0 bg-gray-500 bg-opacity-75 transition-opacity" onclick="window.errorHandler.closeErrorModal()"></div>
            
            <div class="inline-block align-bottom bg-white rounded-lg text-left overflow-hidden shadow-xl transform transition-all sm:my-8 sm:align-middle sm:max-w-lg sm:w-full">
                <div class="bg-white px-4 pt-5 pb-4 sm:p-6 sm:pb-4">
                    <div class="sm:flex sm:items-start">
                        <div class="mx-auto flex-shrink-0 flex items-center justify-center h-12 w-12 rounded-full bg-red-100 sm:mx-0 sm:h-10 sm:w-10">
                            <i class="fas fa-exclamation-triangle text-red-600"></i>
                        </div>
                        <div class="mt-3 text-center sm:mt-0 sm:ml-4 sm:text-left flex-1">
                            <h3 class="text-lg leading-6 font-medium text-gray-900">Error</h3>
                            <div class="mt-2">
                                ${contextText}
                                <p class="text-sm text-gray-500">${escapeHtml(message)}</p>
                                ${errorCodeHtml}
                                ${suggestionsHtml}
                                ${docLinkHtml}
                                ${detailsHtml}
                            </div>
                        </div>
                    </div>
                </div>
                <div class="bg-gray-50 px-4 py-3 sm:px-6 sm:flex sm:flex-row-reverse">
                    <button id="error-modal-copy-btn" 
                            class="w-full inline-flex justify-center rounded-md border border-transparent shadow-sm px-4 py-2 bg-blue-600 text-base font-medium text-white hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500 sm:ml-3 sm:w-auto sm:text-sm">
                        <i class="fas fa-copy mr-2"></i>Copy Error Details
                    </button>
                    <button onclick="window.errorHandler.closeErrorModal()" 
                            class="mt-3 w-full inline-flex justify-center rounded-md border border-gray-300 shadow-sm px-4 py-2 bg-white text-base font-medium text-gray-700 hover:bg-gray-50 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-indigo-500 sm:mt-0 sm:ml-3 sm:w-auto sm:text-sm">
                        Close
                    </button>
                </div>
            </div>
        </div>
    `;
    
    // Attach event listener to copy button
    const copyBtn = modalContainer.querySelector('#error-modal-copy-btn');
    if (copyBtn) {
        copyBtn.addEventListener('click', () => {
            copyErrorDetails(error);
        });
    }
    
    modalContainer.style.display = 'block';
}

/**
 * Close the error modal.
 */
function closeErrorModal() {
    const modalContainer = document.getElementById('error-modal-container');
    if (modalContainer) {
        modalContainer.style.display = 'none';
    }
}

/**
 * Escape HTML to prevent XSS.
 */
function escapeHtml(text) {
    if (typeof text !== 'string') {
        text = String(text);
    }
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
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
        copyErrorDetails,
        showErrorModal,
        closeErrorModal,
        ERROR_MESSAGES,
        ERROR_DOCS
    };
} else {
    // Make available globally
    window.errorHandler = {
        formatError,
        getSuggestedFixes,
        displayError,
        copyErrorDetails,
        showErrorModal,
        closeErrorModal,
        ERROR_MESSAGES,
        ERROR_DOCS
    };
}

