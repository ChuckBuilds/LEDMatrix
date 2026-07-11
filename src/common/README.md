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

## Error Handling (`error_handler.py`)

Common error handling patterns and utilities:

- `handle_file_operation()` - Handle file I/O with consistent error handling
- `handle_json_operation()` - Handle JSON operations with error handling
- `safe_execute()` - Safely execute operations with error handling
- `retry_on_failure()` - Decorator for retrying failed operations
- `log_and_continue()` - Log non-critical errors and continue
- `log_and_raise()` - Log errors and raise exceptions

### Example Usage

```python
from src.common.error_handler import handle_json_operation, safe_execute

# Handle JSON loading
config = handle_json_operation(
    lambda: json.load(open('config.json')),
    "Failed to load config",
    logger,
    default={}
)

# Safe execution with error handling
result = safe_execute(
    lambda: risky_operation(),
    "Operation failed",
    logger,
    default=None
)
```

## API Helpers (`api_helper.py`)

Utilities for making HTTP requests and handling API responses.

## Configuration Helpers (`config_helper.py`)

Utilities for loading, saving, and validating configuration files.

## Display Helpers (`display_helper.py`)

Utilities for rendering content to the LED matrix display.

## Game Helpers (`game_helper.py`)

Utilities for processing game data and team information.

## Logo Helpers (`logo_helper.py`)

Utilities for loading and managing team logos.

## Text Helpers (`text_helper.py`)

Utilities for text processing and formatting.

## Scroll Helpers (`scroll_helper.py`)

Utilities for scrolling text on the display.

## General Utilities (`utils.py`)

General-purpose utility functions:
- Team abbreviation normalization
- Time formatting
- Boolean parsing
- Logger creation (deprecated - use `src.logging_config.get_logger()`)

## Permission Utilities (`permission_utils.py`)

Helpers for ensuring directory permissions and ownership are correct
when running as a service (used by `CacheManager` to set up its
persistent cache directory).

## CLI Helpers (`cli.py`)

Shared CLI argument parsing helpers used by `scripts/dev/*` and other
command-line entry points.

## Best Practices

1. **Use centralized logging**: Import from `src.logging_config` instead of creating loggers directly
2. **Use error handlers**: Use `error_handler` utilities for consistent error handling
3. **Reuse utilities**: Check existing utilities before creating new ones
4. **Document additions**: Add documentation when adding new utilities
