// The store's Install button: which plugin it enables afterwards, and when.
//
//  1. Weather, Music, Stocks and Leaderboard are registry entries (`weather`)
//     whose manifests declare another id (`ledmatrix-weather`). The plugin
//     list, its config section and /plugins/toggle know them by that id, but
//     the button enabled the registry id: /plugins/toggle answered 404
//     "Plugin not found" and the plugin stayed disabled behind "installed,
//     but enabling it failed". It now enables the id the install answer
//     names (`plugin_id`), or, from an answer without one, the installed
//     entry the store entry matches (its id, plugin_path name or aliases).
//
//  2. Reinstall (the same button on an installed plugin) enabled it too, so
//     reinstalling a plugin the user had switched off switched it back on.
//     Only a fresh install enables.
//
// Runs the whole of plugins_manager.js in the sandbox against a fake API.

const { create } = require('../plugins_manager_sandbox');

let pass = 0, fail = 0;
const ok = (label, cond, extra) => cond
  ? (pass++, console.log('  ok   ' + label))
  : (fail++, console.log('  FAIL ' + label + (extra !== undefined ? '  -> ' + JSON.stringify(extra).slice(0, 400) : '')));

const STORE = [
  { id: 'weather', name: 'Weather', category: 'weather', plugin_path: 'plugins/ledmatrix-weather',
    aliases: ['ledmatrix-weather'] },
  { id: 'clock-simple', name: 'Clock', category: 'time', plugin_path: 'plugins/clock-simple', aliases: [] },
];

// A server with one install in flight. `queue` false answers the install
// directly; `names` false leaves plugin_id out of the answer (an older
// server); `installsAs` is the id the installed manifest declares.
function server({ installed = [], queue = true, names = true, installsAs }) {
  const state = { installed: installed.map(p => ({ ...p })), polls: 0 };
  const done = (id) => {
    if (!state.installed.some(p => p.id === installsAs)) {
      state.installed.push({ id: installsAs, name: id, enabled: false });
    }
    const result = { success: true, message: `Plugin ${id} installed successfully`, restart_required: false };
    if (names) result.plugin_id = installsAs;
    return result;
  };
  state.route = (method, url, body) => {
    if (url.startsWith('/api/v3/plugins/store/list')) {
      return { json: { status: 'success', data: { plugins: STORE } } };
    }
    if (url.startsWith('/api/v3/plugins/installed')) {
      return { json: { status: 'success', data: { plugins: state.installed.map(p => ({ ...p })) } } };
    }
    if (method === 'POST' && url === '/api/v3/plugins/install') {
      if (queue) return { json: { status: 'success', message: 'queued', data: { operation_id: 'op-1' } } };
      return { json: { status: 'success', message: 'Plugin installed successfully', ...done(body.plugin_id) } };
    }
    if (url === '/api/v3/plugins/operation/op-1') {
      state.polls++;
      if (state.polls < 3) return { json: { status: 'success', data: { status: 'running' } } };
      return { json: { status: 'success', data: { status: 'completed', result: done('weather') } } };
    }
    if (method === 'POST' && url === '/api/v3/plugins/toggle') {
      const plugin = state.installed.find(p => p.id === body.plugin_id);
      if (!plugin) return { status: 404, json: { status: 'error', message: 'Plugin not found' } };
      plugin.enabled = body.enabled;
      return { json: { status: 'success', message: `Plugin ${body.plugin_id} enabled successfully` } };
    }
    return { json: { status: 'success' } };
  };
  return state;
}

async function install(pluginId, opts) {
  const srv = server(opts);
  const sb = create({ route: srv.route });
  sb.window.searchPluginStore();
  await sb.until(() => sb.requests.some(r => r.url.startsWith('/api/v3/plugins/store/list')), 'store list');
  await sb.window.pluginManager.loadInstalledPlugins(true);
  await sb.settle();
  sb.requests.length = 0;
  sb.toasts.length = 0;
  sb.window.installPlugin(pluginId);
  await sb.until(() => sb.toasts.some(t => /installed and enabled|enabling it failed|reinstalled/.test(t.message)),
                 'the install to finish');
  await sb.settle();
  const toggles = sb.requests.filter(r => r.url === '/api/v3/plugins/toggle').map(r => r.body);
  return { sb, srv, toggles };
}

(async () => {
  console.log('\n1. a fresh install enables the id the plugin was installed as');
  {
    const { srv, toggles, sb } = await install('weather', { installsAs: 'ledmatrix-weather' });
    ok('enables ledmatrix-weather, not the registry id',
       JSON.stringify(toggles) === JSON.stringify([{ plugin_id: 'ledmatrix-weather', enabled: true }]), toggles);
    ok('...which the server enabled', srv.installed.find(p => p.id === 'ledmatrix-weather').enabled === true, srv.installed);
    ok('says so', sb.toasts.some(t => t.type === 'success' && /installed and enabled/.test(t.message)), sb.toasts);
    ok('no "Plugin not found"', !sb.toasts.some(t => /not found|failed/.test(t.message)), sb.toasts);
    const lastList = sb.requests.map(r => r.url).lastIndexOf('/api/v3/plugins/installed');
    const toggleAt = sb.requests.findIndex(r => r.url === '/api/v3/plugins/toggle');
    ok('the installed list is reloaded before enabling, so the new card is there to update',
       lastList >= 0 && lastList < toggleAt, sb.requests.map(r => r.method + ' ' + r.url));
  }
  {
    const { toggles } = await install('weather', { installsAs: 'ledmatrix-weather', names: false });
    ok('an answer without plugin_id: the installed entry the store entry matches (its alias)',
       JSON.stringify(toggles) === JSON.stringify([{ plugin_id: 'ledmatrix-weather', enabled: true }]), toggles);
  }
  {
    const { toggles } = await install('weather', { installsAs: 'ledmatrix-weather', queue: false });
    ok('without the operation queue, from the direct answer',
       JSON.stringify(toggles) === JSON.stringify([{ plugin_id: 'ledmatrix-weather', enabled: true }]), toggles);
  }
  {
    const { toggles } = await install('clock-simple', { installsAs: 'clock-simple', names: false });
    ok('a plugin installed under its registry id is enabled by that id',
       JSON.stringify(toggles) === JSON.stringify([{ plugin_id: 'clock-simple', enabled: true }]), toggles);
  }

  console.log('\n2. a reinstall leaves the plugin as the user had it');
  {
    const { srv, toggles, sb } = await install('weather', {
      installsAs: 'ledmatrix-weather', installed: [{ id: 'ledmatrix-weather', name: 'Weather', enabled: false }],
    });
    ok('sends no toggle', toggles.length === 0, toggles);
    ok('the plugin stays disabled', srv.installed.find(p => p.id === 'ledmatrix-weather').enabled === false);
    ok('says it was reinstalled', sb.toasts.some(t => t.type === 'success' && /reinstalled/.test(t.message)), sb.toasts);
    ok('and reloads the list',
       sb.requests.some(r => r.url === '/api/v3/plugins/installed'), sb.requests.map(r => r.url));
  }
  {
    const { toggles } = await install('weather', {
      installsAs: 'ledmatrix-weather', installed: [{ id: 'ledmatrix-weather', name: 'Weather', enabled: true }],
    });
    ok('an enabled plugin is not toggled either', toggles.length === 0, toggles);
  }

  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
