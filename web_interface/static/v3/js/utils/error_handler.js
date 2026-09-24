/* global showNotification */
/**
 * Frontend error handling utilities.
 *
 * Provides user-friendly error formatting and display with enhanced UI.
 */

/**
 * Comprehensive error code to user-friendly message mapping.
 * Used only when the server response carries no message of its own.
 */
const ERROR_MESSAGES = {
    // Configuration errors
    'CONFIG_SAVE_FAILED': "Couldn't save your settings. Check the values and try again",
    'CONFIG_LOAD_FAILED': "Couldn't load your settings. Reload the page to try again",
    'CONFIG_VALIDATION_FAILED': 'Some settings have invalid values. Fix the highlighted fields and save again',
    'CONFIG_ROLLBACK_FAILED': "Couldn't restore the previous settings. Try again, or restore from Backup & Restore",

    // Plugin errors
    'PLUGIN_NOT_FOUND': "That plugin isn't installed. Check the Plugin Manager",
    'PLUGIN_INSTALL_FAILED': "Couldn't install the plugin. Check the Pi's internet connection and try again",
    'PLUGIN_UPDATE_FAILED': "Couldn't update the plugin. Try again in a moment",
    'PLUGIN_UNINSTALL_FAILED': "Couldn't uninstall the plugin. Try again in a moment",
    'PLUGIN_LOAD_FAILED': "The plugin couldn't start. Check the Logs tab for details",
    'PLUGIN_OPERATION_CONFLICT': 'Another plugin operation is still running. Wait for it to finish, then try again',

    // Validation errors
    'VALIDATION_ERROR': 'Some values are invalid. Fix them and try again',
    'SCHEMA_VALIDATION_FAILED': "Some settings don't match what the plugin expects. Fix them and save again",
    'INVALID_INPUT': "That value isn't valid. Check it and try again",

    // Network errors
    'NETWORK_ERROR': "Couldn't reach the LEDMatrix device. Check that it's on and connected, then try again",
    'API_ERROR': 'The request failed. Try again in a moment',
    'TIMEOUT': 'This took too long to respond. Try again in a moment',

    // Permission errors
    'PERMISSION_DENIED': "LEDMatrix doesn't have permission to do that. See the troubleshooting guide",
    'FILE_PERMISSION_ERROR': "LEDMatrix can't write a file it needs. See the troubleshooting guide",

    // System errors
    'SYSTEM_ERROR': 'Something went wrong on the device. Check the Logs tab for details',
    'SERVICE_UNAVAILABLE': 'The LEDMatrix service is not running. Restart it from the Overview tab',

    // Unknown errors
    'UNKNOWN_ERROR': 'Something went wrong. Try again, and check the Logs tab if it keeps happening'
};

/**
 * Error code to troubleshooting documentation links.
 */
const TROUBLESHOOTING_URL = 'https://github.com/ChuckBuilds/LEDMatrix/blob/main/docs/TROUBLESHOOTING.md';
const ERROR_DOCS = {
    'CONFIG_SAVE_FAILED': TROUBLESHOOTING_URL + '#4-check-configuration',
    'CONFIG_VALIDATION_FAILED': TROUBLESHOOTING_URL + '#4-check-configuration',
    'PLUGIN_INSTALL_FAILED': TROUBLESHOOTING_URL + '#plugin-issues',
    'PLUGIN_OPERATION_CONFLICT': TROUBLESHOOTING_URL + '#plugin-issues',
    'PERMISSION_DENIED': TROUBLESHOOTING_URL + '#permission-issues',
    'FILE_PERMISSION_ERROR': TROUBLESHOOTING_URL + '#permission-issues'
};

/**
 * Format error message for display to user.
 *
 * @param {Object} error - Error object from API response
 * @returns {string} Formatted error message
 */
function formatError(error) {
    if (!error) {
        return ERROR_MESSAGES.UNKNOWN_ERROR;
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
        const message = ERROR_MESSAGES[error.error_code] || error.message || ERROR_MESSAGES.UNKNOWN_ERROR;

        // Add details if available
        if (error.details) {
            return `${message}: ${error.details}`;
        }

        return message;
    }

    return ERROR_MESSAGES.UNKNOWN_ERROR;
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
    if (options.showDetails !== false && (suggestions.length > 0 || docLink || error?.details)) {
        showErrorModal(error, context, message, suggestions, docLink);
    } else {
        // Simple notification
        showNotification(fullMessage, 'error');
    }
}

// Release function for the open error modal's focus trap (see utils/dialog.js).
let errorModalRelease = null;

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
    error = error || {};
    suggestions = suggestions || [];

    // Create modal container if it doesn't exist
    let modalContainer = document.getElementById('error-modal-container');
    if (!modalContainer) {
        modalContainer = document.createElement('div');
        modalContainer.id = 'error-modal-container';
        modalContainer.className = 'fixed inset-0 z-50 overflow-y-auto';
        modalContainer.style.display = 'none';
        document.body.appendChild(modalContainer);
    }

    // Re-opening while open: release the previous trap before replacing it.
    if (errorModalRelease) {
        errorModalRelease();
        errorModalRelease = null;
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
            <a href="${escapeHtml(docLink)}" target="_blank" rel="noopener noreferrer" class="text-blue-600 hover:text-blue-800 text-sm underline">
                <i class="fas fa-book mr-1" aria-hidden="true"></i>View troubleshooting guide<span style="position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;"> (opens in a new tab)</span>
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
            <div class="fixed inset-0 bg-gray-500 bg-opacity-75 transition-opacity" aria-hidden="true" onclick="window.errorHandler.closeErrorModal()"></div>

            <div id="error-modal-panel" class="inline-block align-bottom bg-white rounded-lg text-left overflow-hidden shadow-xl transform transition-all sm:my-8 sm:align-middle sm:max-w-lg sm:w-full" style="position:relative;">
                <div class="bg-white px-4 pt-5 pb-4 sm:p-6 sm:pb-4">
                    <div class="sm:flex sm:items-start">
                        <div class="mx-auto flex-shrink-0 flex items-center justify-center h-12 w-12 rounded-full bg-red-100 sm:mx-0 sm:h-10 sm:w-10" aria-hidden="true">
                            <i class="fas fa-exclamation-triangle text-red-600"></i>
                        </div>
                        <div class="mt-3 text-center sm:mt-0 sm:ml-4 sm:text-left flex-1">
                            <h3 id="error-modal-title" class="text-lg leading-6 font-medium text-gray-900">Something went wrong</h3>
                            <div class="mt-2" id="error-modal-description">
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
                    <button type="button" id="error-modal-copy-btn"
                            class="w-full inline-flex justify-center rounded-md border border-transparent shadow-sm px-4 py-2 bg-blue-600 text-base font-medium text-white hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500 sm:ml-3 sm:w-auto sm:text-sm">
                        <i class="fas fa-copy mr-2" aria-hidden="true"></i>Copy Error Details
                    </button>
                    <button type="button" id="error-modal-close-btn" onclick="window.errorHandler.closeErrorModal()"
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

    const panel = modalContainer.querySelector('#error-modal-panel');
    if (panel) {
        panel.setAttribute('aria-describedby', 'error-modal-description');
        if (window.LEDDialog) {
            errorModalRelease = window.LEDDialog.trap(panel, {
                labelledBy: 'error-modal-title',
                initialFocus: '#error-modal-close-btn',
                onEscape: closeErrorModal
            });
        }
    }
}

/**
 * Close the error modal.
 */
function closeErrorModal() {
    const modalContainer = document.getElementById('error-modal-container');
    if (modalContainer) {
        modalContainer.style.display = 'none';
    }
    if (errorModalRelease) {
        const release = errorModalRelease;
        errorModalRelease = null;
        release();
    }
}

function escapeHtml(text) { return window.LEDEscape.html(text); }

/**
 * Copy error details to clipboard.
 *
 * @param {Object} error - Error object from API response
 */
function copyErrorDetails(error) {
    const errorText = JSON.stringify(error, null, 2);
    function copyFailed(err) {
        console.error('Failed to copy error details:', err);
        showNotification("Couldn't copy to the clipboard. Open Technical details and copy the text by hand.", 'warning');
    }

    if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(errorText).then(() => {
            showNotification('Error details copied to clipboard', 'success');
        }).catch(copyFailed);
    } else {
        // Fallback for older browsers
        const textArea = document.createElement('textarea');
        textArea.value = errorText;
        textArea.style.position = 'fixed';
        textArea.style.opacity = '0';
        // Keep focus inside the open dialog while copying.
        const panel = document.getElementById('error-modal-panel');
        const host = panel && panel.offsetParent !== null ? panel : document.body;
        const returnFocus = document.activeElement;
        host.appendChild(textArea);
        textArea.select();
        try {
            document.execCommand('copy');
            showNotification('Error details copied to clipboard', 'success');
        } catch (err) {
            copyFailed(err);
        }
        host.removeChild(textArea);
        if (returnFocus && typeof returnFocus.focus === 'function') returnFocus.focus();
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
