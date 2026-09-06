"""The on-demand request mailbox: how often it is read, and how it is consumed.

The mailbox is a cache key the web process writes and the display process
reads. Two properties matter and neither is obvious from the call site:

  * it is polled after every rendered frame, so an uncached read here is a
    disk read at frame rate;
  * consuming it must not throw away a request that arrived while the previous
    one was being processed.
"""

from unittest.mock import MagicMock

import pytest


class TestPollingIsBounded:
    """_poll_on_demand_requests runs ~125x/second on a scrolling mode.

    The read is deliberately uncached (memory_ttl=0) because a cached one
    pinned the first request for an hour. That makes the call a real disk read,
    so it needs a floor -- without one it was ~125 reads per second to find
    nothing at all.
    """

    def test_first_call_always_reads(self, test_display_controller):
        c = test_display_controller
        c.cache_manager.get = MagicMock(return_value=None)
        c._poll_on_demand_requests()
        assert c.cache_manager.get.call_count == 1

    def test_immediate_second_call_does_not_read(self, test_display_controller):
        c = test_display_controller
        c.cache_manager.get = MagicMock(return_value=None)
        c._poll_on_demand_requests()
        for _ in range(50):
            c._poll_on_demand_requests()
        assert c.cache_manager.get.call_count == 1, "polling was not bounded"

    def test_reads_again_once_the_interval_has_passed(self, test_display_controller, monkeypatch):
        c = test_display_controller
        c.cache_manager.get = MagicMock(return_value=None)
        clock = {"t": 1000.0}
        monkeypatch.setattr("src.display_controller.time.monotonic", lambda: clock["t"])

        c._poll_on_demand_requests()
        clock["t"] += c.ON_DEMAND_POLL_INTERVAL / 2
        c._poll_on_demand_requests()
        assert c.cache_manager.get.call_count == 1, "read before the interval elapsed"

        clock["t"] += c.ON_DEMAND_POLL_INTERVAL
        c._poll_on_demand_requests()
        assert c.cache_manager.get.call_count == 2

    def test_the_interval_is_short_enough_to_feel_instant(self, test_display_controller):
        # A person clicking in the web UI must not notice the floor.
        assert test_display_controller.ON_DEMAND_POLL_INTERVAL <= 0.5


class TestMailboxIsConsumedByIdentity:
    """Deleting whatever is in the mailbox loses a request that raced in."""

    def _arrange(self, controller, first, later):
        """Mailbox returns `first`, then `later` on the pre-delete re-read."""
        controller.on_demand_active = False
        controller.on_demand_request_id = None
        controller._last_on_demand_poll = None
        reads = iter([first, later])

        def fake_get(key, *a, **kw):
            if key == 'display_on_demand_request':
                return next(reads, later)
            return None  # processed-id lookup

        controller.cache_manager.get = MagicMock(side_effect=fake_get)
        controller.cache_manager.set = MagicMock()
        controller.cache_manager.delete = MagicMock()
        controller._activate_on_demand = MagicMock()

    REQ_A = {'request_id': 'A', 'action': 'start', 'plugin_id': 'p', 'mode': 'm'}
    REQ_B = {'request_id': 'B', 'action': 'start', 'plugin_id': 'p', 'mode': 'm'}

    def test_own_request_is_deleted(self, test_display_controller):
        c = test_display_controller
        self._arrange(c, self.REQ_A, self.REQ_A)
        c._poll_on_demand_requests()
        c.cache_manager.delete.assert_called_once_with('display_on_demand_request')

    def test_a_newer_request_is_left_for_the_next_poll(self, test_display_controller):
        c = test_display_controller
        self._arrange(c, self.REQ_A, self.REQ_B)
        c._poll_on_demand_requests()
        assert c.cache_manager.delete.call_count == 0, \
            "request B was deleted without ever being processed"

    def test_an_already_empty_mailbox_is_still_cleared(self, test_display_controller):
        c = test_display_controller
        self._arrange(c, self.REQ_A, None)
        c._poll_on_demand_requests()
        c.cache_manager.delete.assert_called_once_with('display_on_demand_request')

    def test_the_request_is_still_processed(self, test_display_controller):
        c = test_display_controller
        self._arrange(c, self.REQ_A, self.REQ_B)
        c._poll_on_demand_requests()
        c._activate_on_demand.assert_called_once()
