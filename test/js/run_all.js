#!/usr/bin/env node
// Runs the web-interface JS suites.
//
//   node run_all.js                        unit suites, plus DOM suites if a
//                                          web interface is reachable
//   BASE=http://10.0.10.169:5000 node run_all.js   point the DOM suites at a rig
//
// Unit suites need nothing but node. The DOM suites need `npm install` (jsdom)
// and a running web interface, because they deliberately test against the real
// server-rendered HTML and the real API rather than fixtures.
const { spawnSync } = require('child_process');
const http = require('http');
const path = require('path');
const fs = require('fs');

const BASE = process.env.BASE || 'http://localhost:5000';
const UNIT = ['unit/test_list_filter.js', 'unit/test_render_cards.js'];
const DOM = ['dom/test_installed_dom.js', 'dom/test_store_dom.js', 'dom/test_no_double_fetch.js',
             'dom/test_tools_sections.js'];

function reachable(url) {
  return new Promise(res => {
    const req = http.get(url, r => { r.resume(); res(r.statusCode < 500); });
    req.on('error', () => res(false));
    req.setTimeout(4000, () => { req.destroy(); res(false); });
  });
}

function run(file) {
  const r = spawnSync(process.execPath, [path.join(__dirname, file)],
                      { stdio: 'inherit', cwd: __dirname, env: process.env });
  return r.status === 0;
}

(async () => {
  const results = [];
  for (const f of UNIT) results.push([f, run(f)]);

  const haveJsdom = fs.existsSync(path.join(__dirname, 'node_modules', 'jsdom'));
  const up = await reachable(BASE + '/');

  if (!haveJsdom) {
    console.log(`\nSKIPPING DOM suites: jsdom not installed (run: npm install)\n`);
  } else if (!up) {
    console.log(`\nSKIPPING DOM suites: no web interface reachable at ${BASE}`);
    console.log(`  start one with: EMULATOR=true python3 web_interface/app.py`);
    console.log(`  or point at a rig: BASE=http://<host>:5000 node run_all.js\n`);
  } else {
    console.log(`\nDOM suites against ${BASE}\n`);
    for (const f of DOM) results.push([f, run(f)]);
  }

  const failed = results.filter(([, ok]) => !ok);
  console.log('\n─── summary ───');
  results.forEach(([f, ok]) => console.log(`  ${ok ? 'PASS' : 'FAIL'}  ${f}`));
  if (failed.length) { console.log(`\n${failed.length} suite(s) failed\n`); process.exit(1); }
  console.log('\nall suites passed\n');
})();
