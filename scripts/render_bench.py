#!/usr/bin/env python3
"""Benchmark the render loop against the panel's real refresh rate.

The question this answers is the one that decides whether a rig ships: *does
every frame present on the refresh it was meant to?* It drives the production
path -- a real ``DisplayManager`` and ``ScrollHelper``, the same crisp speed
resolver every ticker uses -- scrolls for a while, and grades the result with
``src.common.frame_pacing``. A run passes when the loop was genuinely locked to
the panel and fewer than ``--max-missed`` percent of frames slipped a refresh.

    # stop the service first; it owns the GPIO
    sudo systemctl stop ledmatrix

    sudo python3 scripts/render_bench.py                    # 60s, default speed
    sudo python3 scripts/render_bench.py --seconds 600      # the 10-minute gate
    sudo python3 scripts/render_bench.py --speed 50         # a slower, held speed
    sudo python3 scripts/render_bench.py --busy 2           # with background load
    sudo python3 scripts/render_bench.py --json /tmp/pi4.json

    sudo systemctl start ledmatrix

Like scripts/scroll_speeds.py, this never starts or stops the service itself,
so a crash here can never leave the panel dark.

Exit status is 0 when the run clears the gate, 1 when it does not, and 2 when
the run could not be set up (no hardware, no root, unusable config) -- so a rig
that cannot be measured is never mistaken for a rig that passed.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import threading
import time
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.common import frame_pacing, scroll_config  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
CONFIG = REPO / "config" / "config.json"

#: Long enough to average out a scheduler hiccup, short enough that nobody
#: skips running it. The shipping gate is --seconds 600.
DEFAULT_SECONDS = 60.0

#: Seconds spent timing bare swaps before the scroll starts. The measurement
#: has to settle, but every second here is a second not scrolling.
MEASURE_SECONDS = 4.0


def load_config() -> dict:
    """The config the display service would run with."""
    try:
        from src.config_manager import ConfigManager

        config = ConfigManager().config
        if isinstance(config, dict) and config:
            return config
    except Exception:
        pass
    # ConfigManager pulls in a lot; a plain read is enough to drive the panel
    # and keeps the benchmark usable on a half-installed machine.
    try:
        with open(CONFIG, encoding="utf-8") as handle:
            config = json.load(handle)
    except (OSError, ValueError) as exc:
        sys.exit(f"could not read {CONFIG}: {exc}")
    if not isinstance(config, dict):
        sys.exit(f"{CONFIG} is not a config object")
    return config


def build_strip(width: int, height: int, label: str):
    """A marquee strip a few screens wide, with text and colour.

    Deliberately not plain white text on black: how long ``SetImage`` takes
    depends on how many subpixels are lit, so a strip that is mostly dark
    flatters the panel and hides exactly the regression this benchmark exists
    to catch.
    """
    from PIL import Image, ImageDraw, ImageFont

    from src.common.font_layout import load_truetype

    font = None
    for path, size in (
        (str(REPO / "assets/fonts/PressStart2P-Regular.ttf"), max(8, height // 4)),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf", max(10, height // 2)),
    ):
        try:
            font = load_truetype(path, size)
            break
        except OSError:
            continue
    if font is None:
        font = ImageFont.load_default()

    text = f"  {label}  ***  THE QUICK BROWN FOX JUMPS OVER THE LAZY DOG  ***  "
    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    box = probe.textbbox((0, 0), text, font=font)
    text_width = max(1, box[2] - box[0])
    text_height = box[3] - box[1]

    reps = max(2, (width * 4) // text_width + 1)
    strip = Image.new("RGB", (text_width * reps, height), (0, 0, 0))
    draw = ImageDraw.Draw(strip)
    draw.fontmode = "1"  # the panel has no partial brightness; see DisplayManager
    palette = [(255, 210, 60), (80, 200, 255), (255, 90, 90), (140, 255, 140)]
    for i in range(reps):
        left = i * text_width
        # A filled block per repeat, so a meaningful share of the strip is lit.
        draw.rectangle(
            [left + 4, height - 4, left + text_width - 4, height - 2],
            fill=palette[i % len(palette)],
        )
        draw.text((left, (height - text_height) // 2 - box[1]), text,
                  font=font, fill=palette[(i + 1) % len(palette)])
    return strip


class BackgroundLoad:
    """Threads that imitate plugins updating while the panel scrolls.

    Not a simulation of any particular plugin -- it is the shape of the work
    that competes with the render loop for the GIL: decoding JSON, resizing an
    image, compressing bytes. A render loop that only holds its pacing on an
    idle machine is not shippable, and this is how that shows up.
    """

    def __init__(self, workers: int) -> None:
        self.workers = max(0, workers)
        self._stop = threading.Event()
        self._threads: list = []

    def __enter__(self) -> "BackgroundLoad":
        for index in range(self.workers):
            thread = threading.Thread(
                target=self._run, args=(index,), name=f"bench-load-{index}", daemon=True)
            thread.start()
            self._threads.append(thread)
        return self

    def __exit__(self, *exc_info) -> None:
        self._stop.set()
        for thread in self._threads:
            thread.join(timeout=2.0)

    def _run(self, index: int) -> None:
        from PIL import Image

        payload = json.dumps({"games": [{"id": n, "score": [n, n + 1],
                                         "name": f"team {n}"} for n in range(200)]})
        image = Image.new("RGB", (256, 64), (12, 34, 56))
        while not self._stop.wait(0.25 + 0.05 * index):
            json.loads(payload)
            image.resize((128, 32), Image.LANCZOS)
            zlib.compress(image.tobytes(), 1)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seconds", type=float, default=DEFAULT_SECONDS,
                        help=f"how long to scroll for (default {DEFAULT_SECONDS:.0f}; "
                             "the shipping gate is 600)")
    parser.add_argument("--speed", type=float, default=None,
                        help="requested px/s; snapped to the nearest speed the "
                             "panel can show in whole pixels (default: one pixel "
                             "per refresh)")
    parser.add_argument("--hz", type=float, default=None,
                        help="skip the measurement and grade against this refresh "
                             "rate instead (for reproducing a rig's numbers)")
    parser.add_argument("--busy", type=int, default=0, metavar="N",
                        help="run N background workers imitating plugin updates")
    parser.add_argument("--max-missed", type=float,
                        default=frame_pacing.DEFAULT_MAX_MISSED_PERCENT,
                        metavar="PCT",
                        help="percent of frames allowed to slip a refresh "
                             f"(default {frame_pacing.DEFAULT_MAX_MISSED_PERCENT})")
    parser.add_argument("--json", dest="json_path", default=None, metavar="PATH",
                        help="also write the report as JSON, for comparing rigs")
    parser.add_argument("--label", default=None,
                        help="name for this run in the JSON report (default: hostname)")
    args = parser.parse_args(argv)

    # Everything the display service logs would otherwise land in the middle of
    # the report; the benchmark's own output is the point.
    logging.basicConfig(level=logging.ERROR, stream=sys.stderr)

    if hasattr(os, "geteuid") and os.geteuid() != 0:
        print("this needs root for GPIO access - rerun with sudo", file=sys.stderr)
        return 2

    config = load_config()

    from src.common.scroll_helper import ScrollHelper
    from src.display_manager import DisplayManager

    try:
        display = DisplayManager(config, suppress_test_pattern=True)
    except Exception as exc:
        print(f"could not open the display ({exc}).\n"
              "If the display service is running it owns the GPIO - stop it "
              "first:\n    sudo systemctl stop ledmatrix", file=sys.stderr)
        return 2

    if getattr(display, "matrix", None) is None:
        print("the display came up in fallback mode - there is no panel here to "
              "measure, and a software loop's frame times say nothing about "
              "vsync. Run this on a rig.", file=sys.stderr)
        return 2

    width, height = display.width, display.height

    if args.hz is not None:
        refresh_hz = float(args.hz)
        print(f"grading against {refresh_hz:.1f}Hz (given, not measured)")
    else:
        print(f"measuring the panel for {MEASURE_SECONDS:.0f}s...", flush=True)
        refresh_hz = frame_pacing.measure_refresh_hz(display.matrix, MEASURE_SECONDS)
        if refresh_hz <= 0:
            print("the panel did not answer a swap; cannot measure it",
                  file=sys.stderr)
            return 2
        cap = scroll_config.refresh_hz_from_config(config)
        note = (f" (cap is {cap:.0f}Hz)" if refresh_hz < cap * 0.98
                else " (at its configured cap)")
        print(f"panel refreshes at {refresh_hz:.1f}Hz{note}")

    requested = args.speed if args.speed else refresh_hz

    # Configured through the shared resolver rather than by setting the helper
    # up by hand, so the benchmark measures the engine every ticker runs on. A
    # speed the bench reached some other way would be measuring something no
    # plugin does.
    helper = ScrollHelper(width, height)
    settings = scroll_config.configure(
        helper,
        plugin_config={"scroll_pixels_per_second": requested},
        global_config=config,
        refresh_hz=refresh_hz,
        display_manager=display,
    )
    choice = settings.crisp
    if choice is None:
        print("the resolver did not snap to a whole-pixel speed; nothing to "
              "grade against", file=sys.stderr)
        return 2
    print(f"asked for {requested:.1f} px/s -> {choice.describe()}")

    helper.set_sub_pixel_scrolling(False)
    helper.set_scrolling_image(
        build_strip(width, height, f"{choice.pixels_per_second:.0f} px/s"))

    print(f"scrolling {width}x{height} for {args.seconds:.0f}s"
          + (f" with {args.busy} background worker(s)" if args.busy else "")
          + " ...", flush=True)

    intervals: list = []
    duplicates = 0
    blanks = 0
    restarts = 0
    last_column = None
    started = time.perf_counter()
    previous = None
    try:
        with BackgroundLoad(args.busy):
            while time.perf_counter() - started < args.seconds:
                helper.update_scroll_position()
                if helper.is_scroll_complete():
                    # The helper parks at the end of the strip and stops
                    # advancing, exactly as it does under a plugin -- which
                    # then hands over to the next one. Here there is nothing
                    # to hand over to, so start the strip again. Without this
                    # the benchmark measures a still image for the rest of the
                    # run and reports a smoothness it never demonstrated.
                    helper.reset_scroll()
                    restarts += 1
                visible = helper.get_visible_portion()
                column = int(helper.scroll_position)
                if column == last_column:
                    duplicates += 1
                last_column = column
                if visible is None:
                    blanks += 1
                else:
                    display.image.paste(visible, (0, 0))
                # Every frame, not once before the loop. The scrolling state
                # expires on its own inactivity threshold and takes the frame
                # hold with it, so a scroll that announces itself once is
                # presented at the wrong rate for all but its first moments --
                # and its unchanged frames start taking the dirty-tracking
                # skip, which returns without waiting for the panel at all.
                # Every ticker re-announces per frame; so does this.
                display.set_scrolling_state(True, frame_hold=choice.frame_hold)
                display.update_display()
                now = time.perf_counter()
                if previous is not None:
                    intervals.append(now - previous)
                previous = now
    except KeyboardInterrupt:
        print("\ninterrupted - reporting what was measured so far")
    finally:
        elapsed = time.perf_counter() - started
        display.set_scrolling_state(False)
        try:
            display.clear()
        except Exception:
            pass

    # The panel does not refresh at its idle rate while the Pi is also pushing
    # frames into it; see frame_pacing.refresh_from_intervals. Grading against
    # the idle number reports misses a locked loop never had, so the rate the
    # panel actually held during the scroll is read back from the frames.
    idle_hz = refresh_hz
    loaded_hz = frame_pacing.refresh_from_intervals(intervals, choice.frame_hold)
    graded_hz = loaded_hz if 0 < loaded_hz <= idle_hz * 1.02 else idle_hz

    report = frame_pacing.analyze(intervals, graded_hz, choice.frame_hold,
                                  seconds=elapsed)
    print()
    if loaded_hz > 0:
        drop = 100.0 * (idle_hz - loaded_hz) / idle_hz
        print(f"panel held {loaded_hz:.1f}Hz while rendering "
              f"({drop:.1f}% below its {idle_hz:.1f}Hz idle rate)")
    print(report.describe(args.max_missed))
    if duplicates:
        # A frame that shows the same columns as the one before it is work the
        # panel did not need. It is not a miss -- the frame arrived on time --
        # but it means the loop is presenting faster than the strip is moving.
        print(f"  duplicate   {duplicates} frames advanced no pixels "
              f"({100.0 * duplicates / max(1, len(intervals)):.2f}%)")
    if blanks:
        print(f"  blank       {blanks} frames had no visible slice to draw")
    if restarts:
        print(f"  restarts    {restarts} (the strip was scrolled through "
              f"{restarts} time{'s' if restarts != 1 else ''})")

    if args.json_path:
        payload = report.as_dict()
        payload.update({
            "label": args.label or os.uname().nodename,
            "width": width,
            "height": height,
            "requested_pixels_per_second": requested,
            "pixels_per_second": choice.pixels_per_second,
            "pixels_per_frame": choice.pixels_per_frame,
            "busy_workers": args.busy,
            "duplicate_frames": duplicates,
            "blank_frames": blanks,
            "strip_restarts": restarts,
            "idle_refresh_hz": idle_hz,
            "loaded_refresh_hz": loaded_hz,
            "max_missed_percent": args.max_missed,
            "passed": report.passed(args.max_missed),
        })
        Path(args.json_path).write_text(json.dumps(payload, indent=2) + "\n",
                                        encoding="utf-8")
        print(f"\nwrote {args.json_path}")

    return 0 if report.passed(args.max_missed) else 1


if __name__ == "__main__":
    sys.exit(main())
