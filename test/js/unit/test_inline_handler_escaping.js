// Registry- and upload-supplied strings must not escape the inline handlers
// plugins_manager.js builds for them.
//
// The store, saved-repository and custom-registry renderers wrote
//
//     <button onclick='... installFromCustomRegistry(${JSON.stringify(id)}) ...'>
//
// JSON.stringify makes a valid JS string but leaves `'` alone, so an entry id
// of  x' onmouseover='alert(1)  closed the single-quoted attribute and added a
// handler of its own. (The uploaded-image list is no longer built here: the
// file-upload widget owns it, and test_file_upload_widget.js covers it.)
//
// Each case renders with the shipped function, parses the tag the way a browser
// does (quoted attribute values, entities decoded), and checks two things: no
// attribute appeared that the template did not write, and the decoded handler
// runs and passes the hostile value through intact as data.

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

// ── DOM shim ───────────────────────────────────────────────────────────────
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
global.isStorePluginInstalled = () => false;
global.isNewPlugin = () => false;
global.formatDate = () => '';
global.setGridHtmlIfChanged = (container, html) => { container.innerHTML = html; };

// eslint-disable-next-line no-eval
eval([
  'function escapeHtml(text) {', 'function escapeAttribute(text) {', 'function jsStringAttr(value) {',
  'function renderPluginStore(plugins) {', 'function renderSavedRepositories(repositories) {',
  'function renderCustomRegistryPlugins(plugins, registryUrl) {',
].map(extract).join('\n') + '\nglobal.jsStringAttr = jsStringAttr; global.escapeHtml = escapeHtml;'
  + '\nglobal.renderPluginStore = renderPluginStore; global.renderSavedRepositories = renderSavedRepositories;'
  + '\nglobal.renderCustomRegistryPlugins = renderCustomRegistryPlugins; global.escapeAttribute = escapeAttribute;');

// ── minimal HTML start-tag tokenizer ───────────────────────────────────────
function decodeEntities(s) {
  return s.replace(/&(#x[0-9a-f]+|#\d+|quot|amp|lt|gt|apos);/gi, (m, e) => {
    const k = e.toLowerCase();
    if (k === 'quot') return '"';
    if (k === 'amp') return '&';
    if (k === 'lt') return '<';
    if (k === 'gt') return '>';
    if (k === 'apos') return "'";
    return String.fromCodePoint(k[1] === 'x' ? parseInt(k.slice(2), 16) : parseInt(k.slice(1), 10));
  });
}
function tags(html, name) {
  const out = [];
  let i = 0;
  while ((i = html.indexOf('<' + name, i)) >= 0) {
    let j = i + name.length + 1;
    const attrs = [];
    for (;;) {
      while (/\s/.test(html[j])) j++;
      if (html[j] === '>' || j >= html.length) break;
      if (html[j] === '/') { j++; continue; }
      let n = '';
      while (j < html.length && !/[\s=>]/.test(html[j])) n += html[j++];
      let v = '';
      while (/\s/.test(html[j])) j++;
      if (html[j] === '=') {
        j++;
        while (/\s/.test(html[j])) j++;
        const q = html[j];
        if (q === '"' || q === "'") {
          const end = html.indexOf(q, j + 1);
          v = html.slice(j + 1, end); j = end + 1;
        } else {
          while (j < html.length && !/[\s>]/.test(html[j])) v += html[j++];
        }
      }
      attrs.push([n.toLowerCase(), decodeEntities(v)]);
    }
    out.push(attrs);
    i = j;
  }
  return out;
}
const names = attrs => attrs.map(a => a[0]);
const attr = (attrs, n) => (attrs.find(a => a[0] === n) || [])[1];

// Run a decoded inline handler with window/document stubs; return the calls.
function runHandler(code) {
  const calls = [];
  const win = new Proxy({}, {
    get: (_, prop) => (...args) => { calls.push([prop, ...args]); },
    has: () => true,
  });
  const doc = { getElementById: () => ({ value: '' }) };
  // eslint-disable-next-line no-new-func
  new Function('window', 'document', 'console', code)(win, doc, { error() {} });
  return calls;
}

const SQ = "x' onmouseover='alert(1)";
const DQ = 'x" onmouseover="alert(1)';
const AMP = 'x&#39; onmouseover=&#39;alert(1)';

console.log('\n1. custom registry install button');
for (const hostile of [SQ, DQ, AMP]) {
  renderCustomRegistryPlugins([{ id: hostile, name: 'n', plugin_path: hostile }], hostile);
  const buttons = tags(els['custom-registry-grid'].innerHTML, 'button');
  const b = buttons.find(a => attr(a, 'onclick'));
  ok(`${JSON.stringify(hostile)}: only onclick and class attributes`,
     b && names(b).join(',') === 'onclick,class', b && names(b));
  let calls = [];
  try { calls = runHandler(attr(b, 'onclick')); } catch (e) { calls = [['threw', String(e)]]; }
  ok(`${JSON.stringify(hostile)}: handler passes id, url and path intact`,
     calls.length === 1 && calls[0][0] === 'installFromCustomRegistry'
       && calls[0][1] === hostile && calls[0][2] === hostile && calls[0][3] === hostile, calls);
}

console.log('\n2. store install and view buttons');
for (const hostile of [SQ, DQ, AMP]) {
  renderPluginStore([{ id: hostile, name: 'n', repo: 'https://github.com/o/r', plugin_path: 'p' }]);
  const buttons = tags(els['plugin-store-grid'].innerHTML, 'button');
  ok(`${JSON.stringify(hostile)}: two buttons, no extra attributes`,
     buttons.length === 2 && buttons.every(b => names(b).every(n => ['onclick', 'class', 'disabled'].includes(n))),
     buttons.map(names));
  let calls = [];
  try { calls = runHandler(attr(buttons[0], 'onclick')); } catch (e) { calls = [['threw', String(e)]]; }
  ok(`${JSON.stringify(hostile)}: install handler gets the id intact`,
     calls.length === 1 && calls[0][0] === 'installPlugin' && calls[0][1] === hostile, calls);
}

console.log('\n3. store view button only opens http(s) links');
renderPluginStore([{ id: 'a', name: 'n', repo: 'https://github.com/o/r', plugin_path: 'plugins/a' }]);
{
  const view = tags(els['plugin-store-grid'].innerHTML, 'button')[1];
  const calls = runHandler(attr(view, 'onclick'));
  ok('https repo opens the plugin tree',
     calls.length === 1 && calls[0][0] === 'open' && calls[0][1] === 'https://github.com/o/r/tree/main/plugins/a', calls);
}
renderPluginStore([{ id: 'a', name: 'n', repo: 'javascript:alert(document.domain)//' }]);
{
  const view = tags(els['plugin-store-grid'].innerHTML, 'button')[1];
  const calls = runHandler(attr(view, 'onclick'));
  ok('javascript: repo opens nothing', calls.length === 0, calls);
  ok('javascript: repo button is disabled', names(view).includes('disabled'), names(view));
}

console.log('\n4. saved repository remove button');
for (const hostile of [SQ, DQ, AMP]) {
  renderSavedRepositories([{ url: hostile, name: hostile }]);
  const b = tags(els['saved-repositories-list'].innerHTML, 'button')[0];
  ok(`${JSON.stringify(hostile)}: no extra attributes`,
     b && names(b).join(',') === 'onclick,class,title,aria-label', b && names(b));
  let calls = [];
  try { calls = runHandler(attr(b, 'onclick')); } catch (e) { calls = [['threw', String(e)]]; }
  ok(`${JSON.stringify(hostile)}: handler gets the url intact`,
     calls.length === 1 && calls[0][0] === 'removeSavedRepository' && calls[0][1] === hostile, calls);
}

console.log(`\n${pass} passed, ${fail} failed\n`);
process.exit(fail ? 1 : 0);
