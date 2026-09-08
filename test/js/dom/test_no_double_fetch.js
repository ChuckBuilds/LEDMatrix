// Proves CodeRabbit's second finding is fixed: typing in the store search or
// changing the category must filter the CACHED list, not refetch
// /api/v3/plugins/store/list.
//
// This loads the WHOLE of plugins_manager.js into jsdom (not a slice), so the
// legacy initializePlugins() wiring is present if it still exists, and counts
// every fetch the page makes.
const fs = require('fs');
const http = require('http');
const { JSDOM, VirtualConsole } = require('jsdom');

const path = require('path');
const V3 = path.resolve(__dirname, '../../../web_interface/static/v3');
const BASE = process.env.BASE || 'http://localhost:5000';
const get = p => new Promise((res, rej) =>
  http.get(BASE + p, r => { let d = ''; r.on('data', c => d += c); r.on('end', () => res(d)); }).on('error', rej));

(async () => {
  const partial = await get('/partials/plugins');
  const store = JSON.parse(await get('/api/v3/plugins/store/list'));
  const installed = JSON.parse(await get('/api/v3/plugins/installed'));

  const loadErrors = [];
  const vc = new VirtualConsole();          // quiet, but keep real errors
  vc.on('jsdomError', e => loadErrors.push(String(e.message || e).split('\n')[0]));
  const dom = new JSDOM(`<!doctype html><html><body><div id="app">${partial}</div></body></html>`,
    { runScripts: 'dangerously', virtualConsole: vc, url: BASE + '/', pretendToBeVisual: true });
  const { window } = dom;

  // Record every request the page attempts; serve from the payloads above.
  const calls = [];
  window.fetch = (url, opts) => {
    const u = String(url);
    calls.push(u);
    let body = { status: 'success', data: {} };
    if (u.includes('/plugins/store/list')) body = store;
    else if (u.includes('/plugins/installed')) body = installed;
    return Promise.resolve({
      ok: true, status: 200,
      json: () => Promise.resolve(body),
      text: () => Promise.resolve(JSON.stringify(body)),
    });
  };
  window.EventSource = function () { return { addEventListener() {}, close() {} }; };
  window.confirm = () => false;
  window.alert = () => {};
  window.scrollTo = () => {};

  // Globals plugins_manager.js expects from app.js (it declares /* global debugLog */).
  const s0 = window.document.createElement('script');
  s0.textContent = `
    window.debugLog = function () {};
    window.showNotification = function () {};
    window.showError = function () {};
    window.updateSystemStatus = function () {};
    window.Alpine = undefined;
    window.htmx = { process: function () {}, ajax: function () {} };
  `;
  window.document.body.appendChild(s0);

  const s = window.document.createElement('script');
  s.textContent = fs.readFileSync(V3 + '/js/plugins/list_filter.js', 'utf8');
  window.document.body.appendChild(s);

  const s2 = window.document.createElement('script');
  s2.textContent = fs.readFileSync(V3 + '/plugins_manager.js', 'utf8');
  window.document.body.appendChild(s2);

  let pass = 0, fail = 0;
  const ok = (l, c, x) => c ? (pass++, console.log('  ok   ' + l))
    : (fail++, console.log('  FAIL ' + l + (x !== undefined ? '  → ' + JSON.stringify(x).slice(0, 300) : '')));
  const tick = ms => new Promise(r => setTimeout(r, ms));

  console.log('\n── store search must not refetch (CodeRabbit #2) ──');
  if (loadErrors.length) console.log('   load errors: ' + loadErrors.slice(0, 3).join(' | '));
  ok('whole plugins_manager.js evaluated', typeof window.initPluginsPage === 'function');

  if (typeof window.initPluginsPage === 'function') window.initPluginsPage();
  await tick(600);

  const storeListCalls = () => calls.filter(u => u.includes('/plugins/store/list')).length;
  const afterInit = storeListCalls();
  console.log(`   init issued ${afterInit} store/list request(s) — that part is expected`);
  ok('init loaded the store at least once', afterInit >= 1, calls);

  const search = window.document.getElementById('plugin-search');
  const category = window.document.getElementById('plugin-category');
  ok('store search input present', !!search);

  // Type a realistic burst, then wait past both debounce windows (300 ms each).
  const before = storeListCalls();
  for (const v of ['w', 'we', 'wea', 'weat', 'weath', 'weathe', 'weather']) {
    search.value = v;
    search.dispatchEvent(new window.Event('input', { bubbles: true }));
    await tick(40);
  }
  await tick(700);
  ok('typing 7 characters issued ZERO new store/list fetches',
     storeListCalls() === before, { before, after: storeListCalls(), calls: calls.slice(-4) });

  const beforeCat = storeListCalls();
  if (category) {
    category.value = 'sports';
    category.dispatchEvent(new window.Event('change', { bubbles: true }));
    await tick(700);
    ok('changing category issued ZERO new store/list fetches',
       storeListCalls() === beforeCat, { before: beforeCat, after: storeListCalls() });
  }

  // ...and the filtering still actually happened.
  // Clear both axes before counting: 'weather' AND category=sports legitimately
  // matches nothing, so counting here would measure the filter, not the render.
  const beforeClear = storeListCalls();
  search.value = '';
  search.dispatchEvent(new window.Event('input', { bubbles: true }));
  if (category) { category.value = ''; category.dispatchEvent(new window.Event('change', { bubbles: true })); }
  await tick(700);
  ok('clearing the filters also issued ZERO fetches', storeListCalls() === beforeClear,
     { before: beforeClear, after: storeListCalls() });

  // Count only real cards: the partial ships loading skeletons that also carry
  // .plugin-card, which would let this pass on a dead page.
  const grid = window.document.getElementById('plugin-store-grid');
  const real = grid.querySelectorAll('.plugin-card:not(.animate-pulse)').length;
  ok('the grid rendered real cards from the cache', real > 0, { real, html: grid.innerHTML.slice(0, 160) });

  ok('no leftover _listenerSetup flag on the search input',
     search && search._listenerSetup === undefined, search && search._listenerSetup);

  console.log(`\n${pass} passed, ${fail} failed\n`);
  process.exit(fail ? 1 : 0);
})().catch(e => { console.log('HARNESS ERROR: ' + e.stack.split('\n').slice(0, 6).join('\n')); process.exit(1); });
