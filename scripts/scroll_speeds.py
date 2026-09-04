#!/usr/bin/env python3
"""Show and try the scroll speeds your panel can display cleanly.

Motion looks smooth when the strip advances a WHOLE number of pixels per panel
refresh. Anything else has to blend two columns (which on pixel-font text reads
as shimmer) or repeat frames unevenly (which reads as judder). So the speeds
worth using are not arbitrary -- they are

    refresh_hz / frame_hold * pixels_per_frame

for whole numbers of frame_hold and pixels_per_frame, and that ladder depends
on how fast YOUR panel actually refreshes. A Pi Zero driving a big chain will
have a completely different set of good speeds from a Pi 4 driving a small one.

    # what can this panel do? (no hardware needed, uses your configured rate)
    python3 scripts/scroll_speeds.py

    # measure what the panel ACTUALLY manages, rather than what is configured
    sudo systemctl stop ledmatrix
    sudo python3 scripts/scroll_speeds.py --measure
    sudo systemctl start ledmatrix

    # what would a 60Hz panel offer?
    python3 scripts/scroll_speeds.py --hz 60

    # try one on the panel
    sudo systemctl stop ledmatrix
    sudo python3 scripts/scroll_speeds.py --demo 50
    sudo systemctl start ledmatrix

This script never starts or stops the display service itself -- that is left to
you, so a crash here can never leave the panel dark.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.common import scroll_config  # noqa: E402

CONFIG = Path(__file__).resolve().parent.parent / "config" / "config.json"


def load_hardware():
    try:
        with open(CONFIG, encoding="utf-8") as handle:
            return (json.load(handle).get("display") or {}).get("hardware") or {}
    except (OSError, ValueError):
        return {}


def build_options(hardware, refresh_override=None):
    from rgbmatrix import RGBMatrixOptions

    o = RGBMatrixOptions()
    o.rows = int(hardware.get("rows", 32))
    o.cols = int(hardware.get("cols", 64))
    o.chain_length = int(hardware.get("chain_length", 1))
    o.parallel = int(hardware.get("parallel", 1))
    o.brightness = int(hardware.get("brightness", 80))
    o.hardware_mapping = hardware.get("hardware_mapping", "regular")
    o.pwm_bits = int(hardware.get("pwm_bits", 11))
    o.pwm_dither_bits = int(hardware.get("pwm_dither_bits", 0))
    o.pwm_lsb_nanoseconds = int(hardware.get("pwm_lsb_nanoseconds", 130))
    o.led_rgb_sequence = hardware.get("led_rgb_sequence", "RGB")
    o.scan_mode = int(hardware.get("scan_mode", 0))
    o.row_address_type = int(hardware.get("row_address_type", 0))
    o.multiplexing = int(hardware.get("multiplexing", 0))
    o.gpio_slowdown = int(hardware.get("gpio_slowdown", 2))
    o.limit_refresh_rate_hz = (
        int(refresh_override) if refresh_override is not None
        else int(hardware.get("limit_refresh_rate_hz", 0))
    )
    return o


def open_matrix(hardware, refresh_override=None):
    """Construct the matrix, or explain why it will not open."""
    if os.geteuid() != 0:
        sys.exit("this needs root for GPIO access - rerun with sudo")
    try:
        from rgbmatrix import RGBMatrix
    except ImportError:
        sys.exit("rgbmatrix is not installed on this machine")
    try:
        return RGBMatrix(options=build_options(hardware, refresh_override))
    except Exception as exc:  # pragma: no cover - hardware dependent
        sys.exit(
            "could not open the panel ({}).\n"
            "If the display service is running it owns the GPIO - stop it first:\n"
            "    sudo systemctl stop ledmatrix".format(exc)
        )


def measure_refresh(hardware, seconds=6.0):
    """Actual refresh rate, by running uncapped and timing the swaps.

    SwapOnVSync blocks until the panel's next refresh, so an unthrottled loop
    runs at exactly the panel's rate. This is what an older Pi or a longer
    chain will really give you, as opposed to whatever limit_refresh_rate_hz
    optimistically asks for.
    """
    matrix = open_matrix(hardware, refresh_override=0)
    canvas = matrix.CreateFrameCanvas()
    canvas = matrix.SwapOnVSync(canvas)  # discard the first, it includes setup
    frames = 0
    started = time.perf_counter()
    while time.perf_counter() - started < seconds:
        canvas = matrix.SwapOnVSync(canvas)
        frames += 1
    measured = frames / (time.perf_counter() - started)
    matrix.Clear()
    return measured


def demo(hardware, target, seconds):
    """Scroll text at the crisp speed nearest `target`."""
    from PIL import Image, ImageDraw, ImageFont

    hz = float(hardware.get("limit_refresh_rate_hz") or scroll_config.DEFAULT_REFRESH_HZ)
    choice = scroll_config.solve_crisp(target, hz)
    print("asked for {:.0f} px/s -> {}".format(target, choice.describe()))

    matrix = open_matrix(hardware)
    canvas = matrix.CreateFrameCanvas()
    W, H = canvas.width, canvas.height

    font = None
    for path, size in (
        (str(Path(__file__).resolve().parent.parent / "assets/fonts/PressStart2P-Regular.ttf"), 16),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf", 26),
    ):
        try:
            font = ImageFont.truetype(path, size)
            break
        except OSError:
            continue
    if font is None:
        font = ImageFont.load_default()

    text = "  {:.0f} px/s  ***  THE QUICK BROWN FOX JUMPS OVER THE LAZY DOG  ***".format(
        choice.pixels_per_second)
    box = ImageDraw.Draw(Image.new("RGB", (8, 8))).textbbox((0, 0), text, font=font)
    tw, th = box[2] - box[0], box[3] - box[1]
    reps = max(2, (W * 3) // max(tw, 1) + 1)
    strip = Image.new("RGB", (tw * reps, H), (0, 0, 0))
    draw = ImageDraw.Draw(strip)
    for i in range(reps):
        draw.text((i * tw, (H - th) // 2 - box[1]), text, font=font, fill=(255, 210, 60))

    offset = 0
    frames = 0
    started = time.time()
    while time.time() - started < seconds:
        window = strip.crop((offset, 0, offset + W, H))
        if window.width < W:
            whole = Image.new("RGB", (W, H), (0, 0, 0))
            head = strip.crop((offset, 0, strip.width, H))
            whole.paste(head, (0, 0))
            whole.paste(strip.crop((0, 0, W - head.width, H)), (head.width, 0))
            window = whole
        canvas.SetImage(window)
        canvas = matrix.SwapOnVSync(canvas, choice.frame_hold)
        offset = (offset + choice.pixels_per_frame) % strip.width
        frames += 1
    elapsed = time.time() - started
    print("  {} frames in {:.1f}s = {:.1f} fps = {:.1f} px/s actual".format(
        frames, elapsed, frames / elapsed, frames * choice.pixels_per_frame / elapsed))
    matrix.Clear()


def print_ladder(hz, highlight=None):
    print("")
    print("Whole-pixel scroll speeds at {:.1f}Hz refresh".format(hz))
    print("(the panel refreshes at {:.0f}Hz for every one of these - holding a "
          "frame costs no flicker)".format(hz))
    print("")
    for entry in scroll_config.crisp_ladder(hz):
        if entry.pixels_per_second > hz * 3:
            break
        mark = " <-- nearest to {:.0f}".format(highlight) if (
            highlight is not None
            and entry.pixels_per_second == scroll_config.solve_crisp(highlight, hz).pixels_per_second
        ) else ""
        print("  " + entry.describe() + mark)
    print("")
    print("Set one in config.json as pixels per second, e.g.")
    print('  "display_options": {{"scroll_pixels_per_second": {:.0f}}}'.format(
        scroll_config.solve_crisp(highlight if highlight else hz / 2, hz).pixels_per_second))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--hz", type=float,
                    help="refresh rate to compute the ladder for (default: your config)")
    ap.add_argument("--measure", action="store_true",
                    help="measure the panel's real refresh rate (needs root, service stopped)")
    ap.add_argument("--demo", type=float, metavar="PXPS",
                    help="scroll text at the crisp speed nearest this (needs root)")
    ap.add_argument("--seconds", type=float, default=15.0, help="demo duration")
    ap.add_argument("--want", type=float, metavar="PXPS",
                    help="highlight the entry nearest this speed")
    args = ap.parse_args()

    hardware = load_hardware()
    configured = float(hardware.get("limit_refresh_rate_hz") or 0)

    if args.demo is not None:
        demo(hardware, args.demo, args.seconds)
        return

    if args.measure:
        measured = measure_refresh(hardware)
        print("measured panel refresh: {:.1f}Hz".format(measured))
        if configured:
            print("configured limit_refresh_rate_hz: {:.0f}".format(configured))
            if measured < configured * 0.95:
                print("  -> the panel cannot reach the configured rate; the ladder")
                print("     below uses what it actually manages")
        print_ladder(measured, args.want)
        return

    hz = args.hz or configured or scroll_config.DEFAULT_REFRESH_HZ
    if not args.hz and not configured:
        print("no limit_refresh_rate_hz in config; assuming {:.0f}Hz".format(hz))
        print("run with --measure to find your panel's real rate")
    print_ladder(hz, args.want)


if __name__ == "__main__":
    main()
