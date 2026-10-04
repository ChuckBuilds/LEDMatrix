"""What the panel shows next: the Arbiter of docs/RUN_LOOP_REDESIGN.md.

``Arbiter.decide(state, inputs, now)`` takes a snapshot that
``DisplayController.run()`` gathers once per pass and returns a
:class:`ScreenPlan` naming the Source that gets the panel. It is a pure
function: no I/O, no clock reads (``now`` is passed in), no locks, and it
changes nothing it is given. That is what lets a plain table of cases test
the priority order, which used to exist only as the order of ``if`` blocks
in ``run()``.

The full order is

    ScheduledOff (a gate), Follower, OnDemand, Wifi, Live, Vegas, Rotation

Stage 2 decides the gate, Follower and Wifi. Every other case returns a
``LEGACY`` plan, meaning "carry on with run()'s existing code" (live
priority, Vegas, then one rotation screen). OnDemand is in the order already
because it outranks the WiFi notice: an active session is a ``LEGACY`` plan
even when a notice is pending.

The Wifi Source's mid-screen rule, :func:`wifi_notice_preempts`, lives here
too, so both of its answers -- at the top of a pass and between frames --
come from one module.
"""

from dataclasses import dataclass, replace
from enum import Enum
from typing import FrozenSet, Optional, Tuple

__all__ = [
    "Arbiter",
    "ArbiterInputs",
    "ArbiterState",
    "FramePolicy",
    "SCHEDULED_OFF_DWELL",
    "ScreenPlan",
    "Source",
    "WIFI_NOTICE_DWELL",
    "WifiNotice",
    "on_demand_bound",
    "wifi_notice_preempts",
]

# How long one scheduled-off pass blanks the panel. The dwell ends early when
# on-demand starts or the schedule turns the panel back on.
SCHEDULED_OFF_DWELL = 60.0

# How long one WiFi-notice pass holds the notice before the next pass looks
# again; the notice stays up, pass after pass, until it expires.
WIFI_NOTICE_DWELL = 0.5


class Source(Enum):
    """Who gets the panel this pass."""

    SCHEDULED_OFF = "scheduled-off"
    FOLLOWER = "follower"
    ON_DEMAND = "on-demand"
    WIFI = "wifi"
    # Not decided by the Arbiter yet: on-demand, live priority, Vegas and the
    # rotation are still chosen by run()'s own code. Stage 3 adds the
    # OnDemand, Live and Rotation Sources; stage 4 adds Vegas.
    LEGACY = "legacy"
    # Not a screen: a plugin reload waits at the top of the loop. It ends a
    # screen between frames (the screen counts as shown and the rotation
    # moves on), and the next pass reloads before it draws.
    RELOAD = "reload"


class FramePolicy(Enum):
    """How often a screen draws: today's two frame loops (see
    DisplayController._needs_high_fps). Stage 5 lets plugins declare it."""

    #: The 125 Hz loop, paced to an 8 ms deadline: scrolling plugins.
    HIGH_FPS = "high-fps"
    #: The 1 Hz loop.
    STATIC = "static"


@dataclass(frozen=True)
class WifiNotice:
    """A WiFi status message waiting to be drawn.

    ``expires_at`` is wall-clock time (``time.time()``), as written by the
    WiFi manager.
    """

    message: str
    expires_at: float


@dataclass(frozen=True)
class ArbiterState:
    """What the Arbiter remembers between passes.

    A snapshot of the controller's own fields, taken when decide() is
    called (DisplayController._arbiter_state); the transitions below return
    the next state, and the controller writes it back.

    Attributes:
        current_mode: The mode on the panel or about to be
            (``current_display_mode``).
        on_demand_modes: The on-demand session's modes, in the order it
            shows them (a pinned mode already moved to the front when the
            session started, by _apply_on_demand_pin).
        on_demand_index: Which of them is showing.
        on_demand_expires_at: When the session ends (wall clock), or None
            for a session with no duration.
        on_demand_pinned: The session was started pinned. Carried for the
            snapshot; the pin itself is already in ``on_demand_modes``.
    """

    current_mode: Optional[str] = None
    on_demand_modes: Tuple[str, ...] = ()
    on_demand_index: int = 0
    on_demand_expires_at: Optional[float] = None
    on_demand_pinned: bool = False

    def next_on_demand(self) -> "ArbiterState":
        """The session's next mode, wrapping round. Needs a mode list."""
        index = (self.on_demand_index + 1) % len(self.on_demand_modes)
        return replace(self, on_demand_index=index,
                       current_mode=self.on_demand_modes[index])

    def showing(self, plan: "ScreenPlan") -> "ArbiterState":
        """The state once ``plan`` is on the panel.

        An on-demand plan puts the session's index on the mode it shows (an
        index past the end of a shortened list starts it again at 0).
        """
        state = replace(self, current_mode=plan.mode)
        if plan.source is Source.ON_DEMAND and self.on_demand_modes:
            state = replace(state, on_demand_index=_on_demand_index(self))
        return state


def _on_demand_index(state: ArbiterState) -> int:
    """The session's index, or 0 once it is past the end of its list."""
    index = state.on_demand_index
    return index if index < len(state.on_demand_modes) else 0


def on_demand_bound(min_duration: float, max_duration: float,
                    deadline: Optional[float],
                    now: float) -> Optional[Tuple[float, float]]:
    """Shorten a screen's (min, max) seconds to what is left of a timed
    on-demand session ending at ``deadline``. None when nothing is left.

    The OnDemand Source's bound, applied after the screen's first frame,
    where it always was (``now`` is read then).
    """
    if deadline is None:
        return min_duration, max_duration
    remaining = max(0.0, deadline - now)
    min_duration = min(min_duration, remaining)
    max_duration = min(max_duration, remaining)
    if max_duration <= 0:
        return None
    return min_duration, max_duration


@dataclass(frozen=True)
class ArbiterInputs:
    """One pass's snapshot, gathered by run() before it calls decide().

    Attributes:
        schedule_on: The display schedule has the panel on, not counting an
            on-demand override of a scheduled-off window.
        on_demand_active: An on-demand session is running.
        follower_active: A sync leader is driving this panel.
        wifi_notice: The pending WiFi notice, or None. run() reads it only
            when it could win (the panel is on, and neither a follower nor
            on-demand outranks it), because reading it has side effects: a
            1 Hz throttle and deleting an expired file.
    """

    schedule_on: bool
    on_demand_active: bool
    follower_active: bool
    wifi_notice: Optional[WifiNotice] = None


@dataclass(frozen=True)
class ScreenPlan:
    """The Arbiter's answer for one pass.

    decide() is pure, so it cannot ask a plugin anything: the fields a
    plugin answers (its durations, whether it runs a dynamic cycle, how
    often it draws) are filled in by the controller after the screen's first
    frame, when they have always been read (DisplayController.complete_plan).

    Attributes:
        source: The Source that gets the panel.
        mode: The display mode to draw (None for a blank, follower or notice).
        plugin: The id of the plugin drawing ``mode``, once resolved.
        min_duration: Seconds the screen runs at least (dynamic duration).
        max_duration: How long the plan holds the panel, in seconds, at most
            (its dwell ends early when what the panel should show changes).
            None when the Source paces itself: a follower frame, or LEGACY.
        dynamic: Run until the plugin's cycle completes, between min and max.
        frame_policy: Which frame loop the screen runs.
        preemptible_by: The Sources that may end the screen mid-way.
        notice: The WiFi notice to draw, for a WIFI plan.
        deadline: For an on-demand plan, when the session ends (wall
            clock): after the first frame the screen's durations are cut to
            what is left (:func:`on_demand_bound`).
    """

    source: Source
    mode: Optional[str] = None
    plugin: Optional[str] = None
    min_duration: Optional[float] = None
    max_duration: Optional[float] = None
    dynamic: bool = False
    frame_policy: Optional[FramePolicy] = None
    preemptible_by: FrozenSet[Source] = frozenset()
    notice: Optional[WifiNotice] = None
    deadline: Optional[float] = None


SCHEDULED_OFF_PLAN = ScreenPlan(Source.SCHEDULED_OFF, max_duration=SCHEDULED_OFF_DWELL)
FOLLOWER_PLAN = ScreenPlan(Source.FOLLOWER)
LEGACY_PLAN = ScreenPlan(Source.LEGACY)
RELOAD_PLAN = ScreenPlan(Source.RELOAD)

#: What may end a screen mid-way: the schedule, an on-demand session
#: starting or ending, a WiFi notice, a plugin reload, and run()'s own
#: changes of mode (live priority).
SCREEN_PREEMPTERS: FrozenSet[Source] = frozenset(
    {Source.SCHEDULED_OFF, Source.ON_DEMAND, Source.WIFI, Source.RELOAD, Source.LEGACY})


class Arbiter:
    """Decides which Source gets the panel. Stateless; see the module docstring."""

    @staticmethod
    def decide(state: ArbiterState, inputs: ArbiterInputs, now: float) -> ScreenPlan:
        """The plan for this pass, from the Sources in priority order.

        Args:
            state: What the Arbiter remembers between passes.
            inputs: This pass's snapshot.
            now: Wall-clock time of the snapshot. The OnDemand Source reads
                it for what is left of a timed session. The top-of-pass WiFi
                check does not: it takes the notice as read, and only the
                mid-screen check (:func:`wifi_notice_preempts`) compares it
                with the expiry.

        Returns:
            The winning Source's plan, or LEGACY_PLAN when the winner is one
            run() still decides itself.
        """
        # ScheduledOff is a gate, not a Source: a scheduled-off panel stays
        # blank even for a follower, and only an on-demand session overrides
        # it (#714 -- one ending in off hours blanks at the next pass).
        if not inputs.schedule_on and not inputs.on_demand_active:
            return SCHEDULED_OFF_PLAN

        # 1. Follower: a sync leader drives this panel, ahead of on-demand.
        if inputs.follower_active:
            return FOLLOWER_PLAN

        # 2. OnDemand: the session's current mode. It outranks the notice.
        if inputs.on_demand_active:
            return _on_demand_plan(state, now)

        # 3. Wifi: a pending notice, held for one short dwell per pass.
        if inputs.wifi_notice is not None:
            return ScreenPlan(Source.WIFI, max_duration=WIFI_NOTICE_DWELL,
                              notice=inputs.wifi_notice)

        # 4-6. Live, Vegas, Rotation: still run()'s own code.
        return LEGACY_PLAN


def _on_demand_plan(state: ArbiterState, now: float) -> ScreenPlan:
    """The OnDemand Source: the session's current mode.

    ``max_duration`` is what is left of a timed session at ``now`` (None
    without a duration); ``deadline`` carries the expiry so the bound can be
    applied again after the first frame. A session with no modes left (its
    plugin was unloaded under it) gets a plan with no mode: the controller
    ends the session and shows the rotation's mode instead.
    """
    modes = state.on_demand_modes
    if not modes:
        return ScreenPlan(Source.ON_DEMAND)
    expires_at = state.on_demand_expires_at
    remaining = None if expires_at is None else max(0.0, expires_at - now)
    return ScreenPlan(Source.ON_DEMAND, mode=modes[_on_demand_index(state)],
                      max_duration=remaining, deadline=expires_at,
                      preemptible_by=SCREEN_PREEMPTERS)


def wifi_notice_preempts(notice: Optional[WifiNotice], on_demand_active: bool,
                         now: float) -> bool:
    """Whether a WiFi notice should end the current screen early.

    The Wifi Source's mid-screen rule, polled between frames, during dwells
    and when a Vegas iteration yields. On-demand outranks the notice, as in
    :meth:`Arbiter.decide`. Unlike the top-of-pass check it also compares
    ``now`` with the expiry, because the 1 Hz read throttle can hand back a
    notice that has expired since it was read.
    """
    if on_demand_active or notice is None:
        return False
    return now < notice.expires_at
