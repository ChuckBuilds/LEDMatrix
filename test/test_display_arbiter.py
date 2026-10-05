"""Arbiter.decide (src/display_arbiter.py): the stage-2 Sources as tables.

decide() is pure, so every case is one row of (inputs) -> expected Source.
The rows are written out, not computed, so a change to the priority order
has to change a row here as well. The last class checks the controller's
side: the snapshot run() gathers gives the same scheduled-off answer as
is_display_active did before the Arbiter, through an on-demand session
that overrides the schedule and ends (#714).
"""

import itertools
from dataclasses import replace
import os
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("EMULATOR", "true")

from src import display_arbiter  # noqa: E402
from src.display_arbiter import (  # noqa: E402
    SCHEDULED_OFF_DWELL,
    SCREEN_PREEMPTERS,
    WIFI_NOTICE_DWELL,
    Arbiter,
    ArbiterInputs,
    ArbiterState,
    ScreenPlan,
    Source,
    WifiNotice,
    live_pick,
    live_takeover,
    on_demand_bound,
    wifi_notice_preempts,
)

NOTICE = WifiNotice(message="Connected to HomeNet", expires_at=1_000.0)

OFF = Source.SCHEDULED_OFF
FOLLOW = Source.FOLLOWER
WIFI = Source.WIFI
ONDEM = Source.ON_DEMAND
LEGACY = Source.LEGACY
ROTATION = Source.ROTATION

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
    (True, False, False, None, ROTATION),     # live / Vegas / rotation
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
        elif expected is ROTATION:
            # No live scan and Vegas off: the rotation's (empty) mode.
            assert plan == ScreenPlan(ROTATION, preemptible_by=SCREEN_PREEMPTERS)
        else:
            # A follower paces itself.
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

        assert step("22:59:30") is ROTATION
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
        assert step("07:00:30") is ROTATION


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


# -- Live (stage 3) -------------------------------------------------------

LIVE = Source.LIVE

# (live_modes, current_mode, advance) -> pick. _check_live_priority's rule.
PICK_TABLE = [
    ((), "clock", True, None),
    (None, "clock", True, None),
    (("nfl",), "clock", True, "nfl"),                 # not on a live mode: first
    (("nfl", "nhl"), "clock", True, "nfl"),
    (("nfl", "nhl"), "clock", False, "nfl"),
    (("nfl", "nhl"), "nfl", True, "nhl"),             # round-robin
    (("nfl", "nhl"), "nhl", True, "nfl"),             # wraps
    (("nfl", "nhl"), "nhl", False, "nhl"),            # a peek stays put
    (("nfl",), "nfl", True, "nfl"),                   # one game: itself
]


@pytest.mark.parametrize("live,current,advance,expected", PICK_TABLE)
def test_live_pick(live, current, advance, expected):
    assert live_pick(live, current, advance) == expected


def _below(live=None, vegas=False, keeps=False, yielded=False, on_demand=False):
    """Inputs for the Sources below the notice (no notice, no follower)."""
    return ArbiterInputs(schedule_on=True, on_demand_active=on_demand,
                         follower_active=False, live_modes=live, vegas_enabled=vegas,
                         vegas_live_in_ticker=keeps, vegas_yielded=yielded)


ROT = ("clock", "weather", "nfl_live", "nhl_live")


def _rot(current="clock", index=0, resume=None, unshown=False):
    return ArbiterState(current_mode=current, rotation=ROT, rotation_index=index,
                        live_resume_index=resume, live_takeover_unshown=unshown)


class TestLive:

    # (state, inputs) -> (source, mode, ends_live)
    TABLE = [
        # Nothing live, nothing to resume.
        (_rot(), _below(live=()), "below", None, False),
        # Not scanned (on-demand, or the ticker keeps live content).
        (_rot(), _below(live=None), "below", None, False),
        # A game is live: it takes the panel.
        (_rot(), _below(live=("nfl_live",)), LIVE, "nfl_live", False),
        # Two: round-robin from the one showing.
        (_rot("nfl_live", 2), _below(live=("nfl_live", "nhl_live")), LIVE, "nhl_live", False),
        # ... unless a mid-screen takeover chose it and it has not shown yet.
        (_rot("nfl_live", 2, resume=0, unshown=True),
         _below(live=("nfl_live", "nhl_live")), LIVE, "nfl_live", False),
        # Vegas keeps live content in its ticker: Live has no say at all,
        # not even the resume.
        (_rot(), _below(live=("nfl_live",), vegas=True, keeps=True), "below", None, False),
        (_rot("nfl_live", 2, resume=1),
         _below(live=(), vegas=True, keeps=True), "below", None, False),
        # Vegas that yields to live content: Live outranks it.
        (_rot(), _below(live=("nfl_live",), vegas=True), LIVE, "nfl_live", False),
        # The game ended: the interrupted rotation resumes.
        (_rot("nfl_live", 2, resume=1), _below(live=()), "below", None, True),
        (_rot("nfl_live", 2, resume=1), _below(live=(), vegas=True), "below", None, True),
        # On-demand outranks Live.
        (ArbiterState(on_demand_modes=("x",)), _below(live=("nfl_live",), on_demand=True),
         ONDEM, "x", False),
    ]

    @pytest.mark.parametrize("state,inputs,source,mode,ends_live", TABLE)
    def test_decide(self, state, inputs, source, mode, ends_live):
        plan = Arbiter.decide(state, inputs, 0.0)
        if source == "below":
            assert plan.source not in (LIVE, ONDEM, OFF, FOLLOW, WIFI)
        else:
            assert plan.source is source
            assert plan.mode == mode
        assert plan.ends_live is ends_live

    def test_a_live_plan_has_no_durations_until_its_first_frame(self):
        plan = Arbiter.decide(_rot(), _below(live=("nfl_live",)), 0.0)
        assert (plan.min_duration, plan.max_duration, plan.frame_policy) == (None, None, None)


class TestLiveTransitions:

    def test_claim_saves_where_the_rotation_was(self):
        nxt = _rot("weather", 1).claim_live("nfl_live")
        assert (nxt.current_mode, nxt.rotation_index, nxt.live_resume_index) == ("nfl_live", 2, 1)

    def test_a_second_claim_keeps_the_first_resume_point(self):
        nxt = _rot("nfl_live", 2, resume=1).claim_live("nhl_live")
        assert (nxt.current_mode, nxt.rotation_index, nxt.live_resume_index) == ("nhl_live", 3, 1)

    def test_claiming_the_mode_showing_changes_nothing(self):
        state = _rot("nfl_live", 2, resume=1)
        assert state.claim_live("nfl_live") is state

    def test_a_live_mode_outside_the_rotation_keeps_the_index(self):
        nxt = _rot("weather", 1).claim_live("mlb_live")
        assert (nxt.current_mode, nxt.rotation_index, nxt.live_resume_index) == ("mlb_live", 1, 1)

    def test_release_resumes_and_forgets(self):
        nxt = _rot("nhl_live", 3, resume=1).release_live()
        assert (nxt.current_mode, nxt.rotation_index, nxt.live_resume_index) == ("weather", 1, None)

    def test_release_wraps_a_resume_point_past_a_shortened_rotation(self):
        nxt = _rot("nhl_live", 3, resume=6).release_live()
        assert (nxt.current_mode, nxt.rotation_index) == ("nfl_live", 2)   # 6 % 4

    def test_release_with_nothing_to_resume_changes_nothing(self):
        state = _rot("clock", 0)
        assert state.release_live() is state
        empty = ArbiterState(current_mode="x", live_resume_index=2)
        assert empty.release_live() is empty      # no rotation to resume into


# -- Vegas and Rotation (stage 3) -----------------------------------------

class TestVegasAndRotation:

    # (state, inputs) -> (source, mode, ends_live). LEGACY now means Vegas only.
    TABLE = [
        (_rot("weather", 1), _below(live=()), ROTATION, "weather", False),
        (_rot("weather", 1), _below(live=None), ROTATION, "weather", False),
        (_rot("weather", 1), _below(live=(), vegas=True), LEGACY, None, False),
        (_rot("weather", 1), _below(live=None, vegas=True, keeps=True), LEGACY, None, False),
        # The iteration yielded: the screen it fell through to.
        (_rot("weather", 1), _below(live=(), vegas=True, yielded=True),
         ROTATION, "weather", False),
        (_rot("weather", 1), _below(live=("nfl_live",), vegas=True, yielded=True),
         LIVE, "nfl_live", False),
        # Live priority just ended: the rotation resumes where it was cut.
        (_rot("nhl_live", 3, resume=1), _below(live=()), ROTATION, "weather", True),
        # ... and Vegas carries the resume through to its own pass.
        (_rot("nhl_live", 3, resume=1), _below(live=(), vegas=True), LEGACY, None, True),
        # A rotation that something moved off its list carries on from there.
        (ArbiterState(current_mode=None, rotation=ROT), _below(live=()), ROTATION, None, False),
    ]

    @pytest.mark.parametrize("state,inputs,source,mode,ends_live", TABLE)
    def test_decide(self, state, inputs, source, mode, ends_live):
        plan = Arbiter.decide(state, inputs, 0.0)
        assert (plan.source, plan.mode, plan.ends_live) == (source, mode, ends_live)

    def test_a_rotation_plan_may_be_preempted_by_everything_a_screen_watches(self):
        plan = Arbiter.decide(_rot(), _below(live=()), 0.0)
        assert plan.preemptible_by == SCREEN_PREEMPTERS
        assert {OFF, ONDEM, WIFI, LIVE, ROTATION, Source.RELOAD} == SCREEN_PREEMPTERS


class _End:
    def __init__(self, on_demand_active=False, still_live=False):
        self.on_demand_active = on_demand_active
        self.still_live = still_live


class TestAfter:
    """ArbiterState.after: _advance_after_screen's step."""

    def test_the_rotation_advances(self):
        nxt = _rot("weather", 1).after(_End())
        assert (nxt.current_mode, nxt.rotation_index) == ("nfl_live", 2)

    def test_it_wraps(self):
        nxt = _rot("nhl_live", 3).after(_End())
        assert (nxt.current_mode, nxt.rotation_index) == ("clock", 0)

    def test_a_live_mode_still_live_holds(self):
        state = _rot("nfl_live", 2)
        assert state.after(_End(still_live=True)) is state

    def test_an_on_demand_session_moves_to_its_next_mode(self):
        state = replace(_session(index=1, current="b"), rotation=ROT, rotation_index=1)
        nxt = state.after(_End(on_demand_active=True))
        assert (nxt.current_mode, nxt.on_demand_index, nxt.rotation_index) == ("c", 2, 1)

    def test_a_session_with_no_modes_is_left_to_the_controller(self):
        state = ArbiterState(current_mode="x", rotation=ROT)
        assert state.after(_End(on_demand_active=True)) is state

    def test_no_rotation_no_step(self):
        state = ArbiterState(current_mode="x")
        assert state.after(_End()) is state


# -- Mid-screen: decide(..., running=plan) (stage 3) ----------------------
#
# What the ScreenRunner asks at each service point. These rows are what
# _check_live_takeover, _screen_preempted and _wifi_notice_pending answered
# between frames before stage 3, written out.

CLOCK = Arbiter.decide(ArbiterState(current_mode="clock"), _below(live=()), 0.0)
NFL = Arbiter.decide(ArbiterState(current_mode="clock"), _below(live=("nfl_live",)), 0.0)
OD = Arbiter.decide(_session(modes=("x", "y")), ON, 0.0)
FRESH = WifiNotice(message="AP mode", expires_at=1_000.0)

HELD = "held"


def _mid(on_demand=False, schedule_on=True, live=None, notice=None, reload=False):
    return ArbiterInputs(schedule_on=schedule_on, on_demand_active=on_demand,
                         follower_active=False, live_modes=live, wifi_notice=notice,
                         reload_pending=reload)


# (running, current_mode, inputs, now) -> HELD or (source, mode)
MID_TABLE = [
    # Nothing changed.
    (CLOCK, "clock", _mid(), 500.0, HELD),
    (CLOCK, "clock", _mid(live=()), 500.0, HELD),
    # A game went live: it takes the panel (the first live mode).
    (CLOCK, "clock", _mid(live=("nfl_live", "nhl_live")), 500.0, (LIVE, "nfl_live")),
    # ... even with a notice pending: the claim is made now, and the next
    # pass shows the notice first (top-of-pass order), then the game.
    (CLOCK, "clock", _mid(live=("nfl_live",), notice=FRESH), 500.0, (LIVE, "nfl_live")),
    # ... but not over the schedule or an on-demand session.
    (CLOCK, "clock", _mid(live=("nfl_live",), schedule_on=False), 500.0, (OFF, None)),
    (CLOCK, "x", _mid(live=("nfl_live",), on_demand=True), 500.0, (ONDEM, "x")),
    # A screen already on a live mode is not taken over by another.
    (CLOCK, "nfl_live", _mid(live=("nfl_live", "nhl_live")), 500.0, (ROTATION, "nfl_live")),
    (NFL, "nfl_live", _mid(live=("nhl_live",)), 500.0, HELD),
    # The mode moved under the screen: on-demand started, or ended, or the
    # rotation was rebuilt.
    (CLOCK, "x", _mid(on_demand=True), 500.0, (ONDEM, "x")),
    (OD, "weather", _mid(), 500.0, (ROTATION, "weather")),
    (CLOCK, "weather", _mid(), 500.0, (ROTATION, "weather")),
    # An on-demand session that ends on the same mode keeps the screen.
    (OD, "x", _mid(), 500.0, HELD),
    # The schedule: off ends it; an on-demand override holds.
    (CLOCK, "clock", _mid(schedule_on=False), 500.0, (OFF, None)),
    (OD, "x", _mid(on_demand=True, schedule_on=False), 500.0, HELD),
    # A WiFi notice, compared with its expiry; on-demand outranks it.
    (CLOCK, "clock", _mid(notice=FRESH), 999.9, (WIFI, None)),
    (CLOCK, "clock", _mid(notice=FRESH), 1_000.0, HELD),
    (NFL, "nfl_live", _mid(notice=FRESH), 500.0, (WIFI, None)),
    (OD, "x", _mid(on_demand=True, notice=FRESH), 500.0, HELD),
    # A plugin reload waits at the top of the loop.
    (CLOCK, "clock", _mid(reload=True), 500.0, (Source.RELOAD, None)),
    (OD, "x", _mid(on_demand=True, reload=True), 500.0, (Source.RELOAD, None)),
    # The order between them: a moved mode before the schedule, the
    # schedule before a notice, a notice before a reload.
    (CLOCK, "weather", _mid(schedule_on=False), 500.0, (ROTATION, "weather")),
    (CLOCK, "clock", _mid(schedule_on=False, notice=FRESH), 500.0, (OFF, None)),
    (CLOCK, "clock", _mid(notice=FRESH, reload=True), 500.0, (WIFI, None)),
]


class TestMidScreen:

    @pytest.mark.parametrize("running,current,inputs,now,expected", MID_TABLE)
    def test_decide(self, running, current, inputs, now, expected):
        state = ArbiterState(current_mode=current)
        plan = Arbiter.decide(state, inputs, now, running=running)
        if expected == HELD:
            assert plan is running
        else:
            assert plan is not running
            assert (plan.source, plan.mode) == expected

    def test_the_running_plans(self):
        assert (CLOCK.source, CLOCK.mode) == (ROTATION, "clock")
        assert (NFL.source, NFL.mode) == (LIVE, "nfl_live")
        assert LIVE not in NFL.preemptible_by
        assert (OD.source, OD.mode) == (ONDEM, "x")

    def test_a_follower_and_vegas_never_preempt(self):
        for plan in (CLOCK, NFL, OD):
            assert FOLLOW not in plan.preemptible_by
            assert LEGACY not in plan.preemptible_by

    def test_nothing_in_preemptible_by_means_nothing_preempts(self):
        bare = replace(CLOCK, preemptible_by=frozenset())
        inputs = _mid(live=("nfl_live",), schedule_on=False, notice=FRESH, reload=True)
        assert Arbiter.decide(ArbiterState(current_mode="weather"), inputs, 0.0,
                              running=bare) is bare

    def test_mid_screen_decide_reads_no_clock(self):
        boom = MagicMock(side_effect=AssertionError("decide read the clock"))
        with patch("time.time", boom), patch("time.monotonic", boom):
            for running, current, inputs, now, _ in MID_TABLE:
                Arbiter.decide(ArbiterState(current_mode=current), inputs, now,
                               running=running)


# (current, inputs) -> live_takeover. The mode a mid-screen check claims.
TAKEOVER_TABLE = [
    ("clock", _mid(live=("nfl_live",)), "nfl_live"),
    ("clock", _mid(live=()), None),
    ("clock", _mid(live=None), None),                       # no scan was due
    ("nfl_live", _mid(live=("nfl_live",)), None),
    ("clock", _mid(live=("nfl_live",), on_demand=True), None),
    ("clock", _mid(live=("nfl_live",), schedule_on=False), None),
    ("clock", replace(_mid(live=("nfl_live",)), vegas_enabled=True,
                      vegas_live_in_ticker=True), None),
]


@pytest.mark.parametrize("current,inputs,expected", TAKEOVER_TABLE)
def test_live_takeover(current, inputs, expected):
    assert live_takeover(ArbiterState(current_mode=current), inputs) == expected
