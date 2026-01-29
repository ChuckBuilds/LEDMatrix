/**
 * LEDMatrix Password Input Widget
 *
 * Password input with show/hide toggle and strength indicator.
 *
 * Schema example:
 * {
 *   "password": {
 *     "type": "string",
 *     "x-widget": "password-input",
 *     "x-options": {
 *       "placeholder": "Enter password",
 *       "showToggle": true,
 *       "showStrength": false,
 *       "minLength": 8,
 *       "requireUppercase": false,
 *       "requireNumber": false,
 *       "requireSpecial": false
 *     }
 *   }
 * }
 *
 * @module PasswordInputWidget
 */

(function() {
    'use strict';

    const base = window.BaseWidget ? new window.BaseWidget('PasswordInput', '1.0.0') : null;

    function escapeHtml(text) {
        if (base) return base.escapeHtml(text);
        const div = document.createElement('div');
        div.textContent = String(text);
        return div.innerHTML.replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    function sanitizeId(id) {
        if (base) return base.sanitizeId(id);
        return String(id).replace(/[^a-zA-Z0-9_-]/g, '_');
    }

    function triggerChange(fieldId, value) {
        if (base) {
            base.triggerChange(fieldId, value);
        } else {
            const event = new CustomEvent('widget-change', {
                detail: { fieldId, value },
                bubbles: true,
                cancelable: true
            });
            document.dispatchEvent(event);
        }
    }

    // Deterministic color class mapping to avoid Tailwind JIT purging
    const STRENGTH_COLORS = {
        gray: 'bg-gray-300',
        red: 'bg-red-500',
        orange: 'bg-orange-500',
        yellow: 'bg-yellow-500',
        lime: 'bg-lime-500',
        green: 'bg-green-500'
    };

    function calculateStrength(password, options) {
        if (!password) return { score: 0, label: '', color: 'gray' };

        let score = 0;
        const minLength = options.minLength || 8;

        // Length check
        if (password.length >= minLength) score += 1;
        if (password.length >= minLength + 4) score += 1;
        if (password.length >= minLength + 8) score += 1;

        // Character variety
        if (/[a-z]/.test(password)) score += 1;
        if (/[A-Z]/.test(password)) score += 1;
        if (/[0-9]/.test(password)) score += 1;
        if (/[^a-zA-Z0-9]/.test(password)) score += 1;

        // Normalize to 0-4 scale
        const normalizedScore = Math.min(4, Math.floor(score / 2));

        const levels = [
            { label: 'Very Weak', color: 'red' },
            { label: 'Weak', color: 'orange' },
            { label: 'Fair', color: 'yellow' },
            { label: 'Good', color: 'lime' },
            { label: 'Strong', color: 'green' }
        ];

        return {
            score: normalizedScore,
            ...levels[normalizedScore]
        };
    }

    window.LEDMatrixWidgets.register('password-input', {
        name: 'Password Input Widget',
        version: '1.0.0',

        render: function(container, config, value, options) {
            const fieldId = sanitizeId(options.fieldId || container.id || 'password_input');
            const xOptions = config['x-options'] || config['x_options'] || {};
            const placeholder = xOptions.placeholder || 'Enter password';
            const showToggle = xOptions.showToggle !== false;
            const showStrength = xOptions.showStrength === true;
            // Validate and sanitize minLength as a non-negative integer
            const rawMinLength = xOptions.minLength !== undefined ? parseInt(xOptions.minLength, 10) : 8;
            const sanitizedMinLength = (Number.isFinite(rawMinLength) && Number.isInteger(rawMinLength) && rawMinLength >= 0) ? rawMinLength : 8;
            const requireUppercase = xOptions.requireUppercase === true;
            const requireNumber = xOptions.requireNumber === true;
            const requireSpecial = xOptions.requireSpecial === true;
            const disabled = xOptions.disabled === true;
            const required = xOptions.required === true;

            const currentValue = value || '';

            let html = `<div id="${fieldId}_widget" class="password-input-widget" data-field-id="${fieldId}" data-min-length="${sanitizedMinLength}" data-require-uppercase="${requireUppercase}" data-require-number="${requireNumber}" data-require-special="${requireSpecial}">`;

            html += '<div class="relative">';

            html += `
                <input type="password"
                       id="${fieldId}_input"
                       name="${escapeHtml(options.name || fieldId)}"
                       value="${escapeHtml(currentValue)}"
                       placeholder="${escapeHtml(placeholder)}"
                       ${sanitizedMinLength > 0 ? `minlength="${sanitizedMinLength}"` : ''}
                       ${disabled ? 'disabled' : ''}
                       ${required ? 'required' : ''}
                       onchange="window.LEDMatrixWidgets.getHandlers('password-input').onChange('${fieldId}')"
                       oninput="window.LEDMatrixWidgets.getHandlers('password-input').onInput('${fieldId}')"
                       class="form-input w-full rounded-md border-gray-300 shadow-sm focus:border-blue-500 focus:ring-blue-500 pr-10 ${disabled ? 'bg-gray-100 cursor-not-allowed' : 'bg-white'} text-black placeholder:text-gray-400">
            `;

            if (showToggle && !disabled) {
                html += `
                    <button type="button"
                            id="${fieldId}_toggle"
                            onclick="window.LEDMatrixWidgets.getHandlers('password-input').onToggle('${fieldId}')"
                            class="absolute inset-y-0 right-0 flex items-center pr-3 text-gray-400 hover:text-gray-600"
                            title="Show/hide password">
                        <i id="${fieldId}_icon" class="fas fa-eye"></i>
                    </button>
                `;
            }

            html += '</div>';

            // Strength indicator
            if (showStrength) {
                const strength = calculateStrength(currentValue, xOptions);
                const colorClass = STRENGTH_COLORS[strength.color] || STRENGTH_COLORS.gray;
                html += `
                    <div id="${fieldId}_strength" class="mt-2 ${currentValue ? '' : 'hidden'}">
                        <div class="flex gap-1 mb-1">
                            <div class="h-1 flex-1 rounded bg-gray-200">
                                <div id="${fieldId}_bar0" class="h-full rounded ${strength.score >= 1 ? colorClass : ''}" style="width: ${strength.score >= 1 ? '100%' : '0'}"></div>
                            </div>
                            <div class="h-1 flex-1 rounded bg-gray-200">
                                <div id="${fieldId}_bar1" class="h-full rounded ${strength.score >= 2 ? colorClass : ''}" style="width: ${strength.score >= 2 ? '100%' : '0'}"></div>
                            </div>
                            <div class="h-1 flex-1 rounded bg-gray-200">
                                <div id="${fieldId}_bar2" class="h-full rounded ${strength.score >= 3 ? colorClass : ''}" style="width: ${strength.score >= 3 ? '100%' : '0'}"></div>
                            </div>
                            <div class="h-1 flex-1 rounded bg-gray-200">
                                <div id="${fieldId}_bar3" class="h-full rounded ${strength.score >= 4 ? colorClass : ''}" style="width: ${strength.score >= 4 ? '100%' : '0'}"></div>
                            </div>
                        </div>
                        <span id="${fieldId}_strength_label" class="text-xs text-gray-500">${strength.label}</span>
                    </div>
                `;
            }

            // Error message area
            html += `<div id="${fieldId}_error" class="text-sm text-red-600 mt-1 hidden"></div>`;

            html += '</div>';

            container.innerHTML = html;
        },

        getValue: function(fieldId) {
            const safeId = sanitizeId(fieldId);
            const input = document.getElementById(`${safeId}_input`);
            return input ? input.value : '';
        },

        setValue: function(fieldId, value) {
            const safeId = sanitizeId(fieldId);
            const input = document.getElementById(`${safeId}_input`);
            if (input) {
                input.value = value || '';
                this.handlers.onInput(fieldId);
            }
        },

        validate: function(fieldId) {
            const safeId = sanitizeId(fieldId);
            const input = document.getElementById(`${safeId}_input`);
            const errorEl = document.getElementById(`${safeId}_error`);
            const widget = document.getElementById(`${safeId}_widget`);

            if (!input) return { valid: true, errors: [] };

            const errors = [];
            let isValid = input.checkValidity();

            if (!isValid) {
                errors.push(input.validationMessage);
            } else if (input.value && widget) {
                // Check custom validation requirements
                const requireUppercase = widget.dataset.requireUppercase === 'true';
                const requireNumber = widget.dataset.requireNumber === 'true';
                const requireSpecial = widget.dataset.requireSpecial === 'true';

                if (requireUppercase && !/[A-Z]/.test(input.value)) {
                    isValid = false;
                    errors.push('Password must contain at least one uppercase letter');
                }
                if (requireNumber && !/[0-9]/.test(input.value)) {
                    isValid = false;
                    errors.push('Password must contain at least one number');
                }
                if (requireSpecial && !/[^a-zA-Z0-9]/.test(input.value)) {
                    isValid = false;
                    errors.push('Password must contain at least one special character');
                }
            }

            if (errorEl) {
                if (!isValid && errors.length > 0) {
                    errorEl.textContent = errors[0];
                    errorEl.classList.remove('hidden');
                    input.classList.add('border-red-500');
                } else {
                    errorEl.classList.add('hidden');
                    input.classList.remove('border-red-500');
                }
            }

            return { valid: isValid, errors };
        },

        handlers: {
            onChange: function(fieldId) {
                const widget = window.LEDMatrixWidgets.get('password-input');
                widget.validate(fieldId);
                triggerChange(fieldId, widget.getValue(fieldId));
            },

            onInput: function(fieldId) {
                const safeId = sanitizeId(fieldId);
                const input = document.getElementById(`${safeId}_input`);
                const strengthEl = document.getElementById(`${safeId}_strength`);
                const strengthLabel = document.getElementById(`${safeId}_strength_label`);
                const widget = document.getElementById(`${safeId}_widget`);

                if (strengthEl && input) {
                    const value = input.value;
                    const minLength = parseInt(widget?.dataset.minLength || '8', 10);

                    if (value) {
                        strengthEl.classList.remove('hidden');
                        const strength = calculateStrength(value, { minLength });

                        // Update bars using shared color mapping
                        const colorClass = STRENGTH_COLORS[strength.color] || STRENGTH_COLORS.gray;

                        for (let i = 0; i < 4; i++) {
                            const bar = document.getElementById(`${safeId}_bar${i}`);
                            if (bar) {
                                // Remove all color classes
                                bar.className = 'h-full rounded';
                                if (i < strength.score) {
                                    bar.classList.add(colorClass);
                                    bar.style.width = '100%';
                                } else {
                                    bar.style.width = '0';
                                }
                            }
                        }

                        if (strengthLabel) {
                            strengthLabel.textContent = strength.label;
                        }
                    } else {
                        strengthEl.classList.add('hidden');
                    }
                }
            },

            onToggle: function(fieldId) {
                const safeId = sanitizeId(fieldId);
                const input = document.getElementById(`${safeId}_input`);
                const icon = document.getElementById(`${safeId}_icon`);

                if (input && icon) {
                    if (input.type === 'password') {
                        input.type = 'text';
                        icon.classList.remove('fa-eye');
                        icon.classList.add('fa-eye-slash');
                    } else {
                        input.type = 'password';
                        icon.classList.remove('fa-eye-slash');
                        icon.classList.add('fa-eye');
                    }
                }
            }
        }
    });

    console.log('[PasswordInputWidget] Password input widget registered');
})();
