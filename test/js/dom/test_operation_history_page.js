// The Operation History tab as a page module
// (static/v3/js/pages/operation-history.js), in a real DOM (jsdom) with the
// real server-rendered partial and the real API's payload shape. Built like
// test_cache_page.js:
//
//   * the partial ships no <script>; its root is data-page="operation-history"
//   * one history request per swap-in, and after five swaps Refresh, Clear,
//     Next and the filters each act exactly once
//   * the plugin filter is filled once per swap-in, not once per swap so far
//   * a request in flight when the page is swapped away is cancelled
//   * plugin ids, users and error messages from the log are shown as text
const http = require('http');
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
  const partial = await get('/partials/operation-history');
  const real = JSON.parse(await get('/api/v3/plugins/operation/history?limit=1000'));
  const { createRegistry } = await load('core/registry.js');
  const { createApi } = await load('core/api.js');
  const historyPage = await load('pages/operation-history.js');

  console.log('\n── Operation History tab: page module (real DOM) ──');
  ok('the partial ships no inline script', !/<script/i.test(partial));
  ok('the partial root is data-page="operation-history"', /data-page="operation-history"/.test(partial));
  ok('the real API answers in the shape the page reads',
     real.status === 'success' && Array.isArray(real.data), real);
  const realKeys = real.data.length ? Object.keys(real.data[0]) : null;
  ok('a real record has the fields the page reads',
     !realKeys || ['operation_type', 'plugin_id', 'status', 'timestamp'].every(k => realKeys.includes(k)), realKeys);

  const HOSTILE = '<img src=x onerror="window.pwned=1">';
  function record(i, extra) {
    return Object.assign({
      operation_id: 'op' + i, operation_type: ['install', 'update', 'enable'][i % 3],
      plugin_id: i % 2 ? 'clock' : 'weather', status: i % 4 === 0 ? 'error' : 'completed',
      user: 'web', timestamp: 1790000000 * 1000 - i * 60000, details: { version: '1.' + i },
    }, extra || {});
  }
  let records = [
    record(0, { plugin_id: HOSTILE, error: HOSTILE + ' went wrong', user: HOSTILE,
                details: { commit: 'abcdef1234', previous_commit: '1234567890' } }),
  ].concat(Array.from({ length: 119 }, (_, i) => record(i + 1)));

  const errs = [];
  const vc = new VirtualConsole();
  vc.on('jsdomError', e => errs.push(String(e.message || e).split('\n')[0]));
  vc.on('error', (...a) => errs.push(a.join(' ')));
  const dom = new JSDOM(`<!doctype html><html><body><div id="operation-history-content">${partial}</div></body></html>`,
    { url: BASE + '/', virtualConsole: vc });
  const { window } = dom;
  const doc = window.document;
  const panel = doc.getElementById('operation-history-content');

  let mode = 'ok';
  let aborted = 0;
  const requests = [];
  function fakeFetch(url, init) {
    requests.push({ url, method: init.method });
    const respond = (status, body, headers = {}) => Promise.resolve({
      status, ok: status >= 200 && status < 300,
      headers: { get: n => headers[n] || null },
      text: () => Promise.resolve(JSON.stringify(body)),
    });
    if (url === '/api/v3/plugins/installed') {
      return respond(200, { status: 'success', data: { plugins: [{ id: 'clock' }, { id: 'weather' }, { id: HOSTILE }] } });
    }
    if (init.method === 'DELETE') return respond(200, { status: 'success', message: 'Operation history cleared' });
    if (mode === 'network') return Promise.reject(new TypeError('Failed to fetch'));
    if (mode === 'error') return respond(500, { status: 'error', message: 'Operation history not initialized' });
    if (mode === 'login') return respond(401, { status: 'error' }, { 'X-LEDMatrix-Login': '/login' });
    if (mode === 'hang') {
      return new Promise((resolve, reject) => {
        init.signal.addEventListener('abort', () => {
          aborted++;
          const e = new Error('aborted'); e.name = 'AbortError'; reject(e);
        });
      });
    }
    return respond(200, { status: 'success', data: records });
  }
  const notes = [];
  const registry = createRegistry({
    document: doc,
    context: { api: createApi({ fetch: fakeFetch }), notify: (m, t) => notes.push([m, t]) },
  });
  registry.register('operation-history', historyPage);

  const count = (url, method = 'GET') => requests.filter(r => r.url === url && r.method === method).length;
  const HISTORY = '/api/v3/plugins/operation/history?limit=1000';
  const histories = () => count(HISTORY);
  const $ = id => doc.getElementById(id);
  const rows = () => doc.querySelectorAll('#history-table-body tr');
  const showing = () => [$('history-start').textContent, $('history-end').textContent, $('history-total').textContent].join('/');
  async function swap() {
    panel.dispatchEvent(new window.CustomEvent('htmx:beforeSwap', { bubbles: true, detail: { target: panel, shouldSwap: true } }));
    panel.innerHTML = partial;
    panel.dispatchEvent(new window.CustomEvent('htmx:afterSwap', { bubbles: true, detail: { target: panel } }));
    await tick(20);
  }
  function change(id, value) {
    $(id).value = value;
    $(id).dispatchEvent(new window.Event('change', { bubbles: true }));
  }

  await registry.start();
  await tick(20);

  // ── first load ──────────────────────────────────────────────────────────
  ok('one history request on start', histories() === 1, histories());
  ok('one plugin-list request on start', count('/api/v3/plugins/installed') === 1);
  ok('the first page holds 50 rows', rows().length === 50, rows().length);
  ok('the counters say 1 to 50 of 120', showing() === '1/50/120', showing());
  ok('Previous is off on the first page', $('history-prev-btn').disabled && !$('history-next-btn').disabled);
  const first = rows()[0];
  ok('a hostile plugin id, user and error are shown as text',
     first.textContent.includes(HOSTILE) && first.textContent.includes(HOSTILE + ' went wrong'));
  ok('...and created no element', !doc.querySelector('#history-table-body img') && !window.pwned);
  ok('a hostile plugin id in the filter is text too',
     [...$('history-plugin-filter').options].some(o => o.value === HOSTILE && o.textContent === HOSTILE));
  ok('details are summarised', first.textContent.includes('commit: abcdef1') && first.textContent.includes('from: 1234567'));
  ok('"error" is shown as failed, in red',
     first.querySelectorAll('span')[1].textContent === 'failed' && first.querySelector('.bg-red-100'));

  // ── repeated swaps ──────────────────────────────────────────────────────
  const oldRefresh = $('refresh-history-btn');
  for (let i = 0; i < 5; i++) await swap();
  ok('one history request per swap', histories() === 6, histories());
  ok('one mounted page after five swaps', registry.list().length === 1, registry.list().length);
  ok('the plugin filter is filled once', $('history-plugin-filter').options.length === 4,
     $('history-plugin-filter').options.length);

  let before = histories();
  $('refresh-history-btn').click();
  await tick(20);
  ok('Refresh makes exactly one request (no duplicate listeners)', histories() === before + 1, histories() - before);
  oldRefresh.click();
  await tick(20);
  ok('a swapped-out button does nothing', histories() === before + 1, histories() - before);

  $('history-next-btn').click();
  ok('Next moves one page', showing() === '51/100/120', showing());
  $('history-next-btn').click();
  ok('...and the last page is short', showing() === '101/120/120' && rows().length === 20, showing());
  ok('Next is off on the last page', $('history-next-btn').disabled);
  $('refresh-history-btn').click();
  await tick(20);
  ok('Refresh keeps the page', showing() === '101/120/120', showing());
  $('history-prev-btn').click();
  ok('Previous moves one page', showing() === '51/100/120', showing());

  // ── filters ─────────────────────────────────────────────────────────────
  change('history-status-filter', 'failed');
  ok('the status filter matches "error" records as failed', showing() === '1/30/30', showing());
  change('history-status-filter', '');
  change('history-plugin-filter', 'weather');
  ok('the plugin filter', [...rows()].every(r => r.children[2].textContent === 'weather') && showing() === '1/50/59', showing());
  change('history-plugin-filter', '');
  $('history-search').value = 'no such thing';
  $('history-search').dispatchEvent(new window.Event('input', { bubbles: true }));
  ok('search waits for the typing to stop', rows().length === 50);
  await tick(350);
  ok('a search with no match says so', rows().length === 1 && rows()[0].textContent.includes('No operations found'));
  ok('...and the counters follow', showing() === '0/0/0', showing());
  $('history-search').value = '';
  $('history-search').dispatchEvent(new window.Event('input', { bubbles: true }));
  await swap(); // swapped away with the search still pending
  await tick(350);
  ok('a pending search on a swapped-out page does nothing', errs.length === 0, errs);

  // ── clear ───────────────────────────────────────────────────────────────
  let asked = null;
  window.confirm = msg => { asked = msg; return false; };
  $('clear-history-btn').click();
  await tick(20);
  ok('Clear asks first', asked && /clear the operation history/.test(asked), asked);
  ok('cancel sends nothing', count('/api/v3/plugins/operation/history', 'DELETE') === 0);
  window.confirm = () => true;
  $('clear-history-btn').click();
  await tick(20);
  ok('one DELETE request', count('/api/v3/plugins/operation/history', 'DELETE') === 1,
     count('/api/v3/plugins/operation/history', 'DELETE'));
  ok('the table empties', rows().length === 1 && showing() === '0/0/0', showing());

  // ── states ──────────────────────────────────────────────────────────────
  notes.length = 0;
  mode = 'error';
  $('refresh-history-btn').click(); await tick(20);
  ok('an API error is reported', notes.length === 1 && notes[0][0] === 'Failed to load operation history' && notes[0][1] === 'error', notes);
  mode = 'network';
  $('refresh-history-btn').click(); await tick(20);
  ok('a network failure is reported', notes.length === 2 && notes[1][0] === 'Error loading operation history', notes);
  mode = 'login';
  $('refresh-history-btn').click(); await tick(20);
  ok('the login redirect reports nothing', notes.length === 2, notes);

  // ── in flight when swapped away ─────────────────────────────────────────
  mode = 'hang';
  $('refresh-history-btn').click(); await tick(5);
  mode = 'ok';
  await swap();
  ok('the swap cancelled the request', aborted === 1, aborted);
  ok('the new page drew its own list', rows().length === 50 && notes.length === 2, [rows().length, notes]);

  // ── PluginAPI's cached installed list, when it is loaded ────────────────
  let pluginApiCalls = 0;
  window.PluginAPI = { getInstalledPlugins: () => { pluginApiCalls++; return Promise.resolve([{ id: 'cached' }]); } };
  const directBefore = count('/api/v3/plugins/installed');
  await swap();
  ok('the plugin filter uses PluginAPI when it is there', pluginApiCalls === 1
     && count('/api/v3/plugins/installed') === directBefore, [pluginApiCalls, count('/api/v3/plugins/installed') - directBefore]);
  ok('...and is filled from it', [...$('history-plugin-filter').options].map(o => o.value).join() === ',cached');
  delete window.PluginAPI;

  ok('no DOM errors', errs.length === 0, errs);

  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
