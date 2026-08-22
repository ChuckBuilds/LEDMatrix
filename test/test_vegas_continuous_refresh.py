"""
Regression tests: changed plugin data must reach the strip in continuous mode.

Two faults combined to freeze Vegas content indefinitely.

PR #291 added a call to ``plugin_adapter.invalidate_plugin_scroll_cache()`` so a
plugin's *own* cached scroll image would be rebuilt from fresh data. The method
was never implemented, and ``hot_swap_content()`` wraps the call in a broad
except, so every hot swap raised AttributeError and was silently swallowed.

Continuous scrolling then removed the only path that reached it at all:
``should_recompose()``/``hot_swap_content()`` are called from the non-continuous
branch, while ``continuous_scroll`` defaults to True.

Together, a plugin composed its scroll image once and handed back the same
picture forever, because the sports plugins' ``get_vegas_content()`` regenerates
only when its cache is empty. Symptom: a game that was live last night is still
drawn as live the following morning.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
from PIL import Image

from src.vegas_mode.config import VegasModeConfig
from src.vegas_mode.plugin_adapter import PluginAdapter
from src.vegas_mode.render_pipeline import RenderPipeline
from src.vegas_mode.stream_manager import StreamManager


class FakeDisplayManager:
    width = 64
    height = 32


def _helper():
    """A stand-in ScrollHelper holding both halves of its cache."""
    image = Image.new('RGB', (128, 32), (10, 20, 30))
    return SimpleNamespace(cached_image=image, cached_array=np.array(image))


class TestInvalidatePluginScrollCache:
    """The method PR #291 called but never defined."""

    def test_method_exists(self):
        # It was called for months without existing; the broad except in
        # hot_swap_content() meant nothing ever surfaced.
        assert hasattr(PluginAdapter, 'invalidate_plugin_scroll_cache')

    def test_clears_helper_attached_to_the_plugin(self):
        adapter = PluginAdapter(FakeDisplayManager(), VegasModeConfig())
        helper = _helper()
        plugin = SimpleNamespace(scroll_helper=helper)

        assert adapter.invalidate_plugin_scroll_cache(plugin, 'stocks') is True
        assert helper.cached_image is None
        assert helper.cached_array is None

    def test_clears_helper_owned_by_a_scroll_manager(self):
        # The sports scoreboards keep theirs on _scroll_manager, which is the
        # layout that produced the reported stale-scores bug.
        adapter = PluginAdapter(FakeDisplayManager(), VegasModeConfig())
        helper = _helper()
        plugin = SimpleNamespace(_scroll_manager=SimpleNamespace(scroll_helper=helper))

        assert adapter.invalidate_plugin_scroll_cache(plugin, 'baseball') is True
        assert helper.cached_image is None
        assert helper.cached_array is None

    def test_clears_both_halves_together(self):
        # cached_array is the image's numpy mirror; leaving one behind lets a
        # reader pick up content the other no longer has.
        adapter = PluginAdapter(FakeDisplayManager(), VegasModeConfig())
        helper = _helper()
        adapter.invalidate_plugin_scroll_cache(
            SimpleNamespace(scroll_helper=helper), 'news')
        assert (helper.cached_image, helper.cached_array) == (None, None)

    def test_plugin_without_a_helper_is_not_an_error(self):
        adapter = PluginAdapter(FakeDisplayManager(), VegasModeConfig())
        assert adapter.invalidate_plugin_scroll_cache(SimpleNamespace(), 'clock') is False


class TestInvalidatePendingUpdates:
    def _manager(self, plugins):
        stream = StreamManager(
            VegasModeConfig(),
            SimpleNamespace(plugins=plugins),
            MagicMock(),
        )
        stream.plugin_adapter = MagicMock()
        return stream

    def test_drops_caches_for_updated_plugins(self):
        helper = _helper()
        plugin = SimpleNamespace(scroll_helper=helper)
        stream = self._manager({'baseball': plugin})
        stream.mark_plugin_updated('baseball')

        assert stream.invalidate_pending_updates() == ['baseball']
        stream.plugin_adapter.invalidate_cache.assert_called_once_with('baseball')
        stream.plugin_adapter.invalidate_plugin_scroll_cache.assert_called_once_with(
            plugin, 'baseball')

    def test_pending_flags_are_consumed(self):
        # Left unconsumed they accumulate forever and nothing ever refreshes.
        stream = self._manager({'baseball': SimpleNamespace()})
        stream.mark_plugin_updated('baseball')
        assert stream.has_pending_updates() is True

        stream.invalidate_pending_updates()
        assert stream.has_pending_updates() is False
        assert stream.invalidate_pending_updates() == []

    def test_no_pending_updates_does_no_work(self):
        stream = self._manager({})
        assert stream.invalidate_pending_updates() == []
        stream.plugin_adapter.invalidate_cache.assert_not_called()

    def test_a_failing_plugin_does_not_stop_the_others(self):
        stream = self._manager({'a': SimpleNamespace(), 'b': SimpleNamespace()})
        stream.mark_plugin_updated('a')
        stream.mark_plugin_updated('b')
        stream.plugin_adapter.invalidate_cache.side_effect = [
            RuntimeError('boom'), None]

        assert sorted(stream.invalidate_pending_updates()) == ['a', 'b']
        assert stream.plugin_adapter.invalidate_cache.call_count == 2


class TestContinuousModeReachesTheRefresh:
    def _pipeline(self):
        stream = MagicMock()
        stream.get_buffer_status.return_value = {'staging_count': 0}
        return RenderPipeline(VegasModeConfig(), FakeDisplayManager(), stream), stream

    def test_refresh_delegates_to_the_stream_manager(self):
        pipeline, stream = self._pipeline()
        stream.invalidate_pending_updates.return_value = ['baseball']
        assert pipeline.refresh_updated_plugins() is True

    def test_refresh_reports_false_when_nothing_changed(self):
        pipeline, stream = self._pipeline()
        stream.invalidate_pending_updates.return_value = []
        assert pipeline.refresh_updated_plugins() is False

    def test_refresh_never_raises_into_the_render_loop(self):
        pipeline, stream = self._pipeline()
        stream.invalidate_pending_updates.side_effect = RuntimeError('boom')
        assert pipeline.refresh_updated_plugins() is False

    def test_refresh_does_not_reposition_the_scroll(self):
        # The whole point of preferring this over hot_swap_content(): that path
        # rebuilds and repositions, which reads as a freeze then a jump.
        pipeline, stream = self._pipeline()
        stream.invalidate_pending_updates.return_value = ['baseball']
        pipeline.scroll_helper.scroll_position = 1234

        pipeline.refresh_updated_plugins()

        assert pipeline.scroll_helper.scroll_position == 1234
        stream.swap_buffers.assert_not_called()
        stream.process_updates.assert_not_called()


class TestCoordinatorWiring:
    """
    The regression itself: continuous mode has to *call* the refresh.

    should_recompose()/hot_swap_content() sit in the non-continuous branch, and
    continuous_scroll defaults to True, so before this fix the refresh was
    simply never reached on a default install.
    """

    def _coordinator(self, continuous):
        import threading

        from src.vegas_mode.coordinator import VegasModeCoordinator

        config = VegasModeConfig()
        config.continuous_scroll = continuous
        # Built without __init__ so the test exercises run_frame's branching
        # without standing up a display, stream and render stack.
        coordinator = VegasModeCoordinator.__new__(VegasModeCoordinator)
        coordinator.vegas_config = config
        coordinator.render_pipeline = MagicMock()
        coordinator.render_pipeline.has_deferred.return_value = False
        coordinator.render_pipeline.needs_extension.return_value = False
        coordinator.render_pipeline.is_cycle_complete.return_value = False
        coordinator.render_pipeline.should_recompose.return_value = False
        coordinator.stream_manager = MagicMock()
        coordinator.stats = {'cycles_completed': 0}
        coordinator._state_lock = threading.Lock()
        coordinator._is_active = True
        coordinator._is_paused = False
        coordinator._should_stop = False
        coordinator._pending_config_update = False
        coordinator._live_priority_check = None
        coordinator._interrupt_check = None
        coordinator.sync_manager = None
        return coordinator

    def test_continuous_mode_refreshes_updated_plugins_every_frame(self):
        coordinator = self._coordinator(continuous=True)
        coordinator.run_frame()
        coordinator.render_pipeline.refresh_updated_plugins.assert_called_once()

    def test_continuous_mode_does_not_use_the_disruptive_swap(self):
        coordinator = self._coordinator(continuous=True)
        coordinator.run_frame()
        coordinator.render_pipeline.hot_swap_content.assert_not_called()

    def test_swap_mode_still_uses_hot_swap(self):
        # The non-continuous path must keep its original behaviour.
        coordinator = self._coordinator(continuous=False)
        coordinator.render_pipeline.should_recompose.return_value = True
        coordinator.run_frame()
        coordinator.render_pipeline.hot_swap_content.assert_called_once()
        coordinator.render_pipeline.refresh_updated_plugins.assert_not_called()

    def test_a_frame_is_still_rendered_either_way(self):
        for continuous in (True, False):
            coordinator = self._coordinator(continuous=continuous)
            coordinator.run_frame()
            coordinator.render_pipeline.render_frame.assert_called_once()
