"""Live Vegas elements end to end: a real plugin through the real ticker.

The stub fixture plugin (test/fixtures/plugins/vegas-live-stub) is loaded by
the real plugin loader onto a real DisplayManager (RGBMatrixEmulator) and a
real PluginManager, and the real coordinator runs it: the first compose uses
its ordinary Vegas content, the background prefetch asks it for elements, and
the strip records where each one landed.
"""
import os
import sys
import time
from pathlib import Path

os.environ["EMULATOR"] = "true"

import numpy as np  # noqa: E402
import pytest  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.plugin_system.plugin_state import PluginState  # noqa: E402
from src.plugin_system.testing.harness import _instantiate  # noqa: E402
from src.plugin_system.testing.loading import build_full_config, load_harness_spec, load_manifest  # noqa: E402
from src.vegas_mode import elements  # noqa: E402

STUB = Path(__file__).resolve().parent / "fixtures" / "plugins" / "vegas-live-stub"
PID = "vegas-live-stub"


@pytest.fixture(scope="module")
def dm(tmp_path_factory):
    from src.display_manager import DisplayManager
    DisplayManager._instance = None
    DisplayManager._initialized = False
    manager = DisplayManager({
        "display": {
            "hardware": {"rows": 32, "cols": 64, "chain_length": 2,
                         "parallel": 1, "brightness": 90},
            "runtime": {"gpio_slowdown": 0},
        },
    }, suppress_test_pattern=True)
    manager._snapshot_path = str(
        tmp_path_factory.mktemp("live") / "led_matrix_preview.png")
    if manager.matrix is None:
        pytest.fail("DisplayManager fell back to matrix=None")
    yield manager
    DisplayManager._instance = None
    DisplayManager._initialized = False


@pytest.fixture
def ticker(dm, tmp_path):
    from src.plugin_system.plugin_manager import PluginManager
    from src.vegas_mode.coordinator import VegasModeCoordinator

    pm = PluginManager(plugins_dir=str(tmp_path), config_manager=None,
                       display_manager=dm, cache_manager=None)
    config = {**build_full_config(STUB, load_harness_spec(STUB), {}),
              "enabled": True, "map_hz": 4}
    plugin = _instantiate(PID, load_manifest(STUB), STUB, config, {}, dm)
    plugin.plugin_manager = pm
    pm.plugins[PID] = plugin
    pm.state_manager.set_state(PID, PluginState.ENABLED)

    coordinator = VegasModeCoordinator({"display": {"vegas_scroll": {
        "enabled": True, "continuous_scroll": True, "plugins_per_cycle": 1,
        "scroll_speed": 100, "lead_in_width": 0,
    }}}, dm, pm)
    yield coordinator, plugin, pm
    coordinator.stop()
    pm.stop_update_worker()


def _run_until(coordinator, predicate, seconds=10.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        coordinator.run_frame()
        if predicate():
            return True
        time.sleep(0.002)
    return False


def test_the_prefetched_stub_is_placed_as_live_elements(ticker):
    coordinator, plugin, _pm = ticker
    assert coordinator.start()
    assert coordinator.live_active
    pipeline = coordinator.render_pipeline
    # The first compose ran on this thread without the plugin's lock, so it
    # is plain content.
    assert pipeline.live_records() == ()

    assert _run_until(coordinator, lambda: pipeline.live_records())
    keys = [r.key for r in pipeline.live_records()]
    assert {"card:0", "card:5", "map"} <= set(keys)
    assert "sep" not in keys
    assert all(r.plugin_id == PID for r in pipeline.live_records())

    # Each record points at the element's pixels: a card is bordered in its
    # colour, with content_padding black columns either side.
    strip = pipeline.scroll_helper.cached_array
    pad = coordinator.vegas_config.content_padding
    for record in pipeline.live_records():
        x = record.abs_x - pipeline._strip_origin
        if x < 0:
            continue
        columns = strip[:, x:x + record.width]
        assert not columns[:, :pad].any() and not columns[:, -pad:].any()
        assert columns[:, pad:record.width - pad].any()


def test_an_update_moves_the_plugins_epoch_and_new_elements_carry_it(ticker):
    coordinator, plugin, pm = ticker
    assert coordinator.start()
    before = coordinator.live_epochs.get(PID)
    pm._note_update_completed(PID)
    epoch = coordinator.live_epochs.get(PID)
    assert epoch > before
    coordinator.plugin_adapter.invalidate_cache(PID)
    images = coordinator.plugin_adapter.get_content(plugin, PID, offscreen_only=True)
    metas = [elements.meta_of(img) for img in images if elements.meta_of(img)]
    assert metas and all(m.epoch == epoch for m in metas)


def _columns(pipeline, record):
    x = record.abs_x - pipeline._strip_origin
    return pipeline.scroll_helper.cached_array[:, max(0, x):x + record.width].copy()


def _upcoming(pipeline, key_prefix):
    """A live record of this kind that is not yet behind the viewport."""
    left = pipeline._strip_origin + int(pipeline.scroll_helper.scroll_position)
    for record in pipeline.live_records():
        if record.key.startswith(key_prefix) and record.abs_x + record.width > left + 40:
            return record
    return None


def test_an_update_changes_cards_already_in_the_strip(ticker):
    coordinator, plugin, pm = ticker
    assert coordinator.start()
    pipeline = coordinator.render_pipeline
    assert _run_until(coordinator, lambda: _upcoming(pipeline, "card:"))
    record = _upcoming(pipeline, "card:")
    before = _columns(pipeline, record)

    plugin.update()                    # new data: every card's bars change
    pm._note_update_completed(PID)     # what the update worker does after it
    assert _run_until(
        coordinator,
        lambda: not np.array_equal(_columns(pipeline, record), before), seconds=5.0)
    # The card changed in place: same columns, same width, new pixels.
    assert pipeline._record_by_seq[record.seq] == record
    assert pipeline._live_worker is not None and pipeline._live_worker.is_alive()


def test_an_animated_element_moves_with_no_update_at_all(ticker):
    coordinator, _plugin, _pm = ticker
    assert coordinator.start()
    pipeline = coordinator.render_pipeline
    assert _run_until(coordinator, lambda: _upcoming(pipeline, "map"))
    record = _upcoming(pipeline, "map")
    before = _columns(pipeline, record)
    assert _run_until(
        coordinator,
        lambda: (pipeline._record_by_seq.get(record.seq) is not None
                 and not np.array_equal(_columns(pipeline, record), before)),
        seconds=5.0)


def test_stopping_vegas_stops_the_worker(ticker):
    coordinator, _plugin, _pm = ticker
    assert coordinator.start()
    pipeline = coordinator.render_pipeline
    assert _run_until(coordinator, lambda: pipeline._live_worker is not None)
    worker = pipeline._live_worker
    coordinator.stop()
    worker.join(3)
    assert not worker.is_alive()
    assert pipeline._live_worker is None


def test_with_live_refresh_off_the_same_run_is_plain_content(dm, tmp_path, ticker):
    coordinator, _plugin, _pm = ticker
    coordinator.vegas_config.live_refresh = False
    assert coordinator.start()
    assert not coordinator.live_active
    pipeline = coordinator.render_pipeline
    start_width = pipeline.scroll_helper.total_scroll_width
    assert _run_until(
        coordinator,
        lambda: pipeline.extensions >= 1, seconds=10.0)
    assert pipeline.live_records() == ()
    assert np.asarray(pipeline.scroll_helper.cached_array).shape[1] > 0
    assert start_width > 0
