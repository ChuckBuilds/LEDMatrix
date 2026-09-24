#!/usr/bin/env python3
"""Soak a running display and report how often moving frames reached the panel late.

Runs NEXT TO the display service, as any user: it only reads the stats file the
service writes (src/common/frame_timing.py) at the start and end of the run and
reports the difference. Nothing is stopped, restarted or drawn.

    # 10 minutes, as the display is now
    python3 scripts/frame_soak.py

    # the same with the web preview open (the preview's PNG encodes are one of
    # the things that used to make the render loop miss refreshes)
    python3 scripts/frame_soak.py --preview

    # quick look at the totals since the service started
    python3 scripts/frame_soak.py --show

    # keep the report for a before/after comparison
    python3 scripts/frame_soak.py --duration 600 --json soak-before.json

Exit status: 0 when the late-frame rate is within ``--max-late-pct``, 1 when it
is not, 2 when there was nothing to measure (no stats file, the service
restarted mid-run, or nothing scrolled).

What the numbers mean
---------------------
late frames     frames that reached the panel one or more refreshes after they
                were due -- the panel showed the previous frame again, which on
                a moving strip is a visible hitch. This is the pass/fail number.
freezes         gaps of 250ms+ inside a scroll: recomposes, plugin handovers,
                blocking calls on the render thread. Reported, not failed on,
                since some are handovers between plugins rather than faults.
blit            copying the frame into the matrix canvas (rgbmatrix SetImage).
                Grows with width x height x pwm_bits.
wait            blocked in SwapOnVSync, i.e. slack before the refresh.
work            everything else between two frames: drawing, scrolling, and
                waiting for the GIL.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.common.frame_timing import (  # noqa: E402
    BUCKET_COUNT,
    SCHEMA_VERSION,
    default_stats_path,
)

#: Touched by the web UI while someone has the preview open; a fresh marker
#: puts the display service's snapshot writer at full rate. Same path as
#: DisplayManager._viewer_marker_path.
VIEWER_MARKER = "/tmp/led_matrix_preview_viewer"  # nosec B108 - fixed path shared with the service

#: A stats file not rewritten for this long means nothing is being presented.
STALE_SECONDS = 30.0


def load(path: str) -> Optional[Dict[str, Any]]:
    try:
        with open(path, encoding="utf-8") as handle:
            stats = json.load(handle)
    except (OSError, ValueError):
        return None
    if not isinstance(stats, dict) or stats.get("version") != SCHEMA_VERSION:
        return None
    return stats


def _histogram(stats: Dict[str, Any], name: str) -> Dict[int, int]:
    raw = (stats.get("histograms") or {}).get(name) or {}
    return {int(k): int(v) for k, v in raw.items()}


def diff(before: Dict[str, Any], after: Dict[str, Any]) -> Dict[str, Any]:
    """What happened between two snapshots of the same process."""
    tb, ta = before["totals"], after["totals"]
    totals = {}
    for key, value in ta.items():
        if isinstance(value, dict):
            totals[key] = {k: v - tb.get(key, {}).get(k, 0)
                           for k, v in value.items()}
        elif key == "worst_interval_ms":
            # A running maximum can't be differenced; it is reported as the
            # worst since the service started.
            totals[key] = value
        else:
            totals[key] = value - tb.get(key, 0)
    histograms = {}
    for name in (after.get("histograms") or {}):
        hb, ha = _histogram(before, name), _histogram(after, name)
        histograms[name] = {k: v - hb.get(k, 0) for k, v in ha.items()
                            if v - hb.get(k, 0) > 0}
    return {"totals": totals, "histograms": histograms,
            "seconds": after["updated"] - before["updated"]}


def percentiles(histogram: Dict[int, int], bucket_ms: float) -> Dict[str, Any]:
    """p50/p95/p99/max from a sparse histogram, as each bucket's upper edge."""
    count = sum(histogram.values())
    if not count:
        return {}
    out = {}
    targets = {"p50": 0.50, "p95": 0.95, "p99": 0.99}
    running = 0
    for index in sorted(histogram):
        running += histogram[index]
        for name, fraction in list(targets.items()):
            if running >= fraction * count:
                out[name] = _edge(index, bucket_ms)
                del targets[name]
    out["max"] = _edge(max(histogram), bucket_ms)
    return out


def _edge(index: int, bucket_ms: float):
    if index >= BUCKET_COUNT - 1:
        return f">={index * bucket_ms:g}"
    return round((index + 1) * bucket_ms, 2)


def build_report(before, after, preview: bool) -> Dict[str, Any]:
    delta = diff(before, after)
    totals = delta["totals"]
    frames = totals["scroll_frames"]
    hours = delta["seconds"] / 3600.0 if delta["seconds"] > 0 else 0.0
    bucket_ms = after.get("bucket_ms", 0.25)
    return {
        "seconds": round(delta["seconds"], 1),
        "preview": preview,
        "info": after.get("info"),
        "binding_releases_gil": after.get("binding_releases_gil"),
        "measured_refresh_hz": after.get("measured_refresh_hz"),
        "scroll_frames": frames,
        "static_frames": totals["static_frames"],
        "late_frames": totals["late_frames"],
        "late_pct": round(100.0 * totals["late_frames"] / frames, 3) if frames else None,
        "missed_refreshes": totals["missed_refreshes"],
        "late_by": totals["late_by"],
        "early_frames": totals.get("early_frames", 0),
        "early_pct": (round(100.0 * totals.get("early_frames", 0) / frames, 3)
                      if frames else None),
        "freeze_by": totals.get("freeze_by", {}),
        "freezes": totals["freezes"],
        "freezes_per_hour": round(totals["freezes"] / hours, 1) if hours else None,
        "freeze_seconds": round(totals["freeze_seconds"], 2),
        "worst_interval_ms": (round(totals["worst_interval_ms"], 1)
                              if totals["worst_interval_ms"] else None),
        "timing_ms": {name: percentiles(h, bucket_ms)
                      for name, h in delta["histograms"].items()},
    }


def print_report(report: Dict[str, Any], limit: float) -> None:
    info = report.get("info") or {}
    size = "{}x{}".format(
        (info.get("cols") or 0) * (info.get("chain_length") or 1),
        (info.get("rows") or 0) * (info.get("parallel") or 1))
    gil = {True: "releases the GIL", False: "STOCK (holds the GIL in SwapOnVSync)",
           None: "unknown"}[report.get("binding_releases_gil")]
    print(f"Rig       {info.get('pi_model') or 'unknown'}")
    print(f"Panel     {size}  chain {info.get('chain_length')} x parallel "
          f"{info.get('parallel')}  pwm_bits {info.get('pwm_bits')}  "
          f"slowdown {info.get('gpio_slowdown')}  mapping {info.get('hardware_mapping')}")
    print(f"Refresh   {report.get('measured_refresh_hz') or '?'} Hz measured, "
          f"cap {info.get('limit_refresh_rate_hz')}")
    print(f"Binding   {gil}")
    print(f"Run       {report['seconds']:.0f}s, preview "
          f"{'open (simulated)' if report['preview'] else 'as-is'}")
    print()
    frames = report["scroll_frames"]
    print(f"Scrolling frames   {frames}")
    if frames:
        late_by = report["late_by"]
        print(f"Late frames        {report['late_frames']} ({report['late_pct']}%)"
              f"  missed refreshes {report['missed_refreshes']}"
              f"  [by 1: {late_by['1']}, 2: {late_by['2']}, "
              f"3-5: {late_by['3-5']}, 6+: {late_by['6+']}]")
        if report["early_frames"]:
            print(f"Early frames       {report['early_frames']} "
                  f"({report['early_pct']}%)  swaps returned a refresh early")
    print(f"Freezes >=250ms    {report['freezes']}"
          f" ({report['freezes_per_hour']}/h, {report['freeze_seconds']}s total)"
          f"  worst gap since start {report['worst_interval_ms'] or '-'} ms")
    if report["freezes"]:
        print("                   by length: " + ", ".join(
            f"{k}: {v}" for k, v in report["freeze_by"].items()))
    print()
    print(f"{'ms':<18}{'p50':>8}{'p95':>8}{'p99':>8}{'max':>8}")
    for name in ("blit", "wait", "work", "interval_per_hold"):
        row = report["timing_ms"].get(name) or {}
        print(f"{name:<18}" + "".join(f"{str(row.get(k, '-')):>8}"
                                        for k in ("p50", "p95", "p99", "max")))
    print()
    if report["late_pct"] is None:
        print("RESULT  nothing scrolled - no verdict")
    elif not locked(report, limit):
        print(f"RESULT  FAIL  NOT LOCKED: {report['early_pct']}% of frames came a "
              "refresh early, so the swaps were not waiting for the panel and "
              "the late count means nothing")
    elif report["late_pct"] <= limit:
        print(f"RESULT  PASS  {report['late_pct']}% late <= {limit}%")
    else:
        print(f"RESULT  FAIL  {report['late_pct']}% late > {limit}%")


def locked(report: Dict[str, Any], limit: float) -> bool:
    """Whether the loop was paced by the panel at all."""
    return (report.get("early_pct") or 0.0) <= limit


def passed(report: Dict[str, Any], limit: float) -> bool:
    return (report["late_pct"] is not None and locked(report, limit)
            and report["late_pct"] <= limit)


def touch_marker() -> bool:
    try:
        with open(VIEWER_MARKER, "a"):
            pass
        os.utime(VIEWER_MARKER, None)
        return True
    except OSError:
        return False


def wait_for_fresh(path: str, timeout: float) -> Optional[Dict[str, Any]]:
    """The first snapshot written after now, so both ends of the run are exact."""
    first = load(path)
    deadline = time.time() + timeout
    while time.time() < deadline:
        current = load(path)
        if current and (first is None or current["updated"] != first["updated"]):
            return current
        time.sleep(0.5)
    return None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--duration", type=float, default=600.0,
                        help="seconds to soak (default 600)")
    parser.add_argument("--preview", action="store_true",
                        help="keep the web-preview viewer marker fresh, as an "
                             "open preview tab does")
    parser.add_argument("--max-late-pct", type=float, default=0.1,
                        help="fail above this percentage of late frames (default 0.1)")
    parser.add_argument("--stats", default=default_stats_path(),
                        help="stats file written by the display service")
    parser.add_argument("--json", metavar="PATH",
                        help="also write the report as JSON")
    parser.add_argument("--show", action="store_true",
                        help="print totals since the service started and exit")
    args = parser.parse_args(argv)

    current = load(args.stats)
    if current is None:
        print(f"No frame stats at {args.stats}. Is the display service running a "
              "build with frame timing, and has anything scrolled for ~10s?",
              file=sys.stderr)
        return 2
    if time.time() - current["updated"] > STALE_SECONDS:
        print(f"Frame stats are {time.time() - current['updated']:.0f}s old: nothing "
              "has been presented recently (static screen, or the service stopped).",
              file=sys.stderr)
        if not args.show:
            return 2

    if args.show:
        empty = json.loads(json.dumps(current))
        for key, value in empty["totals"].items():
            empty["totals"][key] = ({k: 0 for k in value} if isinstance(value, dict)
                                    else 0)
        empty["histograms"] = {}
        empty["updated"] = current["started"]
        report = build_report(empty, current, preview=False)
        print_report(report, args.max_late_pct)
        return 0

    if args.preview and not touch_marker():
        print(f"Cannot touch {VIEWER_MARKER}; run as the web service's user to "
              "simulate an open preview.", file=sys.stderr)
        return 2

    print(f"Waiting for a fresh baseline from {args.stats} ...", flush=True)
    before = wait_for_fresh(args.stats, timeout=60.0)
    if before is None:
        print("The stats file stopped updating.", file=sys.stderr)
        return 2

    end = time.time() + args.duration
    next_progress = time.time() + 60.0
    while time.time() < end:
        if args.preview:
            touch_marker()
        time.sleep(1.0)
        if time.time() >= next_progress:
            now = load(args.stats)
            if now and now.get("pid") == before["pid"]:
                done = now["totals"]["scroll_frames"] - before["totals"]["scroll_frames"]
                late = now["totals"]["late_frames"] - before["totals"]["late_frames"]
                print(f"  {int(end - time.time())}s left: {done} scrolling frames, "
                      f"{late} late", flush=True)
            next_progress += 60.0

    after = wait_for_fresh(args.stats, timeout=60.0)
    if after is None:
        print("The stats file stopped updating during the run.", file=sys.stderr)
        return 2
    if after.get("pid") != before.get("pid"):
        print("The display service restarted during the run; results discarded.",
              file=sys.stderr)
        return 2

    report = build_report(before, after, preview=args.preview)
    print()
    print_report(report, args.max_late_pct)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2)
    if report["late_pct"] is None:
        return 2
    return 0 if passed(report, args.max_late_pct) else 1


if __name__ == "__main__":
    sys.exit(main())
