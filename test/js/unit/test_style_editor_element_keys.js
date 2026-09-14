// Regression test for how style-editor.js decides which rows to draw,
// extracted verbatim from the shipped widget (own/ownObj/elementKeys/
// styleRows/positionRows have no DOM dependency) so the test can't drift from
// the real implementation.
//
// render() claims the whole `layout` child of a customization schema as the
// widget's own -- removing it from the generic fallback renderer entirely,
// because posting the same offset twice from two controls is worse than
// owning slightly too much. That is only safe when every entry under `layout`
// gets exactly one row:
//
//   * styleRows(): one per style element, paired with the layout entry that
//     holds its offsets. The pairing is not by exact name -- a hand-written
//     schema styles 'score_text' but positions 'score' -- so core records it
//     as x-layout-key, resolved through the same alias map the renderer reads
//     offsets with.
//   * positionRows(): one per layout entry no style element claimed -- a logo,
//     a timeout indicator, a possession arrow, or a bare leaf like a
//     show_logo toggle.
//
// This contract replaced an earlier one in which elementKeys() itself
// appended layout-only keys. That compared names exactly, so an aliased entry
// ('score' for 'score_text') was listed a second time and its offsets got two
// controls posting the same field.
const fs = require('fs');
const path = require('path');
const V3 = path.resolve(__dirname, '../../../web_interface/static/v3');

const src = fs.readFileSync(path.join(V3, 'js/widgets/style-editor.js'), 'utf8');
const start = src.indexOf('function own(obj, key)');
const endMark = 'function control(opts)';
const end = src.indexOf(endMark);
if (start === -1 || end === -1) {
  console.error('FAIL: could not locate own()..positionRows() in style-editor.js -- slice markers need updating');
  process.exit(1);
}
eval(src.slice(start, end)); // defines own/ownObj/elementKeys/styleRows/positionRows

let failures = 0;
function ok(desc, cond) {
  if (cond) { console.log(`ok - ${desc}`); }
  else { console.log(`FAIL - ${desc}`); failures++; }
}

function layoutKeysOf(rows) { return rows.map(function (r) { return r.layoutKey; }); }

// score_text is styled and positioned under an alias; odds_text has no
// offsets at all; possession is positioned but never styled; show_logo is a
// bare leaf under layout.
const schema = {
  properties: {
    score_text: {
      type: 'object', 'x-style-managed': true, 'x-layout-key': 'score',
      properties: { font: { type: 'string' }, text_color: { type: 'array' } },
    },
    odds_text: {
      type: 'object', 'x-style-managed': true,
      properties: { font: { type: 'string' } },
    },
    layout: {
      type: 'object',
      'x-propertyOrder': ['score', 'possession', 'show_logo'],
      properties: {
        score: { type: 'object', properties: { x_offset: {}, y_offset: {} } },
        possession: { type: 'object', properties: { x_offset: {}, y_offset: {} } },
        show_logo: { type: 'boolean', default: true },
      },
    },
  },
};

const keys = elementKeys(schema);
ok('elementKeys lists the style elements', keys.join(',') === 'score_text,odds_text');
ok('layout and modes containers are never treated as elements',
   keys.indexOf('layout') === -1 && keys.indexOf('modes') === -1);

const styled = styleRows(schema);
ok('a style element is paired with the layout entry core resolved for it',
   styled[0].key === 'score_text' && styled[0].layoutKey === 'score');
ok('a style element with no offsets claims nothing', styled[1].layoutKey === null);

const positions = positionRows(schema);
ok('a layout-only object entry gets a position row',
   layoutKeysOf(positions).indexOf('possession') !== -1);
ok('a layout-only leaf gets a position row too',
   layoutKeysOf(positions).indexOf('show_logo') !== -1);
ok('an entry claimed through an alias is not listed again as a position',
   layoutKeysOf(positions).indexOf('score') === -1);
ok('positions follow the declared order',
   layoutKeysOf(positions).join(',') === 'possession,show_logo');

const every = layoutKeysOf(styled).filter(Boolean).concat(layoutKeysOf(positions));
ok('every layout entry gets exactly one row',
   every.length === new Set(every).size
   && every.slice().sort().join(',') === ['possession', 'score', 'show_logo'].join(','));

// A schema with no layout block at all must behave exactly as before --
// the overwhelmingly common case must not gain a phantom row.
const noLayout = {
  properties: {
    score_text: {
      type: 'object', 'x-style-managed': true,
      properties: { font: { type: 'string' } },
    },
  },
};
ok('a schema without a layout block has no positions',
   elementKeys(noLayout).join(',') === 'score_text' && positionRows(noLayout).length === 0);

// Without an annotation (the compact declaration, or a schema that never went
// through expansion) the two blocks share one key.
const unannotated = {
  properties: {
    home_logo: { type: 'object', 'x-style-managed': true, properties: {} },
    layout: {
      type: 'object',
      properties: { home_logo: { type: 'object', properties: { x_offset: {}, scale: {} } } },
    },
  },
};
ok('an exact-name layout entry is claimed without an annotation',
   styleRows(unannotated)[0].layoutKey === 'home_logo'
   && positionRows(unannotated).length === 0);

if (failures) { console.log(`\n${failures} failure(s)`); process.exit(1); }
console.log('\nall checks passed');
