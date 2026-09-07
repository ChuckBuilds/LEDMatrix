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

## 3.3.0

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
