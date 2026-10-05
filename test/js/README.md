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

The unit suites need nothing but node; `test/test_js_unit_suites.py` runs every
`unit/*.js` under pytest, so CI covers them. The DOM suites additionally need a
running web interface, because they test against the **real** server-rendered
HTML and the **real** API rather than fixtures:

```bash
# in another shell, from the repo root
EMULATOR=true python3 web_interface/app.py         # http://localhost:5000

# or point the suites at a device
BASE=http://<pi-ip>:5000 node run_all.js
```

`run_all.js` skips the DOM suites (rather than failing) when jsdom is missing or
nothing is listening, so it stays useful in a bare checkout. `REQUIRE_DOM=1`
makes that a failure instead.

CI runs everything: the **Web UI JS tests** job in `.github/workflows/test.yml`
installs jsdom, starts the web interface in emulator mode on port 5000 and runs
`run_all.js` with `REQUIRE_DOM=1`. The DOM suites don't assume a particular
device: the store suite checks pagination whichever side of 48 plugins the live
registry is, and the Tools suite supplies two sample Starlark apps when the
server has none.

## The suites

| Suite | Needs a server | Covers |
|---|---|---|
| `unit/test_list_filter.js` | no | `ListFilter` search/filter/sort/count/sticky, and the installed-plugins config **extracted verbatim** from `plugins_manager.js` so the test can't drift from it |
| `unit/test_update_all.js` | no | `PluginInstallManager.updateAll` from `plugins/install_manager.js`: Check & Update All sends only plugin ids (never `starlark:` app entries), re-sends a request that got no HTTP answer (web service restarting) instead of skipping that plugin, never re-sends one that got any HTTP answer (the real `api_client.js` classifies a proxy 502 or a JSON error without `error_code` as `API_ERROR`), and counts a no-op update as already up to date in the summary. Also run by `test/web_interface/test_update_all_plugins.py` so CI covers it |
| `unit/test_store_install.js` | no | The store's Install button, with the whole of `plugins_manager.js` run by `plugins_manager_sandbox.js` (a vm context, fake DOM and API): a fresh install reloads the list, then enables the id the plugin was installed as -- the answer's `plugin_id`, else the installed entry the store entry matches (Weather installs as `ledmatrix-weather`); a Reinstall leaves the enabled state alone |
| `unit/test_install_polling.js` | no | How long Install waits for a queued install (sandbox): at least the server's 300 s dependency-install timeout; when it stops waiting it reloads the installed list and warns, rather than reporting a failure or enabling anything |
| `unit/test_store_categories.js` | no | The store's category filter (sandbox): the template ships only All Categories, the rest come from the store's plugins (one per category whatever its case), choosing one filters to it, and a swapped-in select is refilled from the cache keeping the choice |
| `unit/test_github_url_install.js` | no | Install Single Plugin (sandbox, the button as `plugins.html` ships it): no inline `onclick`, so a click or Enter sends exactly one `install-from-url` request and raises no error |
| `unit/test_render_cards.js` | no | `renderInstalledCards` markup, both empty states, and HTML-escaping of hostile plugin metadata |
| `unit/test_plugin_order_list.js` | no | `widgets/plugin-order-list.js` (the Vegas and rotation order lists): a disabled plugin, which gets no row, keeps its slot in the saved order and its Vegas exclusion when the list rewrites its hidden inputs, around reordering and include/exclude; an uninstalled plugin's id is dropped, a failed plugin list leaves the inputs as saved, and only string ids are carried over, once each |
| `unit/test_style_editor_element_keys.js` | no | `elementKeys()`/`styleRows()`/`positionRows()` from `widgets/style-editor.js`: every `customization.layout` entry gets exactly one row -- paired with its style element through core's `x-layout-key` (so `score` belongs to `score_text`, not a second row), or a position row of its own, leaves included -- since the widget claims the whole `layout` block from the generic fallback renderer |
| `unit/test_style_editor_layout_leaf_columns.js` | no | `columnsFor()` from `widgets/style-editor.js`: a layout-only key whose own value is a leaf (no x/y sub-object, e.g. a `show_logo` toggle) gets a self-keyed column instead of a blank, uneditable row |
| `unit/test_style_editor_layout_leaf_collision.js` | no | `columnsFor()` from `widgets/style-editor.js`: a layout-only leaf key still gets its own column even when its name collides with an unrelated element's style sub-field or another layout axis's sub-field |
| `unit/test_inline_handler_escaping.js` | no | The store, saved-repository and custom-registry inline `onclick` handlers and the live `window.updateImageList` from `plugins_manager.js`: a registry id, URL or uploaded file name carrying `'`, `"` or entities adds no attributes and reaches the handler intact, and the store's View button opens only http(s) links |
| `unit/test_store_registry_fields.js` | no | The store card's registry fields from `plugins_manager.js`: the commit that introduced the listed version (a hex SHA only, linked to that tree), the "Needs LEDMatrix X+" warning, a card from an older registry without either, and `isStorePluginInstalled` answering to `aliases` |
| `unit/test_page_registry.js` | no | The page lifecycle in `js/core/registry.js` (a minimal DOM shim): one `init` per `data-page` root, `destroy` and an aborted `ctx.signal` when htmx swaps it away, a vetoed swap keeps it, lazy page modules, a root removed without htmx swept on the next swap |
| `unit/test_core_modules.js` | no | `js/core/api.js` (JSON envelope, HTTP/`status: error`/network errors, abort passthrough, the #683 login redirect, same-server paths only) and `js/core/facade.js` (`window.LEDMatrix`, deprecated aliases) |
| `unit/test_overview_reconciliation_poll.js` | no | The Overview's reconciliation-banner poll from `partials/overview.html`, run in a vm: it gives up after a bounded number of requests when the status never says done, runs only while the Overview is on screen (`LEDVisibility`, its own key), and stops once the banner is shown |
| `unit/test_display_partial_ids.js` | no | `js/pages/display.js` started on a fake root that answers only for the ids `partials/display.html` renders: every id it looks up (with every listener and timer it set fired) exists, and moving the brightness slider updates its label without throwing |
| `unit/test_general_web_login_token.js` | no | `createToken` from `js/pages/general.js`, imported with a fake DOM and fetch: a created API token clears the form's `data-dirty` mark (so a reload does not ask "Leave site?"), a refused one keeps it |
| `unit/test_plugin_action_delegation.js` | no | The document-level card-action delegation and `handlePluginAction` from `plugins_manager.js`, run with the handler inside an IIFE as in the real file: each action is handled once, a Starlark app uninstall goes to `DELETE /starlark/apps/<id>`, and an uninstall is confirmed once |
| `dom/test_installed_dom.js` | yes | The toolbar in a real DOM: pill/search/sort interaction, the HTMX partial re-swap, and a `getComputedStyle` check that `.filter-pill[data-active]` really matches the emitted markup |
| `dom/test_store_dom.js` | yes | Store pagination, per-page, category, tri-state Installed button, and persistence across a re-boot, against the live registry |
| `dom/test_no_double_fetch.js` | yes | Loads the **whole** `plugins_manager.js` and counts requests: typing in the store search must filter the cached list, not refetch `/api/v3/plugins/store/list` |
| `dom/test_cache_page.js` | yes | The Cache tab as a page module (`js/pages/cache.js`) on the real partial: no inline script, one request per swap and per Refresh after repeated swaps, a cancelled request draws nothing, hostile keys stay text, delete/empty/error/login states |
| `dom/test_durations_page.js` | yes | The Rotation tab (`js/pages/durations.js`) with the real `plugin-order-list.js` widget: one plugin-list request per swap, one move per click after repeated swaps, a swap cancels the request in flight, a late widget is waited for |
| `dom/test_operation_history_page.js` | yes | The Operation History tab (`js/pages/operation-history.js`): one request per swap and per Refresh, the plugin filter filled once, paging, filters, search, Clear, error/login states, hostile values stay text |
| `dom/test_raw_json_page.js` | yes | The Config Editor tab (`js/pages/raw-json.js`): one POST per Save after repeated swaps, Format/Validate, invalid JSON never sent, a save survives a swap, the old global entry points |
| `dom/test_schedule_page.js` | yes | The Schedule tab (`js/pages/schedule.js`) with the real `schedule-picker` widget: both pickers drawn once per swap from the saved config, one notification per save answer after repeated swaps, the brightness label, a late widget waited for, the old global entry points |
| `dom/test_visibility_service.js` | yes (no server) | `js/core/visibility.js` with the real `LEDVisibility` from `app-shell.js` and the real registry: start/stop with the active tab and the browser tab's visibility, no interval while hidden or after a swap-out, registrations independent, the no-`LEDVisibility` fallback |
| `dom/test_display_page.js` | yes | The Display tab (`js/pages/display.js`) with the real `plugin-order-list` widget and `LEDVisibility`: one page, one sync interval and one action per control after repeated swaps, the sync poll only while on screen and never after a swap-out, sync states as text, the debounced scroll-speed hint, `updateSyncUI`'s entry point |
| `dom/test_general_page.js` | yes | The General tab (`js/pages/general.js`) with the real `timezone-selector` widget: the picker drawn once per swap, one request per Security action after repeated swaps, hostile token names stay text, refused/network/login answers, a write survives a swap, `webLogin`'s entry points |
| `dom/test_backup_restore_page.js` | yes | The Backup & Restore tab (`js/pages/backup-restore.js`): one request per action after repeated swaps, the upload and restore options, reads cancelled and writes not on a swap, hostile names stay text, the old global entry points |
| `dom/test_tools_sections.js` | yes | The Tools tab's MQTT bridge and Pixlet editor sections: form prefill, the write-only password (blank means unchanged), the running-session banner and countdown, and that the editor link points at the host you loaded the page from |

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
