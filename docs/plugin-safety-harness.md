# Plugin Safety Harness

Renders a plugin across **every declared screen (mode)** and **a spread of
matrix sizes**, and fails if any combination crashes, draws past the panel edge,
or — for plugins that ship golden images — drifts visually. The goal: change a
plugin without breaking a size or screen you didn't think to test.

## Sizes: a sample, not a fixed list

There is **no fixed set of supported panel sizes** — an RGB matrix build can be
any width/height and configuration (square, rectangle, 2×2, 4×4, 8×2, long
strips, tall stacks). Plugins are expected to read dimensions dynamically
(`self.display_manager.matrix.width/height`) and lay themselves out
accordingly, so a hardcoded coordinate or unscaled font shows up as a failure
here.

The harness therefore renders against a **representative sample** that spans the
axes of variation (`DEFAULT_TEST_SIZES` in `src/plugin_system/testing/sizes.py`),
not an authoritative list:

Each module is 64×32; entries are real panel-grid arrangements (cols × rows):

| Size    | Grid | Why it's in the sample                     |
|---------|------|--------------------------------------------|
| 64×32   | 1×1  | single panel — tightest common rectangle   |
| 128×32  | 2×1  | the baseline most plugins are tuned for    |
| 64×64   | 1×2  | stacked — tall-narrow centering            |
| 128×64  | 2×2  | block — icon scaling / vertical centering  |
| 256×32  | 4×1  | long strip — wide horizontal layout        |
| 128×96  | 2×3  | tall — vertical overflow                   |
| 256×128 | 4×4  | large block — both dimensions big at once  |

**Override the sizes entirely** to test your actual hardware (or any shape):

```bash
# CLI — one-off:
python scripts/check_plugin.py --plugin clock-simple --sizes 8x16,64x64,256x32

# pytest — force every plugin onto your panel(s):
LEDMATRIX_TEST_SIZES="8x16,128x128" pytest test/plugins/test_plugin_matrix.py

# Per-plugin — declare the shapes a plugin targets in its test/harness.json:
#   { "sizes": [[8, 16], [64, 64]] }
```

Precedence: `LEDMATRIX_TEST_SIZES` env (global) → per-plugin `harness.json`
`sizes` → the default sample. Bounds checking adapts to whatever sizes a run
uses — the backing canvas is padded out to the **largest** panel in the run, so
a coordinate meant for a big build is still caught when rendering a small one.

## Quick start

```bash
# Functional + bounds check across all sizes/screens:
python scripts/check_plugin.py --plugin clock-simple

# Every discovered plugin:
python scripts/check_plugin.py --all

# Dump PNGs to eyeball each size/screen:
python scripts/check_plugin.py --plugin ledmatrix-weather --out-dir /tmp/preview
```

Exit code is non-zero if any `(plugin, size, screen)` fails. Plugins are
discovered in `plugin-repos/` and `plugins/` (override with `--plugin-dir`).

## What it checks (Phase 1 — always on)

1. **Loads** and builds its mode list.
2. **Renders every screen** at every size without raising. `update()` may fail
   (no network in CI) and is tolerated; a crash in `display()` is a failure —
   `display()` must handle the no-data state.
3. **Bounds**: nothing is drawn past the right/bottom edge. Implemented by
   `BoundsCheckingDisplayManager`, which backs the declared panel with an
   oversized canvas and flags any pixels that land in the margin. (Left/top
   overflow at negative coordinates and BDF text are not flagged — golden images
   cover those.)

## Golden images (Phase 2 — opt-in per plugin)

A plugin opts in by committing reference PNGs and (usually) a small harness spec:

```
plugins/<id>/test/harness.json          # how to render deterministically
plugins/<id>/test/fixtures/mock.json     # optional cached data
plugins/<id>/test/golden/<WxH>/<mode>.png
```

`test/harness.json` keys (all optional):

```json
{
  "config":      { "timezone": "UTC" },
  "mock_data":   "fixtures/mock.json",
  "freeze_time": "2025-08-01 15:25:00",
  "skip_update": false,
  "sizes":       [[128, 32], [128, 64]]
}
```

Generate / refresh goldens after an intentional visual change, then review the
diff before committing:

```bash
python scripts/check_plugin.py --plugin clock-simple --update-golden \
  --config '{"timezone":"UTC"}' --freeze-time "2025-08-01 15:25:00"
```

Comparison is exact by default (`compare_images` in `harness.py` accepts a
tolerance for known anti-aliasing noise). Determinism requires a pinned Pillow
and the bundled fonts — keep both stable when regenerating goldens.

## Tests & CI

- `test/plugins/test_harness.py` — unit tests for bounds detection, image
  comparison, and mode enumeration (run anywhere).
- `test/plugins/test_plugin_matrix.py` — parametrized over discovered plugins ×
  sizes × screens; honors each plugin's `test/harness.json` and goldens. Skips
  when no plugins are present (e.g. a fresh core checkout); set
  `LEDMATRIX_REQUIRE_PLUGINS=1` in a pipeline where plugins must be present to
  turn an empty discovery into a hard failure instead. Point it at the monorepo
  with `LEDMATRIX_PLUGINS_DIR=/path/to/ledmatrix-plugins/plugins`.
- `.github/workflows/test.yml` — runs the harness + visual tests on every PR.

The plugin monorepo has its own `Plugin Safety` workflow that runs this harness
against changed plugins on every PR.

## Developer workflow

1. Change the plugin on a branch.
2. `python scripts/check_plugin.py --plugin <id> --out-dir /tmp/preview` and
   eyeball the PNGs.
3. Intentional visual change? `--update-golden`, review diffs, commit goldens.
4. (Monorepo) bump `manifest.json` version and let the pre-commit hook sync
   `plugins.json`.
5. Push — CI re-runs the harness across all sizes and gates the PR.
