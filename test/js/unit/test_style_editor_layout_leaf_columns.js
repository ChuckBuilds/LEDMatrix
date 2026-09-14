// Regression test for style-editor.js's columnsFor(), extracted verbatim
// from the shipped widget (own/ownObj/elementKeys/columnsFor have no DOM
// dependency) so the test can't drift from the real implementation.
//
// elementKeys() already appends a layout-only key that never got its own
// top-level style block (see test_style_editor_element_keys.js), so table()
// draws a row for it. But render() still claims the whole `layout` child as
// the widget's own regardless of what that row actually shows -- and
// columnsFor() only produced a column for a layout-only key whose own value
// is an *object* with sub-fields (x_offset, y_offset, ...). A hand-written
// schema can instead put a plain leaf value directly under layout -- a
// "show_logo" toggle, a timeout indicator's on/off flag -- with no x/y
// object underneath it. That key still got a row (data-element="show_logo"),
// but zero columns ever matched it, so the row rendered with every cell
// blank: no control, and no way back to the fallback since layout was
// removed wholesale. This pins the fix: such a key gets its own
// self-keyed 'layout-leaf' column.
const fs = require('fs');
const path = require('path');
const V3 = path.resolve(__dirname, '../../../web_interface/static/v3');

const src = fs.readFileSync(path.join(V3, 'js/widgets/style-editor.js'), 'utf8');
const start = src.indexOf('function own(obj, key)');
const endMark = 'function control(opts)';
const end = src.indexOf(endMark);
if (start === -1 || end === -1) {
  console.error('FAIL: could not locate own()/columnsFor() in style-editor.js -- slice markers need updating');
  process.exit(1);
}
eval(src.slice(start, end)); // defines own/ownObj/elementKeys/columnsFor

let failures = 0;
function ok(desc, cond) {
  if (cond) { console.log(`ok - ${desc}`); }
  else { console.log(`FAIL - ${desc}`); failures++; }
}

// score_text is a real style element; possession is layout-only but shaped
// like an object (x/y); show_logo is layout-only and a bare leaf value.
const schema = {
  properties: {
    score_text: {
      type: 'object',
      'x-style-managed': true,
      properties: { font: { type: 'string' }, text_color: { type: 'array' } },
    },
    layout: {
      type: 'object',
      properties: {
        score_text: { type: 'object', properties: { x_offset: {}, y_offset: {} } },
        possession: { type: 'object', properties: { x_offset: {}, y_offset: {} } },
        show_logo: { type: 'boolean', default: true },
      },
    },
  },
};

const columns = columnsFor(schema);
const byKey = {};
columns.forEach(function (c) { byKey[c.key] = c; });

ok('an element style field still gets its own column', byKey.font && byKey.font.where === 'element');
ok('a layout-only object key still contributes its x/y columns',
   byKey.x_offset && byKey.x_offset.where === 'layout');
ok('a layout-only leaf key gets a column keyed to itself',
   !!byKey.show_logo);
ok('...marked as a layout-leaf column, not a shared sub-field',
   byKey.show_logo && byKey.show_logo.where === 'layout-leaf');
ok('nothing is duplicated', columns.length === new Set(columns.map(function (c) { return c.key; })).size);

// A schema with no leaf-valued layout key must not gain a phantom column --
// this is the common case (every existing scoreboard) and must be unaffected.
const noLeaf = {
  properties: {
    score_text: {
      type: 'object', 'x-style-managed': true,
      properties: { font: { type: 'string' } },
    },
    layout: {
      type: 'object',
      properties: { score_text: { type: 'object', properties: { x_offset: {} } } },
    },
  },
};
ok('a schema with only object-shaped layout keys gets no layout-leaf column',
   columnsFor(noLeaf).every(function (c) { return c.where !== 'layout-leaf'; }));

if (failures) { console.log(`\n${failures} failure(s)`); process.exit(1); }
console.log('\nall checks passed');
