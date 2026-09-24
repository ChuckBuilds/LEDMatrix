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
                    // htmx's own script handling is switched off here, in the
                    // handler, rather than once at setup: base.html injects
                    // htmx with a dynamic <script>, so at setup htmx is usually
                    // not defined yet and the switch never took effect. htmx
                    // then tried to run each script again in its settle phase,
                    // found it already replaced (no parent) and threw, which
                    // also skipped the rest of that swap's settle tasks.
                    document.body.addEventListener('htmx:afterSwap', function(event) {
                        htmx.config.allowScriptTags = false;
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
