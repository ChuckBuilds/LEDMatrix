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

  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
