"""
Regression tests for RenderPipeline.should_recompose()'s pending-updates check.

PR #299 added a check so a plugin's live score/status change (a "pending
update" in StreamManager) triggers a hot-swap within a few seconds instead
of waiting for a full scroll cycle to complete. PR #330 (multi-display sync)
refactored should_recompose() and dropped that check entirely -- not just
gated behind the new sync-mode deferral it added, but removed outright, so
even standalone (non-sync) installations silently lost live-refresh and fell
back to waiting for full cycle boundaries (which, depending on
min/max_cycle_duration, can be minutes).
"""

from unittest.mock import MagicMock

from src.vegas_mode.config import VegasModeConfig
from src.vegas_mode.render_pipeline import RenderPipeline


class FakeDisplayManager:
    width = 64
    height = 32


def _make_pipeline(sync_manager=None):
    stream_manager = MagicMock()
    stream_manager.get_buffer_status.return_value = {'staging_count': 0}
    pipeline = RenderPipeline(VegasModeConfig(), FakeDisplayManager(), stream_manager)
    pipeline.sync_manager = sync_manager
    return pipeline, stream_manager


class TestShouldRecompose:
    def test_cycle_complete_always_recomposes(self):
        pipeline, stream_manager = _make_pipeline()
        pipeline._cycle_complete = True
        stream_manager.has_pending_updates_for_visible_segments.return_value = False
        assert pipeline.should_recompose() is True

    def test_no_pending_updates_no_staging_does_not_recompose(self):
        pipeline, stream_manager = _make_pipeline()
        stream_manager.has_pending_updates_for_visible_segments.return_value = False
        assert pipeline.should_recompose() is False

    def test_pending_updates_on_visible_segment_triggers_recompose(self):
        """The actual regression: a live-updated plugin currently in view
        must trigger a recompose instead of waiting for cycle end."""
        pipeline, stream_manager = _make_pipeline()
        stream_manager.has_pending_updates_for_visible_segments.return_value = True
        assert pipeline.should_recompose() is True

    def test_staging_buffer_content_triggers_recompose(self):
        pipeline, stream_manager = _make_pipeline()
        stream_manager.get_buffer_status.return_value = {'staging_count': 1}
        stream_manager.has_pending_updates_for_visible_segments.return_value = False
        assert pipeline.should_recompose() is True

    def test_sync_active_defers_pending_updates_to_cycle_boundary(self):
        """Sync-mode deferral (PR #330's actual intent) must still hold:
        pending updates alone must NOT trigger a mid-cycle hot-swap when a
        follower display is attached, since that causes a visible
        freeze+jump on the follower. This must keep working after
        restoring the non-sync pending-updates check above."""
        pipeline, stream_manager = _make_pipeline(sync_manager=MagicMock())
        stream_manager.has_pending_updates_for_visible_segments.return_value = True
        assert pipeline.should_recompose() is False

    def test_sync_active_still_recomposes_on_cycle_complete(self):
        pipeline, stream_manager = _make_pipeline(sync_manager=MagicMock())
        pipeline._cycle_complete = True
        stream_manager.has_pending_updates_for_visible_segments.return_value = True
        assert pipeline.should_recompose() is True
