# Dev Preview & Visual Testing

Tools for rapid plugin development without deploying to the RPi.

## Dev Preview Server

Interactive web UI for tweaking plugin configs and seeing the rendered display in real time.

### Quick Start

```bash
python scripts/dev_server.py
# Opens at http://localhost:5001
```

### Options

```bash
python scripts/dev_server.py --port 8080                          # Custom port
python scripts/dev_server.py --extra-dir /path/to/custom-plugin   # 3rd party plugins
python scripts/dev_server.py --debug                              # Flask debug mode
```

### Workflow

1. Select a plugin from the dropdown (auto-discovers from `plugins/` and `plugin-repos/`)
2. The config form auto-generates from the plugin's `config_schema.json`
3. Tweak any config value — the display preview updates automatically
4. Toggle "Auto" off for plugins with slow `update()` calls, then click "Render" manually
5. Use the zoom slider to scale the tiny display (128x32) up for detailed inspection
6. Toggle the grid overlay to see individual pixel boundaries

### Mock Data for API-dependent Plugins

Many plugins fetch data from APIs (sports scores, weather, stocks). To render these locally, expand "Mock Data" and paste a JSON object with cache keys the plugin expects.

To find the cache keys a plugin uses, search its `manager.py` for `self.cache_manager.set(` calls.

Example for a sports plugin:
```json
{
  "football_scores": {
    "games": [
      {"home": "Eagles", "away": "Chiefs", "home_score": 24, "away_score": 21, "status": "Final"}
    ]
  }
}
```

---

## CLI Render Script

Render any plugin to a PNG image from the command line. Useful for AI-assisted development and scripted workflows.

### Usage

```bash
# Basic — renders with default config
python scripts/render_plugin.py --plugin hello-world --output /tmp/hello.png

# Custom config
python scripts/render_plugin.py --plugin clock-simple \
  --config '{"timezone":"America/New_York","time_format":"12h"}' \
  --output /tmp/clock.png

# Different display dimensions
python scripts/render_plugin.py --plugin hello-world --width 64 --height 32 --output /tmp/small.png

# 3rd party plugin from a custom directory
python scripts/render_plugin.py --plugin my-plugin --plugin-dir /path/to/repo --output /tmp/my.png

# With mock API data
python scripts/render_plugin.py --plugin football-scoreboard \
  --mock-data /tmp/mock_scores.json \
  --output /tmp/football.png
```

### Using with Claude Code / AI

Claude can run the render script, then read the output PNG (Claude is multimodal and can see images). This enables a visual feedback loop:

```
Claude → bash: python scripts/render_plugin.py --plugin hello-world --output /tmp/render.png
Claude → Read /tmp/render.png   ← Claude sees the actual rendered display
Claude → (makes code changes based on what it sees)
Claude → bash: python scripts/render_plugin.py --plugin hello-world --output /tmp/render2.png
Claude → Read /tmp/render2.png  ← verifies the visual change
```

---

## VisualTestDisplayManager (for test suites)

A display manager that renders real pixels for use in pytest, without requiring hardware.

### Basic Usage

```python
from src.plugin_system.testing import VisualTestDisplayManager, MockCacheManager, MockPluginManager

def test_my_plugin_renders_title():
    display = VisualTestDisplayManager(width=128, height=32)
    cache = MockCacheManager()
    pm = MockPluginManager()

    plugin = MyPlugin(
        plugin_id='my-plugin',
        config={'enabled': True, 'title': 'Hello'},
        display_manager=display,
        cache_manager=cache,
        plugin_manager=pm
    )

    plugin.update()
    plugin.display(force_clear=True)

    # Verify pixels were drawn (not just that methods were called)
    pixels = list(display.image.getdata())
    assert any(p != (0, 0, 0) for p in pixels), "Display should not be blank"

    # Save snapshot for manual inspection
    display.save_snapshot('/tmp/test_my_plugin.png')
```

### Pytest Fixture

A `visual_display_manager` fixture is available in plugin tests:

```python
def test_rendering(visual_display_manager):
    visual_display_manager.draw_text("Test", x=10, y=10, color=(255, 255, 255))
    assert visual_display_manager.width == 128
    pixels = list(visual_display_manager.image.getdata())
    assert any(p != (0, 0, 0) for p in pixels)
```

### Key Differences from MockDisplayManager

| Feature | MockDisplayManager | VisualTestDisplayManager |
|---------|-------------------|--------------------------|
| Renders pixels | No (logs calls only) | Yes (real PIL rendering) |
| Loads fonts | No | Yes (same fonts as production) |
| Save to PNG | No | Yes (`save_snapshot()`) |
| Call tracking | Yes | Yes (backwards compatible) |
| Use case | Unit tests (method call assertions) | Visual tests, dev preview |

---

## Plugin Test Runner

The test runner auto-detects `plugin-repos/` for monorepo development:

```bash
# Auto-detect (tries plugins/ then plugin-repos/)
python scripts/run_plugin_tests.py

# Test specific plugin
python scripts/run_plugin_tests.py --plugin clock-simple

# Explicit directory
python scripts/run_plugin_tests.py --plugins-dir plugin-repos/

# With coverage
python scripts/run_plugin_tests.py --coverage --verbose
```
