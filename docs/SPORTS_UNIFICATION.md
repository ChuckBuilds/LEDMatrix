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
| A plugin can drop its bundled copy safely | The **sunset rule**: its manifest must floor `ledmatrix_min_version` at the first core release shipping the module (recorded in `CHANGELOG.md`) — *necessary but not sufficient*. Nothing enforces that floor today, so the copy also waits for the B6 gate below. |

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
and a typo should cost the boost, not the scoreboard. When a plugin needs an
ordering that core does not ship, it calls `register_rotation_strategy` to add
its own — rather than core growing a branch for it.

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

B0–B3 are merged and shipping in core 3.2.0. Everything that remains is
**rollout**, and it splits into three phases with very different risk profiles.
The original plan folded the last two together; they are separated here because
one of them cannot break a user on an old core and the other can.

| Phase | Scope | Status | Gate |
|---|---|---|---|
| **B0** | Characterization tests, CI unit job, `element_style`, font cwd fix, CHANGELOG discipline | ✅ | — |
| **B1** | Promote the nine universal methods; convert `sports.py` → package | ✅ | Characterization suite green; no behavior change intended |
| **B2** | `CelebrationMixin` + rotation strategies as opt-in capabilities | ✅ | Non-adopters have zero new code in their MRO; strategies checked against verbatim plugin transcriptions |
| **B3** | Upstream the scroll **orchestration** layer as `src/common/sports_scroll.py`, reading `global_config['target_fps']` natively | ✅ | Content building stays per-sport |
| **B4** | Ship 3.2.0 *and* make version reporting trustworthy | ✅ | Released 2026-08-03; tag, release and `src.__version__` agree; compatibility gate merged (#428, #431, #433) |
| **B5** | Adoption — guarded core imports, all eight. **Bundled copies stay.** | ✅ | All eight adopted; harness byte-identical; see the B5 retrospective below — four shipped broken and were repaired in plugins #251 |
| **B6** | Sunset — delete the bundled copies | **blocked, deliberately** | 3.2.0 *in users' hands*. Released 2026-08-03; still no adoption data at the 2026-08-26 re-check. The prerequisite regression test is built (#505); only the evidence is missing. See "B6 — the decision as of 2026-08-05" |

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

### B6 — the decision as of 2026-08-05

**Do not run B6 yet. Do not abandon it either.** The blocker is no longer
technical; it is calendar time, and it is the one thing here that cannot be
worked around by writing more code.

**Why not yet.** 3.2.0 was published **2026-08-03**. Its predecessor 3.1.0 ran
for nine months. B6's entire safety argument is "cores without
`src.common.sports_scroll` are gone", and two days after release that is not
close to true. There are no release assets to count and no install telemetry, so
we cannot demonstrate otherwise — and that absence of evidence *is* the answer.
Executing B6 now would strand essentially the whole user base on their current
plugin versions.

**What is already done and waiting.** The hard part is built and tested. The
install gate refuses a plugin whose floor exceeds the core's version, and
refuses one whose floor is above 2.0.0 when the core reports an untrustworthy
version — so a v3.1.0-release user (who reports `1.0.0`) keeps a working plugin
instead of receiving one that cannot load. Every adopted plugin has a
`test_core_fallback.py` covering both paths.

**What would unblock it.** Evidence of 3.2.0 uptake — a few months of it being
the default download, or store-side install data if that is ever added. Revisit
then, not on a schedule.

**Re-checked 2026-08-26 — still held.** v3.2.0 remains the latest release and
`src.__version__` is still `3.2.0`; no 3.3.0 has been cut. That is 23 days, not
the few months above, and no store-side install data has appeared. Note also
that the core updates by `git pull --rebase` rather than by downloading a
release, so release-asset counts would not measure uptake even if we had them —
whatever eventually unblocks this has to come from the store side. The
prerequisite test is now built (#505), so when the evidence does arrive the
remaining work is the sunset itself.

**When it happens, remember:** four plugins declare their floor top-level, where
editing `versions[0]` is a silent no-op, and the floor has three live spellings
(`min_ledmatrix_version`, `requires.min_ledmatrix_version`,
`versions[].ledmatrix_min_version`, plus deprecated `ledmatrix_min`). See
`src/plugin_system/compatibility.py:declared_min_version` for the resolution
order any floor-raising tool must reproduce.

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
and its one delivered user-visible gain is that adopted plugins honour the
global `target_fps` instead of hardcoding ~100 FPS.

### Decision: stop adopting further modules until B6 closes

`data_sources.py` (9 copies), `game_renderer.py` (8) and `base_odds_manager.py`
are the obvious next candidates. **Do not adopt them yet.** Each adoption adds
carrying cost — a second copy to keep in step — against a payoff that is
contingent on B6, and B6 is gated on an installed base we cannot currently
measure. Consolidate what is already committed; revisit when B6 does.

## What's next

Steps 1–5 of the original plan are **done**: 3.2.0 is tagged and published with
a version number CI now asserts (#428), the compatibility gate is in
`install_plugin` and reads `compatible_versions` as well as the floor
(#431, #433), the newest manifest entry is required to use `ledmatrix_min_version`
(plugins #244), and all eight plugins have adopted the scroll orchestration
(plugins #245–#249, repaired in #251, tidied in #252).

What actually remains, smallest first:

1. **Nothing on the critical path.** B6 is the only remaining phase and it is
   waiting on calendar time, not on work. Resist the urge to fill the gap by
   adopting more modules — see the decision above.
2. ~~**The stale plugin-test tranche**~~ — **done** (verified 2026-08-26).
   The 5 failures across baseball, hockey and basketball are gone;
   `scripts/run_plugin_tests.py --all` reports 174 passed, 2 skipped, 0 failed
   across the whole fleet. Re-check with that runner rather than by pointing
   pytest at a plugin directory: these are standalone scripts, not a pytest
   suite, and one of them calls `sys.exit(1)` at import, which collapses a
   pytest run into an INTERNALERROR that looks nothing like the real state.
3. **Soak the remaining adoptions on hardware.** Only baseball has been watched
   through a live game, and hockey has been loaded on devpi. The other six are
   proven by harness, unit tests and pixel comparison — not by a live match.
   Out-of-season sports cannot be soaked until their season starts. **This is
   now the only open item that needs work rather than calendar time.**
4. ~~**`CLAUDE.md` says four panel sizes**~~ — **done.** It reads "Default
   matrix sizes are eight … design for the classic four first".
5. **Then, when the evidence supports it, B6.** Its prerequisite — the
   four-case compatibility regression test — is built: core
   `test/test_sports_sunset_matrix.py` (#505). It drives all four cells through
   the real `PluginManager.load_plugin` and asserts the sunset cell as
   `PluginState.ERROR` plus an error naming the missing module, not as a raise.
   Two modelling traps it had to work through, worth knowing before trusting
   any successor: the copy-removed shape must be an *unguarded* import, or the
   failure names the missing `scroll_display_legacy` instead of the core
   module; and only the leaf module may be hidden, since a pre-3.2.0 core still
   ships `src/common/`.

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
  has it and they agree on intent. Otherwise it stays in the plugins.
- **Never add a sport name to core.** If core needs to know which sport it is,
  the design is wrong — add an override point instead.
- **A capability that is not opted into must not execute.** If you find yourself
  writing `if self.<capability>_enabled` inside a base class, it belongs in a
  mixin.
- **Touch the view-model keys only additively.** Published skins depend on them.
- **Every promotion lands with the characterization suite green**, and every
  pilot adoption lands with that plugin's harness and golden suites green.
