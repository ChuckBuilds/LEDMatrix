"""Which non-favourite games a scoreboard shows, and when the slice moves (sports family 7).

The scoreboards' other-games rotation, reconciled in ledmatrix-plugins
(family 7) from two bodies each into one, and copied here under the existing
names. All of it lives on the plugins' ``SportsCore``, so there is one mixin,
``SportsRotationMixin``:

- ``_by_importance(games, newest_first)``: the non-favourite pool, best
  matchup first and one game per team, when a poll has loaded; kickoff order
  otherwise. ``SportsCoreSharedMixin._favorites_first`` asks it for both the
  filtered and the unfiltered pool.
- ``_other_games_window(others, limit)``: the slice of a pool on screen now.
  It advances by its own width every ``other_rotation_interval_seconds``
  (catching up on intervals that passed unseen) and wraps.
  ``SportsCoreSharedMixin._compose_selection`` cuts with it.
- ``_advance_other_games_if_due`` and ``_rotate_other_games_on_display``:
  the display path's re-cut between fetches. The plugins' ``display()`` calls
  ``_rotate_other_games_on_display`` before its dwell check; the card on
  screen keeps its place if it survived the cut.
- ``_attach_odds_to_rotated_games``: odds for the games a rotation
  brought in, on a daemon thread, when ``show_odds`` is on and there is an
  odds manager.
- ``_rankings_loaded()``: the override point (below).

THE RULES
---------
``update()`` (through ``_favorites_first``) and ``display()`` (through
``_rotate_other_games_on_display``) both advance the window, so the advance
holds ``_games_lock`` (the plugins' RLock): interleaved without it, both saw
the interval elapse and each added a width, skipping a window nobody saw.

The display path's due-check looks at the pool ``_compose_selection`` will
actually cut from: the filtered others, or, when nothing survived at all (no
favourite to show either), the unfiltered fallback. Guessing the unfiltered
pool whenever the others were empty recomposed an identical list on every
frame while a favourite was playing; never looking at it left the fallback
moving only when ``update()`` ran.

Rotated-in games get odds by the same rule as the games ``update()`` picks:
``show_odds`` on (decided 2026-10-09, which brought ufc-scoreboard in line).

OVERRIDE POINT
--------------
``_rankings_loaded()`` -- did a poll load? ``_by_importance`` keeps kickoff
order when not. The default is ``_team_rankings_cache`` being non-empty.
football-scoreboard overrides it to count its rankings keyed by ESPN team id
(``_ranked_team_ids``) as well, the table its ``_best_rank`` reads first.

A new module rather than more methods on ``sports_shared``, for the reason
``sports_helpers`` gives: a missing module fails at load, where the version
checks see it; a missing method fails mid-update.

WHAT A HOST MUST PROVIDE
------------------------
Derived by walking every ``self.<attr>`` the mixin reads; the host-contract
test in ``test/test_sports_rotation.py`` fails if a read is added without being
listed here. Every scoreboard ``SportsCore`` supplies all of them.

- ``other_rotation_interval_seconds`` -- seconds per window; 0 pins it.
- ``_other_window_start`` and ``_other_window_rotated_at`` -- the window's
  position and the monotonic time it last moved (0: never cut). Written here.
- ``_games_lock`` -- the re-entrant lock around ``games_list``.
- ``_selection_pools`` -- what ``_favorites_first`` settled (``favorites``,
  ``others``, ``unfiltered``, ``favorite_limit``, ``other_limit``); read with
  getattr, so a manager that never built it does not rotate.
- ``_compose_selection`` -- from ``SportsCoreSharedMixin``.
- ``_best_rank`` -- a game's better poll position, 99 if neither side ranks
  (family 8, still per-plugin).
- ``_team_rankings_cache`` -- read with getattr by the default
  ``_rankings_loaded``.
- ``games_list``, ``current_game``, ``current_game_index``,
  ``last_game_switch`` -- the switch-mode state a rotation swaps.
- ``logger`` -- one INFO line per rotation.
- ``show_odds`` and ``odds_manager`` (read with getattr), ``mode_config``
  (``odds_update_interval``), ``sport``, ``league`` and ``sport_key`` -- the
  rotated-in odds fetch.

The methods read the game dict's ``id``, ``start_time_utc``, ``home_abbr``,
``away_abbr`` and ``odds``; any may be missing.

BASE ORDER
----------
No other mixin defines these methods, so the position in the bases does not
change which body runs; a method on the plugin's own class (football's
``_rankings_loaded``) still wins. The mixin has no ``__init__``; the state it
writes is the host's.
"""

import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional


class SportsRotationMixin:
    """``SportsCore``'s other-games rotation. See module docstring."""

    # The host contract, declared for type checking only.
    other_rotation_interval_seconds: int
    _other_window_start: int
    _other_window_rotated_at: float
    _games_lock: Any
    _compose_selection: Callable[[], List[Dict]]
    _best_rank: Callable[[Dict], int]
    games_list: List[Dict]
    current_game: Optional[Dict]
    current_game_index: int
    last_game_switch: float
    logger: Any
    odds_manager: Any
    mode_config: Dict[str, Any]
    sport: str
    league: str
    sport_key: str

    def _rankings_loaded(self) -> bool:
        """Did a poll load at all? The ranking reads fail open when not.

        The seam ``_by_importance`` asks before ordering by rank: by default
        the abbreviation table (``_team_rankings_cache``). football-scoreboard
        overrides it to count its table keyed by ESPN team id as well.
        """
        return bool(getattr(self, "_team_rankings_cache", None))

    def _by_importance(self, games: List[Dict], newest_first: bool = False) -> List[Dict]:
        """Non-favourite games, best matchup first.

        The quality filter already declares the poll to be the thing worth
        showing -- and then selection ignored the number entirely. #1 against #2
        and #25 against an unranked side were interchangeable, and whichever
        kicked off sooner took the slot, so the biggest game of the week had no
        better chance of being seen than any other.

        The rotation still walks the entire pool, so nothing is lost and
        coverage is unchanged; it now walks DOWN the ladder instead of along the
        clock. The first window after a restart holds the best games available
        rather than the earliest ones, which is the case that matters -- a board
        is far more often freshly started or freshly updated than three hours
        into a lap.

        Ties fall back to kickoff order, and a league with no poll keeps the
        chronological order it had, because there is nothing to sort on.

        One game per team, which is the part rank ordering cannot do without.
        The upcoming pool is not a week of fixtures -- for college football it
        is the whole season, 947 games on a real board -- so ordering by rank
        alone put all twelve of the #1 team's games above the #2 team's first
        one, and the board walked one team's season. Measured on ledpi the
        moment this shipped: KENT@OSU, ILL@OSU, then OSU@IOWA, MD@OSU. Keeping
        only the soonest game per team makes the pool "what each team has
        next", which is both what an upcoming board means and inherently
        near-term, since a team's next game is by definition the closest one.
        """
        if not self._rankings_loaded():
            return games
        if newest_first:
            def key(game):
                when = game.get("start_time_utc") or datetime.min.replace(tzinfo=timezone.utc)
                return (self._best_rank(game), -when.timestamp())
        else:
            def key(game):
                when = game.get("start_time_utc") or datetime.max.replace(tzinfo=timezone.utc)
                return (self._best_rank(game), when.timestamp())

        # Soonest-first so "one per team" keeps each team's NEXT game, then
        # re-ordered by rank. Doing it the other way round would keep whichever
        # of a team's games happened to sort first by rank, which for a game
        # between two ranked sides is not necessarily the next one.
        soonest_first = sorted(
            games,
            key=lambda g: (g.get("start_time_utc")
                           or datetime.max.replace(tzinfo=timezone.utc)).timestamp(),
            reverse=newest_first,
        )
        seen, once_each = set(), []  # type: ignore[var-annotated]
        for game in soonest_first:
            sides = (game.get("home_abbr"), game.get("away_abbr"))
            if any(side in seen for side in sides):
                continue
            seen.update(s for s in sides if s)
            once_each.append(game)
        return sorted(once_each, key=key)

    def _other_games_window(self, others: List[Dict], limit: int) -> List[Dict]:
        """A rotating slice of the non-favourite games.

        The window advances by its own width, so consecutive windows are
        disjoint and the board walks the schedule rather than resampling the
        same front of it. It wraps, so a short list still cycles.

        Advancing is time-based, not per-update. update() runs every 30s; if
        the window moved with it the games list would change identity on every
        pass, reset the display index, and no card past the first would ever be
        reached.
        """
        if limit <= 0 or not others:
            return []
        if len(others) <= limit:
            return others[:limit]

        interval = self.other_rotation_interval_seconds
        # Under the lock: update() advances this window through
        # _favorites_first, and display() advances it through
        # _rotate_other_games_on_display, so the read-modify-write below has two
        # writers. Interleaved, both can see the interval elapsed and each add a
        # width, skipping a window of games nobody ever sees. _games_lock is an
        # RLock and the display path takes it again straight after, which is
        # why this can be the same lock rather than another one to reason about.
        with self._games_lock:
            if interval > 0:
                now = time.monotonic()
                if not self._other_window_rotated_at:
                    self._other_window_rotated_at = now
                elapsed = now - self._other_window_rotated_at
                if elapsed >= interval:
                    # Advance by however many intervals actually passed. The
                    # board is not guaranteed to be running -- or this mode
                    # displayed -- for every one of them, and stepping once
                    # would let a plugin that sat idle crawl a step at a time.
                    steps = int(elapsed // interval)
                    self._other_window_start += steps * limit
                    self._other_window_rotated_at = now

            start = self._other_window_start % len(others)
        window = others[start:start + limit]
        if len(window) < limit:
            window += others[:limit - len(window)]
        return window

    def _rotate_other_games_on_display(self) -> bool:
        """Swap in a freshly cut slice when the rotation interval has passed.

        Returns True when the list changed, so the caller forces a redraw.

        The card currently on screen keeps its place if it survived the cut:
        rotating the pool should change what comes NEXT, not interrupt whatever
        someone is reading. Only when it is gone does the index reset, and then
        the dwell resets with it so the replacement gets a full turn rather than
        the tail of its predecessor's.
        """
        rebuilt = self._advance_other_games_if_due()
        if not rebuilt:
            return False
        with self._games_lock:
            if [g.get("id") for g in rebuilt] == [g.get("id") for g in self.games_list]:
                return False
            current_id = (self.current_game or {}).get("id")
            self.games_list = rebuilt
            for index, game in enumerate(rebuilt):
                if game.get("id") == current_id:
                    self.current_game_index = index
                    self.current_game = game
                    break
            else:
                self.current_game_index = 0
                self.current_game = rebuilt[0]
                self.last_game_switch = time.time()
            self.logger.info(
                "Rotated the other-games slice to: %s",
                ", ".join("%s@%s" % (g.get("away_abbr"), g.get("home_abbr"))
                          for g in rebuilt),
            )
        self._attach_odds_to_rotated_games(rebuilt)
        return True

    def _attach_odds_to_rotated_games(self, games: List[Dict]) -> None:
        """Fetch odds for freshly rotated-in games off the display path.

        The rotation deliberately does no network work, but odds are only
        attached in update(), and for an upcoming list that runs hourly --
        far longer than any rotated-in card stays on screen. Every slice cut
        between updates therefore rendered without a line even though ESPN
        had one, while the favourites, which survive every cut, kept the
        odds update() gave them.

        One daemon thread per rotation, bounded by the slice size rather
        than the pool's: only games actually going on screen are asked
        about, and get_odds caches per game, so one re-entering the window
        inside its TTL costs a cache lookup rather than a request. The
        thread mutates each game dict in place; the renderer re-reads
        game["odds"] every frame, so a line appears as soon as its fetch
        lands, mid-dwell included. Same as football-scoreboard #343.
        """
        # getattr: managers are built partially in places (the plugin tests
        # among them) that never set show_odds or an odds manager.
        if not getattr(self, "show_odds", False) or not getattr(self, "odds_manager", None):
            return
        pending = [g for g in games if not g.get("odds")]
        if not pending:
            return
        interval = self.mode_config.get("odds_update_interval", 3600)

        def fetch() -> None:
            for game in pending:
                try:
                    odds = self.odds_manager.get_odds(
                        sport=self.sport,
                        league=self.league,
                        event_id=game["id"],
                        update_interval_seconds=interval,
                    )
                    if odds:
                        game["odds"] = odds
                except Exception as exc:
                    self.logger.debug(
                        "Odds fetch for rotated-in game %s failed: %s",
                        game.get("id"), exc)

        threading.Thread(
            target=fetch, daemon=True,
            name="%s-rotated-odds" % self.sport_key).start()

    def _advance_other_games_if_due(self) -> List[Dict]:
        """Re-cut the non-favourite slice on the display path, or [] if not due.

        Costs one list slice and a sort of at most a few games -- no fetch, no
        parsing, no network. Returns the new list rather than assigning it,
        because the two callers keep different bookkeeping around games_list
        and both hold their own lock while they swap it in.
        """
        pools = getattr(self, "_selection_pools", None)
        if not pools:
            return []
        interval = self.other_rotation_interval_seconds
        limit = max(0, pools["other_limit"])
        # Whichever pool _compose_selection will actually slice. It falls back
        # to the unfiltered list only when NOTHING survived -- favourites
        # included. With a favourite playing and the filters rejecting every
        # other game, compose keeps the favourites-only list, so guessing the
        # unfiltered pool here made the due-check fire on every display() call
        # forever, recomposing an identical list each frame.
        others = pools["others"]
        favorites_fill = pools["favorites"] and pools["favorite_limit"] > 0
        if not others and limit > 0 and not favorites_fill:
            others = pools["unfiltered"]
        if interval <= 0 or limit <= 0 or len(others) <= limit:
            return []       # pinned, favourites-only, or nothing to rotate through
        if not self._other_window_rotated_at:
            return []       # no window has been cut yet; update() does the first
        if time.monotonic() - self._other_window_rotated_at < interval:
            return []
        return self._compose_selection()
