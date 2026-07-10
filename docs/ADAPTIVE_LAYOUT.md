# Adaptive Layout & Font Scaling

`src/adaptive_layout.py` lets a plugin render legibly on **any** panel size
(64x32, 128x32, 96x48, 128x64, 256x64, ...) without hand-tuned per-display
layouts. It is **opt-in**: nothing changes for plugins that don't use it.

It generalizes three patterns proven in the plugin ecosystem:

| Pattern | Origin | Core API |
|---|---|---|
| Geometry scale factor vs. a design size | f1-scoreboard | `ctx.px(base)` / `ctx.scale` |
| Breakpoint tiers | masters-tournament | `ctx.tier` / `ctx.by_tier({...})` |
| "Largest crisp font that fits" ladder | baseball-scoreboard | `ctx.fit_text(...)` and friends |

## Quick start

Every `BasePlugin` has a lazy `self.layout` (a `LayoutContext` for the
current logical display size, rebuilt automatically if the size changes)
and a one-liner `self.draw_fit(...)`:

```python
def display(self, force_clear=False):
    from src.adaptive_layout import LADDER_ARCADE

    b = self.layout.bounds.inset(1)          # Region(0,0,W,H) minus 1px margin
    rows = b.split_v(3, 1, 1, gap=1)          # 3/5 for time, 1/5 each for the rest

    self.draw_fit(self.time_str, rows[0], ladder=LADDER_ARCADE)
    self.draw_fit(self.weekday, rows[1])      # default LADDER_GRID
    self.draw_fit(self.date_str, rows[2])
    self.display_manager.update_display()
```

On 128x64 the time renders at press_start 24px; on 64x32 it steps down to
8px. The rows partition the height, so bands can never overlap — no more
`y = height - 7` magic numbers.

## Region — rect algebra

`Region(x, y, w, h)` is a frozen dataclass. All carving clamps to
non-negative dimensions, so degenerate panels behave.

- Carving: `inset(dx, dy)`, `top_band(h)`, `bottom_band(h)`,
  `middle(top_h, bottom_h)`, `left_col(w)`, `right_col(w)`,
  `split_h(*weights, gap=0)`, `split_v(*weights, gap=0)`
- Placement: `align_xy(w, h, align, valign)`, `center_xy(w, h)`,
  `contains(w, h)`, `.center`, `.right`, `.bottom`

Scoreboard-style layout:

```python
b = self.layout.bounds
status = b.top_band(self.layout.px(7))
detail = b.bottom_band(self.layout.px(7))
score_area = b.middle(status.h, detail.h)
away_slot, home_slot = b.left_col(b.h), b.right_col(b.h)
```

## Font ladders — discrete, never fractional

Pixel fonts (BDF, PressStart2P) only look right at native/integer sizes, so
fonts are never scaled continuously. A `FontLadder` is an ordered tuple of
`FontStep(family, size_px)` rungs, largest first; fitting walks down until
the measured text fits.

- `LADDER_GRID` (default): X11 BDFs at native sizes — 10x20 → 9x18 → 9x15 →
  8x13 → 7x13 → 6x13 → 6x12 → 6x10 → 6x9 → 5x8 → 5x7 → 4x6 → tom-thumb.
  Body text, labels, multi-row content.
- `LADDER_ARCADE`: PressStart2P at 32/24/16/8 (integer multiples of its 8px
  grid). Headline text: clocks, scores.

Custom ladders are just tuples — e.g. to add your plugin's registered font
on top: `(FontStep("myplugin::digits", 16),) + LADDER_GRID`.

## LayoutContext

Built per (width, height); exposes facts and fit queries:

- `bounds`, `width`, `height`, `aspect`
- `tier` by height (`xs`≤16, `sm`≤32, `md`≤48, `lg`≤64, `xl`) and
  `width_tier` (`narrow`≤64, `normal`≤128, `wide`≤256, `ultrawide`)
- `is_wide_short` — aspect ≥ 2.5 and height ≤ 32 (the classic 128x32 shape)
- `scale` — `min(w/design_w, h/design_h)` vs. your manifest's
  `display.design_size` (default 128x32). **Geometry only** — gaps, icon
  and logo sizes via `px(base, minimum, maximum)`; fonts use ladders.
- `by_tier({"sm": 10, "lg": 18})` — value for the nearest defined tier
  at-or-below the panel's tier.
- `fit_text(text, box, ladder, ellipsis=True)` → `FitResult` — largest rung
  that fits; ellipsizes as a last resort. Cached per (text, box, ladder).
- `fit_text_proportional(text, box, base_size_px, ladder, ellipsis=True)` —
  rung closest to (not exceeding) `base_size_px * scale`, still capped to
  what fits the box. Use this instead of `fit_text` when several
  independently-fitted elements need to stay visually harmonious as the
  panel grows — `fit_text` maximizes *each one* within its own region,
  which can make one element (e.g. a score with a generous box) balloon
  out of proportion to a neighbor that scales by geometry (e.g. logos
  sized via `px()`), even though each individual pick is "correct" in
  isolation. `base_size_px` is normally the element's existing classic/
  fixed font size, so it scales in step with everything else.
- `fit_lines(lines, box, ladder, spacing)` — every line fits the width and
  the stack fits the height (measures the actual strings).
- `font_for_rows(rows, box_h, ladder)` — largest rung whose line height
  fits `rows` rows.

`FitResult` carries the ready-to-use `font` (drops straight into
`display_manager.draw_text(font=...)`), the possibly-ellipsized `text`,
ink `width`/`height`, `baseline`, `y_offset`, `line_height`, and `fits`.

## Adaptive images

`src/adaptive_images.py` is the image counterpart to `fit_text`, exposed as
`self.layout.fit_image(...)` (cached per panel size) and the one-liner
`self.draw_image(...)`:

```python
# Team logo: trim its transparent padding, fill the slot height (the
# football/hockey pattern), cached across frames by a stable key
self.draw_image(logo, regs.away_slot, mode="fill_height",
                crop_to_ink=True, cache_key=f"logo:{abbr}")

# Album art: cover-crop a square, faces kept by the top anchor
self.draw_image(art, row.art, mode="cover", anchor="top")

# Pixel flags / sprite icons: NEAREST keeps hard edges
from src.adaptive_images import RESAMPLE_NEAREST
self.draw_image(flag, box, resample=RESAMPLE_NEAREST)
```

Modes: `contain` (letterbox, default), `cover` (crop-to-fill),
`fill_height` (logo-style), `stretch`. Unlike PIL's `thumbnail()`
(downscale-only — why imagery stays tiny on big panels) fitting **upscales
by default**; pass `upscale=False` for the legacy behavior. Results are
cached per (image, box size, options) with a bounded LRU — always pass a
stable `cache_key` (e.g. `"logo:KC"`) for images you reload. The module
also exports the Pillow-compat `RESAMPLE_LANCZOS`/`RESAMPLE_NEAREST`
constants so plugins can drop their local shims.

## Composite layouts

Pre-carved Region arrangements for the layouts plugins keep rebuilding:

```python
from src.adaptive_layout import scoreboard_regions, media_row

regs = scoreboard_regions(self.layout.bounds, ctx=self.layout)
# regs.away_slot / home_slot   — logo slots (logo_slot = min(H, W // 2))
# regs.status_band             — top band (replaces the magic y = 1)
# regs.score_area              — center region (replaces y = H//2 - 3)
# regs.detail_band             — bottom band (replaces y = H - 7)
# regs.bottom_left / bottom_right — record/timeout corners

row = media_row(self.layout.bounds, ctx=self.layout)   # art left, text right
```

Both work on the full panel or on a scroll-mode card Region. They return
Regions and never draw — compose them with `draw_fit`/`draw_image`.

## Preserving user customization

Adaptive layout supplies *defaults*; explicit user configuration wins:

- **User-set fonts win.** If the plugin's config has an explicit
  `font`/`font_size` for an element, load it as before and skip the ladder —
  fit only when the user hasn't overridden (see the football-scoreboard
  `_resolve_element_fit` pattern).
- **Offsets apply on top.** `customization.layout.<element>.{x_offset,y_offset}`
  style knobs translate the *computed* region as a final step:
  `region.offset(user_dx, user_dy)`. `draw_image(..., offset=(dx, dy))`
  does the same for images.
- **Colors pass through.** `draw_fit`/`draw_fitted_text` take explicit
  `color=` params; adaptive mode never repaints semantic or user-chosen
  colors.

## Manifest declaration

Declare the size your layout was authored against so `ctx.scale` means
something:

```json
"display": { "design_size": { "width": 128, "height": 32 } }
```

Also available under `requires.display_size`: `min_width`, `min_height`,
`max_width`, `max_height`.

## Performance notes (Pi)

Fit queries are cached, so cost is O(unique strings). For per-second text
(clocks, live scores), fit on a **shape placeholder** and reuse the font:

```python
fit = self.layout.fit_text("00:00", box, ladder=LADDER_ARCADE)  # cached once
self.display_manager.draw_text(current_time, font=fit.font, ...)
```

## Testing across sizes

The harness already renders every plugin at a spread of sizes (now
including 96x48):

```bash
python scripts/check_plugin.py <plugin-dir> --sizes 64x32,128x32,96x48,128x64,256x64
python scripts/render_plugin.py <plugin-dir> --width 96 --height 48
```

`BoundsCheckingDisplayManager` flags right/bottom overflow and now records
mediated draw calls with negative coordinates in
`negative_coordinate_calls` (raw-PIL draws remain uncovered).

Reference migration: the **text-display** plugin's `font_mode: "auto"`.
