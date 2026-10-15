// The General tab's "Start Display on Boot" checkbox (setDisplayAutostart in
// static/v3/js/pages/general.js) posts enable_autostart / disable_autostart
// to /api/v3/system/action and puts the box back when that is refused --
// including the route's HTTP-200 {status: "error"} answer for a failed
// systemctl (e.g. the sudo rule is missing).
//
// Fake fetch, no jsdom and no server needed.

const path = require('path');
const { pathToFileURL } = require('url');

const JS = path.resolve(__dirname, '../../../web_interface/static/v3/js');
const load = f => import(pathToFileURL(path.join(JS, f)).href);

let pass = 0, fail = 0;
const ok = (label, cond, extra) => cond
  ? (pass++, console.log('  ok   ' + label))
  : (fail++, console.log('  FAIL ' + label + (extra !== undefined ? '  ' + JSON.stringify(extra) : '')));

async function run(checked, answer) {
  const { createApi } = await load('core/api.js');
  const general = await load('pages/general.js');
  const sent = [];
  const notes = [];
  const fetch = (url, init) => {
    sent.push([url, JSON.parse(init.body)]);
    if (answer === 'network') return Promise.reject(new TypeError('Failed to fetch'));
    return Promise.resolve({
      ok: answer.ok, status: answer.ok ? 200 : 500,
      headers: { get: () => null },
      text: () => Promise.resolve(JSON.stringify(answer.body)),
    });
  };
  const ctx = { root: {}, state: {}, signal: { aborted: false },
                api: createApi({ fetch }), notify: (m, t) => notes.push([m, t]) };
  const box = { checked, disabled: false };
  await general.setDisplayAutostart(ctx, box);
  return { box, sent, notes };
}

(async () => {
  console.log('\n── General tab: Start Display on Boot ──');

  {
    const t = await run(false, { ok: true, body: { status: 'success', message: 'ok' } });
    ok('unchecking posts disable_autostart',
       t.sent.length === 1 && t.sent[0][0] === '/api/v3/system/action' && t.sent[0][1].action === 'disable_autostart', t.sent);
    ok('...stays unchecked and re-enabled', t.box.checked === false && t.box.disabled === false);
    ok('...and reports success', t.notes.some(([, type]) => type === 'success'), t.notes);
  }

  {
    const t = await run(true, { ok: true, body: { status: 'success' } });
    ok('checking posts enable_autostart', t.sent[0][1].action === 'enable_autostart', t.sent);
  }

  {
    const t = await run(false, { ok: true, body: { status: 'error', message: 'sudo: a password is required' } });
    ok('a failed systemctl (HTTP 200, status error) puts the box back', t.box.checked === true && !t.box.disabled);
    ok('...and shows the route\'s message',
       t.notes.some(([m, type]) => type === 'error' && /sudo/.test(m)), t.notes);
  }

  {
    const t = await run(true, { ok: false, body: { status: 'error', message: 'boom' } });
    ok('an HTTP error puts the box back', t.box.checked === false && !t.box.disabled);
  }

  {
    const t = await run(true, 'network');
    ok('a network failure puts the box back', t.box.checked === false && !t.box.disabled);
    ok('...and reports it', t.notes.some(([, type]) => type === 'error'), t.notes);
  }

  console.log(`\n${pass} passed, ${fail} failed\n`);
  process.exit(fail ? 1 : 0);
})().catch(e => { console.log('HARNESS ERROR: ' + e.stack); process.exit(1); });
