// Verifies renderInstalledCards (extracted verbatim from plugins_manager.js):
// the two empty states, the card markup, and that it never publishes state.
const fs = require('fs');
const path = require('path');
const V3 = path.resolve(__dirname, '../../../web_interface/static/v3');
const src = fs.readFileSync(V3 + '/plugins_manager.js', 'utf8');

function slice(fromMark, toMark) {
  const a = src.indexOf(fromMark);
  const b = src.indexOf(toMark, a + 1);
  if (a < 0 || b < 0) { console.error('FAIL: cannot locate ' + fromMark); process.exit(1); }
  return src.slice(a, b);
}

const container = {
  innerHTML: '',
  querySelectorAll: () => [],   // no skeletons in this harness
};
// escapeHtml() escapes via a detached element, so mirror what a browser does
// when you read innerHTML back off textContent: & < > are escaped, quotes are not.
class FakeEl {
  set textContent(v) { this._t = String(v == null ? '' : v); }
  get textContent() { return this._t || ''; }
  get innerHTML() {
    return (this._t || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }
}
global.document = {
  getElementById: id => (id === 'installed-plugins-grid' ? container : null),
  createElement: () => new FakeEl(),
};
global.window = global;
global.pluginLog = () => {};
global.PLUGIN_DEBUG = false;
global.debugLog = () => {};
function setupInstalledEventDelegation() {}   // stubbed; tested separately

eval(slice('function escapeHtml(text)', '\nfunction ', ));
eval(slice('function renderInstalledCards(plugins, total)',
           '// Set up event delegation for plugin action buttons'));

let pass = 0, fail = 0;
const ok = (l, c, x) => c ? (pass++, console.log('  ok   ' + l))
                          : (fail++, console.log('  FAIL ' + l + (x !== undefined ? '  → ' + JSON.stringify(x).slice(0, 300) : '')));

const SENTINEL = ['do', 'not', 'touch'];
window.installedPlugins = SENTINEL;

console.log('\n1. no plugins installed at all');
renderInstalledCards([], 0);
ok('shows "No plugins installed"', /No plugins installed/.test(container.innerHTML));
ok('does NOT show the filter empty state', !/No plugins match/.test(container.innerHTML));
ok('uses the plug icon', /fa-plug/.test(container.innerHTML));

console.log('\n2. plugins installed but none match the filters');
renderInstalledCards([], 12);
ok('shows "No plugins match your filters"', /No plugins match your filters/.test(container.innerHTML));
ok('does NOT claim nothing is installed', !/No plugins installed/.test(container.innerHTML));
ok('reports the installed total', /12 plugins installed/.test(container.innerHTML), container.innerHTML.match(/\d+ plugins? installed/));
ok('offers a Clear filters button', /data-action="clear-installed-filters"/.test(container.innerHTML));
ok('uses the filter icon', /fa-filter/.test(container.innerHTML));

console.log('\n3. singular/plural of the installed total');
renderInstalledCards([], 1);
ok('"1 plugin installed" (singular)', /1 plugin installed/.test(container.innerHTML) && !/1 plugins/.test(container.innerHTML));

console.log('\n4. real cards');
const plugins = [
  { id: 'alpha', name: 'Alpha', author: 'Ann', version: '1.0.0', category: 'weather',
    description: 'Shows weather', enabled: true, tags: ['a', 'b'], verified: true },
  { id: 'beta', name: 'Beta', author: 'Bob', version: '1.0.0', latest_version: '2.0.0',
    update_available: true, category: 'time', description: 'Clock', enabled: false },
];
renderInstalledCards(plugins, 5);
const html = container.innerHTML;
ok('renders 2 cards', (html.match(/class="plugin-card"/g) || []).length === 2);
ok('enabled card shows Enabled', /<span>Enabled<\/span>/.test(html));
ok('disabled card shows Disabled', /<span>Disabled<\/span>/.test(html));
ok('update badge shows target version', /v2\.0\.0 available/.test(html));
ok('update button labelled "Update to v2.0.0"', /Update to v2\.0\.0/.test(html));
ok('pulsing ring class on the outdated one', /plugin-update-available/.test(html));
ok('verified badge present', /Verified/.test(html));
ok('tags rendered', /badge-info">a</.test(html) && /badge-info">b</.test(html));
ok('per-plugin action hooks present',
   /data-action="configure"/.test(html) && /data-action="update"/.test(html) &&
   /data-action="uninstall"/.test(html) && /data-action="toggle"/.test(html));
ok('no empty state alongside cards', !/empty-state/.test(html));

console.log('\n5. it must not publish state');
ok('window.installedPlugins untouched', window.installedPlugins === SENTINEL, window.installedPlugins);

console.log('\n6. total defaults to list length when omitted');
renderInstalledCards([], undefined);
ok('omitted total + empty list → "no plugins installed"', /No plugins installed/.test(container.innerHTML));

console.log('\n7. XSS: hostile plugin metadata is escaped');
renderInstalledCards([{
  id: 'evil', name: '<img src=x onerror=alert(1)>', author: '"><script>bad()</script>',
  version: '1.0.0', category: 'x', description: '<b>nope</b>', enabled: true, tags: ['<i>t</i>'],
}], 1);
const evil = container.innerHTML;
ok('no raw <img', !/<img src=x/.test(evil));
ok('no raw <script', !/<script>bad/.test(evil));
ok('no raw <b> from description', !/<b>nope<\/b>/.test(evil));
ok('no raw <i> from tags', !/<i>t<\/i>/.test(evil));
ok('escaped entities present instead', /&lt;/.test(evil));

console.log(`\n${pass} passed, ${fail} failed\n`);
process.exit(fail ? 1 : 0);
