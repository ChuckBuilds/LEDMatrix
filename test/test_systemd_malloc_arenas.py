"""The display unit must cap glibc's malloc arenas.

glibc hands each allocating thread its own malloc arena, up to 8 x CPU count,
and an arena that has grown is never returned to the OS. This process runs
threads for the render loop, the update workers and the background fetchers, so
on a 3-core Pi the ceiling is 24 arenas.

Measured on a live rig, 2.5 hours in:

    RSS                 1030 MB
    Private_Dirty        988 MB
    anonymous mappings > 10 MB     23     (ceiling is 8 x 3 = 24)
    largest few          104, 79, 66, 63, 63 MB, on 64 MB-aligned addresses

against live data that accounts for perhaps 15 MB -- the widest scroll strip
observed was 35,746 x 64, about 7 MB as RGB and the same again for its numpy
mirror. Repeated sampling showed RSS flat between 990 and 1030 MB rather than
climbing, so this is arena bloat rather than a leak: memory Python has freed
but glibc is holding per-arena.

The device had 59 MB free at the time.

Capping the arena count trades a little allocator concurrency for that resident
memory. The render loop is latency-sensitive, so if p99 frame time regresses the
right response is to raise this rather than remove it.
"""
import re
from pathlib import Path

import pytest

UNIT = (Path(__file__).resolve().parent.parent / "systemd" / "ledmatrix.service")

#: The value the unit is expected to carry. 2 is the usual choice for a
#: threaded Python process; 1-4 all keep some of the saving, but only one of
#: them is what this project ships.
EXPECTED_ARENA_MAX = 2


def _environment(unit_text):
    return dict(
        line.split("=", 2)[1:3] if line.count("=") >= 2 else (line.split("=", 1)[1], "")
        for line in unit_text.splitlines()
        if line.startswith("Environment=")
    )


def test_the_unit_exists():
    assert UNIT.is_file(), f"{UNIT} is missing"


def test_malloc_arena_max_is_capped():
    env = _environment(UNIT.read_text(encoding="utf-8"))
    assert "MALLOC_ARENA_MAX" in env, (
        "the display unit does not cap glibc arenas; on a 3-core Pi the default "
        "ceiling is 24 and a measured rig held 23 of them, 920 MB"
    )
    value = int(env["MALLOC_ARENA_MAX"])
    # Pinned, not a range. A range let a change to 4 -- which hands most of the
    # saving back -- pass unnoticed, which was the point of the finding that
    # prompted this. Raising it is a legitimate response to a frame-time
    # regression, but it should be a visible edit here rather than a silent
    # drift, so the number lives in one place and changing it shows up in
    # review.
    assert value == EXPECTED_ARENA_MAX, (
        f"MALLOC_ARENA_MAX={value}, expected {EXPECTED_ARENA_MAX}. If this was "
        "raised deliberately because frame times regressed, update "
        "EXPECTED_ARENA_MAX here and say so in the commit."
    )


def test_the_reason_is_recorded_next_to_it():
    """A bare tuning knob invites removal by whoever meets it next."""
    text = UNIT.read_text(encoding="utf-8")
    index = text.index("Environment=MALLOC_ARENA_MAX")
    preamble = text[:index].splitlines()[-12:]
    comment = "\n".join(line for line in preamble if line.startswith("#"))
    assert "arena" in comment.lower(), "no explanation precedes the setting"
    assert re.search(r"\d", comment), (
        "the explanation cites no measurement, so a reader cannot tell whether "
        "it still applies to their hardware"
    )


@pytest.mark.parametrize("unit", ["ledmatrix.service"])
def test_the_unit_still_parses_as_ini(unit):
    """systemd will refuse a malformed unit, and the panel stays dark."""
    import configparser

    path = UNIT.parent / unit
    parser = configparser.ConfigParser(strict=False)
    # systemd allows repeated keys; ConfigParser needs them merged, not rejected.
    parser.read_string(path.read_text(encoding="utf-8"))
    assert parser.has_section("Service")
    assert parser.has_option("Service", "ExecStart")
