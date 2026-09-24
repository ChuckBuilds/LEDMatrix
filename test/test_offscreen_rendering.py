"""Per-thread off-screen rendering (DisplayManager.offscreen) and its Vegas use.

The display canvas used to be one shared object, so any plugin that drew on it
could only be rendered on the render thread, stalling the scroll for 40-600ms
each. offscreen() gives the calling thread a canvas of its own. These tests pin
the property that makes that safe: another thread's drawing never reaches what
the render loop sees, presents or paces by.

Runs against RGBMatrixEmulator, exercising the real DisplayManager.
"""

import os
import sys
import threading
from types import SimpleNamespace

os.environ["EMULATOR"] = "true"

import pytest
from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.vegas_mode.config import VegasModeConfig  # noqa: E402
from src.vegas_mode.plugin_adapter import PluginAdapter  # noqa: E402
from src.vegas_mode.stream_manager import StreamManager  # noqa: E402

WIDTH, HEIGHT = 128, 32
YELLOW = (255, 255, 0)


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
        tmp_path_factory.mktemp("offscreen") / "led_matrix_preview.png")
    if manager.matrix is None:
        pytest.fail("DisplayManager fell back to matrix=None; see the "
                    "'Failed to initialize RGB Matrix' log line above.")
    yield manager
    DisplayManager._instance = None
    DisplayManager._initialized = False


@pytest.fixture
def fresh(dm):
    """A known shared canvas, and scroll state reset around each test."""
    dm.image = Image.new('RGB', (WIDTH, HEIGHT))
    from PIL import ImageDraw
    dm.draw = ImageDraw.Draw(dm.image)
    dm.set_scrolling_state(False)
    yield dm
    dm.set_scrolling_state(False)


class _Spy:
    """Counts calls to one method of one object, and still calls it."""

    def __init__(self, obj, name):
        self.obj, self.name, self.count = obj, name, 0
        self._orig = getattr(obj, name)

    def __enter__(self):
        def counting(*args, **kwargs):
            self.count += 1
            return self._orig(*args, **kwargs)
        setattr(self.obj, self.name, counting)
        return self

    def __exit__(self, *exc):
        setattr(self.obj, self.name, self._orig)


def _in_thread(fn):
    """Run fn on another thread and return its result, re-raising its error."""
    box = {}

    def run():
        try:
            box["value"] = fn()
        except BaseException as exc:  # surfaced below
            box["error"] = exc

    thread = threading.Thread(target=run)
    thread.start()
    thread.join(10)
    assert not thread.is_alive(), "worker hung"
    if "error" in box:
        raise box["error"]
    return box.get("value")


class TestIsolation:
    def test_another_threads_drawing_is_invisible_here(self, fresh):
        dm = fresh
        shared = dm.image
        inside, release = threading.Event(), threading.Event()
        seen = {}

        def worker():
            with dm.offscreen(40) as surface:
                dm.draw.rectangle([0, 0, 39, 31], fill=YELLOW)
                seen.update(width=dm.width, matrix_width=dm.matrix.width,
                            own_image=dm.image is surface.image)
                inside.set()
                release.wait(5)
            seen["restored"] = dm.image is shared

        thread = threading.Thread(target=worker)
        thread.start()
        try:
            assert inside.wait(5)
            # While the worker is mid-draw, this thread sees the real canvas.
            assert dm.image is shared
            assert (dm.width, dm.matrix.width) == (WIDTH, WIDTH)
            assert shared.getpixel((0, 0)) == (0, 0, 0)
        finally:
            release.set()
            thread.join(5)
        assert seen == {"width": 40, "matrix_width": 40, "own_image": True,
                        "restored": True}

    def test_the_render_loop_keeps_presenting_while_another_thread_draws(self, fresh):
        dm = fresh
        inside, release = threading.Event(), threading.Event()

        def worker():
            with dm.offscreen():
                dm.draw.rectangle([0, 0, 10, 10], fill=YELLOW)
                dm.update_display()          # must not reach the panel
                inside.set()
                release.wait(5)

        with _Spy(dm.matrix, "SwapOnVSync") as swaps:
            thread = threading.Thread(target=worker)
            thread.start()
            try:
                assert inside.wait(5)
                dm.draw.point((5, 5), fill=(0, 0, 255))
                dm.update_display()          # the render thread's own frame
            finally:
                release.set()
                thread.join(5)
        assert swaps.count == 1

    def test_assignments_go_to_the_callers_canvas(self, fresh):
        dm = fresh
        shared = dm.image
        replacement = Image.new('RGB', (WIDTH, HEIGHT), (1, 2, 3))

        def worker():
            with dm.offscreen():
                dm.image = replacement       # e.g. clear() inside display()
                return dm.image is replacement

        assert _in_thread(worker) is True
        assert dm.image is shared

    def test_render_size_narrows_only_the_calling_thread(self, fresh):
        dm = fresh
        inside, release = threading.Event(), threading.Event()
        seen = {}

        def worker():
            with dm.render_size(32):
                seen["width"] = dm.width
                inside.set()
                release.wait(5)

        thread = threading.Thread(target=worker)
        thread.start()
        try:
            assert inside.wait(5)
            assert dm.width == WIDTH
        finally:
            release.set()
            thread.join(5)
        assert seen["width"] == 32


class TestNothingReachesThePanel:
    def test_update_display_clear_and_pacing_are_inert_inside(self, fresh):
        dm = fresh
        real_matrix = dm.matrix
        hold_before = dm._frame_hold
        with _Spy(real_matrix, "SwapOnVSync") as swaps, \
                _Spy(dm.offscreen_canvas, "Clear") as clears:
            with dm.offscreen() as surface:
                dm.draw.rectangle([0, 0, 5, 5], fill=YELLOW)
                dm.update_display()
                drawn = dm.image
                dm.clear()
                # clear() replaced the surface's image, not the shared one.
                assert dm.image is surface.image and dm.image is not drawn
                assert dm.image.getpixel((0, 0)) == (0, 0, 0)
                dm.set_scrolling_state(True, 3)
                dm.set_frame_hold(4)
                dm.matrix.SwapOnVSync(object())       # inert through the proxy too
        assert swaps.count == 0
        assert clears.count == 0
        assert dm._frame_hold == hold_before
        assert dm._scrolling_state['is_scrolling'] is False

    def test_capture_mode_inside_offscreen_does_not_end_suppression(self, fresh):
        dm = fresh
        real_matrix = dm.matrix
        with _Spy(real_matrix, "SwapOnVSync") as swaps:
            with dm.offscreen():
                with dm.capture_mode():
                    pass
                dm.draw.point((1, 1), fill=YELLOW)
                dm.update_display()
        assert swaps.count == 0

    def test_nesting_and_exceptions_restore_state(self, fresh):
        dm = fresh
        shared = dm.image
        with pytest.raises(RuntimeError):
            with dm.offscreen(100):
                with dm.offscreen(50):
                    assert dm.width == 50
                    raise RuntimeError("plugin failed mid-draw")
        assert dm.image is shared
        assert dm.width == WIDTH
        assert not dm._writes_suppressed()

        with dm.offscreen(100) as outer:
            with dm.offscreen(50):
                pass
            assert dm.image is outer.image
            assert dm.width == 100


# --- Vegas: the adapter draws plugins off the render thread ------------------

class CapturePlugin:
    """A plugin with neither get_vegas_content nor a scroll helper: captured."""

    def __init__(self, display_manager):
        self.display_manager = display_manager
        self.calls = 0

    def display(self, force_clear=False):
        self.calls += 1
        self.display_manager.draw.rectangle([0, 0, 30, 20], fill=YELLOW)
        self.display_manager.update_display()


class ScrollingPlugin:
    """A ticker whose scroll image only exists once display() has run."""

    def __init__(self, display_manager):
        from src.common.scroll_helper import ScrollHelper
        self.display_manager = display_manager
        self.scroll_helper = ScrollHelper(WIDTH, HEIGHT)

    def display(self, force_clear=False):
        item = Image.new('RGB', (300, HEIGHT), YELLOW)
        self.scroll_helper.create_scrolling_image([item], item_gap=0, element_gap=0)
        self.display_manager.set_scrolling_state(True, 5)
        self.display_manager.update_display()


def _adapter(dm, **config):
    return PluginAdapter(dm, VegasModeConfig(**config))


def _has_yellow(images):
    return any(YELLOW in {img.getpixel((x, y)) for x in range(min(img.width, 40))
                          for y in range(img.height)} for img in images)


class TestAdapterOffTheRenderThread:
    def test_display_capture_runs_on_a_background_thread(self, fresh):
        dm = fresh
        shared = dm.image
        before = shared.tobytes()
        plugin = CapturePlugin(dm)
        adapter = _adapter(dm)

        with _Spy(dm.matrix, "SwapOnVSync") as swaps:
            images = _in_thread(
                lambda: adapter.get_content(plugin, "capture-bg", offscreen_only=True))

        assert images and _has_yellow(images)
        assert dm.image is shared and shared.tobytes() == before
        assert swaps.count == 0

    def test_scroll_content_is_generated_on_a_background_thread(self, fresh):
        dm = fresh
        plugin = ScrollingPlugin(dm)
        adapter = _adapter(dm)
        hold_before = dm._frame_hold

        images = _in_thread(
            lambda: adapter.get_content(plugin, "scroll-bg", offscreen_only=True))

        assert images and _has_yellow(images)
        # The plugin's own set_scrolling_state(True, 5) did not re-pace Vegas.
        assert dm._frame_hold == hold_before
        assert dm._scrolling_state['is_scrolling'] is False

    def test_with_the_switch_off_background_capture_is_left_for_the_render_thread(self, fresh):
        dm = fresh
        plugin = CapturePlugin(dm)
        adapter = _adapter(dm, offscreen_prefetch=False)

        images = _in_thread(
            lambda: adapter.get_content(plugin, "capture-legacy", offscreen_only=True))

        assert images is None
        assert plugin.calls == 0


class TestPluginLock:
    def test_a_background_fetch_waits_for_update_then_skips(self, fresh):
        dm = fresh
        lock = threading.Lock()
        plugin = CapturePlugin(dm)
        adapter = PluginAdapter(dm, VegasModeConfig(),
                                plugin_manager=SimpleNamespace(
                                    get_plugin_lock=lambda plugin_id: lock))
        adapter.PLUGIN_LOCK_TIMEOUT = 0.05

        with lock:   # update() in progress
            skipped = _in_thread(
                lambda: adapter.get_content(plugin, "locked", offscreen_only=True))
        assert skipped is None
        assert plugin.calls == 0

        served = _in_thread(
            lambda: adapter.get_content(plugin, "locked", offscreen_only=True))
        assert served and plugin.calls == 1
        assert not lock.locked()


class TestStreamDoesNotDeferEmptyResults:
    def _group(self, offscreen_prefetch):
        config = VegasModeConfig(offscreen_prefetch=offscreen_prefetch)
        adapter = SimpleNamespace(get_content=lambda *a, **k: None)
        stream = StreamManager(config, SimpleNamespace(plugins={"p": object()}),
                               adapter)
        stream.refresh = lambda: None
        stream._ordered_plugins = ["p"]
        return stream.take_next_group(count=1, offscreen_only=True)

    def test_nothing_to_show_is_not_sent_to_the_render_thread(self):
        assert self._group(offscreen_prefetch=True) == [("p", [])]

    def test_the_old_contract_still_defers_with_the_switch_off(self):
        assert self._group(offscreen_prefetch=False) == [("p", None)]
