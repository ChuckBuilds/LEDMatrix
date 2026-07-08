/*
 * settings-search.js — global settings search + per-tab filter for the v3 UI.
 *
 * Two features share one lightweight index built from the same markup the
 * tooltip work standardizes (.form-group[id^="setting-"] + <label> +
 * .help-tip[data-tooltip]), so it can never drift from what is rendered:
 *
 *   1. Global search (header box): finds settings across ALL tabs, even ones
 *      not yet opened, by fetching each tab's partial once and scanning it.
 *      Clicking a result switches to the tab, waits for the field to load,
 *      then scrolls to and flashes it.
 *   2. Per-tab filter (the .settings-filter box under a partial title):
 *      hides non-matching fields on the current tab. Delegated, so it keeps
 *      working across HTMX swaps.
 *
 * No backend endpoint is required — plugins are enumerated from
 * window.installedPlugins and their config partials are fetched the same way.
 */
(function () {
    'use strict';

    if (window._settingsSearchInit) return;
    window._settingsSearchInit = true;

    var MAX_RESULTS = 25;

    function debounce(fn, ms) {
        var t;
        return function () {
            var args = arguments, ctx = this;
            clearTimeout(t);
            t = setTimeout(function () { fn.apply(ctx, args); }, ms);
        };
    }

    // True when every search term is present in the haystack.
    function termsMatch(hay, terms) {
        return terms.every(function (t) { return hay.indexOf(t) !== -1; });
    }

    function textOf(el) {
        return (el && el.textContent ? el.textContent : '').replace(/\s+/g, ' ').trim();
    }

    // --- Index building -------------------------------------------------------

    var buildPromise = null;

    // Fetch the prebuilt index from the server (one literal-URL JSON request)
    // and cache it for the session. Each entry gets a lowercased `hay` haystack
    // for matching. The server owns which tabs/plugins are included.
    function buildIndex(force) {
        if (window._settingsIndex && !force) return Promise.resolve(window._settingsIndex);
        if (buildPromise && !force) return buildPromise;

        buildPromise = fetch('/v3/settings/search-index', { headers: { 'X-Requested-With': 'settings-search' } })
            .then(function (r) { return r.ok ? r.json() : { fields: [] }; })
            .then(function (data) {
                var fields = (data && data.fields) || [];
                fields.forEach(function (f) {
                    f.hay = [f.label, f.help, f.key, f.tabLabel, f.section].join(' ').toLowerCase();
                });
                window._settingsIndex = fields;
                return fields;
            })
            .catch(function () {
                window._settingsIndex = [];
                return window._settingsIndex;
            });
        return buildPromise;
    }

    // --- Global search UI -----------------------------------------------------

    var input = null, resultsBox = null, activeIndex = -1, currentResults = [];

    function search(q) {
        q = q.trim().toLowerCase();
        if (!q) return [];
        var terms = q.split(/\s+/);
        var out = [];
        (window._settingsIndex || []).some(function (entry) {
            if (termsMatch(entry.hay, terms)) out.push(entry);
            return out.length >= MAX_RESULTS; // stop once we have enough
        });
        return out;
    }

    function span(cls, text) {
        var s = document.createElement('span');
        s.className = cls;
        s.textContent = text;
        return s;
    }

    // Build the dropdown with DOM nodes + textContent (never innerHTML) so
    // setting labels/help can never be interpreted as markup.
    function renderResults(results) {
        currentResults = results;
        activeIndex = -1;
        resultsBox.textContent = '';
        if (!results.length) {
            resultsBox.appendChild(span('ssr-empty', 'No settings found.'));
            openResults();
            return;
        }
        var lastTab = null;
        results.forEach(function (r, i) {
            if (r.tabLabel !== lastTab) {
                const group = document.createElement('div');
                group.className = 'ssr-group';
                group.textContent = r.tabLabel;
                resultsBox.appendChild(group);
                lastTab = r.tabLabel;
            }
            var sub = r.section ? (r.section + ' · ') : '';
            var snippet = r.help ? r.help.split('\n')[0] : '';
            var opt = document.createElement('button');
            opt.type = 'button';
            opt.className = 'ssr-option';
            opt.setAttribute('role', 'option');
            opt.id = 'ssr-' + i;
            opt.setAttribute('data-idx', String(i));
            opt.appendChild(span('ssr-label', r.label));
            var helpText = snippet ? (sub + snippet) : (sub ? r.section : '');
            if (helpText) opt.appendChild(span('ssr-help', helpText));
            resultsBox.appendChild(opt);
        });
        openResults();
    }

    function openResults() {
        resultsBox.classList.remove('hidden');
        if (input) input.setAttribute('aria-expanded', 'true');
    }
    function closeResults() {
        resultsBox.classList.add('hidden');
        activeIndex = -1;
        if (input) {
            input.setAttribute('aria-expanded', 'false');
            input.removeAttribute('aria-activedescendant');
        }
    }

    function highlight(idx) {
        var opts = resultsBox.querySelectorAll('.ssr-option');
        opts.forEach(function (o) { o.classList.remove('active'); });
        if (idx < 0 || idx >= opts.length) { activeIndex = -1; return; }
        activeIndex = idx;
        var el = opts.item(idx);
        el.classList.add('active');
        el.scrollIntoView({ block: 'nearest' });
        input.setAttribute('aria-activedescendant', el.id);
    }

    // --- Navigation to a setting ---------------------------------------------

    function getAppData() {
        var appEl = document.querySelector('[x-data="app()"]') || document.querySelector('[x-data]');
        if (!appEl) return null;
        if (appEl._x_dataStack && appEl._x_dataStack[0]) return appEl._x_dataStack[0];
        if (appEl.__x && appEl.__x.$data) return appEl.__x.$data;
        return null;
    }

    function setActiveTab(tab) {
        var data = getAppData();
        if (data) { data.activeTab = tab; return true; }
        return false;
    }

    function waitForElement(id, timeout) {
        return new Promise(function (resolve) {
            var existing = document.getElementById(id);
            if (existing) { resolve(existing); return; }
            var host = document.getElementById('tab-content') || document.body;
            var done = false;
            var obs = new MutationObserver(function () {
                var el = document.getElementById(id);
                if (el && !done) {
                    done = true;
                    obs.disconnect();
                    resolve(el);
                }
            });
            obs.observe(host, { childList: true, subtree: true });
            setTimeout(function () {
                if (!done) { done = true; obs.disconnect(); resolve(document.getElementById(id)); }
            }, timeout || 6000);
        });
    }

    function isNodeHidden(node) {
        return node.classList.contains('hidden') ||
            (node.style && node.style.display === 'none') ||
            window.getComputedStyle(node).display === 'none';
    }

    function revealNode(node) {
        // toggleSection handles the class, inline display, and chevron.
        if (node.id && typeof window.toggleSection === 'function') {
            window.toggleSection(node.id);
        } else {
            node.classList.remove('hidden');
            node.style.display = 'block';
        }
    }

    // Reveal any collapsed nested section (from render_nested_section) so the
    // target field is actually visible before we scroll to it.
    function revealAncestors(el) {
        var node = el.parentElement;
        while (node && node !== document.body) {
            if (node.classList && node.classList.contains('nested-content') && isNodeHidden(node)) {
                revealNode(node);
            }
            node = node.parentElement;
        }
    }

    function flash(el) {
        el.classList.remove('setting-flash');
        // force reflow so re-adding the class restarts the animation
        void el.offsetWidth;
        el.classList.add('setting-flash');
        var clear = function () { el.classList.remove('setting-flash'); el.removeEventListener('animationend', clear); };
        el.addEventListener('animationend', clear);
    }

    function navigateToSetting(entry) {
        closeResults();
        setActiveTab(entry.tab);
        waitForElement(entry.anchorId, 6000).then(function (el) {
            if (!el) return;
            revealAncestors(el);
            // Let the tab transition settle before scrolling.
            setTimeout(function () {
                el.scrollIntoView({ behavior: 'smooth', block: 'center' });
                flash(el);
            }, 60);
        });
    }

    // --- Wire up the header search box ----------------------------------------

    function initSearchBox() {
        input = document.getElementById('settings-search');
        resultsBox = document.getElementById('settings-search-results');
        if (!input || !resultsBox) return;

        // Warm the index in the background so the first search is instant.
        var warm = function () { buildIndex().catch(function () {}); };
        if ('requestIdleCallback' in window) {
            requestIdleCallback(warm, { timeout: 4000 });
        } else {
            setTimeout(warm, 3000);
        }

        input.addEventListener('focus', function () {
            buildIndex().then(function () {
                if (input.value.trim()) renderResults(search(input.value));
            });
        });

        input.addEventListener('input', debounce(function () {
            var q = input.value;
            if (!q.trim()) { closeResults(); return; }
            buildIndex().then(function () { renderResults(search(q)); });
        }, 200));

        input.addEventListener('keydown', function (e) {
            var opts = resultsBox.querySelectorAll('.ssr-option');
            if (e.key === 'ArrowDown') {
                e.preventDefault();
                if (resultsBox.classList.contains('hidden')) { renderResults(search(input.value)); return; }
                highlight(Math.min(activeIndex + 1, opts.length - 1));
            } else if (e.key === 'ArrowUp') {
                e.preventDefault();
                highlight(Math.max(activeIndex - 1, 0));
            } else if (e.key === 'Enter') {
                const chosen = currentResults.at(activeIndex >= 0 ? activeIndex : 0);
                if (chosen) {
                    e.preventDefault();
                    navigateToSetting(chosen);
                }
            } else if (e.key === 'Escape') {
                closeResults();
                input.blur();
            }
        });

        resultsBox.addEventListener('mousedown', function (e) {
            // mousedown (not click) so it fires before the input blur closes us
            var opt = e.target.closest('.ssr-option');
            if (!opt) return;
            e.preventDefault();
            const idx = parseInt(opt.getAttribute('data-idx'), 10);
            const chosen = currentResults.at(idx);
            if (chosen) navigateToSetting(chosen);
        });

        document.addEventListener('click', function (e) {
            if (!input) return;
            if (e.target === input || resultsBox.contains(e.target)) return;
            closeResults();
        });
    }

    // --- Per-tab filter (delegated) -------------------------------------------

    function filterScope(input) {
        return input.closest('.plugin-config-tab') ||
               input.closest('[id$="-content"]') ||
               input.closest('.bg-white') ||
               document;
    }

    function fieldHay(fg) {
        var label = textOf(fg.querySelector('label'));
        var tip = fg.querySelector('.help-tip');
        var help = tip ? (tip.getAttribute('data-tooltip') || '') : '';
        var key = fg.getAttribute('data-setting-key') || fg.id.replace(/^setting-/, '');
        return (label + ' ' + help + ' ' + key).toLowerCase();
    }

    function applyTabFilter(scope, q) {
        q = q.trim().toLowerCase();
        var terms = q ? q.split(/\s+/) : [];
        var fields = scope.querySelectorAll('.form-group[id^="setting-"]');
        var anyVisible = false;

        fields.forEach(function (fg) {
            var show = !terms.length || termsMatch(fieldHay(fg), terms);
            fg.style.display = show ? '' : 'none';
            if (show) anyVisible = true;
        });

        // Hide section headings whose settings all got filtered out.
        var nodes = scope.querySelectorAll('h3, h4, .form-group');
        var headings = [];
        var current = null;
        nodes.forEach(function (node) {
            if (node.tagName === 'H3' || node.tagName === 'H4') {
                current = { el: node, total: 0, visible: 0 };
                headings.push(current);
            } else if (current && node.matches('.form-group[id^="setting-"]')) {
                current.total++;
                if (node.style.display !== 'none') current.visible++;
            }
        });
        headings.forEach(function (h) {
            // Only auto-hide headings that exclusively group settings fields.
            h.el.style.display = (terms.length && h.total > 0 && h.visible === 0) ? 'none' : '';
        });

        // Toggle the "no matches" note if the filter box provides one.
        const wrap = scope.querySelector('.settings-filter-wrap');
        if (wrap) {
            const empty = wrap.querySelector('.settings-filter-empty');
            if (empty) empty.classList.toggle('hidden', !(terms.length && !anyVisible));
        }
    }

    document.addEventListener('input', function (e) {
        var box = e.target.closest ? e.target.closest('.settings-filter') : null;
        if (!box) return;
        applyTabFilter(filterScope(box), box.value);
    });

    // --- Boot -----------------------------------------------------------------

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initSearchBox);
    } else {
        initSearchBox();
    }

    // Expose for debugging / programmatic use.
    window.LEDMatrixSettingsSearch = {
        buildIndex: buildIndex,
        navigateToSetting: navigateToSetting
    };

    console.log('[SettingsSearch] registered');
})();
