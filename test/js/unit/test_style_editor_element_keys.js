// Regression test for style-editor.js's elementKeys(), extracted verbatim
// from the shipped widget (own/ownObj/elementKeys have no DOM dependency) so
// the test can't drift from the real implementation.
//
// render() in style-editor.js claims the whole `layout` child of a
// customization schema as the widget's own -- removing it from the generic
// fallback renderer entirely, because posting the same offset twice from
// two controls is worse than owning slightly too much. That is only safe
// when the style editor's own table actually renders a row for every key
// declared under `layout`. A hand-written schema can put a key under
// `layout` that never got its own top-level style block -- a logo, a
// timeout indicator, a possession arrow with a position but no font or
// colour -- and elementKeys() is what table() uses to decide which rows to
// draw. Miss one there and the fallback section that used to show it
// disappears with nothing replacing it.
const fs = require('fs');
const path = require('path');
const V3 = path.resolve(__dirname, '../../../web_interface/static/v3');

const src = fs.readFileSync(path.join(V3, 'js/widgets/style-editor.js'), 'utf8');
const start = src.indexOf('function own(obj, key)');
const endMark = 'function titleOf(schema, key)';
const end = src.indexOf(endMark);
if (start === -1 || end === -1) {
  console.error('FAIL: could not locate own()/elementKeys() in style-editor.js -- slice markers need updating');
  process.exit(1);
}
eval(src.slice(start, end)); // defines own/ownObj/elementKeys

let failures = 0;
function ok(desc, cond) {
  if (cond) { console.log(`ok - ${desc}`); }
  else { console.log(`FAIL - ${desc}`); failures++; }
}

// A hand-written schema: score_text has a real style block (and a layout
// offset), home_logo has ONLY a layout offset + scale -- no font/colour of
// its own, the shape _element_block_from_spec never gives a plain
// offsets-only element -- and "possession" lives only under layout, with no
// sibling block in customization.properties at all (the case a hand-authored
// schema can produce that the compact x-style-elements expander cannot).
const schema = {
  properties: {
    score_text: {
      type: 'object',
      'x-style-managed': true,
      properties: {
        font: { type: 'string' },
        text_color: { type: 'array' },
      },
    },
    layout: {
      type: 'object',
      properties: {
        score_text: { type: 'object', properties: { x_offset: {}, y_offset: {} } },
        possession: { type: 'object', properties: { x_offset: {}, y_offset: {} } },
      },
    },
  },
};

const keys = elementKeys(schema);
ok('the declared style element is included', keys.indexOf('score_text') !== -1);
ok('a layout-only key with no style block of its own is still included',
   keys.indexOf('possession') !== -1);
ok('layout and modes containers themselves are never treated as elements',
   keys.indexOf('layout') === -1 && keys.indexOf('modes') === -1);
ok('nothing is duplicated', keys.length === new Set(keys).size);

// A schema with no layout block at all must behave exactly as before --
// this is the overwhelmingly common case and must not gain a phantom row.
const noLayout = {
  properties: {
    score_text: {
      type: 'object',
      'x-style-managed': true,
      properties: { font: { type: 'string' } },
    },
  },
};
ok('a schema without a layout block is unaffected',
   elementKeys(noLayout).join(',') === 'score_text');

// Every layout key already covered by a style element must not be listed
// twice (order: declared elements first, then layout-only extras).
const covered = {
  properties: {
    score_text: {
      type: 'object', 'x-style-managed': true,
      properties: { font: { type: 'string' } },
    },
    home_logo: {
      type: 'object', 'x-style-managed': true,
      properties: {},
    },
    layout: {
      type: 'object',
      properties: {
        score_text: { type: 'object', properties: { x_offset: {} } },
        home_logo: { type: 'object', properties: { x_offset: {}, scale: {} } },
      },
    },
  },
};
ok('a layout key matching an existing element is not appended again',
   elementKeys(covered).join(',') === 'score_text,home_logo');

if (failures) { console.log(`\n${failures} failure(s)`); process.exit(1); }
console.log('\nall checks passed');
