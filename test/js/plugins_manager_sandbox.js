// The whole of plugins_manager.js (and list_filter.js before it, as the page
// loads them), evaluated in a node vm context against a small fake DOM.
//
// For suites that drive the plugin manager's real flows -- install, polling,
// store filters, the GitHub-URL button -- rather than one function sliced
// out of the file. Nothing is mocked inside the script: only what the page
// gives it (document, fetch, timers, showNotification, LEDEscape).
//
//   const sb = create({ route: (method, url, body) => ({ status, json }) });
//   sb.el('plugin-store-grid');          // make an element exist by id
//   sb.window.installPlugin('weather');
//   await sb.until(() => sb.requests.some(r => r.url.includes('/toggle')));
//
// Timers ignore their delays and run on the next turn, so a poll loop that
// would take minutes in a browser finishes in milliseconds. The page is in
// readyState "loading" with no #installed-plugins-grid, so the script's own
// start-up does nothing until a suite asks for it (window.initPluginsPage()).
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const ledEscape = require('./led_escape');

const V3 = path.resolve(__dirname, '../../web_interface/static/v3');
const PLUGINS_HTML = path.resolve(__dirname, '../../web_interface/templates/v3/partials/plugins.html');

class FakeClassList {
  constructor() { this.set = new Set(); }
  add(...c) { c.forEach(x => this.set.add(x)); }
  remove(...c) { c.forEach(x => this.set.delete(x)); }
  contains(c) { return this.set.has(c); }
  toggle(c, force) {
    const on = force === undefined ? !this.set.has(c) : !!force;
    if (on) this.set.add(c); else this.set.delete(c);
    return on;
  }
}

function create({ route } = {}) {
  const elements = new Map();
  const requests = [];
  const toasts = [];
  const errors = [];
  const restartNotes = [];

  class FakeElement {
    constructor(id, tag = 'div', attributes = {}) {
      this.id = id;
      this.tagName = tag.toUpperCase();
      this.attributes = { ...attributes };
      this.listeners = {};
      this.children = [];
      this.classList = new FakeClassList();
      this.style = { removeProperty() {} };
      this.dataset = {};
      this.value = '';
      this.textContent = '';
      this.disabled = false;
      this.parentNode = null;
      this._html = '';
    }
    get innerHTML() { return this._html; }
    set innerHTML(v) { this._html = String(v); this.children = []; }
    getAttribute(n) { return n in this.attributes ? this.attributes[n] : null; }
    setAttribute(n, v) { this.attributes[n] = String(v); }
    hasAttribute(n) { return n in this.attributes; }
    removeAttribute(n) { delete this.attributes[n]; }
    addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); }
    removeEventListener(type, fn) {
      this.listeners[type] = (this.listeners[type] || []).filter(f => f !== fn);
    }
    appendChild(child) { this.children.push(child); child.parentNode = this; return child; }
    querySelector() { return null; }
    querySelectorAll() { return []; }
    closest() { return null; }
    cloneNode() {
      const copy = new FakeElement(this.id, this.tagName, this.attributes);
      copy._html = this._html;
      copy.value = this.value;
      return copy;
    }
    replaceChild(next, prev) {
      next.parentNode = this;
      prev.parentNode = null;
      if (next.id) elements.set(next.id, next);
      return prev;
    }
    replaceWith(next) { if (this.parentNode) this.parentNode.replaceChild(next, this); }
    // A browser runs an inline on<type> attribute first (it was set before
    // any listener was added), then the listeners, and an exception in one
    // does not stop the next: it is reported, which is what `errors` holds.
    dispatch(type, init = {}) {
      const event = {
        type, target: this, currentTarget: this, key: init.key,
        defaultPrevented: false,
        preventDefault() { this.defaultPrevented = true; },
        stopPropagation() {}, stopImmediatePropagation() {},
      };
      const inline = this.getAttribute('on' + type);
      const handlers = [];
      if (inline !== null) {
        handlers.push(vm.runInContext(`(function(event) {\n${inline}\n})`, ctx));
      }
      handlers.push(...(this.listeners[type] || []));
      for (const h of handlers) {
        try { h.call(this, event); } catch (e) { errors.push(e); }
      }
      return event;
    }
    click() { return this.dispatch('click'); }
  }

  function el(id, tag, attributes) {
    if (!elements.has(id)) {
      const parent = new FakeElement(null);
      parent.appendChild(new FakeElement(id, tag, attributes));
      elements.set(id, parent.children[0]);
    }
    return elements.get(id);
  }

  const timers = [];
  const ctx = {
    // Warnings are the script noting elements this fake page doesn't have.
    console: { log: console.log.bind(console), error: console.error.bind(console),
               warn: () => {}, info: () => {}, debug: () => {} },
    debugLog: () => {},
    addEventListener() {},
    document: {
      readyState: 'loading',
      body: { addEventListener() {} },
      getElementById: id => elements.get(id) || null,
      querySelector: () => null,
      querySelectorAll: () => [],
      addEventListener() {},
      dispatchEvent() { return true; },
      createElement: tag => new FakeElement(null, tag),
    },
    CustomEvent: class { constructor(type, init) { this.type = type; this.detail = init && init.detail; } },
    setTimeout: (fn, _ms, ...args) => { timers.push(setImmediate(() => fn(...args))); return timers.length; },
    clearTimeout: () => {},
    setInterval: () => 0,
    clearInterval: () => {},
    requestAnimationFrame: fn => setImmediate(fn),
    getComputedStyle: () => ({ display: 'block' }),
    scrollTo() {},
    sessionStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    confirm: () => true,
    alert: () => {},
    showNotification: (message, type) => {
      toasts.push({ message: String(message),
                    type: type && typeof type === 'object' ? type.type : type });
    },
    noteRestartRequired: (body) => { restartNotes.push(body); },
    fetch: async (url, opts = {}) => {
      const method = (opts.method || 'GET').toUpperCase();
      let body = null;
      try { body = opts.body ? JSON.parse(opts.body) : null; } catch (e) { body = opts.body; }
      requests.push({ method, url: String(url), body });
      const answer = (route && route(method, String(url), body)) || { status: 200, json: { status: 'success' } };
      const status = answer.status || 200;
      return { ok: status < 400, status, json: async () => answer.json };
    },
  };
  ctx.window = ctx;
  vm.createContext(ctx);
  ledEscape.install(ctx);
  for (const file of ['js/plugins/list_filter.js', 'plugins_manager.js']) {
    vm.runInContext(fs.readFileSync(path.join(V3, file), 'utf8'), ctx, { filename: file });
  }

  // Resolves once cond() is true, letting timers and promises run between
  // checks; rejects if it never is.
  async function until(cond, label = 'condition', turns = 20000) {
    for (let i = 0; i < turns; i++) {
      if (cond()) return;
      await new Promise(r => setImmediate(r));
    }
    throw new Error('timed out waiting for ' + label);
  }

  // Lets every pending timer and promise run.
  async function settle(turns = 50) {
    for (let i = 0; i < turns; i++) await new Promise(r => setImmediate(r));
  }

  return { window: ctx, el, FakeElement, requests, toasts, errors, restartNotes, until, settle };
}

// The attributes of the element with this id in partials/plugins.html, as
// the template ships them (no Jinja on the tags these suites read).
function templateAttributes(id) {
  const html = fs.readFileSync(PLUGINS_HTML, 'utf8');
  const at = html.indexOf(`id="${id}"`);
  if (at < 0) throw new Error(`no element with id ${id} in plugins.html`);
  const start = html.lastIndexOf('<', at);
  let end = start, quote = null;
  for (; end < html.length; end++) {
    const ch = html[end];
    if (quote) { if (ch === quote) quote = null; } else if (ch === '"' || ch === "'") quote = ch;
    else if (ch === '>') break;
  }
  const tag = html.slice(start, end + 1);
  const attrs = {};
  const re = /([\w:-]+)\s*=\s*("([^"]*)"|'([^']*)')/g;
  let m;
  while ((m = re.exec(tag))) attrs[m[1]] = m[3] !== undefined ? m[3] : m[4];
  return { tag: tag.match(/^<(\w+)/)[1], attrs, source: tag };
}

module.exports = { create, templateAttributes };
