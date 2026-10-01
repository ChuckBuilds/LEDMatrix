# src/common

Helpers shared by core and plugins. This page lists every module, what it is
for, and whether plugins are expected to import it.

Rules for the package:

- Every module must import without display hardware: nothing here may import
  `src.display_manager` or `src.plugin_system` at module level
  ([`test/test_common_is_hardware_free.py`](../../test/test_common_is_hardware_free.py)).
  That keeps plugins that use it loadable by the web preview,
  `scripts/check_plugin.py` and tests on a laptop.
- A plugin that imports a module added in a given core release must declare
  that release as its minimum (`ledmatrix_min_version` in the manifest's
  `versions` entry). The "Since" column gives the release; "—" means it
  predates 3.1.0, "n/a" that plugins should not import it.
- `from src.common import ...` re-exports `APIHelper`, `ScrollHelper`,
  `LogoHelper`, `TextHelper`, `scroll_config` (plus `ScrollSettings`,
  `configure_scroll`, `resolve_scroll_settings`, `refresh_hz_from_config`) and
  the adaptive layout names below ([`__init__.py`](__init__.py)).

## Summary

| Module | For | Plugins import it? | Since |
|---|---|---|---|
| [`api_helper`](#api_helper) | HTTP GET/POST with caching and rate limiting | Yes | — |
| [`bdf_font`](#bdf_font) | Load and draw BDF bitmap fonts | Yes, if drawing BDF text directly | 3.5.0 |
| [`espn_dates`](#espn_dates) | Fetch ESPN scoreboards across a date range | Yes (scoreboards) | 3.5.0 |
| [`favorite_team_check`](#favorite_team_check) | Log why a favourite team code shows nothing | Yes (scoreboards) | 3.6.0 |
| [`font_layout`](#font_layout) | Reproducible TrueType loading, crisp sizes | Yes | 3.4.0 |
| [`frame_timing`](#frame_timing) | Timing of every presented frame, stall watchdog | No, core-internal | n/a |
| [`json_body`](#json_body) | Parse a response body as JSON, with orjson if installed | Optional (large payloads) | 3.5.0 |
| [`logo_helper`](#logo_helper) | Load, resize and cache team logos | Yes | — |
| [`path_safety`](#path_safety) | Turn request-supplied names into safe paths | No, core-internal | n/a |
| [`permission_utils`](#permission_utils) | File modes and shared-group ownership | Rarely | — |
| [`render_gate`](#render_gate) | Keep background Python off the GIL while the panel swaps | No, core-internal | n/a |
| [`scroll_config`](#scroll_config) | Plugin scroll config → configured `ScrollHelper` | Yes (scrollers) | 3.4.0 |
| [`scroll_helper`](#scroll_helper) | Pre-rendered horizontal scrolling | Yes | — |
| [`snapshot_policy`](#snapshot_policy) | When to write the web preview frame | No, core-internal | n/a |
| [`sports_card`](#sports_card) | Scoreboard card settings, colours, fonts, dates | Yes (scoreboards) | 3.3.0 |
| [`sports_card_wrappers`](#sports_card_wrappers) | The game renderer's `sports_card` delegations | Yes (scoreboards) | 3.7.0 |
| [`sports_celebration`](#sports_celebration) | Draw a scoreboard's score/win celebration | Yes (scoreboards) | 3.7.0 |
| [`sports_display_rules`](#sports_display_rules) | Which games a scoreboard shows, for how long, and its scorebug date line | Yes (scoreboards) | 3.8.0 |
| [`sports_fetch`](#sports_fetch) | Scoreboard season fetch, lookback and live-odds decisions | Yes (scoreboards) | 3.7.0 |
| [`sports_font_path`](#sports_font_path) | Find a scoreboard's bundled font whatever the cwd | Yes (scoreboards) | 3.8.0 |
| [`sports_game_renderer`](#sports_game_renderer) | Scoreboard scroll/Vegas card geometry | Yes (scoreboards) | 3.3.0 |
| [`sports_helpers`](#sports_helpers) | Small helpers every scoreboard `sports.py` copies | Yes (scoreboards) | 3.5.0 |
| [`sports_live_scroll`](#sports_live_scroll) | Rebuild a live scroll strip mid-cycle without moving it | Yes (scoreboards) | 3.8.0 |
| [`sports_plugin_host`](#sports_plugin_host) | Helpers of a scoreboard's plugin class (`manager.py`) | Yes (scoreboards) | 3.8.0 |
| [`sports_scroll`](#sports_scroll) | Scoreboard scroll-display orchestration | Yes (scoreboards) | 3.2.0 |
| [`sports_shared`](#sports_shared) | Sport-independent `sports.py` methods | Yes (scoreboards) | 3.3.0 |
| [`sports_vegas`](#sports_vegas) | Live Vegas cards: keys, card cache, sticky odds, finished games | Yes (scoreboards) | 3.8.0 |
| [`sports_timezone`](#sports_timezone) | Which timezone a scoreboard draws start times in | Yes (scoreboards) | 3.6.0 |
| [`sync_manager`](#sync_manager) | Leader/follower sync between two displays | No, core-internal | n/a |
| [`text_helper`](#text_helper) | Outlined text, wrapping, measurement | Yes | — |

The `sports_*` mixin and card modules hold code the scoreboard plugins
used to carry as identical copies. Each module docstring lists what a host
class must provide. The plan behind them is in
[docs/SPORTS_UNIFICATION.md](../../docs/SPORTS_UNIFICATION.md).

## Adaptive layout and images

`src/adaptive_layout.py` and `src/adaptive_images.py` live outside this
package but are re-exported from `src.common`. They are the recommended way
to lay out a plugin that renders legibly on any panel size. Every
`BasePlugin` already has `self.layout`, `self.draw_fit()` and
`self.draw_image()`:

```python
regs = scoreboard_regions(self.layout.bounds, ctx=self.layout)
self.draw_image(away_logo, regs.away_slot, mode="fill_height",
                crop_to_ink=True, cache_key=f"logo:{abbr}")
self.draw_fit(score_text, regs.score_area)     # largest crisp font that fits
```

Key pieces: `Region`, the font ladders `LADDER_GRID` / `LADDER_ARCADE`,
`LayoutContext` (`fit_text`, `fit_image`, `by_tier`, `px`), and
`scoreboard_regions()` / `media_row()`. Guide:
[docs/ADAPTIVE_LAYOUT.md](../../docs/ADAPTIVE_LAYOUT.md).

## Modules

### api_helper

[`api_helper.py`](api_helper.py). `APIHelper(cache_manager=None, ...)`:
`get()` and `post()` with retries, optional caching through the cache
manager, and a minimum interval between requests (`set_rate_limit()`). Also has
`fetch_espn_scoreboard()`, `fetch_espn_standings()` and
`fetch_espn_rankings()`.

### bdf_font

[`bdf_font.py`](bdf_font.py). The one BDF loader and rasterizer.
`load_bdf_face(path, size)` returns `(face, realised_px)`, falling back to
the file's native strike when it has none at `size`;
`draw_bdf_text(draw, text, x, y, face, color)` draws top-left anchored onto a
PIL `ImageDraw` the same way the panel does. `read_bdf_native_size(path)`
and `clear_face_cache()` round it out. Faces are cached per thread (FreeType
faces are not thread-safe). `DisplayManager`, `FontManager`, `element_style`
and the plugin test harness all use it. Most plugins get BDF text through
`display_manager.draw_text()` or `FontManager` and never import this.

### espn_dates

[`espn_dates.py`](espn_dates.py). ESPN's site API rejects `dates=` ranges
and truncates results when `limit` is above 500. `fetch_espn_scoreboard()`
splits a range into month and day requests ESPN accepts and merges the
results; `espn_date_chunks()`, `fetch_espn_date_chunks()`,
`clamp_espn_limit()` and `merge_scoreboard_payloads()` are the pieces.
Scoreboard plugins also bundle a copy for older cores.

### favorite_team_check

[`favorite_team_check.py`](favorite_team_check.py).
`FavoriteTeamCheck(logger, leagues)`, where `leagues` maps a league key to
`(display name, ESPN sport/league path)`. `schedule(league_key, favorites)`
checks the configured favourite team codes against ESPN's team list once per
league, on a daemon thread, and logs a bad code with the nearest real one, or
says the league has nothing on yet; `reset()` re-arms it after a config edit.
Diagnostics only: every failure is swallowed. Scoreboard plugins also bundle
a copy for older cores.

### font_layout

[`font_layout.py`](font_layout.py). `load_truetype(path, size)` is
`ImageFont.truetype` with PIL's Basic layout engine pinned, so text lays out
the same whether or not the host Pillow has libraqm; use it for anything
drawn to the panel or compared against a golden image. `crisp_size()` gives
the size a bundled face renders on whole pixels at. `resolve_asset_path()`
resolves `assets/fonts/...` against the install root rather than the
working directory.

### frame_timing

[`frame_timing.py`](frame_timing.py). Core-internal. `DisplayManager`
records every presented frame in a `FrameTimingRecorder`, which writes
cumulative late-frame counters and histograms to `/dev/shm` for
`scripts/frame_soak.py` and `scripts/render_bench.py`. `StallWatchdog` logs
the stack of whatever holds up a scroll. See
[docs/SCROLL_PERFORMANCE.md](../../docs/SCROLL_PERFORMANCE.md).

### json_body

[`json_body.py`](json_body.py). `response_json(response)` is
`response.json()` parsed by orjson when it is installed, falling back to the
stdlib parser (and requests' own error) otherwise. For multi-MB payloads such
as a season schedule, where the parse holds the GIL and freezes the display.
A plugin that also runs on older cores should guard the import, as
`espn_dates` does.

### logo_helper

[`logo_helper.py`](logo_helper.py). `LogoHelper(display_width,
display_height, ...)`: `load_logo()`, `load_logo_with_download()`,
`get_logo_variations()`, `normalize_abbreviation()`, with an in-memory cache.

### path_safety

[`path_safety.py`](path_safety.py). Core-internal, used by web handlers that
open files named in a request. `safe_path_component(value)` returns the
value if it is one harmless path segment, else `None`;
`resolve_under(base, *parts)` returns the resolved path, or `None` if a part
is unsafe or the result would leave `base`; `safe_relative_parts()` splits a
relative path the same way. Both return the sanitised value rather than a
boolean, so a caller cannot check one string and open another.

### permission_utils

[`permission_utils.py`](permission_utils.py). The modes and ownership that
let the root display service and the web user share files:
`ensure_directory_permissions()`, `ensure_file_permissions()`, the
`get_*_mode()` functions, `ensure_shared_group_ownership()`,
`sudo_remove_directory()` and `install_requirements_file()` (the sudo
`safe_pip_install.sh` path). `ConfigManager`, `CacheManager` and the store
already call these; a plugin needs them only when it creates its own files
outside the cache. See [docs/PERMISSIONS.md](../../docs/PERMISSIONS.md).

### render_gate

[`render_gate.py`](render_gate.py). Core-internal. `RenderGate` is opened by
the render thread around each vsync swap; a background thread inside
`gate.yielding()` (Vegas's prefetch) parks while the gate is closed, so the
render thread finds the GIL free when its refresh arrives. It never parks a
thread holding a guarded lock or inside logging, threading or import code.

### scroll_config

[`scroll_config.py`](scroll_config.py). `configure(scroll_helper,
plugin_config=, global_config=, display_manager=, plugin_logger=)` reads a
plugin's scroll settings, snaps the speed to a whole number of pixels per
panel refresh, puts the helper in fixed-step mode and returns
`ScrollSettings`. Pass `settings.frame_hold` to
`display_manager.set_scrolling_state(True, frame_hold=...)` or the scroll
runs too fast. `resolve()` does the calculation without touching a helper.
See [docs/SCROLL_PERFORMANCE.md](../../docs/SCROLL_PERFORMANCE.md).

### scroll_helper

[`scroll_helper.py`](scroll_helper.py). `ScrollHelper(display_width,
display_height, logger=None)`: build a wide image once
(`create_scrolling_image()` or `set_scrolling_image()`), then per frame
`update_scroll_position()` and `get_visible_portion()`;
`is_scroll_complete()`, `calculate_dynamic_duration()` and
`get_dynamic_duration()` for timing. Configure it with `scroll_config`
rather than the `set_*` methods. Vegas mode reads a plugin's
`scroll_helper` image when the plugin has no `get_vegas_content()`.

### snapshot_policy

[`snapshot_policy.py`](snapshot_policy.py). Core-internal. `decide()`
tells `DisplayManager` whether to write `/tmp/led_matrix_preview.png`, only
touch its mtime, or skip, based on whether a browser is watching the preview.
The web health check reads the file's age.

### sports_card

[`sports_card.py`](sports_card.py). Free functions taking `config`, `fonts`
and `logger` explicitly: card options (`scroll_card_option()`,
`vs_text()`, `upcoming_center_mode()`), colours (`element_color()`,
`font_color()`, `score_color_for()`, `recent_score_color()`), favourite-team
rules (`favorite_teams_for()`, `side_is_favorite()`, `favorite_result()`),
dates (`format_game_date()`, `format_game_time()`, `card_tzinfo()`) and font
sizes (`schema_font_size()`, `resolve_font_size()`). A plugin keeps its own
method and delegates the body.

### sports_card_wrappers

[`sports_card_wrappers.py`](sports_card_wrappers.py).
`SportsCardWrappersMixin`: the one-line methods a scoreboard's game renderer
uses to call `sports_card` with its own `config` and `logger`
(`_vs_text()`, `_element_color()`, `_format_game_date()`, ... seventeen in
all), under their existing names. They are what `sports_game_renderer`'s
mixin expects its host to provide. No `__init__` and no state.

### sports_celebration

[`sports_celebration.py`](sports_celebration.py). `SportsCelebrationMixin`
draws the full-screen takeover a scoreboard shows when a team scores or wins
(`_draw_celebration_layout(celebration)`): a backdrop in the scoring team's
colours read off its crest, scenery, confetti, the headline and the score.
The colour helpers are free functions (`logo_palette()`, `lift_color()`,
`mix_color()`, ...). Deciding *when* to celebrate stays in the plugin, which
builds the celebration dict the docstring describes.

### sports_display_rules

[`sports_display_rules.py`](sports_display_rules.py). Two `SportsCore`
mixins: `SportsCardOptionsMixin` (`_card_option()`, which never lets the
upcoming scorebug lose both its date and time, and `_recent_date_text()`;
list it before `SportsCoreSharedMixin`) and `SportsGameRulesMixin`
(`_filtered_or_all()`, the no-favourites quality filter that fails open, and
`_effective_live_duration()`, the shorter dwell for a non-favourite live
game).

### sports_fetch

[`sports_fetch.py`](sports_fetch.py). `SportsFetchMixin`: the `SportsCore`
methods that decide which requests a scoreboard makes --
`_fetch_season_directly()` (a season, in chunks ESPN accepts),
`_background_fetches_espn_ranges()`, `_needs_previous_day()` (the live
lookback) and `_wants_live_odds()` (odds only for games near the screen).

### sports_font_path

[`sports_font_path.py`](sports_font_path.py). `resolve_font_path(path)`: the
path as given when it exists (relative to the cwd), else
`font_layout.resolve_asset_path(path)`. What the scoreboards'
`_resolve_font_path` copies return on a core that ships it.

### sports_game_renderer

[`sports_game_renderer.py`](sports_game_renderer.py).
`SportsGameRendererMixin`: the scroll/Vegas card geometry (centre gap, logo
slot, layout offsets, upcoming-card date and time). No `__init__` and no
state; add it as a base class of the plugin's game renderer and override
what differs.

### sports_helpers

[`sports_helpers.py`](sports_helpers.py). Free functions `clamp_window()`,
`clamp_seconds()`, `logo_needs_refresh()`, `spread_weighted_order()`, and
`SportsHelpersMixin` with the scoreboards' `_mode_customization`,
`_setting_int`, `_reset_dwell_on_reentry`, `_next_switch_index`,
`_odds_color` and `_upcoming_date_and_time_text` under their existing names.
Nothing in core uses it.

### sports_live_scroll

[`sports_live_scroll.py`](sports_live_scroll.py). `SportsLiveScrollMixin`:
keeps a live scroll strip current. It fingerprints the live games (the clock
and the display pipeline's own keys excluded, via the host's
`LIVE_VOLATILE_FIELDS`), rebuilds when they change, rate-limited by what a
rebuild costs, and `_preserving_scroll_position()` keeps the marquee where
it was. Pairs with `SportsPluginHostMixin`, whose `_dispatch_switch_refresh()`
it uses.

### sports_plugin_host

[`sports_plugin_host.py`](sports_plugin_host.py). `SportsPluginHostMixin`:
helpers of a scoreboard's `BasePlugin` subclass. `get_vegas_priority_weight()`
(more Vegas slots while a favourite plays, found across every plugin's data
shape), `_dispatch_switch_refresh()` (a manager refresh on a daemon thread, so
`display()` never waits on the network), `get_vegas_content_type()` and small
dynamic-duration helpers. List it before `BasePlugin`.

### sports_scroll

[`sports_scroll.py`](sports_scroll.py). `SportsScrollDisplay` and
`SportsScrollDisplayManager`: the scroll-display orchestration the
scoreboards share (Vegas items, dynamic duration, frame loop), paced through
`scroll_config`. Subclasses supply `prepare_scroll_content()` and set
`SCROLL_LEAGUE_KEYS`; see the module docstring for an example.

### sports_shared

[`sports_shared.py`](sports_shared.py). `SportsCoreSharedMixin`,
`SportsLiveSharedMixin`, `SportsRecentSharedMixin`: the `sports.py` methods
that were identical in every scoreboard (game selection and rotation,
fonts, colours, dates, the switch-mode upcoming card). The docstring lists
the attributes the host class must have and the three methods deliberately
left out.

### sports_vegas

[`sports_vegas.py`](sports_vegas.py). What a scoreboard needs for live Vegas
cards (one element per game, swapped in place while it scrolls):
`game_key()`, `game_fingerprint()`, `dedupe_games()`, `VegasCardCache` (draws
a card only when its fingerprint changes), `StickyOdds` (keeps a card's odds
through a live poll that left them out), and `finished_games()` /
`with_finished_games()` (a game that just went final keeps its card, showing
FINAL). `SportsScrollDisplay.build_vegas_elements()` in `sports_scroll` puts
them together; a scoreboard not built on it (UFC) uses them directly.

### sports_timezone

[`sports_timezone.py`](sports_timezone.py).
`resolve_timezone_name(config, plugin_manager, cache_manager, log, *,
plugin_label, writeback_fixed_in=None)` and `resolve_timezone(...)` (the same
as a pytz zone): the plugin's own `timezone`, then the global one via either
manager's `config_manager`, then the host's zone (`system_timezone_name()`),
then UTC. `plugin_label` names the plugin in the warning logged when nothing
resolves; `writeback_fixed_in` is for a plugin that once wrote `"UTC"` into
the saved config (a bare plugin-level `"UTC"` is then ignored when another
source disagrees). Scoreboard plugins also bundle a copy for older cores.

### sync_manager

[`sync_manager.py`](sync_manager.py). Core-internal. `DisplaySyncManager`
links two displays as leader and follower (`sync.role` in config) over UDP
port 5765, plus TCP on the next port for scroll images. The leader drives the
scroll and sends the follower its part of each frame; a follower falls back
to its own plugins when the leader goes quiet. Rows and columns must match.
Created by `DisplayController`; works with any plugin.

### text_helper

[`text_helper.py`](text_helper.py). `TextHelper(font_dir=None, ...)`:
`load_fonts()`, `draw_text_with_outline()`, `get_text_width()`,
`get_text_dimensions()`, `center_text()`, `wrap_text()`,
`draw_multiline_text()`, `create_text_image()`.

## Logging

Modules here create their logger with `logging.getLogger(__name__)`, which is
the same logger `src.logging_config.get_logger(__name__)` returns. The helper
classes (`APIHelper`, `LogoHelper`, `ScrollHelper`, `TextHelper`) and
`espn_dates` take an optional `logger`. In a plugin, pass `self.logger`: it is
created by `get_logger(..., plugin_id=...)` in `BasePlugin`, so messages carry
the plugin id.

## Adding a module

- Keep it importable without hardware (see the test above).
- Give it a module docstring that says what it is for and, if it is a mixin,
  what the host class must provide.
- Add it to the table on this page and, if plugins may import it, to the
  CHANGELOG with the release to floor on.
