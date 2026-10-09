# Sports Code Unification — Architecture

How the nine sports scoreboard plugins converge onto shared core code **without**
becoming nine clients of a god class.

## The problem

Nine plugins (`afl`, `baseball`, `basketball`, `football`, `hockey`, `lacrosse`,
`nrl`, `soccer`, `ufc`) each ship a ~3,000-line `sports.py` descended from this
repo's former `src/base_classes/sports.py` (since removed). They have drifted
into three lineages, and only 28 of the 66 methods appearing across them are
present in all nine. One logical fix (the UTC start-time bug) cost 75 files.

Merging everything into one base class would fix the duplication and create a
worse problem: a single 2,500-line class that all nine plugins inherit, where any
change has a nine-plugin blast radius and per-sport behavior survives only as
`if self.sport == "hockey"` branches.

## Three properties, three mechanisms

These are independent concerns. Conflating them is what produces god classes.

### Upgradability — a plugin keeps working across core versions

| Rule | Mechanism |
|---|---|
| Plugin loads on a core that predates a module | Guarded import with a bundled fallback (`try: from src.X import Y / except ModuleNotFoundError: from y import Y`) |
| Plugin loads on a core that predates a *method* | Capability probing — `hasattr(SportsCore, "_detect_stale_games")` — never a version comparison. The loader's compat check is advisory-only (it logs and continues), so probing is the real protection. |
| Core changes never break a plugin's rendering | The **view-model contract**: the game dict each plugin's `_extract_game_details_common` builds is read by the shared `src/common` renderers, so its keys may be added, never renamed or removed. |
| A plugin can drop its bundled copy safely | The **sunset rule**: its manifest must floor `ledmatrix_min_version` at the first core release shipping the module (recorded in `CHANGELOG.md`) — *necessary but not sufficient*. The store enforces that floor on every registry-managed install and on both supported update paths (sideloading via `install_from_url` is not gated), but a floor cannot reach a user who never updates, so the copy also waits for the B6 gate below. |

The core API is **additive-only**. A method the plugins call is never removed or
given a new required parameter; new behavior arrives as new methods with
defaults, or as capabilities they opt into.

### Reusability — write once, nine plugins benefit

Only code that is **identical across every plugin that carries it** moves into
core. Stages 0–3 moved the copies that already were; what is left has drifted,
and earns promotion by being reconciled first — made identical in all nine
plugins, one method family per release, with every visible difference decided
rather than averaged away. See [Roadmap](#roadmap).

### Modularity — a change to one feature cannot reach a plugin that doesn't use it

This is the property the naive merge destroys, and it is enforced structurally:

1. **Capabilities are separate modules composed by inheritance, not config
   branches inside the base class.** Hockey has no celebrations, so
   `HockeyLive` does not inherit `CelebrationMixin` — the celebration code is not
   merely disabled for hockey, it is *not in hockey's MRO at all*. No shared
   state, no dead branches, no risk. Contrast with
   `if self.celebrations_enabled:` inside `SportsLive`, where a bug in
   celebration code can still crash a plugin that never wanted the feature.

2. **Variant behavior is a strategy object chosen by name, not a branch.**
   Live rotation exists in three dialects across the lineages; core ships all
   three behind `rotation_strategy: "swrr" | "weighted" | "simple"` and a plugin
   may register its own. Core never learns sport names.

3. **Sport-specific behavior is a documented override point.** The base class
   declares the seam; the plugin fills it. Basketball's tournament-round parsing
   and baseball's BDF sizing stay in their plugins forever — they are not
   candidates for promotion, and core must never grow a branch for them.

4. **Files bound the blast radius.** Capabilities live in their own modules so a
   diff shows at a glance which plugins a change can reach.

## Layering

`src/base_classes/` has been removed: no scoreboard plugin built on it. B1 and
B2 below promoted code into it (`SportsCore`, the mode classes,
`CelebrationMixin`, the rotation strategies); the override points and
capabilities sections record that design, but none of it ships in core any
more. Shared sports code lives in `src/common`:

| Module | Since | Holds |
|---|---|---|
| `sports_scroll.py` | 3.2.0 | `SportsScrollDisplay` / `SportsScrollDisplayManager` — scroll orchestration (content building stays in the plugins) |
| `sports_card.py` | 3.3.0 | Free functions for card settings, colours, favourite-team rules, dates and font sizes |
| `sports_game_renderer.py` | 3.3.0 | `SportsGameRendererMixin` — scroll/Vegas card geometry |
| `sports_shared.py` | 3.3.0 | `SportsCoreSharedMixin`, `SportsLiveSharedMixin`, `SportsRecentSharedMixin` — the sport-independent `sports.py` methods |
| `sports_helpers.py` | 3.5.0 | clamp/logo/rotation free functions and `SportsHelpersMixin`, plus the `_favorite_key` seam |
| `espn_dates.py` | 3.5.0 | ESPN date-range and `limit` workarounds |
| `favorite_team_check.py` | 3.6.0 | `FavoriteTeamCheck` — logs why a favourite team code shows nothing |
| `sports_timezone.py` | 3.6.0 | Which timezone start times are drawn in (`resolve_timezone_name`) |
| `sports_celebration.py` | 3.7.0 | `SportsCelebrationMixin` — draws the score/win takeover; colour helpers |
| `sports_fetch.py` | 3.7.0 | `SportsFetchMixin` — season fetch, live lookback and live-odds decisions |
| `sports_card_wrappers.py` | 3.7.0 | `SportsCardWrappersMixin` — the game renderer's `sports_card` delegations |
| `sports_plugin_host.py` | 3.8.0 | `SportsPluginHostMixin` — the plugin class's (`manager.py`) identical helpers: Vegas weight, off-thread switch refresh |
| `sports_live_scroll.py` | 3.8.0 | `SportsLiveScrollMixin` — rebuild a live scroll strip mid-cycle, keeping the marquee's place |
| `sports_display_rules.py` | 3.8.0 | `SportsCardOptionsMixin`, `SportsGameRulesMixin` — scorebug date options, the no-favourites filter, non-favourite live dwell |
| `sports_font_path.py` | 3.8.0 | `resolve_font_path` — what the plugins' `_resolve_font_path` copies return |
| `sports_game_over.py` | 3.8.1 | `SportsGameOverMixin` — `_is_game_really_over`, with the `FINAL_PERIOD` seam (family 5) |
| `sports_favorites.py` | 3.8.2 | `SportsFavoritesMixin`, `SportsUpcomingFavoritesMixin`, `SportsRecentFavoritesMixin` — `_is_favorite_game` and the favourites-only picks, on the `_favorite_key` seam (family 6) |
| `sports_rotation.py` | 3.8.4 | `SportsRotationMixin` — the other-games rotation (importance order, the window, odds on rotated-in games), on the `_rankings_loaded` seam (family 7) |

Each is described in [src/common/README.md](../src/common/README.md).

### Converging on `src/common`

The scoreboards never built on `src/base_classes` (now removed); their own
`sports.py` copies had moved past it. So shared code now lands in hardware-free `src/common`
modules taken from the plugin copies, each a **new module** rather than growth
on an existing one: a plugin that deletes a method copy and relies on an older
module having gained it fails at runtime with an `AttributeError`, while a
missing module fails at load, where the version checks can see it.
`sports_helpers.py` holds `_favorite_key`, the override point listed below;
`sports_favorites.py` is what calls it.
Each promoted module has a parity test that compares its bodies against the
plugin copies when `LEDMATRIX_PLUGINS` points at a checkout
(`test_sports_helpers.py`, `test_sports_stage3_parity.py`), and
`test/test_common_is_hardware_free.py` keeps `src/common` free of
`rgbmatrix`, `src.display_manager` and `src.plugin_system`. How a plugin adopts a
module and drops its copy is documented in the plugins repo's
`docs/plugin-development/08-shared-sports-code.md`.

## Override points (the plugin-facing seam)

The base class calls these; plugins implement or override them. This table is the
contract — additions require a default implementation, removals require a
deprecation cycle.

| Hook | Purpose | Default |
|---|---|---|
| `_fetch_data()` | Sport's schedule source | abstract |
| `_extract_game_details(event)` | Sport-specific view-model fields on top of the common ones | delegates to `_extract_game_details_common` |
| `_draw_scorebug_layout(game, force_clear)` | Sport's card rendering | base layout |
| `_custom_scorebug_layout(game, draw)` | Per-sport overlay on the base layout | no-op |
| `score_phrase(points, team_abbr)` | Celebration wording (`"GOOOOAAALLL!"` vs `"TOUCHDOWN!"`). `points` is the score delta, which sports with variable-value scores use to name the play | `"<abbr> SCORES!"` — only consulted when `CelebrationMixin` is present |
| `win_phrase(team_abbr)` | Win-celebration wording | `"<abbr> WINS!"` — mixin only |
| `_rankings_loaded()` | Whether a poll loaded, so `_by_importance` orders by rank (`sports_rotation`) | `_team_rankings_cache` is non-empty. football also counts its rankings keyed by team id |
| `_favorite_key(game, side)` | Which view-model field identifies a team for favorites matching. `sports_favorites` compares it, and each `favorite_teams` entry, stripped and upper-cased; a `None` matches nothing | `game["<side>_abbr"]`. nrl returns the ESPN team id, `None` when it is missing |
| `_config_schema_path()` | Plugin's `config_schema.json` — returning it routes `_get_layout_offset` through the `src.element_style` resolver (and gives it the defaults to compare against) | `None`, i.e. the classic inline `customization.layout` read |
| `_font_root()` | Directory to resolve `assets/fonts` against | core install root |

Two class attributes serve the same purpose for values that are per-sport
constants rather than behavior:

| Attribute | Meaning | Default |
|---|---|---|
| `FINAL_PERIOD` | Period from which a 0:00 clock ends a game (`sports_game_over`) | `None`: the clock never ends a game (afl, nrl, soccer, baseball, ufc). Hockey sets `3`; basketball, football and lacrosse `4` |
| `COALESCE_SCORING_SEQUENCE` | Fold score increments arriving during an active celebration into that one celebration | `False` (football overrides to `True` — a touchdown lands as +6, then +1 for the extra point) |

### Why these are seams and not branches

`_favorite_key` exists because NRL abbreviations are **not unique** — "NEW" is both
Newcastle Knights and New Zealand Warriors, "CAN" both Canberra and Canterbury —
so NRL matches favorites on team ID. Flattening every plugin to abbreviations
would silently select the wrong club for NRL users. The base declares the seam,
NRL fills it, and core never learns the string `"nrl"`.

`FINAL_PERIOD` exists for the same reason in the opposite direction: a
soccer clock reading `0:00` means the match has not kicked off, so running the
clock-expiry rule there would evict live games. Those sports declare `None`,
and so do baseball (innings, not a clock) and ufc (a bout ends only on ESPN's
final status). One attribute covers both questions, whether the clock can end
a game and from which period, so no separate count-down flag was added.

`COALESCE_SCORING_SEQUENCE` is another of the same kind. In football one
scoring play arrives as two score updates, so the follow-up must be folded into
the first celebration; in soccer two increments a few seconds apart are two real
goals, and folding them would swallow one. Neither default is "right" — which is
precisely why it is a declared per-sport constant rather than a hidden
assumption baked into the shared body.

## Capabilities

```
capabilities/
  celebrations.py   CelebrationMixin        opt-in: afl, nrl, soccer, football
  rotation.py       RotationStrategy + registry
```

**`CelebrationMixin`** merges the two dialects the lineages grew
(`_check_for_goal`/`celebrate_opponent_goals` vs
`_check_for_score`/`celebrate_opponent_scores`). Their bodies were identical
apart from three things, each now a seam: wording (`score_phrase`), follow-up
suppression (`COALESCE_SCORING_SEQUENCE`), and team identity (`_favorite_key`,
so NRL matches on id). Both config spellings are read, so a plugin adopting the
mixin keeps working with the keys already in its published schema.

Mix it in **before** the mode class — `class SoccerLive(CelebrationMixin,
SportsLive)` — so the celebration `display()` runs first and falls through to
the scorebug via `super()`.

What shipped is narrower. `src/common/sports_celebration.py`
(`SportsCelebrationMixin`) holds only the drawing, which is identical in the
five scoreboards that celebrate (afl, football, hockey, nrl, soccer — hockey
grew celebrations after this was written). Arming a celebration stays in each
plugin: the trigger bodies differ (nrl matches favourites by team id, football
folds a touchdown's extra point into one celebration and picks scenery by
points), and so does `display()`. The seams above were not needed to move the
drawing, so none was added.

**Rotation strategies.** The three "dialects" turned out to be one algorithm
(Smooth Weighted Round-Robin) in two shapes: an incremental picker holding state
across calls (afl/nrl/soccer) and a precomputed per-cycle list
(football/baseball/basketball, and hockey with a different loop shape). They
agree within a cycle and differ only at the boundary — the incremental form has
no restart seam — so core ships both rather than declaring a winner:

```python
self.rotation = get_rotation_strategy("swrr", weight_for=self._live_weight)
```

`weight_for` is supplied by the host, so the *favorites* policy stays with the
plugin and `rotation.py` never learns what a favorite is. An unknown strategy
name degrades to `simple` rather than raising: the name comes from user config,
and a typo should cost the boost, not the scoreboard. When a plugin needs an
ordering that core does not ship, it calls `register_rotation_strategy` to add
its own — rather than core growing a branch for it.

`test_sports_capabilities.py` (removed with `src/base_classes`) checked each
strategy against a **verbatim transcription** of the plugin code it replaces,
over every live-game shape up to four games. That differential is what B5 deletes the bundled copies on the
strength of.

## Scroll display — where the promotion line falls

`src/common/sports_scroll.py` is deliberately *not* a superset of the ten
`scroll_display.py` copies. A method-level comparison of the eight that share a
shape (f1 and ufc are genuine forks) found a sharp split:

| Layer | Evidence | Outcome |
|---|---|---|
| Orchestration — `get_all_vegas_content_items`, `clear_all`, `get_scroll_info`, `get_dynamic_duration`, `is_complete`, `display_frame` | identical to 96–100% similar across all eight | **promoted** |
| Settings — `_get_scroll_settings` | one algorithm; the copies differ *only* in which league keys they walk | **promoted**, with the ladder as data (`SCROLL_LEAGUE_KEYS`) |
| Content — `prepare_scroll_content`, `_load_separator_icons` | 8 distinct bodies across 8 plugins (145 lines, 53% similar at worst); icons 6% | **override point, permanently** |

Same name, different job: `prepare_scroll_content` draws *this sport's* game
card. Merging the eight bodies would be the exact mistake the promotion rule
exists to prevent, so the base class raises `NotImplementedError` rather than
rendering something plausible — a base that rendered *something* would let a
plugin ship a silently blank scroll.

The one behavior the upstreamed version adds is native
`global_config['target_fps']` support. The bundled copies hardcode ~100 FPS via
`scroll_delay = 0.01` and never consult the global smooth-scrolling target;
Part A threaded it through each copy by hand, and this makes that threading
legacy compatibility rather than the mechanism.

> **Superseded.** Once presentation became frame-locked (#545) the helper
> steps a fixed whole-pixel amount per presented frame and the panel presents
> at its own refresh, so honouring `target_fps` only turned it into a speed
> multiplier (60 doubled a scoreboard's speed, 200 halved it). `sports_scroll`
> no longer reads it: the crisp-speed ladder uses the panel refresh
> (`display_manager.refresh_hz`), and speed comes from
> `scroll_settings.scroll_speed` alone. See `docs/SCROLL_PERFORMANCE.md`.

## Roadmap

### Done: stages 0–3

The second project, after the B phases below: move what the nine `sports.py`
copies (and their support files) carried byte-identically into `src/common`,
one new module per stage, and delete the copies once the plugins floor on the
release that ships it.

| Stage | Core | Plugins (ledmatrix-plugins) | What moved |
|---|---|---|---|
| 0 | none needed; found #662 (odds `no_odds` marker) and #663 (a reloaded plugin's dir goes first on `sys.path`) | #562 | Deleted the bundled copies nothing could reach (`base_odds_manager`, `logo_downloader`, three unused data sources, ~4.2k lines); three UFC fixes |
| 1 | 3.5.0: `sports_helpers` (#583), `espn_dates`, `json_body` | #563 (1a, the eight team scoreboards), #564 (1b, ufc); floor 3.5.0 | The identical helpers and ESPN date-range handling; ufc also adopted the `sports_shared` mixins |
| 2 | 3.6.0: `favorite_team_check`, `sports_timezone`; fixes in 3.6.1 (#667) and 3.6.2 (#670) | #565 (guarded adoption), #567 (f1), #570 (sunset, floor 3.6.1), #571 (soccer, 3.6.2) | The favourite-team check (seven copies) and the timezone resolver (ten); each plugin keeps a thin timezone binding |
| 3 | 3.7.0 (#672): `sports_celebration`, `sports_fetch`, `sports_card_wrappers` | #572 (goldens first), #574 (floor 3.7.0, copies deleted) | Celebration drawing (five plugins), four fetch methods (nine), seventeen card delegations (eight renderers) |

Stage 3 was re-checked independently when this roadmap was written: #574's
parent and #574 itself, rendered through the core harness against core 3.7.0,
gave pixel-identical output for all 399 frames (192 harness screens across the
nine plugins at the eight default sizes, 72 scroll/Vegas cards, 135
celebration frames), with a parent-vs-parent rerun as the determinism control.

### Stage 4: the identical sweep (done: core 3.8.0, adopted)

Re-measured on ledmatrix-plugins `56c4f15` (2026-09-30) the report still
lists 58 families identical in every copy. Stage 4 moves the ones that are
identical across the nine, or across eight with the ninth lacking the
method, into four new modules: `sports_plugin_host` (ten `manager.py`
helpers, all nine), `sports_live_scroll` (eight `manager.py` methods, every
plugin with a live strip, so not ufc), `sports_display_rules` (four
`sports.py` methods, in two mixins because their carriers differ) and
`sports_font_path`. The parity test (`test/test_sports_stage4_parity.py`)
compares each with every plugin copy using this report's own normalisation,
plus decorators and constant values, which the normalisation drops.

`_resolve_font_path` was meant to be replaced by
`font_layout.resolve_asset_path`, but that never looks in the cwd, and the
plugins' copy does first, so the swap would change which font a process
started from another checkout loads. `resolve_font_path` is the copy's
behaviour on a core that ships it, checked path for path against all 17
copies (`test/test_sports_font_path.py`).

Left in the plugins, though identical:

- `_get_timezone`, `_extract_game_details`, `_fetch_data` (nine): a
  per-plugin import and the abstract contract, as in stage 3.
- `_schema_font_size`, `_resolve_font_size` (eight renderers): they read the
  plugin's own `_SCHEMA_PATH`, as in stage 3.
- The 29 families carried by seven plugins or fewer: the afl/nrl/soccer
  lineage's own helpers (`_swrr_advance`, `_refresh_switch_mode_managers`,
  `_initialize_logo_dir`, ...), the multi-league helpers
  (`_resolve_managers_for_mode`, `_extract_mode_type`, ...), and eleven
  two-plugin helpers. Each is one lineage's code; most go when
  family 13 or 14 reconciles the code around them. `_odds_color` (seven
  renderers) is already core's, in `SportsHelpersMixin`; a renderer that
  wants it can inherit that.

### Family 5: the game-over check (done: core 3.8.1, adopted)

The pilot of the method below. ledmatrix-plugins `scripts/test_game_over_check.py`
(#621) pinned 3,115 answers across the nine plugins first; the reconcile
(ledmatrix-plugins #625) made the five bodies one and
changed only the cells the owner's decisions under
[Product decisions](#product-decisions-each-family-needs) explain: ufc's
clock rule (65 cells), baseball's dormant one (53, every one a game with a
`period` baseball's games never carry), and a level score at 0:00 (five
cells in hockey, basketball, football and lacrosse). The harness renders
were pixel-identical. `src/common/sports_game_over.py` holds the body;
`test/test_sports_game_over_parity.py` compares it, and each plugin's
`FINAL_PERIOD`, with the plugin copies. Core 3.8.1 shipped it, and all nine
scoreboards inherit it and floor on 3.8.1 (ledmatrix-plugins #631).

### Family 6: favourite matching (done: core 3.8.2, adopted)

ledmatrix-plugins `scripts/test_favourite_matching.py` (#634) pinned 204 rows
across the nine plugins first: `_is_favorite_game` on each manager role, the
two selection methods, the real `update()` with favourites-only on and off,
and the INFO summary; the reconcile extends it to 217 (a lower-case and a
padded favourite through `update()`, and the live favourite boost). The reconcile (ledmatrix-plugins
#635) made `_is_favorite_game` one body on `SportsCore`
(afl and soccer's `SportsUpcoming` copies and five `SportsLive` copies, all
redundant, are gone), added `_favorite_code` beside it, and gave nrl a
`_favorite_key` override instead of its own copies. So that a lower-case
favourite works on a favourites-only Upcoming board, the Upcoming `update()`'s
favourites-only pre-filter and the basketball, hockey and lacrosse live boost
now ask `_is_favorite_game` too (a one-line change each; `update()` itself is
family 13). Of 3,897 cells only those the decisions above explain changed:
case and spaces in eight plugins (30-40 each), the id-less duplicate fix (6-8
each), nrl's key (6) and its "None" match (6), and the INFO line in baseball,
football and ufc. The harness renders were byte-identical. `src/common/sports_favorites.py` holds the
bodies, one mixin per carrying class; `test/test_sports_favorites_parity.py`
compares them with the plugin copies and checks that only nrl overrides
`_favorite_key`. Core 3.8.2 shipped it, and all nine scoreboards inherit it and
floor on 3.8.2 (ledmatrix-plugins #637).

Left for later families: the live screens' favourites-only filter
(`_classify_live_game` and its inline copies) and favourites-first sort still
compare abbreviations exactly, and
`SportsCoreSharedMixin._round_robin_favorites` groups favourites by raw
abbreviation (or by `_team_in` where a plugin has one) instead of through
`_favorite_key`. The result-colour helpers also wait (decision above).

### Family 7: the other-games rotation (core done; adoption waits for a release)

ledmatrix-plugins `scripts/test_other_games_rotation.py` (#640) pinned 96 rows
across the nine plugins first: `_by_importance` per rankings table, core's
`_favorites_first` pools per favourites, quality, divisions and rankings, the
window over time, the real `update()` followed by `display()`'s rotation call
(the list, the card on screen, redraws and how often the list is recomposed),
`update()` and `display()` advancing the window in sequence and interleaved on
two threads, odds for rotated-in games, and `favorite_rotation_boost`'s
switch order. The reconcile (ledmatrix-plugins #641)
made `_by_importance`, `_other_games_window`, `_advance_other_games_if_due`,
`_rotate_other_games_on_display` and `_attach_odds_to_rotated_games` one body
on `SportsCore` (ufc gains the odds helper), with `_rankings_loaded` as the
seam `_by_importance` asks: the abbreviation table by default, football's
override also counts its rankings keyed by team id. Of 864 cells only those
the decisions below explain changed: the window lock (the interleaved row, in
the seven plugins with a reachable `update()` other than football), the
due-check's fallback pool (two rows in the same seven) and ufc's rotated-in
odds (one cell). The harness renders were byte-identical (208 PNGs).
`src/common/sports_rotation.py` holds the bodies in one mixin,
`SportsRotationMixin`; `test/test_sports_rotation_parity.py` compares them
with the plugin copies and checks that only football overrides
`_rankings_loaded`.

Left for later families: in eight plugins the no-favourites branch of
`update()` still picks a fixed "next N" through `_filtered_or_all` and never
builds the pools, so nothing rotates on a board with no favourites; football
routes it through `_favorites_first(games, 0, N)` (decision below, family 13).
ufc's MMA managers override `update()` and never build the pools, so the
rotation is dormant there. `_best_rank`, `_is_ranked_game` and
`_passes_other_filters` are family 8.

### Why the method changes

Byte-identical promotion has nearly run dry. Measured on ledmatrix-plugins
`4327c2e` (2026-09-29, after stage 3) with `scripts/sports_drift_report.py`:

| File | Method families | In all nine | Method lines | Identical copies beyond the first | Drifted families |
|---|---:|---:|---:|---:|---:|
| `sports.py` | 94 | 30 | 30,976 | 1,479 lines | 19 |
| `manager.py` | 114 | 36 | 27,137 | 3,198 lines | 38 |
| `game_renderer.py` (8 plugins) | 89 | — | 6,830 | 546 lines | 11 |

"Drifted" means in at least seven plugins with at least three different
bodies. Everything still identical adds up to about 5,200 duplicated lines;
the rest of the ~65,000 method lines is drifted, one outlier away from
identical, or unique to one plugin. Drifted code cannot move unchanged, so
consolidation stalls unless the copies are made identical first.
`manager.py`, the largest copy of all and the layer the display controller and
Vegas talk to, was in no plan before this one.

### The method: reconcile, then promote

**Owner decision (2026-09-29):** each release, pick one drifted method family,
make all nine copies identical, then promote it to core. A *family* here is a
set of methods that share state and ship together (the rankings methods, the
game-over check); the report measures each method in it. The procedure:

1. **Measure.** `python scripts/sports_drift_report.py --family sports.py::<name> --diff`
   lists which plugins share each body and diffs every variant against the
   most common one. Put the grouping in the PR.
2. **Classify every difference**, and say which class in the PR:
   - *A fix one copy has and the others lack* (a lock, a guard, a correct
     season year). Port it. It is a behaviour change, so it gets a CHANGELOG
     line in each plugin.
   - *A per-sport fact* (hockey ends in period 3; a soccer clock counts up).
     Make it a declared class constant or override point with a default, as
     `FINAL_PERIOD`, `COALESCE_SCORING_SEQUENCE` and `_favorite_key` are,
     and add it to the tables above. Never a sport-name
     branch: core must not learn sport names.
   - *A product difference*: anything a user can see (which games show, a
     colour, a date, a badge, how long a screen stays). The owner picks the
     behaviour before the code changes; the decision goes in the PR and in a
     test that pins it (as `test/test_sports_twins.py` pins the twins).
   - *Noise*: comments, log wording, dead branches. Pick one.
3. **Pin the output first.** Before touching the family, its output must be
   covered: the harness goldens (`test/golden`), the scroll cards
   (`golden-cards`) and celebrations (`golden-celebration`) for drawing
   families; for logic families, a table-driven test over the nine plugins'
   fixture games. Missing coverage lands in its own PR first, as #572 did for
   stage 3.
4. **Reconcile in the plugins** (a monorepo PR). The report must show one
   variant per class for the family. Render every touched plugin before and
   after through the harness and diff pixels, not hashes. Every differing
   frame must match a recorded product decision; any other difference is a
   bug. Bump each plugin's `version`, add a `versions[]` entry and a CHANGELOG
   entry, and run `update_registry.py`.
5. **Promote in core**: a new `src/common` module per family (a new module, not
   growth on an old one, for the reason under Converging on `src/common`), a
   parity test against the plugin copies, and a CHANGELOG module entry naming
   the release that ships it.
6. **Adopt** once that release is out: each plugin floors on it, inherits the
   mixin, deletes its copy, gains a sunset guard (like the monorepo's
   `scripts/test_stage3_mixin_copies.py`), and is pixel-diffed again; the
   expected difference is zero.
7. **Re-measure** and update the numbers here.

A family is only reconciled when *all nine* agree. Leaving one plugin behind
recreates the drift the report exists to measure.

Soaks: pixel diffs prove the drawing, not the timing. A family that changes
when data arrives or which games are live (5, 7, 9, 13 and 14 below) needs a
live-game soak on a rig, and out-of-season sports wait for their season.
Before a soak, check the rig's `*_display_mode`: a board in `switch` mode tells
you nothing about the scroll path.

### Order

One family per release, in this order. Variant counts are from the report
above (per method: distinct bodies across the plugins that carry it, counted
per class role). Stage 4 needs no reconciliation and can ride along with any
release.

| # | Family | Methods (variants) | Why here |
|---|---|---|---|
| 4 | Identical sweep | `manager.py`: `_dispatch_switch_refresh`, `_favorite_team_is_live`, `get_vegas_priority_weight`, `_game_involves`, `_favorite_scan_targets`, `_favorite_scan_games`, `_get_total_games_for_manager` (all nine, 1); the live-scroll helpers `_preserving_scroll_position`, `_refresh_live_scroll_managers`, `_live_scroll_managers`, `_note_live_scroll_built`, `_live_scroll_needs_rebuild`, `_live_scroll_fields` (eight, 1). `sports.py`: `_card_option`, `_filtered_or_all`, `_effective_live_duration`, `_recent_date_text` (eight, 1). 58 identical families in all | Nothing to decide; brings `manager.py` into core as a `SportsPluginHostMixin`. `_resolve_font_path` (identical in nine `sports.py` and eight renderers) becomes `sports_font_path.resolve_font_path`, not `font_layout.resolve_asset_path`, which skips the cwd. Done: core 3.8.0, adopted (ledmatrix-plugins #594); see [Stage 4](#stage-4-the-identical-sweep-done-core-380-adopted) |
| 5 | Game-over check | `SportsLive._is_game_really_over` (5) | Pure logic, no pixels; one seam, `FINAL_PERIOD`. The pilot for the procedure. Reconciled to one body and promoted as `sports_game_over`; shipped in 3.8.1 and adopted. See [Family 5](#family-5-the-game-over-check-done-core-381-adopted) |
| 6 | Favourite matching | `_is_favorite_game` (7 across three classes), `_select_games_for_display` (2: nrl), `_select_recent_games_for_display` (3) | Everything that asks "is this a favourite" goes through the 3.5.0 `_favorite_key` seam. Reconciled to one body each and promoted as `sports_favorites`; shipped in 3.8.2 and adopted. See [Family 6](#family-6-favourite-matching-done-core-382-adopted) |
| 7 | Other-games rotation | `_by_importance`, `_other_games_window`, `_advance_other_games_if_due` (2 each: football), `_rotate_other_games_on_display` (2: ufc), with `_attach_odds_to_rotated_games` (3; ufc had none) | One outlier each; football carried two fixes the other eight lacked. Reconciled to one body each, on a `_rankings_loaded` seam, and promoted as `sports_rotation`; adoption waits for the release that ships it. See [Family 7](#family-7-the-other-games-rotation-core-done-adoption-waits-for-a-release) |
| 8 | Rankings | `_fetch_team_rankings` (3), `_choose_poll` (3), `_load_division_team_ids`, `_passes_other_filters`, `_best_rank`, `_is_ranked_game` (2 each: football) | Needs 7; the rank badge and the "ranked only" filter read it |
| 9 | Live fetch and odds | `_fetch_todays_games` (5), `_fetch_odds` (3), `_attach_odds_to_rotated_games` (3) | The prerequisite for one shared ESPN poller across plugins |
| 10 | View model | `_extract_game_details_common` (9 of 9) | Every renderer reads it; its keys are additive-only, so reconcile to the superset and leave sport extras in `_extract_game_details` |
| 11 | Switch scorebug | `_load_fonts` (4), `_load_custom_font_from_element_config` (6), `_get_layout_offset` (5), `_fit_score_font` (2: football), `_load_and_resize_logo` (9), `_draw_dynamic_odds` (9), then `_draw_scorebug_layout` (20: Upcoming 9, Recent 8, Live 2, ufc's Core 1) | Needs 10 and the twin decisions below. Several releases: fonts and offsets, logos, odds, then one mode's layout per release |
| 12 | Scroll/Vegas card | `game_renderer.py`: `render_game_card` (6), `_load_and_resize_logo` (8), `_load_custom_font`, `preload_logos`, `__init__` (7 each), `_draw_records_or_rankings` (6), `_load_fonts`, `_draw_dynamic_odds`, `_draw_live_game_status`, `_draw_recent_game_status`, `_get_team_display_text` (5 each), `_draw_text_with_outline` (2: football) | Same decisions as 11; sequence after the scroll-performance work on pre-rendered strips lands |
| 13 | Mode lifecycle | `sports.py`: `update` (23), `__init__` (16), `display` (13), `_advance_live_game_if_due` (6) | Where per-sport behaviour lives; last in `sports.py`. Each difference becomes a seam or a strategy chosen by name (live rotation already has three) |
| 14 | `manager.py` host | Nine different bodies in nine plugins: `__init__`, `display`, `update`, `has_live_content`, `get_live_modes`, `get_vegas_content`, `on_config_change`, `_initialize_managers`, `_get_available_modes`, `_get_current_manager`, `_adapt_config_for_manager`. Dynamic duration: `_evaluate_dynamic_cycle_completion` (9), `_record_dynamic_progress` (8), `get_cycle_duration` (8), `get_dynamic_duration_cap` (6), `supports_dynamic_duration`, `is_cycle_complete` (5 each), `reset_cycle_state` (4). Scroll: `_display_scroll_mode` (8), `_ensure_scroll_content_for_vegas` (8), `_collect_games_for_scroll` (7), `_should_use_scroll_mode`, `_has_any_scroll_mode` (6 each) | See below |

**`manager.py`.** Reconciling it body by body would take a release per
method. The plan is a host class in core, `SportsScoreboardPlugin(BasePlugin)`,
that takes the plugin's leagues as data (key, label, ESPN path, Live, Recent
and Upcoming classes: basketball's `manager.py` already describes its leagues
as such a table) and a typed mode key instead of the mode-name string parsing
(`endswith('_live')`, `split('_')`) every copy repeats. Order within it:
stage 4's identical helpers first; then dynamic duration, then live priority (`has_live_content`,
`get_live_modes`, `has_live_priority`), then Vegas content, then mode
resolution, then the lifecycle methods. Pilot the whole host on nrl or afl,
the smallest copies (about 1,850 lines each), with a frame soak and a
live-game soak before a second plugin moves. `get_vegas_content` is also being
changed by the scroll-performance work: coordinate before touching it.

**Held:** `data_sources.py` (nine copies; soccer's matches core's) and
`dynamic_team_resolver.py` (eight true forks, a different constructor from
core's). Effort on data fetching is better spent on the shared poller that
family 9 prepares.

### Product decisions each family needs

Owner calls to make before (or while) reconciling. Items marked *verify* are
suspected behaviour that needs a payload or a rig to confirm first.

- **5, game-over check. Decided 2026-10-05, done:** one seam,
  `FINAL_PERIOD`: hockey 3; basketball, football and lacrosse 4; `None` (the
  clock never ends a game) for afl, nrl and soccer (clocks that count up),
  baseball (its games carry no `period`, so the old rule was dormant) and
  ufc (a bout ends only on ESPN's final status, which also closes the ~1 s
  window at the horn when the ticking clock reads `0:00`; ESPN's round-break
  displayClock `-` was never a zero clock, ledmatrix-plugins#580). Only a
  non-empty clock string counts (the baseball/ufc copy read a missing clock
  as `0:00`). A score level at 0:00 is not over: the game stays live through
  the break before overtime, and one that really ends tied ends on its final
  status. Baseball keeps its postponed/suspended override in `BaseballLive`.
- **6, favourite matching. Decided 2026-10-05, done:** each side of a game is
  named by `_favorite_key` (the abbreviation; NRL overrides it with the ESPN
  team id, and `None` for a missing id, which fixes a favourite typed "None"
  matching every game without one) and compared with `favorite_teams`
  stripped and upper-cased, so " bos" matches BOS. NRL's ambiguous "NEW"
  still matches nothing and is logged; routing the result-colour helpers
  (`side_is_favorite`, which tint both NEW clubs) through `_favorite_key` is
  left for a later family. The recent-games selection logs at INFO in all
  nine. ufc stays on the shared body, dormant: its favourites are fighters,
  which its MMA managers match themselves (a follow-up). Fix ported: only a
  game with an id can be a duplicate in the selection methods.
- **7, other-games rotation. Decided 2026-10-09, done:** two fixes ported
  from football. The window advances under `_games_lock`: `update()` and
  `display()` both advance it, and interleaved, each added a width and a
  window of games was never shown. The display path's due-check looks at the
  pool `_compose_selection` actually cuts from, including the unfiltered
  fallback when nothing else survived. The other eight never rotated that
  fallback between fetches (it moved only when `update()` ran, then several
  windows at once); guessing it whenever the filtered pool was empty would
  recompose an identical list on every frame while a favourite played.
  Rotated-in fights in ufc follow its `show_odds` like every other fight (no
  separate toggle; the rotation is dormant in ufc today, so no board
  changes). `_rankings_loaded` is a seam (default: the abbreviation table is
  non-empty; football counts its by-id table too). Kept for family 13: the
  no-favourites branch of `update()` keeps its fixed "next N" in the eight
  plugins that have it, rather than football's rotating pools.
- **8, rankings.** (a) afl, basketball, nrl and soccer turn a *standings*
  payload into ranks (a pro league's standings position becomes the rank
  badge); baseball, hockey, lacrosse, ufc and football do not. Which is
  right is visible on every pro-league card with "show ranking" on.
  (b) football also keys ranks by team id, so two schools sharing an
  abbreviation across divisions cannot be confused: adopt for all.
  (c) football asks for the division roster of the *season* year (July
  onward is this year's season), which is right for football and wrong for
  college basketball, hockey and lacrosse, whose ESPN season is the year it
  ends: a per-sport seam, not football's constant. (d) baseball's
  `_choose_poll` calls `dynamic_team_resolver.choose_top_division_poll`, the
  others inline it: one home, in core.
- **9, live fetch and odds.** (a) afl, basketball, nrl and soccer cache the
  live scoreboard for 30 s under `<sport_key>_scoreboard_current`; the others
  do not (the harness fixtures seed that key, so the change shows up there).
  (b) basketball fetches college games with no `dates` parameter, citing a
  404 (*verify* now that `espn_dates` handles ranges). (c) odds are fetched
  three ways: blocking (hockey, lacrosse), a thread waited on for 1.5–2 s
  (six plugins), or fire-and-forget for upcoming games (basketball). This
  sets how long `update()` takes and when an odds line appears. (d) nrl
  guards on a missing odds manager; port it.
- **10, view model.** Per key, whether every sport emits it. Additive only:
  no key is renamed or removed.
- **11 and 12, the scorebug and the card.** The pinned divergences in
  `test/test_sports_twins.py`, where switch mode and scroll mode draw the
  same game differently:
  - weekday timezone: the card reads only `config["timezone"]` and falls back
    to UTC, so a board with only the global zone labels an evening kickoff
    with the next day. **Decided 2026-09-24: use the plugin's timezone
    (fix); not yet implemented;**
  - an out-of-range start time: the scorebug drops the weekday, the card
    raises;
  - favourite result on a nested payload, which score wins when flat and
    nested disagree, and where the favourites come from (the manager's list
    vs the game's stamped list plus config);
  - the element vocabulary (`team_text` vs `team_name`; rank and odds in one
    map, not the other), and its consequences: a `team_name` colour reaching
    one team face and not the other, and the odds face shared with the score
    face in scroll mode only;
  - per-mode colour overrides, which apply in switch mode only;
  - by design, kept unless the owner says otherwise: the date format
    (`switch_date_format` "numeric" vs the card's "abbrev") and the upcoming
    centre (`switch_upcoming_center` "date_time" vs "vs"), both with an
    "inherit" opt-in; and the two schema-font caches (per class vs per path).

  Also: football's `_fit_score_font` swaps to the narrow score face at any
  panel height when the score overflows, where the other seven keep the
  design face at or below the design height (a 64x32 board shows the
  difference); and whether switch mode and the card become one renderer drawn
  at two sizes.
- **13, mode lifecycle.** The live-rotation dialect per sport (incremental
  SWRR in afl, nrl and soccer; a precomputed schedule elsewhere); which sports
  arm celebrations and on what (stays in each plugin, as in stage 3).
- **14, `manager.py`.** Dynamic-duration semantics (what completes a cycle,
  the floor and cap per mode), what counts as live content for live priority
  (favourites only or any live game), and the order of Vegas content.

### Measuring progress

`scripts/sports_drift_report.py` prints the numbers above for any
ledmatrix-plugins checkout (`--plugins <path>` or `LEDMATRIX_PLUGINS`). CI runs
it on every push and PR against the monorepo's main (the "Sports drift report"
job in `.github/workflows/test.yml`): report only, never failing, with the
tables in the job summary and the full JSON as an artifact. The monorepo's
`scripts/check_sports_drift.py` is the gate: it fails when a function that
agrees across the plugins starts to differ. A stage is done when its family
shows one variant per class here and its copies are gone.

```
python scripts/sports_drift_report.py --plugins ../ledmatrix-plugins
python scripts/sports_drift_report.py --family sports.py::_is_game_really_over --diff
```

## Phases B0–B6 (history)

The first project: it moved the scroll orchestration into core and proved the
upgrade path (floors, the store's compatibility gate, the sunset). All seven
phases are done. They are kept because the reasoning in B4–B6 is what every
later stage relies on; the plan from here is [Roadmap](#roadmap).

B0–B3 shipped in core 3.2.0. The rollout after them split into three phases
with very different risk profiles, because one of them cannot break a user on
an old core and the other can.

| Phase | Scope | Status | Gate |
|---|---|---|---|
| **B0** | Characterization tests, CI unit job, `element_style`, font cwd fix, CHANGELOG discipline | ✅ | — |
| **B1** | Promote the nine universal methods; convert `sports.py` → package | ✅ | Characterization suite green; no behavior change intended |
| **B2** | `CelebrationMixin` + rotation strategies as opt-in capabilities | ✅ | Non-adopters have zero new code in their MRO; strategies checked against verbatim plugin transcriptions |
| **B3** | Upstream the scroll **orchestration** layer as `src/common/sports_scroll.py`, reading `global_config['target_fps']` natively | ✅ | Content building stays per-sport |
| **B4** | Ship 3.2.0 *and* make version reporting trustworthy | ✅ | Released 2026-08-03; tag, release and `src.__version__` agree; compatibility gate merged (#428, #431, #433) |
| **B5** | Adoption — guarded core imports, all eight. **Bundled copies stay.** | ✅ | All eight adopted; harness byte-identical; see the B5 retrospective below — four shipped broken and were repaired in plugins #251 |
| **B6** | Sunset — delete the bundled copies | ✅ | Ran 2026-09-01, all eight. Floors at 3.2.0; the store refuses on all three routes in (#431/#433, #508, #510). See "B6 — what actually happened" |

### B4 — what "ship 3.2.0" actually requires

Cutting the tag is the small part. The version *number* has to become something
a floor can be trusted against, and today it is not:

- **The tag and `src.__version__` have never agreed.** `v3.1.0` was tagged
  2026-05-31; `__version__` only became `"3.1.0"` on 2026-07-12 (`7f7f0d64`).
  The v3.1.0 release therefore reports `__version__ = "1.0.0"`.
- **Which silences the compatibility warning entirely for that population.**
  `PluginLoader._warn_if_incompatible` skips the check when the parsed core
  version is below `(2, 0, 0)` — an anti-spam guard that, given the above,
  matches exactly the users most likely to be behind.
- **Nothing enforces a floor anyway.** The check is advisory (it logs and
  continues), and neither `StoreManager.install_plugin` nor
  `StoreManager.update_plugin` compares the core version at all — `update_plugin`
  compares the plugin's manifest version against the registry's
  `latest_version` and nothing else.

  *Fixed, in two parts.* `install_plugin` gained the gate in #431/#433, which
  covers every registry-managed install and, through `_reinstall_with_rollback`,
  the update path that re-downloads.
  `update_plugin`'s git branch pulls in place and re-downloads nothing, so it
  stayed ungated until `_gate_pulled_commit` closed it — checked after the pull
  (the registry then carried no floor field, so the incoming floor was
  unknowable before it) and undone with `git reset --hard` to the pre-pull
  commit. The registry now publishes `ledmatrix_min_version`, and install and
  update refuse on it before downloading or pulling; both post-download gates
  remain as the fallback for older registries, other branches and
  `compatible_versions`. That
  route is rare in practice, since monorepo plugins install as archives; it was
  closed because the sunset rule in the plugins repo's
  `08-shared-sports-code.md` states as **condition 3** that the core enforces
  the floor "at install/update time", and B6 rests on that being true rather
  than merely written down. `install_from_url` — sideloading a plugin from a
  URL — is still ungated.

So B4 is: tag and release 3.2.0; make the tag, the release, and `__version__`
agree, and keep them agreeing; reconsider the `< 2.0.0` skip; migrate manifests
from `ledmatrix_min` to `ledmatrix_min_version`; and add the install/update
compatibility gate that B6 depends on.

#### Two fields express compatibility, and the gate only reads one

`compatible_versions` is the canonical contract: `schema/manifest_schema.json`
**requires** it, all 42 published manifests carry it, and it holds semver
*ranges* — `[">=2.0.0"]` in 41 of them, `[">=1.0.0"]` in `7-segment-clock`.
`ledmatrix_min_version` is the optional per-release floor inside `versions[]`.

The gate as merged reads only the floor. Today that is harmless: no manifest
uses an upper bound, and the two fields agree everywhere except
`7-segment-clock` (`>=1.0.0` against a `2.0.0` floor). But the fields *can*
disagree, and the range syntax the schema already permits includes upper bounds
— a plugin declaring `["2.0.0 - 2.9.9"]` means "not compatible with 3.x" and
the gate would install it on 3.2.0 regardless.

**Before B6, the gate must evaluate `compatible_versions` as well**, and the
manifest migration must reconcile the two fields rather than only renaming the
floor. Deciding which wins when they disagree is part of that work; the safe
default is the more restrictive.

(The schema also deprecates a top-level `ledmatrix_version` in favour of
`compatible_versions`. No manifest still carries it, so there is nothing to
migrate there.)

### B5 — the *fallback* is safe by construction; the modern path is not

The heading matters, because the unqualified version of this claim is false and
this document proves it two sections down: four of the eight adopted plugins
shipped with scroll mode broken on a 3.2.0 core. What is safe by construction is
narrower than "adoption".

A plugin adopting core imports keeps its bundled copy and reaches it through the
guarded import (see the Upgradability table above). On a core that doesn't ship
the module the plugin falls back and behaves exactly as it does today. That
fallback compatibility — and only that — is safe by construction. On a core that
*does* ship the module, correctness is not automatic — object-level and scroll-mode validation
(building both classes and comparing, per the retrospective below) is required
to prove full behavior. There is no version of this step that breaks a user *on
an old core*, which is why it does not wait for B6's gate.

The hockey scroll-display pilot is **already validated**: adopted against a core
carrying 3.2.0, `scroll_display.py` went from 691 to 289 lines and all 16 harness
renders (8 sizes × 2 screens) came out byte-for-byte identical to the
pre-adoption run. That byte-comparison is the acceptance gate for every
adoption. The recipe and its two gotchas are in the plugins repo's
`docs/plugin-development/08-shared-sports-code.md`.

### B6 — why the sunset needs more than a version floor

Deleting a bundled copy removes the fallback, so the guarded import becomes a
hard dependency. On a core without the module the plugin raises
`ModuleNotFoundError` at load; `PluginManager.load_plugin` catches it, records
`PluginState.ERROR`, logs one line, and continues. Nothing crashes — the user
simply loses that scoreboard, with no visible explanation.

Verified against a `v3.1.0` worktree: `src/common/sports_scroll.py`,
`src/element_style.py` and the `src/base_classes/sports/` package are all absent
there, and the import fails with `exc.name == 'src.common.sports_scroll'`. Guard
sets must name that exact dotted path — `{"src"}` alone does not match it.

Combined with the B4 findings, a plugin that deletes its copy today reaches an
un-updated user through a normal store update, fails to load, and warns nobody.
**B6 therefore waits for B4's compatibility gate to have shipped and to have
been in users' hands long enough that the population running a core without it
is small.** The bundled copies cost disk space; deleting them early costs
scoreboards, silently. That trade is not close.

Before the first sunset, add a **compatibility regression test**. **Built:**
core `test/test_sports_sunset_matrix.py` (#505). It has to
cover four cases, not one — B5's safety claim and B6's failure mode are
different propositions and only the second is obvious:

| | bundled copy present | bundled copy removed |
|---|---|---|
| **pinned old core** | **loads** — this is B5's whole guarantee, that the guarded import falls back | `PluginState.ERROR`, and the recorded error names the exact missing module |
| **current core** | loads, using core code | loads, using core code |

The top-left cell is the one worth writing first: nothing in the suite currently
proves that an adopted plugin still works on a core that predates the module,
which is the entire basis for saying B5 is safe to run ahead of the gate.

Assert the old-core/removed-copy case as `PluginState.ERROR` **plus the missing
module path**, not as an uncaught exception. `PluginManager.load_plugin` catches
`ModuleNotFoundError`, so nothing propagates — a test expecting a raise would
pass for the wrong reason on a core where the module is merely broken rather
than absent. "Fails loudly" is aspirational, not what the code does today: it
fails into `ERROR` state with one log line, which is precisely why B6 needs the
gate rather than trusting the failure to be noticed.

The same suite should exercise the install/update gate, since it is the other
half of the guarantee.

### B6 — what actually happened

**Ran 2026-09-01, across all eight scoreboards.** Held from 2026-08-05 to
2026-09-01 on the argument below, which is kept because the reasoning applies to
the next module, not because it is still in force.

**The hold, and why it lifted.** The stated gate was evidence of 3.2.0 uptake —
"a few months of it being the default download, or store-side install data".
That evidence never arrived and could not: the core updates by
`git pull --rebase`, so release-asset counts cannot measure uptake, and no
store-side telemetry exists. What changed instead is that the *risk* the gate
protected against was closed directly. The store now refuses a plugin whose
floor exceeds the running core on **all three** routes in:

| route | gated by |
|---|---|
| `install_plugin` — every path that re-downloads, `_reinstall_with_rollback` included | #431, #433 |
| `update_plugin`'s git branch — pulls in place, re-downloads nothing | #508 |
| `install_from_url` — sideloading | #510 |

With all three closed a pre-3.2.0 user cannot receive a sunset plugin at all;
they keep the version they already run. The population the hold existed to
protect is protected by refusal rather than by a bundled copy — which is what
the copy was standing in for.

**What shipped.** Eight plugins, ~5,800 lines of frozen fallback deleted. Each:
copy removed, guarded import collapsed to a plain one, floor raised to 3.2.0,
`test_core_fallback.py` rewritten as `test_core_scroll.py` asserting the sunset
rather than the fallback. `scripts/check_scroll_adoption.py` gained
`sunset_violations` and a `SUNSET_PLUGINS` set naming all eight, so a
resurrected copy or a returned guard fails CI.

Delivered as plugins #346 (hockey, later folded into #351), #349 (football),
#350 (baseball), #351 (the remaining six).

**Two things found by doing it, both worth carrying forward:**

- **Only one fallback held orchestration logic the core lacked.** baseball's
  `_configure_scroll_helper` reinterpreted `scroll_speed` as pixels-per-*frame*
  when `speed × delay` fell outside the 0.1–5.0 window — measured, 10–20×
  faster than configured for a speed between 1.0 and 5.0. Standardised onto the
  core's behaviour (honour the documented px/sec, clamp) rather than preserved.
  Every other difference across the eight was a docstring, an unreachable
  `scroll_helper is None` guard, or an equivalent diagnostic.
- **Two tests had been leaning on the guard without anyone knowing.**
  `soccer/test_live_screens.py` stubbed `src` in a way that shadowed the core,
  so its guarded import fell back and the test had been exercising the *frozen
  copy* rather than the shipping class since B5. Before sunsetting anything else
  that carries a guarded core import, grep for tests that stub `src`.

**The floor-raising traps still apply** to any future sunset: four plugins
declare their floor top-level, where editing `versions[0]` is a silent no-op,
and the floor has three live spellings (`min_ledmatrix_version`,
`requires.min_ledmatrix_version`, `versions[].ledmatrix_min_version`, plus
deprecated `ledmatrix_min`). See
`src/plugin_system/compatibility.py:declared_min_version` for the resolution
order any floor-raising tool must reproduce — and note the name is **inverted**
between the top level and `versions[]`.

**The modules held back then** (`data_sources.py`, `game_renderer.py`,
`base_odds_manager.py`) have since gone different ways: the eight team
scoreboards import core's `base_odds_manager` (ufc keeps an MMA fork), the
game renderers inherit core's `SportsCardWrappersMixin` (3.7.0) but keep their
drawing, and `data_sources.py` is still copied. Their status is under
[Roadmap](#roadmap). B5's lesson applies to all of them: build the object and
diff rendered output rather than trust a static check.

### B5 retrospective — what the adoption actually cost

Recorded because it is the evidence behind the two decisions above, and because
"the adoption went fine" is not what happened.

**Four of the eight shipped with scroll mode broken** on a 3.2.0 core, and were
repaired in plugins-repo #251. The restructure lifted the content methods
verbatim but left the state they read off `self` behind: separator-icon
constants (hockey, basketball, lacrosse) and the game-renderer cache (afl).
hockey/basketball/lacrosse could not construct the scroll display at all; afl
raised inside `prepare_scroll_content`, which the core base *catches*, so its
only symptom was scroll mode silently drawing nothing.

Three things are worth carrying forward:

- **The bundled fallback did not protect anyone from this.** The break was on
  the modern path, which the fallback never touches. Carrying the second copy
  bought nothing against the actual defect while creating the divergence that
  produced it. That is an argument *for* B6, not against it.
- **Every gate was green.** The safety harness renders the scoreboard screens,
  not scroll mode; `test_core_fallback.py` checked that methods existed and that
  their *globals* resolved, and `self.NHL_SEPARATOR_ICON` is an attribute read,
  invisible to an AST scan for `Name` loads. The fix was to stop reasoning about
  source and **build the object**: construct both classes on both paths, compare
  the separator icons they end up with, and assert the adopted class ends up
  with every instance attribute the bundled one sets.
- **Test what the change touches, not what is convenient to render.** Scroll
  mode had no coverage because the harness could not reach it. A comparison
  harness that renders the same games through both paths and diffs the pixels
  needs no per-sport knowledge of the right answer, only that adopting core code
  did not change it.

**The ledger.** Before adoption, eight duplicated copies totalled 5,685 lines.
After adoption plus the frozen legacy copies it was 10,610; removing the dead
inline duplication (plugins #252) brought it to roughly 8,620. B6 would take it
to about 3,300 including the shared core module — some 2,400 fewer than before
this project started. **Until B6 runs, the adoption is net negative on disk**,
and its one delivered user-visible gain was that adopted plugins honoured the
global `target_fps` instead of hardcoding ~100 FPS (since withdrawn: see the
note under the B3 design above).

### Decision: stop adopting further modules until B6 closes (lifted)

Held from B5 until B6 ran on 2026-09-01: each adoption added a second copy to
keep in step against a payoff that depended on the sunset. Once the store
refused a too-new plugin on every route, adopting and sunsetting in one stage
became safe, and stages 0–3 under [Roadmap](#roadmap) did exactly that.

## How to keep this project healthy

Lessons this migration paid for, worth applying beyond it:

- **A version number is a promise; keep it in one place.** Three different
  answers to "what version am I on" (tag, release, `__version__`) is what made
  the floor untrustworthy. Assert their agreement mechanically.
- **Advisory checks protect nobody.** If a rule matters, enforce it where the
  action happens — the install path, not a log line the user will never read.
  If it doesn't matter enough to enforce, don't write the rule.
- **Prefer failures that are loud and early.** A plugin that dies at load with
  one journal line is indistinguishable, to a user, from a plugin that was never
  installed. Surface plugin health in the UI.
- **Keep the two repos' rules in sync deliberately.** The sunset rule lives in
  both this file and the plugins repo's
  `docs/plugin-development/08-shared-sports-code.md`. When one changes, change
  the other in the same PR — drift between them is how a contributor ends up
  following a rule that was superseded.
- **Measure before and after, on real hardware.** Byte-identical harness renders
  and a device soak caught what unit tests could not. Reserve "it should be
  fine" for things you have actually looked at.

## Rules for contributors

- **Promote on evidence, not intuition.** A method moves to core when every copy
  that has it is identical. Drifted copies are reconciled first, one family
  per release, with each visible difference an owner decision (see
  [Roadmap](#roadmap)); until then they stay in the plugins.
- **Never add a sport name to core.** If core needs to know which sport it is,
  the design is wrong — add an override point instead.
- **A capability that is not opted into must not execute.** If you find yourself
  writing `if self.<capability>_enabled` inside a base class, it belongs in a
  mixin.
- **Touch the view-model keys only additively.** The shared `src/common`
  renderers read them.
- **Every promotion lands with the characterization suite green**, and every
  pilot adoption lands with that plugin's harness and golden suites green.
