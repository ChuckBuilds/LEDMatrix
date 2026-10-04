"""Arbiter.decide (src/display_arbiter.py): the stage-2 Sources as tables.

decide() is pure, so every case is one row of (inputs) -> expected Source.
The rows are written out, not computed, so a change to the priority order
has to change a row here as well. The last class checks the controller's
side: the snapshot run() gathers gives the same scheduled-off answer as
is_display_active did before the Arbiter, through an on-demand session
that overrides the schedule and ends (#714).
"""

import itertools
import os
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("EMULATOR", "true")

from src import display_arbiter  # noqa: E402
from src.display_arbiter import (  # noqa: E402
    SCHEDULED_OFF_DWELL,
    WIFI_NOTICE_DWELL,
    Arbiter,
    ArbiterInputs,
    ArbiterState,
    ScreenPlan,
    Source,
    WifiNotice,
    on_demand_bound,
    wifi_notice_preempts,
)

NOTICE = WifiNotice(message="Connected to HomeNet", expires_at=1_000.0)

OFF = Source.SCHEDULED_OFF
FOLLOW = Source.FOLLOWER
WIFI = Source.WIFI
ONDEM = Source.ON_DEMAND
LEGACY = Source.LEGACY

# (schedule_on, on_demand_active, follower_active, notice) -> Source.
# Every combination of the stage-2 inputs: 2 x 2 x 2 x 2 = 16 rows.
DECIDE_TABLE = [
    # The gate: scheduled off and no on-demand session -- blank, whatever
    # else is pending, a follower and a WiFi notice included.
    (False, False, False, None, OFF),
    (False, False, False, NOTICE, OFF),
    (False, False, True, None, OFF),
    (False, False, True, NOTICE, OFF),
    # Scheduled off, but on-demand overrides the gate.
    (False, True, False, None, ONDEM),        # on-demand overrides the gate
    (False, True, False, NOTICE, ONDEM),      # on-demand outranks WiFi
    (False, True, True, None, FOLLOW),        # follower outranks on-demand
    (False, True, True, NOTICE, FOLLOW),
    # Scheduled on.
    (True, False, False, None, LEGACY),       # live / Vegas / rotation
    (True, False, False, NOTICE, WIFI),
    (True, False, True, None, FOLLOW),
    (True, False, True, NOTICE, FOLLOW),      # follower outranks WiFi
    (True, True, False, None, ONDEM),
    (True, True, False, NOTICE, ONDEM),       # on-demand outranks WiFi
    (True, True, True, None, FOLLOW),
    (True, True, True, NOTICE, FOLLOW),
]


def _inputs(schedule_on, on_demand, follower, notice):
    return ArbiterInputs(schedule_on=schedule_on, on_demand_active=on_demand,
                         follower_active=follower, wifi_notice=notice)


def test_the_table_covers_every_combination_once():
    keys = [row[:4] for row in DECIDE_TABLE]
    combos = list(itertools.product([False, True], [False, True],
                                    [False, True], [None, NOTICE]))
    assert sorted(keys, key=repr) == sorted(combos, key=repr)


class TestDecide:

    @pytest.mark.parametrize("schedule_on,on_demand,follower,notice,expected",
                             DECIDE_TABLE)
    def test_source(self, schedule_on, on_demand, follower, notice, expected):
        plan = Arbiter.decide(ArbiterState(),
                              _inputs(schedule_on, on_demand, follower, notice),
                              500.0)
        assert plan.source is expected

    @pytest.mark.parametrize("schedule_on,on_demand,follower,notice,expected",
                             DECIDE_TABLE)
    def test_plan_fields(self, schedule_on, on_demand, follower, notice, expected):
        plan = Arbiter.decide(ArbiterState(),
                              _inputs(schedule_on, on_demand, follower, notice),
                              500.0)
        if expected is OFF:
            assert plan == ScreenPlan(OFF, max_duration=SCHEDULED_OFF_DWELL)
        elif expected is WIFI:
            assert plan == ScreenPlan(WIFI, max_duration=WIFI_NOTICE_DWELL,
                                      notice=NOTICE)
        elif expected is ONDEM:
            # An empty state: a session with no modes, which the
            # controller ends (see TestOnDemand for real sessions).
            assert plan == ScreenPlan(ONDEM)
        else:
            # A follower paces itself; LEGACY is run()'s existing code.
            assert plan == ScreenPlan(expected)

    def test_dwells_are_todays(self):
        # The constants that run() used to hard-code.
        assert SCHEDULED_OFF_DWELL == 60.0
        assert WIFI_NOTICE_DWELL == 0.5

    @pytest.mark.parametrize("now", [0.0, NOTICE.expires_at - 1,
                                     NOTICE.expires_at, NOTICE.expires_at + 1e6])
    def test_top_of_pass_wifi_takes_the_notice_as_read(self, now):
        """Before the Arbiter, the top of the pass drew whatever notice
        _check_wifi_status_message returned without comparing the expiry;
        decide() must not start comparing it with ``now``."""
        plan = Arbiter.decide(ArbiterState(), _inputs(True, False, False, NOTICE), now)
        assert plan.source is WIFI


class TestPurity:

    def test_decide_reads_no_clock(self):
        boom = MagicMock(side_effect=AssertionError("decide read the clock"))
        with patch("time.time", boom), patch("time.monotonic", boom), \
                patch("time.perf_counter", boom):
            for *key, expected in DECIDE_TABLE:
                assert Arbiter.decide(ArbiterState(), _inputs(*key), 1.0).source is expected

    def test_the_module_imports_no_io_or_clock(self):
        for name in ("time", "os", "datetime", "threading", "json", "pathlib"):
            assert not hasattr(display_arbiter, name), name

    def test_same_inputs_same_plan(self):
        for *key, _expected in DECIDE_TABLE:
            inputs = _inputs(*key)
            first = Arbiter.decide(ArbiterState(), inputs, 1.0)
            assert Arbiter.decide(ArbiterState(), inputs, 1.0) == first
            assert inputs == _inputs(*key)       # not mutated

    def test_inputs_and_plans_are_frozen(self):
        inputs = _inputs(True, False, False, NOTICE)
        with pytest.raises(Exception):
            inputs.schedule_on = False  # type: ignore[misc]
        plan = Arbiter.decide(ArbiterState(), inputs, 1.0)
        with pytest.raises(Exception):
            plan.source = OFF  # type: ignore[misc]


# (notice, on_demand_active, now) -> preempts. The mid-screen rule.
PREEMPT_TABLE = [
    (None, False, 0.0, False),
    (None, True, 0.0, False),
    (NOTICE, False, NOTICE.expires_at - 0.001, True),
    (NOTICE, False, NOTICE.expires_at, False),        # expired at expires_at
    (NOTICE, False, NOTICE.expires_at + 5, False),    # the throttle's stale copy
    (NOTICE, True, NOTICE.expires_at - 0.001, False),  # on-demand outranks it
    (NOTICE, True, NOTICE.expires_at + 5, False),
]


@pytest.mark.parametrize("notice,on_demand,now,expected", PREEMPT_TABLE)
def test_wifi_notice_preempts(notice, on_demand, now, expected):
    assert wifi_notice_preempts(notice, on_demand, now) is expected


class TestControllerSnapshot:
    """DisplayController._arbiter_inputs, on a stub controller."""

    def _controller(self, *, display_active=True, override=False, on_demand=False,
                    follower=False, status=None):
        from src.display_controller import DisplayController
        dc = object.__new__(DisplayController)
        dc.is_display_active = display_active
        dc.on_demand_schedule_override = override
        dc.on_demand_active = on_demand
        dc.sync_manager = MagicMock()
        dc.sync_manager.is_follower_active.return_value = follower
        dc._check_wifi_status_message = MagicMock(return_value=status)
        return dc

    def test_reads_the_notice_when_it_can_win(self):
        dc = self._controller(status={"message": "AP mode", "expires_at": 12,
                                      "timestamp": 7, "duration": 5})
        inputs = dc._arbiter_inputs()
        assert inputs == ArbiterInputs(schedule_on=True, on_demand_active=False,
                                       follower_active=False,
                                       wifi_notice=WifiNotice("AP mode", 12.0))

    @pytest.mark.parametrize("kwargs", [
        {"display_active": False},
        {"follower": True},
        {"on_demand": True, "override": True},
        {"on_demand": True},
    ])
    def test_does_not_read_the_notice_when_it_cannot_win(self, kwargs):
        """Reading it deletes an expired file and moves the 1 Hz throttle,
        which these passes never did before the Arbiter."""
        dc = self._controller(status={"message": "x", "expires_at": 1e12}, **kwargs)
        assert dc._arbiter_inputs().wifi_notice is None
        dc._check_wifi_status_message.assert_not_called()

    def test_an_on_demand_override_is_not_the_schedule(self):
        dc = self._controller(display_active=True, override=True, on_demand=True)
        assert dc._arbiter_inputs().schedule_on is False

    def test_the_gate_matches_is_display_active_through_an_on_demand_session(self):
        """Through _evaluate_schedule, as run() calls it: the Arbiter blanks
        exactly when is_display_active is False, which is what run() tested
        before. The schedule is 07:00-23:00."""
        from test.test_display_controller_schedule import at, make_controller
        dc = make_controller({"schedule": {"enabled": True, "start_time": "07:00",
                                           "end_time": "23:00"},
                              "timezone": "UTC"})
        dc.on_demand_active = False
        dc.on_demand_schedule_override = False
        dc.sync_manager = MagicMock()
        dc.sync_manager.is_follower_active.return_value = False
        dc._check_wifi_status_message = MagicMock(return_value=None)

        def step(time_str):
            p = at(time_str)
            try:
                dc._evaluate_schedule()
            finally:
                p.stop()
            plan = Arbiter.decide(ArbiterState(), dc._arbiter_inputs(), 0.0)
            assert (plan.source is OFF) is (not dc.is_display_active), time_str
            return plan.source

        assert step("22:59:30") is LEGACY
        assert step("23:00:00") is OFF                 # window ends
        dc.on_demand_active = True
        assert step("23:00:10") is ONDEM               # on-demand overrides
        assert dc.on_demand_schedule_override is True
        assert step("23:01:00") is ONDEM               # next minute, still on
        dc._reset_on_demand_fields()                   # session ends
        assert step("23:01:20") is OFF                 # same minute: blanks
        dc.on_demand_active = True
        assert step("06:59:00") is ONDEM
        assert step("07:00:00") is ONDEM               # schedule back on mid-session
        dc._reset_on_demand_fields()
        assert step("07:00:30") is LEGACY


# -- OnDemand (stage 3) ---------------------------------------------------

ON = ArbiterInputs(schedule_on=True, on_demand_active=True, follower_active=False)


def _session(modes=("a", "b", "c"), index=0, expires_at=None, current=None):
    return ArbiterState(current_mode=current, on_demand_modes=tuple(modes),
                        on_demand_index=index, on_demand_expires_at=expires_at)


class TestOnDemand:

    # (modes, index, expires_at, now) -> (mode, max_duration)
    TABLE = [
        (("a", "b", "c"), 0, None, 100.0, "a", None),       # untimed
        (("a", "b", "c"), 2, None, 100.0, "c", None),
        (("a", "b", "c"), 3, None, 100.0, "a", None),       # past the end: 0
        (("a", "b", "c"), 9, None, 100.0, "a", None),
        (("a",), 0, 130.0, 100.0, "a", 30.0),               # 30 s left
        (("a",), 0, 130.0, 130.0, "a", 0.0),                # none left
        (("a",), 0, 130.0, 200.0, "a", 0.0),                # never negative
    ]

    @pytest.mark.parametrize("modes,index,expires_at,now,mode,max_duration", TABLE)
    def test_current_mode_and_time_left(self, modes, index, expires_at, now, mode,
                                        max_duration):
        plan = Arbiter.decide(_session(modes, index, expires_at), ON, now)
        assert plan.source is ONDEM
        assert plan.mode == mode
        assert plan.max_duration == max_duration
        assert plan.deadline == expires_at

    def test_no_modes_left_is_a_plan_with_no_mode(self):
        plan = Arbiter.decide(_session(modes=()), ON, 0.0)
        assert plan == ScreenPlan(ONDEM)

    def test_preemptible_by_the_schedule_a_reload_and_its_own_changes(self):
        plan = Arbiter.decide(_session(), ON, 0.0)
        assert {Source.SCHEDULED_OFF, ONDEM, Source.RELOAD} <= plan.preemptible_by
        assert Source.FOLLOWER not in plan.preemptible_by

    @pytest.mark.parametrize("index,expected_index,expected_mode", [
        (0, 1, "b"), (1, 2, "c"), (2, 0, "a")])
    def test_next_on_demand_wraps(self, index, expected_index, expected_mode):
        nxt = _session(index=index).next_on_demand()
        assert (nxt.on_demand_index, nxt.current_mode) == (expected_index, expected_mode)

    def test_showing_a_plan_resets_an_index_past_the_end(self):
        state = _session(index=5, current="x")
        plan = Arbiter.decide(state, ON, 0.0)
        shown = state.showing(plan)
        assert (shown.on_demand_index, shown.current_mode) == (0, "a")
        assert state.on_demand_index == 5            # not mutated


# (min, max, deadline, now) -> bounds. The bound applied after the first frame.
BOUND_TABLE = [
    (10.0, 20.0, None, 0.0, (10.0, 20.0)),      # untimed: unchanged
    (10.0, 20.0, 100.0, 50.0, (10.0, 20.0)),    # plenty left
    (10.0, 20.0, 100.0, 85.0, (10.0, 15.0)),    # max cut to what is left
    (10.0, 20.0, 100.0, 95.0, (5.0, 5.0)),      # both cut
    (10.0, 20.0, 100.0, 100.0, None),           # nothing left
    (10.0, 20.0, 100.0, 150.0, None),
]


@pytest.mark.parametrize("min_d,max_d,deadline,now,expected", BOUND_TABLE)
def test_on_demand_bound(min_d, max_d, deadline, now, expected):
    assert on_demand_bound(min_d, max_d, deadline, now) == expected
