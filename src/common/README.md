# Common Utilities

This directory contains reusable utilities and helpers for LEDMatrix plugins and core modules.

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

## Best Practices

1. **Use centralized logging**: Import from `src.logging_config` instead of creating loggers directly
2. **Use error handlers**: Use `error_handler` utilities for consistent error handling
3. **Reuse utilities**: Check existing utilities before creating new ones
4. **Document additions**: Add documentation when adding new utilities
