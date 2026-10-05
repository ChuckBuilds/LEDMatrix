// The page-visibility service (static/v3/js/core/visibility.js), in a real
// DOM (jsdom) with the real window.LEDVisibility from app-shell.js and the
// real page registry, wired the way core/boot.js wires them (each mount gets
// ctx.visibility from mountContext):
//
//   * whileVisible(start, stop) runs start() only while the page's tab is the
//     active tab AND the browser tab is visible, stop() when either changes
//   * every(ms, fn) calls fn at once and then on an interval while visible;
//     no interval is left running while hidden
//   * everything a page registered stops when the page is swapped out, and a
//     page that registers after it was destroyed starts nothing
//   * registrations never replace each other (two timers on one page, two
//     pages, or a classic partial's own LEDVisibility key)
//   * without LEDVisibility, the browser tab's visibility alone decides
//
// Needs jsdom but no server.
const fs = require('fs');
const path = require('path');
const { pathToFileURL } = require('url');
const { JSDOM, VirtualConsole } = require('jsdom');

const JS = path.resolve(__dirname, '../../../web_interface/static/v3/js');
const load = f => import(pathToFileURL(path.join(JS, f)).href);
const tick = ms => new Promise(r => setTimeout(r, ms || 0));

let pass = 0, fail = 0;
const ok = (l, c, x) => c ? (pass++, console.log('  ok   ' + l))
  : (fail++, console.log('  FAIL ' + l + (x !== undefined ? '  -> ' + JSON.stringify(x).slice(0, 300) : '')));

(async () => {
  const { createRegistry } = await load('core/registry.js');
  const { createVisibility } = await load('core/visibility.js');

  console.log('\n── Page visibility service (real DOM, real LEDVisibility) ──');
  const errs = [];
  const logged = [];
  const vc = new VirtualConsole();
  vc.on('jsdomError', e => errs.push(String(e.message || e).split('\n')[0]));
  vc.on('error', (...a) => logged.push(a.join(' ')));
  const dom = new JSDOM('<!doctype html><html><body><div id="display-content"></div><div id="logs-content"></div></body></html>',
    { url: 'http://localhost/', virtualConsole: vc, runScripts: 'outside-only' });
  const { window } = dom;
  const doc = window.document;

  // The browser tab's visibility, under the test's control.
  let hidden = false;
  Object.defineProperty(doc, 'hidden', { get: () => hidden, configurable: true });
  function setHidden(value) {
    hidden = value;
    doc.dispatchEvent(new window.Event('visibilitychange'));
  }
  function setTab(tab) {
    doc.dispatchEvent(new window.CustomEvent('ledmatrix:tab-changed', { detail: { tab } }));
  }

  // Intervals, counted: the point is that none is left running.
  const intervals = new Map();
  let nextInterval = 1;
  window.setInterval = (fn, ms) => { const id = nextInterval++; intervals.set(id, { fn, ms }); return id; };
  window.clearInterval = id => { intervals.delete(id); };
  const fireIntervals = () => [...intervals.values()].forEach(i => i.fn());

  // The real LEDVisibility (app-shell.js). Alpine is absent, so the active
  // tab is the last ledmatrix:tab-changed. The SSE streams are not under
  // test: they open stand-in EventSources.
  window.getApp = () => null;
  window.EventSource = class { addEventListener() {} removeEventListener() {} close() {} };
  window.eval(fs.readFileSync(path.join(JS, 'app-shell.js'), 'utf8'));
  ok('app-shell.js defines LEDVisibility', !!(window.LEDVisibility && window.LEDVisibility.onActive));

  const visibility = createVisibility({ window });
  const registry = createRegistry({
    document: doc,
    context: {},
    mountContext: ctx => ({ visibility: visibility.forPage(ctx) }),
  });

  const log = [];
  let polls = 0;
  const handles = [];
  registry.register('display', {
    init(root, ctx) {
      handles.push(ctx.visibility);
      ctx.visibility.whileVisible(() => log.push('start'), () => log.push('stop'));
      ctx.visibility.every(5000, () => { polls++; });
    },
  });
  let otherRuns = 0;
  registry.register('logs', {
    init(root, ctx) { ctx.visibility.every(1000, () => { otherRuns++; }); },
  });

  const panel = doc.getElementById('display-content');
  async function swap(html) {
    panel.dispatchEvent(new window.CustomEvent('htmx:beforeSwap', { bubbles: true, detail: { target: panel, shouldSwap: true } }));
    panel.innerHTML = html;
    panel.dispatchEvent(new window.CustomEvent('htmx:afterSwap', { bubbles: true, detail: { target: panel } }));
    await tick(10);
  }

  // A classic partial's own registration, keyed by its tab name: the
  // service's registrations must not replace it, nor it them.
  let classic = 0;
  window.LEDVisibility.onActive('display', () => { classic++; }, () => {});

  setTab('overview');
  await registry.start();
  await swap('<div data-page="display"></div>');

  // ── mounted on another tab ──────────────────────────────────────────────
  ok('mounted while another tab is active: nothing starts', log.length === 0 && polls === 0, [log, polls]);
  ok('...and no interval runs', intervals.size === 0, intervals.size);
  ok('isVisible() is false', handles[0].isVisible() === false);
  ok('the page\'s tab is its name', handles[0].tab === 'display');

  // ── its tab comes on screen ─────────────────────────────────────────────
  setTab('display');
  ok('switching to the tab runs start()', log.join() === 'start', log);
  ok('every() calls fn at once', polls === 1, polls);
  ok('...and sets one interval at the asked period', intervals.size === 1 && [...intervals.values()][0].ms === 5000,
     [...intervals.values()].map(i => i.ms));
  ok('isVisible() is true', handles[0].isVisible() === true);
  ok('the classic registration still runs alongside', classic === 1, classic);
  fireIntervals();
  fireIntervals();
  ok('fn runs on each interval', polls === 3, polls);

  // ── the browser tab is hidden, then shown ───────────────────────────────
  setHidden(true);
  ok('hiding the browser tab runs stop()', log.join() === 'start,stop', log);
  ok('...and clears the interval', intervals.size === 0, intervals.size);
  ok('isVisible() is false while hidden', handles[0].isVisible() === false);
  setHidden(false);
  ok('showing it again runs start()', log.join() === 'start,stop,start', log);
  ok('...and fn at once, with one interval again', polls === 4 && intervals.size === 1, [polls, intervals.size]);

  // ── another tab ─────────────────────────────────────────────────────────
  setTab('logs');
  ok('switching away runs stop() and clears the interval', log.join() === 'start,stop,start,stop' && intervals.size === 0,
     [log, intervals.size]);
  setTab('display');
  ok('switching back restarts it', log.length === 5 && polls === 5 && intervals.size === 1, [log, polls]);

  // ── the partial is swapped out while on screen ──────────────────────────
  await swap('<p>no page here</p>');
  ok('a swap-out stops it', log[log.length - 1] === 'stop', log);
  ok('...and leaves no interval running', intervals.size === 0, intervals.size);
  const before = [log.length, polls];
  setTab('overview');
  setTab('display');
  setHidden(true);
  setHidden(false);
  ok('a destroyed page never starts again', log.length === before[0] && polls === before[1], [log, polls]);
  ok('the classic registration keeps running after the swap', classic === 5, classic);

  // ── five swaps, then one page ───────────────────────────────────────────
  for (let i = 0; i < 5; i++) await swap('<div data-page="display"></div>');
  ok('after five swaps one interval runs, not five', intervals.size === 1, intervals.size);
  const pollsBefore = polls;
  fireIntervals();
  ok('...and one poll per tick', polls === pollsBefore + 1, polls - pollsBefore);

  // ── two timers on one page, and an end function ─────────────────────────
  const h = handles[handles.length - 1];
  let a = 0, b = 0;
  const endA = h.every(100, () => { a++; });
  h.every(200, () => { b++; });
  ok('two more timers on one page both start', a === 1 && b === 1 && intervals.size === 3, [a, b, intervals.size]);
  endA();
  endA();
  ok('an end function stops just its own timer (twice is harmless)', intervals.size === 2, intervals.size);

  // ── a page that registers after it was destroyed ────────────────────────
  const gone = handles[handles.length - 1];
  await swap('<p>gone</p>');
  ok('the swap-out clears every timer the page had', intervals.size === 0, intervals.size);
  let late = 0;
  const end = gone.every(1000, () => { late++; });
  ok('registering after destroy starts nothing', late === 0 && intervals.size === 0 && typeof end === 'function');

  // ── a start() that throws ───────────────────────────────────────────────
  await swap('<div data-page="display"></div>');
  const h2 = handles[handles.length - 1];
  const loggedBefore = logged.length;
  let afterThrow = 0;
  h2.whileVisible(() => { throw new Error('boom'); }, () => {});
  h2.whileVisible(() => { afterThrow++; }, () => {});
  ok('a throwing start() is logged', logged.length === loggedBefore + 1 && /boom|start failed/.test(logged.join()),
     logged.slice(loggedBefore));
  ok('...and the next registration still starts', afterThrow === 1);
  await swap('<p>gone</p>');

  // ── without LEDVisibility (a page outside base.html) ────────────────────
  const bare = createVisibility({ window, tracker: () => null }).forPage({ name: 'standalone', signal: new window.AbortController().signal });
  const seen = [];
  bare.whileVisible(() => seen.push('start'), () => seen.push('stop'));
  ok('without LEDVisibility, a visible document starts at once', seen.join() === 'start', seen);
  setHidden(true);
  ok('...and hiding it stops', seen.join() === 'start,stop', seen);
  ok('...isVisible() follows the document', bare.isVisible() === false);
  setHidden(false);
  ok('...and showing it starts again', seen.join() === 'start,stop,start', seen);

  ok('registrations need functions', (() => { try { h2.whileVisible(null, null); return false; } catch { return true; } })());
  ok('every() needs a positive period', (() => { try { h2.every(0, () => {}); return false; } catch { return true; } })());
  ok('the logs page was never on screen: it never ran', otherRuns === 0, otherRuns);
  ok('no DOM errors', errs.length === 0, errs);

  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
