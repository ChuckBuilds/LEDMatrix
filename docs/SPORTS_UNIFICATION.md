# Sports Code Unification — Architecture

How the nine sports scoreboard plugins converge onto shared core code **without**
becoming nine clients of a god class.

## The problem

Nine plugins (`afl`, `baseball`, `basketball`, `football`, `hockey`, `lacrosse`,
`nrl`, `soccer`, `ufc`) each ship a ~3,000-line `sports.py` descended from this
repo's `src/base_classes/sports.py`. They have drifted into three lineages, and
only 28 of the 66 methods appearing across them are present in all nine. One
logical fix (the UTC start-time bug) cost 75 files.

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
| Core changes never break a plugin's rendering | The **view-model contract**: `_extract_game_details_common` returns a dict whose `GUARANTEED_KEYS` are frozen by `test/test_skin_system.py::TestViewModelContract`. Keys may be added, never renamed or removed. |
| A plugin can drop its bundled copy safely | The **sunset rule**: only when its manifest floors `ledmatrix_min_version` at the first core release shipping the module (recorded in `CHANGELOG.md`). |

The core API is **additive-only**. A method the plugins call is never removed or
given a new required parameter; new behavior arrives as new methods with
defaults, or as capabilities they opt into.

### Reusability — write once, nine plugins benefit

Only code that is **identical in intent across all nine** moves into the base
class. That set is small and knowable — it is exactly the methods present in every
copy today (phase B1 below). Everything else stays where it is until it earns
promotion.

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

```
src/base_classes/sports/
  __init__.py            re-exports the public API (import path unchanged)
  core.py                SportsCore — fetch, cache, config, logos, fonts, odds,
                         view-model extraction, the skin seam
  modes.py               SportsUpcoming / SportsRecent / SportsLive
  capabilities/
    celebrations.py      CelebrationMixin        (opt-in: 4 of 9 plugins)
    rotation.py          RotationStrategy + registry

src/common/
  sports_scroll.py       SportsScrollDisplay / …Manager — scroll orchestration
                         (content building stays in the plugins)
```

`from src.base_classes.sports import SportsCore` keeps working — the package
`__init__` re-exports, so the conversion is invisible to every existing importer.

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
| `render_skin_card(game, size)` | Skin-system entry point | built-in fallback |
| `score_phrase(points, team_abbr)` | Celebration wording (`"GOOOOAAALLL!"` vs `"TOUCHDOWN!"`). `points` is the score delta, which sports with variable-value scores use to name the play | `"<abbr> SCORES!"` — only consulted when `CelebrationMixin` is present |
| `win_phrase(team_abbr)` | Win-celebration wording | `"<abbr> WINS!"` — mixin only |
| `_favorite_key(game, side)` | Which view-model field identifies a team for favorites matching | `game["<side>_abbr"]` |
| `_config_schema_path()` | Plugin's `config_schema.json` — returning it routes `_get_layout_offset` through the `src.element_style` resolver (and gives it the defaults to compare against) | `None`, i.e. the classic inline `customization.layout` read |
| `_font_root()` | Directory to resolve `assets/fonts` against | core install root |

Two class attributes serve the same purpose for values that are per-sport
constants rather than behavior:

| Attribute | Meaning | Default |
|---|---|---|
| `FINAL_PERIOD` | Period at/after which a zero clock can mean "over" | `4` (hockey overrides to `3`) |
| `CLOCK_COUNTS_DOWN` | Whether `0:00` means "expired" | `True` (soccer/afl/nrl override to `False` — their clocks count up, so `0:00` is kickoff) |
| `COALESCE_SCORING_SEQUENCE` | Fold score increments arriving during an active celebration into that one celebration | `False` (football overrides to `True` — a touchdown lands as +6, then +1 for the extra point) |

### Why these are seams and not branches

`_favorite_key` exists because NRL abbreviations are **not unique** — "NEW" is both
Newcastle Knights and New Zealand Warriors, "CAN" both Canberra and Canterbury —
so NRL matches favorites on team ID. Flattening every plugin to abbreviations
would silently select the wrong club for NRL users. The base declares the seam,
NRL fills it, and core never learns the string `"nrl"`.

`CLOCK_COUNTS_DOWN` exists for the same reason in the opposite direction: a
soccer clock reading `0:00` means the match has not kicked off, so running the
clock-expiry branch there would evict live games.

`COALESCE_SCORING_SEQUENCE` is the third of the same kind. In football one
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
and a typo should cost the boost, not the scoreboard. A plugin needing an
ordering core does not ship calls `register_rotation_strategy` instead of core
growing a branch.

`test_sports_capabilities.py` checks each strategy against a **verbatim
transcription** of the plugin code it replaces, over every live-game shape up to
four games. That differential is what B5 deletes the bundled copies on the
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

## Phases

| Phase | Scope | Risk control |
|---|---|---|
| **B0** ✅ | Characterization tests, CI unit job, `element_style`, font cwd fix, CHANGELOG discipline | — |
| **B1** ✅ | Promote the nine universal methods; convert `sports.py` → package | Characterization suite must stay green; no behavior change intended |
| **B2** ✅ | `CelebrationMixin` + rotation strategies as opt-in capabilities | Plugins that don't opt in have zero new code in their MRO; strategies checked against verbatim plugin transcriptions |
| **B3** ✅ | Upstream the scroll **orchestration** layer as `src/common/sports_scroll.py`, reading `global_config['target_fps']` natively | Plugin copies remain until sunset; content building stays per-sport |
| **B4** | Bump to 3.2.0, record modules in CHANGELOG, migrate `ledmatrix_min` → `ledmatrix_min_version` | Gives plugins a version to floor on |
| **B5** ⏳ | Pilot one plugin per lineage (hockey, soccer, football) on core imports; then the remaining six; then delete bundled copies | Pilot soaks before rollout; harness + golden suites gate each |

**B5 is blocked on this PR merging and 3.2.0 shipping** — a plugin cannot floor
`ledmatrix_min_version` at a release that does not exist, and an unguarded
`src.common.sports_scroll` import would break every user on 3.1.0.

The hockey scroll-display pilot has been **validated ahead of that gate**:
adopted against a core carrying 3.2.0, `scroll_display.py` went from 691 to 289
lines and all 16 harness renders (8 sizes × 2 screens) came out byte-for-byte
identical to the pre-adoption run. The adoption recipe and the two gotchas it
surfaced are written up in the plugins repo's
`docs/plugin-development/08-shared-sports-code.md`.

## Rules for contributors

- **Promote on evidence, not intuition.** A method moves to core when every copy
  has it and they agree on intent. Otherwise it stays in the plugins.
- **Never add a sport name to core.** If core needs to know which sport it is,
  the design is wrong — add an override point instead.
- **A capability that is not opted into must not execute.** If you find yourself
  writing `if self.<capability>_enabled` inside a base class, it belongs in a
  mixin.
- **Touch the view-model keys only additively.** Published skins depend on them.
- **Every promotion lands with the characterization suite green**, and every
  pilot adoption lands with that plugin's harness and golden suites green.
