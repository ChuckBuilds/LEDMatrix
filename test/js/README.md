# Web-interface JS tests

Covers `web_interface/static/v3/js/plugins/list_filter.js` (the shared
search/filter/sort controller) and the plugin-manager grids that use it:
Installed Plugins, the Plugin Store, and Starlark Apps.

There is no JS toolchain in this repo, so these are plain node scripts with no
test framework. Each prints `ok`/`FAIL` lines and exits non-zero on failure.

## Running

```bash
cd test/js
npm install                 # jsdom, for the DOM suites only
node run_all.js
```

The unit suites need nothing but node. The DOM suites additionally need a
running web interface, because they test against the **real** server-rendered
HTML and the **real** API rather than fixtures:

```bash
# in another shell, from the repo root
EMULATOR=true python3 web_interface/app.py         # http://localhost:5000

# or point the suites at a device
BASE=http://10.0.10.169:5000 node run_all.js
```

`run_all.js` skips the DOM suites (rather than failing) when jsdom is missing or
nothing is listening, so it stays useful in a bare checkout.

## The suites

| Suite | Needs a server | Covers |
|---|---|---|
| `unit/test_list_filter.js` | no | `ListFilter` search/filter/sort/count/sticky, and the installed-plugins config **extracted verbatim** from `plugins_manager.js` so the test can't drift from it |
| `unit/test_render_cards.js` | no | `renderInstalledCards` markup, both empty states, and HTML-escaping of hostile plugin metadata |
| `dom/test_installed_dom.js` | yes | The toolbar in a real DOM: pill/search/sort interaction, the HTMX partial re-swap, and a `getComputedStyle` check that `.filter-pill[data-active]` really matches the emitted markup |
| `dom/test_store_dom.js` | yes | Store pagination, per-page, category, tri-state Installed button, and persistence across a re-boot, against the live registry |
| `dom/test_no_double_fetch.js` | yes | Loads the **whole** `plugins_manager.js` and counts requests: typing in the store search must filter the cached list, not refetch `/api/v3/plugins/store/list` |

Point the DOM suites at a rig with a full plugin set when it matters — a dev box
with two plugins installed will pass while exercising very little.

## Notes for whoever changes this next

- The suites read the shipped files off disk and, for the DOM ones, the partial
  from the running server. They do not keep their own copy of the markup, so
  renaming an element id will fail them loudly rather than silently pass.
- `unit/test_list_filter.js` `eval`s a slice of `plugins_manager.js` located by
  the text `function installedSortName(plugin)`. If that function is renamed,
  fix the slice markers rather than pasting a copy of the config into the test.
- A few assertions exist specifically to stop earlier bugs coming back:
  trailing spaces surviving the search debounce; a multi-word query that spans
  two adjacent search fields (field order in the haystack is load-bearing);
  `window.installedPlugins` staying at full length while the grid is filtered.
- Watch for assertions that can pass vacuously. Several here deliberately guard
  against it — e.g. counting only non-skeleton cards, and asserting a search
  phrase matches something before comparing two results.

The old-vs-new differential suites used to verify that the store and Starlark
migrations were behaviour-preserving are not included: they compared against the
pre-refactor implementation, which now only exists in git history. See PR #540
if that comparison ever needs redoing.
