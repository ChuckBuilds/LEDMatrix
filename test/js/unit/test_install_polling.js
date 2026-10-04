// How long the store's Install waits for a queued install, and what it says
// when it stops waiting.
//
// It polled the operation 60 times, a second apart, then reported "Install
// operation timed out" as an error and did nothing else. The server is
// allowed far longer: the plugin's dependency install alone may take 300 s
// (install_requirements_file in src/plugin_system/store_install.py), after
// a download that fetches the plugin one file at a time. So an install that
// went on to succeed was reported as failed, never enabled, and missing from
// the installed list until the page was reloaded.
//
// Runs the whole of plugins_manager.js in the sandbox; its timers ignore
// their delays, so each poll here stands for one second on a real page.

const { create } = require('../plugins_manager_sandbox');

let pass = 0, fail = 0;
const ok = (label, cond, extra) => cond
  ? (pass++, console.log('  ok   ' + label))
  : (fail++, console.log('  FAIL ' + label + (extra !== undefined ? '  -> ' + JSON.stringify(extra).slice(0, 400) : '')));

// The server's dependency-install timeout, in polls (one a second).
const DEPENDENCY_INSTALL_TIMEOUT_POLLS = 300;

function server(completesAfterPolls) {
  const state = { polls: 0, installed: [] };
  state.route = (method, url, body) => {
    if (url.startsWith('/api/v3/plugins/installed')) {
      return { json: { status: 'success', data: { plugins: state.installed.map(p => ({ ...p })) } } };
    }
    if (method === 'POST' && url === '/api/v3/plugins/install') {
      return { json: { status: 'success', message: 'queued', data: { operation_id: 'op-1' } } };
    }
    if (url === '/api/v3/plugins/operation/op-1') {
      state.polls++;
      if (completesAfterPolls === null || state.polls < completesAfterPolls) {
        return { json: { status: 'success', data: { status: 'running' } } };
      }
      state.installed = [{ id: 'clock-simple', name: 'Clock', enabled: false }];
      return { json: { status: 'success', data: { status: 'completed',
        result: { success: true, message: 'installed', plugin_id: 'clock-simple' } } } };
    }
    if (method === 'POST' && url === '/api/v3/plugins/toggle') {
      return { json: { status: 'success', message: 'enabled' } };
    }
    return { json: { status: 'success' } };
  };
  return state;
}

(async () => {
  console.log('\nan install that takes longer than a minute');
  {
    // 200 s: well inside what the server allows.
    const srv = server(200);
    const sb = create({ route: srv.route });
    sb.window.installPlugin('clock-simple');
    await sb.until(() => sb.toasts.some(t => /installed and enabled|enabling it failed|timed out|still/i.test(t.message)),
                   'the install to finish');
    await sb.settle();
    ok('is waited for until it completes', srv.polls === 200, srv.polls);
    ok('is not reported as an error', !sb.toasts.some(t => t.type === 'error'), sb.toasts);
    ok('and is enabled', sb.requests.some(r => r.url === '/api/v3/plugins/toggle' && r.body.plugin_id === 'clock-simple'),
       sb.requests.filter(r => r.method === 'POST'));
  }

  console.log('\nan install that never reports back');
  {
    const srv = server(null);
    const sb = create({ route: srv.route });
    sb.window.installPlugin('clock-simple');
    await sb.until(() => sb.toasts.length >= 3, 'the poller to give up');
    await sb.settle();
    ok(`is polled for at least the ${DEPENDENCY_INSTALL_TIMEOUT_POLLS} s dependency-install timeout`,
       srv.polls >= DEPENDENCY_INSTALL_TIMEOUT_POLLS, srv.polls);
    ok('...but not forever', srv.polls <= 1200, srv.polls);
    const lastPoll = sb.requests.map(r => r.url).lastIndexOf('/api/v3/plugins/operation/op-1');
    ok('then the installed list is reloaded, to show what actually happened',
       sb.requests.slice(lastPoll + 1).some(r => r.url === '/api/v3/plugins/installed'),
       sb.requests.slice(lastPoll + 1).map(r => r.url));
    const last = sb.toasts[sb.toasts.length - 1];
    ok('it says the install may still be running, as a warning, not a failure',
       last && last.type === 'warning' && !/fail|timed out/i.test(last.message), sb.toasts);
    ok('nothing is enabled on a guess', !sb.requests.some(r => r.url === '/api/v3/plugins/toggle'));
  }

  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
