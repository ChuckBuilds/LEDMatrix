#!/usr/bin/env python3
"""
Tests that "which plugins have fresh data" survives the async update worker.

Regression under test: run_scheduled_updates_with_changes() snapshotted
plugin_last_update, called run_scheduled_updates(), and diffed the two. But
run_scheduled_updates() only *enqueues* -- the work runs on the update worker
and stamps the timestamp there, after the method has already returned. The
snapshots were therefore always identical and the result always empty.

Vegas depends on that result: it is what calls mark_plugin_updated(), which
drops the cached content for a plugin whose data changed. With it always
empty, a segment kept scrolling whatever it was first built from -- the
"last night's live game still drawn as live the next morning" failure the
coordinator comments describe. Observed on a live rig: zero update ticks in
twenty minutes, with weather, stocks and news all updating.

Run: python -m pytest test/test_update_change_reporting.py -v
"""

import ast
import inspect
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.plugin_system.plugin_manager import PluginManager  # noqa: E402


def _manager():
    """A PluginManager with only the update-reporting state initialised."""
    manager = PluginManager.__new__(PluginManager)
    manager._completed_updates = set()
    manager._completed_updates_lock = threading.Lock()
    return manager


class DrainCompletedUpdates(unittest.TestCase):
    def setUp(self):
        self.manager = _manager()

    def test_nothing_completed_reports_nothing(self):
        self.assertEqual(self.manager.drain_completed_updates(), [])

    def test_a_completed_update_is_reported(self):
        self.manager._note_update_completed("news")
        self.assertEqual(self.manager.drain_completed_updates(), ["news"])

    def test_draining_clears_so_the_next_poll_is_empty(self):
        self.manager._note_update_completed("news")
        self.manager.drain_completed_updates()
        self.assertEqual(
            self.manager.drain_completed_updates(), [],
            "a plugin must be reported once per update, not on every poll, "
            "or Vegas would drop its cached content every few seconds")

    def test_repeated_completions_between_polls_collapse(self):
        for _ in range(5):
            self.manager._note_update_completed("weather")
        self.assertEqual(self.manager.drain_completed_updates(), ["weather"])

    def test_multiple_plugins_are_all_reported(self):
        for plugin_id in ("news", "weather", "ledmatrix-stocks"):
            self.manager._note_update_completed(plugin_id)
        self.assertEqual(self.manager.drain_completed_updates(),
                         ["ledmatrix-stocks", "news", "weather"])


class CompletionReportingIsAsyncSafe(unittest.TestCase):
    """The point of the change: completion may land after the call returns."""

    def setUp(self):
        self.manager = _manager()

    def test_an_update_completing_after_the_call_is_still_reported(self):
        """The exact shape of the bug.

        The enqueueing call sees nothing, because the worker has not run yet.
        The next poll must report it -- under the old diff it was lost, since
        the second snapshot was taken before the worker ever stamped.
        """
        first = self.manager.drain_completed_updates()
        self.assertEqual(first, [], "nothing has finished yet")

        # The worker finishes some time later, on its own thread.
        worker = threading.Thread(
            target=self.manager._note_update_completed, args=("news",))
        worker.start()
        worker.join()

        self.assertEqual(
            self.manager.drain_completed_updates(), ["news"],
            "an update that finishes between polls must still be reported")

    def test_concurrent_completions_are_not_lost(self):
        ids = ["plugin-%02d" % i for i in range(40)]
        threads = [threading.Thread(target=self.manager._note_update_completed,
                                    args=(pid,)) for pid in ids]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(self.manager.drain_completed_updates(), sorted(ids))

    def test_a_completion_during_a_drain_is_not_swallowed(self):
        """A drain must not clear an entry it did not report."""
        self.manager._note_update_completed("news")
        reported = self.manager.drain_completed_updates()
        # ...worker finishes another one immediately afterwards
        self.manager._note_update_completed("weather")
        self.assertEqual(reported, ["news"])
        self.assertEqual(self.manager.drain_completed_updates(), ["weather"])


class EveryStampRecordsACompletion(unittest.TestCase):
    """The ledger is only correct if the production paths actually fill it.

    Asserting on the mechanics alone passes even when nothing calls
    _note_update_completed -- verified by deleting the call sites, which the
    behavioural tests above did not notice. This checks the invariant at the
    source: wherever a successful update stamps plugin_last_update, it must
    also record the completion, or Vegas silently stops being told.
    """

    def test_success_paths_record_the_completion(self):
        import src.plugin_system.plugin_manager as pm

        tree = ast.parse(inspect.getsource(pm))
        stamps = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.With):
                continue
            # `with self._plugin_last_update_lock:` blocks that stamp a real
            # time on success. Two stamps are deliberately excluded: the 0.0
            # written at registration, and the failure path, which backs the
            # timestamp off to space out retries -- neither means fresh data.
            assigns_time = any(
                isinstance(stmt, ast.Assign)
                and any(isinstance(t, ast.Subscript)
                        and getattr(t.value, "attr", None) == "plugin_last_update"
                        for t in stmt.targets)
                and not (isinstance(stmt.value, ast.Constant)
                         and stmt.value.value == 0.0)
                and "failure" not in ast.dump(stmt.value)
                for stmt in node.body
            )
            if assigns_time:
                stamps.append(node)

        self.assertGreaterEqual(
            len(stamps), 2,
            "expected the worker and inline success paths to stamp the time; "
            "if this drops, the search below is looking at the wrong thing")

        for stamp in stamps:
            enclosing = self._enclosing_function(tree, stamp)
            calls = [n for n in ast.walk(enclosing)
                     if isinstance(n, ast.Call)
                     and getattr(n.func, "attr", None) == "_note_update_completed"]
            self.assertTrue(
                calls,
                "%s stamps plugin_last_update on success but never calls "
                "_note_update_completed, so a plugin's fresh data would never "
                "be reported and Vegas would keep its stale cached content"
                % enclosing.name)

    @staticmethod
    def _enclosing_function(tree, target):
        best = None
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.lineno <= target.lineno <= (node.end_lineno or node.lineno):
                    if best is None or node.lineno > best.lineno:
                        best = node
        return best


if __name__ == "__main__":
    unittest.main(verbosity=2)
