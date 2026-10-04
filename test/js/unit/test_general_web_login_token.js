// Creating an API token on the General tab must leave its form clean.
//
// app.js marks a form data-dirty on any input in it and clears the mark only
// after a successful htmx request; its beforeunload handler then asks "Leave
// site?" while any visible form is still dirty. The token form posts with
// fetch (window.webLogin.createToken in partials/general.html), so after a
// token was created the form stayed dirty and reloading the page while the
// General tab was open prompted about changes that had been saved.
//
// Runs the shipped inline script in a vm with a fake fetch and DOM -- no jsdom
// and no server needed.

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const PARTIAL = path.resolve(__dirname, '../../../web_interface/templates/v3/partials/general.html');

let pass = 0, fail = 0;
const ok = (label, cond, extra) => cond
  ? (pass++, console.log('  ok   ' + label))
  : (fail++, console.log('  FAIL ' + label + (extra !== undefined ? '  ' + JSON.stringify(extra) : '')));

function webLoginScript() {
  const html = fs.readFileSync(PARTIAL, 'utf8');
  const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]);
  const found = scripts.find(s => s.includes('window.webLogin = {'));
  if (!found) throw new Error('webLogin script not found in general.html');
  return found;
}

function el() {
  const classes = new Set(['hidden']);
  return {
    textContent: '', dataset: {}, style: {}, className: '',
    classList: { add: c => classes.add(c), remove: c => classes.delete(c), contains: c => classes.has(c) },
    appendChild() {}, addEventListener() {}, querySelector: () => null,
  };
}

function load(answer) {
  const elements = {
    'web-login-tokens': el(),
    'web-login-new-token-value': el(),
    'web-login-new-token': el(),
  };
  const notes = [];
  const window = { showNotification: (m, t) => notes.push([m, t]), alert() {}, confirm: () => true };
  const context = {
    window, console,
    document: {
      getElementById: id => elements[id] || null,
      createElement: () => el(),
      querySelectorAll: () => [],
    },
    fetch: () => Promise.resolve({
      ok: answer.ok, status: answer.ok ? 200 : 400,
      json: () => Promise.resolve(answer.body),
    }),
  };
  vm.createContext(context);
  vm.runInContext(webLoginScript(), context);
  return { webLogin: context.window.webLogin, elements, notes };
}

function dirtyForm() {
  const attrs = new Map([['data-dirty', '']]);
  return {
    querySelector: sel => (sel === '[name="name"]' ? { value: 'Home Assistant' } : null),
    reset() {},
    hasAttribute: name => attrs.has(name),
    setAttribute: (name, value) => attrs.set(name, String(value)),
    removeAttribute: name => attrs.delete(name),
  };
}

const flush = async () => { for (let i = 0; i < 10; i++) await new Promise(r => setImmediate(r)); };

(async () => {
  console.log('\n── General tab: API token form ──');

  {
    const t = load({ ok: true, body: {
      status: 'success', message: 'Token created',
      data: { token: 'lmx_secret', record: { id: 't1', name: 'Home Assistant', prefix: 'lmx_sec' } },
    } });
    const form = dirtyForm();
    t.webLogin.createToken(form);
    await flush();
    ok('the new token is shown', t.elements['web-login-new-token-value'].textContent === 'lmx_secret');
    ok('a created token leaves the form clean (no "Leave site?" on reload)',
       !form.hasAttribute('data-dirty'));
  }

  {
    const t = load({ ok: false, body: { status: 'error', message: 'Name is required' } });
    const form = dirtyForm();
    t.webLogin.createToken(form);
    await flush();
    ok('a refused request reports the error', t.notes.some(([m, type]) => type === 'error' && /Name is required/.test(m)),
       t.notes);
    ok('...and keeps the form dirty: nothing was saved', form.hasAttribute('data-dirty'));
  }

  console.log(`\n${pass} passed, ${fail} failed\n`);
  process.exit(fail ? 1 : 0);
})().catch(e => { console.log('HARNESS ERROR: ' + e.stack); process.exit(1); });
