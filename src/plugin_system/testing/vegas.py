"""Offline checks for a plugin's live Vegas elements.

A plugin that implements ``get_vegas_elements()`` promises the ticker a few
things it cannot check for itself until they go wrong on a panel: unique,
stable keys; images at the display's height; the same width for the same key
until the data changes; the same result when nothing changed; and, for an
element with ``refresh_hz``, a ``redraw_vegas_element()`` that returns exactly
the size asked for, quickly. :func:`check_vegas_elements` exercises each of
those the way the Vegas ticker calls the hooks -- on a canvas of the plugin's
own, told its render width -- and says what failed.

``scripts/check_plugin.py`` runs it for every plugin that implements the hook.
See "Live Vegas elements" in docs/PLUGIN_API_REFERENCE.md.
"""

from __future__ import annotations

import time
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, field
from typing import Any, Iterator, List, Optional

from PIL import Image

#: A warm get_vegas_elements() slower than this holds the ticker's single
#: background worker, and the plugin's lock, for longer than it should.
SLOW_ELEMENTS_SECONDS = 0.2
#: A redraw_vegas_element() slower than this cannot keep up with a few Hz.
SLOW_REDRAW_SECONDS = 0.02
#: The narrowed render width the check also tries, as a share of the panel.
NARROW_PCT = 60


@dataclass
class VegasElementReport:
    """What :func:`check_vegas_elements` found."""
    implemented: bool
    elements: int = 0
    live: int = 0
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def implements_vegas_elements(plugin: Any) -> bool:
    """Whether the plugin's class overrides BasePlugin.get_vegas_elements."""
    from src.plugin_system.base_plugin import BasePlugin

    method = getattr(type(plugin), 'get_vegas_elements', None)
    return method is not None and method is not getattr(
        BasePlugin, 'get_vegas_elements', None)


@contextmanager
def _as_vegas_canvas(plugin: Any, display_manager: Any, width: int) -> Iterator[None]:
    """Run a hook the way Vegas does: told its width, on a canvas of its own."""
    plugin._vegas_render_width = width
    try:
        offscreen = getattr(display_manager, 'offscreen', None)
        with offscreen(width) if offscreen is not None else nullcontext():
            yield
    finally:
        plugin._vegas_render_width = None


def render_vegas_elements(plugin: Any, display_manager: Any,
                          width: Optional[int] = None) -> Any:
    """Call ``plugin.get_vegas_elements()`` as the Vegas ticker does."""
    render_width = int(width or display_manager.width)
    with _as_vegas_canvas(plugin, display_manager, render_width):
        return plugin.get_vegas_elements()


def redraw_vegas_element(plugin: Any, display_manager: Any, key: str,
                         width: int, height: int, at: Optional[float] = None,
                         render_width: Optional[int] = None) -> Any:
    """Call ``plugin.redraw_vegas_element()`` as the Vegas ticker does."""
    with _as_vegas_canvas(plugin, display_manager,
                          int(render_width or display_manager.width)):
        return plugin.redraw_vegas_element(
            key, width, height, time.monotonic() if at is None else at)


def _refresh_hz(element: Any) -> Optional[float]:
    """An element's refresh_hz as a number (None counts as 0), or None if it is not one."""
    try:
        return float(element.refresh_hz or 0.0)
    except (TypeError, ValueError):
        return None


def _usable(element: Any) -> bool:
    """A VegasElement the checks can read: a str key and an image."""
    from src.plugin_system.vegas_elements import VegasElement
    return (isinstance(element, VegasElement) and isinstance(element.key, str)
            and bool(element.key) and isinstance(element.image, Image.Image))


def check_vegas_elements(plugin: Any, display_manager: Any) -> VegasElementReport:
    """Exercise a plugin's live-element hooks and report what breaks the contract.

    Errors are what the ticker would refuse or show wrongly; warnings are what
    it would cope with but should not have to (slow calls, an element wider
    than the width the plugin was asked to render at).
    """
    report = VegasElementReport(implemented=implements_vegas_elements(plugin))
    if not report.implemented:
        return report
    from src.plugin_system.vegas_elements import VegasElement

    height = int(display_manager.height)
    full = int(display_manager.width)

    def fetch(width: int, label: str):
        started = time.perf_counter()
        try:
            result = render_vegas_elements(plugin, display_manager, width)
        except Exception as exc:  # noqa: BLE001 - a plugin hook can raise anything
            report.errors.append(f"get_vegas_elements() raised {exc!r} ({label})")
            return None, 0.0
        return result, time.perf_counter() - started

    first, _ = fetch(full, "full width")
    if first is None:
        if not report.errors:
            report.warnings.append(
                "get_vegas_elements() returned None: the ticker will use "
                "get_vegas_content() instead")
        return report
    if not isinstance(first, (list, tuple)):
        report.errors.append(
            f"get_vegas_elements() returned {type(first).__name__}, expected a list")
        return report

    seen = set()
    widths = {}
    for index, element in enumerate(first):
        where = f"element[{index}]"
        if not isinstance(element, VegasElement):
            report.errors.append(f"{where} is a {type(element).__name__}, not a VegasElement")
            continue
        key = element.key
        if not isinstance(key, str) or not key:
            report.errors.append(f"{where} has no key (a non-empty str is required)")
            continue
        where = f"element {key!r}"
        if key in seen:
            report.errors.append(f"{where} appears twice; keys must be unique")
            continue
        seen.add(key)
        image: Any = element.image      # typed Image, but a plugin may pass anything
        if not isinstance(image, Image.Image):
            report.errors.append(f"{where} image is a {type(image).__name__}")
            continue
        if element.image.height != height:
            report.errors.append(
                f"{where} is {element.image.height}px tall; the display is {height}px")
        if element.image.width <= 0 or element.image.height <= 0:
            report.errors.append(f"{where} image is empty ({element.image.width}x"
                                 f"{element.image.height})")
            continue
        if element.image.width > full:
            report.warnings.append(
                f"{where} is {element.image.width}px wide, wider than the "
                f"{full}px it was asked to render at")
        hz = _refresh_hz(element)
        if hz is None:
            report.errors.append(
                f"{where} refresh_hz {element.refresh_hz!r} is not a number")
        elif hz < 0:
            report.errors.append(f"{where} has a negative refresh_hz")
        report.elements += 1
        if element.live:
            report.live += 1
            widths[key] = element.image.width

    if report.errors:
        return report

    # The same data twice must give the same keys, widths and versions: the
    # ticker redraws on every update and swaps in only what changed.
    second, warm = fetch(full, "second call")
    if second is not None and not isinstance(second, (list, tuple)):
        report.errors.append(
            f"get_vegas_elements() returned {type(second).__name__} on a second "
            "call, expected a list")
    elif isinstance(second, (list, tuple)):
        again = {e.key: e for e in second if _usable(e)}
        for element in first:
            other = again.get(element.key)
            if other is None:
                report.errors.append(
                    f"element {element.key!r} disappeared on a second call with "
                    "no new data")
                continue
            if element.live and other.image.width != element.image.width:
                report.errors.append(
                    f"element {element.key!r} changed width with no new data "
                    f"({element.image.width} -> {other.image.width}px); a live "
                    "element's width must not depend on when it is drawn")
            if element.version is not None and other.version != element.version \
                    and not _refresh_hz(element):
                report.warnings.append(
                    f"element {element.key!r} changed version with no new data; "
                    "every update will redraw it")
    if warm > SLOW_ELEMENTS_SECONDS:
        report.warnings.append(
            f"get_vegas_elements() took {warm * 1000:.0f}ms with nothing new "
            f"(over {SLOW_ELEMENTS_SECONDS * 1000:.0f}ms); cache what has not "
            "changed")

    narrow = max(1, full * NARROW_PCT // 100)
    if narrow < full:
        narrowed, _ = fetch(narrow, f"{NARROW_PCT}% width")
        if isinstance(narrowed, (list, tuple)):
            for element in narrowed:
                if isinstance(element, VegasElement) and isinstance(element.image, Image.Image) \
                        and element.image.width > narrow:
                    report.warnings.append(
                        f"element {element.key!r} is {element.image.width}px wide at "
                        f"a {narrow}px render width; read get_vegas_render_width() "
                        "or display_manager.width when sizing it")
                    break

    has_redraw = type(plugin).redraw_vegas_element is not _base_redraw()
    for element in first:
        hz = _refresh_hz(element) or 0.0
        if not (element.live and hz > 0):
            continue
        if not has_redraw:
            report.warnings.append(
                f"element {element.key!r} asks for {hz:g}Hz but "
                "redraw_vegas_element() is not implemented; the ticker re-runs "
                "get_vegas_elements() under the plugin's lock instead")
            continue
        w, h = element.image.width, element.image.height
        started = time.perf_counter()
        try:
            redrawn = redraw_vegas_element(plugin, display_manager, element.key, w, h)
        except Exception as exc:  # noqa: BLE001 - a plugin hook can raise anything
            report.errors.append(f"redraw_vegas_element({element.key!r}) raised {exc!r}")
            continue
        took = time.perf_counter() - started
        if redrawn is not None:
            if not isinstance(redrawn, Image.Image):
                report.errors.append(
                    f"redraw_vegas_element({element.key!r}) returned "
                    f"{type(redrawn).__name__}, expected an Image or None")
            elif redrawn.size != (w, h):
                report.errors.append(
                    f"redraw_vegas_element({element.key!r}) returned "
                    f"{redrawn.width}x{redrawn.height}, asked for {w}x{h}")
        if took > SLOW_REDRAW_SECONDS:
            report.warnings.append(
                f"redraw_vegas_element({element.key!r}) took {took * 1000:.1f}ms "
                f"(over {SLOW_REDRAW_SECONDS * 1000:.0f}ms); the ticker will "
                "slow its refresh")
    return report


def _base_redraw():
    from src.plugin_system.base_plugin import BasePlugin
    return BasePlugin.redraw_vegas_element


def check_plugin_vegas_elements(plugin_id: str, plugin_dir: Any, config: dict,
                                mock_data: dict, width: int, height: int,
                                run_update: bool = True) -> VegasElementReport:
    """Load a plugin from its directory at one panel size and check its elements.

    What ``scripts/check_plugin.py`` runs: the plugin gets the same mocked
    managers as the rendering harness, and its update() is run first (a
    network error there is tolerated, as in the harness) so the elements are
    drawn from data rather than from an empty start.
    """
    from pathlib import Path

    from src.plugin_system.testing.harness import _TOLERATED_UPDATE_ERRORS, _instantiate
    from src.plugin_system.testing.loading import load_manifest
    from src.plugin_system.testing.visual_display_manager import VisualTestDisplayManager

    plugin_dir = Path(plugin_dir)
    display_manager = VisualTestDisplayManager(width=width, height=height)
    try:
        plugin = _instantiate(plugin_id, load_manifest(plugin_dir), plugin_dir,
                              config, mock_data, display_manager)
    except Exception as exc:  # noqa: BLE001 - the matrix run reports load errors
        report = VegasElementReport(implemented=False)
        report.warnings.append(f"not checked: the plugin did not load ({exc!r})")
        return report
    report = VegasElementReport(implemented=implements_vegas_elements(plugin))
    if not report.implemented:
        return report
    if run_update:
        try:
            plugin.update()
        except Exception as exc:  # noqa: BLE001 - a plugin's update can raise anything
            if not isinstance(exc, _TOLERATED_UPDATE_ERRORS):
                report.errors.append(f"update() raised {exc!r}")
                return report
            report.warnings.append(f"update() had no network ({exc!r}); checked "
                                   "with whatever data the plugin starts with")
    checked = check_vegas_elements(plugin, display_manager)
    checked.warnings[:0] = report.warnings
    return checked
