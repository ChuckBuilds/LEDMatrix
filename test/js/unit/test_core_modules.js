// core/api.js and core/facade.js (web_interface/static/v3/js/core/).
//
// api.js: one fetch wrapper. Resolves to the parsed JSON body; rejects with
// an ApiError for HTTP errors, {"status": "error"} bodies, unreadable bodies
// and network failures; passes an AbortError through untouched; and turns the
// optional web login's 401 + X-LEDMatrix-Login (#683) into a quiet
// `loginRequired` error, since base.html's fetch wrapper is already sending
// the browser to the login page.
//
// facade.js: window.LEDMatrix, and deprecated aliases for moved globals.
//
// Plain node: imports the shipped ES modules, no DOM needed.

const path = require('path');
const { pathToFileURL } = require('url');

const CORE = path.resolve(__dirname, '../../../web_interface/static/v3/js/core');
const load = f => import(pathToFileURL(path.join(CORE, f)).href);

let pass = 0, fail = 0;
const ok = (label, cond, extra) => cond
  ? (pass++, console.log('  ok   ' + label))
  : (fail++, console.log('  FAIL ' + label + (extra !== undefined ? '  -> ' + JSON.stringify(extra) : '')));

function response(status, body, headers = {}) {
  const text = typeof body === 'string' ? body : JSON.stringify(body);
  return {
    status, ok: status >= 200 && status < 300,
    headers: { get: n => headers[n] !== undefined ? headers[n] : null },
    text: () => Promise.resolve(text),
  };
}
async function rejection(promise) {
  try { await promise; return null; } catch (e) { return e; }
}

(async () => {
  const { createApi, ApiError, isLoginRedirect, isAbort } = await load('api.js');
  const { createFacade, installFacade, defineDeprecatedAlias, FACADE_VERSION } = await load('facade.js');
  const { createRegistry } = await load('registry.js');

  console.log('\n1. api: requests go out as JSON, through fetch at call time');
  {
    const calls = [];
    const api = createApi({ fetch: (url, init) => { calls.push([url, init]); return Promise.resolve(response(200, { status: 'success', data: { n: 1 } })); } });
    const body = await api.get('/api/v3/cache/list');
    ok('resolves to the parsed body', body.data.n === 1, body);
    ok('GET has no body', calls[0][1].method === 'GET' && calls[0][1].body === undefined);
    await api.post('/api/v3/cache/delete', { key: 'a"b' });
    ok('POST sends JSON', calls[1][1].headers['Content-Type'] === 'application/json' && JSON.parse(calls[1][1].body).key === 'a"b');
    const controller = new AbortController();
    await api.get('/api/v3/x', { signal: controller.signal });
    ok('the signal is passed to fetch', calls[2][1].signal === controller.signal);

    // Default: window.fetch looked up per call, so base.html's login wrapper
    // (installed before any module runs, or replaced later) is the one used.
    const seen = [];
    globalThis.fetch = () => { seen.push('first'); return Promise.resolve(response(200, { status: 'success' })); };
    const live = createApi();
    await live.get('/api/v3/a');
    globalThis.fetch = () => { seen.push('second'); return Promise.resolve(response(200, { status: 'success' })); };
    await live.get('/api/v3/b');
    ok('uses whatever window.fetch is at call time', seen.join() === 'first,second', seen);
  }

  console.log('\n2. api: errors');
  {
    const api = r => createApi({ fetch: () => (r instanceof Error ? Promise.reject(r) : Promise.resolve(r)) });
    let e = await rejection(api(response(500, { status: 'error', message: 'Disk full' })).get('/api/v3/x'));
    ok('HTTP error carries status and message', e instanceof ApiError && e.status === 500 && e.message === 'Disk full', e && e.message);
    e = await rejection(api(response(200, { status: 'error', message: 'Nope' })).get('/api/v3/x'));
    ok('a 200 with status "error" is an error', e instanceof ApiError && e.status === 200 && e.message === 'Nope' && e.body.status === 'error');
    e = await rejection(api(response(502, '<html>Bad gateway</html>')).get('/api/v3/x'));
    ok('a non-JSON error page says the status', e instanceof ApiError && e.status === 502 && e.message === 'HTTP 502', e && e.message);
    e = await rejection(api(response(200, 'not json')).get('/api/v3/x'));
    ok('an unreadable 200 is an error', e instanceof ApiError && /Unreadable/.test(e.message));
    e = await rejection(api(new TypeError('Failed to fetch')).get('/api/v3/x'));
    ok('a network failure is flagged', e instanceof ApiError && e.network && e.status === 0 && e.message === 'Failed to fetch');
    const abort = new Error('aborted'); abort.name = 'AbortError';
    e = await rejection(api(abort).get('/api/v3/x'));
    ok('an abort passes through untouched', e === abort && isAbort(e));
  }

  console.log('\n3. api: the optional web login (#683)');
  {
    const login = response(401, { status: 'error', message: 'Login required' }, { 'X-LEDMatrix-Login': '/login?next=/' });
    ok('isLoginRedirect matches the wrapper in base.html', isLoginRedirect(login));
    ok('...not a protocol-relative URL', !isLoginRedirect(response(401, {}, { 'X-LEDMatrix-Login': '//evil.example/' })));
    ok('...not a 401 without the header', !isLoginRedirect(response(401, {})));
    ok('...not another status', !isLoginRedirect(response(403, {}, { 'X-LEDMatrix-Login': '/login' })));
    const e = await rejection(createApi({ fetch: () => Promise.resolve(login) }).get('/api/v3/x'));
    ok('rejects quietly with loginRequired', e instanceof ApiError && e.loginRequired && e.status === 401);
  }

  console.log('\n4. api: only this server\'s paths');
  {
    const api = createApi({ fetch: () => Promise.resolve(response(200, { status: 'success' })) });
    for (const bad of ['//evil.example/x', 'https://evil.example/x', 'api/v3/x', '/a b', '/a\\b']) {
      const e = await rejection(api.get(bad));
      ok(`refuses ${JSON.stringify(bad)}`, e instanceof TypeError, e && e.message);
    }
  }

  console.log('\n5. facade: window.LEDMatrix');
  {
    const warnings = [];
    const win = { console: { warn: m => warnings.push(m), log() {}, error() {} } };
    const api = createApi({ fetch: () => Promise.resolve(response(200, { status: 'success' })) });
    const reg = createRegistry({ document: { addEventListener() {}, removeEventListener() {}, querySelectorAll: () => [] } });
    const facade = installFacade(win, createFacade(win, api, reg));
    ok('installed as window.LEDMatrix', win.LEDMatrix === facade && facade.version === FACADE_VERSION);
    ok('exposes api and pages', facade.api === api && typeof facade.pages.register === 'function' && typeof facade.pages.refresh === 'function');
    ok('is frozen', Object.isFrozen(facade) && Object.isFrozen(facade.pages));
    win.LEDEscape = { html: s => s };
    win.LEDMatrixWidgets = { get() {} };
    ok('escape and widgets read through at call time', facade.escape === win.LEDEscape && facade.widgets === win.LEDMatrixWidgets);
    const notes = [];
    win.showNotification = (m, t) => notes.push([m, t]);
    facade.notify('saved', 'success');
    win.showNotification = (m, t) => notes.push(['replaced', m, t]);
    facade.notify('again');
    ok('notify uses the current showNotification', JSON.stringify(notes) === JSON.stringify([['saved', 'success'], ['replaced', 'again', 'info']]), notes);
  }

  console.log('\n6. facade: deprecated aliases keep old globals working');
  {
    const warnings = [];
    const win = {};
    const logger = { warn: m => warnings.push(m) };
    const calls = [];
    defineDeprecatedAlias(win, 'deleteCacheFile', function(key) { calls.push([this, key]); return 'done'; }, 'the Delete buttons', logger);
    ok('a function alias forwards its arguments and result', win.deleteCacheFile('k1') === 'done' && calls[0][1] === 'k1');
    win.deleteCacheFile('k2');
    ok('warns once, naming the replacement', warnings.length === 1 && /deleteCacheFile/.test(warnings[0]) && /the Delete buttons/.test(warnings[0]), warnings);
    defineDeprecatedAlias(win, 'oldThing', { a: 1 }, 'LEDMatrix.thing', logger);
    ok('a value alias is a getter', win.oldThing.a === 1 && warnings.length === 2);
    Object.defineProperty(win, 'locked', { value: 1, configurable: false });
    ok('a non-configurable global is left alone', defineDeprecatedAlias(win, 'locked', () => 2, null, logger) === false && win.locked === 1);
  }

  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
