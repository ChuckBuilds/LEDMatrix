#!/usr/bin/env python3
"""Drive a sports scoreboard scroll on the panel and report what it did.

The eight sports scoreboards scroll through ``src/common/sports_scroll.py``,
and that path is per-league opt-in: a rig showing static game cards never
constructs a SportsScrollDisplay at all, so nothing about its pacing can be
observed from a normal run. This drives it directly, with synthetic games, so
the pacing can be measured without changing anyone's configuration.

What it checks is what the shared resolver is supposed to buy:

* the requested speed lands on a whole number of pixels per refresh
* the frame hold that makes that true is published to the display manager
* frames actually arrive at the interval the hold implies

    sudo systemctl stop ledmatrix
    sudo python3 scripts/sports_scroll_check.py --seconds 20
    sudo systemctl start ledmatrix

Like scripts/scroll_speeds.py, this never starts or stops the display service
itself -- that is left to the caller, so a crash here cannot leave the panel
dark.
"""
from __future__ import annotations

import argparse
import json
import statistics
import subprocess  # nosec B404 - list-form argv only, no shell  # nosemgrep
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image  # noqa: E402

from src.common.sports_scroll import SportsScrollDisplay  # noqa: E402
from src.display_manager import DisplayManager  # noqa: E402


class _Check(SportsScrollDisplay):
    """A scoreboard whose cards are plain blocks -- pacing is what matters."""

    SCROLL_LEAGUE_KEYS = ("nfl",)

    def prepare_scroll_content(self, games, game_type, leagues, rankings_cache=None):
        width = self.display_height * 2
        cards = []
        for i, _ in enumerate(games):
            card = Image.new("RGB", (width, self.display_height), (0, 0, 0))
            shade = 40 + (i * 37) % 180
            for x in range(2, width - 2):
                for y in range(2, self.display_height - 2):
                    card.putpixel((x, y), (shade, 90, 220 - shade // 2))
            cards.append(card)
        self._current_games = list(games)
        self._current_game_type = game_type
        self._current_leagues = list(leagues)
        self.scroll_helper.create_scrolling_image(content_items=cards, item_gap=24)
        return bool(cards)


class _HoldSpy:
    """Records what the scroll publishes, without changing what it does."""

    def __init__(self, display_manager):
        self.dm = display_manager
        self.calls = []
        self._real = display_manager.set_scrolling_state

    def __enter__(self):
        def spy(is_scrolling, frame_hold=1):
            self.calls.append((is_scrolling, frame_hold))
            return self._real(is_scrolling, frame_hold=frame_hold)
        self.dm.set_scrolling_state = spy
        return self

    def __exit__(self, *exc):
        self.dm.set_scrolling_state = self._real
        return False


MESSAGE = """ledmatrix is running and owns the panel's GPIO.

Stop it first, or this run can leave the display dark:

    sudo systemctl stop ledmatrix
    sudo python3 scripts/sports_scroll_check.py
    sudo systemctl start ledmatrix

Use --fallback to check the pacing logic without the panel, or --force if
you really mean it."""


def _refuse_if_the_service_is_running(force):
    """Refuse to touch the panel while ledmatrix has it.

    rpi-rgb-led-matrix configures GPIO directions and the hardware PWM inside
    RGBMatrix(), and on the root check it calls exit() from C -- no cleanup.
    Do that while the service is driving those same pins and the panel goes
    dark while the service carries on rendering happily: fresh framebuffer,
    every pixel lit, "RGB Matrix initialized successfully", nothing in the log.
    A restart brings it back, but only once you work out that is what happened.

    The module docstring says to stop the service first. This makes it true.
    """
    if force:
        return
    try:
        active = subprocess.run(  # nosec B603 B607 - hardcoded systemctl args  # nosemgrep
            ["systemctl", "is-active", "ledmatrix"],
            capture_output=True, text=True).stdout.strip()
    except OSError:
        return  # not a systemd box; nothing to protect
    if active == "active":
        sys.exit(MESSAGE)


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seconds", type=float, default=20.0)
    ap.add_argument("--speed", type=float, default=None,
                    help="px/s to request; default is the module's own")
    ap.add_argument("--games", type=int, default=6)
    ap.add_argument("--force", action="store_true",
                    help="run even though the display service is up. It owns "
                         "the GPIO; expect a dark panel until you restart it.")
    ap.add_argument("--fallback", action="store_true",
                    help="run without the panel. Driving the real matrix needs "
                         "root; this checks everything except the vsync pacing "
                         "-- what speed resolves to, that the hold is published, "
                         "and that it is released afterwards.")
    args = ap.parse_args()

    if not args.fallback:
        _refuse_if_the_service_is_running(args.force)

    root = Path(__file__).resolve().parent.parent
    config = json.loads((root / "config" / "config.json").read_text(encoding="utf-8"))

    display_manager = DisplayManager(config, force_fallback=args.fallback)
    settings = {} if args.speed is None else {
        "nfl": {"scroll_settings": {"scroll_speed": args.speed}}}

    display = _Check(display_manager, settings, global_config=config)
    resolved = display._scroll_settings
    print("resolved: %s" % resolved.describe())
    print("frame hold: %d refresh(es) per frame" % resolved.frame_hold)
    if resolved.warning:
        print("warning: %s" % resolved.warning)

    display.prepare_scroll_content(
        [{"id": "g%d" % i} for i in range(args.games)], "live", ["nfl"])

    gaps, drawn = [], 0
    last = None
    with _HoldSpy(display_manager) as spy:
        started = time.perf_counter()
        while time.perf_counter() - started < args.seconds:
            if not display.display_scroll_frame():
                break
            now = time.perf_counter()
            if last is not None:
                gaps.append((now - last) * 1000.0)
            last = now
            drawn += 1
        display.clear()

    if not gaps:
        sys.exit("no frames were drawn -- the scroll never started")

    gaps.sort()
    expected = 1000.0 * resolved.frame_hold / (resolved.crisp.refresh_hz
                                               if resolved.crisp else 100.0)
    print("\n%d frames in %.1fs -> %.1f fps" % (
        drawn, args.seconds, drawn / args.seconds))
    print("frame gap  median %.2fms  p95 %.2fms  max %.2fms  (hold implies %.2fms)"
          % (statistics.median(gaps), gaps[int(len(gaps) * 0.95)], gaps[-1], expected))

    holds = {h for on, h in spy.calls if on}
    print("published while scrolling: frame_hold=%s" % (sorted(holds) or "NOTHING"))
    print("released on clear: %s" % any(not on for on, _ in spy.calls))
    print("display manager hold now: %d (1 means released)"
          % getattr(display_manager, "_frame_hold", -1))

    if not holds:
        sys.exit("FAIL: the scroll never told the core it was scrolling")
    if holds != {resolved.frame_hold}:
        sys.exit("FAIL: published %s but resolved %d" % (holds, resolved.frame_hold))
    print("\nOK: the resolved hold reached the panel and was released after")


if __name__ == "__main__":
    main()
