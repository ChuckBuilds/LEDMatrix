// Every HTML escaper in the web interface must escape quotes, not just
// & < >.
//
// The escapers are all written as `div.textContent = x; return div.innerHTML`.
// That round-trip escapes &, < and > because those are the only characters the
// HTML serializer has to escape in a *text node* -- quotes are left alone. But
// the widgets interpolate the result into quoted attribute values
// (`value="${escapeHtml(v)}"`, `title="${escapeHtml(v)}"`, ...), and there a
// bare `"` closes the attribute and lets the value add attributes of its own:
//
//     name" onfocus="alert(1)
//
// CodeQL reported 83 js/incomplete-html-attribute-sanitization alerts for
// exactly this. The web UI now has one implementation, window.LEDEscape in
// app-early.js, and the old per-file escapers are one-line names for it. This
// suite runs LEDEscape and every one of those names as shipped, and fails if a
// hand-rolled escaper appears anywhere else in web_interface/.

const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '../../../web_interface');

let pass = 0, fail = 0;
const ok = (label, cond, extra) => cond
  ? (pass++, console.log('  ok   ' + label))
  : (fail++, console.log('  FAIL ' + label + (extra !== undefined ? '  -> ' + JSON.stringify(extra).slice(0, 300) : '')));

// ── DOM shim ───────────────────────────────────────────────────────────────
// Mirrors what a browser does reading innerHTML back off textContent: & < >
// are escaped, quotes are not. Anything the escaper adds on top of that is
// the escaper's own doing, which is what we are testing.
class FakeEl {
  set textContent(v) { this._t = String(v == null ? '' : v); }
  get textContent() { return this._t || ''; }
  get innerHTML() {
    return (this._t || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');
  }
}
global.document = { createElement: () => new FakeEl() };
global.window = global;
const LEDEscape = require('../led_escape').install(window);

// ── source extraction ──────────────────────────────────────────────────────
// Pull a function out of a real source file by its opening line and balanced
// braces, so the test runs the shipped code rather than a copy of it.
function extract(file, opener) {
  const src = fs.readFileSync(path.join(ROOT, file), 'utf8');
  const start = src.indexOf(opener);
  if (start < 0) {
    console.error(`FAIL: cannot find ${JSON.stringify(opener)} in ${file}`);
    process.exit(1);
  }
  let i = src.indexOf('{', start), depth = 0;
  for (let j = i; j < src.length; j++) {
    if (src[j] === '{') depth++;
    else if (src[j] === '}') {
      depth--;
      if (depth === 0) return src.slice(start, j + 1);
    }
  }
  console.error(`FAIL: unbalanced braces after ${JSON.stringify(opener)} in ${file}`);
  process.exit(1);
}

// Evaluate an extracted escaper and return it as a callable.
function loadFn(file, opener, name, { method = false } = {}) {
  if (file === null) return LEDEscape[name];
  const body = extract(file, opener);
  // Class/object methods (`escapeHtml(text) {...}`) are not valid statements on
  // their own -- wrap them in an object literal so they can be evaluated.
  const code = method
    ? `(function(){ const o = { ${body} }; return o.${name}.bind(o); })()`
    : `(function(){ ${body}; return ${name}; })()`;
  // eslint-disable-next-line no-eval
  return eval(code);
}

// ── the escapers, as shipped ───────────────────────────────────────────────
const ESCAPERS = [
  ['app-early.js (LEDEscape.html)', null, null, 'html'],
  ['app-early.js (LEDEscape.attr)', null, null, 'attr'],
  ['base-widget.js (BaseWidget.escapeHtml)',
   'static/v3/js/widgets/base-widget.js', 'escapeHtml(text) {', 'escapeHtml', true],
  ['plugins_manager.js (top-level escapeHtml)',
   'static/v3/plugins_manager.js', 'function escapeHtml(text) {', 'escapeHtml', false],
  ['plugins_manager.js (starlark escapeHtml)',
   'static/v3/plugins_manager.js', 'function escapeHtml(str) {', 'escapeHtml', false],
  ['error_handler.js (escapeHtml)',
   'static/v3/js/utils/error_handler.js', 'function escapeHtml(text) {', 'escapeHtml', false],
  ['json-file-manager.js (_esc)',
   'static/v3/js/widgets/json-file-manager.js', '_esc(str) {', '_esc', true],
  ['plugin-file-manager.js (escHtml)',
   'static/v3/js/widgets/plugin-file-manager.js', 'function escHtml(s) {', 'escHtml', false],
  ['plugins_manager.js (escapeAttribute)',
   'static/v3/plugins_manager.js', 'function escapeAttribute(text) {', 'escapeAttribute', false],
  ['notification.js (escapeHtml)',
   'static/v3/js/widgets/notification.js', 'function escapeHtml(text) {', 'escapeHtml', false],
  ['google-calendar-picker.js (escapeHtml)',
   'static/v3/js/widgets/google-calendar-picker.js', 'function escapeHtml(str) {', 'escapeHtml', false],
  ['file-upload.js (escapeHtml)',
   'static/v3/js/widgets/file-upload.js', 'function escapeHtml(text) {', 'escapeHtml', false],
  ['text-input.js (escapeHtml)',
   'static/v3/js/widgets/text-input.js', 'function escapeHtml(text) {', 'escapeHtml', false],
  ['slider.js (escapeAttr)',
   'static/v3/js/widgets/slider.js', 'function escapeAttr(text) {', 'escapeAttr', false],
  ['display.html (escapeAttr)',
   'templates/v3/partials/display.html', 'function escapeAttr(text) {', 'escapeAttr', false],
  ['backup_restore.html (escapeHtml)',
   'templates/v3/partials/backup_restore.html', 'function escapeHtml(value) {', 'escapeHtml', false],
  ['operation_history.html (escapeHtml)',
   'templates/v3/partials/operation_history.html', 'function escapeHtml(text) {', 'escapeHtml', false],
  ['tools.html (escHtml)',
   'templates/v3/partials/tools.html', 'function escHtml(s) {', 'escHtml', false],
  ['tools.html (phEscape)',
   'templates/v3/partials/tools.html', 'function phEscape(s) {', 'phEscape', false],
  ['logs.html (escapeHtml)',
   'templates/v3/partials/logs.html', 'function escapeHtml(text) {', 'escapeHtml', false],
  ['cache.html (escapeHtml)',
   'templates/v3/partials/cache.html', 'function escapeHtml(text) {', 'escapeHtml', false],
];

// The breakout payload: closes a double-quoted attribute and opens an event
// handler. If `"` survives escaping, this is live script in the rendered page.
const BREAKOUT = 'x" onmouseover="alert(1)';

console.log('\n1. every escaper neutralises a double-quote attribute breakout');
for (const [label, file, opener, name, method] of ESCAPERS) {
  const fn = loadFn(file, opener, name, { method });
  const out = String(fn(BREAKOUT));
  ok(`${label}: no raw "`, !out.includes('"'), out);
  ok(`${label}: emits &quot;`, out.includes('&quot;'), out);
}

console.log('\n2. every escaper also escapes single quotes');
for (const [label, file, opener, name, method] of ESCAPERS) {
  const fn = loadFn(file, opener, name, { method });
  const out = String(fn("x' onmouseover='alert(1)"));
  ok(`${label}: no raw '`, !out.includes("'"), out);
}

console.log('\n3. the & < > behaviour they already had is unchanged');
for (const [label, file, opener, name, method] of ESCAPERS) {
  const fn = loadFn(file, opener, name, { method });
  const out = String(fn('<img src=x onerror=alert(1)> & done'));
  ok(`${label}: no raw <`, !out.includes('<'), out);
  ok(`${label}: no raw >`, !out.includes('>'), out);
  ok(`${label}: & becomes &amp;`, /&amp;/.test(out), out);
}

console.log('\n4. ampersands are escaped before quotes, so &quot; cannot be forged');
// If `&` were escaped last, the input `&quot;` would come out as a literal
// `"` after the browser decodes the attribute. Order matters; pin it.
for (const [label, file, opener, name, method] of ESCAPERS) {
  const fn = loadFn(file, opener, name, { method });
  const out = String(fn('&quot;'));
  ok(`${label}: &quot; input stays inert`, out === '&amp;quot;', out);
}

console.log('\n4b. LEDEscape.jsStringAttr: a JS string literal that survives an attribute');
{
  const decode = s => s.replace(/&(quot|#39|lt|gt|amp);/g, (m, e) =>
    ({ quot: '"', '#39': "'", lt: '<', gt: '>', amp: '&' })[e]);
  for (const v of ["x' onmouseover='alert(1)", 'x" onmouseover="alert(1)', '</script><b>', 'a&#39;b']) {
    const out = LEDEscape.jsStringAttr(v);
    ok(`${JSON.stringify(v)}: no raw quote or bracket`, !/["'<>]/.test(out), out);
    // eslint-disable-next-line no-eval
    ok(`${JSON.stringify(v)}: decodes back to the same string`, eval(decode(out)) === v, out);
  }
  ok('null and undefined become the empty string',
     LEDEscape.html(null) === '' && LEDEscape.html(undefined) === '' && LEDEscape.jsStringAttr(null) === '&quot;&quot;');
  ok('numbers are kept', LEDEscape.html(0) === '0', LEDEscape.html(0));
}

console.log('\n4c. no hand-rolled escaper outside app-early.js');
{
  const skip = new Set(['static/v3/js/app-early.js',
                        // documentation example, kept self-contained on purpose
                        'static/v3/js/widgets/example-color-picker.js']);
  const found = [];
  const walk = dir => fs.readdirSync(dir, { withFileTypes: true }).forEach(e => {
    const p = path.join(dir, e.name);
    const rel = path.relative(ROOT, p).split(path.sep).join('/');
    if (e.isDirectory()) { if (e.name !== 'vendor') walk(p); return; }
    if (!/\.(js|html)$/.test(e.name) || /\.min\.js$/.test(e.name) || skip.has(rel)) return;
    // Writing the entity for a quote is what an escaper does; nothing else in
    // the UI needs to.
    let text = fs.readFileSync(p, 'utf8');
    // In templates only the inline scripts count; Jinja's own |replace("'", "&#39;")
    // escaping of server-rendered values is not a JS escaper.
    if (e.name.endsWith('.html')) text = (text.match(/<script[^>]*>[\s\S]*?<\/script>/g) || []).join('\n');
    if (/['"`]&quot;['"`]|['"`]&#39;['"`]/.test(text)) found.push(rel);
  });
  walk(path.join(ROOT, 'static'));
  walk(path.join(ROOT, 'templates'));
  ok('every escaper is window.LEDEscape', found.length === 0, found);
  // Plugin pages may call these globals; they stay, as aliases.
  const early = fs.readFileSync(path.join(ROOT, 'static/v3/js/app-early.js'), 'utf8');
  ok('window.escapeHtml is kept as an alias of LEDEscape.html',
     early.includes('window.escapeHtml = window.LEDEscape.html;'));
  ok('window.escapeAttribute is kept as an alias of LEDEscape.attr',
     early.includes('window.escapeAttribute = window.LEDEscape.attr;'));
}

// ── url-input scheme handling (js/xss-through-dom) ─────────────────────────
console.log('\n5. url-input never treats a scriptable scheme as a valid URL');
{
  const src = fs.readFileSync(path.join(ROOT, 'static/v3/js/widgets/url-input.js'), 'utf8');
  const a = src.indexOf('const RFC_SCHEME_PATTERN');
  const b = src.indexOf("window.LEDMatrixWidgets.register('url-input'");
  if (a < 0 || b < 0) { console.error('FAIL: cannot locate url-input helpers'); process.exit(1); }
  // eslint-disable-next-line no-eval
  const helpers = eval(`(function(){ ${src.slice(a, b)}; return { normalizeProtocols, isValidUrl, safeHref }; })()`);
  const { normalizeProtocols, isValidUrl, safeHref } = helpers;

  ok('javascript: rejected under the default protocols',
     !isValidUrl('javascript:alert(1)', ['http', 'https']));
  ok('javascript: still rejected when a schema asks for it',
     !isValidUrl('javascript:alert(1)', normalizeProtocols(['javascript'])));
  ok('a schema asking only for javascript falls back to http/https',
     JSON.stringify(normalizeProtocols(['javascript'])) === JSON.stringify(['http', 'https']),
     normalizeProtocols(['javascript']));
  ok('data: rejected', !isValidUrl('data:text/html,<script>alert(1)</script>', ['http', 'https', 'data']));
  ok('vbscript: rejected', !isValidUrl('vbscript:msgbox(1)', ['http', 'https', 'vbscript']));
  ok('normalizeProtocols drops scriptable schemes but keeps the rest',
     JSON.stringify(normalizeProtocols('https,javascript,ftp')) === JSON.stringify(['https', 'ftp']),
     normalizeProtocols('https,javascript,ftp'));

  ok('ordinary https URL still valid', isValidUrl('https://example.com/x?y=1', ['http', 'https']));
  ok('ordinary http URL still valid', isValidUrl('http://example.com', ['http', 'https']));
  ok('a scheme outside the allow-list is still rejected',
     !isValidUrl('ftp://example.com', ['http', 'https']));

  ok('safeHref passes a good URL through', safeHref('https://example.com', ['http', 'https']) === 'https://example.com');
  ok('safeHref blanks a javascript: URL', safeHref('javascript:alert(1)', ['http', 'https']) === '');
  ok('safeHref blanks an unparseable value', safeHref('not a url', ['http', 'https']) === '');
}

// ── url-input onInput: the sink itself, not just the pulled-out helpers ────
// The scheme check now lives inline in onInput, right where the value reaches
// `previewLink.href`, instead of behind safeHref/isValidUrl -- see the comment
// at that assignment in url-input.js for why. Run the handler as shipped so a
// regression that reintroduces an unguarded `previewLink.href = value` fails
// here, not just in a scanner run weeks later.
console.log('\n6. url-input onInput: previewLink.href is guarded at the sink');
{
  class FakeClassList {
    constructor() { this.classes = new Set(['hidden']); }
    add(c) { this.classes.add(c); }
    remove(c) { this.classes.delete(c); }
    contains(c) { return this.classes.has(c); }
  }
  const mockDoc = (value, protocolsAttr) => {
    const elements = {
      _input: { value, checkValidity: () => true, validationMessage: '', classList: new FakeClassList() },
      _preview: { classList: new FakeClassList() },
      _preview_link: {
        classList: new FakeClassList(),
        _href: undefined,
        set href(v) { this._href = v; },
        get href() { return this._href; },
        removeAttribute(name) { if (name === 'href') this._href = undefined; },
      },
      _widget: { dataset: { protocols: protocolsAttr } },
      _error: { classList: new FakeClassList(), textContent: '' },
    };
    return {
      elements,
      getElementById: (id) => elements[Object.keys(elements).find(k => id === `field${k}`)] || null,
    };
  };

  function runOnInput(value, protocolsAttr) {
    const { elements, getElementById } = mockDoc(value, protocolsAttr);
    const savedDocument = global.document;
    const savedWindow = global.window;
    let registered = null;
    global.document = { createElement: () => new FakeEl(), getElementById };
    global.window = {
      LEDMatrixWidgets: {
        register: (name, obj) => { registered = obj; },
        get: () => registered,
        getHandlers: () => registered.handlers,
      },
    };
    try {
      const src = fs.readFileSync(path.join(ROOT, 'static/v3/js/widgets/url-input.js'), 'utf8');
      // eslint-disable-next-line no-eval
      eval(src);
      registered.handlers.onInput('field');
    } finally {
      global.document = savedDocument;
      global.window = savedWindow;
    }
    return elements;
  }

  let els = runOnInput('javascript:alert(1)', 'http,https');
  ok('javascript: never reaches previewLink.href', els._preview_link.href === undefined, els._preview_link.href);
  ok('javascript: leaves the preview hidden', els._preview.classList.contains('hidden'));

  els = runOnInput('https://example.com', 'http,https');
  ok('an ordinary https URL reaches previewLink.href', els._preview_link.href === 'https://example.com', els._preview_link.href);
  ok('an ordinary https URL unhides the preview', !els._preview.classList.contains('hidden'));

  els = runOnInput('data:text/html,<script>alert(1)</script>', 'http,https,data');
  ok('data: never reaches previewLink.href even when the schema allows it',
     els._preview_link.href === undefined, els._preview_link.href);

  els = runOnInput('not a url', 'http,https');
  ok('an unparseable value never reaches previewLink.href', els._preview_link.href === undefined, els._preview_link.href);
}

// ── plugin-file-manager: cell edits travel via data-*, not inline handlers ──
// A JSON key/day from an uploaded file used to be spliced, HTML-escaped,
// into an oninput="...('${escHtml(col)}'...)" attribute. escHtml neutralises
// a quote for an ordinary attribute, but here the value also has to survive
// as a *JS string literal* -- the browser HTML-decodes the attribute before
// running it as script, which turns the escaped quote back into a real one
// and lets a column named `x');alert(1);//` break out of the string and run
// arbitrary JS. Cell edits now reach _pfmCellEdit only via data-day/data-col
// read by one delegated listener, so this pins that no inline handler string
// is built from the value at all.
console.log("\n7. plugin-file-manager: cell edits never go through an inline handler string");
{
  const src = fs.readFileSync(path.join(ROOT, 'static/v3/js/widgets/plugin-file-manager.js'), 'utf8');

  function extractFn(opener) {
    const start = src.indexOf(opener);
    if (start < 0) { console.error(`FAIL: cannot find ${JSON.stringify(opener)}`); process.exit(1); }
    let i = src.indexOf('{', start), depth = 0;
    for (let j = i; j < src.length; j++) {
      if (src[j] === '{') depth++;
      else if (src[j] === '}') { depth--; if (depth === 0) return src.slice(start, j + 1); }
    }
    console.error(`FAIL: unbalanced braces after ${JSON.stringify(opener)}`);
    process.exit(1);
  }

  const escHtmlFn = loadFn('static/v3/js/widgets/plugin-file-manager.js', 'function escHtml(s) {', 'escHtml', false);
  const renderEntryTableSrc = extractFn('function renderEntryTable(fieldId, container, content) {');

  const calls = [];
  const fakeWindow = { _pfmCellEdit: (fieldId, day, col, value) => calls.push({ fieldId, day, col, value }) };

  class FakeContainer {
    constructor() { this._html = ''; this._listeners = {}; }
    set innerHTML(v) { this._html = v; }
    get innerHTML() { return this._html; }
    set textContent(v) { this._html = v; }
    addEventListener(type, fn) { this._listeners[type] = fn; }
    dispatch(type, target) { this._listeners[type]({ target }); }
  }

  function fakeCell(day, col, value) {
    return {
      closest: (sel) => (sel.includes('data-day') ? { dataset: { day: String(day), col: String(col) }, value } : null),
    };
  }

  // eslint-disable-next-line no-eval
  const renderEntryTable = eval(`(function(getState, escHtml, safeSetHTML, window){
    ${renderEntryTableSrc}
    return renderEntryTable;
  })`)(
    () => ({ entriesPerPage: 20, _tablePage: 1 }),
    escHtmlFn,
    (target, html) => { target.innerHTML = html; },
    fakeWindow
  );

  const maliciousCol = "x');alert(1);//";
  const container = new FakeContainer();
  renderEntryTable('field1', container, { '1': { [maliciousCol]: 'hello' } });

  ok('no inline oninput handler is emitted for a cell', !/oninput=/.test(container.innerHTML), container.innerHTML);
  ok('the malicious column name never appears unescaped in the markup',
     !container.innerHTML.includes(maliciousCol), container.innerHTML);

  container.dispatch('input', fakeCell('1', maliciousCol, 'typed value'));
  ok('the delegated listener still reaches _pfmCellEdit with the real day/col',
     calls.length === 1 && calls[0].day === '1' && calls[0].col === maliciousCol && calls[0].value === 'typed value',
     calls);
}

console.log(`\n${pass} passed, ${fail} failed\n`);
process.exit(fail ? 1 : 0);
