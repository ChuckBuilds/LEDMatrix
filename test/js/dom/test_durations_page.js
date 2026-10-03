// The Rotation & Durations tab as a page module
// (static/v3/js/pages/durations.js), in a real DOM (jsdom) with the real
// server-rendered partial, the real plugin-order-list widget and the real
// /api/v3/plugins/installed payload shape. Built like test_cache_page.js:
//
//   * the partial ships no <script>; its root is data-page="durations"
//   * the rotation list loads once per swap-in, however many swaps came first
//   * a list request still in flight when the page is swapped away is
//     cancelled and draws nothing
//   * a widget that loads late is waited for, and a page swapped away while
//     waiting starts nothing
//   * plugin names reach the page as text
const http = require('http');
const fs = require('fs');
const path = require('path');
const { pathToFileURL } = require('url');
const { JSDOM, VirtualConsole } = require('jsdom');

const BASE = process.env.BASE || 'http://localhost:5000';
const JS = path.resolve(__dirname, '../../../web_interface/static/v3/js');
const get = p => new Promise((res, rej) =>
  http.get(BASE + p, r => { let d = ''; r.on('data', c => d += c); r.on('end', () => res(d)); }).on('error', rej));
const load = f => import(pathToFileURL(path.join(JS, f)).href);
const tick = ms => new Promise(r => setTimeout(r, ms || 0));

let pass = 0, fail = 0;
const ok = (l, c, x) => c ? (pass++, console.log('  ok   ' + l))
  : (fail++, console.log('  FAIL ' + l + (x !== undefined ? '  -> ' + JSON.stringify(x).slice(0, 300) : '')));

(async () => {
  const partial = await get('/partials/durations');
  const real = JSON.parse(await get('/api/v3/plugins/installed'));
  const { createRegistry } = await load('core/registry.js');
  const { createApi } = await load('core/api.js');
  const durationsPage = await load('pages/durations.js');
  const widgetSource = fs.readFileSync(path.join(JS, 'widgets/plugin-order-list.js'), 'utf8');

  console.log('\n── Rotation & Durations tab: page module (real DOM) ──');
  ok('the partial ships no inline script', !/<script/i.test(partial));
  ok('the partial root is data-page="durations"', /data-page="durations"/.test(partial));
  ok('the rotation list and its hidden input are in the partial',
     /id="rotation_plugin_order"/.test(partial) && /id="rotation_plugin_order_value"/.test(partial));
  ok('the real API answers in the shape the widget reads',
     real.status === 'success' && real.data && Array.isArray(real.data.plugins), real);

  const HOSTILE = '<img src=x onerror="window.pwned=1">';
  const plugins = [
    { id: 'clock', name: 'Clock', enabled: true },
    { id: 'weather', name: HOSTILE, enabled: true },
    { id: 'stocks', name: 'Stocks', enabled: true },
    { id: 'off', name: 'Disabled one', enabled: false },
  ];

  const errs = [];
  const vc = new VirtualConsole();
  vc.on('jsdomError', e => errs.push(String(e.message || e).split('\n')[0]));
  vc.on('error', (...a) => errs.push(a.join(' ')));
  const dom = new JSDOM(`<!doctype html><html><body><div id="durations-content">${partial}</div></body></html>`,
    { url: BASE + '/', virtualConsole: vc, runScripts: 'outside-only' });
  const { window } = dom;
  const doc = window.document;
  const panel = doc.getElementById('durations-content');

  let mode = 'ok';
  const requests = [];
  const pending = [];
  let aborted = 0;
  function fakeFetch(url, init = {}) {
    requests.push({ url, method: init.method || 'GET' });
    const respond = (status, body) => Promise.resolve({
      status, ok: status >= 200 && status < 300,
      headers: { get: () => null },
      json: () => Promise.resolve(body),
      text: () => Promise.resolve(JSON.stringify(body)),
    });
    if (mode === 'hang') {
      return new Promise((resolve, reject) => {
        pending.push(resolve);
        if (init.signal) init.signal.addEventListener('abort', () => {
          aborted++;
          const e = new Error('aborted'); e.name = 'AbortError'; reject(e);
        });
      });
    }
    return respond(200, { status: 'success', data: { plugins } });
  }
  // The widget is a classic script: it calls the window's own fetch.
  window.fetch = fakeFetch;
  window.eval(widgetSource);
  const widget = window.PluginOrderList;
  ok('the widget script defines PluginOrderList', !!(widget && widget.init));

  const registry = createRegistry({
    document: doc,
    context: { api: createApi({ fetch: fakeFetch }), notify: () => {} },
  });
  registry.register('durations', durationsPage);

  const lists = () => requests.filter(r => r.url === '/api/v3/plugins/installed').length;
  const $ = id => doc.getElementById(id);
  const order = () => JSON.parse($('rotation_plugin_order_value').value || '[]');
  async function swap() {
    panel.dispatchEvent(new window.CustomEvent('htmx:beforeSwap', { bubbles: true, detail: { target: panel, shouldSwap: true } }));
    panel.innerHTML = partial;
    panel.dispatchEvent(new window.CustomEvent('htmx:afterSwap', { bubbles: true, detail: { target: panel } }));
    await tick(20);
  }

  await registry.start();
  await tick(20);

  // ── first load ──────────────────────────────────────────────────────────
  ok('one plugin-list request on start', lists() === 1, lists());
  let rows = doc.querySelectorAll('#rotation_plugin_order .plugin-order-item');
  ok('one row per enabled plugin', rows.length === 3, rows.length);
  ok('the hidden input holds the order', order().length === 3 && order().includes('clock'), order());
  ok('a hostile plugin name is shown as text', $('rotation_plugin_order').textContent.includes(HOSTILE));
  ok('...and created no element', !doc.querySelector('#rotation_plugin_order img') && !window.pwned);

  // ── repeated swaps ──────────────────────────────────────────────────────
  for (let i = 0; i < 5; i++) await swap();
  ok('one plugin-list request per swap', lists() === 6, lists());
  ok('one mounted page after five swaps', registry.list().length === 1, registry.list().length);
  rows = doc.querySelectorAll('#rotation_plugin_order .plugin-order-item');
  ok('the list is drawn once, not stacked', rows.length === 3, rows.length);
  const before = order();
  const down = rows[0].querySelector('button[aria-label^="Move"][aria-label$="down"]');
  down.click();
  await tick(5);
  const after = order();
  ok('Move down moves one place (one listener)',
     after[0] === before[1] && after[1] === before[0] && after[2] === before[2], [before, after]);
  ok('reordering makes no request', lists() === 6, lists());

  // ── in flight when swapped away ─────────────────────────────────────────
  mode = 'hang';
  await swap();
  ok('a request is in flight', pending.length === 1, pending.length);
  mode = 'ok';
  await swap();
  await tick(20);
  ok('the new page drew its own list', doc.querySelectorAll('#rotation_plugin_order .plugin-order-item').length === 3);
  ok('the cancelled request drew no error', !/Error loading plugins/.test($('rotation_plugin_order').textContent));
  ok('the swap cancelled the request (ctx.signal reaches the widget)', aborted === 1, aborted);
  // Answer it late anyway, with one plugin: the new page's list is untouched.
  pending[0]({ status: 200, ok: true, headers: { get: () => null },
               json: () => Promise.resolve({ status: 'success', data: { plugins: [plugins[0]] } }) });
  await tick(20);
  ok('a late answer to the cancelled request redraws nothing',
     doc.querySelectorAll('#rotation_plugin_order .plugin-order-item').length === 3,
     doc.querySelectorAll('#rotation_plugin_order .plugin-order-item').length);

  // ── the widget loads late ───────────────────────────────────────────────
  delete window.PluginOrderList;
  const beforeLate = lists();
  await swap();
  ok('no request while the widget is missing', lists() === beforeLate, lists() - beforeLate);
  window.PluginOrderList = widget;
  await tick(150);
  ok('starts once the widget arrives', lists() === beforeLate + 1, lists() - beforeLate);

  delete window.PluginOrderList;
  await swap();
  const beforeGone = lists();
  // Swapped to another tab's content while still waiting for the widget.
  panel.dispatchEvent(new window.CustomEvent('htmx:beforeSwap', { bubbles: true, detail: { target: panel, shouldSwap: true } }));
  panel.innerHTML = '<p>another tab</p>';
  panel.dispatchEvent(new window.CustomEvent('htmx:afterSwap', { bubbles: true, detail: { target: panel } }));
  window.PluginOrderList = widget;
  await tick(250);
  ok('a page swapped away while waiting starts nothing', lists() === beforeGone, lists() - beforeGone);
  ok('nothing left mounted', registry.list().length === 0, registry.list().length);

  ok('no DOM errors', errs.length === 0, errs);

  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
