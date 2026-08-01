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

## 3.2.0

**The first release shipping the unified sports library.** This is the version
a sports plugin floors `ledmatrix_min_version` at before deleting its bundled
copy of `sports.py`, `scroll_display.py`, `data_sources.py` or
`base_odds_manager.py` — the sunset rule in
`docs/plugin-development/08-shared-sports-code.md` keys on exactly this number.

Adoption is deliberately staged: the modules below ship here, plugins adopt them
behind guarded imports, and only then do the bundled copies go away. Nothing in
this release changes what an existing plugin loads.

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

### Changed
- `src/__init__.py` bumped to **3.2.0** — the number the sunset rule keys on.
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
