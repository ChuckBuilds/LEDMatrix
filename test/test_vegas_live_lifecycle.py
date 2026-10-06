"""When the live-element worker runs, and what happens to its work when it stops.

The worker fetches the strip's groups as well as redrawing live elements, so
starting it where nothing is live costs a thread for nothing, and stopping it
must not lose the group it was fetching: nothing else asks for one until the
next extension, which would then fetch inline and stall the scroll.
"""
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.vegas_mode import elements, render_pipeline  # noqa: E402
from src.vegas_mode.config import VegasModeConfig  # noqa: E402
from src.vegas_mode.elements import ElementMeta  # noqa: E402
from src.vegas_mode.render_pipeline import RenderPipeline  # noqa: E402

from test.test_vegas_live_apply import _DM  # noqa: E402

H = 32


def _image(width, seed, key=None):
    rng = np.random.default_rng(seed)
    pixels = rng.integers(20, 255, (H, width, 3), dtype=np.uint8)
    image = Image.frombytes("RGB", (width, H), pixels.tobytes())
    if key is None:
        return image
    pinned, array = elements.pin_element(image, 8)
    return elements.tag(pinned, ElementMeta("p", key, 1, elements.pixel_digest(array), 0.0, 0.0))


class _Stream:
    def __init__(self, live):
        self.first = [("p", [_image(40, i, f"k{i}" if live else None) for i in range(6)])]
        self.plugin_manager = type("PM", (), {"plugins": {}})()
        self.plugin_adapter = None
        self.taken = 0

    def get_grouped_content_for_composition(self):
        return self.first

    def take_next_group(self, count=None, offscreen_only=False):
        self.taken += 1
        return [("q", [_image(40, 99)])]


class _FakeWorker:
    """Stands in for VegasWorker: no thread, and says what it was asked."""

    instances = []
    alive = True

    def __init__(self, pipeline):
        self.pipeline = pipeline
        self.requests = 0
        self.stopped = False
        self.on_join = None
        _FakeWorker.instances.append(self)

    def start(self):
        pass

    def is_alive(self):
        return _FakeWorker.alive and not self.stopped

    def request_group(self):
        self.requests += 1

    def notify_data(self, plugin_id):
        pass

    def stop(self):
        # Still finishing its job: alive until joined.
        self.stopping = True

    def join(self, timeout=None):
        if self.on_join is not None:
            self.on_join()
        self.stopped = True


@pytest.fixture(autouse=True)
def _fake_worker(monkeypatch):
    _FakeWorker.instances = []
    _FakeWorker.alive = True
    monkeypatch.setattr(render_pipeline, "VegasWorker", _FakeWorker)


def _pipeline(live=True):
    p = RenderPipeline(VegasModeConfig(continuous_scroll=True, lead_in_width=0),
                       _DM(), _Stream(live))
    assert p.compose_scroll_content()
    return p


def _wait_for(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return False


def test_supervision_starts_no_worker_while_nothing_is_live():
    p = _pipeline(live=False)
    p.set_live(True)
    for _ in range(p.LIVE_SUPERVISE_FRAMES * 2 + 1):
        p.apply_live_patches()
    assert p._live_worker is None and not _FakeWorker.instances


def test_live_elements_start_the_worker_and_it_is_asked_for_a_group():
    p = _pipeline()
    p.set_live(True)
    assert isinstance(p._live_worker, _FakeWorker)
    assert p._live_worker.requests == 1


def test_switching_live_off_hands_group_fetching_back():
    p = _pipeline()
    p.set_live(True)
    worker = p._live_worker
    p.set_live(False)
    assert p._live_worker is None and p._retired_worker is worker
    assert _wait_for(lambda: p._prepared_group is not None)
    assert worker.stopped                   # waited for before fetching
    assert p._prefetch_thread.name == "vegas-strip-prefetch"
    assert p.stream_manager.taken == 1


def test_a_group_the_stopping_worker_hands_over_is_kept():
    p = _pipeline()
    p.set_live(True)
    worker = p._live_worker
    handed = [("p", ["from the worker"])]

    def publish():
        with p._prefetch_lock:
            p._prepared_group = handed

    worker.on_join = publish
    p.set_live(False)
    p._prefetch_thread.join(2)
    assert p._prepared_group is handed
    assert p.stream_manager.taken == 0      # nothing fetched over it


def test_a_reset_while_stopping_fetches_nothing():
    p = _pipeline()
    p.set_live(True)
    release = threading.Event()
    p._live_worker.on_join = lambda: release.wait(2)
    p.set_live(False)
    p.reset()
    release.set()
    p._prefetch_thread.join(2)
    assert p._prepared_group is None and p.stream_manager.taken == 0


def test_a_worker_given_up_on_hands_group_fetching_back():
    p = _pipeline()
    p.set_live(True)
    _FakeWorker.alive = False               # it, and every restart, dies
    for _ in range(p.LIVE_WORKER_MAX_DEATHS):
        p._ensure_live_worker()
    assert not p._live_enabled and p._live_worker is None
    assert _wait_for(lambda: p._prepared_group is not None)
    assert p.stream_manager.taken == 1
