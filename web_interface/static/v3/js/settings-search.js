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

    // Core settings tabs to index. Only tabs that contain real settings fields
    // (a .form-group[id^="setting-"]) are listed. Operational/action tabs
    // (overview, logs, history, cache, tools, fonts, backup/restore, raw config
    // editor, plugin store) have nothing to navigate to and are excluded.
    var CORE_TABS = [
        { tab: 'general', label: 'General', url: '/v3/partials/general' },
        { tab: 'display', label: 'Display', url: '/v3/partials/display' },
        { tab: 'schedule', label: 'Schedule', url: '/v3/partials/schedule' },
        { tab: 'wifi', label: 'WiFi', url: '/v3/partials/wifi' }
    ];

    var MAX_RESULTS = 25;

    // Plugin ids are constrained to this allowlist (mirrors the server's
    // _SAFE_PLUGIN_ID_RE in pages_v3.py) before they can appear in a request
    // path, so a fetched URL can never be influenced by untrusted input.
    var PLUGIN_ID_RE = /^[a-zA-Z0-9_-]{1,64}$/;

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

    // Extract one index entry per settings field from a parsed document.
    function scanDoc(doc, tab, tabLabel) {
        var entries = [];
        var nodes = doc.querySelectorAll('h2, h3, h4, .form-group[id^="setting-"]');
        var section = '';
        nodes.forEach(function (node) {
            var tag = node.tagName.toLowerCase();
            if (tag === 'h3' || tag === 'h4') {
                section = textOf(node);
                return;
            }
            if (tag === 'h2') { section = ''; return; }
            // A settings .form-group
            var labelEl = node.querySelector('label');
            var label = textOf(labelEl) || node.id.replace(/^setting-/, '');
            var tipEl = node.querySelector('.help-tip');
            var help = tipEl ? (tipEl.getAttribute('data-tooltip') || '') : '';
            var key = node.getAttribute('data-setting-key') || node.id.replace(/^setting-/, '');
            entries.push({
                tab: tab,
                tabLabel: tabLabel,
                section: section,
                key: key,
                label: label,
                help: help,
                anchorId: node.id,
                hay: [label, help, key, tabLabel, section].join(' ').toLowerCase()
            });
        });
        return entries;
    }

    function fetchAndScan(url, tab, tabLabel) {
        // `url` is always one of our own same-origin settings partial paths
        // (CORE_TABS literals or a plugin path built from an allowlisted id).
        return fetch(url, { headers: { 'X-Requested-With': 'settings-search' } })
            .then(function (r) { return r.ok ? r.text() : ''; })
            .then(function (html) {
                if (!html) return [];
                // Parsed into an inert document: scripts do not run and it is
                // never inserted into the live DOM — we only read labels and
                // tooltip text from it to build the search index.
                var doc = new DOMParser().parseFromString(html, 'text/html');
                return scanDoc(doc, tab, tabLabel);
            })
            .catch(function () { return []; });
    }

    var buildPromise = null;
    function buildIndex(force) {
        if (window._settingsIndex && !force) return Promise.resolve(window._settingsIndex);
        if (buildPromise && !force) return buildPromise;

        var jobs = CORE_TABS.map(function (t) { return fetchAndScan(t.url, t.tab, t.label); });

        var plugins = (window.installedPlugins || []);
        plugins.forEach(function (p) {
            // Skip ids that don't match the strict allowlist so the request
            // path is always built from safe, validated components.
            if (!p || !p.id || !PLUGIN_ID_RE.test(p.id)) return;
            jobs.push(fetchAndScan('/v3/partials/plugin-config/' + p.id, p.id, p.name || p.id));
        });

        buildPromise = Promise.all(jobs).then(function (lists) {
            var index = [];
            lists.forEach(function (l) { index = index.concat(l); });
            window._settingsIndex = index;
            return index;
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
