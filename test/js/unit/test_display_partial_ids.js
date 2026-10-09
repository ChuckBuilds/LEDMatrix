// The Display tab's page module must only look up elements the partial
// renders.
//
// Its brightness slider handler once also wrote to #brightness-display, a
// "LED brightness: N%" line that #387 removed from partials/display.html. The
// lookup returned null, so every movement of the slider threw a TypeError.
// This imports the shipped module (static/v3/js/pages/display.js), starts it
// on a fake root that answers only for the ids the partial's markup renders
// (null for any other, as in a browser), fires every listener it registered,
// and checks that every id it asked for exists. Then it moves the slider.
//
// No jsdom and no server needed.

const fs = require('fs');
const path = require('path');
const { pathToFileURL } = require('url');

const PARTIAL = path.resolve(__dirname, '../../../web_interface/templates/v3/partials/display.html');
const JS = path.resolve(__dirname, '../../../web_interface/static/v3/js');

let pass = 0, fail = 0;
const ok = (label, cond, extra) => cond
  ? (pass++, console.log('  ok   ' + label))
  : (fail++, console.log('  FAIL ' + label + (extra !== undefined ? '  ' + JSON.stringify(extra) : '')));

const html = fs.readFileSync(PARTIAL, 'utf8');
const rendered = new Set([...html.matchAll(/\bid="([^"{}]+)"/g)].map(m => m[1]));

function fakeElement(id) {
  const listeners = {};
  const classes = new Set();
  return {
    id, value: '1', textContent: '', min: '', max: '', checked: false,
    style: {}, dataset: {}, className: '',
    classList: {
      add: c => classes.add(c), remove: c => classes.delete(c),
      contains: c => classes.has(c),
    },
    addEventListener: (type, fn) => { (listeners[type] ||= []).push(fn); },
    dispatchEvent() { return true; },
    appendChild() {},
    getAttribute: () => null,
    listeners,
  };
}

(async () => {
  console.log('\n── Display page module: element lookups ──');
  const display = await import(pathToFileURL(path.join(JS, 'pages/display.js')).href);

  const asked = new Set();
  const elements = new Map();
  const timers = [];
  const win = {
    setTimeout: fn => { timers.push(fn); return timers.length; },
    clearTimeout() {},
    URLSearchParams,
    Event: class { constructor(type) { this.type = type; } },
    PluginOrderList: { init() {} },
  };
  const doc = { defaultView: win, createElement: () => fakeElement(''), createTextNode: () => ({}) };
  const rootListeners = {};
  const root = {
    ownerDocument: doc,
    querySelector(sel) {
      const m = /^#([\w-]+)$/.exec(sel);
      if (!m) throw new Error('unexpected selector ' + sel);
      asked.add(m[1]);
      if (!rendered.has(m[1])) return null;
      if (!elements.has(m[1])) elements.set(m[1], fakeElement(m[1]));
      return elements.get(m[1]);
    },
    addEventListener: (type, fn) => { (rootListeners[type] ||= []).push(fn); },
    contains: () => true,
  };
  const never = () => new Promise(() => {});
  const polls = [];
  const ctx = {
    root, name: 'display', state: {}, signal: { aborted: false },
    api: { get: never },
    visibility: { every: (ms, fn) => { polls.push(ms); fn(); return () => {}; } },
  };

  let loadError = null;
  try { display.init(root, ctx); } catch (e) { loadError = e; }
  ok('init() runs', !loadError, loadError && String(loadError));
  ok('the sync status is polled through ctx.visibility', polls.length === 1 && polls[0] === 5000, polls);

  // Fire everything it wired, so every lookup it can make is made.
  let thrown = null;
  try {
    for (const el of elements.values()) {
      for (const fns of Object.values(el.listeners)) fns.forEach(fn => fn.call(el, { target: el }));
    }
    while (timers.length) timers.shift()();
  } catch (e) { thrown = e; }
  ok('its listeners and timers run without throwing', !thrown, thrown && String(thrown));

  const missing = [...asked].filter(id => !rendered.has(id));
  ok('it looks elements up', asked.size > 10, asked.size);
  ok('every looked-up id is rendered by the partial', missing.length === 0, missing);

  const slider = elements.get('brightness') || fakeElement('brightness');
  const handlers = slider.listeners.input || [];
  ok('the slider has an input handler', handlers.length > 0);
  slider.value = '42';
  thrown = null;
  try { handlers.forEach(fn => fn.call(slider, { target: slider })); } catch (e) { thrown = e; }
  ok('moving the slider throws nothing', !thrown, thrown && String(thrown));
  const label = elements.get('brightness-value');
  ok('...and shows the new value', !!label && label.textContent === '42', label && label.textContent);

  display.destroy(root, ctx);
  console.log(`\n${pass} passed, ${fail} failed\n`);
  process.exit(fail ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
