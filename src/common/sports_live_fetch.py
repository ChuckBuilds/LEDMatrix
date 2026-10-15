"""The live poll's scoreboard request and the odds requests (sports family 9).

The scoreboards' live fetch and odds code, reconciled in ledmatrix-plugins
(family 9) from seven and three bodies into one each, and copied here under
the existing names. All of it lives on the plugins' ``SportsCore``, so there
is one mixin, ``SportsLiveFetchMixin``:

- ``_fetch_todays_games()``: today's scoreboard for the live poll, with
  yesterday's while it can still hold a live game (``_needs_previous_day``),
  as ``{"events": [...]}``; None when ESPN cannot be reached.
- ``_fetch_odds(game)``: queue one game for an odds request; returns at once.
- ``_drain_odds_queue()``: the odds worker that answers the queue.

THE RULES (decided 2026-10-12)
------------------------------
Live scores are always fresh. Every poll asks ESPN (``get_espn_scoreboard``
with ``max_age=1``: only a copy another reader fetched within the last second,
the managers of one display tick, is reused) and leaves the answer under the
shared ``espn_scoreboard_cache_key`` for its dates, so odds-ticker and every
other reader of the same day share it. The plugins' old 30 s cache under
``<sport_key>_scoreboard_current`` is gone. College basketball is asked by
date like every other league; ESPN answered single dates for it on 2026-10-12
(the old no-dates request cited a 404 that no longer happens). An answer whose
``events`` is not a list counts as no games.

Odds never block ``update()``. ``_fetch_odds`` puts the game on the manager's
queue and starts one daemon worker if none is running; the worker asks the
odds manager for each queued game in turn and attaches the line to every
dict queued for that id, in place, under ``_games_lock`` (display() reads the
games under it), so a card can draw once without odds and gain them when
ESPN answers. One batch in flight per manager: a game queued while the worker
is busy joins its batch, so a slow endpoint never stacks threads or requests.
The odds manager bounds each request (core's ``BaseOddsManager``: 5 s, then a
60 s hold-off). A live game's line is cached ``live_odds_update_interval``
(60 s), any other ``odds_update_interval`` (3600 s). Without ``show_odds``, a
game id or an odds manager nothing is asked (the last logged at debug).

Before the reconcile, six plugins waited on a worker for 1.5 s (2 s for a
live game) per game and hockey and lacrosse asked on the calling thread: with
a 3 s endpoint, ``update()`` of four upcoming games took 6 s and 12 s, and in
the six the line appeared only at the next ``update()`` (an hour later for an
upcoming game).

A new module rather than more methods on ``sports_fetch``, for the reason
``sports_helpers`` gives: a missing module fails at load, where the version
checks see it; a missing method fails mid-update.

WHAT A HOST MUST PROVIDE
------------------------
Derived by walking every ``self.<attr>`` the mixin reads; the host-contract
test in ``test/test_sports_live_fetch.py`` fails if a read is added without
being listed here. Every scoreboard ``SportsCore`` supplies all of them.

- ``session``, ``headers``, ``cache_manager``, ``logger``, ``sport``,
  ``league`` -- the scoreboard request.
- ``_needs_previous_day`` -- from ``SportsFetchMixin``.
- ``show_odds``, ``mode_config`` (the two odds intervals), ``sport_key`` (the
  worker's thread name), ``_games_lock`` (a lock display() also holds).
- ``odds_manager``, read with ``getattr`` -- ``get_odds(sport=, league=,
  event_id=, update_interval_seconds=)``.
- ``_odds_pending`` and ``_odds_worker_busy`` -- the queue and whether a
  worker is running; created here on first use, under ``_games_lock``.

The game dict's ``id`` and ``is_live`` are read; ``odds`` is written.

BASE ORDER
----------
No other mixin defines these names, so the position in the bases does not
change which body runs; a method on the plugin's own class still wins. The
mixin has no ``__init__``.
"""

import threading
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple

import pytz
import requests

from src.common.espn_dates import get_espn_scoreboard


class SportsLiveFetchMixin:
    """``SportsCore``'s live fetch and odds. See module docstring."""

    # The host contract, declared for type checking only.
    session: Any
    headers: Dict[str, str]
    cache_manager: Any
    logger: Any
    sport: str
    league: str
    sport_key: str
    show_odds: bool
    mode_config: Dict[str, Any]
    _games_lock: Any
    _needs_previous_day: Callable[[datetime], bool]
    odds_manager: Any
    _odds_pending: Dict[Any, Tuple[List[Dict], Any]]
    _odds_worker_busy: bool

    def _fetch_todays_games(self) -> Optional[Dict]:
        """Today's scoreboard for the live poll, with yesterday's while it can
        still hold a live game (``_needs_previous_day``)."""
        # ESPN anchors its calendar to Eastern US time.
        now = datetime.now(pytz.timezone("America/New_York"))
        today = now.strftime("%Y%m%d")
        dates = (
            f"{(now - timedelta(days=1)).strftime('%Y%m%d')}-{today}"
            if self._needs_previous_day(now) else today
        )
        try:
            # Asked on every poll and left under core's shared key for this
            # scoreboard and date, so odds-ticker and every other reader of
            # the same day reuse it. max_age=1: a copy another reader fetched
            # within the last second (the managers of one display tick) is the
            # same answer; anything older is asked again.
            data = get_espn_scoreboard(
                self.session, self.sport, self.league, dates,
                cache_manager=self.cache_manager, max_age=1,
                headers=self.headers, timeout=10, logger=self.logger,
            )
        except requests.exceptions.RequestException as e:
            self.logger.error(
                f"API error fetching today's games for {self.sport} - {self.league}: {e}"
            )
            return None
        events = data.get("events") if isinstance(data, dict) else None
        if not isinstance(events, list):
            events = []  # ESPN can answer "events": null
        self.logger.info(
            f"Fetched {len(events)} games for {dates} for {self.sport} - {self.league}"
        )
        return {"events": events}

    def _fetch_odds(self, game: Dict) -> None:
        """Queue ``game`` for an odds request; never waits for the answer.

        One daemon worker per manager asks the odds manager for each queued
        game in turn and attaches the line to the game dict in place, under
        ``_games_lock`` (display() reads the games under it), so a card can
        draw once without odds and gain them when ESPN answers. A game queued
        while the worker is busy joins its batch, and every dict queued for one
        id (update() rebuilds them; the list on screen may keep older ones)
        gets the one answer, so a slow endpoint never stacks threads or
        requests. The odds manager bounds each request (5 s, then a 60 s
        hold-off).
        """
        if not self.show_odds or game.get("id") is None:
            return
        if not getattr(self, "odds_manager", None):
            self.logger.debug(f"No odds manager; no odds for game {game['id']}")
            return
        interval = (
            self.mode_config.get("live_odds_update_interval", 60)
            if game.get("is_live", False)
            else self.mode_config.get("odds_update_interval", 3600)
        )
        with self._games_lock:
            pending = getattr(self, "_odds_pending", None)
            if pending is None:
                pending = self._odds_pending = {}
            games, _ = pending.get(game["id"], ([], None))
            if not any(queued is game for queued in games):
                games.append(game)
            pending[game["id"]] = (games, interval)
            if getattr(self, "_odds_worker_busy", False):
                return
            self._odds_worker_busy = True
        try:
            threading.Thread(target=self._drain_odds_queue, daemon=True,
                             name=f"{self.sport_key}-odds").start()
        except RuntimeError as e:  # no thread to be had; try again next update
            with self._games_lock:
                self._odds_worker_busy = False
            self.logger.debug(f"Could not start the odds worker: {e}")

    def _drain_odds_queue(self) -> None:
        """The odds worker: ask for each queued game until none is left."""
        while True:
            with self._games_lock:
                if not self._odds_pending:
                    self._odds_worker_busy = False
                    return
                game_id = next(iter(self._odds_pending))
                games, interval = self._odds_pending.pop(game_id)
            try:
                odds = self.odds_manager.get_odds(
                    sport=self.sport,
                    league=self.league,
                    event_id=game_id,
                    update_interval_seconds=interval,
                )
            except Exception as e:
                self.logger.debug(f"Odds fetch failed for game {game_id}: {e}")
                continue
            if odds:
                with self._games_lock:
                    for game in games:
                        game["odds"] = odds
