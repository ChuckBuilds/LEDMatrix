// The "restart the display" banner is raised by the server's answer, not by
// which URL was called.
//
// app.js used to show it after any successful POST to /api/v3/config/main and
// nothing else. A plugin update, install or uninstall that the running display
// cannot pick up live now answers `restart_required: true` (with the banner's
// wording in `restart_message`), and so does a main-config save. The htmx
// after-request handler and window.noteRestartRequired must both follow the
// flag. Runs the shipped app.js in a vm with a minimal fake DOM -- no jsdom and
// no server needed, so it runs under test/test_js_unit_suites.py too.

const fs = require('fs');
const path = require('path');
const vm = require('vm');
const V3 = path.resolve(__dirname, '../../../web_interface/static/v3');

let pass = 0, fail = 0;
const ok = (label, cond, extra) => cond
  ? (pass++, console.log('  ok   ' + label))
  : (fail++, console.log('  FAIL ' + label + (extra !== undefined ? '  ' + JSON.stringify(extra) : '')));

function load() {
  const handlers = {};
  const listen = (target) => (type, fn) => { (handlers[target + ':' + type] ||= []).push(fn); };
  const banner = { style: { display: 'none' } };
  const text = { dataset: {}, textContent: '  Configuration saved — restart the display to apply the changes  ' };
  const store = {};
  const document = {
    body: { addEventListener: listen('body') },
    addEventListener: listen('document'),
    getElementById: (id) => ({ 'restart-pending-banner': banner, 'restart-pending-text': text })[id] || null,
    querySelector: () => null,
    querySelectorAll: () => [],
  };
  const window = {
    addEventListener: listen('window'),
    getApp: () => null,
  };
  const context = {
    window, document, console,
    sessionStorage: {
      setItem: (k, v) => { store[k] = String(v); },
      removeItem: (k) => { delete store[k]; },
      getItem: (k) => (k in store ? store[k] : null),
    },
    showNotification: () => {},
    setTimeout: () => 0,
  };
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(path.join(V3, 'app.js'), 'utf8'), context);
  const afterRequest = (handlers['body:htmx:afterRequest'] || [])[0];
  const fire = ({ status = 200, body, path: reqPath = '/api/v3/anything', reportsItself = false }) => {
    const elt = { closest: () => (reportsItself ? {} : null) };
    afterRequest({
      target: { closest: () => null },
      detail: {
        xhr: { status, responseText: body === undefined ? '' : (typeof body === 'string' ? body : JSON.stringify(body)) },
        elt,
        requestConfig: { verb: 'post', path: reqPath },
      },
    });
  };
  return { window, banner, text, store, fire, afterRequest };
}

console.log('\nwindow.noteRestartRequired');
{
  const t = load();
  ok('app.js defines it', typeof t.window.noteRestartRequired === 'function');
  ok('no flag, no banner', t.window.noteRestartRequired({ status: 'success' }) === false
     && t.banner.style.display === 'none');
  ok('a missing body is ignored', t.window.noteRestartRequired(null) === false);
  ok('restart_required: false is not a request',
     t.window.noteRestartRequired({ restart_required: false }) === false && t.banner.style.display === 'none');
  ok('restart_required: true shows the banner',
     t.window.noteRestartRequired({ restart_required: true }) === true && t.banner.style.display === 'block');
  ok('without a message the template wording stays',
     t.text.textContent === 'Configuration saved — restart the display to apply the changes', t.text.textContent);
  t.window.noteRestartRequired({ restart_required: true, restart_message: 'Plugin updated — restart the display to run the new version' });
  ok('restart_message becomes the wording',
     t.text.textContent === 'Plugin updated — restart the display to run the new version', t.text.textContent);
  ok('and survives a reload with the flag', t.store['ledmatrix-restart-pending'] === '1'
     && t.store['ledmatrix-restart-pending-text'] === 'Plugin updated — restart the display to run the new version');
}

console.log('\nhtmx after-request follows the flag, not the URL');
{
  const t = load();
  ok('the handler is registered', typeof t.afterRequest === 'function');
  t.fire({ path: '/api/v3/config/main', body: { status: 'success', message: 'Configuration saved successfully' } });
  ok('a /config/main answer without the flag raises nothing', t.banner.style.display === 'none');
  t.fire({ path: '/api/v3/plugins/update', status: 500, body: { status: 'error', restart_required: true } });
  ok('an error answer never raises it', t.banner.style.display === 'none');
  t.fire({ path: '/api/v3/whatever', body: '<html>not json' });
  ok('a non-JSON answer is ignored', t.banner.style.display === 'none');
  // The main-config forms report their own result (hx-on after-request), so
  // the flag must be read even when the toast is not this handler's to show.
  t.fire({ path: '/api/v3/config/main', reportsItself: true,
           body: { status: 'success', message: 'Configuration saved successfully', restart_required: true } });
  ok('a flagged answer raises it, even from a form that reports itself', t.banner.style.display === 'block');
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
