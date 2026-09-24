"""vegas_scroll.switch_interval_ms: applied for a Vegas run, restored after."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.vegas_mode.config import VegasModeConfig  # noqa: E402
from src.vegas_mode.coordinator import VegasModeCoordinator  # noqa: E402


def _coordinator(ms):
    c = VegasModeCoordinator.__new__(VegasModeCoordinator)
    c.vegas_config = VegasModeConfig(switch_interval_ms=ms)
    return c


def test_applied_for_the_run_and_restored_after():
    before = sys.getswitchinterval()
    c = _coordinator(1.0)
    try:
        c._apply_switch_interval()
        assert abs(sys.getswitchinterval() - 0.001) < 1e-9
        c._apply_switch_interval()      # a second start must not lose the original
    finally:
        c._restore_switch_interval()
    assert sys.getswitchinterval() == before


def test_zero_leaves_the_interpreter_alone():
    before = sys.getswitchinterval()
    c = _coordinator(0.0)
    c._apply_switch_interval()
    c._restore_switch_interval()
    assert sys.getswitchinterval() == before


def test_read_from_config():
    config = VegasModeConfig.from_config(
        {"display": {"vegas_scroll": {"switch_interval_ms": 1}}})
    assert config.switch_interval_ms == 1.0
    assert VegasModeConfig.from_config({"display": {"vegas_scroll": {}}}).switch_interval_ms == 0.0
