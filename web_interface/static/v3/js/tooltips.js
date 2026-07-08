/*
 * tooltips.js — accessible, delegated tooltip controller for the v3 web UI.
 *
 * A single controller handles every `.help-tip` trigger on the page, including
 * ones inside partials that HTMX swaps in later, with zero per-field wiring.
 * Triggers are emitted by the `help_tip` Jinja macro (partials/_macros.html) as
 * <button class="help-tip" data-tooltip="..."><i class="fas fa-circle-info">.
 *
 * Behaviour:
 *   - hover (mouse)            -> show / hide
 *   - keyboard focus           -> show / hide (only for :focus-visible)
 *   - click / tap              -> toggle (the touch path)
 *   - Escape / outside click   -> hide
 * The tooltip text is set via textContent (XSS-safe) and supports "\n" line
 * breaks via CSS `white-space: pre-line`. Styling lives in app.css and uses the
 * --color-* theme vars, so light/dark mode work automatically.
 */
(function () {
    'use strict';

    if (window._tooltipsInit) return;
    window._tooltipsInit = true;

    var panel = null;
    var currentTrigger = null;

    function getPanel() {
        if (panel) return panel;
        panel = document.createElement('div');
        panel.id = 'ledm-tooltip';
        panel.setAttribute('role', 'tooltip');
        panel.hidden = true;
        document.body.appendChild(panel);
        return panel;
    }

    function positionPanel(trigger) {
        var p = getPanel();
        var margin = 8;
        var rect = trigger.getBoundingClientRect();
        var pw = p.offsetWidth;
        var ph = p.offsetHeight;
        var vw = document.documentElement.clientWidth;
        var vh = document.documentElement.clientHeight;

        // Prefer above the trigger; flip below if it would clip the top.
        var top = rect.top - ph - margin;
        var placedBelow = false;
        if (top < margin) {
            top = rect.bottom + margin;
            placedBelow = true;
        }
        // Keep it on screen vertically as a last resort.
        if (top + ph > vh - margin) top = Math.max(margin, vh - ph - margin);

        // Center horizontally on the trigger, clamped to the viewport.
        var left = rect.left + rect.width / 2 - pw / 2;
        if (left < margin) left = margin;
        if (left + pw > vw - margin) left = Math.max(margin, vw - pw - margin);

        p.style.top = Math.round(top) + 'px';
        p.style.left = Math.round(left) + 'px';
        p.setAttribute('data-placement', placedBelow ? 'below' : 'above');
    }

    function show(trigger) {
        var text = trigger.getAttribute('data-tooltip');
        if (!text) return;
        var p = getPanel();
        p.textContent = text;
        p.hidden = false;
        // Measure after it is displayed, then position.
        positionPanel(trigger);
        trigger.setAttribute('aria-describedby', 'ledm-tooltip');
        currentTrigger = trigger;
    }

    function hide() {
        if (!panel) return;
        panel.hidden = true;
        if (currentTrigger) {
            currentTrigger.removeAttribute('aria-describedby');
            currentTrigger = null;
        }
    }

    function triggerFrom(target) {
        return target && target.closest ? target.closest('.help-tip') : null;
    }

    // --- Delegated listeners on document (survive HTMX swaps) ---

    document.addEventListener('mouseover', function (e) {
        var t = triggerFrom(e.target);
        if (t) show(t);
    });

    document.addEventListener('mouseout', function (e) {
        var t = triggerFrom(e.target);
        if (!t) return;
        // Ignore moves that stay within the same trigger.
        var to = e.relatedTarget;
        if (to && t.contains(to)) return;
        if (currentTrigger === t) hide();
    });

    document.addEventListener('focusin', function (e) {
        var t = triggerFrom(e.target);
        if (!t) return;
        // Only auto-show on keyboard focus, so a mouse/touch focus does not
        // fight the click handler below.
        var focusVisible;
        try {
            focusVisible = t.matches(':focus-visible');
        } catch { // older browsers without :focus-visible
            focusVisible = true;
        }
        if (focusVisible) show(t);
    });

    document.addEventListener('focusout', function (e) {
        var t = triggerFrom(e.target);
        if (t && currentTrigger === t) hide();
    });

    document.addEventListener('click', function (e) {
        var t = triggerFrom(e.target);
        if (t) {
            // Prevent an enclosing <label> from toggling its control, and
            // prevent form submission.
            e.preventDefault();
            e.stopPropagation();
            if (currentTrigger === t && !getPanel().hidden) {
                hide();
            } else {
                show(t);
            }
            return;
        }
        // Click anywhere else closes an open tooltip.
        if (panel && !panel.hidden && !panel.contains(e.target)) hide();
    });

    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape' && panel && !panel.hidden) hide();
    });

    // Reposition while visible; close when content is swapped out.
    window.addEventListener('scroll', function () {
        if (currentTrigger && panel && !panel.hidden) positionPanel(currentTrigger);
    }, true);
    window.addEventListener('resize', function () {
        if (currentTrigger && panel && !panel.hidden) positionPanel(currentTrigger);
    });
    document.body.addEventListener('htmx:afterSwap', function () {
        // The current trigger may have been removed by the swap.
        if (currentTrigger && !document.body.contains(currentTrigger)) hide();
    });

    console.log('[Tooltips] controller registered');
})();
