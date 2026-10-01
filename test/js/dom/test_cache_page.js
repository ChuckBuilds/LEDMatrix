// The Cache tab as a page module (static/v3/js/pages/cache.js), in a real DOM
// (jsdom) with the real server-rendered partial and the real API's payload
// shape. The reference conversion for docs/WEB_FRONTEND_ARCHITECTURE.md, so
// this pins what every converted page must do:
//
//   * the partial ships no <script>; its root is data-page="cache"
//   * the page starts once per swap-in and stops on swap-out: repeated htmx
//     swaps leave exactly one live set of listeners (one request per Refresh
//     click, however many times the tab was reloaded)
//   * a request still in flight when the page is swapped away is cancelled
//     and draws nothing
//   * server data reaches the page as text, never as markup
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
  const partial = await get('/partials/cache');
  const real = JSON.parse(await get('/api/v3/cache/list'));
  const { createRegistry } = await load('core/registry.js');
  const { createApi } = await load('core/api.js');
  const cachePage = await load('pages/cache.js');

  console.log('\n── Cache tab: page module (real DOM) ──');
  ok('the partial ships no inline script', !/<script/i.test(partial));
  ok('the partial root is data-page="cache"', /data-page="cache"/.test(partial));
  ok('the real API answers in the shape the page reads',
     real.status === 'success' && real.data && Array.isArray(real.data.cache_files), real);

  // Real shape, plus entries the page must treat as text.
  const HOSTILE = '<img src=x onerror="window.pwned=1">\'"&';
  const sample = Object.assign({}, real.data, {
    cache_dir: real.data.cache_dir || '/var/cache/ledmatrix',
    cache_files: [
      { key: 'weather_current', filename: 'weather_current.json', age_seconds: 12,
        age_display: '12s', size_display: '1.2 KB', modified_datetime: '2026-09-30T12:00:00' },
      { key: HOSTILE, filename: HOSTILE + '.json', age_seconds: 7200,
        age_display: '2h', size_display: '3 B', modified_datetime: '2026-09-30T10:00:00' },
    ],
  });

  const errs = [];
  const vc = new VirtualConsole();
  vc.on('jsdomError', e => errs.push(String(e.message || e).split('\n')[0]));
  vc.on('error', (...a) => errs.push(a.join(' ')));
  const dom = new JSDOM(`<!doctype html><html><body><div id="cache-content">${partial}</div></body></html>`,
    { url: BASE + '/', virtualConsole: vc });
  const { window } = dom;
  const doc = window.document;
  const panel = doc.getElementById('cache-content');

  // Controllable API.
  let listBody = { status: 'success', data: sample };
  let listMode = 'ok';
  const requests = [];
  const pending = [];
  function fakeFetch(url, init) {
    requests.push({ url, method: init.method, body: init.body });
    const respond = (status, body, headers = {}) => Promise.resolve({
      status, ok: status >= 200 && status < 300,
      headers: { get: n => headers[n] || null },
      text: () => Promise.resolve(JSON.stringify(body)),
    });
    if (url === '/api/v3/cache/delete') return respond(200, { status: 'success', message: 'Deleted it' });
    if (listMode === 'network') return Promise.reject(new TypeError('Failed to fetch'));
    if (listMode === 'login') return respond(401, { status: 'error' }, { 'X-LEDMatrix-Login': '/login' });
    if (listMode === 'hang') {
      return new Promise((resolve, reject) => {
        pending.push(resolve);
        init.signal.addEventListener('abort', () => {
          const e = new Error('aborted'); e.name = 'AbortError'; reject(e);
        });
      });
    }
    return respond(200, listBody);
  }
  const notes = [];
  const registry = createRegistry({
    document: doc,
    context: { api: createApi({ fetch: fakeFetch }), notify: (m, t) => notes.push([m, t]) },
  });
  registry.register('cache', cachePage);

  const lists = () => requests.filter(r => r.url === '/api/v3/cache/list').length;
  const $ = id => doc.getElementById(id);
  const visible = id => !$(id).classList.contains('hidden');
  // What htmx does around a swap of the tab panel.
  async function swap() {
    panel.dispatchEvent(new window.CustomEvent('htmx:beforeSwap', { bubbles: true, detail: { target: panel, shouldSwap: true } }));
    panel.innerHTML = partial;
    panel.dispatchEvent(new window.CustomEvent('htmx:afterSwap', { bubbles: true, detail: { target: panel } }));
    await tick(20);
  }

  await registry.start();
  await tick(20);

  // ── first load ──────────────────────────────────────────────────────────
  ok('one list request on start', lists() === 1, lists());
  const rows = doc.querySelectorAll('#cache-files-tbody tr');
  ok('one row per cache file', rows.length === 2, rows.length);
  ok('cache directory shown', $('cache-dir').textContent === sample.cache_dir, $('cache-dir').textContent);
  ok('hostile key is shown as text', rows[1].textContent.includes(HOSTILE));
  ok('...and created no element', !doc.querySelector('#cache-files-tbody img') && !window.pwned);
  const buttons = [...doc.querySelectorAll('#cache-files-tbody button[data-cache-key]')];
  ok('delete buttons carry the exact key', buttons.map(b => b.dataset.cacheKey).join('|') === 'weather_current|' + HOSTILE);
  ok('delete buttons have no inline handler', buttons.every(b => !b.getAttribute('onclick')));
  ok('fresh entries are green, old ones red',
     rows[0].querySelector('.text-green-600') && rows[1].querySelector('.text-red-600'));

  // ── repeated swaps ──────────────────────────────────────────────────────
  const oldRefresh = $('refresh-cache-btn');
  for (let i = 0; i < 5; i++) await swap();
  ok('one list request per swap', lists() === 6, lists());
  ok('one mounted page after five swaps', registry.list().length === 1, registry.list().length);
  const before = lists();
  $('refresh-cache-btn').click();
  await tick(20);
  ok('Refresh makes exactly one request (no duplicate listeners)', lists() === before + 1, lists() - before);
  oldRefresh.click();
  await tick(20);
  ok('a swapped-out button does nothing', lists() === before + 1, lists() - before);

  // ── delete ──────────────────────────────────────────────────────────────
  let asked = null;
  window.confirm = msg => { asked = msg; return false; };
  doc.querySelector('#cache-files-tbody button[data-cache-key]').click();
  await tick(20);
  ok('delete asks first', asked && asked.includes('weather_current'), asked);
  ok('cancel sends nothing', !requests.some(r => r.url === '/api/v3/cache/delete'));
  window.confirm = () => true;
  const listsBeforeDelete = lists();
  doc.querySelectorAll('#cache-files-tbody button[data-cache-key]')[1].click();
  await tick(30);
  const del = requests.filter(r => r.url === '/api/v3/cache/delete');
  ok('one delete request', del.length === 1, del.length);
  ok('it posts the exact key as JSON', del[0] && del[0].method === 'POST' && JSON.parse(del[0].body).key === HOSTILE);
  ok('the server\'s message is shown', notes.some(n => n[0] === 'Deleted it' && n[1] === 'success'), notes);
  ok('the list reloads after a delete', lists() === listsBeforeDelete + 1, lists() - listsBeforeDelete);
  const viaAlias = await cachePage.deleteCacheFile('weather_current');
  ok('the old deleteCacheFile(key) entry point still works', viaAlias === true);

  // ── states ──────────────────────────────────────────────────────────────
  listBody = { status: 'success', data: { cache_dir: null, cache_files: [] } };
  $('refresh-cache-btn').click(); await tick(20);
  ok('empty state shown', visible('cache-empty') && !visible('cache-error') && !doc.querySelector('#cache-files-tbody tr'));
  ok('a missing cache directory says so', $('cache-dir').textContent === 'Not configured');

  listBody = { status: 'error', message: 'Cache unavailable' };
  $('refresh-cache-btn').click(); await tick(20);
  ok('an API error shows its message', visible('cache-error') && $('cache-error-message').textContent === 'Cache unavailable',
     $('cache-error-message').textContent);

  listMode = 'network';
  $('refresh-cache-btn').click(); await tick(20);
  ok('a network failure says so', $('cache-error-message').textContent === 'Error loading cache files: Failed to fetch',
     $('cache-error-message').textContent);

  listMode = 'ok'; listBody = { status: 'success', data: sample };
  await swap();
  listMode = 'login';
  $('cache-error').classList.add('hidden');
  $('refresh-cache-btn').click(); await tick(20);
  ok('the login redirect draws no error', !visible('cache-error'));

  // ── in flight when swapped away ─────────────────────────────────────────
  listMode = 'hang';
  $('refresh-cache-btn').click(); await tick(5);
  ok('a request is in flight', pending.length >= 1);
  listMode = 'ok';
  await swap();
  ok('the new page drew its own list', doc.querySelectorAll('#cache-files-tbody tr').length === 2);
  ok('the cancelled request drew nothing', !visible('cache-error'));

  ok('no DOM errors', errs.length === 0, errs);

  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
