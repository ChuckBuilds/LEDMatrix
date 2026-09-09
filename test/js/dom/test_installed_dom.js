// Integration test in a REAL DOM (jsdom):
//   - HTML comes from the running server's /partials/plugins (real template output)
//   - list_filter.js is loaded as a real script
//   - plugin data comes from the running server's real API
//   - interactions are real dispatched DOM events on the real pill/select nodes
// This exercises HTML parsing, attribute reflection, event bubbling and
// delegation for real — none of which the hand-rolled shim could vouch for.
const fs = require('fs');
const http = require('http');
const { JSDOM, VirtualConsole } = require('jsdom');

const path = require('path');
const V3 = path.resolve(__dirname, '../../../web_interface/static/v3');
const BASE = process.env.BASE || 'http://localhost:5000';

function get(path) {
  return new Promise((res, rej) => {
    http.get(BASE + path, r => { let d = ''; r.on('data', c => d += c); r.on('end', () => res(d)); })
        .on('error', rej);
  });
}

(async () => {
  const partial = await get('/partials/plugins');
  const installed = JSON.parse(await get('/api/v3/plugins/installed')).data.plugins;

  // Collected so an uncaught error inside a handler fails the run instead of
  // silently vanishing.
  const jsErrors = [];
  const vc = new VirtualConsole();
  vc.on('jsdomError', e => jsErrors.push(String(e.message || e)));
  vc.on('error', (...a) => jsErrors.push('console.error: ' + a.join(' ')));

  const dom = new JSDOM(
    `<!doctype html><html><body><div id="app">${partial}</div></body></html>`,
    { runScripts: 'dangerously', virtualConsole: vc, url: BASE + '/' });

  const { window } = dom;
  const { document } = window;

  // Minimal ambient globals the extracted block expects from plugins_manager.js.
  window.pluginLog = () => {};
  window.debugLog = () => {};
  window.PLUGIN_DEBUG = false;
  window.installedPlugins = installed;
  window.escapeHtml = function (text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  };

  // Load the helper as a real <script>.
  const s = document.createElement('script');
  s.textContent = fs.readFileSync(V3 + '/js/plugins/list_filter.js', 'utf8');
  document.body.appendChild(s);

  if (!window.ListFilter) { console.log('FAIL: list_filter.js did not expose window.ListFilter'); process.exit(1); }

  // Pull the installed-plugins wiring out of plugins_manager.js verbatim and run
  // it as a real script in this document.
  const src = fs.readFileSync(V3 + '/plugins_manager.js', 'utf8');
  const a = src.indexOf('function installedSortName(plugin)');
  const b = src.indexOf('// Set up event delegation for plugin action buttons');
  const c = src.indexOf('function handlePluginAction(event)');
  const d = src.indexOf('function findInstalledPlugin(pluginId)');
  if ([a, b, c, d].some(i => i < 0)) { console.log('FAIL: could not slice plugins_manager.js'); process.exit(1); }

  const s2 = document.createElement('script');
  s2.textContent = `
    var installedPlugins = window.installedPlugins;
    var escapeHtml = window.escapeHtml, pluginLog = window.pluginLog, debugLog = window.debugLog;
    var PLUGIN_DEBUG = false;
    ${src.slice(a, b)}
    ${src.slice(b, c)}
    ${src.slice(c, d)}
    window.__t = { getInstalledFilter, renderInstalledPlugins, setupInstalledFilterListeners,
                   applyInstalledFiltersAndRender };
  `;
  document.body.appendChild(s2);
  if (jsErrors.length) { console.log('FAIL: errors while loading:\n  ' + jsErrors.join('\n  ')); process.exit(1); }

  const T = window.__t;
  const $ = id => document.getElementById(id);
  const grid = () => $('installed-plugins-grid');
  const cards = () => grid().querySelectorAll('.plugin-card:not(.installed-skeleton)').length;
  const pill = v => document.querySelector(`#installed-filter-pills [data-installed-filter="${v}"]`);
  const litPills = () => [...document.querySelectorAll('#installed-filter-pills [data-installed-filter]')]
    .filter(b => b.getAttribute('data-active') === 'true')
    .map(b => b.getAttribute('data-installed-filter'));
  const countText = () => $('installed-count').textContent.trim();

  const click = el => el.dispatchEvent(new window.MouseEvent('click', { bubbles: true, cancelable: true }));
  const type = (el, v) => { el.value = v; el.dispatchEvent(new window.Event('input', { bubbles: true })); };
  const change = (el, v) => { el.value = v; el.dispatchEvent(new window.Event('change', { bubbles: true })); };
  const tick = ms => new Promise(r => setTimeout(r, ms));

  let pass = 0, fail = 0;
  const ok = (l, cond, extra) => cond ? (pass++, console.log('  ok   ' + l))
    : (fail++, console.log('  FAIL ' + l + (extra !== undefined ? '  → ' + JSON.stringify(extra) : '')));

  console.log('\n── real-DOM integration (jsdom) ──');
  console.log(`   server gave ${installed.length} installed plugins: ` +
              installed.map(p => `${p.id}(en=${p.enabled},upd=${!!p.update_available})`).join(' '));

  // ── the markup the server actually rendered ────────────────────────────
  ok('toolbar present in server HTML', !!$('installed-filter-bar'));
  ok('4 pills parsed from server HTML',
     document.querySelectorAll('#installed-filter-pills [data-installed-filter]').length === 4);
  ok('sort dropdown has the 5 agreed options',
     [...$('installed-sort').options].map(o => o.value).join(',') === 'a-z,z-a,status,recent,category',
     [...$('installed-sort').options].map(o => o.value));
  ok('skeletons present before first render', grid().querySelectorAll('.installed-skeleton').length === 3);

  // ── first render ───────────────────────────────────────────────────────
  T.setupInstalledFilterListeners();
  T.renderInstalledPlugins(installed);
  ok('skeletons removed after render', grid().querySelectorAll('.installed-skeleton').length === 0);
  ok('one card per plugin', cards() === installed.length, cards());
  ok('count text unfiltered', countText() === `${installed.length} installed`, countText());
  ok('only the All pill is lit', litPills().join(',') === 'all', litPills());
  ok('aria-pressed mirrors data-active', pill('all').getAttribute('aria-pressed') === 'true');

  const enabledCount = installed.filter(p => p.enabled).length;
  const disabledCount = installed.length - enabledCount;
  const updCount = installed.filter(p => p.update_available).length;

  // ── the CSS hook this feature depends on ───────────────────────────────
  // .filter-pill[data-active="true"] had never been used by any code before
  // this change, so confirm the attribute really flips on the real nodes.
  click(pill('enabled'));
  ok('Enabled pill lights up', litPills().join(',') === 'enabled', litPills());
  ok('Enabled filters the grid', cards() === enabledCount, { cards: cards(), expect: enabledCount });
  ok('count switches to "N of M shown"', countText() === `${enabledCount} of ${installed.length} shown`, countText());
  ok('Clear button revealed', !$('installed-clear-filters').classList.contains('hidden'));

  click(pill('disabled'));
  ok('Disabled pill lights, Enabled unlights', litPills().join(',') === 'disabled', litPills());
  ok('Disabled filters the grid', cards() === disabledCount, { cards: cards(), expect: disabledCount });

  click(pill('updates'));
  ok('Updates filters the grid', cards() === updCount, { cards: cards(), expect: updCount });
  ok('updates badge count matches', $('installed-updates-count').textContent === String(updCount),
     $('installed-updates-count').textContent);
  ok('badge visible when >0', updCount === 0 || !$('installed-updates-count').classList.contains('hidden'));

  click(pill('all'));
  ok('back to All shows everything', cards() === installed.length);
  ok('Clear button hidden again', $('installed-clear-filters').classList.contains('hidden'));

  // ── debounced search, through the real event path ──────────────────────
  const first = installed[0];
  type($('installed-search'), first.name.slice(0, 4));
  await tick(350);
  ok('search narrows to a match', cards() >= 1 && cards() <= installed.length, cards());
  ok('search clear ✕ revealed', !$('installed-search-clear').classList.contains('hidden'));

  type($('installed-search'), 'zzz-no-such-plugin');
  await tick(350);
  ok('no matches → zero cards', cards() === 0);
  ok('shows the filter empty state', /No plugins match your filters/.test(grid().textContent));
  ok('does NOT say "No plugins installed"', !/No plugins installed/.test(grid().textContent));

  // Clear button inside the empty state goes through the delegated handler.
  const emptyClear = grid().querySelector('[data-action="clear-installed-filters"]');
  ok('empty state offers a Clear button', !!emptyClear);
  if (emptyClear) {
    click(emptyClear);
    ok('empty-state Clear restores the grid', cards() === installed.length, cards());
    ok('empty-state Clear empties the search box', $('installed-search').value === '');
  }

  // ── regression: typing a space must survive the debounce ───────────────
  // setSearch() trims for matching; if the trimmed value were written back into
  // the input, pausing mid-phrase would delete the space you just typed and
  // multi-word terms would be untypable.
  const si = $('installed-search');
  type(si, 'web ');
  await tick(350);
  ok('trailing space survives the debounce', si.value === 'web ', JSON.stringify(si.value));
  type(si, 'web u');
  await tick(350);
  ok('can keep typing past the space', si.value === 'web u', JSON.stringify(si.value));
  type(si, '   ');
  await tick(350);
  ok('whitespace-only is not treated as an active filter', cards() === installed.length, cards());
  ok('whitespace-only text is still left in the box', si.value === '   ', JSON.stringify(si.value));
  type(si, '');
  await tick(350);

  // ── sorts, via the real select ─────────────────────────────────────────
  const names = () => [...grid().querySelectorAll('.plugin-card h4')].map(h => h.textContent.trim());
  change($('installed-sort'), 'a-z');
  const az = names();
  change($('installed-sort'), 'z-a');
  ok('z-a is the reverse of a-z', names().join('|') === [...az].reverse().join('|'),
     { az, za: names() });
  for (const s of ['status', 'recent', 'category']) {
    change($('installed-sort'), s);
    ok(`sort "${s}" keeps every card`, cards() === installed.length, cards());
  }

  // ── Clear Filters resets all three axes at once ────────────────────────
  type($('installed-search'), first.name.slice(0, 3));
  await tick(350);
  click(pill('enabled'));
  change($('installed-sort'), 'z-a');
  click($('installed-clear-filters'));
  ok('Clear resets search box', $('installed-search').value === '');
  ok('Clear resets sort select', $('installed-sort').value === 'a-z');
  ok('Clear relights All', litPills().join(',') === 'all', litPills());
  ok('Clear restores all cards', cards() === installed.length, cards());
  ok('Clear hides itself', $('installed-clear-filters').classList.contains('hidden'));

  // ── the state-leak guard, on a real DOM ────────────────────────────────
  click(pill('updates'));
  ok('grid is filtered', cards() === updCount);
  ok('window.installedPlugins still full length', window.installedPlugins.length === installed.length,
     window.installedPlugins.length);
  click(pill('all'));

  // ── HTMX partial re-swap: the path no unit test could reach ────────────
  // Replace the section's markup wholesale (what hx-swap does when you leave
  // and re-enter the tab), then re-run init exactly as initPluginsPage would.
  click(pill('enabled'));
  type($('installed-search'), first.name.slice(0, 3));
  await tick(350);
  type($('installed-search'), $('installed-search').value + ' ');   // trailing space, on purpose
  await tick(350);
  const beforeSwap = { search: $('installed-search').value, pills: litPills().join(','), cards: cards() };
  ok('pre-swap value retains its trailing space', /\s$/.test(beforeSwap.search), JSON.stringify(beforeSwap.search));

  document.getElementById('app').innerHTML = partial;   // fresh toolbar at defaults
  ok('post-swap DOM starts at defaults',
     $('installed-search').value === '' && litPills().join(',') === 'all');

  T.setupInstalledFilterListeners();        // what initPluginsPage() calls
  T.renderInstalledPlugins(window.installedPlugins);
  ok('post-swap: search text restored', $('installed-search').value === beforeSwap.search,
     { got: $('installed-search').value, want: beforeSwap.search });
  ok('post-swap: pill state restored', litPills().join(',') === beforeSwap.pills, litPills());
  // Search AND pill are both still active, so the restored view must match the
  // pre-swap view exactly — comparing to it avoids restating the filter logic.
  ok('post-swap: same view restored', cards() === beforeSwap.cards,
     { got: cards(), want: beforeSwap.cards });

  // and the freshly swapped controls must still be live
  type($('installed-search'), '');
  await tick(350);
  ok('post-swap: clearing search re-widens to the pill alone', cards() === enabledCount, cards());
  click(pill('all'));
  ok('post-swap: pills still clickable', cards() === installed.length, cards());
  click(pill('disabled'));
  ok('post-swap: pill filters again', cards() === disabledCount, cards());
  click(pill('all'));
  type($('installed-search'), 'zzz-none');
  await tick(350);
  ok('post-swap: search still live', cards() === 0);
  click(grid().querySelector('[data-action="clear-installed-filters"]'));
  ok('post-swap: delegated Clear still wired', cards() === installed.length);

  // ── does the stylesheet actually hook the markup? ──────────────────────
  const css = fs.readFileSync(V3 + '/app.css', 'utf8');
  const st = document.createElement('style');
  st.textContent = css;
  document.head.appendChild(st);
  click(pill('enabled'));
  const activeWeight = window.getComputedStyle(pill('enabled')).fontWeight;
  const idleWeight = window.getComputedStyle(pill('disabled')).fontWeight;
  ok('.filter-pill[data-active="true"] matches our markup (font-weight 600)',
     activeWeight === '600', { active: activeWeight, idle: idleWeight });
  ok('inactive pill does not pick up the active rule', idleWeight !== '600', idleWeight);
  click(pill('all'));

  ok('no uncaught JS errors anywhere', jsErrors.length === 0, jsErrors.slice(0, 4));

  console.log(`\n${pass} passed, ${fail} failed\n`);
  process.exit(fail ? 1 : 0);
})().catch(e => { console.log('HARNESS ERROR: ' + e.stack); process.exit(1); });
