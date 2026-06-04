# Plugin Safety Harness

Renders a plugin across **every declared screen (mode)** and **every supported
matrix size**, and fails if any combination crashes, draws past the panel edge,
or — for plugins that ship golden images — drifts visually. The goal: change a
plugin without breaking a size or screen you didn't think to test.

## Supported sizes

Defined once in `src/plugin_system/testing/sizes.py`:

| Size    | Build                |
|---------|----------------------|
| 64×32   | single panel         |
| 128×32  | two chained          |
| 128×64  | 2×2 block            |
| 256×32  | four chained         |

Plugins must read dimensions dynamically (`self.display_manager.matrix.width/height`),
so a single hardcoded coordinate or unscaled font shows up as a failure here.

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
  "skip_update": false
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
  when no plugins are present (e.g. a fresh core checkout). Point it at the
  monorepo with `LEDMATRIX_PLUGINS_DIR=/path/to/ledmatrix-plugins/plugins`.
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
