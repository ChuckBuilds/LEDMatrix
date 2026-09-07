"""Frame pacing and FPS health reporting must not depend on the wall clock.

These devices have no RTC, so the system clock jumps by however wrong boot
time was the moment NTP first syncs. The render loop sleeps the *remainder*
of each frame budget:

    frame_elapsed = <now> - frame_started
    time.sleep(max(0.0, frame_interval - frame_elapsed))

With a wall-clock `now`, a backward jump makes frame_elapsed negative, so
`frame_interval - frame_elapsed` exceeds the whole budget and the render loop
stalls for the size of the correction. A forward jump instead inflates the
p99 and worst-frame numbers the telemetry reports.
"""
import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

COORD = (Path(__file__).resolve().parent.parent
         / "src" / "vegas_mode" / "coordinator.py")
TREE = ast.parse(COORD.read_text(encoding="utf-8"))


def _assignments_of(name):
    """Every `name = <expr>` in the module, as unparsed source."""
    out = []
    for node in ast.walk(TREE):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    out.append((node.lineno, ast.unparse(node.value)))
    return out


def test_per_frame_timestamps_are_monotonic():
    for name in ("frame_started", "frame_elapsed"):
        assigns = _assignments_of(name)
        assert assigns, f"{name} is no longer assigned -- has the loop changed?"
        for lineno, expr in assigns:
            assert "time.time()" not in expr, (
                f"{name} at line {lineno} uses the wall clock ({expr!r}). A "
                "backward NTP step makes the per-frame delta negative and the "
                "loop then sleeps longer than the whole frame budget.")
            assert "time.monotonic()" in expr, (
                f"{name} at line {lineno} is {expr!r}, expected monotonic")


def test_the_fps_window_is_monotonic():
    for lineno, expr in _assignments_of("current_time"):
        assert "time.monotonic()" in expr, (
            f"current_time at line {lineno} is {expr!r}; fps is frames divided "
            "by this delta, so a clock step would corrupt the rate itself")


def test_health_state_is_not_reset_every_iteration():
    """run_iteration() runs once per cycle -- locals here reset every few seconds.

    As locals, `last_fps_health_log = 0.0` made the 300s heartbeat fire on the
    first sample of every iteration, and a recovery spanning two iterations was
    never reported because was_degraded had already gone back to False.
    """
    run_iteration = next(
        (n for n in ast.walk(TREE)
         if isinstance(n, ast.FunctionDef) and n.name == "run_iteration"), None)
    assert run_iteration is not None, "run_iteration() not found"

    local_names = {t.id for n in ast.walk(run_iteration)
                   if isinstance(n, ast.Assign)
                   for t in n.targets if isinstance(t, ast.Name)}
    for leaked in ("last_fps_health_log", "was_degraded"):
        assert leaked not in local_names, (
            f"{leaked} is a local of run_iteration() again, so it resets every "
            "cycle -- the heartbeat degenerates to once per iteration")

    body = ast.unparse(run_iteration)
    assert "self._fps_last_health_log" in body and "self._fps_was_degraded" in body, (
        "the health state should live on the coordinator, across iterations")


def test_start_clears_stale_health_state():
    """A new run must not inherit "was degraded" from the previous one."""
    start = next((n for n in ast.walk(TREE)
                  if isinstance(n, ast.FunctionDef) and n.name == "start"), None)
    assert start is not None, "start() not found"
    body = ast.unparse(start)
    assert "self._fps_last_health_log" in body and "self._fps_was_degraded" in body, (
        "start() does not reset the FPS health state")


def test_the_degraded_threshold_is_documented():
    """The 90% band is deliberate; say so where the constant is defined."""
    source = COORD.read_text(encoding="utf-8")
    idx = source.index("_FPS_HEALTHY_FRACTION = ")
    preamble = source[max(0, idx - 700):idx]
    assert "90%" in preamble or "0.9" in preamble, (
        "the degradation threshold is not explained at its definition, so "
        "'below target' reads as a bug rather than a deliberate band")
