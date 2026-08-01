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
| `score_phrase(game)` | Celebration wording (`"GOAL"` vs `"TOUCHDOWN"`) | `"SCORE"` — only consulted when `CelebrationMixin` is present |

## Phases

| Phase | Scope | Risk control |
|---|---|---|
| **B0** ✅ | Characterization tests, CI unit job, `element_style`, font cwd fix, CHANGELOG discipline | — |
| **B1** | Promote the nine universal methods; convert `sports.py` → package | Characterization suite must stay green; no behavior change intended |
| **B2** | `CelebrationMixin` + rotation strategies as opt-in capabilities | Plugins that don't opt in have zero new code in their MRO |
| **B3** | Upstream `ScrollDisplay` as `src/common/sports_scroll.py`, reading `global_config['target_fps']` natively | Plugin copies remain until sunset |
| **B4** | Bump to 3.2.0, record modules in CHANGELOG, migrate `ledmatrix_min` → `ledmatrix_min_version` | Gives plugins a version to floor on |
| **B5** | Pilot one plugin per lineage (hockey, soccer, football) on guarded core imports; then the remaining six; then delete bundled copies | Pilot soaks before rollout; harness + golden suites gate each |

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
