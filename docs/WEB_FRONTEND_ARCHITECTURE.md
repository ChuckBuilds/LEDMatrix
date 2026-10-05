# Web frontend architecture

This page covers where the web UI's JavaScript is going and how it gets
there one page at a time. The UI is Flask + HTMX + Alpine.js. Templates
live in `web_interface/templates/v3/` and static files in
`web_interface/static/v3/`.

Two rules hold at every step:

- **The Pi never builds anything.** It serves the files that are committed.
  CI builds the generated CSS (`scripts/build_css.py`, see #685) and checks
  it. The JavaScript needs no build at all: it is native ES modules that the
  browser loads as they are.
- **Every page keeps working, and so does every plugin.** Third-party plugin
  forms, `x-widget` scripts and plugin web UIs use the existing `window.*`
  names. Each name keeps working as an alias until a release announces that
  it will be removed.

## Where it started

- About 195 `window.*` globals. Their load order is held together by comments
  repeated in the headers of `app-early.js`, `app-shell.js` and
  `plugins_manager.js`.
- About 5,700 lines of inline `<script>` in the tab partials.
  `js/htmx-config.js` re-runs every one of them after every htmx swap, so
  each partial's code had to cope with running twice.
- The installed-plugin list is kept in four places.
- Plugin config forms are drawn by the `render_field` macro in
  `partials/plugin_config.html`, which is about 1,100 lines of Jinja. It
  duplicates the JS widgets. The server then needs about 430 lines to
  rebuild JSON from the flat dotted keys the form posts. The soccer form
  renders to 1.2 MB of HTML.
- `plugins_manager.js` is 3,800 lines. The owner decided that it needs a
  namespace refactor before it is split, which is what this plan provides.

## Target

```
static/v3/js/
  core/                 ES modules ("type": "module" in core/package.json)
    boot.js             entry point; base.html loads it with <script type="module">
    registry.js         page lifecycle: init/destroy on htmx swaps
    api.js              fetch wrapper for /api/v3 (JSON envelope, login redirect)
    facade.js           window.LEDMatrix and deprecated aliases
    (later) escape.js, notify.js, dialog.js, streams.js, visibility.js,
            store.js (the one installed-plugin store), form/renderer.js
  pages/                one module per tab partial
    cache.js            export init(root, ctx), destroy(root, ctx)
    durations.js, operation-history.js, raw-json.js, backup-restore.js
    ...
```

### The page lifecycle

A converted partial has no `<script>`. Its root element names its page:

```html
<div class="..." data-page="cache"> ... </div>
```

`core/boot.js` lists each page with a loader,
`'cache': page(function() { return import('../pages/cache.js'); })`, and
registers them all. A page's module is fetched only when its partial first
appears. `page()` remembers the module once loaded, so the alias of an old
synchronous global (`validateJSON` returns a boolean) still answers
synchronously while its page is on screen.

The conventions the converted pages share:

- **Buttons name an action.** A partial's buttons carry `data-action` (and
  any argument as another `data-*` attribute) instead of an `onclick` that
  names a global. One delegated listener on the page root handles them all,
  including rows drawn later.
- **Server data is drawn with `textContent`**, never a markup string.
- **Reads are cancelled, writes are not.** Loads pass `ctx.signal`, so a swap
  cancels them. Saves, deletes, exports and restores do not: the server
  finishes them anyway, so the page still reports the result in a
  notification but draws nothing into a page that has gone.
- **Old globals become aliases.** Each `window.*` name a page used to define
  is made in `boot.js` with `alias(page, name, replacement)`, which forwards
  to the module's export of the same name and warns once.
- **Timers are cleared in `destroy()`**, the one thing `ctx.signal` cannot
  undo by itself.
- **A page reports its own htmx saves.** A form whose result a page module
  shows (an `htmx:afterRequest` listener on the page root, in place of an
  `hx-on` attribute naming a global) carries `data-reports-result`. `app.js`
  then leaves the server's message to the page, as it does for a form with
  an `hx-on` after-request handler, so a save shows one notification.
- **Server data for the module goes in `data-*` attributes**, as JSON where
  it is structured (`data-schedule-config='{{ schedule_config | tojson }}'`),
  not templated into a script.

`core/registry.js` handles the rest:

| Event | What the registry does |
|---|---|
| `htmx:beforeSwap` (on `document`, so it runs after the body-level handlers that can veto a swap) | If `detail.shouldSwap` is still true, destroys every mounted page inside the swap target |
| `htmx:afterSwap` | Destroys any mounted page whose root has left the document, then mounts every `data-page` root not mounted yet |
| `LEDMatrix.pages.refresh()` | Same as afterSwap. `loadPartialDirect` (the no-htmx fallback in `base.html`) calls it |
| `start()` | Mounts whatever is already on the page. Module scripts run deferred, so a partial may arrive first |

Mounting is idempotent: a root is never initialised twice.

Each mount gets a `ctx` object:

| Field | Contents |
|---|---|
| `ctx.root` | The page's root element |
| `ctx.name` | The page name |
| `ctx.signal` | An `AbortSignal` that is aborted after `destroy()` |
| `ctx.state` | A per-mount object for the page's own state |
| `ctx.api` | Shared service from `boot.js` |
| `ctx.notify` | Shared service from `boot.js` |

A page that passes `{ signal: ctx.signal }` to `addEventListener` and
`fetch` needs no teardown code. Its listeners and in-flight requests go
away when the partial is swapped out. `pages/cache.js` is the worked
example: its delete buttons use one delegated listener, rows are built with
`textContent` rather than markup strings, and a newer load supersedes an
older one.

### One facade

`window.LEDMatrix` is the only global the module code adds:

| Member | What it is |
|---|---|
| `api` | `core/api.js`: `get`/`post`/`put`/`del`. Resolves to the JSON body, rejects with an `ApiError` |
| `pages` | `register`, `refresh`, `list` |
| `notify(message, type)` | Calls `window.showNotification`, looked up at call time |
| `escape` | Read-through to `window.LEDEscape` |
| `widgets` | Read-through to `window.LEDMatrixWidgets` |
| `deprecate(name, target, replacement)` | Keeps an old `window.*` name working. It warns once in the console, then forwards |

`ApiError` carries `status`, `body`, `network` and `loginRequired`.

`escape`, `widgets` and `notify` are read at call time. The classic scripts
that define them are deferred, and a plugin may replace them.

Login: `base.html` wraps `window.fetch` before any other script runs, and
the wrapper sends a 401 with `X-LEDMatrix-Login` to the login page (#683).
`api.js` calls `window.fetch` at call time, so its requests get the same
redirect. It also rejects that answer quietly with `loginRequired`, so no
error message flashes up while the page navigates away.

### Serving modules from the Pi

- **MIME type.** A browser runs a module only when it is served with a
  JavaScript MIME type. `app.py` pins `.js` and `.mjs` to `text/javascript`
  rather than trusting the host's mimetypes table, and
  `test/web_interface/test_es_modules.py` checks it.
- **Caching.** `url_for` adds `?v=<mtime>` to the entry script, but modules
  import each other by plain relative URL, without the version. A static
  `.js` request without `v` is therefore served `Cache-Control: no-cache`
  (revalidated, so 304 when unchanged) instead of being cached as immutable
  for a year. Versioned URLs keep the long cache. A later optimisation is an
  import map that maps each module to its versioned URL.
- **Load order.** `boot.js` loads after every classic script. Modules are
  deferred and run in document order with the deferred classic scripts.
  Nothing classic may depend on a module at load time. A classic script that
  needs a module service calls `window.LEDMatrix` at run time.

### One form model

`src/plugin_system/field_model.py` provides
`build_field_model(schema, config, plugin_id)`. It walks a plugin's schema
once and returns a JSON tree with these keys for each field:

- path, label, help, widget
- starting value, default
- constraints, options, secret flag
- the exact form controls the macro posts today (`inputs`)
- the JS widget it mounts (`mount`)

`test/test_field_model_parity.py` renders the real macro for every schema it
can find and checks that the model names the same controls, with the same
starting values, in the same order, and the same widget mounts. The schemas
come from `plugin-repos/`, `test/fixtures/plugins/`, the ledmatrix-plugins
monorepo when a checkout is present, and a synthetic schema that reaches
every branch of the macro. The test was mutation-checked when it was
written. Each of these deliberate model bugs makes it fail:

- dropping the checkbox-group sentinel
- dropping a table's `00:00` time default
- picking the first matching `<select>` option instead of the last
- dropping the `None` quirk
- missing all-hidden objects

The model mirrors the macro's quirks on purpose. The parity run surfaced
these:

- 83 number fields whose schema default is `null` render `value="None"`.
- Four array fields name an `x-widget` the core does not ship (`color`,
  `tag-input`) and fall back to a comma-separated text box.
- A list-typed `type` uses its first entry, so `["null", "string"]` draws a
  text box.
- Eleven objects with no properties and no widget render nothing.

These get fixed once, in the renderer, after the switch below.

## Switching forms to the model, behind a flag

Stage 1 (this change) only proves the model is complete. Rendering does not
change. The switch is staged so either path can be turned back on at any
point:

1. **Model endpoint.** `GET /api/v3/plugins/config/model?plugin_id=<id>`
   returns `build_field_model(schema, prepared_masked_config)`. It uses the
   same preparation as the partial: defaults merged, secrets masked.
2. **Renderer module.** `core/form/renderer.js` walks the model. It draws
   plain fields itself and hands every widget to `LEDMatrixWidgets` through
   one `mount(el, field)` adapter. The adapter keeps plugin widgets' existing
   `render(container, config, value, options)` signature (the hard
   constraint in PRODUCT.md). `getValue()` results are assembled into one
   JSON object.
3. **Flag.** `plugin_config.html` renders the macro unless the form-model
   flag is on. The flag is a `web_interface.form_model` setting in
   `config.json` (default off), plus a per-browser override
   (`localStorage.ledmatrixFormModel`) so a tester can compare both paths on
   one device. With the flag on, the partial renders only a
   `<div data-page="plugin-config" data-plugin-id="...">` root, and
   `pages/plugin-config.js` fetches the model and renders it.
4. **JSON submit.** With the flag on, Save posts
   `Content-Type: application/json` to the existing
   `POST /api/v3/plugins/config` JSON path (`plugin_config.py`, `is_json`).
   That path already validates against the schema and keeps secrets. No
   dotted keys, no `__rendered_section`, no checkbox reconstruction.
5. **Save parity test.** This gates turning the flag on by default. For every
   schema, posting the macro form's data and posting the renderer's JSON
   must store the same config.
6. **Retire.** Once the flag has been on by default for a release with no
   regressions, the macro shrinks to a no-JS fallback for plain fields, and
   the form-encoded reconstruction (`_parse_form_value_with_schema`,
   `_set_nested_value`, `_set_missing_booleans_to_false` and friends in
   `api_v3/__init__.py`) is deleted. The settings search index is then built
   from the model instead of from rendered HTML.

## Migration order

Smallest and most isolated first. `plugins_manager.js` goes last. Line counts
are the inline script in each partial today.

| # | Page | Inline JS | Why it is here |
|---|---|---|---|
| 1 | Cache (`cache.html`) | 163 lines, now 0 | **Done in stage 1.** One endpoint pair, no globals other pages use. The reference conversion |
| 2 | Rotation (`durations.html`) | 29 lines, now 0 | **Done in stage 2.** The form stays plain htmx; the page starts the shared rotation-order widget, whose plugin-list request now takes `ctx.signal`. Its `hx-on` and `onsubmit` attributes call shared globals (`showSaveResult`, `fixInvalidNumberInputs`) and move with step 6 |
| 3 | Operation History | 293 lines, now 0 | **Done in stage 2.** Read-only list; rows drawn with `textContent`, the search debounce cleared on destroy. The "Showing x to y" counters now also reset when nothing matches |
| 4 | Config Editor (`raw_json.html`) | 212 lines, now 0 | **Done in stage 2.** Plain textareas (no CodeMirror on this page). It defined 5 globals after all (`formatJson`, `manualValidateJson`, `validateJSON`, `saveMainConfig`, `saveSecretsConfig`); nothing else used them, and they are deprecated aliases now. The live "Invalid JSON" line no longer puts the parser's message into `innerHTML` |
| 5 | Backup & Restore | 232 lines, now 0 | **Done in stage 2.** Its 5 globals (`exportBackup`, `loadBackupList`, `validateRestoreFile`, `clearRestore`, `runRestore`) are deprecated aliases; the buttons are delegated `data-action`s. Uploads go through `ctx.api.request(..., { body: formData })` (`api.js` gained a raw `body` option) |
| 6 | Schedule | 193 lines, now 0 | **Done in stage 3.** Its 2 `hx-on` response handlers (`handleScheduleResponse`, `handleDimScheduleResponse`) are one `htmx:afterRequest` listener on the page root, and deprecated aliases. The forms are marked `data-reports-result` so `app.js` does not repeat the server's message. The saved schedules reach the module as JSON in `data-schedule-config` / `data-dim-schedule-config` instead of being templated into the script |
| 7 | General | 153 lines, now 0 | **Done in stage 3.** The Security section's three forms and two buttons are delegated `data-action`s (one submit and one click listener); `window.webLogin` is a deprecated alias of an object with its five methods. Login requests go through `ctx.api`, so the login redirect is quiet. The settings form keeps its `hx-on` call to the shared `showSaveResult`, as Rotation's does |
| 8 | Display | 231 | First page with `LEDVisibility` timers: those move to a `ctx.visibility` service that stops on destroy |
| 9 | Overview | 410 (4 scripts) | First-run surface: Getting Started, update banner, live preview. Five globals |
| 10 | WiFi | 364 | `x-data="wifiSetup()"` is defined by its own script. Moves to `Alpine.data()` registered from the module. AP-mode first screen, so it needs the AP-mode test on a real device |
| 11 | Fonts | 681 | Large, but self-contained (6 globals) |
| 12 | Logs | 801 | 14 globals, a stream and timers. Uses the visibility service from step 8 |
| 13 | Tools | 1,022 | 21 globals, MQTT bridge, Pixlet editor, diagnostics polling |
| 14 | Starlark app config, plugin config (`plugin_config.html`) | 123 + 294 | Plugin panels sit inside an Alpine `x-if` that removes them without an htmx swap. The registry's sweep covers that on the next swap; this step adds a MutationObserver or an `x-if` hook. Then the form-model flag (above) |
| 15 | Plugin Manager (`plugins.html` + `plugins_manager.js`) | 3,836-line file | Last. Split along the seams that already exist (installed grid, store, registries, Starlark section, on-demand) into `pages/plugins/*.js`. Its 42 globals become aliases. The four installed-plugin stores merge into one `core/store.js`, and `window.installedPlugins` becomes a getter over it |

The shell moves in parallel, a service at a time, with no page depending on
the order:

| Service | Current home | New module |
|---|---|---|
| `showNotification` | 4 versions | `core/notify.js` |
| The modal helper | `utils/dialog.js` | `core/dialog.js` |
| SSE streams | `app-shell.js` | `core/streams.js` |
| `LEDVisibility` | `app-shell.js` | `core/visibility.js` |

Each move leaves the old global as an alias. When the last inline script is
gone, the script re-execution in `htmx-config.js` and the "HTMX never
loaded" fallbacks in `base.html` can go too (keep the captive-page path).

## How the tests cover each step

The JS suites live in `test/js` (see `test/js/README.md` and
`docs/HOW_TO_RUN_TESTS.md`). In CI, the **Web UI JS tests** job installs
jsdom, starts the web interface and runs `node test/js/run_all.js` with
`REQUIRE_DOM=1`, so a skipped DOM suite fails the job.
`test/test_js_unit_suites.py` also runs every unit suite under pytest.

Unit suites need only node. They import the shipped modules directly:
`core/package.json` and `pages/package.json` mark those directories
`"type": "module"`.

| Suite | Kind | What it covers |
|---|---|---|
| `unit/test_page_registry.js` | Unit, minimal DOM shim | The lifecycle: one init per root, destroy on swap, a veto keeps the page, swaps elsewhere leave it alone, the sweep, lazy loading, a destroy while loading, error containment |
| `unit/test_core_modules.js` | Unit | `api.js` (envelope, errors, abort, login redirect, path check) and `facade.js` (facade, aliases) |
| `dom/test_cache_page.js` | DOM: real partial, real API shape | No inline script; one request per swap and per Refresh after five swaps; a cancelled request draws nothing; hostile keys stay text; delete, empty, error, network and login states |
| `dom/test_durations_page.js` | DOM: real partial, real widget, real API shape | One plugin-list request per swap; Move down moves one place after five swaps; the swap cancels a request in flight; a late-loading widget is waited for, and a page swapped away while waiting starts nothing; hostile names stay text |
| `dom/test_operation_history_page.js` | DOM: real partial, real API shape | One history request per swap and per Refresh; the plugin filter filled once (from `PluginAPI`'s cache when loaded); paging, filters, debounced search, Clear (one DELETE), error/network/login states, cancel on swap; hostile ids, users and errors stay text |
| `dom/test_raw_json_page.js` | DOM: real partial, real config | One POST per Save after five swaps, to the right file; Format and Validate act once; invalid JSON never sent and its message stays text; a save survives a swap and is still reported; the old globals' entry points |
| `dom/test_schedule_page.js` | DOM: real partial, real widget | Both pickers drawn once per swap from the saved config; after five swaps each form's answer is one notification (message, fallback, refused, non-JSON, `null`), a request from outside the forms none; the brightness label; a late widget waited for, a page swapped away while waiting draws nothing; the old globals' entry points |
| `dom/test_general_page.js` | DOM: real partial, real widget, real API shape | The timezone picker drawn once per swap with the saved zone; the settings form left to htmx; after five swaps each Security action makes one request (create, copy, revoke and its cancel, password and its mismatch); hostile token names stay text; refused, network and login answers; a create made before a swap is still reported and draws nothing; `webLogin`'s entry points |
| `dom/test_backup_restore_page.js` | DOM: real partial, real API shape | One request per Refresh, Delete, Export (busy button ignores a second click), Inspect and Restore after five swaps; the upload's fields and the six restore options; reads cancelled by a swap, writes not; hostile file and host names stay text; the old globals' entry points |
| `test/web_interface/test_es_modules.py` | pytest | MIME type; `no-cache` without `?v` and immutable with it; `boot.js` loads last; every import resolves inside `core/` and `pages/`; the converted pages are exactly the registered ones, each with its module, `init`, and one root in the rendered partial; a converted partial has no `<script>` and no `onclick`; every moved global is aliased in `boot.js` and exported by its module, and no template defines it any more |
| `test/test_field_model_parity.py` | pytest | The model against the macro for every available schema |

What each future step adds:

- **A page conversion** adds `dom/test_<page>_page.js`, built like the cache
  suite: the real partial from the server, the real API's payload shape, N
  swaps followed by one action that must make exactly one request, the
  destroy and cancel behaviour, and escaping. `test_es_modules.py` picks up
  the new page automatically. A unit suite that today slices a function out
  of a template or `plugins_manager.js` and `eval`s it is rewritten to
  import the module once that code moves (stage C of the plan).
- **A shell service move** adds a unit suite for the module and an alias
  test showing the old global still works.
- **The form switch** adds the save parity test (macro form data and
  renderer JSON store the same config, for every schema) and a DOM suite
  for `pages/plugin-config.js`. Both run with the flag on and off.
- **`plugins_manager.js`.** The existing DOM suites (`test_installed_dom.js`,
  `test_store_dom.js`, `test_no_double_fetch.js`) already test the real
  Plugin Manager in jsdom. They stay green throughout the split and are the
  gate for it, alongside the unit suites that pin its card rendering and
  escaping.
