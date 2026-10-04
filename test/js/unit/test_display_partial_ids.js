// The Display tab's inline script must only look up elements the partial
// renders.
//
// Its brightness slider handler also wrote to #brightness-display, a "LED
// brightness: N%" line that #387 removed from partials/display.html. The
// lookup returned null, so every movement of the slider threw a TypeError.
// This checks every literal getElementById() in the partial's inline scripts
// against the ids its markup renders, and runs the shipped script in a vm
// with a fake DOM (null for an id the markup lacks, as in a browser) to move
// the slider.
//
// No jsdom and no server needed.

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const PARTIAL = path.resolve(__dirname, '../../../web_interface/templates/v3/partials/display.html');

let pass = 0, fail = 0;
const ok = (label, cond, extra) => cond
  ? (pass++, console.log('  ok   ' + label))
  : (fail++, console.log('  FAIL ' + label + (extra !== undefined ? '  ' + JSON.stringify(extra) : '')));

const html = fs.readFileSync(PARTIAL, 'utf8');
const scripts = [...html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script[^>]*>/gi)].map(m => m[1]);
const markup = html.replace(/<script\b[^>]*>[\s\S]*?<\/script[^>]*>/gi, '');
const rendered = new Set([...markup.matchAll(/\bid="([^"{}]+)"/g)].map(m => m[1]));

console.log('\n── Display partial: element lookups ──');

// 1. Static: every literal lookup names an id the partial renders.
const lookups = scripts.flatMap(s => [...s.matchAll(/getElementById\('([^']+)'\)/g)].map(m => m[1]));
const missing = [...new Set(lookups.filter(id => !rendered.has(id)))];
ok('the inline scripts look elements up', lookups.length > 0, lookups.length);
ok('every looked-up id is rendered by the partial', missing.length === 0, missing);

// 2. Behaviour: moving the brightness slider updates its label and throws nothing.
function fakeElement(id) {
  const listeners = {};
  const classes = new Set();
  return {
    id, value: '', textContent: '', min: '', max: '', checked: false,
    style: {}, dataset: {}, className: '',
    classList: {
      add: c => classes.add(c), remove: c => classes.delete(c),
      toggle: (c, on) => (on === undefined ? (classes.has(c) ? classes.delete(c) : classes.add(c)) : (on ? classes.add(c) : classes.delete(c))),
      contains: c => classes.has(c),
    },
    addEventListener: (type, fn) => { (listeners[type] ||= []).push(fn); },
    dispatchEvent() { return true; },
    appendChild() {},
    listeners,
  };
}

const main = scripts.find(s => s.includes("getElementById('brightness')"));
ok('found the script that wires the brightness slider', !!main);
if (main) {
  const elements = new Map();
  const document = {
    readyState: 'complete',
    hidden: false,
    getElementById: id => {
      if (!rendered.has(id)) return null;
      if (!elements.has(id)) elements.set(id, fakeElement(id));
      return elements.get(id);
    },
    createElement: () => fakeElement(''),
    createTextNode: () => ({}),
    addEventListener() {},
  };
  const window = {
    LEDEscape: { html: v => String(v), attr: v => String(v) },
    LEDVisibility: { onActive() {} },
  };
  const context = {
    window, document, console, URLSearchParams,
    fetch: () => new Promise(() => {}),
    setTimeout: () => 0, clearTimeout() {}, setInterval: () => 0, clearInterval() {},
  };
  vm.createContext(context);
  let loadError = null;
  try { vm.runInContext(main, context); } catch (e) { loadError = e; }
  ok('the script loads', !loadError, loadError && String(loadError));

  const slider = elements.get('brightness');
  const handlers = (slider && slider.listeners.input) || [];
  ok('the slider has an input handler', handlers.length > 0);
  let thrown = null;
  slider.value = '42';
  try { handlers.forEach(fn => fn.call(slider, { target: slider })); } catch (e) { thrown = e; }
  ok('moving the slider throws nothing', !thrown, thrown && String(thrown));
  ok('...and shows the new value', elements.get('brightness-value').textContent === '42',
     elements.get('brightness-value').textContent);
}

console.log(`\n${pass} passed, ${fail} failed\n`);
process.exit(fail ? 1 : 0);
