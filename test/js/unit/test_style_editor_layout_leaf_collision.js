// Regression test for style-editor.js's columnsFor(), extracted verbatim
// from the shipped widget (see test_style_editor_layout_leaf_columns.js for
// the base case this builds on).
//
// A layout-only leaf key (e.g. a bare "scale" toggle directly under
// `layout`, no x/y object underneath) used to be keyed in the internal
// `seen` map by its own bare field name. If some *other* element's style
// block, or another element's layout axis block, happened to declare a
// sub-field with that exact same name (e.g. "scale" is also a real
// COLUMN_ORDER axis name most elements use), the `!seen.has(key)` guard
// skipped creating the leaf's column -- it silently reused the existing
// 'element'/'layout' column instead. That column's row binding in
// elementRow() then looked the field up under the wrong schema location for
// the leaf's own row, found nothing, and rendered a blank cell: the same
// "silently disappears" bug the leaf-column fix was meant to close, just
// reached through a name collision instead of a missing column altogether.
// This pins the fix: layout-leaf columns are keyed under a namespaced id so
// they can never be shadowed by an unrelated column sharing their name.
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

// `possession` is a real style element whose layout axis block declares a
// "scale" sub-field, which seeds seen.set('scale', 'layout'). `timeout_flag`
// is layout-only and a bare leaf valued directly under layout, but its own
// key is *also* "scale" -- an unrelated collision with that same name.
const schema = {
  properties: {
    possession: {
      type: 'object',
      'x-style-managed': true,
      properties: { font: { type: 'string' } },
    },
    scale: {
      // The collision: a second element literally named "scale", entirely
      // unrelated to the layout-only leaf below sharing that string.
      type: 'boolean',
    },
    layout: {
      type: 'object',
      properties: {
        possession: { type: 'object', properties: { scale: { type: 'number' } } },
        scale: { type: 'boolean', default: true },
      },
    },
  },
};

const columns = columnsFor(schema);
const leafColumns = columns.filter(function (c) { return c.where === 'layout-leaf'; });

ok('the colliding layout axis sub-field still gets its own column',
   columns.some(function (c) { return c.key === 'scale' && c.where === 'layout'; }));
ok('the layout-only leaf key still gets its own column despite the name collision',
   leafColumns.length === 1 && leafColumns[0].key === 'scale');
ok('the leaf column is distinct from the colliding element/layout column',
   columns.filter(function (c) { return c.key === 'scale'; }).length === 2);

if (failures) { console.log(`\n${failures} failure(s)`); process.exit(1); }
console.log('\nall checks passed');
