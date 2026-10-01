"""Keep a live scoreboard's scrolling strip current without restarting it.

In scroll mode a scoreboard renders its games into one wide image and
scrolls it past the panel. The strip used to be rebuilt only when a cycle
completed, so a score changed mid-cycle stayed frozen in the pixels until the
marquee finished. Eight scoreboards -- afl, baseball, basketball, football,
hockey, lacrosse, nrl and soccer (ufc has no live strip) -- carry the same
fix in their ``manager.py``: fingerprint the live games, rebuild when the
fingerprint changes (rate-limited, and never for the clock alone), and keep
the marquee's position across the rebuild. Its eight methods and two class
constants are identical (executable AST, docstrings stripped, decorators
compared) in all eight and were copied here from ledmatrix-plugins
``56c4f15`` (origin/main, 2026-09-30) under their existing names:

- ``_live_scroll_managers`` -- the live managers whose games are on the strip;
- ``_refresh_live_scroll_managers`` -- let them refresh before they are
  fingerprinted, off the render thread;
- ``_live_scroll_fields``, ``_fingerprint_games`` and
  ``_live_scroll_fingerprint`` -- what the strip was drawn from;
- ``_live_scroll_needs_rebuild`` (with ``LIVE_SCROLL_REBUILD_MIN_SECONDS``
  and ``LIVE_SCROLL_REBUILD_DUTY_DIVISOR``) and ``_note_live_scroll_built``
  -- when to rebuild;
- ``_preserving_scroll_position`` -- a context manager that keeps the marquee
  where it was across a rebuild.

``LIVE_VOLATILE_FIELDS`` stays in each plugin: afl, nrl and soccer also
exclude ``period_text``, which embeds the clock in those sports.

A separate module from ``sports_plugin_host`` because ufc has no live strip:
it inherits that mixin and not this one, so none of this is in its MRO.

WHAT A HOST MUST PROVIDE
------------------------
Derived by walking every ``self.<attr>`` / ``cls.<attr>`` the mixin reads;
the host-contract test in ``test/test_sports_live_scroll.py`` fails if a read
is added without being listed here.

- ``LIVE_VOLATILE_FIELDS`` -- a class constant: the game-dict keys a rebuild
  ignores (the clock, and what the display pipeline adds).
- ``_live_scroll_fingerprints``, ``_live_scroll_rebuilt_at`` and
  ``_live_scroll_rebuild_cost`` -- empty dicts the host creates in
  ``__init__``, keyed by scroll key.
- ``logger``.
- ``_dispatch_switch_refresh(manager)`` -- from ``SportsPluginHostMixin``.
- ``_league_registry`` (``{league: {"enabled": bool, "managers": {"live":
  manager}}}``) or a ``_get_manager(mode_type)`` accessor, both read with
  ``getattr`` -- ``_live_scroll_managers``. A host with neither gets no
  managers, which leaves the feature inert rather than wrong.
- ``_scroll_manager``, read with ``getattr`` --
  ``_preserving_scroll_position`` asks it for the mode's scroll helper.

Add it as a base of the plugin class beside ``SportsPluginHostMixin``, before
``BasePlugin``: ``class SoccerScoreboardPlugin(SportsPluginHostMixin,
SportsLiveScrollMixin, BasePlugin)``. The two define no name in common. No
``__init__``; a method on the plugin's own class still wins over the mixin's.
"""

import logging
import time
from contextlib import contextmanager
from typing import Any, Callable, ClassVar, Dict, FrozenSet, Iterator, List


class SportsLiveScrollMixin:
    """Mid-cycle rebuilds of a live scroll strip. See module docstring."""

    # The host contract, declared for type checking only: these create no
    # attributes, so the host's own values are what the methods read.
    logger: logging.Logger
    LIVE_VOLATILE_FIELDS: ClassVar[FrozenSet[str]]
    _live_scroll_fingerprints: Dict[Any, Any]
    _live_scroll_rebuilt_at: Dict[Any, float]
    _live_scroll_rebuild_cost: Dict[Any, float]
    _dispatch_switch_refresh: Callable[[Any], None]

    #: Floor between mid-cycle strip rebuilds, and the duty-cycle cap that can
    #: raise it.
    #:
    #: A rebuild re-renders every card into one wide image, on the render
    #: thread, so the marquee is frozen for however long it takes. Measured on a
    #: Pi 4: 28ms for one game, 139ms for five, 435ms for fifteen. A fixed 5s
    #: floor is fine for one game and wrong for a full slate -- with fifteen
    #: live games a pitch lands somewhere every second or so, the fingerprint
    #: changes continuously, and 435ms every 5s is nearly a tenth of the time
    #: spent not scrolling.
    #:
    #: So the floor also scales with what the last rebuild actually cost: never
    #: spend more than 1/LIVE_SCROLL_REBUILD_DUTY_DIVISOR of wall time
    #: rebuilding. Fifteen games self-limits to a rebuild every ~8.7s; one game
    #: stays on the 5s floor. No per-sport tuning, and it adapts to slate size
    #: and panel width on its own.
    LIVE_SCROLL_REBUILD_MIN_SECONDS: ClassVar[float] = 5.0
    LIVE_SCROLL_REBUILD_DUTY_DIVISOR: ClassVar[float] = 20.0

    def _live_scroll_managers(self, league=None):
        """The live managers whose games are on the strip.

        Two shapes across the scoreboard lineage: a _league_registry (baseball,
        basketball, hockey, lacrosse, soccer, football) and a _get_manager
        accessor on the single-league plugins (afl, nrl). Anything else returns
        nothing, which leaves this feature inert rather than wrong.
        """
        registry = getattr(self, "_league_registry", None)
        if isinstance(registry, dict) and registry:
            managers = []
            for league_id, entry in registry.items():
                if league is not None and league_id != league:
                    continue
                entry = entry or {}
                if not entry.get("enabled", False):
                    continue
                manager = (entry.get("managers") or {}).get("live")
                if manager is not None:
                    managers.append(manager)
            return managers
        getter = getattr(self, "_get_manager", None)
        if callable(getter):
            try:
                # pylint: disable=not-callable
                # The lineages that lack _get_manager infer this as None, so a
                # static checker calls it uncallable. callable() above is the
                # runtime guard; the branch is simply dead in those plugins.
                manager = getter("live")
            except (AttributeError, KeyError, TypeError, ValueError, OSError):
                return []
            return [manager] if manager is not None else []
        return []

    def _refresh_live_scroll_managers(self, league=None) -> None:
        """Let the live managers refresh before their games are fingerprinted.

        Switch mode stays current because _try_manager_display() calls
        _ensure_manager_updated() on every pass. Scroll mode had no equivalent:
        its only refresh sat inside the block gated by the rebuild decision, and
        that decision is computed from the data the refresh would replace. So
        once the first strip was built nothing could change it, and the score on
        the marquee stayed frozen until the process restarted.

        The refresh runs off the render thread -- see _dispatch_switch_refresh().
        This is called on every scroll frame, and a due manager.update() is a
        network round trip: run inline, it froze the marquee for the length of
        the ESPN request. The refreshed games land a few frames later, and the
        fingerprint check that follows this call picks them up on the next frame
        after they do. Dispatches for a manager are rate-limited, so the frames
        where nothing is due cost a dict lookup and a clock read.

        Deliberately NOT gated on mode_type == "live". A recent/upcoming strip
        never rebuilds from the fingerprint (_live_scroll_needs_rebuild returns
        early for those), so refreshing here looks like wasted work -- but with
        live_priority the plugin only switches TO live mode once it knows live
        games exist, and it learns that from these same managers. Refreshing
        only while live mode is on screen would rebuild the same circularity one
        level up, and a game that went live would wait for the background
        plugin update -- an hour, on a rig that sets update_interval: 3600.
        """
        for manager in self._live_scroll_managers(league) or []:
            try:
                self._dispatch_switch_refresh(manager)
            except (AttributeError, KeyError, TypeError, ValueError, OSError,
                    RuntimeError) as exc:
                # Narrow on purpose: the update itself runs on another thread,
                # and _ensure_manager_updated() swallows whatever it raises, so
                # anything arriving here is a lookup error or a thread that
                # could not be started, not a fetch failure.
                self.logger.debug("Live scroll refresh skipped: %s", exc)

    @classmethod
    def _live_scroll_fields(cls, game) -> tuple:
        """One game as sorted ``(key, value)`` strings, minus the volatile keys."""
        try:
            items = list(game.items())
        except AttributeError:
            return (("<not-a-dict>", str(game)),)
        return tuple(sorted((str(k), str(v)) for k, v in items
                            if k not in cls.LIVE_VOLATILE_FIELDS))

    @classmethod
    def _fingerprint_games(cls, games) -> tuple:
        """Order-independent fingerprint of a list of games."""
        return tuple(sorted(cls._live_scroll_fields(g) for g in (games or [])))

    def _live_scroll_fingerprint(self, league=None) -> tuple:
        """Fingerprint of every live game the strip's managers hold now."""
        games: List[Any] = []
        for manager in self._live_scroll_managers(league):
            games.extend(getattr(manager, "live_games", None) or [])
        return self._fingerprint_games(games)

    def _live_scroll_needs_rebuild(self, scroll_key, mode_type, league=None) -> bool:
        """True when the live card would draw differently than the strip does.

        _scroll_prepared is cleared only when the cycle *completes*, so a score
        scored mid-cycle stayed frozen in the rendered strip until the marquee
        finished -- minutes, for a long game list. Restarting the display forces
        a rebuild, which is the workaround users find.
        """
        if mode_type != "live":
            return False
        known = self._live_scroll_fingerprints.get(scroll_key)
        if known is None:
            return False                      # nothing built yet; normal path
        if self._live_scroll_fingerprint(league) == known:
            return False
        last = self._live_scroll_rebuilt_at.get(scroll_key, 0.0)
        cost = self._live_scroll_rebuild_cost.get(scroll_key, 0.0)
        floor = max(self.LIVE_SCROLL_REBUILD_MIN_SECONDS,
                    cost * self.LIVE_SCROLL_REBUILD_DUTY_DIVISOR)
        if time.time() - last < floor:
            return False                      # deferred, not dropped
        return True

    def _note_live_scroll_built(self, scroll_key, mode_type, fingerprint=None,
                                league=None) -> None:
        """Record what the strip was built from.

        Takes a fingerprint captured from the *managers* immediately before the
        render, not one computed from the games handed to the renderer. Those
        two are not comparable: _collect_games_for_scroll() decorates each game
        with extra keys ("league", "status"), so a fingerprint taken from its
        output can never equal one taken from the managers -- every check past
        the rate limiter would rebuild, defeating the clock exclusion entirely.
        That is not hypothetical; it is what the first version of this did, and
        an end-to-end simulation caught it rebuilding on a bare clock tick.

        Capturing before the render also closes the race a plain re-read would
        open: a background update landing mid-render would otherwise be recorded
        as though the strip already contained it.
        """
        if mode_type != "live":
            return
        self._live_scroll_fingerprints[scroll_key] = (
            fingerprint if fingerprint is not None
            else self._live_scroll_fingerprint(league))
        self._live_scroll_rebuilt_at[scroll_key] = time.time()

    @contextmanager
    def _preserving_scroll_position(self, mode_type, active, scroll_key=None) -> Iterator[None]:
        """Keep the marquee where it is across a mid-cycle rebuild.

        ScrollHelper.set_scrolling_image() resets two counters and both matter:
        scroll_position (without it the marquee snaps back to the start, which
        looks worse than the stale score being fixed) and total_distance_scrolled
        (without it the cycle restarts, so a game that keeps scoring could stop
        the strip ever completing). Restored clamped to the new strip, since a
        score gaining a digit changes its card's width by a few pixels.

        A no-op unless `active` -- a first build should start at zero.
        """
        helper = None
        if active and getattr(self, "_scroll_manager", None):
            try:
                helper = self._scroll_manager.get_scroll_display(mode_type).scroll_helper  # type: ignore[attr-defined]
            except Exception:  # pragma: no cover - defensive
                helper = None
        position = getattr(helper, "scroll_position", None) if helper else None
        distance = getattr(helper, "total_distance_scrolled", None) if helper else None
        started = time.time()
        try:
            yield
        finally:
            # What this render cost, so the next floor can scale with it. Keyed by
            # scroll_key, which is what _live_scroll_needs_rebuild() reads --
            # they are only the same string in some of these plugins, and keying
            # by mode_type made the duty cap silently inert in the rest.
            self._live_scroll_rebuild_cost[scroll_key or mode_type] = time.time() - started
            if helper is not None and position is not None:
                width = max(getattr(helper, "total_scroll_width", 0) - 1, 0)
                helper.scroll_position = min(position, width)
                if distance is not None:
                    helper.total_distance_scrolled = distance
                helper.scroll_complete = False
                self.logger.info(
                    "[Scroll] Live card changed; rebuilt the %s strip in place "
                    "at position %d", mode_type, int(helper.scroll_position))


__all__ = ["SportsLiveScrollMixin"]
