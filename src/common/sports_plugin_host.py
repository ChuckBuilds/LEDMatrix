"""The scoreboard plugin class's helpers every ``manager.py`` copies.

Each scoreboard's ``manager.py`` holds its ``BasePlugin`` subclass (the
"host": ``SoccerScoreboardPlugin``, ``UFCScoreboardPlugin``, ...). Ten of its
methods, and the class constant one of them reads, are identical
(executable AST, docstrings stripped, decorators compared) in all nine
scoreboards -- afl, baseball, basketball, football, hockey, lacrosse, nrl,
soccer and ufc -- and were copied here from ledmatrix-plugins ``56c4f15``
(origin/main, 2026-09-30) under their existing names:

- ``_dispatch_switch_refresh`` (with ``_SWITCH_REFRESH_MIN_GAP_SECONDS``) --
  run a manager's refresh on a daemon thread so ``display()`` never blocks
  on the network;
- ``get_vegas_priority_weight``, ``_favorite_team_is_live``,
  ``_favorite_scan_targets``, ``_favorite_scan_games`` and
  ``_game_involves`` -- how many Vegas slots the plugin asks for, and
  whether a configured favourite is playing live;
- ``get_vegas_content_type`` -- ``'multi'``: a scoreboard is a list of games;
- ``_dynamic_feature_enabled``, ``_get_total_games_for_manager`` and
  ``_build_manager_key`` -- small dynamic-duration helpers.

This is stage 4 of the consolidation (docs/SPORTS_UNIFICATION.md): the
families that needed no reconciling. The rest of ``manager.py`` has drifted
and is reconciled one family per release before it moves.

A new module rather than more methods on an existing mixin, for the reason
``sports_helpers`` gives: a missing module fails at load, where the version
checks see it; a missing method fails mid-frame.

WHAT A HOST MUST PROVIDE
------------------------
Derived by walking every ``self.<attr>`` the mixin reads; the host-contract
test in ``test/test_sports_plugin_host.py`` fails if a read is added without
being listed here.

- ``_ensure_manager_updated(manager)`` -- ``_dispatch_switch_refresh`` runs
  it on the thread it starts. It must swallow its own errors: nothing joins
  the thread.
- ``global_config``, ``has_live_priority()``, ``has_live_content()`` and
  ``supports_dynamic_duration()`` -- all on ``BasePlugin``; the scoreboards
  override the last three.
- ``is_enabled`` -- set by each scoreboard's ``__init__`` (``BasePlugin``
  calls its flag ``enabled``).
- ``_switch_refresh_threads`` and ``_switch_refresh_at``, read with
  ``getattr`` -- ``_dispatch_switch_refresh`` creates both on first use, so
  a host need not.
- The live managers it scans for favourites are found through ``vars(self)``
  (``_favorite_scan_targets``): any attribute, or value of a dict
  attribute, with ``favorite_teams`` (or ``favorite_fighters``) and
  ``live_games`` (or ``live_matches``, or an ``active_celebration`` dict
  holding a ``game``).

Add it as a base of the plugin class, **before** ``BasePlugin``, e.g.
``class SoccerScoreboardPlugin(SportsPluginHostMixin, BasePlugin)``:
``get_vegas_priority_weight`` and ``get_vegas_content_type`` override
``BasePlugin``'s defaults. A method on the plugin's own class still wins over
the mixin's. The mixin has no ``__init__`` and creates no class attributes
beyond its one constant.
"""

import logging
import threading
import time
from typing import Any, Callable, ClassVar, Dict, Iterator, Optional


class SportsPluginHostMixin:
    """The scoreboard plugin class's identical helpers. See module docstring."""

    # The host contract, declared for type checking only: these create no
    # attributes, so the host's own values are what the methods read.
    logger: logging.Logger
    is_enabled: bool
    global_config: Dict[str, Any]
    _ensure_manager_updated: Callable[[Any], Any]
    has_live_priority: Callable[[], bool]
    has_live_content: Callable[[], bool]
    supports_dynamic_duration: Callable[[], bool]
    # Created on first use by _dispatch_switch_refresh, per instance.
    _switch_refresh_threads: Dict[int, threading.Thread]
    _switch_refresh_at: Dict[int, float]

    #: Floor between two draw-time refresh dispatches for one manager. The
    #: manager's own update() still decides whether anything is fetched; this
    #: only stops display() starting a thread on every frame just to be told
    #: the interval has not elapsed.
    _SWITCH_REFRESH_MIN_GAP_SECONDS: ClassVar[float] = 5.0

    def _dispatch_switch_refresh(self, manager) -> None:
        """Run _ensure_manager_updated(manager) on a daemon thread.

        Called from display(), so it must not block: when an update is due,
        manager.update() fetches rankings and the schedule over the network,
        and doing that inline stalled the frame for the length of the round
        trip. The refreshed games land in the manager a few frames later --
        still within the manager's own interval, which is the freshness the
        switch path was missing.

        At most one refresh per manager runs at a time, and dispatches for the
        same manager are at least _SWITCH_REFRESH_MIN_GAP_SECONDS apart. Only
        the render thread touches the two bookkeeping dicts, so they need no
        lock; manager.update() stamps last_update before it fetches, so a
        concurrent background plugin.update() for the same manager returns
        early rather than fetching twice.
        """
        threads = getattr(self, "_switch_refresh_threads", None)  # type: Optional[Dict[int, threading.Thread]]
        if threads is None:
            threads = self._switch_refresh_threads = {}
        stamps = getattr(self, "_switch_refresh_at", None)  # type: Optional[Dict[int, float]]
        if stamps is None:
            stamps = self._switch_refresh_at = {}

        key = id(manager)
        running = threads.get(key)
        if running is not None and running.is_alive():
            return
        now = time.monotonic()
        last = stamps.get(key)
        if last is not None and now - last < self._SWITCH_REFRESH_MIN_GAP_SECONDS:
            return
        stamps[key] = now
        thread = threading.Thread(
            target=self._ensure_manager_updated,
            args=(manager,),
            daemon=True,
            name="SwitchRefresh-%s" % type(manager).__name__,
        )
        threads[key] = thread
        thread.start()

    # ---- Vegas weighting: is a favourite playing? -----------------------
    #
    # With display.vegas_scroll.live_in_ticker set, the marquee keeps running
    # through a live game and plugins can claim more than one slot per cycle.
    # The core already gives any plugin with live content `live_weight`; this
    # exists for the one thing the core cannot work out for itself, which is
    # *whose* game is live. See PLUGIN_API_REFERENCE, "Vegas scroll hooks",
    # and ADVANCED_FEATURES, "Live content in the ticker".

    def get_vegas_priority_weight(self):
        """Slots per Vegas cycle: more when a favorite team is playing.

        Returns None when nothing is live, which leaves the decision to the
        core rather than asserting a weight of 1 -- the core may have its own
        reason to boost this plugin later.
        """
        try:
            if not (self.has_live_priority() and self.has_live_content()):
                return None
            vegas = (self.global_config or {}).get('display', {}).get(
                'vegas_scroll', {})
            if self._favorite_team_is_live():
                return vegas.get('favorite_live_weight', 5)
            return vegas.get('live_weight', 3)
        except Exception:
            # Never let a weighting question break the rotation; the core
            # treats an exception as weight 1 anyway, and None says the same
            # thing more cheaply.
            return None

    def _favorite_team_is_live(self):
        """Whether any live game or fight involves a configured favorite.

        The sports plugins do not share one data shape, so this enumerates the
        real ones rather than assuming. An earlier version looked only for an
        attribute holding `live_games` alongside `favorite_teams`, which was
        true of five plugins and quietly false for four others -- they simply
        never reported a favorite, and no test noticed because the tests used
        the assumed shape rather than each plugin's own.

        Handled:

        * managers held directly on the plugin *and* inside a dict such as
          ``self._managers`` (nrl, afl)
        * ``live_games`` (most) and ``live_matches`` (cricket)
        * ``favorite_teams`` (most) and ``favorite_fighters`` (ufc)
        * identifiers ``home_abbr``/``away_abbr``, ``home_id``/``away_id``,
          ``fighter1_name``/``fighter2_name``, and cricket's nested
          ``teams: [{name, abbr, short_name}]``
        * ``active_celebration["game"]``, a snapshot the live manager keeps
          precisely because the game leaves ``live_games`` while the
          celebration is still on screen
        """
        for holder in self._favorite_scan_targets():
            favorites = (getattr(holder, 'favorite_teams', None)
                         or getattr(holder, 'favorite_fighters', None))
            if not favorites:
                continue
            wanted = {str(f).strip().lower() for f in favorites if f}
            if not wanted:
                continue
            for game in self._favorite_scan_games(holder):
                if self._game_involves(game, wanted):
                    return True
        return False

    def _favorite_scan_targets(self) -> Iterator[Any]:
        """Objects that might carry live content: attributes, and dict values.

        nrl and afl keep their per-league managers in a ``self._managers``
        dict, so walking attribute values alone finds the dict and stops.
        """
        for value in list(vars(self).values()):
            yield value
            if isinstance(value, dict):
                for nested in list(value.values()):
                    yield nested

    @staticmethod
    def _favorite_scan_games(holder) -> Iterator[Dict[str, Any]]:
        """Every game/fight on a holder that a favorite could be playing in."""
        for attr in ('live_games', 'live_matches'):
            for game in (getattr(holder, attr, None) or []):
                if isinstance(game, dict):
                    yield game
        celebration = getattr(holder, 'active_celebration', None)
        if isinstance(celebration, dict) and isinstance(celebration.get('game'), dict):
            yield celebration['game']

    @staticmethod
    def _game_involves(game, wanted) -> bool:
        """Whether a game/fight involves one of the wanted names."""
        for field in ('home_abbr', 'away_abbr', 'home_id', 'away_id',
                      'fighter1_name', 'fighter2_name'):
            value = game.get(field)
            if value is not None and str(value).strip().lower() in wanted:
                return True
        # Cricket nests its sides and matches on any of three names, by
        # substring -- "india" should match "India Women". Mirrors that
        # plugin's own _match_has_team rather than inventing a second rule.
        for team in (game.get('teams') or []):
            if not isinstance(team, dict):
                continue
            hay = " ".join(str(team.get(k) or '') for k in
                           ('name', 'abbr', 'short_name')).lower()
            if any(name in hay for name in wanted):
                return True
        return False

    def get_vegas_content_type(self) -> str:
        """Plugin provides multiple scrollable items (games)."""
        return 'multi'

    # ---- dynamic duration ------------------------------------------------

    def _dynamic_feature_enabled(self) -> bool:
        """Dynamic duration applies: the plugin is enabled and supports it."""
        if not self.is_enabled:
            return False
        return self.supports_dynamic_duration()

    @staticmethod
    def _get_total_games_for_manager(manager) -> int:
        """How many games a manager holds, from the first list it carries."""
        if manager is None:
            return 0
        for attr in ("live_games", "games_list", "recent_games", "upcoming_games"):
            value = getattr(manager, attr, None)
            if isinstance(value, list):
                return len(value)
        return 0

    @staticmethod
    def _build_manager_key(mode_name: str, manager) -> str:
        """``"<mode>:<manager class>"``, the key progress is tracked under."""
        manager_name = manager.__class__.__name__ if manager else "None"
        return f"{mode_name}:{manager_name}"


__all__ = ["SportsPluginHostMixin"]
