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
                            const isHtmxError = errorStr.includes('htmx') ||
                                               errorStack.includes('htmx') ||
                                               args.some(arg => {
                                                   if (typeof arg === 'string') {
                                                       return arg.includes('htmx');
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
                        
                        // For form submissions, log field names only — values
                        // may contain API keys, passwords, or other secrets
                        // that must never reach the console.
                        if (target && target.tagName === 'FORM') {
                            const formData = new FormData(target);
                            const fieldNames = [];
                            for (const [key] of formData.entries()) {
                                fieldNames.push(key);
                            }
                            console.error('Form fields (values redacted):', fieldNames);
                            
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
                                } catch {
                                    console.error('Error response (non-JSON):', xhr.responseText.substring(0, 500));
                                }
                            }
                        }
                    });
                    
                    document.body.addEventListener('htmx:swapError', function(event) {
                        // Log but don't break the app
                        console.warn('HTMX swap error:', event.detail);
                    });

                    // Execute <script> tags in swapped content ourselves, on
                    // htmx:afterSwap (synchronous, right after the swap) rather
                    // than relying on htmx's own script handling, which runs
                    // during its later "settle" phase (~20ms after swap, per
                    // htmx's defaultSettleDelay). Alpine's MutationObserver
                    // processes newly-inserted x-data elements synchronously
                    // as soon as the swap lands, which is BEFORE htmx's settle
                    // phase - so any partial whose x-data component function
                    // (e.g. wifiSetup()) is defined by an inline <script> in
                    // that same partial would have that script still un-run
                    // when Alpine evaluates x-data, permanently failing with
                    // "wifiSetup is not defined" (Alpine does not retry).
                    // Disable htmx's own native script re-execution so the
                    // same script doesn't also run a second time via settle.
                    if (typeof htmx !== 'undefined' && htmx.config) {
                        htmx.config.allowScriptTags = false;
                    }
                    document.body.addEventListener('htmx:afterSwap', function(event) {
                        const target = event.detail && event.detail.target;
                        if (!target || !(target instanceof Element)) return;
                        target.querySelectorAll('script').forEach(function(oldScript) {
                            const newScript = document.createElement('script');
                            for (const attr of oldScript.attributes) {
                                newScript.setAttribute(attr.name, attr.value);
                            }
                            newScript.textContent = oldScript.textContent;
                            oldScript.replaceWith(newScript);
                        });
                    });

                    // Mark tab containers as loaded once their content settles, so switching
                    // away and back doesn't re-fetch. Scoped to the "loadtab" trigger (tab
                    // containers only) so modals and plugin config panels can still reload.
                    document.body.addEventListener('htmx:afterSettle', function(event) {
                        if (event.detail && event.detail.target) {
                            const target = event.detail.target;
                            const trigger = target.getAttribute('hx-trigger') || '';
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

                // Keep assistive tech in sync: any toggle button that declares
                // aria-controls for this section mirrors the expanded state.
                const controlBtn = document.querySelector(`[aria-controls="${sectionId}"]`);
                if (controlBtn) {
                    controlBtn.setAttribute('aria-expanded', String(isHidden));
                }
            };
        })();
