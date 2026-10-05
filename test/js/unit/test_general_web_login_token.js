// Creating an API token on the General tab must leave its form clean.
//
// app.js marks a form data-dirty on any input in it and clears the mark only
// after a successful htmx request; its beforeunload handler then asks "Leave
// site?" while any visible form is still dirty. The token form posts with
// fetch (createToken in static/v3/js/pages/general.js), so after a token was
// created the form stayed dirty and reloading the page while the General tab
// was open prompted about changes that had been saved.
//
// Imports the shipped page module and runs createToken with a fake fetch and
// DOM -- no jsdom and no server needed.

const path = require('path');
const { pathToFileURL } = require('url');

const JS = path.resolve(__dirname, '../../../web_interface/static/v3/js');
const load = f => import(pathToFileURL(path.join(JS, f)).href);

let pass = 0, fail = 0;
const ok = (label, cond, extra) => cond
  ? (pass++, console.log('  ok   ' + label))
  : (fail++, console.log('  FAIL ' + label + (extra !== undefined ? '  ' + JSON.stringify(extra) : '')));

function el() {
  const classes = new Set(['hidden']);
  return {
    textContent: '', dataset: {}, style: {}, className: '',
    classList: { add: c => classes.add(c), remove: c => classes.delete(c), contains: c => classes.has(c) },
    appendChild() {}, addEventListener() {}, querySelector: () => null,
  };
}

async function setup(answer) {
  const { createApi } = await load('core/api.js');
  const general = await load('pages/general.js');
  const elements = {
    '#web-login-tokens': el(),
    '#web-login-new-token-value': el(),
    '#web-login-new-token': el(),
  };
  const notes = [];
  const doc = { createElement: () => el(), defaultView: { confirm: () => true } };
  const root = { ownerDocument: doc, querySelector: sel => elements[sel] || null, querySelectorAll: () => [] };
  const fetch = () => Promise.resolve({
    ok: answer.ok, status: answer.ok ? 200 : 400,
    headers: { get: () => null },
    text: () => Promise.resolve(JSON.stringify(answer.body)),
  });
  const ctx = {
    root, state: {}, signal: { aborted: false },
    api: createApi({ fetch }),
    notify: (m, t) => notes.push([m, t]),
  };
  return { general, ctx, elements, notes };
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

(async () => {
  console.log('\n── General tab: API token form ──');

  {
    const t = await setup({ ok: true, body: {
      status: 'success', message: 'Token created',
      data: { token: 'lmx_secret', record: { id: 't1', name: 'Home Assistant', prefix: 'lmx_sec' } },
    } });
    const form = dirtyForm();
    await t.general.createToken(t.ctx, form);
    ok('the new token is shown', t.elements['#web-login-new-token-value'].textContent === 'lmx_secret');
    ok('a created token leaves the form clean (no "Leave site?" on reload)',
       !form.hasAttribute('data-dirty'));
  }

  {
    const t = await setup({ ok: false, body: { status: 'error', message: 'Name is required' } });
    const form = dirtyForm();
    await t.general.createToken(t.ctx, form);
    ok('a refused request reports the error', t.notes.some(([m, type]) => type === 'error' && /Name is required/.test(m)),
       t.notes);
    ok('...and keeps the form dirty: nothing was saved', form.hasAttribute('data-dirty'));
  }

  console.log(`\n${pass} passed, ${fail} failed\n`);
  process.exit(fail ? 1 : 0);
})().catch(e => { console.log('HARNESS ERROR: ' + e.stack); process.exit(1); });
