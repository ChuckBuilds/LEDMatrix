# Per-element styling for plugin authors

Users want to change the font, size and colour of individual things on screen,
nudge them a few pixels, hide the ones they do not care about, and scale a logo
down. This is the one system that does that, and a plugin joins it by
**declaring elements in its `config_schema.json`** — not by writing a style
resolver, a font cache or a web form.

The short version:

```jsonc
"customization": {
  "type": "object",
  "title": "Display Customization",
  "x-style-elements": {
    "score_text": {
      "title": "Score",
      "font":  { "default": "PressStart2P-Regular.ttf" },
      "size":  { "default": 10, "min": 4, "max": 16 },
      "color": { "default": [255, 255, 255] },
      "offsets": true,
      "visible": true,
      "align": true
    },
    "home_logo": { "title": "Home logo", "offsets": true, "scale": true }
  },
  "x-style-modes": ["live", "upcoming", "recent"]
}
```

That is the whole declaration. The core expands it into a full JSON Schema, the
web UI renders a compact style editor with a row per element, and the values
land in `config.json` under the keys you named.

## What each key does

| Key | Effect |
|---|---|
| `font` | Font picker listing every shipped **and user-uploaded** font. |
| `size` | Number field. `min`/`max` also cap which fixed-size fonts are offered. |
| `color` | Colour swatch; stored as `[r, g, b]`. |
| `offsets` | X/Y nudge, stored under `customization.layout.<element>`. |
| `visible` | Show/hide toggle. |
| `align` | `left` / `center` / `right`. |
| `scale` | Size multiplier, for logos and images. Also under `layout`. |

`x-style-modes` is optional. Declare it and every element gains a per-mode
override tab — a scoreboard can then style its live, upcoming and recent cards
separately. **A mode field left blank means "inherit", not zero.**

## Reading the values

Every plugin inherits `BasePlugin.styles`, which finds your `config_schema.json`
on its own:

```python
style = self.styles.style(
    "score_text",
    classic_font="PressStart2P-Regular.ttf",   # what you shipped
    classic_size=10,
    classic_color=(255, 255, 255),
)
if style.visible:
    draw.text((x + style.offset[0], y + style.offset[1]),
              text, font=style.font, fill=style.color)
```

For a specific mode, use `self.styles_for("recent")`, or set
`STYLE_MODE = "recent"` on the class and keep calling `self.styles`.

### The one rule that matters

**Pass your shipped values as the `classic_*` arguments.** The resolver returns
them verbatim unless the user actually changed something, which is what keeps an
untouched install rendering byte-identically. It can tell the difference because
a value only counts as user-forced when it *differs from the schema default* —
the save path writes the full default object into `config.json` on every save,
so "present in config" proves nothing.

Never compare against the default yourself; that rule lives in exactly one place.

### Stateless readers

For helpers handed a config dict rather than a plugin instance:

```python
from src.element_style import (element_color, element_visible,
                               element_align, element_scale, layout_offset)

colour = element_color(config, "score_text", (255, 255, 255), mode)
shown  = element_visible(config, "records", True, mode)
dy     = layout_offset(config, "score", "y_offset", 0, mode)
```

## Sports scoreboards

`SportsCoreSharedMixin` wires most of this up already. Two things to know:

* **Name your draws.** `_draw_text_with_outline(..., element="score_text")`
  resolves the colour by name *and* honours the visibility toggle. Without it
  the colour has to be guessed from the identity of the font object, which
  cannot tell two elements apart when they share a face — the case every
  bitmap font is in.
* **Modes are free.** Live/upcoming/recent are separate instances, so setting
  `SKIN_MODE` on each is enough; no call site passes a mode.

## Adopting an existing hand-written block

If your schema already spells out `font` / `font_size` / `text_color` per
element longhand, **you do not need to change anything**. The core recognises
that shape and upgrades it in place: the style editor, the real font picker
(including uploaded fonts) and per-mode overrides all appear on a core update.
Add `x-style-modes` if you want the mode tabs.

## Fonts, and why size is sometimes locked

32 of the 35 shipped fonts are fixed-strike BDF bitmaps: they render at exactly
one pixel size and ignore `font_size`. The picker knows which, and the editor
locks the size field to the native size and labels it `fixed`. A font too tall
for the `max` you declared is not offered at all.

Uploaded fonts (Fonts tab) land in `assets/fonts/` and appear in the picker
automatically.

## Checklist

1. Declare `x-style-elements` (and `x-style-modes` if you have modes).
2. Read through `self.styles`, passing your shipped values as `classic_*`.
3. Honour `style.visible`, `style.offset` and `style.scale` where they apply.
4. Confirm an untouched config renders identically:
   `python scripts/check_plugin.py --plugin <id>`.
5. Monorepo plugins: bump `manifest.json` and run `python update_registry.py`.

See also: [docs/PLUGIN_CONFIGURATION_GUIDE.md](PLUGIN_CONFIGURATION_GUIDE.md),
[docs/FONT_MANAGER.md](FONT_MANAGER.md).
