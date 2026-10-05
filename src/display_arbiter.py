"""What the panel shows next: the Arbiter of docs/RUN_LOOP_REDESIGN.md.

``Arbiter.decide(state, inputs, now)`` takes a snapshot that
``DisplayController.run()`` gathers and returns a :class:`ScreenPlan` naming
the Source that gets the panel. It is a pure function: no I/O, no clock
reads (``now`` is passed in), no locks, and it changes nothing it is given.
That is what lets a plain table of cases test the priority order, which
used to exist only as the order of ``if`` blocks in ``run()``.

The order is

    ScheduledOff (a gate), Follower, OnDemand, Wifi, Live, Vegas, Rotation

Every Source but Vegas is decided here (stage 3). Vegas is the ``LEGACY``
plan: the Arbiter picks it, but its iteration is still run()'s own code
until stage 4.

A pass asks twice: once with the inputs every pass reads (the gate,
Follower, OnDemand, Wifi), and once more, only when nothing above the
notice took the panel, with the inputs the Sources below it need (whether
Vegas is on, the live-priority scan), read where run() always read them.

``decide(..., running=plan)`` is the other question, asked by the
ScreenRunner (src/screen_runner.py) at its service points: does a Source
in ``plan.preemptible_by`` now take the panel from the screen that is
running? The mid-screen rules are :func:`_hold_or_preempt`.

The state transitions (the next on-demand mode, a live claim and its
release, the rotation's step after a screen) are pure methods of
:class:`ArbiterState`; the controller applies what they return.
"""

from dataclasses import dataclass, replace
from enum import Enum
from typing import FrozenSet, Optional, Protocol, Tuple

__all__ = [
    "Arbiter",
    "ArbiterInputs",
    "ArbiterState",
    "FramePolicy",
    "LIVE_PREEMPTERS",
    "SCHEDULED_OFF_DWELL",
    "SCREEN_PREEMPTERS",
    "ScreenEnd",
    "ScreenPlan",
    "Source",
    "WIFI_NOTICE_DWELL",
    "WifiNotice",
    "live_pick",
    "live_takeover",
    "on_demand_bound",
    "rotation_plan",
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
    LIVE = "live"
    # Vegas: the Arbiter picks it, but its iteration is still run()'s own
    # code (and its interrupt callback a second copy of this order) until
    # stage 4 makes it a Source driven frame by frame.
    LEGACY = "legacy"
    ROTATION = "rotation"
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


class ScreenEnd(Protocol):
    """What ArbiterState.after needs to know about how a screen ended
    (screen_runner.Outcome, filled in by the controller)."""

    @property
    def on_demand_active(self) -> bool:
        """An on-demand session was running when the screen ended."""

    @property
    def still_live(self) -> bool:
        """The mode's plugin still had live content: hold the rotation."""


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
        rotation: The rotation's modes (``available_modes``).
        rotation_index: Where the rotation is (``current_mode_index``).
        live_resume_index: Where the rotation was when live priority took
            the panel, so it resumes there once nothing is live; None while
            live priority holds nothing.
        live_takeover_unshown: A mid-screen takeover chose current_mode and
            it has not been shown yet, so the next pass must not advance the
            live round-robin past it.
    """

    current_mode: Optional[str] = None
    on_demand_modes: Tuple[str, ...] = ()
    on_demand_index: int = 0
    on_demand_expires_at: Optional[float] = None
    on_demand_pinned: bool = False
    rotation: Tuple[str, ...] = ()
    rotation_index: int = 0
    live_resume_index: Optional[int] = None
    live_takeover_unshown: bool = False

    def next_on_demand(self) -> "ArbiterState":
        """The session's next mode, wrapping round. Needs a mode list."""
        index = (self.on_demand_index + 1) % len(self.on_demand_modes)
        return replace(self, on_demand_index=index,
                       current_mode=self.on_demand_modes[index])

    def claim_live(self, mode: str) -> "ArbiterState":
        """Live priority takes the panel for ``mode``.

        The rotation's position is saved only on the first claim, not on
        each re-check while the hold continues, so it resumes where live
        priority interrupted it instead of after the live mode (which would
        skip every mode between the two).
        """
        if self.current_mode == mode:
            return self
        resume = self.rotation_index if self.live_resume_index is None else self.live_resume_index
        index = self.rotation.index(mode) if mode in self.rotation else self.rotation_index
        return replace(self, current_mode=mode, rotation_index=index,
                       live_resume_index=resume)

    def after(self, outcome: "ScreenEnd") -> "ArbiterState":
        """The state once a screen has run its course: the next mode.

        An on-demand session moves to its next mode. Otherwise the rotation
        advances -- unless the mode just shown is a live-priority mode that
        is still live, which holds the panel. A session with no modes left
        is ended by the controller before it asks (that is not pure: it
        resumes the rotation and clears the cache).
        """
        if outcome.on_demand_active:
            return self.next_on_demand() if self.on_demand_modes else self
        if outcome.still_live or not self.rotation:
            return self
        index = (self.rotation_index + 1) % len(self.rotation)
        return replace(self, rotation_index=index, current_mode=self.rotation[index])

    def release_live(self) -> "ArbiterState":
        """Nothing is live any more: the rotation resumes where it was."""
        if self.live_resume_index is None or not self.rotation:
            return self
        index = self.live_resume_index % len(self.rotation)
        return replace(self, current_mode=self.rotation[index], rotation_index=index,
                       live_resume_index=None)

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
        live_modes: The modes with live content, from a live-priority scan,
            in registration order; None when no scan was made (on-demand,
            Vegas keeping live content in its ticker, a throttled
            mid-screen check). A scan asks every live-priority plugin, so it
            is made only where run() always made it.
        vegas_enabled: Vegas mode is on (and no on-demand session holds it
            off).
        vegas_live_in_ticker: Vegas keeps live content in its ticker
            instead of yielding the panel to it.
        vegas_yielded: This pass's Vegas iteration has run and yielded, so
            the Vegas Source passes and the screen it fell through to is
            decided.
        reload_pending: Mid-screen only: a plugin reload is waiting for the
            top of the loop, at a service point where that ends the screen.
    """

    schedule_on: bool
    on_demand_active: bool
    follower_active: bool
    wifi_notice: Optional[WifiNotice] = None
    live_modes: Optional[Tuple[str, ...]] = None
    vegas_enabled: bool = False
    vegas_live_in_ticker: bool = False
    vegas_yielded: bool = False
    reload_pending: bool = False


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
            None when the Source paces itself (a follower frame, Vegas), and
            for a rotation or live plan until its first frame.
        dynamic: Run until the plugin's cycle completes, between min and max.
        frame_policy: Which frame loop the screen runs.
        preemptible_by: The Sources that may end the screen mid-way.
        notice: The WiFi notice to draw, for a WIFI plan.
        deadline: For an on-demand plan, when the session ends (wall
            clock): after the first frame the screen's durations are cut to
            what is left (:func:`on_demand_bound`).
        ends_live: Nothing is live any more and live priority had
            interrupted the rotation: taking this plan resumes the rotation
            where it was (ArbiterState.release_live) before it shows.
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
    ends_live: bool = False


SCHEDULED_OFF_PLAN = ScreenPlan(Source.SCHEDULED_OFF, max_duration=SCHEDULED_OFF_DWELL)
FOLLOWER_PLAN = ScreenPlan(Source.FOLLOWER)
RELOAD_PLAN = ScreenPlan(Source.RELOAD)

#: What may end a screen mid-way: the schedule, an on-demand session
#: starting or ending, a WiFi notice, a live game, the rotation moving
#: under the screen, and a plugin reload. Not a follower or Vegas: those
#: are only looked at between screens.
SCREEN_PREEMPTERS: FrozenSet[Source] = frozenset(
    {Source.SCHEDULED_OFF, Source.ON_DEMAND, Source.WIFI, Source.LIVE, Source.ROTATION,
     Source.RELOAD})

#: A live screen is not preempted by Live: live games take turns between
#: screens, never mid-screen.
LIVE_PREEMPTERS: FrozenSet[Source] = SCREEN_PREEMPTERS - {Source.LIVE}


class Arbiter:
    """Decides which Source gets the panel. Stateless; see the module docstring."""

    @staticmethod
    def decide(state: ArbiterState, inputs: ArbiterInputs, now: float,
               running: Optional[ScreenPlan] = None) -> ScreenPlan:
        """The plan for this pass, from the Sources in priority order.

        With ``running``, the question is the ScreenRunner's at one of its
        service points instead: does a Source in ``running.preemptible_by``
        now take the panel from that screen? The answer is ``running``
        itself (the same object) while it holds, else the plan that ends it.
        See :func:`_hold_or_preempt` for the rules.

        Args:
            state: What the Arbiter remembers between passes.
            inputs: This pass's snapshot.
            now: Wall-clock time of the snapshot. The OnDemand Source reads
                it for what is left of a timed session, and the mid-screen
                WiFi rule (:func:`wifi_notice_preempts`) to compare with the
                notice's expiry. The top-of-pass WiFi check does not: it
                takes the notice as read.
            running: The screen on the panel, for a mid-screen check.

        Returns:
            The winning Source's plan (LEGACY for Vegas), or ``running``.
        """
        if running is not None:
            return _hold_or_preempt(state, inputs, now, running)

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

        # 4. Live: the next live game, round-robin across several. With
        # nothing live, a rotation that live priority interrupted resumes.
        ends_live = False
        if _live_applies(inputs):
            pick = live_pick(inputs.live_modes, state.current_mode,
                             advance=not state.live_takeover_unshown)
            if pick is not None:
                return ScreenPlan(Source.LIVE, mode=pick, preemptible_by=LIVE_PREEMPTERS)
            ends_live = state.live_resume_index is not None and bool(state.rotation)

        # 5. Vegas: one iteration of the ticker, run by run()'s own code
        # until stage 4. Passes once this pass's iteration has yielded.
        if inputs.vegas_enabled and not inputs.vegas_yielded:
            return ScreenPlan(Source.LEGACY, ends_live=ends_live)

        # 6. Rotation: the rotation's current mode (after the resume, when
        # live priority just ended).
        return rotation_plan(state.release_live() if ends_live else state,
                             ends_live=ends_live)


def _hold_or_preempt(state: ArbiterState, inputs: ArbiterInputs, now: float,
                     running: ScreenPlan) -> ScreenPlan:
    """The mid-screen rules: ``running``, or the plan that ends it.

    What the frame loops used to check one by one (_check_live_takeover,
    then _screen_preempted with _wifi_notice_pending in it, before stage 3),
    in their order:

    1. Live: a game went live while a non-live screen runs (the inputs
       carry a scan only when one was due, at most once a second). Checked
       first because it is the one preemption that changes the state -- the
       rotation moves to the live mode and remembers where it was -- and it
       still happens when a WiFi notice is also pending: the next pass then
       shows the notice, and the game after it.
    2. The panel's mode moved under the screen: an on-demand session
       started, ended or changed mode, or the rotation was rebuilt (a
       plugin enabled, disabled or reloaded).
    3. The schedule turned the panel off.
    4. A WiFi notice arrived (unless on-demand outranks it), compared with
       its expiry because the read throttle can hand back a stale one.
    5. A plugin reload is waiting at the top of the loop.

    A follower and Vegas are never mid-screen preemptions; they are looked
    at between screens.
    """
    by = running.preemptible_by
    if Source.LIVE in by:
        takeover = live_takeover(state, inputs)
        if takeover is not None:
            return ScreenPlan(Source.LIVE, mode=takeover, preemptible_by=LIVE_PREEMPTERS)
    if state.current_mode != running.mode:
        source = Source.ON_DEMAND if inputs.on_demand_active else Source.ROTATION
        if source in by:
            return ScreenPlan(source, mode=state.current_mode, preemptible_by=SCREEN_PREEMPTERS)
    if (Source.SCHEDULED_OFF in by and not inputs.schedule_on
            and not inputs.on_demand_active):
        return SCHEDULED_OFF_PLAN
    notice = inputs.wifi_notice
    if (Source.WIFI in by and notice is not None
            and wifi_notice_preempts(notice, inputs.on_demand_active, now)):
        return ScreenPlan(Source.WIFI, max_duration=WIFI_NOTICE_DWELL, notice=notice)
    if Source.RELOAD in by and inputs.reload_pending:
        return RELOAD_PLAN
    return running


def live_takeover(state: ArbiterState, inputs: ArbiterInputs) -> Optional[str]:
    """The live mode that takes the panel mid-screen, or None.

    The first live mode, when a scan found one and the panel is not on a
    live mode already. Never while on-demand holds the panel, while it is
    scheduled off, or while Vegas keeps live content in its ticker.
    """
    if not _live_applies(inputs) or inputs.on_demand_active or not inputs.schedule_on:
        return None
    live = inputs.live_modes
    if not live or state.current_mode in live:
        return None
    return live[0]


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


def rotation_plan(state: ArbiterState, ends_live: bool = False) -> ScreenPlan:
    """The Rotation Source: the mode the rotation is on.

    That is ``state.current_mode``, which is ``rotation[rotation_index]``
    except where something moved the panel off the list and the rotation
    carries on from there: a live mode no rotation entry names, or None
    when a session ended with no enabled mode to resume to.
    """
    return ScreenPlan(Source.ROTATION, mode=state.current_mode, ends_live=ends_live,
                      preemptible_by=SCREEN_PREEMPTERS)


def _live_applies(inputs: ArbiterInputs) -> bool:
    """Whether the Live Source has a say: a scan was made, and Vegas is not
    keeping live content in its ticker (where the live plugin takes extra
    turns in the marquee instead of the panel)."""
    if inputs.live_modes is None:
        return False
    return not (inputs.vegas_enabled and inputs.vegas_live_in_ticker)


def live_pick(live_modes: Optional[Tuple[str, ...]], current_mode: Optional[str],
              advance: bool) -> Optional[str]:
    """The live mode to show, or None when nothing is live.

    When several plugins are live at once this round-robins between them, so
    the panel alternates each dwell instead of pinning to the first one
    registered. The mode on the panel is the cursor, so this stays right as
    games start and end.

    Args:
        live_modes: The live modes, in registration order.
        current_mode: The mode on the panel.
        advance: True for the rotation's pick (the live mode after the one
            showing). False for a peek (the one showing if it is still live,
            else the first), which Vegas uses to ask whether anything is.
    """
    if not live_modes:
        return None
    if current_mode in live_modes:
        if advance:
            index = live_modes.index(current_mode)
            return live_modes[(index + 1) % len(live_modes)]
        return current_mode
    return live_modes[0]


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
