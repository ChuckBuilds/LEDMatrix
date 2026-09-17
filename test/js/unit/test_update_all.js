// "Check & Update All" -- which ids it sends to POST /api/v3/plugins/update.
//
// Pins two bugs seen on a real device (core 3.4.0):
//
//  1. /plugins/installed lists installed Starlark apps as virtual
//     `starlark:<app_id>` entries. Update-all sent those to the plugin
//     updater, which answered 500 "plugin not found" for each one.
//
//  2. A web-service restart landed mid-run. The request in flight died with
//     the old process and the next one (stock-news) was refused while the
//     service was coming back; both were recorded as failures and never sent
//     again, so stock-news -- installed, disabled, with an update waiting --
//     was silently not updated.
//
// Runs the shipped install_manager.js (it exports itself under node) against
// a fake PluginAPI. The installed list is the device's, trimmed.

const path = require('path');
const V3 = path.resolve(__dirname, '../../../web_interface/static/v3');

let pass = 0, fail = 0;
const ok = (label, cond, extra) => cond
  ? (pass++, console.log('  ok   ' + label))
  : (fail++, console.log('  FAIL ' + label + (extra !== undefined ? '  ' + JSON.stringify(extra) : '')));

global.window = {};
const Manager = require(path.join(V3, 'js/plugins/install_manager.js'));

const INSTALLED = [
  { id: 'ledmatrix-flights', enabled: true, version: '1.14.0' },
  { id: 'stock-news', enabled: false, version: '2.6.2', latest_version: '2.8.0', update_available: true },
  { id: 'static-image', enabled: false, version: '1.1.3' },
  { id: 'starlark-apps', enabled: false, version: '1.0.0' },   // a real plugin -- keep it
  { id: 'pomodoro-timer', enabled: false, version: '1.3.6' },
  { id: 'starlark:analogtime', enabled: false, version: 'starlark', is_starlark_app: true },
  { id: 'starlark:analogclock', enabled: true, version: 'starlark', is_starlark_app: true },
];
const EXPECTED = ['ledmatrix-flights', 'stock-news', 'static-image', 'starlark-apps', 'pomodoro-timer'];

const netErr = () => ({ error_code: 'NETWORK_ERROR', message: 'Failed to fetch' });

function fakeApi(behaviour = {}) {
  const calls = [];
  return {
    calls,
    updatePlugin: async (id) => {
      calls.push(id);
      const b = behaviour[id];
      if (typeof b === 'function') return b(calls.filter(c => c === id).length);
      return { status: 'success', message: `Plugin ${id} updated successfully` };
    },
  };
}

function setup(api, { stateList, windowList } = {}) {
  global.window = {
    PluginAPI: api,
    installedPlugins: windowList,
    PluginStateManager: stateList === undefined ? undefined : {
      installedPlugins: stateList,
      loadInstalledPlugins: async () => stateList,
    },
  };
}

const noSleep = { sleep: async () => {} };

(async () => {
  console.log('\nselection');
  ok('starlark app entries are not updatable',
     !Manager.isUpdatablePlugin(INSTALLED[5]) && !Manager.isUpdatablePlugin(INSTALLED[6]));
  ok('a starlark: id without the flag is still excluded',
     !Manager.isUpdatablePlugin({ id: 'starlark:foo' }));
  ok('the starlark-apps plugin itself is updatable', Manager.isUpdatablePlugin(INSTALLED[3]));
  ok('a disabled plugin with an update is updatable', Manager.isUpdatablePlugin(INSTALLED[1]));
  ok('entries without a usable id are skipped',
     !Manager.isUpdatablePlugin({}) && !Manager.isUpdatablePlugin(null) && !Manager.isUpdatablePlugin({ id: '' }));
  const sel = Manager.updatablePlugins(INSTALLED).map(p => p.id);
  ok('updatablePlugins keeps list order and drops only starlark apps',
     JSON.stringify(sel) === JSON.stringify(EXPECTED), sel);

  console.log('\nupdateAll sends only plugin ids');
  {
    const api = fakeApi();
    setup(api, { windowList: INSTALLED });
    const progress = [];
    const results = await Manager.updateAll((i, n, id) => progress.push([i, n, id]), noSleep);
    ok('POSTs exactly the non-starlark ids, in order',
       JSON.stringify(api.calls) === JSON.stringify(EXPECTED), api.calls);
    ok('no starlark: id reached the plugin updater', !api.calls.some(id => id.startsWith('starlark:')));
    ok('one result per plugin sent', results.length === EXPECTED.length, results.length);
    ok('progress total counts only what is sent',
       progress.length === EXPECTED.length && progress.every(([, n]) => n === EXPECTED.length), progress);
  }
  {
    const api = fakeApi();
    setup(api, { stateList: INSTALLED, windowList: [] });
    await Manager.updateAll(null, noSleep);
    ok('the PluginStateManager list is filtered the same way',
       JSON.stringify(api.calls) === JSON.stringify(EXPECTED), api.calls);
  }
  {
    const api = fakeApi();
    setup(api, { windowList: INSTALLED.filter(p => p.is_starlark_app) });
    const results = await Manager.updateAll(null, noSleep);
    ok('a list of only starlark apps sends nothing', api.calls.length === 0 && results.length === 0);
  }

  console.log('\nweb service restarting mid-run (the stock-news case)');
  {
    // ledmatrix-flights is in flight when the service stops (reset); stock-news
    // is refused twice while it comes back; then everything answers.
    const api = fakeApi({
      'ledmatrix-flights': (n) => { if (n === 1) throw netErr(); return { status: 'success', message: 'ok' }; },
      'stock-news': (n) => { if (n <= 2) throw netErr(); return { status: 'success', message: 'Plugin stock-news updated successfully' }; },
    });
    setup(api, { windowList: INSTALLED });
    const slept = [];
    const results = await Manager.updateAll(null, { sleep: async ms => { slept.push(ms); }, retryDelaysMs: [5, 10, 20] });
    const byId = Object.fromEntries(results.map(r => [r.pluginId, r]));
    ok('stock-news is sent again until the server answers',
       api.calls.filter(id => id === 'stock-news').length === 3, api.calls);
    ok('stock-news ends up updated, not skipped', byId['stock-news'] && byId['stock-news'].success === true, byId['stock-news']);
    ok('the request lost with the old process is re-sent too',
       byId['ledmatrix-flights'] && byId['ledmatrix-flights'].success === true);
    ok('it backs off between attempts', JSON.stringify(slept) === JSON.stringify([5, 5, 10]), slept);
    ok('every plugin still gets exactly one result',
       JSON.stringify(results.map(r => r.pluginId)) === JSON.stringify(EXPECTED), results.map(r => r.pluginId));
  }
  {
    const api = fakeApi({ 'stock-news': () => { throw netErr(); } });
    setup(api, { windowList: INSTALLED });
    const results = await Manager.updateAll(null, { sleep: async () => {}, retryDelaysMs: [1, 1] });
    const r = results.find(x => x.pluginId === 'stock-news');
    ok('a server that never comes back is retried a bounded number of times',
       api.calls.filter(id => id === 'stock-news').length === 3, api.calls);
    ok('...then reported as a failure, and the run continues',
       r && r.success === false && api.calls[api.calls.length - 1] === 'pomodoro-timer');
  }
  {
    const api = fakeApi({ 'static-image': () => { throw { error_code: 'PLUGIN_UPDATE_FAILED', message: 'check logs' }; } });
    setup(api, { windowList: INSTALLED });
    const results = await Manager.updateAll(null, { sleep: async () => { throw new Error('must not sleep'); } });
    ok('an HTTP error answer is not retried',
       api.calls.filter(id => id === 'static-image').length === 1, api.calls);
    ok('...and is reported as a failure', results.find(x => x.pluginId === 'static-image').success === false);
  }

  console.log('\nthe real api_client.js: only a missing HTTP answer is retried');
  {
    // The fake PluginAPI above decides error_code itself. This runs the shipped
    // client so its classification is what gets tested: a proxy's 502 page or
    // a JSON 500 without error_code used to come back as NETWORK_ERROR and be
    // re-sent five more times.
    const PluginAPI = require(path.join(V3, 'js/plugins/api_client.js'));
    const run = async (makeFetch) => {
      let requests = 0, sleeps = 0;
      global.fetch = async () => { requests++; return makeFetch(requests); };
      global.window = { PluginAPI, installedPlugins: [{ id: 'stock-news' }] };
      const results = await Manager.updateAll(null, { sleep: async () => { sleeps++; }, retryDelaysMs: [1, 1, 1] });
      return { requests, sleeps, result: results[0] };
    };
    const httpAnswer = (status, json) => ({ ok: status < 400, status, json });

    let r = await run(() => httpAnswer(502, async () => { throw new SyntaxError('Unexpected token <'); }));
    ok('a 502 with an HTML body is sent once', r.requests === 1 && r.sleeps === 0, r);
    ok('...and is an API_ERROR carrying the status, not NETWORK_ERROR',
       r.result.success === false && r.result.error.error_code === 'API_ERROR' && r.result.error.status === 502, r.result.error);

    r = await run(() => httpAnswer(500, async () => ({ status: 'error', message: 'boom' })));
    ok('a JSON 500 without error_code is sent once', r.requests === 1 && r.sleeps === 0, r);
    ok('...and keeps the server message', r.result.error.message === 'boom', r.result.error);

    r = await run(() => httpAnswer(500, async () => ({ status: 'error', error_code: 'PLUGIN_UPDATE_FAILED', message: 'x' })));
    ok('a structured error is passed through unchanged',
       r.requests === 1 && r.result.error.error_code === 'PLUGIN_UPDATE_FAILED', r.result.error);

    r = await run((n) => {
      if (n === 1) throw new TypeError('Failed to fetch');
      return httpAnswer(200, async () => ({ status: 'success', message: 'ok', data: { update_status: 'updated' } }));
    });
    ok('a fetch() that rejects is NETWORK_ERROR and re-sent', r.requests === 2 && r.sleeps === 1 && r.result.success, r);

    r = await run(() => httpAnswer(200, async () => { throw new SyntaxError('Unexpected end of JSON input'); }));
    ok('an unreadable 200 is not retried either', r.requests === 1 && r.result.error.error_code === 'API_ERROR', r);
    delete global.fetch;
  }

  console.log('\nsummary: a no-op update is not counted as updated');
  {
    const answer = (update_status, message) => ({ success: true, result: { status: 'success', message, data: { update_status } } });
    const results = [
      answer('updated', 'Plugin a updated to version 2.8.0'),
      // ZIP-installed monorepo plugin already at the registry version: the
      // route used to call this "updated successfully".
      answer('up_to_date', 'Plugin stock-news already up to date (version 2.8.0)'),
      answer('up_to_date', 'Plugin clock already up to date (commit abcdef1)'),
      answer('local_only', 'Plugin mine is managed locally and does not receive registry updates'),
      { success: false, error: { error_code: 'PLUGIN_UPDATE_FAILED' } },
    ];
    const s = Manager.summarizeUpdateResults(results);
    ok('counts come from update_status',
       s.updated === 1 && s.upToDate === 2 && s.localOnly === 1 && s.failed === 1, s);
    ok('toast text names each outcome',
       s.text === '1 updated, 2 already up to date, 1 managed locally, 1 failed', s.text);
    ok('a failure alongside an update is a warning', s.type === 'warning', s.type);
    ok('an older server that only says so in the message is still up to date',
       Manager.updateOutcome({ success: true, result: { message: 'Plugin x already up to date (commit 1234567)' } }) === 'up_to_date');
    ok('a success without a status or telltale message counts as updated',
       Manager.updateOutcome({ success: true, result: { message: 'Plugin x updated successfully' } }) === 'updated');
    const allNoop = Manager.summarizeUpdateResults([answer('up_to_date', ''), answer('up_to_date', '')]);
    ok('nothing to do is a success toast with no "updated"',
       allNoop.type === 'success' && allNoop.text === '2 already up to date', allNoop);
  }

  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
