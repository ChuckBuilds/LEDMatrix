# Skin System Architecture

Skins are user-installable **visual overlays** for the sports scoreboards.
A skin replaces only the *look* of a scoreboard — the host plugin keeps doing
data fetching, scheduling, caching, dedup, live-priority takeover, and vegas
mode. If you only want to **build** a skin, read
[CREATING_SKINS.md](CREATING_SKINS.md); this document explains how the system
works and why it is shaped this way.

## Why skins instead of forks

Before skins, changing a scoreboard's layout meant forking the whole plugin
(e.g. the community MLB scoreboard fork). The fork gets the new look but loses
everything the maintained plugin keeps earning: duration/scheduling behavior,
vegas mode support, caching and background-fetch improvements, bug fixes. It
also silently drifts: every upstream improvement now has to be re-ported by
hand.

A skin inverts that trade. The plugin remains stock and keeps updating through
the store; the skin is ~100 lines of pure rendering code that receives the
plugin's already-fetched data each frame. Uninstalling the skin (or the skin
crashing) simply restores the built-in look.

```text
              (unchanged)                        (the skin seam)
 ESPN API ──► update() ──► game view model ──► _render_game() ──► display
              fetching        (a dict)          │         │
              caching                           │         └─ built-in
              scheduling                        └─ skin.render_<mode>(ctx, game)
              live priority                        draws onto ctx.canvas
```

## The render funnel

Every sports scoreboard (baseball, football, basketball, hockey — anything
built on `src/base_classes/sports.py`) renders through exactly one seam:
`SportsCore._render_game(game, force_clear)`.

1. The mode class's `display()` (live, `SportsUpcoming`, `SportsRecent`)
   picks `self.current_game` and calls `_render_game`.
2. `_render_game` lazily loads the configured skin (once, on first render —
   a broken skin can never block plugin startup).
3. If a skin is active, the host builds a `SkinContext` — a fresh black
   canvas at the current display size plus layout/font/logo helpers — and
   calls the skin's `render_live` / `render_recent` / `render_upcoming`
   with a **copy** of the game dict.
4. If the skin returns `True`, the canvas is composited onto the display.
   If it returns `False`, isn't implemented for that mode, or raises, the
   built-in `_draw_scorebug_layout` runs instead.

Key properties that fall out of this design:

- **Per-mode fallback.** A skin that only implements `render_live` gets the
  stock recent/upcoming screens for free.
- **Three strikes.** A skin that raises 3 times in a row is disabled for the
  rest of the session (one loud error log per failure); the display never
  goes dark. Restarting the service re-arms it.
- **Copies, not references.** Skins receive a shallow copy of the game dict,
  so a buggy skin cannot corrupt the plugin's scheduling state.
- **Vegas mode works untouched.** Vegas capture falls back to grabbing the
  regular `display()` output, which is already skin-rendered. Skins can
  additionally implement `render_vegas_card` for purpose-built scroll cards,
  and hosts can call `SportsCore.render_skin_card(game, size)` to use it.
- **Hot-loop caution.** `render_live` runs every display-loop pass during a
  live game. The host logs a warning when a skin render exceeds 150 ms, and
  `scripts/validate_skin.py` enforces a budget at development time — but
  Python cannot forcibly time-out a stuck render, so a skin that blocks
  (network I/O, giant image ops) stalls the display. This is why the rules
  in CREATING_SKINS.md ban I/O in render paths.

## The view model contract

The `game` dict a skin receives is the plugin's already-extracted view model
(`SportsCore._extract_game_details_common` plus per-sport extras from
`src/base_classes/{baseball,basketball,football,hockey}.py`).

- **Guaranteed keys (view model v1.0)** — always present for every sport:
  `id`, `game_time`, `game_date`, `start_time_utc` (a UTC `datetime`),
  `status_text`, `is_live`, `is_final`, `is_upcoming`, `is_halftime`,
  `home_abbr`/`away_abbr`, `home_id`/`away_id`, `home_score`/`away_score`
  (**strings**), `home_logo_path`/`away_logo_path`, `home_record`/`away_record`.
- **Sport extras** — documented per sport in CREATING_SKINS.md (e.g. baseball
  adds `inning`, `inning_half`, `balls`, `strikes`, `outs`, `bases_occupied`).
- **Optional keys** (`odds`, rankings, `series_summary`, …) are present only
  when the feature is enabled — skins must always use `.get()`.

Versioning policy: additive changes bump the minor version
(`VIEW_MODEL_VERSION` in `src/skin_system/skin_base.py`, surfaced to skins as
`ctx.view_model_version`); renaming or removing a guaranteed key requires a
major bump plus a compat shim. `test/test_skin_system.py::TestViewModelContract`
fails CI if a guaranteed key disappears from the extractor.

Separately, `SKIN_API_VERSION` versions the Python API (`ScoreboardSkin`,
`SkinContext`). The loader refuses a skin whose manifest declares a different
major version and falls back to the built-in renderer with a clear
"skin needs an update" log line.

## Package layout and lifecycle

```text
skins/<skin-id>/
  skin.json      # manifest (required)
  skin.py        # ScoreboardSkin subclass (required)
  preview.png    # optional, shown by the web UI
  assets/        # optional skin-local images
  helpers.py ... # optional extra modules (namespaced per skin at import)
```

Skins live in the central `skins/` directory — deliberately **not** inside the
plugin's directory, because plugin reinstall/update deletes the whole plugin
directory and a skin must survive that. One skin can also target several
plugins (mlb + milb).

Lifecycle: discovered lazily on first render → manifest validated → API major
version gated → module imported under a namespaced `sys.modules` key (two
skins can both ship a `helpers.py`, same scheme plugins use) → instantiated
with `(manifest, options)`. Every failure logs and falls back to built-in.

Skins should be **stateless**: the live, recent, and upcoming mode classes
each hold their own skin instance, so derive everything from `(ctx, game)`.

## Selection and configuration

Inside the plugin's own config section in `config/config.json`:

```json
"baseball-scoreboard": {
  "skin": "retro-baseball",
  "skin_options": { "accent_color": [255, 80, 0] }
}
```

`"skin"` is either one id for all modes or a per-mode mapping
(`{"live": "retro-baseball", "recent": "built-in"}`). Absent, empty, or
`"built-in"` means the stock renderer. Because this rides the plugin's config
section, it persists across plugin reinstalls like every other setting.

The web UI shows a **Visual Skin** dropdown for plugins that have matching
skins installed: `SchemaManager.inject_skin_selector` adds an enum to the
*served* schema only. Validation never sees the enum — so a config that
references an uninstalled skin stays valid (rendering just falls back), and
the currently-configured value is always kept selectable. `GET /api/v3/skins`
lists installed skins (optionally filtered by `?plugin_id=`).

## Distribution

- **Manual:** `git clone <skin repo> skins/<skin-id>` — that's the whole
  install. No manifest bumps, no `update_registry.py`; skins are not monorepo
  plugins.
- **Store:** registry entries with `"type": "skin"` install through the same
  `plugins.json` pipeline; `PluginStoreManager` routes them to `skins/`,
  validates `skin.json` (including the API major version) instead of
  `manifest.json`, and never installs dependencies — skins are render-only
  (stdlib + PIL + the provided context, no third-party packages in v1).

## Trust model

A skin is Python executing inside the display service — **exactly the same
trust level as a plugin**, even though "skin" sounds cosmetic. Only install
skins from sources you'd be willing to install a plugin from.

## v2 directions (not in v1)

- A generic `BasePlugin` opt-in (`render_with_skin()`) so non-sports plugins
  (weather, music) can offer skinnable layouts; `skin_runtime` is already
  sports-agnostic in anticipation.
- Store UI: preview gallery, one-click install from the skin browser.
- An update path for git-cloned skins (today: re-clone or store reinstall).
- Animation support in skins (today the API is one frame per render call;
  stateful tricks work but are at-your-own-risk).
