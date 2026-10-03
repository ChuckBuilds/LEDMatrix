// The Backup & Restore tab as a page module
// (static/v3/js/pages/backup-restore.js), in a real DOM (jsdom) with the real
// server-rendered partial and the real API's payload shapes. Built like
// test_cache_page.js:
//
//   * the partial ships no <script> and no onclick; its root is
//     data-page="backup-restore" and its buttons name an action
//   * after five swaps each button makes exactly one request
//   * reads in flight are cancelled by a swap; writes (export, delete,
//     restore) are not, and their result is still reported
//   * file names, host names, plugin ids and messages are shown as text
//   * the five old globals' entry points still work
const http = require('http');
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
  const partial = await get('/partials/backup-restore');
  const realPreview = JSON.parse(await get('/api/v3/backup/preview'));
  const realList = JSON.parse(await get('/api/v3/backup/list'));
  const { createRegistry } = await load('core/registry.js');
  const { createApi } = await load('core/api.js');
  const backupPage = await load('pages/backup-restore.js');

  console.log('\n── Backup & Restore tab: page module (real DOM) ──');
  ok('the partial ships no inline script', !/<script/i.test(partial));
  ok('the partial has no inline handlers', !/onclick=/i.test(partial));
  ok('the partial root is data-page="backup-restore"', /data-page="backup-restore"/.test(partial));
  ok('its buttons name an action', ['export', 'inspect', 'restore', 'cancel', 'refresh']
     .every(a => partial.includes(`data-action="${a}"`)));
  ok('the real preview answers in the shape the page reads', realPreview.status === 'success'
     && ['has_config', 'has_secrets', 'has_wifi', 'user_fonts', 'plugin_uploads', 'plugins'].every(k => k in realPreview.data),
     realPreview);
  ok('the real list answers in the shape the page reads', realList.status === 'success' && Array.isArray(realList.data), realList);

  const HOSTILE = '<img src=x onerror="window.pwned=1">';
  const ZIP = 'ledmatrix-backup-host-20260930.zip';
  const ODD = 'odd name #1 ' + HOSTILE + '.zip';
  const preview = Object.assign({}, realPreview.data, {
    has_config: true, has_secrets: false, has_wifi: true, user_fonts: ['a.bdf', HOSTILE], plugin_uploads: 3, plugins: [1, 2],
  });
  const listing = [
    { filename: ZIP, size: 2048, created_at: '2026-09-30 12:00:00' },
    { filename: ODD, size: 0, created_at: HOSTILE },
  ];
  const manifest = {
    created_at: '2026-09-30T12:00:00', hostname: HOSTILE, ledmatrix_version: '3.8.0',
    detected_contents: ['config', 'secrets'], plugins: [{ plugin_id: 'clock' }, { plugin_id: HOSTILE }],
  };

  const errs = [];
  let navigations = 0;
  const vc = new VirtualConsole();
  vc.on('jsdomError', e => {
    const msg = String(e.message || e).split('\n')[0];
    // jsdom does not navigate; the export's download is that navigation.
    if (/Not implemented: navigation/.test(msg)) { navigations++; return; }
    errs.push(msg);
  });
  vc.on('error', (...a) => errs.push(a.join(' ')));
  const dom = new JSDOM(`<!doctype html><html><body><div id="backup-restore-content">${partial}</div></body></html>`,
    { url: BASE + '/', virtualConsole: vc });
  const { window } = dom;
  const doc = window.document;
  const panel = doc.getElementById('backup-restore-content');

  let mode = 'ok';
  let restoreAnswer = null;
  let aborted = 0;
  const requests = [];
  const pending = [];
  function fakeFetch(url, init) {
    requests.push({ url, method: init.method, body: init.body, signal: init.signal });
    const respond = (status, body) => Promise.resolve({
      status, ok: status >= 200 && status < 300,
      headers: { get: () => null },
      text: () => Promise.resolve(JSON.stringify(body)),
    });
    if (mode === 'hang') {
      return new Promise((resolve, reject) => {
        pending.push(() => resolve(respond(200, { status: 'success', data: [] })));
        if (init.signal) init.signal.addEventListener('abort', () => {
          aborted++;
          const e = new Error('aborted'); e.name = 'AbortError'; reject(e);
        });
      });
    }
    if (url === '/api/v3/backup/preview') return respond(200, { status: 'success', data: preview });
    if (url === '/api/v3/backup/list') return respond(200, { status: 'success', data: listing });
    if (url === '/api/v3/backup/export') return respond(200, { status: 'success', filename: ZIP });
    if (url === '/api/v3/backup/validate') return respond(200, { status: 'success', data: manifest });
    if (url === '/api/v3/backup/restore') return restoreAnswer();
    if (init.method === 'DELETE') return respond(200, { status: 'success' });
    return respond(404, { status: 'error', message: 'unexpected ' + url });
  }
  const notes = [];
  const registry = createRegistry({
    document: doc,
    context: { api: createApi({ fetch: fakeFetch }), notify: (m, t) => notes.push([m, t]) },
  });
  registry.register('backup-restore', backupPage);

  const count = (url, method = 'GET') => requests.filter(r => r.url === url && r.method === method).length;
  const lists = () => count('/api/v3/backup/list');
  const $ = id => doc.getElementById(id);
  const action = a => doc.querySelector(`button[data-action="${a}"]`);
  const hidden = id => $(id).classList.contains('hidden');
  async function swap() {
    panel.dispatchEvent(new window.CustomEvent('htmx:beforeSwap', { bubbles: true, detail: { target: panel, shouldSwap: true } }));
    panel.innerHTML = partial;
    panel.dispatchEvent(new window.CustomEvent('htmx:afterSwap', { bubbles: true, detail: { target: panel } }));
    await tick(20);
  }
  function pick(file) {
    Object.defineProperty($('restore-file-input'), 'files', { value: file ? [file] : [], configurable: true });
    $('restore-file-input').dispatchEvent(new window.Event('change', { bubbles: true }));
  }

  await registry.start();
  await tick(20);

  // ── first load ──────────────────────────────────────────────────────────
  ok('one preview and one list request on start',
     count('/api/v3/backup/preview') === 1 && lists() === 1, requests.map(r => r.url));
  ok('the summary is drawn', /Main config: yes/.test($('export-preview').textContent)
     && /Secrets: no/.test($('export-preview').textContent) && /Plugin image uploads: 3 file\(s\)/.test($('export-preview').textContent),
     $('export-preview').textContent);
  ok('a hostile font name is text', $('export-preview').textContent.includes(HOSTILE));
  const rows = () => doc.querySelectorAll('#backup-history tbody tr');
  ok('one history row per backup', rows().length === 2, rows().length);
  ok('a hostile file name and date are text', rows()[1].textContent.includes(ODD) && rows()[1].textContent.includes(HOSTILE));
  ok('...and created no element', !doc.querySelector('#backup-restore-content img') && !window.pwned);
  ok('download links encode the name', rows()[1].querySelector('a').getAttribute('href')
     === '/api/v3/backup/download/' + encodeURIComponent(ODD), rows()[1].querySelector('a').getAttribute('href'));
  ok('delete buttons carry the exact name and no handler',
     rows()[1].querySelector('button[data-action="delete"]').dataset.filename === ODD
     && !rows()[1].querySelector('button').getAttribute('onclick'));

  // ── repeated swaps ──────────────────────────────────────────────────────
  const oldRefresh = action('refresh');
  for (let i = 0; i < 5; i++) await swap();
  ok('one list request per swap', lists() === 6, lists());
  ok('one mounted page after five swaps', registry.list().length === 1, registry.list().length);
  let before = lists();
  action('refresh').click();
  await tick(20);
  ok('Refresh makes exactly one request (no duplicate listeners)', lists() === before + 1, lists() - before);
  oldRefresh.click();
  await tick(20);
  ok('a swapped-out button does nothing', lists() === before + 1, lists() - before);

  // ── delete ──────────────────────────────────────────────────────────────
  let asked = null;
  window.confirm = msg => { asked = msg; return false; };
  rows()[1].querySelector('button[data-action="delete"]').click();
  await tick(20);
  ok('Delete asks first, naming the file', asked === 'Delete ' + ODD + '?', asked);
  ok('cancel sends nothing', !requests.some(r => r.method === 'DELETE'));
  window.confirm = () => true;
  before = lists();
  rows()[1].querySelector('button[data-action="delete"]').click();
  await tick(20);
  const deletes = requests.filter(r => r.method === 'DELETE');
  ok('one DELETE request, to the encoded name', deletes.length === 1
     && deletes[0].url === '/api/v3/backup/' + encodeURIComponent(ODD), deletes.map(d => d.url));
  ok('the list reloads once', lists() === before + 1, lists() - before);
  ok('the deletion is reported', notes.some(n => n[0] === 'Backup deleted' && n[1] === 'success'), notes);

  // ── export ──────────────────────────────────────────────────────────────
  before = lists();
  action('export').click();
  action('export').click(); // a second click while it is busy
  ok('the export button is busy while it runs', $('export-backup-btn').disabled
     && $('export-backup-btn').textContent.includes('Creating'));
  await tick(30);
  ok('one export request', count('/api/v3/backup/export', 'POST') === 1, count('/api/v3/backup/export', 'POST'));
  ok('the new backup is reported', notes.some(n => n[0] === 'Backup created: ' + ZIP), notes);
  ok('its download starts once', navigations === 1, navigations);
  ok('the list reloads once', lists() === before + 1, lists() - before);
  ok('the button comes back', !$('export-backup-btn').disabled
     && $('export-backup-btn').textContent.trim() === 'Download backup', $('export-backup-btn').textContent);

  // ── inspect ─────────────────────────────────────────────────────────────
  notes.length = 0;
  action('inspect').click();
  await tick(20);
  ok('Inspect with no file asks for one', notes[0] && notes[0][0] === 'Choose a backup file first', notes);
  ok('...and sends nothing', count('/api/v3/backup/validate', 'POST') === 0);
  const file = new window.File(['PK'], 'backup.zip', { type: 'application/zip' });
  pick(file);
  action('inspect').click();
  await tick(20);
  const validate = requests.filter(r => r.url === '/api/v3/backup/validate');
  ok('one validate request', validate.length === 1, validate.length);
  ok('it uploads the file as backup_file', validate[0] && validate[0].body instanceof window.FormData
     && validate[0].body.get('backup_file').name === 'backup.zip');
  ok('the contents are shown', !hidden('restore-preview')
     && /Includes: config, secrets/.test($('restore-preview-body').textContent), $('restore-preview-body').textContent);
  ok('a hostile host name and plugin id are text', $('restore-preview-body').textContent.includes(HOSTILE)
     && !doc.querySelector('#restore-preview-body img'));

  pick(new window.File(['PK2'], 'other.zip'));
  ok('picking another file hides the old contents', hidden('restore-preview'));
  notes.length = 0;
  window.confirm = () => true;
  action('restore').click();
  await tick(20);
  ok('Restore needs an inspected file', notes[0] && notes[0][0] === 'Inspect the file before restoring'
     && count('/api/v3/backup/restore', 'POST') === 0, notes);

  // ── restore ─────────────────────────────────────────────────────────────
  pick(file);
  action('inspect').click();
  await tick(20);
  $('opt-secrets').checked = false;
  restoreAnswer = () => Promise.resolve({
    status: 200, ok: true, headers: { get: () => null },
    text: () => Promise.resolve(JSON.stringify({ status: 'success', data: {
      success: true, restored: ['config', HOSTILE], skipped: ['secrets'], plugins_installed: [], plugins_failed: [], errors: [],
    } })),
  });
  window.confirm = () => false;
  action('restore').click();
  await tick(20);
  ok('Restore asks first', count('/api/v3/backup/restore', 'POST') === 0);
  window.confirm = () => true;
  notes.length = 0;
  action('restore').click();
  await tick(30);
  const restores = requests.filter(r => r.url === '/api/v3/backup/restore');
  ok('one restore request', restores.length === 1, restores.length);
  const options = restores[0] && JSON.parse(restores[0].body.get('options'));
  ok('it sends exactly the six options, as booleans', options && Object.keys(options).sort().join() ===
     'reinstall_plugins,restore_config,restore_fonts,restore_plugin_uploads,restore_secrets,restore_wifi'
     && options.restore_secrets === false && options.restore_config === true, options);
  ok('...with the inspected file', restores[0] && restores[0].body.get('backup_file').name === 'backup.zip');
  ok('a restore is not cancelled by a swap', restores[0] && restores[0].signal === undefined);
  ok('the result is drawn', !hidden('restore-result') && /Restore complete/.test($('restore-result').textContent)
     && /Skipped: secrets/.test($('restore-result').textContent) && /Restart the display service/.test($('restore-result').textContent),
     $('restore-result').textContent);
  ok('a hostile restored name is text', $('restore-result').textContent.includes(HOSTILE) && !doc.querySelector('#restore-result img'));
  ok('...in green, reported once', $('restore-result').classList.contains('bg-green-50')
     && notes.length === 1 && notes[0][0] === 'Restore complete' && notes[0][1] === 'success', notes);
  ok('the restore button comes back', !$('run-restore-btn').disabled && $('run-restore-btn').textContent.trim() === 'Restore now');

  restoreAnswer = () => Promise.resolve({
    status: 500, ok: false, headers: { get: () => null },
    text: () => Promise.resolve(JSON.stringify({ status: 'error', message: 'Restore incomplete — failed: secrets',
                                                 data: { errors: ['secrets'] } })),
  });
  notes.length = 0;
  action('restore').click();
  await tick(30);
  ok('a failed restore reports the server\'s message', notes[0] && notes[0][0] === 'Restore failed: Restore incomplete — failed: secrets'
     && notes[0][1] === 'error', notes);

  action('cancel').click();
  ok('Cancel hides the contents and forgets the file', hidden('restore-preview') && hidden('restore-result')
     && $('restore-file-input').value === '');
  notes.length = 0;
  action('restore').click();
  await tick(20);
  ok('...so Restore asks for an inspection again', notes[0] && notes[0][0] === 'Inspect the file before restoring', notes);

  // ── reads in flight are cancelled by a swap ─────────────────────────────
  mode = 'hang';
  action('refresh').click();
  await tick(5);
  mode = 'ok';
  await swap();
  ok('the swap cancelled the list request', aborted === 1, aborted);
  pending.forEach(resolve => resolve());
  await tick(20);
  ok('the new page drew its own list', rows().length === 2, rows().length);

  // ── the old globals ─────────────────────────────────────────────────────
  before = lists();
  await backupPage.loadBackupList();
  ok('loadBackupList() reloads once', lists() === before + 1, lists() - before);
  notes.length = 0;
  await backupPage.validateRestoreFile();
  ok('validateRestoreFile() inspects', notes[0] && notes[0][0] === 'Choose a backup file first', notes);
  const exportsBefore = count('/api/v3/backup/export', 'POST');
  await backupPage.exportBackup();
  ok('exportBackup() exports once', count('/api/v3/backup/export', 'POST') === exportsBefore + 1);
  ok('clearRestore and runRestore are there', typeof backupPage.clearRestore === 'function'
     && typeof backupPage.runRestore === 'function');

  ok('no DOM errors', errs.length === 0, errs);

  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
