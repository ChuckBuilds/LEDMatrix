// The Schedule tab as a page module (static/v3/js/pages/schedule.js), in a
// real DOM (jsdom) with the real server-rendered partial and the real
// widget registry and schedule-picker widget. Built like test_cache_page.js:
//
//   * the partial ships no <script> and no inline handlers; its root is
//     data-page="schedule" and carries both saved schedules as JSON
//   * both pickers are drawn once per swap-in, from the saved config, however
//     many swaps came first
//   * each form's save is reported in exactly one notification (the forms
//     are marked data-reports-result so app.js stays quiet)
//   * the dim brightness label follows the slider
//   * a widget that loads late is waited for, and a page swapped away while
//     waiting draws nothing
//   * the old globals' entry points still work
const http = require('http');
const fs = require('fs');
const path = require('path');
const { pathToFileURL } = require('url');
const { JSDOM, VirtualConsole } = require('jsdom');

const BASE = process.env.BASE || 'http://localhost:5000';
const JS = path.resolve(__dirname, '../../../web_interface/static/v3/js');
const get = p => new Promise((res, rej) =>
  http.get(BASE + p, r => { let d = ''; r.on('data', c => d += c); r.on('end', () => res(d)); }).on('error', rej));
const load = f => import(pathToFileURL(path.join(JS, f)).href);
const tick = ms => new Promise(r => setTimeout(r, ms || 0));

let pass = 0, fail = 0;
const ok = (l, c, x) => c ? (pass++, console.log('  ok   ' + l))
  : (fail++, console.log('  FAIL ' + l + (x !== undefined ? '  -> ' + JSON.stringify(x).slice(0, 300) : '')));

(async () => {
  const partial = await get('/partials/schedule');
  const { createRegistry } = await load('core/registry.js');
  const schedulePage = await load('pages/schedule.js');

  console.log('\n── Schedule tab: page module (real DOM) ──');
  ok('the partial ships no inline script', !/<script/i.test(partial));
  ok('the partial has no inline handlers', !/\son(click|input|change|submit)=/i.test(partial));
  ok('the forms carry no hx-on handler', !/hx-on/i.test(partial));
  ok('the partial root is data-page="schedule"', /data-page="schedule"/.test(partial));
  ok('both forms are marked data-reports-result',
     (partial.match(/<form[^>]*data-reports-result/g) || []).length === 2);

  const errs = [];
  const logged = [];
  const vc = new VirtualConsole();
  vc.on('jsdomError', e => errs.push(String(e.message || e).split('\n')[0]));
  vc.on('error', (...a) => logged.push(a.join(' ')));
  const dom = new JSDOM(`<!doctype html><html><body><div id="schedule-content">${partial}</div></body></html>`,
    { url: BASE + '/', virtualConsole: vc, runScripts: 'outside-only' });
  const { window } = dom;
  const doc = window.document;
  const panel = doc.getElementById('schedule-content');
  // base.html defines LEDEscape (app-early.js) before any tab loads; the
  // widget escapes with it.
  require('../led_escape').install(window);
  window.eval(fs.readFileSync(path.join(JS, 'widgets/registry.js'), 'utf8'));
  window.eval(fs.readFileSync(path.join(JS, 'widgets/schedule-picker.js'), 'utf8'));
  const widgets = window.LEDMatrixWidgets;
  ok('the widget scripts register schedule-picker', !!(widgets && widgets.get('schedule-picker')));

  const notes = [];
  const registry = createRegistry({
    document: doc,
    context: { api: null, notify: (m, t) => notes.push([m, t]) },
  });
  registry.register('schedule', schedulePage);

  const $ = id => doc.getElementById(id);
  const root = () => doc.querySelector('[data-page="schedule"]');
  const saved = key => JSON.parse(root().dataset[key]);
  const drawn = () => ['schedule_picker_container', 'dim_schedule_picker_container']
    .map(id => $(id).querySelectorAll('.schedule-picker-widget').length);
  async function swap(html) {
    panel.dispatchEvent(new window.CustomEvent('htmx:beforeSwap', { bubbles: true, detail: { target: panel, shouldSwap: true } }));
    panel.innerHTML = html === undefined ? partial : html;
    panel.dispatchEvent(new window.CustomEvent('htmx:afterSwap', { bubbles: true, detail: { target: panel } }));
    await tick(20);
  }
  function answer(formId, xhr) {
    $(formId).dispatchEvent(new window.CustomEvent('htmx:afterRequest', {
      bubbles: true, detail: { xhr, elt: $(formId), successful: xhr.status < 300 } }));
  }

  await registry.start();
  await tick(20);

  // ── first load ──────────────────────────────────────────────────────────
  ok('both pickers drawn once', drawn().join() === '1,1', drawn());
  const schedule = saved('scheduleConfig');
  const dim = saved('dimScheduleConfig');
  ok('the saved config reaches the page as JSON', schedule && typeof schedule === 'object' && dim && typeof dim === 'object');
  const mode = cfg => cfg.mode ? cfg.mode.replace('-', '_') : (cfg.days ? 'per_day' : 'global');
  ok('the display picker shows the saved mode', $('schedule_mode_value').value === mode(schedule),
     [$('schedule_mode_value').value, schedule.mode]);
  ok('the dim picker shows the saved mode', $('dim_schedule_mode_value').value === mode(dim),
     [$('dim_schedule_mode_value').value, dim.mode]);
  ok('the dim picker shows the saved start time',
     $('dim_schedule_start_time_hidden').value === (dim.start_time || '20:00'), $('dim_schedule_start_time_hidden').value);

  // ── repeated swaps ──────────────────────────────────────────────────────
  for (let i = 0; i < 5; i++) await swap();
  ok('one mounted page after five swaps', registry.list().length === 1, registry.list().length);
  ok('each picker drawn once, not stacked', drawn().join() === '1,1', drawn());

  const okXhr = body => ({ status: 200, responseText: JSON.stringify(body) });
  answer('schedule_form', okXhr({ status: 'success', message: 'Schedule configuration saved successfully' }));
  ok('a schedule save is reported once', notes.length === 1, notes);
  ok('...with the server message and status',
     notes[0] && notes[0][0] === 'Schedule configuration saved successfully' && notes[0][1] === 'success', notes[0]);
  answer('dim_schedule_form', okXhr({ status: 'success' }));
  ok('a dim schedule save without a message says so',
     notes.length === 2 && notes[1][0] === 'Dim schedule settings saved' && notes[1][1] === 'success', notes[1]);
  answer('schedule_form', { status: 400, responseText: JSON.stringify({ status: 'error' }) });
  ok('a refused save without a message says so',
     notes.length === 3 && notes[2][0] === 'Error saving schedule' && notes[2][1] === 'error', notes[2]);
  answer('dim_schedule_form', { status: 502, responseText: '<html>Bad gateway</html>' });
  ok('a non-JSON answer is an error',
     notes.length === 4 && notes[3][0] === 'Invalid response from server' && notes[3][1] === 'error', notes[3]);
  answer('schedule_form', { status: 200, responseText: 'null' });
  ok('a JSON null answer is an error, not a crash',
     notes.length === 5 && notes[4][1] === 'error', notes[4]);
  // An htmx request from elsewhere on the page (outside both forms) is not a save.
  root().querySelector('.settings-filter').dispatchEvent(new window.CustomEvent('htmx:afterRequest', {
    bubbles: true, detail: { xhr: okXhr({ status: 'success', message: 'x' }) } }));
  ok('a request from outside the two forms reports nothing', notes.length === 5, notes.length);

  // ── the brightness label ────────────────────────────────────────────────
  $('dim_brightness').value = '42';
  $('dim_brightness').dispatchEvent(new window.Event('input', { bubbles: true }));
  ok('the dim brightness label follows the slider', $('dim_brightness_display').textContent === '42%',
     $('dim_brightness_display').textContent);

  // ── the widget loads late ───────────────────────────────────────────────
  delete window.LEDMatrixWidgets;
  await swap();
  ok('nothing drawn while the widget is missing', drawn().join() === '0,0', drawn());
  window.LEDMatrixWidgets = widgets;
  await tick(150);
  ok('drawn once the widget arrives', drawn().join() === '1,1', drawn());

  delete window.LEDMatrixWidgets;
  await swap();
  const kept = root();
  await swap('<p>another tab</p>');
  window.LEDMatrixWidgets = widgets;
  await tick(250);
  ok('a page swapped away while waiting draws nothing',
     kept.querySelectorAll('.schedule-picker-widget').length === 0);
  ok('nothing left mounted', registry.list().length === 0, registry.list().length);

  // ── the old globals ─────────────────────────────────────────────────────
  await swap();
  const before = notes.length;
  schedulePage.handleScheduleResponse({ target: $('schedule_form'), detail: { xhr: okXhr({ status: 'success' }) } });
  schedulePage.handleDimScheduleResponse({ target: $('dim_schedule_form'), detail: { xhr: okXhr({ status: 'success' }) } });
  ok('handleScheduleResponse(event) and handleDimScheduleResponse(event) report once each',
     notes.length === before + 2 && notes[before][0] === 'Schedule settings saved'
       && notes[before + 1][0] === 'Dim schedule settings saved', notes.slice(before));

  ok('no console errors', logged.length === 0, logged);
  ok('no DOM errors', errs.length === 0, errs);

  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
