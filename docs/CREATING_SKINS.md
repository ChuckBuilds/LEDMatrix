# Creating Skins

A skin restyles a sports scoreboard (live / recent / upcoming) without
forking the plugin: the plugin keeps fetching data, scheduling, caching, and
doing vegas mode; your skin only draws. Architecture background:
[SKIN_SYSTEM.md](SKIN_SYSTEM.md).

## Quick start

```bash
cp -r skins/example-classic-baseball skins/my-skin
# edit skins/my-skin/skin.json  -> set id ("my-skin"), name, author, class_name
# edit skins/my-skin/skin.py    -> rename the class, start restyling
python scripts/validate_skin.py --skin my-skin
```

The validator renders your skin against bundled fixture games at several
panel sizes with **no hardware, no network, no running service**, saves PNGs
(plus 4x previews) to `skin_renders/`, and fails loudly on errors. Iterate:
edit → validate → look at the PNGs.

To see it on your matrix, add to your plugin's section in `config/config.json`:

```json
"baseball-scoreboard": {
  "skin": "my-skin",
  "skin_options": { }
}
```

or pick it from the **Visual Skin** dropdown in the web UI (it appears once a
matching skin is installed). `"skin"` also accepts a per-mode mapping:
`{"live": "my-skin", "recent": "built-in"}`.

## The manifest (`skin.json`)

```json
{
  "id": "my-skin",
  "name": "My Skin",
  "version": "1.0.0",
  "author": "you",
  "description": "What it looks like",
  "skin_api_version": "1.0.0",
  "targets": {
    "sports": ["baseball"],
    "sport_keys": ["mlb", "milb"],
    "plugins": []
  },
  "entry_point": "skin.py",
  "class_name": "MySkin",
  "modes": ["live", "recent", "upcoming"],
  "preview": "preview.png"
}
```

Field notes: `id` must equal the directory name; `skin_api_version`'s major
version must match the host's `SKIN_API_VERSION` or the skin is refused at
load; `targets` takes sport families (`sports`), exact sport keys
(`sport_keys`), and/or exact plugin ids (`plugins`) — any match applies.

## The renderer (`skin.py`)

```python
from src.skin_system.skin_base import ScoreboardSkin, SkinContext

class MySkin(ScoreboardSkin):
    def render_live(self, ctx: SkinContext, game: dict) -> bool:
        score = f"{game.get('away_score', '0')}-{game.get('home_score', '0')}"
        fit = ctx.layout.fit_text(score, ctx.layout.bounds)
        ctx.draw_fit(fit, ctx.layout.bounds)
        return True   # True = "I drew it"; False = use the built-in layout
```

Implement only the modes you care about — anything else falls back to the
plugin's built-in rendering. Return `False` to decline a specific game (e.g.
a layout that only makes sense while a game is live).

### The rules (they keep your skin from breaking the display)

1. **Draw only onto `ctx.canvas`** (via the helpers or `ctx.draw`). Never
   reassign `ctx.canvas`, never touch the display or call any update method.
2. **No I/O in render paths.** No network, no file loads per frame —
   `render_live` runs every display pass, and a slow render stalls the whole
   matrix (the host warns at >150 ms). Use `ctx.load_logo` (cached) and
   `cache_key=` for images.
3. **Derive everything from `(ctx, game)`.** Skins must be stateless: the
   live/recent/upcoming modes each get their own instance.
4. **Always `.get()` optional keys.** Only the guaranteed keys below are
   promised to exist.
5. **Never hardcode pixel positions for the panel.** Use `ctx.width`/
   `ctx.height`, `ctx.layout` regions and `fit_text` — your skin will be run
   at sizes you didn't test (64x32, 128x64, vegas cards).
6. **No third-party dependencies.** Stdlib + PIL + what `ctx` provides.

A skin that raises 3 renders in a row is disabled until the service restarts
(the built-in layout takes over), so a bug is cosmetic — but check your logs.

## SkinContext reference

| Member | What it is |
|---|---|
| `ctx.canvas` / `ctx.draw` | Fresh RGB `PIL.Image` at display size + its `ImageDraw` (raw-PIL escape hatch) |
| `ctx.width`, `ctx.height` | Canvas size — the only size truth |
| `ctx.layout` | `LayoutContext` (see [ADAPTIVE_LAYOUT.md](ADAPTIVE_LAYOUT.md)): `bounds`, `fit_text`, `fit_text_proportional`, `fit_image`, `px`, `by_tier` |
| `ctx.draw_fit(fit, box, color, align, valign)` | Draw a `fit_text` result aligned in a `Region` (handles BDF fonts) |
| `ctx.draw_text(text, x, y, color, font)` | Positioned text (handles BDF fonts) |
| `ctx.draw_image(img, box, mode, align, valign, cache_key)` | Fit + paste an image with alpha; no-ops on `None` |
| `ctx.load_logo("home" \| "away")` | Team logo as RGBA, or `None` (always handle `None`). Cached after first use; see note below |
| `ctx.draw_text_outlined(text, (x, y), font, fill, outline_color)` | The classic scorebug outlined text (TTF fonts only) |
| `ctx.fonts` | The host's font dict — keys `score`, `time`, `team`, `status`, `detail`, `rank` |
| `ctx.options` | Your user's `skin_options` from config |
| `ctx.sport`, `ctx.view_model_version`, `ctx.logger` | Context metadata + logger |

**A note on `ctx.load_logo` vs the no-I/O rule:** `load_logo` is the one
sanctioned exception. It goes through the host's logo cache — after the
first call per team it's a pure in-memory lookup. If a logo file is missing
on disk, the *first* call may download it, exactly like the built-in
renderer does for the same game (a skin is never worse than built-in here).
Always pass a stable `cache_key` when drawing it, never load image files
yourself in a render path, and always handle `None`.

The default layout idiom — carve regions, then fit text into them:

```python
from src.adaptive_layout import scoreboard_regions

regions = scoreboard_regions(ctx.layout.bounds, ctx=ctx.layout)
ctx.draw_image(ctx.load_logo("away"), regions.away_slot, cache_key=f"logo:{game.get('away_abbr')}")
ctx.draw_image(ctx.load_logo("home"), regions.home_slot, cache_key=f"logo:{game.get('home_abbr')}")
fit = ctx.layout.fit_text("3-5", regions.score_area)
ctx.draw_fit(fit, regions.score_area)
```

`Region` supports `split_h`/`split_v`/`inset`/`top_band`/`bottom_band`/
`left_col`/`right_col` for custom carves. Raw `ctx.draw.rectangle/polygon/
ellipse/...` is always available for custom marks (see the bases diamond in
the example skin).

## The game view model

Guaranteed for every sport (view model v1.0 — renaming these breaks skins and
is treated as a breaking change upstream):

| Key | Notes |
|---|---|
| `id` | Event id (string) |
| `status_text` | Display-ready status, e.g. `"Final"`, `"7:30 PM"`, `"Bot 7th"` |
| `is_live`, `is_final`, `is_upcoming`, `is_halftime` | Booleans |
| `game_date`, `game_time` | Pre-formatted local date/time strings |
| `start_time_utc` | UTC `datetime` |
| `home_abbr`, `away_abbr` | Team abbreviations (can be 2–5 chars — fit, don't assume) |
| `home_id`, `away_id` | Team ids |
| `home_score`, `away_score` | **Strings**, not ints |
| `home_record`, `away_record` | `"58-33"` or `""` (0-0 records are blanked) |
| `home_logo_path`, `away_logo_path` | Prefer `ctx.load_logo` over touching these |

Sport extras (present for that sport, still `.get()` defensively):

- **baseball**: `inning` (int), `inning_half` (`"top"`/`"bottom"`), `balls`,
  `strikes`, `outs` (ints), `bases_occupied` (`[first, second, third]`
  booleans), `series_summary` (str)
- **football**: `period`, `period_text`, `clock`, `home_timeouts`,
  `away_timeouts`, `down_distance_text`, `down_distance_text_long`,
  `is_redzone`, `possession`, `possession_indicator` (`"home"`/`"away"`),
  `scoring_event`
- **basketball**: `period`, `period_text`, `clock`
- **hockey**: `period`, `period_text`, `clock`, `power_play`, `penalties`,
  `home_shots`, `away_shots`

Optional everywhere (only when the user enabled the feature): `odds` (dict),
`series_summary`, rankings-related fields.

Fixture copies of these dicts live in `src/skin_system/fixtures/` — that's
exactly what the validator feeds your skin.

## Vegas mode

You get vegas support for free: vegas captures the normal display output,
which is already your skin's rendering. Optionally implement
`render_vegas_card(ctx, game)` to return a purpose-built card at
`ctx.width x ctx.height` (sizes vary — never assume 128x32).

## Building a skin with Claude Code

Skins are ideal Claude Code projects: small, isolated, and verifiable with
one command. Paste this to start:

> You are building a **display skin** for LEDMatrix — a visual overlay for a
> sports scoreboard on a small LED matrix (commonly 128x32 or 64x32 pixels).
> First read `docs/CREATING_SKINS.md` and the reference skin in
> `skins/example-classic-baseball/`.
>
> Rules:
> - Create/modify files ONLY under `skins/<my-skin-id>/`. Do NOT modify
>   anything in `src/`, `scripts/`, the plugins, or any other skin.
> - Render only from the `game` dict and `ctx` helpers. No network calls, no
>   per-frame file I/O, no new pip dependencies, no touching the display —
>   draw onto `ctx.canvas` and return True.
> - Use `ctx.layout` regions and `fit_text` for positioning so the skin works
>   at any panel size; use `.get()` for every optional game key.
> - After every change run
>   `python scripts/validate_skin.py --skin <my-skin-id>` and LOOK at the
>   PNGs it writes to `skin_renders/` (the `_x4.png` files are easiest to
>   read). Iterate until it passes and looks right at both 128x32 and 64x32.
>
> What I want it to look like: <describe your layout — where logos, score,
> status go; colors; what shows during live vs upcoming vs final>

Tips that keep Claude (and you) out of trouble:

- One mode at a time: get `render_live` right before touching the others —
  unimplemented modes automatically use the built-in look.
- Ask for edge-case renders: long team abbreviations, missing logos
  (`ctx.load_logo` returning `None`), 0-0 records, extra innings/OT.
- If the render looks cramped at 64x32, ask Claude to use
  `ctx.layout.by_tier(...)` to drop elements on small panels rather than
  shrinking everything.
- Never let it "fix" a problem by editing `src/` — if the skin can't do
  something within its directory, that's a feature request, not a workaround.

## Pre-publish checklist

- [ ] `python scripts/validate_skin.py --skin <id> --size 128x32 --size 64x32 --size 128x64` passes
- [ ] Looked at every PNG in `skin_renders/` — nothing clipped or overlapping
- [ ] Handles a missing logo (`None`) without crashing — temporarily point a
      fixture's logo path at a nonexistent file to test
- [ ] Long abbreviations (`"TA&M"`, 4–5 chars) don't overflow
- [ ] No render warning above the time budget
- [ ] `skin.json`: `id` matches the directory, `version` set,
      `skin_api_version` matches the host, targets correct
- [ ] `preview.png` added (grab your favorite `_x4` render)
- [ ] Tested on real hardware if you have it — a Pi is much slower than your
      dev machine

Distribute by publishing the directory as a git repo (users
`git clone <repo> skins/<id>`), or submit it to the plugin registry as an
entry with `"type": "skin"` (see [SKIN_SYSTEM.md](SKIN_SYSTEM.md) §Distribution).

**Trust note:** a skin is Python running inside the display service — the
same trust level as a plugin. Review code before installing skins from
others.
