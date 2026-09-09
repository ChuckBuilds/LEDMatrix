// Functional test for ListFilter + the installed-plugins config extracted
// verbatim from plugins_manager.js. Runs under node with a minimal DOM shim.
const fs = require('fs');
const path = require('path');
const V3 = path.resolve(__dirname, '../../../web_interface/static/v3');

// ── minimal DOM shim ───────────────────────────────────────────────────────
class El {
  constructor(id, attrs = {}) {
    this.id = id; this._attrs = { ...attrs }; this.value = '';
    this.textContent = ''; this.innerHTML = ''; this.title = '';
    this._classes = new Set(); this.children = []; this._listeners = {};
    this.parentEl = null;
    const self = this;
    this.classList = {
      add: (...c) => c.forEach(x => self._classes.add(x)),
      remove: (...c) => c.forEach(x => self._classes.delete(x)),
      contains: c => self._classes.has(c),
      toggle: (c, force) => {
        const on = force === undefined ? !self._classes.has(c) : !!force;
        if (on) self._classes.add(c); else self._classes.delete(c);
        return on;
      },
    };
  }
  setAttribute(k, v) { this._attrs[k] = String(v); }
  getAttribute(k) { return k in this._attrs ? this._attrs[k] : null; }
  addEventListener(type, fn) { (this._listeners[type] ||= []).push(fn); }
  fire(type, target) {
    // Real DOM listeners get `this` === the element the listener is bound to;
    // the sort/select handlers rely on that, so mirror it.
    (this._listeners[type] || []).forEach(fn =>
      fn.call(this, { target: target || this, currentTarget: this, key: undefined,
                      preventDefault() {}, stopPropagation() {} }));
  }
  append(child) { child.parentEl = this; this.children.push(child); return child; }
  querySelectorAll(sel) {
    const m = /^\[([^\]=]+)\]$/.exec(sel);
    if (m) return this.children.filter(c => c.getAttribute(m[1]) !== null);
    return [];
  }
  contains(node) { return node === this || this.children.includes(node); }
  closest(sel) {
    const m = /^\[([^\]=]+)\]$/.exec(sel);
    let n = this;
    while (n) { if (m && n.getAttribute(m[1]) !== null) return n; n = n.parentEl; }
    return null;
  }
}

const registry = new Map();
function mk(id, attrs) { const e = new El(id, attrs); if (id) registry.set(id, e); return e; }

global.document = {
  getElementById: id => registry.get(id) || null,
  querySelector: sel => {
    let m = /^#([\w-]+)$/.exec(sel);
    if (m) return registry.get(m[1]) || null;
    m = /^#([\w-]+)\s+\[([\w-]+)="([^"]+)"\]$/.exec(sel);
    if (m) {
      const parent = registry.get(m[1]);
      if (!parent) return null;
      return parent.children.find(c => c.getAttribute(m[2]) === m[3]) || null;
    }
    return null;
  },
  dispatchEvent() {}, addEventListener() {},
};
global.CustomEvent = class { constructor(t, o) { this.type = t; Object.assign(this, o); } };
global.window = global;

// ── build the toolbar exactly as plugins.html declares it ─────────────────
mk('installed-search');
mk('installed-search-clear');
mk('installed-sort');
mk('installed-clear-filters');
mk('installed-count');
mk('installed-updates-count');
mk('installed-plugins-grid');
const pills = mk('installed-filter-pills');
['all', 'enabled', 'disabled', 'updates'].forEach(v => {
  const b = new El(null, { 'data-installed-filter': v });
  pills.append(b);
});
document.getElementById('installed-sort').value = 'a-z';

// ── load the helper ───────────────────────────────────────────────────────
const ListFilter = require(path.join(V3, 'js/plugins/list_filter.js'));
global.ListFilter = ListFilter;

// ── extract the installed-plugins config verbatim from plugins_manager.js ──
const src = fs.readFileSync(path.join(V3, 'plugins_manager.js'), 'utf8');
const start = src.indexOf('function installedSortName(plugin)');
const endMark = 'function setupInstalledFilterListeners()';
const end = src.indexOf(endMark);
if (start < 0 || end < 0) { console.error('FAIL: could not locate installed filter block'); process.exit(1); }
const block = src.slice(start, end);

// deps the block expects from its enclosing IIFE
let rendered = null;
global.installedPlugins = [];
global.pluginLog = () => {};
function renderInstalledCards(list, total) { rendered = { list, total }; }

eval(block);   // defines installedSortName/comparators/getInstalledFilter/etc.

// ── fixture ───────────────────────────────────────────────────────────────
const P = (id, o) => Object.assign(
  { id, name: id, enabled: true, update_available: false, category: 'general',
    description: '', author: 'someone', tags: [], version: '1.0.0', last_updated: null }, o);

const FIXTURE = [
  P('zulu-clock',    { name: 'Zulu Clock',    enabled: true,  category: 'time',      last_updated: '2026-01-05' }),
  P('alpha-weather', { name: 'Alpha Weather', enabled: false, category: 'weather',   last_updated: '2026-03-11', update_available: true, latest_version: '2.0.0' }),
  P('mid-stocks',    { name: 'Mid Stocks',    enabled: true,  category: 'financial', last_updated: null,         update_available: true, latest_version: '3.1.0' }),
  P('no-date',       { name: 'No Date Here',  enabled: false, category: 'general',   last_updated: null }),
  P('taggy',         { name: 'Taggy',         enabled: true,  category: 'media',     last_updated: '2026-06-30', tags: ['hockey', 'nhl'] }),
  P('bad-date',      { name: 'Bad Date',      enabled: true,  category: 'general',   last_updated: 'not-a-date' }),
];
global.installedPlugins = FIXTURE;
window.installedPlugins = FIXTURE;

const ctl = getInstalledFilter();
if (!ctl) { console.error('FAIL: controller not created'); process.exit(1); }
ctl.bind();

// ── assertions ────────────────────────────────────────────────────────────
let pass = 0, fail = 0;
function ok(label, cond, extra) {
  if (cond) { pass++; console.log('  ok   ' + label); }
  else { fail++; console.log('  FAIL ' + label + (extra !== undefined ? '  → ' + JSON.stringify(extra) : '')); }
}
const ids = () => rendered.list.map(p => p.id);
const countText = () => document.getElementById('installed-count').textContent;
const badge = () => document.getElementById('installed-updates-count');
const pillOf = v => pills.children.find(c => c.getAttribute('data-installed-filter') === v);
const setPill = v => pills.fire('click', pillOf(v));

console.log('\n1. default state (no filters)');
ctl.apply();
ok('renders all 6', rendered.list.length === 6, ids());
ok('total is 6', rendered.total === 6);
ok('sorted A→Z', ids().join(',') === 'alpha-weather,bad-date,mid-stocks,no-date,taggy,zulu-clock', ids());
ok('count reads "6 installed"', countText() === '6 installed', countText());
ok('updates badge shows 2', badge().textContent === '2' && !badge().classList.contains('hidden'), badge().textContent);
ok('clear button hidden', document.getElementById('installed-clear-filters').classList.contains('hidden'));

console.log('\n2. pill: enabled / disabled partition');
setPill('enabled');
const enabledIds = ids();
ok('enabled → 4', enabledIds.length === 4, enabledIds);
ok('count reads "4 of 6 shown"', countText() === '4 of 6 shown', countText());
ok('clear button visible', !document.getElementById('installed-clear-filters').classList.contains('hidden'));
ok('enabled pill lit', pillOf('enabled').getAttribute('data-active') === 'true');
ok('all pill unlit', pillOf('all').getAttribute('data-active') === 'false');
ok('aria-pressed set', pillOf('enabled').getAttribute('aria-pressed') === 'true');
setPill('disabled');
const disabledIds = ids();
ok('disabled → 2', disabledIds.length === 2, disabledIds);
ok('partition is exact, no overlap/loss',
   enabledIds.length + disabledIds.length === 6 &&
   !enabledIds.some(i => disabledIds.includes(i)));

console.log('\n3. pill: updates');
setPill('updates');
ok('updates → only update_available', ids().join(',') === 'alpha-weather,mid-stocks', ids());
ok('badge count unaffected by filtering', badge().textContent === '2');

console.log('\n4. search');
setPill('all');
const searchEl = document.getElementById('installed-search');
function search(text) { searchEl.value = text; ctl.setSearch(text); }
search('weather');
ok('name match', ids().join(',') === 'alpha-weather', ids());
search('WEATHER');
ok('case-insensitive', ids().join(',') === 'alpha-weather', ids());
search('nhl');
ok('matches tags[]', ids().join(',') === 'taggy', ids());
search('financial');
ok('matches category', ids().join(',') === 'mid-stocks', ids());
search('someone');
ok('matches author → all 6', ids().length === 6);
search('  zulu  ');
ok('trims whitespace', ids().join(',') === 'zulu-clock', ids());
search('zzzznope');
ok('no match → empty list, total preserved', rendered.list.length === 0 && rendered.total === 6);
ok('count reads "0 of 6 shown"', countText() === '0 of 6 shown', countText());
// The haystack joins fields in the CONFIGURED order (name, id, description,
// author, category, tags), so a query may straddle a field boundary. The
// fixtures deliberately insert `id` before `name`, so reading the object's own
// entry order instead would break this.
search('zulu clock zulu-clock');
ok('phrase spanning name→id matches (field order preserved)',
   ids().join(',') === 'zulu-clock', ids());
search('zulu-clock zulu clock');
ok('the reverse (object key order) does NOT match', rendered.list.length === 0, ids());
search('taggy media');
ok('phrase spanning category→tags respects field order', ids().length === 0, ids());
search('');
ok('cleared → all 6 back', ids().length === 6);
ok('count back to "6 installed"', countText() === '6 installed', countText());

console.log('\n5. search + pill combine (AND)');
setPill('enabled');
search('a');
const both = rendered.list;
ok('all results enabled', both.every(p => p.enabled), ids());
ok('all results match "a"', both.every(p => (p.name + p.id + p.category + p.author).toLowerCase().includes('a')));
search('');
setPill('all');

console.log('\n6. sorts');
const sortEl = document.getElementById('installed-sort');
function sort(v) { sortEl.value = v; sortEl.fire('change'); }
sort('z-a');
ok('z-a reverses', ids().join(',') === 'zulu-clock,taggy,no-date,mid-stocks,bad-date,alpha-weather', ids());
sort('status');
const st = ids();
ok('status: updates first', st.slice(0, 2).sort().join(',') === 'alpha-weather,mid-stocks', st);
ok('status: disabled last', st[st.length - 1] === 'no-date', st);
sort('recent');
const rec = ids();
ok('recent: newest first', rec[0] === 'taggy' && rec[1] === 'alpha-weather' && rec[2] === 'zulu-clock', rec);
ok('recent: missing/unparseable dates last',
   ['bad-date', 'mid-stocks', 'no-date'].every(i => rec.indexOf(i) >= 3), rec);
ok('recent: nothing dropped', rec.length === 6);
sort('category');
const cat = ids();
ok('category groups', cat.join(',') === 'mid-stocks,bad-date,no-date,taggy,zulu-clock,alpha-weather', cat);
sort('a-z');

console.log('\n7. clear filters');
search('taggy'); setPill('updates'); sort('z-a');
ok('3 axes active', ctl.activeCount() === 3, ctl.activeCount());
document.getElementById('installed-clear-filters').fire('click');
ok('reset → all 6', ids().length === 6, ids());
ok('reset clears search input', searchEl.value === '');
ok('reset restores sort to a-z', ctl.state.sort === 'a-z' && sortEl.value === 'a-z');
ok('reset relights All pill', pillOf('all').getAttribute('data-active') === 'true');
ok('activeCount back to 0', ctl.activeCount() === 0);

console.log('\n8. sticky: the just-toggled card must not vanish');
setPill('enabled');
ok('zulu-clock visible while enabled', ids().includes('zulu-clock'));
ctl.sticky.add('zulu-clock');
FIXTURE[0].enabled = false;               // simulate the toggle landing
ctl.apply();
ok('still visible after being disabled', ids().includes('zulu-clock'), ids());
ok('but total unchanged', rendered.total === 6);
setPill('enabled');                        // user touches toolbar → sticky clears
ok('sticky cleared on toolbar use', !ids().includes('zulu-clock'), ids());
FIXTURE[0].enabled = true;
setPill('all');

console.log('\n9. state-leak guard: window.installedPlugins must stay whole');
search('taggy');
ok('grid shows 1', rendered.list.length === 1);
ok('window.installedPlugins still 6', window.installedPlugins.length === 6, window.installedPlugins.length);
search('');

console.log('\n10. updates badge when nothing needs updating');
const noUpd = FIXTURE.map(p => ({ ...p, update_available: false }));
window.installedPlugins = noUpd; global.installedPlugins = noUpd;
ctl.apply();
ok('badge hidden at 0', badge().classList.contains('hidden'));
ok('updates pill dimmed', pillOf('updates').classList.contains('opacity-50'));
ok('pill title explains', /No plugins have updates/.test(pillOf('updates').title), pillOf('updates').title);
window.installedPlugins = FIXTURE; global.installedPlugins = FIXTURE;
ctl.apply();
ok('badge back at 2', badge().textContent === '2' && !badge().classList.contains('hidden'));
ok('pill undimmed', !pillOf('updates').classList.contains('opacity-50'));

console.log('\n11. empty installed list');
window.installedPlugins = []; global.installedPlugins = [];
ctl.apply();
ok('renders nothing, total 0', rendered.list.length === 0 && rendered.total === 0);
ok('count reads "0 installed"', countText() === '0 installed', countText());

console.log('\n12. defensive: missing fields');
const ragged = [{ id: 'bare' }, { id: 'nulls', name: null, tags: null, category: null, last_updated: null }];
window.installedPlugins = ragged; global.installedPlugins = ragged;
sort('recent'); ctl.apply();
ok('no throw on ragged records, both rendered', rendered.list.length === 2, ids());
search('bare');
ok('search works on ragged records', ids().join(',') === 'bare', ids());

console.log(`\n${pass} passed, ${fail} failed\n`);
process.exit(fail ? 1 : 0);
