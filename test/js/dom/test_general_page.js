// The General tab as a page module (static/v3/js/pages/general.js), in a real
// DOM (jsdom) with the real server-rendered partial, the real timezone
// widget and the real web-login endpoints' answer shapes. Built like
// test_cache_page.js:
//
//   * the partial ships no <script> and no inline handlers; its root is
//     data-page="general" and the Security section's forms and buttons name
//     an action
//   * the timezone picker is drawn once per swap-in, with the saved zone
//   * after five swaps, each Security action makes exactly one request
//   * a login change is a write: a swap does not cancel it, its result is
//     still reported, and nothing is drawn into the page that has gone
//   * token names reach the page as text
//   * the settings form itself is left to htmx
//   * window.webLogin's entry points still work
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
  const partial = await get('/partials/general');
  const realTokens = JSON.parse(await get('/api/v3/auth/tokens'));
  const { createRegistry } = await load('core/registry.js');
  const { createApi } = await load('core/api.js');
  const generalPage = await load('pages/general.js');

  console.log('\n── General tab: page module (real DOM) ──');
  ok('the partial ships no inline script', !/<script/i.test(partial));
  ok('the partial has no inline click or submit handlers', !/\son(click|submit|input)=/i.test(partial));
  ok('the partial root is data-page="general"', /data-page="general"/.test(partial));
  const security = /id="web-login-settings"/.test(partial);
  ok('the server renders the Security section (it has a login store)', security);
  ok('the real token list answers in the shape the section shows',
     realTokens.status === 'success' && realTokens.data && Array.isArray(realTokens.data.tokens), realTokens);
  ok('the Security forms name their action',
     /<form[^>]*data-action="set-password"/.test(partial) && /<form[^>]*data-action="create-token"/.test(partial));
  ok('the Copy button names its action', /data-action="copy-token"/.test(partial));

  const errs = [];
  const logged = [];
  const vc = new VirtualConsole();
  vc.on('jsdomError', e => errs.push(String(e.message || e).split('\n')[0]));
  vc.on('error', (...a) => logged.push(a.join(' ')));
  const dom = new JSDOM(`<!doctype html><html><body><div id="general-content">${partial}</div></body></html>`,
    { url: BASE + '/', virtualConsole: vc, runScripts: 'outside-only' });
  const { window } = dom;
  const doc = window.document;
  const panel = doc.getElementById('general-content');
  require('../led_escape').install(window);
  window.eval(fs.readFileSync(path.join(JS, 'widgets/registry.js'), 'utf8'));
  window.eval(fs.readFileSync(path.join(JS, 'widgets/timezone-selector.js'), 'utf8'));
  const widgets = window.LEDMatrixWidgets;
  ok('the widget scripts register timezone-selector', !!(widgets && widgets.get('timezone-selector')));

  let confirmAnswer = true;
  const confirms = [];
  window.confirm = m => { confirms.push(m); return confirmAnswer; };
  const reloads = [];
  window.htmx = { ajax: (method, url, opts) => reloads.push([method, url, opts.target]) };

  const HOSTILE = '<img src=x onerror="window.pwned=1">';
  let mode = 'ok';
  let nextId = 1;
  const requests = [];
  const pending = [];
  function fakeFetch(url, init) {
    requests.push({ url, method: init.method, body: init.body ? JSON.parse(init.body) : undefined });
    const respond = (status, body, headers) => Promise.resolve({
      status, ok: status >= 200 && status < 300,
      headers: { get: h => (headers || {})[h] || null },
      text: () => Promise.resolve(JSON.stringify(body)),
    });
    if (mode === 'network') return Promise.reject(new TypeError('Failed to fetch'));
    if (mode === 'login') return respond(401, { status: 'error' }, { 'X-LEDMatrix-Login': '/login' });
    if (mode === 'refuse') return respond(400, { status: 'error', message: 'Give the token a name.' });
    if (url === '/api/v3/auth/tokens' && init.method === 'POST') {
      const id = 'tok' + (nextId++);
      const name = JSON.parse(init.body).name;
      const answer = () => respond(201, {
        status: 'success', message: 'Token created. Copy it now: it is not shown again.',
        data: { token: 'lmx_' + id, record: { id, name, prefix: 'lmx_' + id.slice(0, 3), created_at: '2026-10-04T00:00:00' } },
      });
      if (mode === 'hang') return new Promise(resolve => pending.push(() => resolve(answer())));
      return answer();
    }
    if (url.startsWith('/api/v3/auth/tokens/') && init.method === 'DELETE') {
      return respond(200, { status: 'success', message: 'Token revoked.', data: { tokens: [] } });
    }
    if (url === '/api/v3/auth/password') {
      return respond(200, { status: 'success', message: 'Login is on. Other browsers now need the password.' });
    }
    return respond(404, { status: 'error', message: 'unexpected ' + url });
  }
  const notes = [];
  const registry = createRegistry({
    document: doc,
    context: { api: createApi({ fetch: fakeFetch }), notify: (m, t) => notes.push([m, t]) },
  });
  registry.register('general', generalPage);

  const $ = id => doc.getElementById(id);
  const root = () => doc.querySelector('[data-page="general"]');
  const timezoneWidgets = () => $('timezone_container').querySelectorAll('.timezone-selector-widget').length;
  const rows = () => doc.querySelectorAll('#web-login-tokens [data-token-id]');
  const calls = (method, prefix) => requests.filter(r => r.method === method && r.url.startsWith(prefix));
  const lastNote = () => notes[notes.length - 1] || [];
  function submit(form) {
    const event = new window.Event('submit', { bubbles: true, cancelable: true });
    form.dispatchEvent(event);
    return event;
  }
  const form = action => root().querySelector(`form[data-action="${action}"]`);
  async function swap(html) {
    panel.dispatchEvent(new window.CustomEvent('htmx:beforeSwap', { bubbles: true, detail: { target: panel, shouldSwap: true } }));
    panel.innerHTML = html === undefined ? partial : html;
    panel.dispatchEvent(new window.CustomEvent('htmx:afterSwap', { bubbles: true, detail: { target: panel } }));
    await tick(20);
  }

  await registry.start();
  await tick(20);

  // ── the timezone picker ─────────────────────────────────────────────────
  const savedZone = $('timezone_container').dataset.timezone;
  ok('the partial carries the saved timezone', !!savedZone, savedZone);
  ok('the timezone picker is drawn once', timezoneWidgets() === 1, timezoneWidgets());
  ok('...holding the saved zone', $('timezone_data') && $('timezone_data').value === savedZone,
     $('timezone_data') && $('timezone_data').value);
  ok('...posted as "timezone"', $('timezone_data') && $('timezone_data').name === 'timezone');

  for (let i = 0; i < 5; i++) await swap();
  ok('one mounted page after five swaps', registry.list().length === 1, registry.list().length);
  ok('the timezone picker is drawn once, not stacked', timezoneWidgets() === 1, timezoneWidgets());

  // ── the settings form is htmx's ─────────────────────────────────────────
  const settings = root().querySelector('form[hx-post="/api/v3/config/main"]');
  ok('submitting the settings form is not prevented', settings && !submit(settings).defaultPrevented);
  ok('...and makes no request of the page\'s own', requests.length === 0, requests.length);

  if (security) {
    // ── create a token ────────────────────────────────────────────────────
    const before = rows().length;
    const create = form('create-token');
    create.querySelector('[name="name"]').value = HOSTILE;
    create.setAttribute('data-dirty', '');
    ok('Create token is handled by the page', submit(create).defaultPrevented);
    await tick(20);
    ok('one POST to /api/v3/auth/tokens', calls('POST', '/api/v3/auth/tokens').length === 1, requests);
    ok('...with the name typed', calls('POST', '/api/v3/auth/tokens')[0].body.name === HOSTILE);
    ok('a row is added', rows().length === before + 1, rows().length);
    ok('the hostile token name is shown as text', root().querySelector('#web-login-tokens').textContent.includes(HOSTILE));
    ok('...and created no element', !root().querySelector('#web-login-tokens img') && !window.pwned);
    ok('the "No tokens yet" line is gone', !root().querySelector('#web-login-tokens [data-empty]'));
    ok('the token is shown once', $('web-login-new-token-value').textContent === 'lmx_tok1'
       && !$('web-login-new-token').classList.contains('hidden'));
    ok('the form is clean again (no "Leave site?")', !create.hasAttribute('data-dirty'));
    ok('one success notification', lastNote()[1] === 'success' && /Token created/.test(lastNote()[0]), notes);

    // ── copy it (plain http: not a secure context, so it is selected) ────
    root().querySelector('button[data-action="copy-token"]').click();
    ok('Copy selects the token where the clipboard API is unavailable',
       window.getSelection().toString() === 'lmx_tok1' && /Selected/.test(lastNote()[0]), lastNote());

    // ── revoke it (the row drawn by the page, so delegation covers it) ───
    confirmAnswer = false;
    const added = rows()[rows().length - 1];
    added.querySelector('button[data-action="revoke-token"]').click();
    await tick(20);
    ok('a cancelled Revoke sends nothing', calls('DELETE', '/api/v3/auth/tokens/').length === 0);
    ok('...after asking with the token name as written', confirms.length === 1 && confirms[0].includes(HOSTILE), confirms);
    confirmAnswer = true;
    added.querySelector('button[data-action="revoke-token"]').click();
    await tick(20);
    ok('Revoke sends one DELETE for that token',
       calls('DELETE', '/api/v3/auth/tokens/').length === 1 && calls('DELETE', '/api/v3/auth/tokens/')[0].url === '/api/v3/auth/tokens/tok1',
       calls('DELETE', '/api/v3/auth/tokens/'));
    ok('...and removes its row', rows().length === before, rows().length);

    // ── the password ──────────────────────────────────────────────────────
    const pw = form('set-password');
    pw.querySelector('[name="new_password"]').value = 'correct horse battery';
    pw.querySelector('[name="confirm_password"]').value = 'correct horse batterY';
    submit(pw);
    await tick(20);
    ok('mismatched passwords are never sent', calls('POST', '/api/v3/auth/password').length === 0);
    ok('...and say so', lastNote()[1] === 'error' && /do not match/.test(lastNote()[0]), lastNote());
    pw.querySelector('[name="confirm_password"]').value = 'correct horse battery';
    submit(pw);
    await tick(20);
    const sent = calls('POST', '/api/v3/auth/password');
    ok('a matching password is sent once', sent.length === 1, sent.length);
    ok('...with the current password only when the form has one',
       sent[0] && sent[0].body.new_password === 'correct horse battery'
         && (('current_password' in sent[0].body) === !!pw.querySelector('[name="current_password"]')), sent[0]);
    ok('...and the section is reloaded once', reloads.length === 1 && reloads[0][1] === '/v3/partials/general'
       && reloads[0][2] === '#general-content', reloads);

    // ── refused, network failure, login redirect ──────────────────────────
    mode = 'refuse';
    submit(form('create-token'));
    await tick(20);
    ok('a refused request shows the server message', lastNote()[1] === 'error' && lastNote()[0] === 'Give the token a name.', lastNote());
    mode = 'network';
    submit(form('create-token'));
    await tick(20);
    ok('a network failure says the request failed', lastNote()[1] === 'error' && /^Request failed: /.test(lastNote()[0]), lastNote());
    mode = 'login';
    const quiet = notes.length;
    submit(form('create-token'));
    await tick(20);
    ok('the login redirect shows nothing (the page is leaving)', notes.length === quiet, notes.slice(quiet));

    // ── a write survives a swap ───────────────────────────────────────────
    mode = 'hang';
    await swap();
    const rowsBefore = rows().length;
    form('create-token').querySelector('[name="name"]').value = 'Late';
    submit(form('create-token'));
    await tick(5);
    await swap();
    pending.shift()();
    await tick(20);
    ok('a token created before a swap is still reported', lastNote()[1] === 'success', lastNote());
    ok('...and draws nothing into the new page', rows().length === rowsBefore
       && $('web-login-new-token').classList.contains('hidden'), rows().length);
    mode = 'ok';

    // ── window.webLogin ────────────────────────────────────────────────────
    const viaAlias = calls('POST', '/api/v3/auth/tokens').length;
    form('create-token').querySelector('[name="name"]').value = 'Alias';
    await generalPage.webLogin.createToken(form('create-token'));
    ok('webLogin.createToken(form) creates one token', calls('POST', '/api/v3/auth/tokens').length === viaAlias + 1);
    ok('webLogin has the five old methods',
       ['setPassword', 'disable', 'createToken', 'copyToken', 'revoke'].every(m => typeof generalPage.webLogin[m] === 'function'));
  }

  // ── the widget loads late ───────────────────────────────────────────────
  delete window.LEDMatrixWidgets;
  await swap();
  ok('nothing drawn while the widget is missing', timezoneWidgets() === 0, timezoneWidgets());
  window.LEDMatrixWidgets = widgets;
  await tick(150);
  ok('drawn once the widget arrives', timezoneWidgets() === 1, timezoneWidgets());

  delete window.LEDMatrixWidgets;
  await swap();
  const kept = root();
  await swap('<p>another tab</p>');
  window.LEDMatrixWidgets = widgets;
  await tick(250);
  ok('a page swapped away while waiting draws nothing', kept.querySelectorAll('.timezone-selector-widget').length === 0);
  ok('nothing left mounted', registry.list().length === 0, registry.list().length);

  ok('no console errors', logged.length === 0, logged);
  ok('no DOM errors', errs.length === 0, errs);

  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
