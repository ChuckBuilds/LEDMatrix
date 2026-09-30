// The store card shows the registry fields added after 3.7.0 -- the commit
// that introduced the listed version and a warning when the plugin needs a
// newer core -- and still renders a card from an older registry that has
// neither. isStorePluginInstalled also answers to an entry's `aliases`.
//
// Rendered with the shipped functions (extracted from plugins_manager.js).

const fs = require('fs');
const path = require('path');

const SRC = fs.readFileSync(
  path.resolve(__dirname, '../../../web_interface/static/v3/plugins_manager.js'), 'utf8');

let pass = 0, fail = 0;
const ok = (label, cond, extra) => cond
  ? (pass++, console.log('  ok   ' + label))
  : (fail++, console.log('  FAIL ' + label + (extra !== undefined ? '  -> ' + JSON.stringify(extra).slice(0, 400) : '')));

function extract(opener) {
  const start = SRC.indexOf(opener);
  if (start < 0) { console.error('FAIL: cannot find ' + JSON.stringify(opener)); process.exit(1); }
  let depth = 0;
  for (let j = SRC.indexOf('{', start); j < SRC.length; j++) {
    if (SRC[j] === '{') depth++;
    else if (SRC[j] === '}' && --depth === 0) return SRC.slice(start, j + 1);
  }
  console.error('FAIL: unbalanced braces after ' + opener); process.exit(1);
}

class FakeEl {
  constructor() { this.innerHTML = ''; this.value = ''; this.textContent = ''; }
}
class TextEl {
  set textContent(v) { this._t = String(v == null ? '' : v); }
  get innerHTML() {
    return (this._t || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }
}
const els = {};
global.document = {
  getElementById: id => (els[id] ||= new FakeEl()),
  createElement: () => new TextEl(),
};
global.window = global;
require('../led_escape').install(window);
global.pluginLog = () => {};
global.isNewPlugin = () => false;
global.formatDate = () => '';
global.setGridHtmlIfChanged = (container, html) => { container.innerHTML = html; };
global.installedPlugins = [];

// eslint-disable-next-line no-eval
eval([
  'function escapeHtml(text) {', 'function escapeAttribute(text) {', 'function jsStringAttr(value) {',
  'function isStorePluginInstalled(pluginIdOrPlugin) {', 'function renderPluginStore(plugins) {',
].map(extract).join('\n') + '\nglobal.renderPluginStore = renderPluginStore;'
  + '\nglobal.isStorePluginInstalled = isStorePluginInstalled;');

function render(plugin) {
  renderPluginStore([plugin]);
  return els['plugin-store-grid'].innerHTML;
}

const SHA = '843588025a81197056f8d96779ccb2be19337ab8';
const base = {
  id: 'weather', name: 'Weather', author: 'ChuckBuilds', category: 'weather',
  description: 'Forecasts', version: '2.1.0',
  repo: 'https://github.com/ChuckBuilds/ledmatrix-plugins', plugin_path: 'plugins/ledmatrix-weather',
};

console.log('\n1. a registry with the new fields');
let html = render({ ...base, commit: SHA, ledmatrix_min_version: '3.7.0', aliases: ['ledmatrix-weather'] });
ok('shows the short commit', html.includes('>8435880<'), html.match(/v2\.1\.0[^\n]*/));
ok('links it to the plugin at that commit',
   html.includes(`href="https://github.com/ChuckBuilds/ledmatrix-plugins/tree/${SHA}/plugins/ledmatrix-weather"`));
ok('no compatibility warning when the core is new enough', !html.includes('Needs LEDMatrix'));

console.log('\n2. a plugin this core cannot run');
html = render({ ...base, ledmatrix_min_version: '9.0.0',
                incompatible_reason: 'Weather requires LEDMatrix 9.0.0 or newer' });
ok('warns with the floor', html.includes('Needs LEDMatrix 9.0.0+'));
ok('and the reason as its title', html.includes('title="Weather requires LEDMatrix 9.0.0 or newer"'));

console.log('\n3. an older registry: no commit, floor or aliases');
html = render({ ...base });
ok('still renders the card and its version', html.includes('class="plugin-card"') && html.includes('v2.1.0'));
ok('shows no commit and no warning', !html.includes('font-mono') && !html.includes('Needs LEDMatrix'));

console.log('\n4. a commit value that is not a SHA is not rendered');
html = render({ ...base, commit: 'javascript:alert(1)' });
ok('dropped', !html.includes('javascript:') && !html.includes('font-mono'));
html = render({ ...base, commit: SHA, repo: 'javascript:alert(1)' });
ok('without a web repo link it is plain text, not a link',
   html.includes('>8435880<') && !html.includes('/tree/'));

console.log('\n5. installed under an alias');
global.installedPlugins = [{ id: 'ledmatrix-weather' }];
ok('aliases count as installed',
   isStorePluginInstalled({ id: 'weather', plugin_path: '', aliases: ['ledmatrix-weather'] }));
ok('plugin_path still does (older registry)',
   isStorePluginInstalled({ id: 'weather', plugin_path: 'plugins/ledmatrix-weather' }));
ok('a different plugin is not installed', !isStorePluginInstalled({ id: 'stocks', plugin_path: '' }));

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
