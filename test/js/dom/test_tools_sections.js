// Real-DOM (jsdom) test of the two new Tools sections. The HTML is the actual
// server-rendered /partials/tools, and the payloads are the real API's, so a
// renamed field or a changed shape fails this rather than passing quietly.
const http = require('http');
const { JSDOM, VirtualConsole } = require('jsdom');

const BASE = process.env.BASE || 'http://localhost:5000';
const get = p => new Promise((res, rej) =>
  http.get(BASE + p, r => { let d = ''; r.on('data', c => d += c); r.on('end', () => res(d)); }).on('error', rej));

(async () => {
  const partial = await get('/partials/tools');
  const bridge = JSON.parse(await get('/api/v3/integrations/mqtt-bridge'));
  const apps = JSON.parse(await get('/api/v3/starlark/editor/apps'));

  const errs = [];
  const vc = new VirtualConsole();
  vc.on('jsdomError', e => errs.push(String(e.message || e).split('\n')[0]));

  // Controllable fetch: serve the real payloads, and let tests swap in others.
  let editorStatus = { status: 'success', data: { running: false } };
  let bridgePayload = bridge;
  let onPut = null;
  const stubFetch = (url, opts) => {
    const u = String(url);
    if (onPut && opts && opts.method === 'PUT') onPut(JSON.parse(opts.body));
    let body = { status: 'success', data: {} };
    if (u.includes('/integrations/mqtt-bridge')) body = bridgePayload;
    else if (u.includes('/starlark/editor/status')) body = editorStatus;
    else if (u.includes('/starlark/editor/apps')) body = apps;
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
  };

  // runScripts:'dangerously' so the partial's own <script> executes the way a
  // browser runs it -- function declarations land on window. Evaluating the
  // source by hand instead leaves helpers like escHtml off the global object
  // and the page fails in ways it never would in a browser.
  const dom = new JSDOM(`<!doctype html><html><body>${partial}</body></html>`,
    { runScripts: 'dangerously', virtualConsole: vc, url: BASE + '/',
      beforeParse(w) { w.fetch = stubFetch; w.confirm = () => true; } });
  const { window } = dom;

  const tick = ms => new Promise(r => setTimeout(r, ms));
  await tick(300);

  const $ = id => window.document.getElementById(id);
  let pass = 0, fail = 0;
  const ok = (l, c, x) => c ? (pass++, console.log('  ok   ' + l))
    : (fail++, console.log('  FAIL ' + l + (x !== undefined ? '  → ' + JSON.stringify(x).slice(0, 200) : '')));

  console.log('\n── Tools: MQTT bridge + Pixlet editor (real DOM) ──');

  // ── MQTT bridge ────────────────────────────────────────────────────────
  ok('bridge form rendered', !!$('mqtt-host'), $('mqtt-bridge-body').textContent.slice(0, 80));
  ok('host prefilled from the API', $('mqtt-host').value === bridge.data.config.mqtt_host,
     { got: $('mqtt-host') && $('mqtt-host').value, want: bridge.data.config.mqtt_host });
  ok('port prefilled', $('mqtt-port').value === String(bridge.data.config.mqtt_port));
  ok('log level selected', $('mqtt-log-level').value === bridge.data.config.log_level);
  ok('TLS checkbox matches', $('mqtt-tls').checked === !!bridge.data.config.mqtt_tls);
  ok('password field is EMPTY', $('mqtt-password').value === '');
  ok('password field is type=password', $('mqtt-password').type === 'password');
  ok('no password value anywhere in the DOM',
     !/s3cret|mqtt_password"\s*:\s*"/.test(window.document.body.innerHTML));
  ok('state badge rendered', ($('mqtt-bridge-state').textContent || '').trim().length > 0,
     $('mqtt-bridge-state').textContent);
  ok('not-installed shows an Install button', !!$('btn-mqtt-install'));
  ok('config path shown', $('mqtt-bridge-body').textContent.includes('bridge_config.json'));
  ok('env override hint shown', $('mqtt-bridge-body').textContent.includes('LEDMATRIX_MQTT_'));

  // The save body must omit the password when the field is blank.
  let sent = null;
  onPut = body => { sent = body; };
  window.saveMqttBridge();
  await tick(150);
  ok('save omits password when left blank', sent && !('mqtt_password' in sent), sent && Object.keys(sent));
  ok('save sends the edited fields', sent && sent.mqtt_host === bridge.data.config.mqtt_host, sent);

  $('mqtt-password').value = 'typed-secret';
  window.saveMqttBridge();
  await tick(150);
  ok('save includes password once typed', sent && sent.mqtt_password === 'typed-secret');
  onPut = null;

  // ── Pixlet editor, idle ────────────────────────────────────────────────
  const appIds = (apps.data.apps || []).map(a => a.id);
  ok('editor lists the apps on disk',
     appIds.every(id => $('pixlet-editor-body').textContent.includes(id)), appIds);
  ok('no session banner while idle', !$('pixlet-countdown'));
  // escHtml does not encode single quotes, so an app id interpolated into an
  // inline onclick="startPixletEditor('...')" could break out of the JS string
  // and run script. The id must reach the handler through dataset instead.
  const editBtns = [...window.document.querySelectorAll('[id^="btn-pixlet-edit-"]')];
  ok('edit buttons exist', editBtns.length > 0, editBtns.length);
  ok('edit buttons carry no inline onclick',
     editBtns.every(b => !b.getAttribute('onclick')),
     editBtns.map(b => b.getAttribute('onclick')));
  ok('edit buttons pass the app id via dataset',
     editBtns.every(b => appIds.includes(b.dataset.appId)),
     editBtns.map(b => b.dataset.appId));
  ok('warns that the display stops',
     window.document.body.textContent.includes('display stops while a session is open'));

  // ── Pixlet editor, running ─────────────────────────────────────────────
  editorStatus = { status: 'success', data: {
    running: true, app_id: 'test-editor-app', port: 8099, seconds_remaining: 1634,
    timeout: 1800, host_bound: '0.0.0.0' } };
  window.loadPixletEditor();
  await tick(200);

  ok('running session shows the banner', !!$('pixlet-countdown'));
  ok('countdown formatted mm:ss', $('pixlet-countdown').textContent === '27:14',
     $('pixlet-countdown') && $('pixlet-countdown').textContent);
  ok('Stop button offered', !!$('btn-pixlet-stop'));
  const link = [...window.document.querySelectorAll('#pixlet-editor-body a')].find(a => /Open the editor/.test(a.textContent));
  ok('editor link present', !!link);
  ok('link uses this host, not localhost — no tunnel needed',
     !!link && link.href.includes(window.location.hostname) && link.href.includes(':8099'),
     link && link.href);
  ok('Edit buttons disabled while a session runs',
     [...window.document.querySelectorAll('[id^="btn-pixlet-edit-"]')].every(b => b.disabled));
  ok('banner names the app being edited',
     $('pixlet-editor-body').textContent.includes('test-editor-app'));

  ok('no uncaught JS errors', errs.length === 0, errs.slice(0, 3));
  console.log(`\n${pass} passed, ${fail} failed\n`);
  process.exit(fail ? 1 : 0);
})().catch(e => { console.log('HARNESS ERROR: ' + e.stack.split('\n').slice(0, 5).join('\n')); process.exit(1); });
