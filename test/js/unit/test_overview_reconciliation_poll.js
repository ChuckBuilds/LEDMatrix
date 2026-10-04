// The Overview's "Plugin Config Warning" poll must end.
//
// The banner script in partials/overview.html asks
// /api/v3/plugins/reconciliation-status every 2 s until startup reconciliation
// says it is done. The route answers done: false whenever its status file is
// missing -- reconciliation raised before writing it, or /tmp was cleaned
// under a long-running web service -- so the poll used to run every 2 s for
// as long as the page stayed open, on every tab. It now gives up after a
// bounded number of tries and runs only while the Overview is on screen
// (LEDVisibility, like the other partials' pollers).
//
// Runs the shipped inline script in a vm with fake timers, fetch and DOM --
// no jsdom and no server needed.

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const PARTIAL = path.resolve(__dirname, '../../../web_interface/templates/v3/partials/overview.html');

let pass = 0, fail = 0;
const ok = (label, cond, extra) => cond
  ? (pass++, console.log('  ok   ' + label))
  : (fail++, console.log('  FAIL ' + label + (extra !== undefined ? '  ' + JSON.stringify(extra) : '')));

function bannerScript() {
  const html = fs.readFileSync(PARTIAL, 'utf8');
  const scripts = [...html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script[^>]*>/gi)].map(m => m[1]);
  const found = scripts.find(s => s.includes('ledmatrix-recon-dismissed'));
  if (!found) throw new Error('reconciliation banner script not found in overview.html');
  return found;
}

const flush = async () => { for (let i = 0; i < 10; i++) await new Promise(r => setImmediate(r)); };

function load({ payload, visibility = true }) {
  const timers = new Map();
  let nextId = 1;
  const calls = [];
  const banner = { style: { setProperty() {} }, dataset: {} };
  const text = { textContent: '' };
  const registrations = [];
  const window = {};
  if (visibility) {
    window.LEDVisibility = {
      onActive(tab, start, stop, key) { registrations.push({ tab, start, stop, key }); start(); },
    };
  }
  const context = {
    window,
    document: {
      getElementById: id => ({ 'reconciliation-banner': banner, 'reconciliation-banner-text': text })[id] || null,
    },
    sessionStorage: { getItem: () => null, setItem() {} },
    fetch: (url) => {
      calls.push(url);
      return Promise.resolve({ json: () => Promise.resolve(payload()) });
    },
    setTimeout: (fn) => { const id = nextId++; timers.set(id, fn); return id; },
    clearTimeout: (id) => { timers.delete(id); },
  };
  vm.createContext(context);
  vm.runInContext(bannerScript(), context);
  const fireTimers = async () => {
    const due = [...timers.entries()];
    timers.clear();
    due.forEach(([, fn]) => fn());
    await flush();
  };
  return { calls, timers, registrations, banner, text, window, fireTimers };
}

(async () => {
  console.log('\n── Overview reconciliation poll ──');

  // 1. A status file that never says done: the poll stops on its own.
  {
    const t = load({ payload: () => ({ status: 'success', data: { done: false, unresolved: [] } }) });
    await flush();
    for (let i = 0; i < 200; i++) await t.fireTimers();
    ok('a status that never turns done stops being polled', t.timers.size === 0,
       { pending: t.timers.size, requests: t.calls.length });
    ok('...after a bounded number of requests (at most 30, a minute at 2 s)',
       t.calls.length > 1 && t.calls.length <= 30, t.calls.length);
  }

  // 2. Runs only while the Overview is on screen.
  {
    const t = load({ payload: () => ({ status: 'success', data: { done: false, unresolved: [] } }) });
    await flush();
    const reg = t.registrations[0];
    ok('registers with LEDVisibility for the overview tab', !!reg && reg.tab === 'overview', reg && reg.tab);
    ok('under its own key, so it does not replace another overview poller',
       !!reg && !!reg.key && reg.key !== 'overview', reg && reg.key);
    ok('first request goes out at once', t.calls.length === 1, t.calls.length);
    if (reg) {
      reg.stop();
      ok('leaving the tab cancels the pending retry', t.timers.size === 0, t.timers.size);
      for (let i = 0; i < 5; i++) await t.fireTimers();
      ok('no requests while another tab is active', t.calls.length === 1, t.calls.length);
      reg.start();
      await flush();
      ok('coming back asks again at once', t.calls.length === 2, t.calls.length);
      ok('...and keeps polling', t.timers.size === 1, t.timers.size);
    }
  }

  // 3. A finished reconciliation with findings shows the banner and stops.
  {
    let done = false;
    const t = load({ payload: () => (done
      ? { status: 'success', data: { done: true, unresolved: [{ plugin_id: 'clock', type: 'plugin_missing_on_disk' }] } }
      : { status: 'success', data: { done: false, unresolved: [] } }) });
    await flush();
    await t.fireTimers();
    done = true;
    await t.fireTimers();
    ok('the banner names the finding once reconciliation is done',
       t.text.textContent.includes('clock'), t.text.textContent);
    const before = t.calls.length;
    for (let i = 0; i < 5; i++) await t.fireTimers();
    ok('no more requests once it is done', t.calls.length === before && t.timers.size === 0,
       { before, after: t.calls.length, pending: t.timers.size });
  }

  // 4. Without LEDVisibility (base.html always has it) it still runs, bounded.
  {
    const t = load({ visibility: false, payload: () => ({ status: 'success', data: { done: false } }) });
    await flush();
    ok('runs without LEDVisibility', t.calls.length === 1, t.calls.length);
    for (let i = 0; i < 200; i++) await t.fireTimers();
    ok('...and is still bounded', t.timers.size === 0 && t.calls.length <= 30, t.calls.length);
  }

  console.log(`\n${pass} passed, ${fail} failed\n`);
  process.exit(fail ? 1 : 0);
})().catch(e => { console.log('HARNESS ERROR: ' + e.stack); process.exit(1); });
