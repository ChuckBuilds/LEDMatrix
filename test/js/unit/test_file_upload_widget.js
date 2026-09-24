// The image list and schedule editor of the file-upload widget
// (web_interface/static/v3/js/widgets/file-upload.js), which owns them since
// plugins_manager.js stopped shipping its own copies.
//
// 1. Upload-supplied strings (file name, id) reach the page as text and data
//    attributes only, and the delete button hands them back intact.
// 2. Card and editor ids use the same rule as plugin_config.html
//    (img_id|replace('.', '_')|replace('-', '_')), so the schedule button of a
//    server-rendered card with a UUID id finds its editor.
// 3. A schedule edit saves to the hidden input and updates the card's summary
//    in place: the open editor is not rebuilt, so what the user just changed
//    stays on screen and keeps focus.
// 4. Re-rendering the list (upload, delete) keeps an open editor open.
//
// Plain node with a minimal DOM shim, like the other unit suites.

const fs = require('fs');
const path = require('path');

const SRC = fs.readFileSync(
  path.resolve(__dirname, '../../../web_interface/static/v3/js/widgets/file-upload.js'), 'utf8');

let pass = 0, fail = 0;
const ok = (label, cond, extra) => cond
  ? (pass++, console.log('  ok   ' + label))
  : (fail++, console.log('  FAIL ' + label + (extra !== undefined ? '  -> ' + JSON.stringify(extra).slice(0, 400) : '')));

// ── DOM shim ───────────────────────────────────────────────────────────────
class ClassList {
  constructor(el) { this.el = el; }
  get list() { return (this.el.className || '').split(/\s+/).filter(Boolean); }
  contains(c) { return this.list.includes(c); }
  add(...cs) { this.el.className = [...new Set([...this.list, ...cs])].join(' '); }
  remove(...cs) { this.el.className = this.list.filter(c => !cs.includes(c)).join(' '); }
}
class El {
  constructor(tag) {
    this.tagName = String(tag).toUpperCase();
    this.children = []; this.parent = null; this.attrs = {}; this.dataset = {}; this.style = {};
    this.className = ''; this.id = ''; this._text = ''; this._html = null; this.listeners = {};
    this.classList = new ClassList(this);
  }
  appendChild(c) { c.parent = this; this.children.push(c); return c; }
  setAttribute(n, v) { this.attrs[n] = String(v); }
  getAttribute(n) { return this.attrs[n]; }
  addEventListener(t, f) { (this.listeners[t] ||= []).push(f); }
  click() { (this.listeners.click || []).forEach(f => f.call(this, { target: this })); }
  set textContent(v) { this.children = []; this._html = null; this._text = String(v); }
  get textContent() { return this._text + this.children.map(c => c.textContent).join(''); }
  // Markup written with innerHTML is kept as a string; the shim never parses it.
  set innerHTML(v) { this.children = []; this._text = ''; this._html = String(v); }
  get innerHTML() {
    if (this._html !== null) return this._html;
    return this._text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }
  *walk() { for (const c of this.children) { yield c; yield* c.walk(); } }
  querySelector(sel) {
    const tests = {
      '[id^="schedule_"]:not(.hidden)': e => e instanceof El && e.id.startsWith('schedule_') && !e.classList.contains('hidden'),
      '.image-schedule-summary': e => e instanceof El && e.classList.contains('image-schedule-summary'),
    };
    if (!tests[sel]) throw new Error('shim: unsupported selector ' + sel);
    for (const e of this.walk()) if (tests[sel](e)) return e;
    return null;
  }
}
class TextNode { constructor(t) { this.textContent = String(t); } *walk() {} }

const root = new El('body');
const docListeners = {};
global.document = {
  createElement: t => new El(t),
  createTextNode: t => new TextNode(t),
  getElementById: id => {
    if (root.id === id) return root;
    for (const e of root.walk()) if (e.id === id) return e;
    return null;
  },
  addEventListener: (t, f) => { (docListeners[t] ||= []).push(f); },
};
global.window = global;
require('../led_escape').install(window);
window.LEDMatrixWidgets = { register() {} };
window.currentPluginConfig = null;
window.getUploadConfig = () => ({ plugin_id: 'static-image' });

// eslint-disable-next-line no-eval
eval(SRC);
window.getUploadConfig = () => ({ plugin_id: 'static-image' });

function mount(fieldId, images) {
  root.children = [];
  const list = root.appendChild(new El('div')); list.id = `${fieldId}_image_list`;
  const hidden = root.appendChild(new El('input')); hidden.id = `${fieldId}_images_data`;
  hidden.value = JSON.stringify(images);
  return { list, hidden };
}
const buttons = card => [...card.walk()].filter(e => e.tagName === 'BUTTON');

const SQ = "x' onmouseover='alert(1)";
const DQ = 'x" onmouseover="alert(1)';
const UUID = '1b2c3d4e-aaaa-bbbb-cccc-123456789abc';

console.log('\n1. upload-supplied strings stay text and data');
{
  const name = '<img src=x onerror=alert(1)>' + DQ + '.png';
  const { list } = mount('f', []);
  window.updateImageList('f', [{ id: SQ, path: 'assets/x".png', filename: DQ, original_filename: name, size: 1 }]);
  const card = list.children[0];
  const all = [...card.walk()];
  ok('no element carries markup written with innerHTML', all.every(e => !(e instanceof El) || e._html === null || e._html === ''),
     all.filter(e => e instanceof El && e._html).map(e => e._html));
  const p = all.find(e => e.tagName === 'P');
  ok('file name is text', p.textContent === name, p.textContent);
  const img = all.find(e => e.tagName === 'IMG');
  ok('alt is the stored filename', img.alt === DQ, img.alt);
  ok('thumbnail loads lazily', img.loading === 'lazy' && img.decoding === 'async', [img.loading, img.decoding]);
  const [sched, del] = buttons(card);
  ok('buttons are labelled for screen readers',
     sched.attrs['aria-label'] === 'Schedule image ' + name && del.attrs['aria-label'] === 'Delete image ' + name,
     [sched.attrs, del.attrs]);
  const calls = [];
  window.deleteUploadedImage = (...a) => calls.push(a);
  del.click();
  ok('delete gets field, image and plugin ids intact',
     calls.length === 1 && calls[0][0] === 'f' && calls[0][1] === SQ && calls[0][2] === 'static-image', calls);
  sched.click();
  const editor = card.children[1];
  ok('editor opens for a hostile id', !editor.classList.contains('hidden'), editor.className);
  ok('editor stores the id escaped', !editor.innerHTML.includes("x' onmouseover") && editor.innerHTML.includes('x&#39; onmouseover'),
     editor.innerHTML.slice(0, 300));
}

console.log('\n2. ids match the server-rendered template');
{
  const { list } = mount('g', [{ id: UUID, path: 'a.png', filename: 'a.png' }]);
  window.updateImageList('g', [{ id: UUID, path: 'a.png', filename: 'a.png' }]);
  const want = UUID.replace(/[.-]/g, '_');
  ok('card id replaces "-" like the template', list.children[0].id === 'img_' + want, list.children[0].id);
  ok('editor id replaces "-" like the template', list.children[0].children[1].id === 'schedule_' + want,
     list.children[0].children[1].id);

  // A card as plugin_config.html renders it: its button passes the raw UUID.
  root.children = [];
  const hidden = root.appendChild(new El('input')); hidden.id = 'g_images_data';
  hidden.value = JSON.stringify([{ id: UUID, path: 'a.png', filename: 'a.png' }]);
  const editor = root.appendChild(new El('div'));
  editor.id = 'schedule_' + want; editor.className = 'hidden mt-3';
  window.openImageSchedule('g', UUID, 0);
  ok('schedule button of a server-rendered card opens its editor',
     !editor.classList.contains('hidden') && editor.innerHTML.includes('Schedule Settings'), editor.className);
}

console.log('\n3. a schedule edit saves without rebuilding the editor');
{
  const images = [{ id: UUID, path: 'a.png', filename: 'a.png' }];
  const { list, hidden } = mount('h', images);
  window.updateImageList('h', images);
  window.openImageSchedule('h', UUID, 0);
  const editor = list.children[0].children[1];
  const before = editor.innerHTML;
  // The shim does not parse the editor markup, so stand in the controls the
  // update functions read.
  const want = UUID.replace(/[.-]/g, '_');
  const check = root.appendChild(new El('input')); check.id = 'schedule_enabled_' + want; check.checked = true;
  const mode = root.appendChild(new El('select')); mode.id = 'schedule_mode_' + want; mode.value = 'time_range';
  window.toggleImageScheduleEnabled('h', UUID, 0);
  window.updateImageScheduleMode('h', UUID, 0);
  const saved = JSON.parse(hidden.value)[0].schedule;
  ok('hidden input holds the new schedule', saved.enabled === true && saved.mode === 'time_range', saved);
  ok('editor was not rebuilt', editor.innerHTML === before && !editor.classList.contains('hidden'));
  const summary = list.children[0].querySelector('.image-schedule-summary');
  ok('card summary updated in place', summary.textContent === '08:00 - 18:00 (daily)', summary.textContent);
}

console.log('\n4. re-rendering the list keeps an open editor open');
{
  const images = [{ id: UUID, path: 'a.png', filename: 'a.png', schedule: { enabled: true, mode: 'per_day', days: {} } },
                  { id: 'other', path: 'b.png', filename: 'b.png' }];
  const { list } = mount('k', images);
  window.updateImageList('k', images);
  window.openImageSchedule('k', UUID, 0);
  window.updateImageList('k', images.slice(0, 1));
  const editor = list.children[0].children[1];
  ok('editor still open after re-render', !editor.classList.contains('hidden'), editor.className);
  ok('editor rebuilt from the saved schedule', editor.innerHTML.includes('value="per_day" selected'));
}

console.log('\n5. the delegated change listener routes editor controls');
{
  const calls = [];
  const saved = window.updateImageScheduleDay;
  window.updateImageScheduleDay = (...a) => calls.push(a);
  const control = new El('input');
  control.dataset = { scheduleControl: 'day', fieldId: SQ, imageId: DQ, imageIdx: '2', day: 'monday' };
  control.closest = () => control;
  (docListeners.change || []).forEach(f => f({ target: control }));
  window.updateImageScheduleDay = saved;
  ok('day control reaches updateImageScheduleDay with its data intact',
     calls.length === 1 && calls[0][0] === SQ && calls[0][1] === DQ && calls[0][2] === 2 && calls[0][3] === 'monday', calls);
}

console.log(`\n${pass} passed, ${fail} failed\n`);
process.exit(fail ? 1 : 0);
