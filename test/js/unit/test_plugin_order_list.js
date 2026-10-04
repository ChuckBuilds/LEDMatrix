// The shared plugin order list (widgets/plugin-order-list.js) keeps what it
// does not show.
//
// It lists enabled plugins only, and rewrites its hidden inputs from those
// rows as soon as it has drawn them. A disabled plugin's place in the order
// and its Vegas exclusion used to vanish from the inputs on that rewrite, so
// any later save of the Display or Rotation & Durations tab stored them
// without it: re-enabled, the plugin came back at the end of the rotation and
// scrolling in Vegas again. Runs the shipped widget in a vm with a minimal
// fake DOM -- no jsdom and no server needed, so it runs under
// test/test_js_unit_suites.py too.

const fs = require('fs');
const path = require('path');
const vm = require('vm');
const WIDGET = path.resolve(__dirname, '../../../web_interface/static/v3/js/widgets/plugin-order-list.js');

let pass = 0, fail = 0;
const ok = (label, cond, extra) => cond
  ? (pass++, console.log('  ok   ' + label))
  : (fail++, console.log('  FAIL ' + label + (extra !== undefined ? '  ' + JSON.stringify(extra) : '')));
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

class FakeElement {
  constructor(tag) {
    this.tagName = tag.toUpperCase();
    this.children = [];
    this.parent = null;
    this.dataset = {};
    this.style = {};
    this.className = '';
    this.value = '';
    this.checked = false;
    this.listeners = {};
    this._text = '';
  }
  appendChild(child) {
    if (child.parent) child.parent.children = child.parent.children.filter(c => c !== child);
    child.parent = this;
    this.children.push(child);
    return child;
  }
  insertBefore(child, ref) {
    if (!ref) return this.appendChild(child);
    if (child.parent) child.parent.children = child.parent.children.filter(c => c !== child);
    child.parent = this;
    this.children.splice(this.children.indexOf(ref), 0, child);
    return child;
  }
  get previousElementSibling() {
    const siblings = this.parent ? this.parent.children : [];
    return siblings[siblings.indexOf(this) - 1] || null;
  }
  get nextElementSibling() {
    const siblings = this.parent ? this.parent.children : [];
    const i = siblings.indexOf(this);
    return i < 0 ? null : siblings[i + 1] || null;
  }
  set textContent(value) { this._text = value; this.children = []; }
  get textContent() { return this._text; }
  setAttribute() {}
  focus() {}
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  fire(type, event) { (this.listeners[type] || []).forEach(fn => fn.call(this, event || {})); }
  descendants() { return this.children.flatMap(c => [c, ...c.descendants()]); }
  querySelectorAll(selector) {
    const cls = selector.replace(/^\./, '');
    return this.descendants().filter(e => e.className.split(/\s+/).includes(cls));
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
}

/** Run the widget over `plugins` with the given saved inputs; resolves once it has drawn. */
async function mount({ plugins, order, excluded }) {
  const els = {
    list: new FakeElement('div'),
    order: Object.assign(new FakeElement('input'), { value: JSON.stringify(order) }),
  };
  if (excluded !== undefined) {
    els.excluded = Object.assign(new FakeElement('input'), { value: JSON.stringify(excluded) });
  }
  const context = {
    console,
    window: {},
    document: {
      getElementById: (id) => els[id] || null,
      createElement: (tag) => new FakeElement(tag),
      createTextNode: (text) => new FakeElement('#text'),
    },
    fetch: () => Promise.resolve({
      json: () => Promise.resolve({ status: 'success', data: { plugins } }),
    }),
  };
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(WIDGET, 'utf8'), context);
  context.window.PluginOrderList.init({
    containerId: 'list', orderInputId: 'order',
    excludedInputId: excluded !== undefined ? 'excluded' : undefined,
  });
  await new Promise(resolve => setTimeout(resolve, 0));
  const rows = () => els.list.querySelectorAll('.plugin-order-item');
  return {
    rows,
    rowIds: () => rows().map(r => r.dataset.pluginId),
    order: () => JSON.parse(els.order.value),
    excluded: () => JSON.parse(els.excluded.value),
    row: (id) => rows().find(r => r.dataset.pluginId === id),
  };
}

const PLUGINS = [
  { id: 'weather', name: 'Weather', enabled: true },
  { id: 'clock', name: 'Clock', enabled: false },
  { id: 'stocks', name: 'Stocks', enabled: true },
];

(async () => {
  console.log('\nVegas: a disabled plugin keeps its place and its exclusion');
  {
    const t = await mount({ plugins: PLUGINS, order: ['weather', 'clock', 'stocks'], excluded: ['clock'] });
    ok('only enabled plugins get a row', same(t.rowIds(), ['weather', 'stocks']), t.rowIds());
    ok('drawing the list keeps the disabled plugin in the order, in its place',
       same(t.order(), ['weather', 'clock', 'stocks']), t.order());
    ok('drawing the list keeps its exclusion', same(t.excluded(), ['clock']), t.excluded());

    // Move Stocks up: the rows swap, and Clock stays in its saved slot.
    const up = t.row('stocks').querySelectorAll('.plugin-order-move')[0];
    up.fire('click');
    ok('reordering the rows fills the other slots in the new order',
       same(t.order(), ['stocks', 'clock', 'weather']), t.order());

    const include = t.row('weather').querySelector('.plugin-order-include');
    include.checked = false;
    include.fire('change');
    ok('unchecking a row adds it, and the disabled exclusion stays',
       same([...t.excluded()].sort(), ['clock', 'weather']), t.excluded());
    include.checked = true;
    include.fire('change');
    ok('checking it again removes only that one', same(t.excluded(), ['clock']), t.excluded());
  }

  console.log('\nRotation order: the same, without exclusions');
  {
    const plugins = [
      { id: 'clock', enabled: true },
      { id: 'off', enabled: false },
      { id: 'weather', enabled: true },
      { id: 'new', enabled: true },
    ];
    const t = await mount({ plugins, order: ['clock', 'off', 'weather'] });
    ok('the disabled plugin keeps its slot; a plugin not in the saved order goes last',
       same(t.order(), ['clock', 'off', 'weather', 'new']), t.order());
  }

  console.log('\nOnly what the server would accept is carried over');
  {
    const t = await mount({ plugins: PLUGINS, order: ['weather', 7, 'clock', null, 'clock', 'stocks'],
                            excluded: ['clock', 3, 'clock'] });
    // /config/main refuses a list holding anything but strings, which would
    // block every later Display save; a repeated id is kept once.
    ok('non-string and repeated saved ids are dropped from the order',
       same(t.order(), ['weather', 'clock', 'stocks']), t.order());
    ok('and from the exclusions', same(t.excluded(), ['clock']), t.excluded());
  }

  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
