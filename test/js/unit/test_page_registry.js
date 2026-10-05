// The page lifecycle (web_interface/static/v3/js/core/registry.js).
//
// A converted partial's root carries data-page="<name>"; the registry calls
// the page module's init(root, ctx) once when the root appears and
// destroy(root, ctx) when htmx swaps it away, aborting ctx.signal so every
// listener the page registered with it goes too. This is what replaces the
// inline <script> blocks that htmx-config.js re-ran on every swap.
//
// Imports the shipped ES module directly (js/core/package.json marks the
// directory "type": "module"). The DOM is a minimal shim, so this needs only
// node and runs under test/test_js_unit_suites.py as well as run_all.js.

const path = require('path');
const { pathToFileURL } = require('url');

const CORE = path.resolve(__dirname, '../../../web_interface/static/v3/js/core');

let pass = 0, fail = 0;
const ok = (label, cond, extra) => cond
  ? (pass++, console.log('  ok   ' + label))
  : (fail++, console.log('  FAIL ' + label + (extra !== undefined ? '  -> ' + JSON.stringify(extra) : '')));

// ── DOM shim: just what the registry touches ───────────────────────────────
class El extends EventTarget {
  constructor(tag, attrs = {}) {
    super();
    this.tagName = tag.toUpperCase();
    this.attrs = new Map(Object.entries(attrs));
    this.children = [];
    this.parentNode = null;
  }
  getAttribute(n) { return this.attrs.has(n) ? this.attrs.get(n) : null; }
  setAttribute(n, v) { this.attrs.set(n, String(v)); }
  appendChild(c) { if (c.parentNode) c.remove(); c.parentNode = this; this.children.push(c); return c; }
  remove() { if (this.parentNode) { this.parentNode.children = this.parentNode.children.filter(x => x !== this); this.parentNode = null; } }
  replaceChildren(...nodes) { this.children.slice().forEach(c => c.remove()); nodes.forEach(n => this.appendChild(n)); }
  *descendants() { for (const c of this.children) { yield c; yield* c.descendants(); } }
  // Only the one selector the registry uses: [attr]
  matches(sel) { const m = /^\[([\w-]+)\]$/.exec(sel); return !!m && this.attrs.has(m[1]); }
  querySelectorAll(sel) { return [...this.descendants()].filter(e => e.matches(sel)); }
  contains(other) { for (let n = other; n; n = n.parentNode) if (n === this) return true; return false; }
  get isConnected() { let n = this; while (n.parentNode) n = n.parentNode; return n instanceof Doc; }
}
class Doc extends El {
  constructor() { super('#document'); this.documentElement = this.appendChild(new El('html')); this.body = this.documentElement.appendChild(new El('body')); }
}
const event = (type, detail) => { const e = new Event(type); e.detail = detail; return e; };
// htmx fires its events on the target and they bubble to the document, where
// the registry listens. Node's EventTarget has no tree, so walk it here.
function fire(target, type, detail) {
  for (let n = target; n; n = n.parentNode) n.dispatchEvent(event(type, detail));
}
const tick = () => new Promise(r => setTimeout(r, 0));

// A page module that records its lifecycle, and checks ctx.signal works.
function recorder(log) {
  return {
    init(root, ctx) {
      log.push(['init', root.getAttribute('id'), ctx.name]);
      ctx.state.clicks = 0;
      root.addEventListener('click', () => { ctx.state.clicks++; log.push(['click', root.getAttribute('id')]); }, { signal: ctx.signal });
      ctx.signal.addEventListener('abort', () => log.push(['aborted', root.getAttribute('id')]));
      if (ctx.service) log.push(['service', ctx.service]);
    },
    destroy(root, ctx) { log.push(['destroy', root.getAttribute('id'), ctx.signal.aborted]); },
  };
}

(async () => {
  const { createRegistry, PAGE_ATTRIBUTE } = await import(pathToFileURL(path.join(CORE, 'registry.js')).href);
  const quiet = { error: () => {}, warn: () => {} };

  console.log('\n1. mounts on start, once per root, with the shared context');
  {
    const doc = new Doc();
    const panel = doc.body.appendChild(new El('div', { id: 'cache-content' }));
    const root = panel.appendChild(new El('div', { id: 'a', [PAGE_ATTRIBUTE]: 'demo' }));
    const log = [];
    const reg = createRegistry({ document: doc, context: { service: 'api' }, logger: quiet });
    reg.register('demo', recorder(log));
    await reg.start();
    ok('init ran once on start', log.filter(e => e[0] === 'init').length === 1, log);
    ok('ctx carries the page name', log[0][2] === 'demo', log);
    ok('ctx carries the shared services', log.some(e => e[0] === 'service' && e[1] === 'api'), log);
    await reg.refresh(); await reg.scan(); await reg.mount(root);
    ok('refresh/scan/mount again do not re-init', log.filter(e => e[0] === 'init').length === 1, log);
    root.dispatchEvent(new Event('click'));
    ok('the page listener works', log.filter(e => e[0] === 'click').length === 1, log);
    ok('list() reports the mounted page', reg.list().length === 1 && reg.list()[0].initialised === true, reg.list().length);
  }

  console.log('\n2. an htmx swap destroys the old page and starts the new one');
  {
    const doc = new Doc();
    const panel = doc.body.appendChild(new El('div', { id: 'panel' }));
    const first = panel.appendChild(new El('div', { id: 'first', [PAGE_ATTRIBUTE]: 'demo' }));
    const log = [];
    const reg = createRegistry({ document: doc, logger: quiet });
    reg.register('demo', recorder(log));
    await reg.start();

    for (let i = 0; i < 5; i++) {
      fire(panel, 'htmx:beforeSwap', { target: panel, shouldSwap: true });
      panel.replaceChildren(new El('div', { id: 'swap' + i, [PAGE_ATTRIBUTE]: 'demo' }));
      fire(panel, 'htmx:afterSwap', { target: panel });
      await tick();
    }
    const inits = log.filter(e => e[0] === 'init').map(e => e[1]);
    const destroys = log.filter(e => e[0] === 'destroy').map(e => e[1]);
    ok('one init per swapped-in root', JSON.stringify(inits) === JSON.stringify(['first', 'swap0', 'swap1', 'swap2', 'swap3', 'swap4']), inits);
    ok('one destroy per swapped-out root', JSON.stringify(destroys) === JSON.stringify(['first', 'swap0', 'swap1', 'swap2', 'swap3']), destroys);
    ok('destroy runs before the signal is aborted', log.filter(e => e[0] === 'destroy').every(e => e[2] === false), log);
    ok('every destroyed page had its signal aborted', log.filter(e => e[0] === 'aborted').length === 5, log);
    ok('only the live page is mounted', reg.list().length === 1 && reg.list()[0].root.getAttribute('id') === 'swap4');
    // The old root's listener was registered with ctx.signal: gone.
    first.dispatchEvent(new Event('click'));
    ok('a destroyed page no longer hears its own events', !log.some(e => e[0] === 'click' && e[1] === 'first'), log);
  }

  console.log('\n3. a vetoed swap (shouldSwap false, e.g. an error response) keeps the page');
  {
    const doc = new Doc();
    const panel = doc.body.appendChild(new El('div'));
    panel.appendChild(new El('div', { id: 'keep', [PAGE_ATTRIBUTE]: 'demo' }));
    const log = [];
    const reg = createRegistry({ document: doc, logger: quiet });
    reg.register('demo', recorder(log));
    await reg.start();
    fire(panel, 'htmx:beforeSwap', { target: panel, shouldSwap: false });
    fire(panel, 'htmx:afterSwap', { target: panel });
    ok('not destroyed', !log.some(e => e[0] === 'destroy'), log);
    ok('still mounted', reg.list().length === 1);
  }

  console.log('\n4. a swap elsewhere leaves the page alone');
  {
    const doc = new Doc();
    const a = doc.body.appendChild(new El('div'));
    const b = doc.body.appendChild(new El('div'));
    a.appendChild(new El('div', { id: 'a-page', [PAGE_ATTRIBUTE]: 'demo' }));
    const log = [];
    const reg = createRegistry({ document: doc, logger: quiet });
    reg.register('demo', recorder(log));
    await reg.start();
    fire(b, 'htmx:beforeSwap', { target: b, shouldSwap: true });
    b.replaceChildren(new El('p'));
    fire(b, 'htmx:afterSwap', { target: b });
    ok('the other panel\'s page is untouched', log.filter(e => e[0] !== 'service').map(e => e[0]).join() === 'init', log);
  }

  console.log('\n5. content removed without htmx (Alpine x-if, outerHTML) is swept on the next swap or refresh');
  {
    const doc = new Doc();
    const panel = doc.body.appendChild(new El('div'));
    const root = panel.appendChild(new El('div', { id: 'gone', [PAGE_ATTRIBUTE]: 'demo' }));
    const log = [];
    const reg = createRegistry({ document: doc, logger: quiet });
    reg.register('demo', recorder(log));
    await reg.start();
    root.remove();
    ok('nothing happens until the registry looks', !log.some(e => e[0] === 'destroy'));
    await reg.refresh();
    ok('refresh() destroys a detached root', log.some(e => e[0] === 'destroy' && e[1] === 'gone'), log);
    // loadPartialDirect inserts HTML without htmx events and calls refresh().
    panel.appendChild(new El('div', { id: 'direct', [PAGE_ATTRIBUTE]: 'demo' }));
    await reg.refresh();
    ok('refresh() starts a root inserted without htmx', log.some(e => e[0] === 'init' && e[1] === 'direct'), log);
  }

  console.log('\n6. lazy page modules: loaded on first use, once');
  {
    const doc = new Doc();
    const panel = doc.body.appendChild(new El('div'));
    const log = [];
    let loads = 0;
    const reg = createRegistry({ document: doc, logger: quiet });
    reg.register('lazy', () => { loads++; return Promise.resolve({ default: recorder(log) }); });
    await reg.start();
    ok('not loaded while no partial uses it', loads === 0);
    for (let i = 0; i < 3; i++) {
      fire(panel, 'htmx:beforeSwap', { target: panel, shouldSwap: true });
      panel.replaceChildren(new El('div', { id: 'l' + i, [PAGE_ATTRIBUTE]: 'lazy' }));
      fire(panel, 'htmx:afterSwap', { target: panel });
      await tick(); await tick();
    }
    ok('loader called once', loads === 1, loads);
    ok('a default export works', log.filter(e => e[0] === 'init').length === 3, log);

    // Swapped away while its module is still loading: never initialised.
    let release;
    const slowLog = [];
    reg.register('slow', () => new Promise(r => { release = r; }));
    fire(panel, 'htmx:beforeSwap', { target: panel, shouldSwap: true });
    panel.replaceChildren(new El('div', { id: 's', [PAGE_ATTRIBUTE]: 'slow' }));
    fire(panel, 'htmx:afterSwap', { target: panel });
    fire(panel, 'htmx:beforeSwap', { target: panel, shouldSwap: true });
    panel.replaceChildren(new El('p'));
    fire(panel, 'htmx:afterSwap', { target: panel });
    release(recorder(slowLog));
    await tick(); await tick();
    ok('a page destroyed before its module arrived never runs init', slowLog.length === 0, slowLog);
  }

  console.log('\n7. a page registered after its partial arrived still starts');
  {
    const doc = new Doc();
    doc.body.appendChild(new El('div', { id: 'early', [PAGE_ATTRIBUTE]: 'late' }));
    const log = [];
    const reg = createRegistry({ document: doc, logger: quiet });
    await reg.start();
    reg.register('late', recorder(log));
    await tick();
    ok('init ran on register', log.some(e => e[0] === 'init' && e[1] === 'early'), log);
  }

  console.log('\n8. a failing page is contained');
  {
    const doc = new Doc();
    doc.body.appendChild(new El('div', { id: 'bad', [PAGE_ATTRIBUTE]: 'bad' }));
    doc.body.appendChild(new El('div', { id: 'good', [PAGE_ATTRIBUTE]: 'demo' }));
    const errors = [];
    const log = [];
    const reg = createRegistry({ document: doc, logger: { error: (...a) => errors.push(a.join(' ')), warn() {} } });
    reg.register('bad', { init() { throw new Error('boom'); }, destroy() { log.push(['bad-destroy']); } });
    reg.register('demo', recorder(log));
    await reg.start();
    ok('the error is logged with the page name', errors.length === 1 && /bad/.test(errors[0]), errors);
    ok('the other page still started', log.some(e => e[0] === 'init' && e[1] === 'good'), log);
    reg.stop();
    ok('destroy is not called for a page whose init failed', !log.some(e => e[0] === 'bad-destroy'), log);
    ok('stop() destroys every page', log.some(e => e[0] === 'destroy' && e[1] === 'good') && reg.list().length === 0, log);
  }

  console.log('\n9. register() rejects mistakes loudly');
  {
    const reg = createRegistry({ document: new Doc(), logger: quiet });
    const throws = fn => { try { fn(); return false; } catch (e) { return true; } };
    ok('no name', throws(() => reg.register('', { init() {} })));
    ok('no init and not a loader', throws(() => reg.register('x', {})));
    reg.register('dup', { init() {} });
    ok('a duplicate name', throws(() => reg.register('dup', { init() {} })));
    ok('has()', reg.has('dup') && !reg.has('nope'));
  }

  console.log('\n10. mountContext adds per-mount fields, after the shared ones');
  {
    const doc = new Doc();
    const panel = doc.body.appendChild(new El('div', { id: 'panel' }));
    panel.appendChild(new El('div', { id: 'a', [PAGE_ATTRIBUTE]: 'demo' }));
    const seen = [];
    const made = [];
    const reg = createRegistry({
      document: doc, context: { api: 'shared' }, logger: quiet,
      mountContext(ctx) {
        made.push([ctx.name, ctx.root.getAttribute('id'), !!ctx.signal, ctx.api]);
        return { bound: { root: ctx.root, signal: ctx.signal } };
      },
    });
    reg.register('demo', { init(root, ctx) { seen.push(ctx); } });
    await reg.start();
    await tick();
    ok('mountContext sees the mount\'s name, root, signal and the shared services',
       made.length === 1 && made[0].join() === 'demo,a,true,shared', made);
    ok('...and its fields reach init()', seen.length === 1 && seen[0].bound && seen[0].bound.root === seen[0].root, seen.length);
    fire(panel, 'htmx:beforeSwap', { target: panel, shouldSwap: true });
    panel.replaceChildren(new El('div', { id: 'b', [PAGE_ATTRIBUTE]: 'demo' }));
    fire(panel, 'htmx:afterSwap', { target: panel });
    await tick();
    ok('called again for each new mount, with that mount\'s signal',
       made.length === 2 && seen.length === 2 && !!seen[1].bound && seen[1].bound.signal === seen[1].signal
         && seen[0].signal.aborted, made);

    const errors = [];
    const doc2 = new Doc();
    doc2.body.appendChild(new El('div', { id: 'c', [PAGE_ATTRIBUTE]: 'demo' }));
    const reg2 = createRegistry({ document: doc2, logger: { error: (...a) => errors.push(a.join(' ')) },
                                  mountContext() { throw new Error('boom'); } });
    let started = 0;
    reg2.register('demo', { init() { started++; } });
    await reg2.start();
    await tick();
    ok('a throwing mountContext is logged and the page still starts', started === 1 && errors.length === 1, errors);
  }

  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
