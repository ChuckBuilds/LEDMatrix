// POST /api/v3/display/on-demand/start answers 202 with status "starting"
// when the display service has to be started first: the request is taken,
// and the web process sends it once the display listens. "Preview on
// display" (app.js) must read that as taken -- an info toast and the
// floating preview opened -- not as a failure. Runs the shipped app.js in a
// vm with a minimal fake DOM, as test_restart_banner.js does.

const fs = require('fs');
const path = require('path');
const vm = require('vm');
const V3 = path.resolve(__dirname, '../../../web_interface/static/v3');

let pass = 0, fail = 0;
const ok = (label, cond, extra) => cond
  ? (pass++, console.log('  ok   ' + label))
  : (fail++, console.log('  FAIL ' + label + (extra !== undefined ? '  ' + JSON.stringify(extra) : '')));

function load(answer) {
  const notes = [];
  const opened = [];
  const noop = () => {};
  const document = {
    body: { addEventListener: noop },
    addEventListener: noop,
    getElementById: () => null,
    querySelector: () => null,
    querySelectorAll: () => [],
  };
  const window = { addEventListener: noop, getApp: () => null };
  const context = {
    window, document, console,
    sessionStorage: { setItem: noop, removeItem: noop, getItem: () => null },
    showNotification: (m, t) => notes.push([m, t]),
    setTimeout: () => 0,
    fetch: () => Promise.resolve({ json: () => Promise.resolve(answer) }),
  };
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(path.join(V3, 'app.js'), 'utf8'), context);
  window.toggleFloatingPreview = (open) => opened.push(open);
  return { window, notes, opened };
}

async function preview(answer) {
  const t = load(answer);
  t.window.previewPluginNow('weather');
  for (let i = 0; i < 5; i++) await Promise.resolve();
  return t;
}

(async () => {
  console.log('\npreviewPluginNow');
  {
    const t = await preview({ status: 'starting', message: 'The display service is starting',
                              data: { request_id: 'r1', pending: true } });
    ok('a 202 "starting" answer is an info toast, not an error',
       t.notes.length === 1 && t.notes[0][1] === 'info', t.notes);
    ok('and the preview opens', t.opened.length === 1 && t.opened[0] === true, t.opened);
  }
  {
    const t = await preview({ status: 'success', data: { request_id: 'r1' } });
    ok('a 200 success still opens it', t.opened.length === 1 && t.notes[0][1] === 'success', t.notes);
  }
  {
    const t = await preview({ status: 'error', message: 'no display' });
    ok('an error does not', t.opened.length === 0 && t.notes[0][1] === 'error', t.notes);
  }
  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
})();
