// Real-DOM (jsdom) test of the migrated Plugin Store toolbar, against the
// server's actual 48-plugin registry — enough data for 4 pages, so pagination,
// the ellipsis strip and per-page changes are exercised for real.
// The card renderer itself is stubbed: Step 2 did not touch it, and stubbing
// keeps the assertions on the parts that did change.
const fs = require('fs');
const http = require('http');
const { JSDOM, VirtualConsole } = require('jsdom');

const path = require('path');
const V3 = path.resolve(__dirname, '../../../web_interface/static/v3');
const BASE = process.env.BASE || 'http://localhost:5000';
const get = path => new Promise((res, rej) =>
  http.get(BASE + path, r => { let d = ''; r.on('data', c => d += c); r.on('end', () => res(d)); }).on('error', rej));

function slice(src, a, b) {
  const i = src.indexOf(a), j = src.indexOf(b, i + 1);
  if (i < 0 || j < 0) throw new Error('cannot slice: ' + a);
  return src.slice(i, j);
}

(async () => {
  const partial = await get('/partials/plugins');
  const storeJson = JSON.parse(await get('/api/v3/plugins/store/list'));
  const storePlugins = (storeJson.data && storeJson.data.plugins) || storeJson.plugins || [];
  const installed = JSON.parse(await get('/api/v3/plugins/installed')).data.plugins;

  const jsErrors = [];
  const vc = new VirtualConsole();
  vc.on('jsdomError', e => jsErrors.push(String(e.message || e)));

  const dom = new JSDOM(`<!doctype html><html><body><div id="app">${partial}</div></body></html>`,
    { runScripts: 'dangerously', virtualConsole: vc, url: BASE + '/' });
  const { window } = dom;
  const { document } = window;

  const src = fs.readFileSync(V3 + '/plugins_manager.js', 'utf8');

  function boot() {
    const s = document.createElement('script');
    // IIFE-wrapped so a second boot gets a fresh closure instead of clashing on
    // the top-level `const ListFilter`; it still publishes via window.ListFilter.
    s.textContent = '(function(){\n' + fs.readFileSync(V3 + '/js/plugins/list_filter.js', 'utf8') + '\n})();';
    document.body.appendChild(s);

    const s2 = document.createElement('script');
    s2.textContent = `(function(){
      ${slice(src, 'const safeLocalStorage = {', '\n// ')}
      var installedPlugins = ${JSON.stringify(installed)};
      window.installedPlugins = installedPlugins;
      var pluginStoreCache = ${JSON.stringify(storePlugins)};
      window.__renderCalls = 0;
      // Stand-in for renderPluginStore: real DOM nodes, minimal markup.
      function renderPluginStore(plugins) {
        window.__renderCalls++;
        const grid = document.getElementById('plugin-store-grid');
        grid.innerHTML = (plugins || []).map(p =>
          '<div class="plugin-card" data-id="' + p.id + '"></div>').join('');
      }
      ${slice(src, 'function isStorePluginInstalled(', '// Expose searchPluginStore')}
      window.__t = { applyStoreFiltersAndSort, setupStoreFilterListeners, getStoreFilter };
    })();`;
    document.body.appendChild(s2);
  }
  boot();
  if (jsErrors.length) { console.log('FAIL loading:\n  ' + jsErrors.join('\n  ')); process.exit(1); }

  const T = window.__t;
  const $ = id => document.getElementById(id);
  const cards = () => $('plugin-store-grid').querySelectorAll('.plugin-card').length;
  const ids = () => [...$('plugin-store-grid').querySelectorAll('.plugin-card')].map(c => c.dataset.id);
  const info = () => $('store-results-info').textContent.trim();
  const infoBot = () => $('store-results-info-bottom') ? $('store-results-info-bottom').textContent.trim() : null;
  const pagTop = () => $('store-pagination-top').innerHTML;
  const pageBtns = () => [...$('store-pagination-top').querySelectorAll('[data-list-page]')];
  const clickPage = label => {
    const b = pageBtns().find(x => x.textContent.trim() === String(label));
    if (!b) throw new Error('no page button labelled ' + label);
    b.dispatchEvent(new window.MouseEvent('click', { bubbles: true, cancelable: true }));
  };
  const click = el => el.dispatchEvent(new window.MouseEvent('click', { bubbles: true, cancelable: true }));
  const change = (el, v) => { el.value = v; el.dispatchEvent(new window.Event('change', { bubbles: true })); };
  const type = (el, v) => { el.value = v; el.dispatchEvent(new window.Event('input', { bubbles: true })); };
  const tick = ms => new Promise(r => setTimeout(r, ms));

  let pass = 0, fail = 0;
  const ok = (l, c, x) => c ? (pass++, console.log('  ok   ' + l))
    : (fail++, console.log('  FAIL ' + l + (x !== undefined ? '  → ' + JSON.stringify(x).slice(0, 240) : '')));

  console.log(`\n── store, real DOM, ${storePlugins.length} plugins from the live registry ──`);

  T.setupStoreFilterListeners();
  T.applyStoreFiltersAndSort();

  const N = storePlugins.length;
  ok('page 1 holds 12 cards', cards() === 12, cards());
  ok('results info counts all ' + N, info() === `Showing 1–12 of ${N} plugins`, info());
  ok('bottom info matches top', infoBot() === info(), { top: info(), bottom: infoBot() });
  ok('pagination rendered', pageBtns().length > 0);
  ok('per-page control shows 12', $('store-per-page').value === '12');

  const totalPages = Math.ceil(N / 12);
  ok(`last page button is ${totalPages}`,
     pageBtns().some(b => b.textContent.trim() === String(totalPages)), pagTop().slice(0, 200));
  ok('prev is disabled on page 1', pageBtns()[0].hasAttribute('disabled'));

  const p1 = ids();
  clickPage(2);
  ok('page 2 renders 12 different cards', cards() === 12 && ids().join() !== p1.join());
  ok('page 2 info', info() === `Showing 13–24 of ${N} plugins`, info());
  clickPage(totalPages);
  ok('last page info ends at ' + N, new RegExp(`–${N} of ${N} plugins$`).test(info()), info());
  ok('next is disabled on the last page',
     pageBtns()[pageBtns().length - 1].hasAttribute('disabled'));
  ok('ellipsis appears for a far page', /&hellip;|…/.test(pagTop()), pagTop().slice(0, 120));
  clickPage(1);
  ok('back on page 1 shows the original slice', ids().join() === p1.join());

  // ── per-page ───────────────────────────────────────────────────────────
  change($('store-per-page'), '48');
  ok('per-page 48 shows all in one page', cards() === Math.min(48, N), cards());
  ok('pagination hidden when one page', pagTop().trim() === '' || pageBtns().length === 0, pagTop().slice(0, 80));
  ok('per-page persisted', window.localStorage.getItem('storePerPage') === '48');
  change($('store-per-page'), '12');
  ok('back to 12 restores pagination', cards() === 12 && pageBtns().length > 0);

  // ── sorts ──────────────────────────────────────────────────────────────
  const firstOf = () => ids()[0];
  change($('store-sort'), 'a-z');
  const azFirst = firstOf();
  change($('store-sort'), 'z-a');
  ok('z-a changes the leading card', firstOf() !== azFirst, { az: azFirst, za: firstOf() });
  ok('sort persisted to localStorage', window.localStorage.getItem('storeSort') === 'z-a');
  for (const s of ['category', 'author', 'newest']) {
    change($('store-sort'), s);
    ok(`sort "${s}" still fills a page`, cards() === 12, cards());
  }
  change($('store-sort'), 'a-z');

  // ── category ───────────────────────────────────────────────────────────
  const cat = [...$('plugin-category').options].map(o => o.value).find(v => v && storePlugins.some(p => (p.category || '').toLowerCase() === v.toLowerCase()));
  if (cat) {
    change($('plugin-category'), cat);
    const expect = storePlugins.filter(p => (p.category || '').toLowerCase() === cat.toLowerCase()).length;
    ok(`category "${cat}" filters to ${expect}`, cards() === Math.min(expect, 12), { cards: cards(), expect });
    ok('badge reports active filters', /filter/.test($('store-active-filters').textContent),
       $('store-active-filters').textContent);
    change($('plugin-category'), '');
    ok('category cleared restores', cards() === 12);
  } else { console.log('  --   no usable category option in the template, skipped'); }

  // ── tri-state installed button ─────────────────────────────────────────
  const instBtn = $('store-filter-installed');
  ok('starts as All', /All/.test(instBtn.innerHTML) && instBtn.classList.contains('bg-white'), instBtn.innerHTML);
  click(instBtn);
  ok('→ Installed, green', /Installed/.test(instBtn.innerHTML) && instBtn.classList.contains('bg-green-50')
     && !instBtn.classList.contains('bg-white'), instBtn.innerHTML);
  const instCount = storePlugins.filter(p => installed.some(i => i.id === p.id ||
      (p.plugin_path && i.id === p.plugin_path.split('/').pop()))).length;
  ok('Installed count matches the installed set', cards() === Math.min(instCount, 12), { cards: cards(), expect: instCount });
  click(instBtn);
  ok('→ Not Installed, red', /Not Installed/.test(instBtn.innerHTML) && instBtn.classList.contains('bg-red-50'), instBtn.innerHTML);
  ok('Not Installed count is the complement', cards() === Math.min(N - instCount, 12), { cards: cards(), expect: N - instCount });
  click(instBtn);
  ok('→ back to All', /All/.test(instBtn.innerHTML) && instBtn.classList.contains('bg-white'));

  // ── search ─────────────────────────────────────────────────────────────
  const term = (storePlugins[0].name || storePlugins[0].id).slice(0, 4);
  type($('plugin-search'), term);
  await tick(400);
  ok(`search "${term}" narrows the set`, cards() > 0 && cards() <= 12, cards());
  type($('plugin-search'), 'zzz-definitely-nothing');
  await tick(400);
  ok('no matches → 0 cards', cards() === 0);
  ok('no-match info text', info() === 'No plugins match your filters', info());
  type($('plugin-search'), '');
  await tick(400);
  ok('search cleared restores', cards() === 12);

  // ── clear filters ──────────────────────────────────────────────────────
  change($('store-per-page'), '24');
  change($('store-sort'), 'z-a');
  if (cat) change($('plugin-category'), cat);
  click(instBtn);
  type($('plugin-search'), term);
  await tick(400);
  ok('clear button visible with filters active', !$('store-clear-filters').classList.contains('hidden'));
  click($('store-clear-filters'));
  ok('clear resets search', $('plugin-search').value === '');
  ok('clear resets sort to a-z', $('store-sort').value === 'a-z');
  ok('clear resets category', $('plugin-category').value === '');
  ok('clear resets installed button to All', /All/.test(instBtn.innerHTML));
  ok('clear PRESERVES per-page 24', $('store-per-page').value === '24', $('store-per-page').value);
  ok('clear shows a full 24-card page', cards() === 24, cards());
  ok('clear hides itself', $('store-clear-filters').classList.contains('hidden'));

  // ── persistence across a reload ────────────────────────────────────────
  change($('store-sort'), 'author');
  change($('store-per-page'), '48');
  const saved = { sort: window.localStorage.getItem('storeSort'), per: window.localStorage.getItem('storePerPage') };
  ok('prefs written under the original keys', saved.sort === 'author' && saved.per === '48', saved);

  // Re-swap the partial (fresh controls at defaults) and re-boot the module, as
  // a page reload would.
  document.getElementById('app').innerHTML = partial;
  ok('fresh markup starts at defaults',
     $('store-sort').value === 'a-z' && $('store-per-page').value === '12');
  const controllerBefore = window.__t;
  boot();                       // fresh module instances, fresh localStorage read
  ok('re-boot produced a new module instance', window.__t !== controllerBefore);
  window.__t.setupStoreFilterListeners();
  window.__t.applyStoreFiltersAndSort();
  ok('reload restores sort=author into the control', $('store-sort').value === 'author', $('store-sort').value);
  ok('reload restores per-page=48 into the control', $('store-per-page').value === '48', $('store-per-page').value);
  ok('reload renders with the restored page size', cards() === Math.min(48, N), cards());

  ok('no uncaught JS errors', jsErrors.length === 0, jsErrors.slice(0, 3));

  console.log(`\n${pass} passed, ${fail} failed\n`);
  process.exit(fail ? 1 : 0);
})().catch(e => { console.log('HARNESS ERROR: ' + e.stack); process.exit(1); });
