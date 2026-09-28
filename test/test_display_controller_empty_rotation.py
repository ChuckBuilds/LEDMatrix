"""The run loop must not spin when no mode has anything to show.

A mode with no content rotates to the next one at once. When every enabled
mode is empty (only a sports plugin enabled in its off-season, say), the loop
used to go round with no sleep at all: a full core, a plugin-executor thread
per pass and several log lines each time, forever. After one empty rotation it
now pauses between passes, and it stops pausing as soon as something shows.
"""

import os

os.environ.setdefault("EMULATOR", "true")

from src.display_controller import DisplayController  # noqa: E402


def _bare_controller(modes, on_demand_modes=None):
    dc = object.__new__(DisplayController)
    dc.available_modes = list(modes)
    dc.on_demand_active = on_demand_modes is not None
    dc.on_demand_modes = list(on_demand_modes or [])
    dc.sleeps = []
    dc._sleep_with_plugin_updates = lambda duration, tick_interval=1.0: dc.sleeps.append(duration)
    return dc


class TestNoteEmptyPass:
    def test_a_partly_empty_rotation_never_pauses(self):
        dc = _bare_controller(["a", "b", "c"])
        dc._note_empty_pass()
        dc._note_empty_pass()
        assert dc.sleeps == []

    def test_a_fully_empty_rotation_pauses_every_further_pass(self):
        dc = _bare_controller(["a", "b", "c"])
        for _ in range(5):
            dc._note_empty_pass()
        # Passes 3, 4 and 5 are at or past one full rotation.
        assert dc.sleeps == [DisplayController.EMPTY_ROTATION_PAUSE] * 3

    def test_content_resets_the_streak(self):
        dc = _bare_controller(["a", "b"])
        dc._note_empty_pass()
        dc._note_empty_pass()
        assert len(dc.sleeps) == 1
        dc._empty_pass_streak = 0  # what the run loop does when a mode shows
        dc._note_empty_pass()
        assert len(dc.sleeps) == 1

    def test_on_demand_counts_its_own_modes(self):
        dc = _bare_controller(["a", "b", "c", "d"], on_demand_modes=["x"])
        dc._note_empty_pass()
        assert dc.sleeps == [DisplayController.EMPTY_ROTATION_PAUSE]

    def test_a_single_mode_pauses_straight_away(self):
        dc = _bare_controller(["only"])
        dc._note_empty_pass()
        assert dc.sleeps == [DisplayController.EMPTY_ROTATION_PAUSE]


class TestRunLoopWithNothingToShow:
    def test_run_pauses_after_one_empty_rotation(self, test_display_controller):
        controller = test_display_controller
        # Modes with no plugin behind them take the "nothing to display" path.
        controller.available_modes = ["m1", "m2", "m3"]
        controller.current_mode_index = 0
        controller.plugin_modes = {}

        # Count passes at the top of the loop. The cap stops a spinning loop
        # (the old behaviour) instead of hanging the test run.
        passes = []
        original_poll = controller._poll_on_demand_requests

        def counting_poll():
            passes.append(1)
            if len(passes) > 50:
                raise RuntimeError("stop-test-loop: spinning")
            original_poll()

        sleeps = []

        def fake_sleep(duration, tick_interval=1.0):
            sleeps.append((len(passes), duration))
            if len(sleeps) >= 2:
                raise RuntimeError("stop-test-loop")

        controller._poll_on_demand_requests = counting_poll
        controller._sleep_with_plugin_updates = fake_sleep

        controller.run()

        # Three empty passes (one full rotation) before the first pause, then
        # one pause per further pass -- not thousands of passes a second.
        assert [n for n, _ in sleeps] == [3, 4]
        assert all(d == DisplayController.EMPTY_ROTATION_PAUSE for _, d in sleeps)


class TestRotationChanges:
    def test_a_new_rotation_starts_a_new_streak(self):
        # A long empty streak in a one-mode on-demand session must not make
        # the normal rotation pause before its own modes have been tried.
        dc = _bare_controller(["a", "b", "c"], on_demand_modes=["x"])
        for _ in range(4):
            dc._note_empty_pass()
        assert len(dc.sleeps) == 4
        dc.on_demand_active = False
        dc._note_empty_pass()
        dc._note_empty_pass()
        assert len(dc.sleeps) == 4
        dc._note_empty_pass()
        assert len(dc.sleeps) == 5

    def test_a_changed_mode_list_starts_a_new_streak(self):
        dc = _bare_controller(["a"])
        dc._note_empty_pass()
        assert len(dc.sleeps) == 1
        dc.available_modes = ["a", "b"]  # a plugin was enabled
        dc._note_empty_pass()
        assert len(dc.sleeps) == 1


class TestOnDemandDuringThePause:
    def test_the_requested_mode_is_shown_first(self, test_display_controller):
        controller = test_display_controller
        controller.available_modes = ["m1", "m2", "m3"]
        controller.current_mode_index = 0
        controller.plugin_modes = {}

        tried = []
        original_note = controller._note_empty_pass

        def recording_note():
            tried.append(controller.current_display_mode)
            if len(tried) > 50:
                raise RuntimeError("stop-test-loop: spinning")
            original_note()

        sleeps = []

        def fake_sleep(duration, tick_interval=1.0):
            sleeps.append(duration)
            if len(sleeps) == 1:
                # What the real pause does when an on-demand request lands:
                # activate it and return early.
                controller.on_demand_active = True
                controller.on_demand_modes = ["x", "y"]
                controller.on_demand_mode_index = 0
                controller.current_display_mode = "x"
            else:
                raise RuntimeError("stop-test-loop")

        controller._note_empty_pass = recording_note
        controller._sleep_with_plugin_updates = fake_sleep

        controller.run()

        # m1, m2, m3 empty -> pause -> on-demand starts; "x" must come next,
        # not be skipped by rotating the empty pass that was interrupted.
        assert tried[:3] == ["m1", "m2", "m3"]
        assert tried[3] == "x"
