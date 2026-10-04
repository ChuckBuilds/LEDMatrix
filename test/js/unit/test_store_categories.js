// The Plugin Store's category filter offers the categories its plugins have.
//
// The template listed seven fixed categories. The registry uses about
// twenty (productivity, utility, transit, finance, ...), so roughly a third
// of the store could not be filtered to at all, and "Financial" missed the
// plugin filed under "finance". The options are now built from the store's
// plugins, as the Starlark section builds its own; the template ships only
// "All Categories".
//
// Runs the whole of plugins_manager.js in the sandbox.

const fs = require('fs');
const path = require('path');
const { create, templateAttributes } = require('../plugins_manager_sandbox');

let pass = 0, fail = 0;
const ok = (label, cond, extra) => cond
  ? (pass++, console.log('  ok   ' + label))
  : (fail++, console.log('  FAIL ' + label + (extra !== undefined ? '  -> ' + JSON.stringify(extra).slice(0, 400) : '')));

const STORE = [
  { id: 'nfl', name: 'NFL', category: 'sports' },
  { id: 'nba', name: 'NBA', category: 'Sports' },
  { id: 'todo', name: 'Todo', category: 'productivity' },
  { id: 'stocks', name: 'Stocks', category: 'finance' },
  { id: 'crypto', name: 'Crypto', category: 'financial' },
  { id: 'bus', name: 'Bus', category: 'transit' },
  { id: 'mystery', name: 'Mystery' },
];

function route(method, url) {
  if (url.startsWith('/api/v3/plugins/store/list')) return { json: { status: 'success', data: { plugins: STORE } } };
  if (url.startsWith('/api/v3/plugins/installed')) return { json: { status: 'success', data: { plugins: [] } } };
  if (url.startsWith('/api/v3/plugins/store/github-status')) {
    return { json: { status: 'success', data: { token_status: 'valid', authenticated: true, rate_limit: 5000 } } };
  }
  if (url.startsWith('/api/v3/plugins/saved-repositories')) {
    return { json: { status: 'success', data: { repositories: [] } } };
  }
  if (url.startsWith('/api/v3/display/on-demand/status')) {
    return { json: { status: 'success', data: { state: {}, service: {} } } };
  }
  return { json: { status: 'success' } };
}

const options = sel => sel.children.map(o => o.value);
const cardIds = sb => [...sb.el('plugin-store-grid').innerHTML.matchAll(/<h4[^>]*>([^<]*)<\/h4>/g)].map(m => m[1]);

(async () => {
  console.log('\nthe template');
  {
    const html = fs.readFileSync(path.resolve(__dirname,
      '../../../web_interface/templates/v3/partials/plugins.html'), 'utf8');
    const start = html.indexOf('<select id="plugin-category"');
    const block = html.slice(start, html.indexOf('</select>', start));
    const shipped = [...block.matchAll(/<option value="([^"]*)"/g)].map(m => m[1]);
    ok('ships only "All Categories"', JSON.stringify(shipped) === JSON.stringify(['']), shipped);
  }

  const sb = create({ route });
  const attrs = templateAttributes('plugin-category').attrs;
  const select = sb.el('plugin-category', 'select', attrs);
  sb.el('plugin-store-grid');
  sb.el('installed-plugins-grid');
  sb.window.initPluginsPage();
  await sb.until(() => sb.requests.some(r => r.url.startsWith('/api/v3/plugins/store/list')), 'the store list');
  await sb.settle();

  console.log('\noptions come from the store\'s plugins');
  ok('"All Categories" is still the first choice', /<option value="">All Categories<\/option>/.test(select.innerHTML),
     select.innerHTML);
  ok('every category a plugin has is offered once, whatever its case',
     JSON.stringify(options(select)) === JSON.stringify(['finance', 'financial', 'productivity', 'sports', 'transit']),
     options(select));
  ok('labels are capitalised',
     (select.children.find(o => o.value === 'productivity') || {}).textContent === 'Productivity');

  console.log('\nchoosing one filters to it');
  select.value = 'productivity';
  select.dispatch('change');
  ok('productivity shows its plugin', JSON.stringify(cardIds(sb)) === JSON.stringify(['Todo']), cardIds(sb));
  select.value = 'finance';
  select.dispatch('change');
  ok('finance is not lost to "financial"', JSON.stringify(cardIds(sb)) === JSON.stringify(['Stocks']), cardIds(sb));
  select.value = 'sports';
  select.dispatch('change');
  ok('one option covers both spellings of sports',
     JSON.stringify(cardIds(sb).sort()) === JSON.stringify(['NBA', 'NFL']), cardIds(sb));

  console.log('\nthe partial is swapped back in (tab switch)');
  {
    // A fresh <select> from the template, the store list still cached.
    const fresh = new sb.FakeElement('plugin-category', 'select', attrs);
    select.parentNode.replaceChild(fresh, select);
    sb.window.searchPluginStore(false);
    await sb.settle();
    ok('the new select is filled from the cache',
       JSON.stringify(options(fresh)) === JSON.stringify(['finance', 'financial', 'productivity', 'sports', 'transit']),
       options(fresh));
    ok('keeping the chosen category', fresh.value === 'sports', fresh.value);
  }

  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
