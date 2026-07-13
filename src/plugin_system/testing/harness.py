"""
Plugin safety harness.

Renders a plugin across every declared screen (mode) and every supported matrix
size, capturing crashes and overflow. Used by scripts/check_plugin.py and the
pytest matrix test to guarantee a plugin change doesn't break a screen at a size
the author didn't try.

The render flow mirrors scripts/render_plugin.py (same PluginLoader call), but
this module adds: multi-size iteration, per-mode rendering, overflow detection
via BoundsCheckingDisplayManager, and golden-image comparison.
"""

import contextlib
import http.client
import inspect
import socket
import ssl
import urllib.error
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageChops

from src.logging_config import get_logger
from .bounds_display_manager import BoundsCheckingDisplayManager
from .loading import load_config_defaults, load_manifest
from .sizes import DEFAULT_TEST_SIZES, safe_mode_filename, size_label

logger = get_logger("[Plugin Harness]")


def _tolerated_update_errors() -> Tuple[type, ...]:
    """Exception types from update() we treat as a tolerated no-connectivity
    failure (expected in CI / headless dev) rather than a real plugin bug.

    Anything NOT in this set is a genuine regression — a plugin that lets a
    non-network exception escape update() should fail the harness, not pass
    green because display() happened to survive.
    """
    types: List[type] = [
        ConnectionError, TimeoutError,        # builtins
        socket.gaierror, socket.timeout,      # DNS / socket timeouts
        ssl.SSLError,
        urllib.error.URLError,
        http.client.HTTPException,
    ]
    try:  # requests is optional; cover its whole error tree when present
        import requests
        types.append(requests.exceptions.RequestException)
    except ImportError:  # pragma: no cover - requests not installed
        logger.debug("requests not installed; its connectivity errors won't be specifically tolerated")
    return tuple(types)


_TOLERATED_UPDATE_ERRORS = _tolerated_update_errors()


@dataclass
class RenderResult:
    """Outcome of rendering one (size, mode) of a plugin."""
    plugin_id: str
    width: int
    height: int
    mode: str
    image: Optional[Image.Image] = None
    error: Optional[str] = None          # fatal: load/display crash, or a non-network update() error
    update_error: Optional[str] = None   # tolerated: connectivity error from update() (no network in CI)
    overflow: Optional[Tuple[int, int, int, int]] = None  # bbox past the panel
    # golden comparison (populated only when a golden was provided)
    golden_checked: bool = False
    golden_ok: Optional[bool] = None
    golden_diff_pixels: int = 0
    golden_max_delta: int = 0
    # fill / scale-up check (populated only for sizes >= 2x the design size)
    fill_checked: bool = False
    fill_ok: Optional[bool] = None       # False only in strict mode
    fill_extent: Optional[Tuple[float, float]] = None  # (extent_x, extent_y)

    @property
    def size_label(self) -> str:
        return size_label(self.width, self.height)

    @property
    def ok(self) -> bool:
        """Phase-1 pass: rendered without crashing and without overflow, and if a
        golden was checked it matched."""
        if self.error is not None or self.overflow is not None:
            return False
        if self.golden_checked and self.golden_ok is False:
            return False
        if self.fill_ok is False:
            return False
        return True


def list_modes(plugin_instance: Any, manifest: Dict[str, Any], plugin_id: str) -> List[str]:
    """Enumerate a plugin's screens: instance.modes wins, then manifest
    display_modes, then the plugin id as a single mode."""
    modes = getattr(plugin_instance, "modes", None)
    if modes:
        return [str(m) for m in modes]
    declared = manifest.get("display_modes")
    if declared:
        return [str(m) for m in declared]
    return [plugin_id]


def _instantiate(plugin_id: str, manifest: Dict[str, Any], plugin_dir: Path,
                 config: Dict[str, Any], mock_data: Dict[str, Any],
                 display_manager: Any) -> Any:
    """Load and construct a plugin instance with mocked managers."""
    from src.plugin_system.plugin_loader import PluginLoader
    from src.plugin_system.testing import MockCacheManager, MockPluginManager

    cache_manager = MockCacheManager()
    for key, value in (mock_data or {}).items():
        cache_manager.set(key, value)

    loader = PluginLoader()
    plugin_instance, _module = loader.load_plugin(
        plugin_id=plugin_id,
        manifest=manifest,
        plugin_dir=plugin_dir,
        config=config,
        display_manager=display_manager,
        cache_manager=cache_manager,
        plugin_manager=MockPluginManager(),
        install_deps=False,
    )
    return plugin_instance


def _render_mode(plugin_instance: Any, mode: str) -> None:
    """Render a specific screen. Prefer an explicit display_mode kwarg; otherwise
    drive the plugin's internal mode state machine (first display() call renders
    modes[current_mode_index] when current_display_mode is None)."""
    sig = inspect.signature(plugin_instance.display)
    if "display_mode" in sig.parameters:
        plugin_instance.display(force_clear=True, display_mode=mode)
        return

    modes = getattr(plugin_instance, "modes", None)
    if modes and mode in modes:
        plugin_instance.current_mode_index = list(modes).index(mode)
    if hasattr(plugin_instance, "current_display_mode"):
        plugin_instance.current_display_mode = None
    plugin_instance.display(force_clear=False)


def _freeze(freeze_time: Optional[str]):
    """Context manager that freezes wall-clock time when freeze_time is given,
    so time-dependent plugins (clocks, countdowns) render deterministic goldens."""
    if not freeze_time:
        return contextlib.nullcontext()
    try:
        from freezegun import freeze_time as _ft
    except ImportError as e:  # pragma: no cover - only hit without the dep
        raise RuntimeError(
            "freeze_time requires the 'freezegun' package (pip install freezegun)"
        ) from e
    return _ft(freeze_time)


def render_plugin_matrix(
    plugin_id: str,
    plugin_dir: Path,
    config: Optional[Dict[str, Any]] = None,
    mock_data: Optional[Dict[str, Any]] = None,
    sizes: Optional[List[Tuple[int, int]]] = None,
    run_update: bool = True,
    freeze_time: Optional[str] = None,
) -> List[RenderResult]:
    """Render every (size, mode) combination for a plugin.

    Returns a flat list of RenderResult. A fresh plugin instance is built per
    (size, mode) so state never leaks between screens. Pass freeze_time (e.g.
    "2025-08-01 15:25:00") to make time-dependent plugins reproducible.
    """
    plugin_dir = Path(plugin_dir)
    manifest = load_manifest(plugin_dir)
    # Start from config_schema.json defaults so the plugin behaves like a real
    # install; explicit caller config still wins over a schema default.
    config = {"enabled": True, **load_config_defaults(plugin_dir), **(config or {})}
    sizes = sizes or DEFAULT_TEST_SIZES
    results: List[RenderResult] = []

    # The largest panel in this run. Every (smaller) canvas is padded out to it
    # so a coordinate meant for the biggest configuration is still caught when
    # rendering a smaller one, instead of being clipped into a false pass.
    extent = (max(w for w, _ in sizes), max(h for _, h in sizes))

    with _freeze(freeze_time):
        for width, height in sizes:
            results.extend(_render_size(
                plugin_id, manifest, plugin_dir, config, mock_data or {},
                width, height, run_update, extent,
            ))

    return results


def _render_size(plugin_id, manifest, plugin_dir, config, mock_data,
                 width, height, run_update, extent) -> List[RenderResult]:
    """Render every mode at one size. A fresh instance per mode avoids state leaks."""
    results: List[RenderResult] = []

    # Discover modes once per size (instance build can depend on config).
    try:
        probe_dm = BoundsCheckingDisplayManager(width=width, height=height, overflow_extent=extent)
        probe = _instantiate(plugin_id, manifest, plugin_dir, config, mock_data, probe_dm)
        modes = list_modes(probe, manifest, plugin_id)
    except Exception as e:  # noqa: BLE001 — surface any load failure as a result
        return [RenderResult(plugin_id, width, height, "<load>", error=repr(e))]

    for mode in modes:
        result = RenderResult(plugin_id, width, height, mode)
        dm = BoundsCheckingDisplayManager(width=width, height=height, overflow_extent=extent)
        try:
            inst = _instantiate(plugin_id, manifest, plugin_dir, config, mock_data, dm)
            if run_update:
                try:
                    inst.update()
                except _TOLERATED_UPDATE_ERRORS as e:
                    # Expected when CI / headless dev has no network: record it
                    # (surfaced in the report) but don't fail the run.
                    result.update_error = repr(e)
                    logger.debug("update() connectivity error for %s [%s]: %s", plugin_id, mode, e)
                except Exception as e:  # noqa: BLE001 — a non-network update() failure is a real bug
                    # A regression in update() must not pass green just because
                    # display() survives, so treat it as a failure of this render.
                    result.error = repr(e)
                    logger.warning("update() raised a non-connectivity error for %s [%s]: %s",
                                   plugin_id, mode, e)
            if result.error is None:
                _render_mode(inst, mode)
                result.image = dm.get_image()
                result.overflow = dm.check_overflow()
        except Exception as e:  # noqa: BLE001 — a display crash is a real failure
            result.error = repr(e)
        results.append(result)

    return results


# ---------------------------------------------------------------------------
# Golden-image comparison
# ---------------------------------------------------------------------------

def compare_images(rendered: Image.Image, golden: Image.Image,
                   max_delta: int = 0, max_diff_pixels: int = 0) -> Tuple[bool, int, int]:
    """Compare two images. Returns (ok, diff_pixel_count, max_per_channel_delta).

    Tolerances default to exact match; bump them only to absorb known platform
    anti-aliasing noise (requires a pinned Pillow + bundled fonts for stability).
    """
    if rendered.size != golden.size:
        return False, rendered.size[0] * rendered.size[1], 255
    a = rendered.convert("RGB")
    b = golden.convert("RGB")
    diff = ImageChops.difference(a, b)
    bbox = diff.getbbox()
    if bbox is None:
        return True, 0, 0
    # Count pixels whose largest per-channel delta exceeds the allowed tolerance,
    # and track the worst delta seen (for reporting).
    diff_pixels = 0
    observed_max = 0
    for px in diff.crop(bbox).getdata():
        m = max(px) if isinstance(px, tuple) else px
        if m > observed_max:
            observed_max = m
        if m > max_delta:
            diff_pixels += 1
    # Pass when the number of out-of-tolerance pixels is within budget.
    ok = diff_pixels <= max_diff_pixels
    return ok, diff_pixels, observed_max


def golden_path(golden_dir: Path, width: int, height: int, mode: str) -> Path:
    """Location of a golden image: <golden_dir>/<WxH>/<mode>.png.

    The mode is sanitized to a safe basename so a mode name with '/' or '..'
    can't read or write outside the golden directory.
    """
    return Path(golden_dir) / size_label(width, height) / f"{safe_mode_filename(mode)}.png"


def compare_to_goldens(results: List[RenderResult], golden_dir: Path,
                       max_delta: int = 0, max_diff_pixels: int = 0) -> List[RenderResult]:
    """Compare rendered results against committed goldens, mutating each result's
    golden_* fields. Results with no golden file on disk are left unchecked."""
    for r in results:
        if r.image is None:
            continue
        gp = golden_path(golden_dir, r.width, r.height, r.mode)
        if not gp.exists():
            continue
        r.golden_checked = True
        with Image.open(gp) as g:
            ok, diff_pixels, observed_max = compare_images(
                r.image, g, max_delta=max_delta, max_diff_pixels=max_diff_pixels)
        r.golden_ok = ok
        r.golden_diff_pixels = diff_pixels
        r.golden_max_delta = observed_max
    return results


# ---------------------------------------------------------------------------
# Fill / scale-up check
# ---------------------------------------------------------------------------
#
# Overflow catches content that is too BIG for a panel; nothing catches
# content that stays tiny on a panel much larger than the plugin's design
# size (e.g. 128x32 content in the corner of a 256x128 renders "green").
# These helpers measure how much of the panel the lit content spans so the
# harness can flag plugins that don't scale up.

# A pixel counts as "lit" above this luminance — low enough to catch dim
# content, high enough to ignore near-black noise.
_LIT_THRESHOLD = 16
# Content must span at least this fraction of an axis that is >= 2x the
# design size. Lenient on purpose: margins are fine, a tiny corner is not.
_MIN_FILL_EXTENT = 0.5


def fill_metrics(image: Image.Image) -> Tuple[float, float, float]:
    """Measure lit-content coverage: (extent_x, extent_y, ink_ratio).

    extent_* are the lit bounding box's spans as fractions of the panel;
    ink_ratio is the fraction of pixels lit (reporting only — sparse pixel
    fonts legitimately have low ink ratios)."""
    lit = image.convert("L").point(lambda p: 255 if p > _LIT_THRESHOLD else 0)
    bbox = lit.getbbox()
    if bbox is None:
        return (0.0, 0.0, 0.0)
    extent_x = (bbox[2] - bbox[0]) / image.width
    extent_y = (bbox[3] - bbox[1]) / image.height
    ink = sum(1 for p in lit.getdata() if p) / (image.width * image.height)
    return (extent_x, extent_y, ink)


def check_scale_up(results: List[RenderResult],
                   design_size: Tuple[int, int] = (128, 32),
                   min_extent: float = _MIN_FILL_EXTENT,
                   strict: bool = False) -> List[RenderResult]:
    """Flag renders that leave a big panel mostly empty.

    For each result whose panel is at least 2x the design size on an axis,
    require the lit content to span >= min_extent of that axis. Mutates the
    results' fill_* fields. In the default warn-only mode fill_ok is left
    None (reported, never failing); strict=True sets fill_ok=False, which
    fails RenderResult.ok — opt in per plugin via harness.json
    {"fill_check": "strict"} once its adaptive layout is in place.
    """
    design_w, design_h = design_size
    for r in results:
        if r.image is None or r.error is not None:
            continue
        check_x = r.width >= 2 * design_w
        check_y = r.height >= 2 * design_h
        if not (check_x or check_y):
            continue
        extent_x, extent_y, _ink = fill_metrics(r.image)
        r.fill_checked = True
        r.fill_extent = (round(extent_x, 3), round(extent_y, 3))
        underfilled = ((check_x and extent_x < min_extent)
                       or (check_y and extent_y < min_extent))
        if underfilled and strict:
            r.fill_ok = False
        elif not underfilled:
            r.fill_ok = True
        # warn-only underfill: fill_ok stays None; fill_extent tells the story
    return results


def write_goldens(results: List[RenderResult], golden_dir: Path) -> int:
    """Write each successfully-rendered result to its golden path. Returns count."""
    written = 0
    for r in results:
        if r.image is None or r.error is not None:
            continue
        gp = golden_path(golden_dir, r.width, r.height, r.mode)
        gp.parent.mkdir(parents=True, exist_ok=True)
        r.image.save(gp, format="PNG")
        written += 1
    return written
