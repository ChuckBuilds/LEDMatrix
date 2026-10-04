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

from dataclasses import dataclass
from enum import Enum
from typing import FrozenSet, Optional

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

    Nothing yet: the stage-2 Sources decide from the inputs alone. The
    on-demand index, the rotation index and the live resume point move here
    with their Sources in stage 3.
    """


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


SCHEDULED_OFF_PLAN = ScreenPlan(Source.SCHEDULED_OFF, max_duration=SCHEDULED_OFF_DWELL)
FOLLOWER_PLAN = ScreenPlan(Source.FOLLOWER)
LEGACY_PLAN = ScreenPlan(Source.LEGACY)
RELOAD_PLAN = ScreenPlan(Source.RELOAD)

#: What may end a screen mid-way: the schedule, a WiFi notice, a plugin
#: reload, and run()'s own changes of mode (on-demand, live priority).
SCREEN_PREEMPTERS: FrozenSet[Source] = frozenset(
    {Source.SCHEDULED_OFF, Source.WIFI, Source.RELOAD, Source.LEGACY})


class Arbiter:
    """Decides which Source gets the panel. Stateless; see the module docstring."""

    @staticmethod
    def decide(state: ArbiterState, inputs: ArbiterInputs, now: float) -> ScreenPlan:
        """The plan for this pass, from the Sources in priority order.

        Args:
            state: What the Arbiter remembers between passes (nothing yet).
            inputs: This pass's snapshot.
            now: Wall-clock time of the snapshot. No stage-2 Source reads it:
                the top-of-pass WiFi check takes the notice as read, and only
                the mid-screen check (:func:`wifi_notice_preempts`) compares
                it with the expiry. It is in the signature for the Sources
                stage 3 adds (on-demand expiry, durations).

        Returns:
            The winning Source's plan, or LEGACY_PLAN when the winner is one
            run() still decides itself.
        """
        del state, now  # not read by the stage-2 Sources; see the docstring

        # ScheduledOff is a gate, not a Source: a scheduled-off panel stays
        # blank even for a follower, and only an on-demand session overrides
        # it (#714 -- one ending in off hours blanks at the next pass).
        if not inputs.schedule_on and not inputs.on_demand_active:
            return SCHEDULED_OFF_PLAN

        # 1. Follower: a sync leader drives this panel, ahead of on-demand.
        if inputs.follower_active:
            return FOLLOWER_PLAN

        # 2. OnDemand: decided by run() until stage 3. It outranks the notice.
        if inputs.on_demand_active:
            return LEGACY_PLAN

        # 3. Wifi: a pending notice, held for one short dwell per pass.
        if inputs.wifi_notice is not None:
            return ScreenPlan(Source.WIFI, max_duration=WIFI_NOTICE_DWELL,
                              notice=inputs.wifi_notice)

        # 4-6. Live, Vegas, Rotation: still run()'s own code.
        return LEGACY_PLAN


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
