# Common Utilities

This directory contains reusable utilities and helpers for LEDMatrix plugins and core modules.

## Adaptive Layout & Images (`src/adaptive_layout.py`, `src/adaptive_images.py`)

The recommended way to lay out plugins that render legibly on **any** panel
size (64x32 through 256x128+) without hand-tuned coordinates. Re-exported
from `src.common` for convenience; canonical import paths are
`src.adaptive_layout` / `src.adaptive_images`.

```python
# Every BasePlugin already has self.layout and the draw helpers:
regs = scoreboard_regions(self.layout.bounds, ctx=self.layout)
self.draw_image(away_logo, regs.away_slot, mode="fill_height",
                crop_to_ink=True, cache_key=f"logo:{abbr}")
self.draw_fit(score_text, regs.score_area)     # largest crisp font that fits
self.draw_fit(status, regs.status_band)
```

Key pieces: `Region` (rect algebra: bands/columns/splits/offset),
font ladders (`LADDER_GRID`, `LADDER_ARCADE` — discrete crisp sizes, never
fractional scaling), `LayoutContext` (`fit_text`, `fit_image`, `by_tier`,
`px`), and composite carvers `scoreboard_regions()` / `media_row()`.
Full guide: [docs/ADAPTIVE_LAYOUT.md](../../docs/ADAPTIVE_LAYOUT.md).

## API Helpers (`api_helper.py`)

Utilities for making HTTP requests and handling API responses.

## Logo Helpers (`logo_helper.py`)

Utilities for loading and managing team logos.

## Text Helpers (`text_helper.py`)

Utilities for text processing and formatting.

## Scroll Helpers (`scroll_helper.py`)

Utilities for scrolling text on the display.

## Permission Utilities (`permission_utils.py`)

Helpers for ensuring directory permissions and ownership are correct
when running as a service (used by `CacheManager` to set up its
persistent cache directory).

## Best Practices

1. **Use centralized logging**: Import from `src.logging_config` instead of creating loggers directly
2. **Reuse utilities**: Check existing utilities before creating new ones
3. **Document additions**: Add documentation when adding new utilities
