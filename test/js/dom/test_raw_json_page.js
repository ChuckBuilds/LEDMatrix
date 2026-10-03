// The Config Editor tab as a page module (static/v3/js/pages/raw-json.js), in
// a real DOM (jsdom) with the real server-rendered partial and the real
// endpoints' answers. Built like test_cache_page.js:
//
//   * the partial ships no <script> and no onclick; its root is
//     data-page="raw-json" and its buttons name an action and an editor
//   * after five swaps, each Save makes exactly one request and each
//     Format / Validate acts once
//   * invalid JSON is never sent, and the parser's message is shown as text
//   * a save is a write: a swap does not cancel it, and its result is still
//     reported
//   * the old globals' entry points still work
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
  const partial = await get('/partials/raw-json');
  const { createRegistry } = await load('core/registry.js');
  const { createApi } = await load('core/api.js');
  const rawPage = await load('pages/raw-json.js');

  console.log('\n── Config Editor tab: page module (real DOM) ──');
  ok('the partial ships no inline script', !/<script/i.test(partial));
  ok('the partial has no inline handlers', !/onclick=/i.test(partial));
  ok('the partial root is data-page="raw-json"', /data-page="raw-json"/.test(partial));
  ok('six buttons name an action and an editor',
     (partial.match(/data-action="(format|validate|save)" data-editor="(main|secrets)"/g) || []).length === 6);

  const errs = [];
  const vc = new VirtualConsole();
  vc.on('jsdomError', e => errs.push(String(e.message || e).split('\n')[0]));
  vc.on('error', (...a) => errs.push(a.join(' ')));
  const dom = new JSDOM(`<!doctype html><html><body><div id="config-editor-content">${partial}</div></body></html>`,
    { url: BASE + '/', virtualConsole: vc });
  const { window } = dom;
  const doc = window.document;
  const panel = doc.getElementById('config-editor-content');

  let mode = 'ok';
  const requests = [];
  const pending = [];
  function fakeFetch(url, init) {
    requests.push({ url, method: init.method, body: init.body, signal: init.signal });
    const respond = (status, body) => Promise.resolve({
      status, ok: status >= 200 && status < 300,
      headers: { get: () => null },
      text: () => Promise.resolve(JSON.stringify(body)),
    });
    if (mode === 'network') return Promise.reject(new TypeError('Failed to fetch'));
    if (mode === 'error') return respond(400, { status: 'error', message: 'Configuration must be a JSON object' });
    if (mode === 'hang') return new Promise(resolve => pending.push(() => resolve(respond(200, { status: 'success' }))));
    return respond(200, { status: 'success', message: 'Main configuration saved successfully' });
  }
  const notes = [];
  const registry = createRegistry({
    document: doc,
    context: { api: createApi({ fetch: fakeFetch }), notify: (m, t) => notes.push([m, t]) },
  });
  registry.register('raw-json', rawPage);

  const $ = id => doc.getElementById(id);
  const button = (action, editor) => doc.querySelector(`button[data-action="${action}"][data-editor="${editor}"]`);
  const posts = url => requests.filter(r => r.url === url && r.method === 'POST');
  async function swap() {
    panel.dispatchEvent(new window.CustomEvent('htmx:beforeSwap', { bubbles: true, detail: { target: panel, shouldSwap: true } }));
    panel.innerHTML = partial;
    panel.dispatchEvent(new window.CustomEvent('htmx:afterSwap', { bubbles: true, detail: { target: panel } }));
    await tick(20);
  }
  function type(id, text) {
    $(id).value = text;
    $(id).dispatchEvent(new window.Event('input', { bubbles: true }));
  }

  await registry.start();
  await tick(20);

  // ── first load ──────────────────────────────────────────────────────────
  ok('the real files parse', (() => { try { JSON.parse($('main-config-editor').value); JSON.parse($('secrets-config-editor').value); return true; } catch (e) { return false; } })());
  ok('both editors are validated on start',
     $('main-config-validation').textContent.trim() === 'Valid JSON' && $('secrets-config-validation').textContent.trim() === 'Valid JSON',
     [$('main-config-validation').textContent, $('secrets-config-validation').textContent]);
  ok('nothing is requested on start', requests.length === 0, requests.length);

  // ── repeated swaps ──────────────────────────────────────────────────────
  const oldSave = button('save', 'main');
  for (let i = 0; i < 5; i++) await swap();
  ok('one mounted page after five swaps', registry.list().length === 1, registry.list().length);

  type('main-config-editor', '{"display": {"brightness": 50}, "note": "a\\"b"}');
  button('save', 'main').click();
  await tick(20);
  ok('Save makes exactly one request (no duplicate listeners)', posts('/api/v3/config/raw/main').length === 1,
     posts('/api/v3/config/raw/main').length);
  const sent = posts('/api/v3/config/raw/main')[0];
  ok('it posts the parsed file as JSON', sent && JSON.parse(sent.body).note === 'a"b' && JSON.parse(sent.body).display.brightness === 50);
  ok('success is reported once', notes.length === 1 && notes[0][0] === 'config.json saved successfully!' && notes[0][1] === 'success', notes);
  oldSave.click();
  await tick(20);
  ok('a swapped-out button does nothing', posts('/api/v3/config/raw/main').length === 1);

  button('save', 'secrets').click();
  await tick(20);
  ok('Save on the secrets editor posts to the secrets endpoint, once', posts('/api/v3/config/raw/secrets').length === 1);
  ok('...and names that file', notes[1] && notes[1][0] === 'config_secrets.json saved successfully!', notes);

  // ── format and validate ─────────────────────────────────────────────────
  notes.length = 0;
  type('main-config-editor', '{"a":1,"b":[1,2]}');
  button('format', 'main').click();
  ok('Format re-indents by four spaces', $('main-config-editor').value === '{\n    "a": 1,\n    "b": [\n        1,\n        2\n    ]\n}',
     $('main-config-editor').value);
  ok('...and says so once', notes.length === 1 && notes[0][0] === 'JSON formatted successfully!', notes);
  button('validate', 'main').click();
  ok('Validate shows the detailed box', $('main-config-validation').textContent.includes('JSON is valid!'));
  ok('...and says so once', notes.length === 2 && notes[1][0] === 'JSON validation successful!', notes);

  // ── invalid JSON ────────────────────────────────────────────────────────
  const HOSTILE = '{"x": <img src=x onerror="window.pwned=1">';
  type('main-config-editor', HOSTILE);
  ok('typing re-validates', /^Invalid JSON: /.test($('main-config-validation').textContent.trim()),
     $('main-config-validation').textContent);
  ok('the parser message is text, not markup', !doc.querySelector('#main-config-validation img') && !window.pwned);
  notes.length = 0;
  const postsBefore = requests.length;
  button('save', 'main').click();
  await tick(20);
  ok('invalid JSON is not sent', requests.length === postsBefore);
  ok('...and the user is told', notes.length === 1 && notes[0][0] === 'Invalid JSON! Please fix errors before saving.', notes);
  button('format', 'main').click();
  ok('Format refuses invalid JSON', $('main-config-editor').value === HOSTILE && /^Cannot format invalid JSON/.test(notes[1][0]), notes);
  button('validate', 'main').click();
  ok('Validate shows the error box', $('main-config-validation').textContent.includes('Invalid JSON syntax')
     && !doc.querySelector('#main-config-validation img'));

  // ── server answers ──────────────────────────────────────────────────────
  type('main-config-editor', '{"a": 1}');
  notes.length = 0;
  mode = 'error';
  button('save', 'main').click(); await tick(20);
  ok('a refused save shows the server\'s message', notes[0] && notes[0][0] === 'Error saving config.json: Configuration must be a JSON object', notes);
  mode = 'network';
  button('save', 'main').click(); await tick(20);
  ok('a network failure says so', notes[1] && notes[1][0] === 'Error saving config.json: Failed to fetch', notes);

  // ── a save is a write: a swap does not cancel it ────────────────────────
  mode = 'hang';
  notes.length = 0;
  button('save', 'main').click(); await tick(5);
  const inFlight = requests[requests.length - 1];
  ok('the save carries no page signal', inFlight.signal === undefined);
  mode = 'ok';
  await swap();
  pending.forEach(resolve => resolve());
  await tick(20);
  ok('its result is still reported after the swap', notes.length === 1 && notes[0][1] === 'success', notes);

  // ── the old globals ─────────────────────────────────────────────────────
  type('main-config-editor', '[1,2]');
  ok('validateJSON(editorId) answers synchronously', rawPage.validateJSON('main-config-editor') === true);
  type('main-config-editor', '[1,');
  ok('...false for invalid JSON', rawPage.validateJSON('main-config-editor') === false);
  type('secrets-config-editor', '{"k":"v"}');
  rawPage.formatJson('secrets-config-editor', 'secrets-config-validation');
  ok('formatJson(editorId, validationId) formats that editor', $('secrets-config-editor').value === '{\n    "k": "v"\n}');
  const before = posts('/api/v3/config/raw/secrets').length;
  await rawPage.saveSecretsConfig();
  ok('saveSecretsConfig() saves once', posts('/api/v3/config/raw/secrets').length === before + 1);

  ok('no DOM errors', errs.length === 0, errs);

  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
