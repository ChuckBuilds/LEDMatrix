/* global debugLog */
// HTMX swap/script-execution configuration and section toggle helpers
// Extracted from templates/v3/base.html so browsers cache it as a static asset.
        // Configure HTMX to evaluate scripts in swapped content and fix insertBefore errors
        (function() {
            function setupScriptExecution() {
                if (document.body) {
                    // Fix HTMX insertBefore errors by validating targets before swap
                    document.body.addEventListener('htmx:beforeSwap', function(event) {
                        try {
                            const target = event.detail.target;
                            if (!target) {
                                console.warn('[HTMX] Target is null, skipping swap');
                                event.detail.shouldSwap = false;
                                return false;
                            }
                            
                            // Check if target is a valid DOM element
                            if (!(target instanceof Element)) {
                                console.warn('[HTMX] Target is not a valid Element, skipping swap');
                                event.detail.shouldSwap = false;
                                return false;
                            }
                            
                            // Check if target has a parent node (required for insertBefore)
                            if (!target.parentNode) {
                                console.warn('[HTMX] Target has no parent node, skipping swap');
                                event.detail.shouldSwap = false;
                                return false;
                            }
                            
                            // Ensure target is in the DOM
                            if (!document.body.contains(target) && !document.head.contains(target)) {
                                console.warn('[HTMX] Target is not in DOM, skipping swap');
                                event.detail.shouldSwap = false;
                                return false;
                            }
                            
                            // Additional check: ensure parent is also in DOM
                            if (target.parentNode && !document.body.contains(target.parentNode) && !document.head.contains(target.parentNode)) {
                                console.warn('[HTMX] Target parent is not in DOM, skipping swap');
                                event.detail.shouldSwap = false;
                                return false;
                            }
                            
                            // All checks passed, allow swap
                            return true;
                        } catch (e) {
                            // If validation fails, cancel swap
                            console.warn('[HTMX] Error validating target:', e);
                            event.detail.shouldSwap = false;
                            return false;
                        }
                    });
                    
                    // Suppress HTMX insertBefore errors and other noisy errors - they're harmless but noisy
                    const originalError = console.error;
                    const originalWarn = console.warn;
                    
                    console.error = function(...args) {
                        const errorStr = args.join(' ');
                        const errorStack = args.find(arg => arg && typeof arg === 'string' && arg.includes('htmx')) || '';
                        
                        // Suppress HTMX insertBefore errors (comprehensive check)
                        // These occur when HTMX tries to swap content but the target element is null
                        // Usually happens due to timing/race conditions and is harmless
                        if (errorStr.includes("insertBefore") || 
                            errorStr.includes("Cannot read properties of null") ||
                            errorStr.includes("reading 'insertBefore'")) {
                            // Check if it's from HTMX by looking at stack trace or error string
                            // Also check the call stack if available
                            const isHtmxError = errorStr.includes('htmx.org') || 
                                               errorStr.includes('htmx') || 
                                               errorStack.includes('htmx') ||
                                               args.some(arg => {
                                                   if (typeof arg === 'string') {
                                                       return arg.includes('htmx.org') || arg.includes('htmx');
                                                   }
                                                   // Check error objects for stack traces
                                                   if (arg && typeof arg === 'object' && arg.stack) {
                                                       return arg.stack.includes('htmx');
                                                   }
                                                   return false;
                                               });
                            
                            if (isHtmxError) {
                                return; // Suppress - this is a harmless HTMX timing/race condition issue
                            }
                        }
                        
                        // Suppress script execution errors from malformed HTML
                        if (errorStr.includes("Failed to execute 'appendChild' on 'Node'") ||
                            errorStr.includes("Failed to execute 'insertBefore' on 'Node'")) {
                            if (errorStr.includes('Unexpected token')) {
                                return; // Suppress malformed HTML errors
                            }
                        }
                        originalError.apply(console, args);
                    };
                    
                    console.warn = function(...args) {
                        const warnStr = args.join(' ');
                        // Suppress Permissions-Policy warnings (harmless browser warnings)
                        if (warnStr.includes('Permissions-Policy header') ||
                            warnStr.includes('Unrecognized feature') ||
                            warnStr.includes('Origin trial controlled feature') ||
                            warnStr.includes('browsing-topics') ||
                            warnStr.includes('run-ad-auction') ||
                            warnStr.includes('join-ad-interest-group') ||
                            warnStr.includes('private-state-token') ||
                            warnStr.includes('private-aggregation') ||
                            warnStr.includes('attribution-reporting')) {
                            return; // Suppress - these are harmless browser feature warnings
                        }
                        originalWarn.apply(console, args);
                    };
                    
                    // Handle HTMX errors gracefully with detailed logging
                    document.body.addEventListener('htmx:responseError', function(event) {
                        const detail = event.detail;
                        const xhr = detail.xhr;
                        const target = detail.target;
                        
                        // Enhanced error logging
                        console.error('HTMX response error:', {
                            status: xhr?.status,
                            statusText: xhr?.statusText,
                            url: xhr?.responseURL,
                            target: target?.id || target?.tagName,
                            responseText: xhr?.responseText
                        });
                        
                        // For form submissions, log the form data
                        if (target && target.tagName === 'FORM') {
                            const formData = new FormData(target);
                            const formPayload = {};
                            for (const [key, value] of formData.entries()) {
                                formPayload[key] = value;
                            }
                            console.error('Form payload:', formPayload);
                            
                            // Try to parse error response for validation details
                            if (xhr?.responseText) {
                                try {
                                    const errorData = JSON.parse(xhr.responseText);
                                    console.error('Error details:', {
                                        message: errorData.message,
                                        details: errorData.details,
                                        validation_errors: errorData.validation_errors,
                                        context: errorData.context
                                    });
                                } catch (e) {
                                    console.error('Error response (non-JSON):', xhr.responseText.substring(0, 500));
                                }
                            }
                        }
                    });
                    
                    document.body.addEventListener('htmx:swapError', function(event) {
                        // Log but don't break the app
                        console.warn('HTMX swap error:', event.detail);
                    });
                    
                    document.body.addEventListener('htmx:afterSwap', function(event) {
                        if (event.detail && event.detail.target) {
                            try {
                                const scripts = event.detail.target.querySelectorAll('script');
                                scripts.forEach(function(oldScript) {
                                    try {
                                        if (oldScript.innerHTML.trim() || oldScript.src) {
                                            const newScript = document.createElement('script');
                                            if (oldScript.src) newScript.src = oldScript.src;
                                            if (oldScript.type) newScript.type = oldScript.type;
                                            if (oldScript.innerHTML) newScript.textContent = oldScript.innerHTML;
                                            if (oldScript.parentNode) {
                                                oldScript.parentNode.insertBefore(newScript, oldScript);
                                                oldScript.parentNode.removeChild(oldScript);
                                            } else {
                                                // If no parent, append to head or body
                                                (document.head || document.body).appendChild(newScript);
                                            }
                                        }
                                    } catch (e) {
                                        // Silently ignore script execution errors
                                    }
                                });
                            } catch (e) {
                                // Silently ignore errors in script processing
                            }
                        }
                    });

                    // Mark tab containers as loaded once their content settles, so switching
                    // away and back doesn't re-fetch. Scoped to the "loadtab" trigger (tab
                    // containers only) so modals and plugin config panels can still reload.
                    document.body.addEventListener('htmx:afterSettle', function(event) {
                        if (event.detail && event.detail.target) {
                            var target = event.detail.target;
                            var trigger = target.getAttribute('hx-trigger') || '';
                            if (trigger.includes('loadtab')) {
                                target.setAttribute('data-loaded', 'true');
                            }
                        }
                    });
                } else {
                    if (document.readyState === 'loading') {
                        document.addEventListener('DOMContentLoaded', setupScriptExecution);
                    } else {
                        setTimeout(setupScriptExecution, 100);
                    }
                }
            }
            setupScriptExecution();
            
            // Section toggle function - define early so it's available for HTMX-loaded content
            window.toggleSection = function(sectionId) {
                const section = document.getElementById(sectionId);
                const icon = document.getElementById(sectionId + '-icon');
                if (!section) {
                    console.warn('toggleSection: Could not find section for', sectionId);
                    return;
                }
                if (!icon) {
                    console.warn('toggleSection: Could not find icon for', sectionId);
                    return;
                }
                
                // Check if currently hidden by checking both class and computed display
                const hasHiddenClass = section.classList.contains('hidden');
                const computedDisplay = window.getComputedStyle(section).display;
                const isHidden = hasHiddenClass || computedDisplay === 'none';
                
                if (isHidden) {
                    // Show the section - remove hidden class and explicitly set display to block
                    section.classList.remove('hidden');
                    section.style.display = 'block';
                    icon.classList.remove('fa-chevron-right');
                    icon.classList.add('fa-chevron-down');
                } else {
                    // Hide the section - add hidden class and set display to none
                    section.classList.add('hidden');
                    section.style.display = 'none';
                    icon.classList.remove('fa-chevron-down');
                    icon.classList.add('fa-chevron-right');
                }
            };
        })();
