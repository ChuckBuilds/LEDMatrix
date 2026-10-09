"""STATIC Vegas plugins pause the scroll when the scroll reaches their turn.

The coordinator used to decide by peeking at the front of the stream
manager's segment buffer. Continuous scrolling (the default) never advances
that buffer -- it extends the strip with take_next_group() -- so the same
first segment was examined on every frame: a STATIC plugin paused the scroll
only if it happened to be first, once, at startup, and otherwise scrolled past
as ordinary content. The render pipeline now marks where each STATIC plugin's
turn falls in the strip, and the coordinator pauses when the scroll gets there.
"""

import threading
from collections import deque
from types import SimpleNamespace
from unittest.mock import MagicMock

from PIL import Image

from src.plugin_system.base_plugin import VegasDisplayMode
from src.vegas_mode.config import VegasModeConfig
from src.vegas_mode.render_pipeline import RenderPipeline

W, H = 64, 32
SEP = 32


def block(width):
    return Image.new('RGB', (width, H), (255, 255, 255))


class DM:
    width = W
    height = H

    def set_scrolling_state(self, *a):
        pass


class FakeStream:
    """Stands in for StreamManager: a composed buffer, then extension groups."""

    def __init__(self, layout, statics=(), groups=()):
        # layout: [(plugin_id, width or None for STATIC)]
        self.layout = layout
        self.statics = set(statics) | {pid for pid, w in layout if w is None}
        self.groups = deque(groups)
        self.plugin_manager = SimpleNamespace(plugins={})

    def get_grouped_content_for_composition(self):
        return [(pid, [block(w)]) for pid, w in self.layout if w is not None]

    def get_static_layout(self):
        return [(pid, w is None) for pid, w in self.layout]

    def is_static_plugin(self, plugin_id):
        return plugin_id in self.statics

    def take_next_group(self, count=None, offscreen_only=False):
        return self.groups.popleft() if self.groups else []


def pipeline(stream, **cfg):
    cfg.setdefault('separator_width', SEP)
    cfg.setdefault('lead_in_width', 0)
    p = RenderPipeline(VegasModeConfig(**cfg), DM(), stream)
    p.start_prefetch = lambda: None  # keep extension on this thread
    return p


def scroll_to(p, x):
    p.scroll_helper.scroll_position = float(x)


class TestComposedStrip:
    def test_a_static_plugin_mid_strip_triggers_when_the_scroll_reaches_it(self):
        p = pipeline(FakeStream([('a', 100), ('s', None), ('b', 50)]), lead_in_width=10)
        assert p.compose_scroll_content()
        # 's' follows 'a', which ends at 10 + 100 = 110.
        scroll_to(p, 110 - W - 1)
        assert p.next_static_trigger() is None
        scroll_to(p, 110 - W)
        assert p.next_static_trigger() == 's'
        # Consumed: one pause per place in the strip.
        assert p.next_static_trigger() is None

    def test_static_content_is_not_in_the_strip(self):
        p = pipeline(FakeStream([('a', 100), ('s', None), ('b', 50)]))
        assert p.compose_scroll_content()
        assert p.scroll_helper.cached_image.width == 100 + SEP + 50

    def test_a_static_plugin_first_triggers_at_once(self):
        p = pipeline(FakeStream([('s', None), ('a', 100)]))
        assert p.compose_scroll_content()
        assert p.next_static_trigger() == 's'

    def test_no_static_plugins_no_triggers(self):
        p = pipeline(FakeStream([('a', 100), ('b', 50)]))
        assert p.compose_scroll_content()
        scroll_to(p, 1000)
        assert p.next_static_trigger() is None


class TestContinuousExtension:
    def test_a_static_plugin_in_a_later_group_triggers_at_its_place(self):
        # The reported bug: in continuous mode, a STATIC plugin anywhere but
        # first never paused the scroll at all.
        stream = FakeStream([('a', 200)], statics={'s'},
                            groups=[[('c', [block(40)]), ('s', []), ('d', [block(30)])]])
        p = pipeline(stream)
        assert p.compose_scroll_content()
        assert p.extend_scroll_content()
        # The strip had 200 columns; 'c' is appended after a separator and
        # 's' follows it: 200 + 32 + 40 = 272. 's' adds no columns.
        assert p.scroll_helper.cached_image.width == 200 + SEP + 40 + SEP + 30
        scroll_to(p, 272 - W - 1)
        assert p.next_static_trigger() is None
        scroll_to(p, 272 - W)
        assert p.next_static_trigger() == 's'

    def test_markers_follow_the_strip_when_its_head_is_trimmed(self):
        stream = FakeStream([('a', 400)], statics={'s'},
                            groups=[[('c', [block(40)]), ('s', [])]])
        p = pipeline(stream)
        assert p.compose_scroll_content()
        # Far enough along that extension trims the head of the strip.
        scroll_to(p, 300)
        assert p.extend_scroll_content()
        cut = 300 - int(p.scroll_helper.scroll_position)
        assert cut > 0
        marker = 400 + SEP + 40 - cut
        scroll_to(p, marker - W - 1)
        assert p.next_static_trigger() is None
        scroll_to(p, marker - W)
        assert p.next_static_trigger() == 's'

    def test_a_group_of_only_static_plugins_marks_the_strip_end(self):
        stream = FakeStream([('a', 200)], statics={'s'}, groups=[[('s', [])]])
        p = pipeline(stream)
        assert p.compose_scroll_content()
        p.extend_scroll_content()
        scroll_to(p, 200 - W)
        assert p.next_static_trigger() == 's'

    def test_reset_forgets_markers(self):
        p = pipeline(FakeStream([('a', 100), ('s', None), ('b', 50)]))
        assert p.compose_scroll_content()
        p.reset()
        scroll_to(p, 1000)
        assert p.next_static_trigger() is None


class TestStreamManagerSkipsStaticContent:
    def _stream(self, plugins):
        from src.vegas_mode.stream_manager import StreamManager
        sm = StreamManager.__new__(StreamManager)
        sm._buffer_lock = threading.RLock()
        sm._ordered_plugins = list(plugins)
        sm._prefetch_index = 0
        sm.config = SimpleNamespace(plugins_per_cycle=len(plugins), offscreen_prefetch=True)
        sm.plugin_manager = SimpleNamespace(plugins=plugins)
        sm.plugin_adapter = MagicMock()
        sm.plugin_adapter.get_content.return_value = [block(10)]
        sm.refresh = lambda: None
        return sm

    def _plugin(self, mode):
        return SimpleNamespace(get_vegas_display_mode=lambda: mode)

    def test_static_plugins_keep_their_place_but_are_not_rendered(self):
        sm = self._stream({
            'a': self._plugin(VegasDisplayMode.SCROLL),
            's': self._plugin(VegasDisplayMode.STATIC),
        })
        group = sm.take_next_group()
        assert [pid for pid, _ in group] == ['a', 's']
        assert dict(group)['s'] == []
        fetched = [c.args[1] for c in sm.plugin_adapter.get_content.call_args_list]
        assert fetched == ['a']

    def test_static_layout_lines_up_with_composition(self):
        from src.vegas_mode.stream_manager import ContentSegment, StreamManager
        sm = StreamManager.__new__(StreamManager)
        sm._buffer_lock = threading.RLock()
        sm._active_buffer = deque([
            ContentSegment('a', [block(10)], VegasDisplayMode.SCROLL),
            ContentSegment('empty', [], VegasDisplayMode.SCROLL),
            ContentSegment('s', [], VegasDisplayMode.STATIC),
            ContentSegment('b', [block(10)], VegasDisplayMode.SCROLL),
        ])
        assert sm.get_static_layout() == [('a', False), ('s', True), ('b', False)]
        assert [pid for pid, _ in sm.get_grouped_content_for_composition()] == ['a', 'b']


class TestCoordinatorStaticPause:
    def _coord(self, plugin, lock=None):
        from src.vegas_mode.coordinator import VegasModeCoordinator
        coord = VegasModeCoordinator.__new__(VegasModeCoordinator)
        coord.render_pipeline = MagicMock()
        coord.render_pipeline.get_scroll_position.return_value = 0
        coord.display_manager = MagicMock()
        locks = {plugin.plugin_id: lock or threading.Lock()}
        coord.plugin_manager = SimpleNamespace(
            get_plugin={plugin.plugin_id: plugin}.get,
            get_plugin_lock=locks.__getitem__)
        coord._state_lock = threading.Lock()
        coord._static_pause_active = False
        coord._saved_scroll_position = None
        coord._should_stop = False
        coord._live_priority_active = False
        coord._live_priority_check = None
        coord._interrupt_check = None
        return coord

    def _plugin(self):
        plugin = MagicMock()
        plugin.plugin_id = 'clock'
        # A moment: zero would pause 15 s, as the rotation shows it.
        plugin.get_display_duration.return_value = 0.01
        return plugin

    def test_trigger_comes_from_the_pipeline(self):
        plugin = self._plugin()
        coord = self._coord(plugin)
        coord.render_pipeline.next_static_trigger.return_value = 'clock'
        assert coord._check_static_plugin_trigger() is plugin
        coord.render_pipeline.next_static_trigger.return_value = None
        assert coord._check_static_plugin_trigger() is None

    def test_pause_displays_under_the_plugin_lock(self):
        plugin = self._plugin()
        lock = threading.Lock()
        seen = []
        plugin.display.side_effect = lambda **k: seen.append(lock.locked())
        coord = self._coord(plugin, lock)
        assert coord._handle_static_pause(plugin) is True
        assert seen == [True]
        assert not lock.locked()

    def test_pause_is_skipped_while_update_holds_the_lock(self):
        plugin = self._plugin()
        lock = threading.Lock()
        lock.acquire()
        coord = self._coord(plugin, lock)
        coord.STATIC_LOCK_TIMEOUT = 0.01
        try:
            assert coord._handle_static_pause(plugin) is True
        finally:
            lock.release()
        plugin.display.assert_not_called()
