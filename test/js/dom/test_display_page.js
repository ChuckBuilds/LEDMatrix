// The Display tab as a page module (static/v3/js/pages/display.js), in a
// real DOM (jsdom) with the real server-rendered partial, the real
// plugin-order-list widget, the real window.LEDVisibility (app-shell.js)
// behind ctx.visibility, and the real API's answer shapes. Built like
// test_cache_page.js:
//
//   * the partial ships no <script> and no inline handlers; its root is
//     data-page="display"
//   * after five swaps: one mounted page, the Vegas order drawn once, each
//     control acting once (brightness, resolution, Vegas and double-sided
//     toggles, the Advanced section toggle, one debounced scroll-speed hint
//     request)
//   * the sync status is polled only while the Display tab is on screen and
//     the browser tab visible: no interval runs while hidden, and none after
//     the partial is swapped out
//   * sync states drawn as text; a failed poll says "unavailable", a login
//     redirect draws nothing
//   * a widget that loads late is waited for, and a page swapped away while
//     waiting starts nothing
//   * window.updateSyncUI's entry point still works
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
  const partial = await get('/partials/display');
  const realSync = JSON.parse(await get('/api/v3/sync/status'));
  const smooth = JSON.parse(await get('/api/v3/config/scroll-speed-advice?speed=50&min=1&max=200'));
  const rough = JSON.parse(await get('/api/v3/config/scroll-speed-advice?speed=37&min=1&max=200'));
  const { createRegistry } = await load('core/registry.js');
  const { createApi } = await load('core/api.js');
  const { createVisibility } = await load('core/visibility.js');
  const displayPage = await load('pages/display.js');

  console.log('\n── Display tab: page module (real DOM) ──');
  ok('the partial ships no inline script', !/<script/i.test(partial));
  ok('the partial has no inline handlers', !/\son(click|change|input)=/i.test(partial));
  ok('the partial root is data-page="display"', /data-page="display"/.test(partial));
  ok('the Advanced toggle names its action',
     /data-action="toggle-section"\s+data-section="display-section-advanced-hardware"/.test(partial));
  ok('the real sync status answers in the shape the page reads',
     realSync.status === 'success' && realSync.data && typeof realSync.data.state === 'string', realSync);
  ok('the real scroll-speed advice answers in the shape the page reads',
     smooth.status === 'success' && smooth.data.applied && Array.isArray(rough.data.alternatives)
       && rough.data.alternatives.length > 0, rough);

  const errs = [];
  const logged = [];
  const vc = new VirtualConsole();
  vc.on('jsdomError', e => errs.push(String(e.message || e).split('\n')[0]));
  vc.on('error', (...a) => logged.push(a.join(' ')));
  const dom = new JSDOM(`<!doctype html><html><body><div id="display-content">${partial}</div></body></html>`,
    { url: BASE + '/', virtualConsole: vc, runScripts: 'outside-only' });
  const { window } = dom;
  const doc = window.document;
  const panel = doc.getElementById('display-content');

  // The browser tab's visibility and the app's active tab, under test control.
  let hidden = false;
  Object.defineProperty(doc, 'hidden', { get: () => hidden, configurable: true });
  const setHidden = v => { hidden = v; doc.dispatchEvent(new window.Event('visibilitychange')); };
  const setTab = tab => doc.dispatchEvent(new window.CustomEvent('ledmatrix:tab-changed', { detail: { tab } }));
  // Intervals, counted. Timeouts (the hint's debounce, the widget retry) are real.
  const intervals = new Map();
  let nextInterval = 1;
  window.setInterval = (fn, ms) => { const id = nextInterval++; intervals.set(id, { fn, ms }); return id; };
  window.clearInterval = id => { intervals.delete(id); };
  const fireIntervals = () => [...intervals.values()].forEach(i => i.fn());

  const HOSTILE = '<img src=x onerror="window.pwned=1">';
  const plugins = [
    { id: 'clock', name: 'Clock', enabled: true },
    { id: 'weather', name: HOSTILE, enabled: true },
  ];
  let syncAnswer = { status: 'success', data: { role: 'leader', state: 'no_peer' } };
  let syncMode = 'ok';
  let advice = smooth;
  const shortfall = { measured_hz: 110.4, planned_hz: 120, suggested_cap_hz: 100, slow_percent: 8 };
  let refreshAnswer = { status: 'success', data: { planned_hz: 120, measured_hz: 110.4, shortfall } };
  const requests = [];
  function fakeFetch(url, init = {}) {
    requests.push(url);
    const respond = (status, body, headers) => Promise.resolve({
      status, ok: status >= 200 && status < 300,
      headers: { get: h => (headers || {})[h] || null },
      json: () => Promise.resolve(body),
      text: () => Promise.resolve(JSON.stringify(body)),
    });
    if (url === '/api/v3/plugins/installed') return respond(200, { status: 'success', data: { plugins } });
    if (url.startsWith('/api/v3/config/scroll-speed-advice?')) return respond(200, advice);
    if (url === '/api/v3/config/refresh-rate') return respond(200, refreshAnswer);
    if (url === '/api/v3/sync/status') {
      if (syncMode === 'network') return Promise.reject(new TypeError('Failed to fetch'));
      if (syncMode === 'login') return respond(401, { status: 'error' }, { 'X-LEDMatrix-Login': '/login' });
      return respond(200, syncAnswer);
    }
    return respond(404, { status: 'error', message: 'unexpected ' + url });
  }
  window.fetch = fakeFetch;

  // The shell: LEDVisibility (no Alpine here, so the active tab is the last
  // ledmatrix:tab-changed; the SSE streams open stand-in EventSources), and
  // the shared toggleSection the Advanced button calls.
  window.getApp = () => null;
  window.EventSource = class { addEventListener() {} removeEventListener() {} close() {} };
  window.eval(fs.readFileSync(path.join(JS, 'app-shell.js'), 'utf8'));
  const toggled = [];
  window.toggleSection = id => toggled.push(id);
  window.eval(fs.readFileSync(path.join(JS, 'widgets/plugin-order-list.js'), 'utf8'));
  const widget = window.PluginOrderList;
  ok('the widget script defines PluginOrderList', !!(widget && widget.init));

  const visibility = createVisibility({ window });
  const registry = createRegistry({
    document: doc,
    context: { api: createApi({ fetch: fakeFetch }), notify: () => {} },
    mountContext: ctx => ({ visibility: visibility.forPage(ctx) }),
  });
  registry.register('display', displayPage);

  const $ = id => doc.getElementById(id);
  const root = () => doc.querySelector('[data-page="display"]');
  const count = prefix => requests.filter(u => u.startsWith(prefix)).length;
  const syncPolls = () => count('/api/v3/sync/status');
  async function swap(html) {
    panel.dispatchEvent(new window.CustomEvent('htmx:beforeSwap', { bubbles: true, detail: { target: panel, shouldSwap: true } }));
    panel.innerHTML = html === undefined ? partial : html;
    panel.dispatchEvent(new window.CustomEvent('htmx:afterSwap', { bubbles: true, detail: { target: panel } }));
    await tick(20);
  }
  function fire(el, type) { el.dispatchEvent(new window.Event(type, { bubbles: true })); }
  function setRole(role) { $('sync_role').value = role; fire($('sync_role'), 'change'); }

  setTab('display');
  await registry.start();
  await tick(200);

  // ── first load ──────────────────────────────────────────────────────────
  ok('one plugin-list request on start', count('/api/v3/plugins/installed') === 1, requests);
  ok('one scroll-speed hint request on start (after the debounce)', count('/api/v3/config/scroll-speed-advice') === 1, requests);
  const refreshHint = $('limit_refresh_rate_hz_hint');
  ok('a panel short of its cap says so, as text, with a button for a cap it can hold',
     /about 110 Hz, below this 120 Hz cap.*8% slower/.test(refreshHint.textContent)
     && refreshHint.querySelector('button').textContent === 'Use 100 Hz', refreshHint.textContent);
  refreshHint.querySelector('button').click();
  ok('the button fills the field and says to save and restart',
     $('limit_refresh_rate_hz').value === '100' && /Save, then restart/.test(refreshHint.textContent),
     [$('limit_refresh_rate_hz').value, refreshHint.textContent]);
  ok('the saved role is standalone: no sync request, no interval work',
     $('sync_role').value === 'standalone' && syncPolls() === 0, [$('sync_role').value, syncPolls()]);
  ok('the sync poll interval runs while the tab is on screen', intervals.size === 1
     && [...intervals.values()][0].ms === 5000, intervals.size);
  ok('the status bar is hidden for standalone', $('sync_status_bar').classList.contains('hidden'));

  // ── five swaps ──────────────────────────────────────────────────────────
  for (let i = 0; i < 5; i++) await swap();
  await tick(200);
  ok('one mounted page after five swaps', registry.list().length === 1, registry.list().length);
  ok('one sync interval, not six', intervals.size === 1, intervals.size);
  ok('one plugin-list request per swap', count('/api/v3/plugins/installed') === 6, count('/api/v3/plugins/installed'));
  ok('the Vegas order drawn once, not stacked', doc.querySelectorAll('#vegas_plugin_order .plugin-order-item').length === 2,
     doc.querySelectorAll('#vegas_plugin_order .plugin-order-item').length);
  ok('a hostile plugin name is shown as text', $('vegas_plugin_order').textContent.includes(HOSTILE)
     && !doc.querySelector('#vegas_plugin_order img') && !window.pwned);

  // ── the controls ────────────────────────────────────────────────────────
  $('brightness').value = '42';
  fire($('brightness'), 'input');
  ok('the brightness value follows the slider', $('brightness-value').textContent === '42', $('brightness-value').textContent);

  $('rows').value = '32'; $('cols').value = '64'; $('chain_length').value = '3'; $('parallel').value = '2';
  fire($('parallel'), 'input');
  ok('the resolution readout is cols x chain by rows x parallel',
     $('display-resolution-value').textContent === '192 × 64 pixels', $('display-resolution-value').textContent);
  $('orientation').value = '90';
  fire($('orientation'), 'change');
  ok('...swapped for a 90-degree orientation', $('display-resolution-value').textContent === '64 × 192 pixels',
     $('display-resolution-value').textContent);
  $('rows').value = '';
  fire($('rows'), 'input');
  ok('...and a dash while a field is empty', $('display-resolution-value').textContent === '—');

  for (const [box, settings, shown] of [['vegas_scroll_enabled', 'vegas_scroll_settings', 'block'],
                                         ['double_sided_enabled', 'double_sided_settings', 'grid']]) {
    $(box).checked = true; fire($(box), 'change');
    const on = $(settings).style.display;
    $(box).checked = false; fire($(box), 'change');
    ok(`${box} shows and hides its settings`, on === shown && $(settings).style.display === 'none',
       [on, $(settings).style.display]);
  }

  root().querySelector('[data-action="toggle-section"]').click();
  ok('the Advanced button toggles its section once', toggled.join() === 'display-section-advanced-hardware', toggled);

  // ── the scroll-speed hint ───────────────────────────────────────────────
  const hints = count('/api/v3/config/scroll-speed-advice');
  advice = rough;
  for (const v of ['36', '37', '38']) { $('vegas_scroll_speed').value = v; fire($('vegas_scroll_speed'), 'input'); }
  ok('the speed value follows the slider', $('vegas_scroll_speed_value').textContent === '38');
  await tick(250);
  ok('three quick moves make one hint request', count('/api/v3/config/scroll-speed-advice') === hints + 1,
     count('/api/v3/config/scroll-speed-advice') - hints);
  ok('...for the last speed', requests.filter(u => u.includes('advice')).pop().includes('speed=38'));
  const buttons = $('vegas_scroll_speed_hint').querySelectorAll('button');
  ok('a rough speed offers the smooth ones', buttons.length === rough.data.alternatives.length
     && /will run as/.test($('vegas_scroll_speed_hint').textContent), $('vegas_scroll_speed_hint').textContent);
  buttons[0].click();
  ok('picking one sets the slider', $('vegas_scroll_speed').value === String(Math.round(rough.data.alternatives[0].pixels_per_second))
     && $('vegas_scroll_speed_value').textContent === $('vegas_scroll_speed').value, $('vegas_scroll_speed').value);
  advice = smooth;
  await tick(250);
  ok('...and asks again', count('/api/v3/config/scroll-speed-advice') === hints + 2);
  ok('a smooth speed says so', /^Smooth on this panel/.test($('vegas_scroll_speed_hint').textContent),
     $('vegas_scroll_speed_hint').textContent);

  // ── sync: the role ──────────────────────────────────────────────────────
  setRole('leader');
  await tick(20);
  ok('choosing Leader shows the status bar', !$('sync_status_bar').classList.contains('hidden'));
  ok('...hides Position', $('setting-display-sync_follower_position').style.display === 'none');
  ok('...and asks for the status once', syncPolls() === 1, syncPolls());
  ok('...drawn as text', $('sync_status_content').textContent.includes('No follower detected'),
     $('sync_status_content').textContent);
  setRole('follower');
  await tick(20);
  ok('choosing Follower shows Position', $('setting-display-sync_follower_position').style.display === '');

  // ── sync: the poll runs only while on screen ────────────────────────────
  let polls = syncPolls();
  fireIntervals();
  await tick(20);
  ok('each interval tick polls once', syncPolls() === polls + 1, syncPolls() - polls);
  setTab('logs');
  ok('switching to another tab clears the interval', intervals.size === 0, intervals.size);
  polls = syncPolls();
  setTab('display');
  await tick(20);
  ok('switching back polls at once', syncPolls() === polls + 1 && intervals.size === 1, [syncPolls() - polls, intervals.size]);
  setHidden(true);
  ok('hiding the browser tab clears the interval', intervals.size === 0, intervals.size);
  polls = syncPolls();
  setHidden(false);
  await tick(20);
  ok('showing it polls at once', syncPolls() === polls + 1 && intervals.size === 1, [syncPolls() - polls, intervals.size]);

  // ── sync: states ────────────────────────────────────────────────────────
  async function poll() { fireIntervals(); await tick(20); return $('sync_status_content').textContent; }
  syncAnswer = { status: 'success', data: { role: 'leader', state: 'connected', peer_ip: HOSTILE, peer_chain: 2 } };
  ok('a connected follower, its address as text', (await poll()).includes('Follower connected — ' + HOSTILE)
     && !$('sync_status_content').querySelector('img'), $('sync_status_content').textContent);
  syncAnswer = { status: 'success', data: { role: 'leader', state: 'incompatible', error: 'rows differ ' + HOSTILE } };
  ok('incompatible panels show the reason as text', (await poll()).includes('incompatible')
     && !$('sync_error_detail').classList.contains('hidden') && $('sync_error_text').textContent === 'rows differ ' + HOSTILE
     && !$('sync_error_detail').querySelector('img'));
  syncAnswer = { status: 'success', data: realSync.data };
  ok('the real server\'s answer is drawn', (await poll()).length > 0, $('sync_status_content').textContent);
  syncMode = 'network';
  ok('a failed poll says unavailable', (await poll()).includes('Sync status unavailable'));
  syncAnswer = { status: 'success', data: { role: 'follower', state: 'follower', leader_ip: '10.0.0.2' } };
  syncMode = 'ok';
  ok('receiving from a leader', (await poll()).includes('Receiving from leader — 10.0.0.2'));
  syncMode = 'login';
  ok('a login redirect draws nothing', (await poll()).includes('Receiving from leader'));
  syncMode = 'ok';

  // ── standalone stops asking ─────────────────────────────────────────────
  setRole('standalone');
  polls = syncPolls();
  fireIntervals();
  await tick(20);
  ok('standalone hides the bar and the poll asks nothing',
     $('sync_status_bar').classList.contains('hidden') && syncPolls() === polls, syncPolls() - polls);

  // ── window.updateSyncUI ─────────────────────────────────────────────────
  $('sync_role').value = 'leader';
  polls = syncPolls();
  displayPage.updateSyncUI();
  await tick(20);
  ok('updateSyncUI() applies the role and asks once',
     !$('sync_status_bar').classList.contains('hidden') && syncPolls() === polls + 1, syncPolls() - polls);

  // ── swapped out ─────────────────────────────────────────────────────────
  await swap('<p>another tab</p>');
  ok('nothing left mounted', registry.list().length === 0, registry.list().length);
  ok('no interval left running', intervals.size === 0, intervals.size);
  polls = syncPolls();
  setTab('overview');
  setTab('display');
  setHidden(true);
  setHidden(false);
  await tick(20);
  ok('a swapped-out page never polls again', syncPolls() === polls && intervals.size === 0, syncPolls() - polls);
  const hintsGone = count('/api/v3/config/scroll-speed-advice');
  await swap();
  $('vegas_scroll_speed').value = '40';
  fire($('vegas_scroll_speed'), 'input');
  await swap('<p>another tab</p>');
  await tick(250);
  ok('a hint still waiting out its debounce at the swap is never asked for',
     count('/api/v3/config/scroll-speed-advice') === hintsGone, count('/api/v3/config/scroll-speed-advice') - hintsGone);

  // ── the widget loads late ───────────────────────────────────────────────
  delete window.PluginOrderList;
  const beforeLate = count('/api/v3/plugins/installed');
  await swap();
  ok('no list request while the widget is missing', count('/api/v3/plugins/installed') === beforeLate);
  window.PluginOrderList = widget;
  await tick(150);
  ok('the list starts once the widget arrives', count('/api/v3/plugins/installed') === beforeLate + 1);
  delete window.PluginOrderList;
  await swap();
  const beforeGone = count('/api/v3/plugins/installed');
  await swap('<p>another tab</p>');
  window.PluginOrderList = widget;
  await tick(250);
  ok('a page swapped away while waiting starts nothing', count('/api/v3/plugins/installed') === beforeGone);

  ok('no console errors', logged.length === 0, logged);
  ok('no DOM errors', errs.length === 0, errs);

  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
