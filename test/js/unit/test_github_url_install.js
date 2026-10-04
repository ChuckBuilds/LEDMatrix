// Plugin Manager > Install from GitHub > Install Single Plugin: one click,
// one request, no errors.
//
// The Install button carried an inline onclick calling
// window.handleGitHubPluginInstall, and attachInstallButtonHandler also gave
// it a click listener that installs. Both ran on every click. The inline one
// threw a ReferenceError (it called isGithubUrl, which lives inside the
// plugin-manager IIFE, from outside it), so only the listener's request went
// out -- and fixing that scope alone would have sent every install twice.
// The button now has the listener only.
//
// Runs the whole of plugins_manager.js in the sandbox, with the button as
// partials/plugins.html ships it.

const { create, templateAttributes } = require('../plugins_manager_sandbox');

let pass = 0, fail = 0;
const ok = (label, cond, extra) => cond
  ? (pass++, console.log('  ok   ' + label))
  : (fail++, console.log('  FAIL ' + label + (extra !== undefined ? '  -> ' + JSON.stringify(extra).slice(0, 400) : '')));

const URL = 'https://github.com/someone/ledmatrix-demo';

function route(method, url) {
  if (method === 'POST' && url === '/api/v3/plugins/install-from-url') {
    return { json: { status: 'success', message: 'Plugin demo installed successfully', plugin_id: 'demo' } };
  }
  if (url.startsWith('/api/v3/plugins/installed')) return { json: { status: 'success', data: { plugins: [] } } };
  return { json: { status: 'success' } };
}

function page() {
  const sb = create({ route });
  const button = templateAttributes('install-plugin-from-url');
  sb.el('install-plugin-from-url', button.tag, button.attrs);
  sb.el('github-plugin-url', 'input');
  sb.el('github-plugin-status');
  sb.el('plugin-branch-input', 'input');
  return sb;
}

const installs = sb => sb.requests.filter(r => r.url === '/api/v3/plugins/install-from-url');

(async () => {
  console.log('\nthe template');
  {
    const { attrs } = templateAttributes('install-plugin-from-url');
    ok('the Install button has no inline onclick', !('onclick' in attrs), attrs.onclick);
  }

  console.log('\na click');
  {
    const sb = page();
    sb.window.attachInstallButtonHandler();
    // htmx:afterSettle runs it again on every swap; that must not add a handler.
    sb.window.attachInstallButtonHandler();
    sb.el('github-plugin-url').value = URL;
    sb.window.document.getElementById('install-plugin-from-url').click();
    await sb.settle();
    ok('raises no error', sb.errors.length === 0, sb.errors.map(String));
    ok('sends exactly one install request', installs(sb).length === 1, installs(sb));
    ok('for the URL typed', installs(sb)[0] && installs(sb)[0].body.repo_url === URL, installs(sb));
    ok('and reports the result', /Successfully installed: demo/.test(sb.el('github-plugin-status').innerHTML),
       sb.el('github-plugin-status').innerHTML);
  }

  console.log('\nEnter in the URL field');
  {
    const sb = page();
    sb.window.attachInstallButtonHandler();
    const input = sb.el('github-plugin-url');
    input.value = URL;
    input.dispatch('keypress', { key: 'Enter' });
    await sb.settle();
    ok('raises no error', sb.errors.length === 0, sb.errors.map(String));
    ok('sends exactly one install request', installs(sb).length === 1, installs(sb));
  }

  console.log('\na URL that is not GitHub');
  {
    const sb = page();
    sb.window.attachInstallButtonHandler();
    sb.el('github-plugin-url').value = 'https://example.com/x';
    sb.window.document.getElementById('install-plugin-from-url').click();
    await sb.settle();
    ok('is refused without a request or an error',
       installs(sb).length === 0 && sb.errors.length === 0 && /valid GitHub URL/.test(sb.el('github-plugin-status').innerHTML),
       { errors: sb.errors.map(String), status: sb.el('github-plugin-status').innerHTML });
  }

  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
