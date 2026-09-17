# Changelog

Notable changes to the LEDMatrix core. The version below is the value of
`src.__version__`, which the plugin loader reports to compatibility checks and
which plugin manifests reference via `ledmatrix_min_version`.

**Why this file exists:** the plugin monorepo bundles fallback copies of several
core modules (see `docs/plugin-development/08-shared-sports-code.md` in
[ledmatrix-plugins](https://github.com/ChuckBuilds/ledmatrix-plugins)). A plugin
may delete its bundled copy only when its manifest floors on the first core
release that ships the module — which requires module additions to be recorded
here, against a version number. When you add a module plugins will import via
`src.*`, note it in the Unreleased section and bump `src/__init__.py` in the
release that ships it.

**Use `ledmatrix_min_version` in manifests, not `ledmatrix_min`.** The loader
accepts both, but the store flags the old spelling as deprecated
(`store_manager.py`) and only the new one is in `schema/manifest_schema.json`.

## Unreleased

Config saves and plugin config preparation:

- A JSON `POST /api/v3/config/main` changes only the keys it sends. The MQTT
  bridge's brightness slider used to turn off `disable_hardware_pulsing`,
  `inverse_colors`, `show_refresh_rate` and `use_short_date_format`, and a
  timezone- or location-only save turned off web-UI autostart and weekly
  automatic updates. Missing checkboxes still save as unchecked for the
  settings forms (they now send a hidden `__form_section` field) and for
  form-encoded posts.
- A partial JSON `POST /api/v3/plugins/config` merges onto the plugin's stored
  settings instead of resetting everything it didn't send to the schema
  defaults, and keeps a submitted `skin`, `skin_options`, `vegas_width_pct`,
  `vegas_overflow` or `vegas_max_width_screens` (they were silently dropped).
- Plugin sections posted to `/config/main` are validated and prepared exactly
  like `/plugins/config`; a value that endpoint rejects is rejected here too,
  and nothing is saved.
- Legacy boolean settings (#588) are read as `{"enabled": ...}` objects
  everywhere, not just when the plugin loads: `GET /plugins/config` returns
  the object, posting it back saves, and hot reload hands plugins the same
  shape (schema defaults included) they were constructed with.
  `schema_manager.prepare_plugin_config` is the one implementation.
- `scripts/dev_server.py`, `check_plugin.py`, `render_plugin.py` and the plugin
  harness build configs the way a device does: nested defaults are included,
  a schema `enabled: false` no longer beats the forced `enabled: true` in the
  dev server, and nested overrides such as `{"nhl": {"enabled": true}}` keep
  the other defaults of that section.
- Clearing Vegas "Min/Max Cycle Time" no longer rejects the whole Display save,
  and those fields no longer add junk entries to `display.display_durations`.
- Turning automatic updates on from the Raw JSON editor finishes their setup
  like the General tab does, instead of waiting for the next display restart.
- `POST /config/schedule` and `/config/dim-schedule` accept the per-day
  `days.<day>.{enabled,start_time,end_time}` shape their GETs return, as well
  as the flat form keys.
- The startup check no longer warns that `auto_update` or `dim_schedule` is
  "enabled but not found in plugins directory", and plugin ids that collide
  with any core config section are flagged: the last private copies of the
  core-key list now use `src/core_config_keys.py`.

New module a plugin may import via `src.*` (floor on the release that ships
this):

- `src/common/sports_helpers.py` — the helpers the scoreboards' `sports.py`
  carry byte-identical copies of: `clamp_window`, `clamp_seconds`,
  `logo_needs_refresh`, `spread_weighted_order` (+ `MIN_WINDOW_DAYS`,
  `MAX_WINDOW_DAYS`), and `SportsHelpersMixin` with `_mode_customization`,
  `_setting_int`, `_reset_dwell_on_reentry`, `_next_switch_index`,
  `_spread_weighted_order`, `_odds_color`, `_upcoming_date_and_time_text` under
  the plugins' names and signatures, plus the `_favorite_key` override point.
  Constructor-free; keeps lazy state on its host (see the module docstring,
  which also gives the host contract).
  A new module rather than more methods on `sports_shared`: a plugin that
  deletes a copy and leans on an older module having grown the method fails at
  runtime with `AttributeError`, which no load-time check sees, while a missing
  module fails at load. Nothing in core uses it yet.
- `test/test_common_is_hardware_free.py` — `src/common` must import without
  `rgbmatrix` and never import `src.base_classes`, `src.display_manager` or
  `src.plugin_system` at module level.
- `src/common/espn_dates.py` — `fetch_espn_scoreboard`,
  `fetch_espn_date_chunks`, `espn_date_chunks`, `clamp_espn_limit`,
  `ESPN_MAX_LIMIT`: fetch an ESPN scoreboard date range now that ESPN rejects
  ranges (see Sports data below). Plugins bundle a copy of it.

Sports data:

- Since 2026-09-15 ESPN answers `dates=YYYYMMDD-YYYYMMDD` scoreboard queries
  with `400 Bad Request` for every sport, so season schedules, the weeks window
  and today's games all failed ("400 Client Error" from the NFL/NCAAFB managers
  and `src.background_data_service`). A rejected range is now re-fetched as
  whole months (`dates=YYYYMM`) plus the leftover days at each end, which cover
  the window exactly: a football season is 8 requests. A month that returns
  exactly 500 events is truncated and is re-fetched day by day.
- Scoreboard requests send `limit=500` at most. Above 500 ESPN silently returns
  a short list: college football gave 25 of 68 games for one Saturday at the
  `limit=1000` everything used to send.
- `BackgroundDataService.handles_espn_date_ranges` is `True`. Plugins check it
  to decide whether to submit a season range to the service or fetch it
  themselves on an older core.

Web interface:

- The plugin settings form honours `"x-display": "hidden"` in config schemas:
  the property gets no control at any depth (top level, nested objects, array
  rows, Advanced Settings), and saving the form never changes its stored value.
  JSON API saves are unaffected. Lets plugins keep deprecated or internal keys
  declared, e.g. countdown's row `id` and weather's `api_key` / `radar_zoom`.
  See `docs/widget-guide.md`.
- Display settings no longer silently cut values on save: columns were capped
  at 128, chain length at 24 and PWM LSB nanoseconds at 500. Rows, columns and
  chain length now have no upper limit (the current rgbmatrix library still
  rejects more than 64 rows per panel); rows must be even and at least 8,
  parallel is 1–3 and PWM dither bits 0–2, matching the library. A stored GPIO
  slowdown, PWM dither bits or refresh-rate cap of 0 no longer shows (and
  re-saves) as 3, 1 or 120, and the refresh cap accepts 0 (no cap). The config
  API rejects out-of-range or non-integer `rows`, `cols`, `chain_length`,
  `parallel`, `brightness`, `scan_mode`, `pwm_bits`, `pwm_dither_bits`,
  `pwm_lsb_nanoseconds`, `limit_refresh_rate_hz`, `row_address_type`,
  `multiplexing` and `gpio_slowdown` with a 400 (JSON `true` or `5.5` used to
  save as 1 or 5) instead of saving a config the matrix refuses to start with.
- Display setting help tips and README / config-reference entries corrected
  and completed: `panel_type` and `rp1_rio` are documented,
  `show_refresh_rate` prints to the console rather than drawing on the panel,
  PWM dither bits raise the refresh rate rather than lowering it, and every
  numeric setting states its range.
- Row Address Type offers 5, the SM5368 / B707 row shift register. The
  Waveshare 96x48 V2 panel (back silkscreen `24S-A1`) needs it with RGB
  sequence BGR and, on a Pi 4, a GPIO slowdown of 6–8. Panels with FM6124
  column drivers need no Panel Type.
- On a Raspberry Pi 5 the pinned rgbmatrix library can drive only row address
  types 0 and 2, parallel 1–3 and the standard mappings. For anything else it
  returns no matrix, which the Python binding doesn't catch, so the display
  service crashed and restarted every 10 seconds. `DisplayManager` now refuses
  those settings before creating the matrix (logged, reported by
  `/api/v3/hardware/status`, fallback mode), the config API rejects them, and
  the Display form offers only row address types 0 and 2 on a Pi 5. The rule
  lives in `src/pi5_matrix_support.py` and must be re-checked when the
  submodule is bumped.
- The Plugin Config Warning no longer lists core settings as plugins that are
  "in config but not installed" (seen as `auto_update` on 3.4.0, where the
  advice would have deleted the weekly-update setting). Core top-level config
  keys now live in one list, `src/core_config_keys.py`, which reconciliation
  uses and tests pin to `config.template.json` and the settings save endpoint.
  A stored warning is also dropped once its entry is no longer a plugin in
  config, so an old verdict clears without a restart.
- **Check & Update All** no longer sends installed Starlark apps
  (`starlark:<app_id>` entries in `/plugins/installed`) to the plugin updater,
  which answered each with a 500 "plugin not found". `POST /plugins/update`
  now answers a `starlark:` id with a 400 saying it is a Starlark app. A
  request that gets no HTTP answer (e.g. the web service restarting mid-run) is
  re-sent with backoff instead of being counted as failed and skipped — that is
  how a disabled plugin with an update waiting was silently left out.

Plugin system:

- A plugin no longer starts with a schema warning and a degraded flag because
  config.json still holds a boolean where its schema now has an object with an
  `enabled` property (news' `global.dynamic_duration: true`). The loader reads
  the boolean as `{"enabled": <bool>}` before merging schema defaults and
  validating, the same rule the settings form already applies
  (`legacy_bool_as_object` in `src/plugin_system/schema_manager.py`). Nothing
  is written at load; the next save of that plugin's settings stores the object.
  Other type mismatches still warn.

Core:

- `ConfigManager.load_config()` no longer raises on a host without the POSIX
  ownership APIs. The self-heal that chgrp's `config_secrets.json` to the
  shared group (added in #416) looked up `os.geteuid` unguarded; that name does
  not exist on Windows, and the resulting `AttributeError` is not an `OSError`,
  so it escaped the helper's own "best-effort" handling and every caller's.
  Any Windows checkout with a `config/config_secrets.json` got a `ConfigError`
  from every config load and could not `import web_interface.app` at all.
  `ensure_shared_group_ownership()` now returns immediately when `os.geteuid`
  or `os.chown` is missing. No behaviour change on the Pi.

## 3.4.0

Plugin-facing changes since 3.3.0 (tag `v3.3.1`) not covered further down:

- `BasePlugin.get_update_interval()` (#555) — return seconds to override the
  manifest's `update_interval` at runtime (e.g. poll fast only while a game is
  live), or `None` to keep it. Clamped to at least 5 seconds; a raising or
  non-numeric return is ignored. Called every scheduling tick, so keep it
  cheap. Older cores never call it. See `docs/PLUGIN_API_REFERENCE.md`.
- `src.common.scroll_config` (#523) — turns a plugin's scroll config into a
  configured `ScrollHelper` in one place, replacing per-plugin resolution that
  disagreed between tickers, and warns when a speed won't advance whole pixels
  per panel refresh. Floor on 3.4.0 to import it.
- **Skins are marked unsupported.** No current scoreboard plugin builds on
  `src.base_classes`, so the skin hook (`SportsCore._render_game`) never runs.
  The web UI no longer shows the Visual Skin dropdown, the store hides and
  refuses `"type": "skin"` entries, and `GET /api/v3/skins` reports
  `"supported": false`. Saved `skin` config values still load and save.
  `src/skin_system/` is unchanged.
- **Web preview size** now comes from `src/display_geometry.py`, the same
  computation `DisplayManager` uses: double-sided setups preview one screen,
  and a missing `chain_length` defaults to 2 everywhere (the Starlark magnify
  default and the sync handshake used 1). The module is core-internal: plugins
  keep reading `display_manager.width`/`height`.
- `src.common.font_layout` (#539, #565) — `load_truetype()` is
  `ImageFont.truetype` with the layout engine pinned, so text lays out the same
  whether or not the host's Pillow was built with libraqm; `crisp_size()` and
  `FONT_PIXEL_GRID` give the size a bundled face renders on whole pixels at
  (`sports_card` still re-exports them); `resolve_asset_path()` resolves
  `assets/fonts/...` against the install root, not the working directory.
  Floor on 3.4.0 to import it. Relatedly, `DisplayManager` now draws text
  1-bit (#521), so golden images recorded against 3.3.x may need regenerating.

### Install and updates

**Weekly automatic updates (#581), off by default.** Switching on
*Automatically check for and install updates once a week* on the General tab
(or `first_time_install.sh --enable-auto-update` / `LEDMATRIX_AUTO_UPDATE=1`)
updates the core and then every installed plugin once a week, preferably 2–5 AM
local time. It follows the branch the checkout tracks — `main` on a standard
install — so a device gets whatever has merged there, not only tagged releases.
See `docs/WEB_INTERFACE_GUIDE.md`.

- The core step is skipped, with the reason shown, when the checkout has local
  edits or commits, a rebase or merge is in progress, the branch has no
  upstream, less than 300 MB is free, or that commit was already rolled back.
- After pulling, `ledmatrix-update-verify.service` restarts the services and
  requires the web interface to answer and the display to stay up. If they
  don't, or the new requirements fail to install, it resets to the previous
  commit, reinstalls its requirements and restarts again. Anything but success
  shows under the toggle and as a banner on Overview.
- Plugins update through the Plugin Store even when the core step is skipped,
  fails or is rolled back. A plugin version whose `ledmatrix_min_version` is
  above the device's core is held back, not installed. When the core did
  update, plugins wait for its health check, and are left alone if that check
  never reports or the rollback fails.
- No SSH is needed: switching the toggle on restarts the display service, which
  installs the health-check units (`src/auto_update_setup.py`, core-internal
  and not a plugin API).

Installer and service fixes:

- rgbmatrix builds on ARMv6 boards (Pi Zero, Pi 1); an existing checkout is
  moved forward to the new pin and no longer left root-owned (#577).
- `first_time_install.sh` grants the web user `safe_pip_install.sh`, as
  `configure_web_sudo.sh` already did, so plugin requirements install where
  the display service can see them (#579).
- The web interface starts when `web_display_autostart` is missing or
  `config.json` is unreadable; only an explicit `false` keeps it down (#556).
- Installers render every systemd unit from its `systemd/` template, so the
  boot-time unit-drift warning can clear, non-root installs included (#547).

### Scrolling

- **Frame pacing (#523).** The loop waits only for the rest of each panel
  refresh instead of a flat 8 ms: 44–46 fps → 100 fps, and slow frames 14% →
  0.02%, on a 2×128×64 chain. Sub-pixel blending is off by default again (it
  shimmered on pixel fonts; Vegas mode still opts in).
- **Whole-pixel steps (#545).** At a speed `scroll_config` can render in whole
  pixels, every frame advances by exactly the same amount, removing about six
  hitches a second. A loop that can't keep up now scrolls slightly slow rather
  than jumping.
- The eight sports scoreboards scroll through `scroll_config` too (#542): the
  default 50 px/s holds each frame for two refreshes instead of alternating
  0 px and 1 px steps.
- **Frame stats ignore the pause between scrolls (#582).** The `Scroll frame
  stats` log line counted the idle wait before each scroll as one frame,
  inflating `max` and the stall rate. `docs/SCROLL_PERFORMANCE.md` now
  describes the line actually logged.

### Plugins

- `FontManager` registers the bundled `tom_thumb` font, so plugins no longer
  need a private loader (#534).
- The test harness's `set_scrolling_state()` accepts `frame_hold`, as
  `DisplayManager`'s does (#534).
- A `display()` with nothing to draw should return `False`, the only value the
  controller skips on; starlark-apps now does, rather than holding a black
  panel (#534).
- Starlark apps may set `render_width`/`render_height` in their `config.json`
  to render at their own canvas size instead of Pixlet's 64×32 (#552).
- `scripts/render_plugin.py --display-mode <mode>` renders one mode of a
  multi-mode plugin; scoreboards previously rendered blank (#522).
- Scoreboards resolve their own directory under the real plugin loader
  (declare `_PLUGIN_DIR`), so 4x6 text snaps to its 7px grid instead of
  rendering a pixel narrow, and an unreadable schema is logged (#519, #520).
  `DisplayManager` loads 4x6 on that grid too (#565).
- The 5x7 BDF face reports a real height, so rows stacked by
  `get_font_height()` no longer overlap (#539).
- `LogoHelper` remembers a missing logo instead of warning every rotation
  (#548), and the decoded sports logo cache is bounded (#559).

### Web interface

- Installed Plugins has search, All / Enabled / Disabled / Updates filters and
  sort (#540).
- Hardened and polished per the September 2026 audit (#568): utility classes
  such as `.hidden` actually exist, focus rings, labels and modal focus
  trapping, dark theme throughout, no overflow at phone width, and background
  streams pause when hidden, with first-load JS/CSS down from 1358 KB to 291 KB.
- WiFi Connect works from the LEDMatrix-Setup hotspot: the page is answered
  before the hotspot drops, and reopening it shows why an attempt failed (#571).
- Pixlet install, the Starlark app store and app toggles work again (#535,
  #537); the store uses the configured GitHub token and reports a rate limit
  instead of drawing a blank grid (#541).
- Plugin config: geochron and news saves no longer always fail (#575), the page
  survives stored values the schema outgrew (#578), the form uses the full page
  height (#573), and file-manager widgets show the script's error (#574).
- The live status stream reports real disk usage and available memory (#558);
  a system action refused for want of passwordless sudo says so and names
  `configure_web_sudo.sh` (#560).

### Tools and security

- **CodeQL triage (#561):** 129 of 134 alerts fixed. Three were exploitable
  path-handling flaws in the web interface and are closed; web UI escapers now
  escape quotes, and URL fields refuse script schemes. Path checks share
  `src/common/path_safety.py` (core-internal).
- **Home Assistant MQTT bridge** (`integrations/mqtt_bridge`, #538): mode
  select, stop, power and brightness over MQTT Discovery.
- **Tools tab** manages the MQTT bridge and the Pixlet editor (#554); the
  editor stays on loopback when `PIXLET_EDITOR_HOST` says so.

### Fixes

- Updating a plugin whose directory is named for its manifest id (leaderboard,
  music, stocks, weather) silently did nothing (#536).
- Plugin reconciliation no longer reports working plugins as stale or replaces
  their config with a stub, and the Overview banner advises each case correctly
  (#557).
- Two config saves in the same second no longer share one backup, so rollback
  restores the version asked for (#564).
- On-demand: a second request is honoured without a restart (#534), a pinned
  request stays on its mode, and restarting mid-session loads every plugin
  again (#538).
- `/health` and `/display/current` report real state, and the preview no longer
  freezes on a leftover snapshot temp file (#534).

### Per-element display customization

**Per-element display customization, and the last mile of it into the web UI.**
A user can set the font, size, colour, position, visibility and alignment of
individual display elements per plugin -- and, where a plugin has display
modes, separately per mode.

New public API a plugin may import via `src.*` (floor on the release that
ships this):

- `src.element_style.layout_offset(config, element, axis, default, mode)` and
  `element_color(config, element, default, mode)` — the stateless reads the
  scoreboard helpers share. There were three copies of the offset read and two
  of the colour read; these are the one implementation, and they carry the
  element-name aliasing and the per-mode lookup.
- `src.element_style.alias_keys(element)` — the names one element may be stored
  under. The style block names elements `score_text` while the layout block
  says `score`, and `records`/`record` and `status_text`/`status` split seven
  to two across the published schemas. A lookup tries the exact name first, so
  this is inert for a config that already matches.
- `src.element_style.element_visible(config, element, default, mode)`,
  `element_align(...)` and `element_scale(...)` — the stateless reads for the
  three knobs the resolver already understood but no draw path consumed, so an
  element could be marked hidden in the web UI and still render.
- `SportsCoreSharedMixin._draw_text_with_outline(..., element="score_text")` —
  naming the element resolves its colour by name and honours its visibility
  toggle. Without a name the colour is inferred from font-object identity,
  which cannot separate two elements sharing a face; that is the case every
  bitmap font is in, because a `freetype.Face` cannot be re-instantiated, and
  it is how a BDF-rendered element silently lost a configured colour. Shared
  faces now resolve when exactly one sharer has a colour set.
- `LogoHelper.load_logo(..., scale=)` — applies a user's image scale, and keys
  the cache on the scaled box so two elements scaled differently cannot be
  served each other's image.
- `src.element_style.native_bdf_size(font)` — the one pixel size a bitmap font
  can render at, or None for a scalable one. The web UI needs this to know
  whether a size control can take effect at all.
- `ElementStyleResolver(config, defaults, mode=...)` plus `visible`, `align`
  and `scale` on `ElementStyle`. The mode binds to the resolver rather than
  being passed per call, so a plugin with one instance per mode makes every
  existing lookup mode-aware by setting one class attribute.
- `BasePlugin.styles` / `styles_for(mode)` / `STYLE_MODE` — the accessor every
  plugin inherits, so adopting this is no longer a guarded import plus schema
  discovery plus resolver invalidation in each plugin.
- `SportsCoreSharedMixin._get_layout_offset` — promoted from the plugins'
  bundled copies. Each still carries its own, which wins by MRO, so adopting
  it is a deletion.

Schema and web UI:

- A `customization` block is now rendered by a composite style editor: one row
  per element rather than nested accordions, with a tab per declared mode.
  Plugins that hand-wrote their style blocks get it without a plugin release;
  `x-style-elements` and `x-style-modes` declare it compactly.
- Font fields become a real picker rather than a hardcoded `enum`, so a font
  the user uploads is selectable. Bitmap fonts taller than the element's
  declared size ceiling are filtered out, because a bitmap font ignores
  `font_size` and renders at its own size.
- `/static/plugin-widgets/<plugin>/<widget>.js` serves a plugin's own web-UI
  widgets. The client half and the docs already existed; nothing served them.

Fixed:

- A bitmap font asked for a size it has no strike for fell back to
  *PressStart2P* — a different typeface — rather than to its own native size.
  32 of the 35 shipped fonts are bitmap, so this was reachable for most font
  choices.
- The plugin config form read `config_schema.json` directly while the save
  route read it through `SchemaManager`. Only the latter expands a compact
  `x-style-elements` declaration, so a plugin using that form had a
  customization section that rendered as empty space.
- `unshare_element_fonts` rebuilt faces through bare `ImageFont.truetype`,
  bypassing the layout engine `src/common/font_layout.py` pins. These were the
  only two call sites in `src/` doing so.
- The form parser compared a schema type to a bare string, so a nullable field
  (`["array", "null"]`) never had its indexed colour inputs recombined, and a
  blank one became `[]` rather than null.

Removed:

- The Fonts tab's "Element Font Overrides" panel and its three endpoints. They
  reported success and saved nothing, and the element keys the panel offered
  (`nfl.live.score`, `clock.time`) are read by no plugin, so wiring them to the
  real `FontManager` methods would still have changed nothing on the panel.
  Per-element font choice now lives in each plugin's own config editor.
- "Detected Manager Fonts", which listed every installed font with a hardcoded
  usage count.
- Two dead client-side config-form renderers in `app-shell.js` (~580 lines) and
  the legacy `plugins/config_manager.js`, superseded by server-side rendering.

## 3.3.0

Historical note: tags `v3.3.0` and `v3.3.1` both report `__version__` "3.3.0" and both ship `src/common/sports_shared.py`, so a "3.3.0" floor always means a core with `sports_shared`.

**The release the sports scoreboards floor on to delete their bundled copies.**
3.2.0 shipped the unified sports library and made `ledmatrix_min_version`
enforceable; this ships the last three shared modules and completes the store
gate, so a scoreboard can now floor here and carry no fallback at all.

New modules a plugin may import via `src.*` and floor on 3.3.0 for:

- `src/common/sports_card.py` — settings, colour, font and date helpers for a
  scoreboard's `game_renderer.py`. Free functions taking `config`/`fonts`
  explicitly, so nothing about the caller's class is assumed.
- `src/common/sports_game_renderer.py` — `SportsGameRendererMixin`: scroll/Vegas
  card geometry (centre gap, logo slot and cache key, layout offsets, the
  upcoming-card date and time layout). No `__init__` and no state, so adoption
  is one line on the class statement.
- `src/common/sports_shared.py` — `SportsCoreSharedMixin`,
  `SportsLiveSharedMixin`, `SportsRecentSharedMixin`: the `sports.py` bodies
  byte-identical in all eight lineage-sharing scoreboards.

All three sit under `src/common/` rather than `src/base_classes/sports/`,
deliberately: importing that package pulls `core.py` → `DisplayManager` →
`rgbmatrix`, and these are pure logic. Plugins importing them must not acquire a
hardware dependency.

Three notes for anyone adopting `sports_shared`:

- `SportsRecentSharedMixin` defines `__init__`. Its bare `super()` binds to the
  mixin, so it reaches the host only when the mixin is listed **first** in the
  bases. Reversing that order silently skips the host constructor.
- Methods that resolve the plugin's `config_schema.json` use `_plugin_dir()`,
  which walks the MRO rather than reading `__file__` — `__file__` is now
  `src/common/`. It walks because `SportsCore` is an ABC: a subclass built with
  `type(name, bases, ns)` reports `__module__` as `"abc"`.
- `_get_timezone`, `_extract_game_details` and `_fetch_data` are byte-identical
  across the eight but stay in the plugins. The first binds a per-plugin
  timezone module whose contents differ; the other two are the abstract stubs
  that define the sport.

**The store's compatibility gate is now on every registry-managed route.** 3.2.0
gated `install_plugin`. This release gates the git-pull update path and
`install_from_url`, so a plugin whose floor the core cannot meet is refused
after download with no partial directory left behind.

### Fixed

- **ESPN 403s.** `site.api` began rejecting the User-Agent strings this repo
  sent on 2026-08-04; every shared-data-source scoreboard returned
  `403 Forbidden`. Requests now send an identifying token with a project URL —
  browser-style strings and bare custom tokens are both refused.
- **Low-memory boards becoming unreachable under load** while still answering
  pings and serving the web UI. Fetched payloads are released after delivery
  rather than pinned on the completed request for up to an hour, malloc arenas
  are capped, and log volume and SD writes are reduced. Available memory is now
  reported in Tools diagnostics.
- **A failed logo download pinning a team to a grey box**: the placeholder was
  written under the real logo's filename, so later attempts found a file and
  reported success without retrying.
- **A plugin enabled but never loaded is retried** rather than staying absent
  with `error = null`.
- `ttl` now controls cache expiry; abandoned cache writes no longer leave temp
  files; one cache-cleanup thread per directory rather than per manager.
- Odds are fetched for the games displayed, not the whole schedule window, and a
  stalled ESPN no longer stalls the whole plugin update.
- Array-item secrets are no longer wiped or logged.
- A restored backup matches the device it was taken from.
- The web UI reports the real error instead of "unknown", rejects non-finite
  JSON numbers, and stops checkbox groups posting back hidden options.

### Added

- `display.hardware.orientation` for panels mounted upside down.
- Vegas keeps live content in the ticker rather than being preempted by it.
- Schemas can label enum dropdown options.

## 3.2.0

**The first release shipping the unified sports library.** This is the version
a sports plugin floors `ledmatrix_min_version` at before deleting its bundled
copy of `sports.py`, `scroll_display.py`, `data_sources.py` or
`base_odds_manager.py` — the sunset rule in
`docs/plugin-development/08-shared-sports-code.md` keys on exactly this number.

Adoption is deliberately staged: the modules below ship here, plugins adopt them
behind guarded imports, and only then do the bundled copies go away. Nothing in
this release changes what an existing plugin loads.

**This is also the first release that *enforces* `ledmatrix_min_version`.**
Before it, the floor was advisory — the loader logged a warning and continued,
and the plugin store never compared the core version at all, so an update could
deliver a plugin that could not run. From 3.2.0 the store refuses such an
install. That matters for the sunset rule: a plugin may only delete its bundled
fallback once the cores in the field actually enforce the floor, which means
waiting for 3.2.0 to be widely installed rather than merely released. See
`docs/SPORTS_UNIFICATION.md`, phase B6.

One deliberate exception: a core reporting a version below `2.0.0` is treated as
*unknown* rather than old and is never blocked. The v3.1.0 release ships
`__version__ = "1.0.0"` (the tag was cut before the string was bumped), and
nearly every published manifest floors at `2.0.0` — so blocking on that number
would lock those users out of the plugin store entirely.

### Added
- `src/element_style.py` — per-element style resolver backing the
  `x-style-elements` config-schema extension. Already consumed (behind guarded
  imports with classic fallbacks) by the `of-the-day`, `ledmatrix-music`, and
  `football-scoreboard` plugins.
- Core unit-test CI job enrolling the previously unenrolled suites (skin
  system, data sources, API extractors, scroll helper, adaptive layout, loader
  compatibility warning) plus new characterization tests for
  `src/base_classes/sports.py` ahead of the shared sports-code unification.
- `src/base_classes/sports/` — `sports.py` is now a package (`core.py` +
  `modes.py`). The import path is unchanged: `from src.base_classes.sports
  import SportsCore` still works.
- Nine methods promoted onto the sports base classes from the plugins'
  bundled copies, plus the override points `_favorite_key`,
  `_config_schema_path` and `_font_root` and the class attributes
  `FINAL_PERIOD` / `CLOCK_COUNTS_DOWN`. See `docs/SPORTS_UNIFICATION.md`.
  A plugin may start calling these once its manifest floors
  `ledmatrix_min_version` at the release that ships them.

- `src/base_classes/sports/capabilities/` — opt-in capabilities for the sports
  scoreboards, composed by inheritance rather than gated by config branches
  inside the base classes:
  - `CelebrationMixin` — the score/win takeover, merging the goal and score
    dialects behind the `score_phrase()` / `win_phrase()` hooks, the
    `COALESCE_SCORING_SEQUENCE` class attribute and the `_favorite_key` seam.
    Reads both the `celebrate_opponent_goals` and `celebrate_opponent_scores`
    config spellings. Sports that do not mix it in have none of this code in
    their MRO.
  - `RotationStrategy` + a name registry (`swrr`, `weighted`, `simple`,
    plus `register_rotation_strategy` for plugin-supplied orderings). Each
    built-in is verified against a verbatim transcription of the plugin
    implementation it replaces. An unknown name degrades to `simple`.

- `src/common/sports_scroll.py` — `SportsScrollDisplay` and
  `SportsScrollDisplayManager`, the shared scroll **orchestration** layer for
  the sports scoreboards, plus native support for
  `global_config['target_fps']` (the bundled plugin copies hardcode ~100 FPS
  via `scroll_delay` and never consult the global target). Content building
  (`prepare_scroll_content`, `_load_separator_icons`) is per-sport and stays an
  override point — see `docs/SPORTS_UNIFICATION.md` for where the line falls
  and why.

- `src/plugin_system/compatibility.py` — the single place that answers "can this
  plugin run on this core?", shared by the loader (advisory, at load time) and
  the store (blocking, at install/update time) so the two cannot drift. Reads
  every spelling published manifests use, including the deprecated
  `versions[].ledmatrix_min`. It does **not** yet evaluate `compatible_versions`,
  which is the schema-required field and can express upper bounds; closing that
  is tracked in `docs/SPORTS_UNIFICATION.md` before B6.
- `scripts/check_release_version.py` and a `Release version check` workflow —
  assert that a tag, the newest CHANGELOG heading and `src.__version__` agree,
  on pushed `v*` tags and published releases. Runnable via `workflow_dispatch`
  to check a tag *before* creating it. Added because `v3.1.0` was tagged six
  weeks before `src/__init__.py` was bumped to match, which is why devices
  installed from that release report `1.0.0`.

### Changed
- `src/__init__.py` bumped to **3.2.0** — the number the sunset rule keys on.
- **The plugin store refuses an incompatible install.**
  `StoreManager.install_plugin` now checks the downloaded manifest's declared
  floor against `src.__version__` and refuses when the plugin needs a newer
  core. The check sits in `install_plugin` because `_reinstall_with_rollback`
  calls it, so a refused *update* restores the version the user already had.
  Refusal requires evidence: an undeclared floor, an unparseable version on
  either side, or an untrustworthy core version all allow the install.
- **A failed install no longer destroys the plugin it replaced.**
  `install_plugin` previously deleted the existing plugin directory before
  downloading, so any later failure — a dropped connection, a malformed
  manifest, or the new compatibility refusal — left the user with nothing. The
  existing copy is now set aside and restored if the install fails, matching
  the protection `_reinstall_with_rollback` already gave the update path.
- `web_interface.__version__` re-exports `src.__version__` instead of carrying
  its own hardcoded `"3.0.0"`, which had drifted two majors from the core.
- **Live games are no longer dropped when the feed omits a game clock.**
  `SportsLive._is_game_really_over` previously (in the baseball and UFC
  plugin lineages) coerced a missing or non-string clock to the literal
  `"0:00"` and then treated the game as finished once `period >= 4`. Baseball
  has no game clock and `period` is the inning, so live MLB games disappeared
  from the scoreboard from the 5th inning onward; UFC was affected the same
  way. The clock check is now skipped when the clock is unusable, and the
  period threshold is the per-sport `FINAL_PERIOD` (hockey ends in P3).
  Sports whose clocks count up — soccer, AFL, NRL — set
  `CLOCK_COUNTS_DOWN = False` and never run the check at all, since `0:00`
  there means kickoff rather than expiry.

### Fixed
- **Plugin updates could hang the web request thread.** The per-plugin reinstall
  locks were non-reentrant, and `_reinstall_with_rollback` holds one across its
  call to `install_plugin` — which now takes the same lock to protect the
  set-aside/restore above. That nesting deadlocked
  `update_plugin → _reinstall_with_rollback → install_plugin`, the standard
  path for every monorepo plugin update. The locks are now `RLock`s.
- `FontManager` resolves `assets/fonts` against the core install root instead
  of the process working directory, so font loading works when the process
  starts elsewhere (e.g. the plugin safety harness on CI).
- Hockey events whose competitors carry no `statistics` array are no longer
  discarded. The extractor read `competitor["statistics"]` unguarded, so a
  `KeyError` inside the generator dropped the entire event despite valid
  scores and status; shot counts now fall back to `0`.
- Live baseball events that populate status only at the competition level are
  no longer discarded. The extractor read the event top-level
  `game_event["status"]` for the inning; real ESPN events duplicate it, but
  MiLB events synthesized from the MLB Stats API do not, so the lookup raised
  a bare `KeyError`. It now reads the already-validated competition-level
  status.
- `SportsLive._is_game_really_over` no longer crashes the live-update pass when
  a feed sends an explicit null `period`. `None >= FINAL_PERIOD` raised
  `TypeError`, and the only caller (`_detect_stale_games`) has no `try/except`
  — the same failure shape as the already-fixed null `period_text`.
- An expired clock spelled `"00:00"` now ends the game. The check compared the
  colon-stripped clock against a hand-listed set of literals, which `"0000"` is
  not a member of, so a finished game with a two-digit-minute clock stayed on
  the scoreboard indefinitely. The comparison is now numeric.
- `SportsCore._load_fonts` resolves `assets/fonts` through the `_font_root()`
  seam instead of the process working directory. Started outside the install
  root, every scoreboard font silently degraded to PIL's default bitmap face.
- `SportsCore._should_log` no longer raises `AttributeError` on the first
  warning of a run; `_last_warning_time` is initialized in `__init__` rather
  than lazily by an unrelated method.
- `SportsCore._resolve_project_path` resolved relative logo directories
  against `<root>/src` instead of the repo root after `sports.py` became a
  package — the class bodies moved byte-identically but `__file__` gained a
  directory. Both it and `_font_root` now derive from one `_INSTALL_ROOT`
  constant.

## 3.1.0

Baseline for this changelog. Highlights already shipped at this version:
skin system for sports scoreboards (#419), Vegas continuous-scroll overhaul
(#423), plugin update surfacing (#421).
