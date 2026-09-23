// Installed-plugin card actions go through handlePluginAction exactly once.
//
// The document-level delegation in plugins_manager.js tested
// `typeof handlePluginAction`, which lives inside the plugin-manager IIFE and
// so was never visible to it. Every click took a copied fallback instead,
// which stopped propagation (the grid's own listener never ran), asked to
// confirm an uninstall twice, and sent Starlark app uninstalls to the plugin
// endpoint instead of DELETE /starlark/apps/<id>.
//
// Runs the shipped global delegation and handlePluginAction, sliced out of
// plugins_manager.js, against a minimal fake DOM.

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const SRC = fs.readFileSync(
  path.resolve(__dirname, '../../../web_interface/static/v3/plugins_manager.js'), 'utf8');

function slice(startMarker, endMarker) {
  const a = SRC.indexOf(startMarker);
  const b = SRC.indexOf(endMarker, a);
  if (a < 0 || b < 0) throw new Error(`could not find ${startMarker} .. ${endMarker}`);
  return SRC.slice(a, b);
}

const GLOBAL_DELEGATION = slice('(function setupGlobalEventDelegation() {', '// Note: configurePlugin');
const HANDLER = slice('function handlePluginAction(event) {', 'function findInstalledPlugin(pluginId)');

let pass = 0, fail = 0;
const ok = (label, cond, extra) => cond
  ? (pass++, console.log('  ok   ' + label))
  : (fail++, console.log('  FAIL ' + label + (extra !== undefined ? '  ' + JSON.stringify(extra) : '')));

function setup() {
  const listeners = {};
  const calls = { confirm: 0, fetch: [], uninstallPlugin: [], togglePlugin: [] };
  const window = {
    installedPlugins: [{ id: 'clock', enabled: false }],
    uninstallPlugin: id => calls.uninstallPlugin.push(id),
    togglePlugin: (id, on) => calls.togglePlugin.push([id, on]),
  };
  const ctx = {
    window,
    document: {
      addEventListener: (type, fn, capture) => { (listeners[type] = listeners[type] || []).push(fn); },
    },
    confirm: () => { calls.confirm++; return true; },
    fetch: (url, opts) => { calls.fetch.push([url, opts && opts.method]); return new Promise(() => {}); },
    alert: () => {},
    console,
    setTimeout,
    debugLog: () => {},
    getInstalledFilter: () => null,
  };
  vm.createContext(ctx);
  // The handler lives inside the plugin-manager IIFE in the real file, so it
  // runs in one here too: the global delegation must not see it by name.
  vm.runInContext(GLOBAL_DELEGATION + '\n(function() {\n' + HANDLER + '\n})();', ctx);

  const click = (action, pluginId) => {
    const el = {
      getAttribute: name => ({ 'data-action': action, 'data-plugin-id': pluginId })[name],
      type: 'button',
    };
    let stopped = false;
    const event = {
      type: 'click',
      target: { closest: () => el },
      preventDefault() {},
      stopPropagation() { stopped = true; },
    };
    for (const fn of listeners.click || []) fn(event);
    return stopped;
  };
  return { window, calls, click, listeners };
}

(async () => {
  console.log('\n-- plugin card action delegation --');

  {
    const t = setup();
    ok('handlePluginAction is exposed on window', typeof t.window.handlePluginAction === 'function');
    ok('document-level click listener registered', (t.listeners.click || []).length === 1);
  }

  {
    // A non-Element target (a text node, the document) has no closest().
    const t = setup();
    let threw = null;
    try { for (const fn of t.listeners.click) fn({ type: 'click', target: {} }); } catch (e) { threw = e; }
    ok('non-Element event target is ignored', threw === null, threw && threw.message);
  }

  {
    const t = setup();
    t.click('uninstall', 'starlark:analogclock');
    ok('Starlark uninstall confirms once', t.calls.confirm === 1, t.calls.confirm);
    ok('Starlark uninstall hits DELETE /starlark/apps/<id>',
       t.calls.fetch.length === 1 && t.calls.fetch[0][0] === '/api/v3/starlark/apps/analogclock'
       && t.calls.fetch[0][1] === 'DELETE', t.calls.fetch);
    ok('Starlark uninstall does not go to the plugin uninstaller', t.calls.uninstallPlugin.length === 0);
  }

  {
    const t = setup();
    t.click('uninstall', 'clock');
    await new Promise(r => setTimeout(r, 20));
    ok('plugin uninstall: handler itself does not confirm (uninstallPlugin does)', t.calls.confirm === 0,
       t.calls.confirm);
    ok('plugin uninstall calls uninstallPlugin once', t.calls.uninstallPlugin.length === 1
       && t.calls.uninstallPlugin[0] === 'clock', t.calls.uninstallPlugin);
  }

  {
    const t = setup();
    const stopped = t.click('toggle', 'clock');
    await new Promise(r => setTimeout(r, 20));
    ok('toggle flips the stored state', JSON.stringify(t.calls.togglePlugin) === '[["clock",true]]',
       t.calls.togglePlugin);
    ok('handled once (propagation stopped)', stopped === true);
  }

  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
})();
