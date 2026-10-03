"""Which requests a scoreboard makes: season fetches, the lookback, live odds.

Four ``SportsCore`` methods are identical (executable AST, docstrings
stripped) in all nine scoreboards' ``sports.py`` -- afl, baseball,
basketball, football, hockey, lacrosse, nrl, soccer and ufc -- and were
copied here from ledmatrix-plugins ``30455671`` (origin/main, 2026-09-29)
under their existing names:

- ``_background_fetches_espn_ranges`` -- whether the core's background
  service can fetch an ESPN date range, or the plugin must;
- ``_fetch_season_directly`` -- fetch and cache a season in chunks ESPN
  accepts, on the calling thread;
- ``_needs_previous_day`` (with ``_LOOKBACK_CUTOFF_HOUR``) -- whether the
  live fetch still has to ask for yesterday;
- ``_wants_live_odds`` (with ``_LIVE_ODDS_LOOKAHEAD``) -- whether a live
  game is close enough to the screen to be worth an odds request.

Three other ``SportsCore`` methods are as identical and stay in the plugins,
for the reasons ``sports_shared`` gives: ``_get_timezone`` binds each
plugin's own ``resolve_timezone`` shim, and ``_extract_game_details`` /
``_fetch_data`` are the abstract sport-specific contract. So does
``SportsUpcoming.__init__``: the mixins in ``src/common`` hold no
constructor, so the plugins' constructor signature stays theirs.

A new module rather than more methods on ``sports_shared``, for the reason
``sports_helpers`` gives: a missing module fails at load, where the version
checks see it; a missing method fails mid-update.

WHAT A HOST MUST PROVIDE
------------------------
Derived by walking every ``self.<attr>`` the mixin reads; the host-contract
test in ``test/test_sports_fetch.py`` fails if a read is added without being
listed here.

- ``session``, ``headers``, ``cache_manager`` and ``logger`` --
  ``_fetch_season_directly``.
- ``_games_lock`` -- ``_wants_live_odds``, which also reads ``live_games``,
  ``current_game_index`` and ``_rotation_schedule`` with ``getattr``
  (only ``SportsLive`` has them).
- ``live_games``, read with ``getattr`` -- ``_needs_previous_day``.
- ``background_service``, read with ``getattr`` --
  ``_background_fetches_espn_ranges``.
- ``sport`` and ``league`` (ESPN's path segments, e.g. ``football`` /
  ``nfl``) -- ``_schedule_cache_key``, and ``_fetch_season_directly`` when
  it is given no key and cannot read one from its URL.

THE SCHEDULE CACHE KEY (fetch service stage 2)
----------------------------------------------
``_schedule_cache_key`` names a schedule window with the canonical
``espn_scoreboard_cache_key`` instead of a plugin-built
``{sport_key}_schedule_{window}``, and ``_cached_schedule`` reads it with the
old key as a fallback for one release, so an upgrade serves the copy already
on disk instead of refetching every league at once. The canonical key
carries the window's dates, so it moves on a day as the window slides; a
miss on it also deletes the copy for the day before, so a league keeps one
window file instead of a week of them.

Add it as a base of the plugin's ``SportsCore``, e.g.
``class SportsCore(SportsFetchMixin, SportsCoreSharedMixin,
SportsHelpersMixin, ABC)``. It defines nothing those define; a method or
constant on the plugin's own class still wins over the mixin's.
"""

import logging
import threading
from datetime import datetime, timedelta
from typing import Any, ClassVar, Dict, Iterable, Optional

from src.common.espn_dates import (
    ESPN_MAX_LIMIT,
    espn_scoreboard_cache_key,
    espn_scoreboard_cache_key_for_url,
    fetch_espn_scoreboard,
    parse_espn_date_range,
)
from src.common.fetch_service import get_fetch_service

_ESPN_SITE = "https://site.api.espn.com/"


def _previous_window_key(cache_key: str) -> Optional[str]:
    """The canonical key of the same window one day earlier, or None when
    ``cache_key`` is not a canonical day-range key."""
    head, sep, dates = cache_key.rpartition("_")
    if not sep or not head.startswith("espn_scoreboard_"):
        return None
    span = parse_espn_date_range(dates)
    if span is None:
        return None
    start, end = (day - timedelta(days=1) for day in span)
    return f"{head}_{start.strftime('%Y%m%d')}-{end.strftime('%Y%m%d')}"


class SportsFetchMixin:
    """Season fetch, lookback and live-odds decisions. See module docstring."""

    # The host contract, declared for type checking only: these create no
    # attributes, so the host's own values are what the methods read.
    session: Any
    headers: Dict[str, str]
    cache_manager: Any
    logger: logging.Logger
    _games_lock: threading.RLock
    sport: str
    league: str

    #: How many games past the one on screen keep their odds warm. One is
    #: enough for the line to be ready when the rotation advances; more just
    #: re-creates the whole-slate fetch this replaced.
    _LIVE_ODDS_LOOKAHEAD: ClassVar[int] = 1

    def _wants_live_odds(self, game: Dict) -> bool:
        """Whether a live game is near enough the front of the rotation to be
        worth an odds request.

        Odds used to be fetched for *every* live game in the league on every
        update. The renderer only ever draws ``current_game``, and a full
        rotation of a big slate takes minutes while ``live_odds_update_interval``
        is 60s -- so all but one of those requests expired before the game they
        belonged to came round.

        Measured 2026-09-19 over a full college-football slate: 11,978 odds
        requests in 13h on one rig, 54% of all its ESPN traffic, across only
        ~140 distinct games. The eager loop also cost up to 2s of ``update()``
        per live game, because ``_fetch_odds`` waits on its worker thread.

        Mirrors the narrowing already applied to the upcoming path and to
        ``_attach_odds_to_rotated_games``: only games about to be on screen are
        asked about. ``get_odds`` still caches per game, so a game re-entering
        the window inside its TTL costs a cache lookup, not a request.

        The rotation state read here is the previous cycle's -- the new list is
        still being built -- which is exactly the question being asked: is this
        game at or near the position currently on the panel?
        """
        # Read defensively: this predicate lives on SportsCore so it sits
        # beside _fetch_odds, but live_games/_rotation_schedule belong to
        # SportsLive, which is the only caller.
        with self._games_lock:
            games = list(getattr(self, "live_games", ()) or ())
            index = getattr(self, "current_game_index", 0)
            schedule = list(getattr(self, "_rotation_schedule", ()) or ())
        if not games:
            # Cold start: nothing is on screen yet, so let the games seen on
            # this first pass through rather than render a blank line for a
            # whole cycle. Bounded -- the next pass has a rotation to narrow by.
            return True
        order = schedule or [g.get("id") for g in games]
        if not order:
            return True
        start = index if 0 <= index < len(order) else 0
        wanted = {
            order[(start + offset) % len(order)]
            for offset in range(self._LIVE_ODDS_LOOKAHEAD + 1)
        }
        return game.get("id") in wanted

    #: Hour of the Eastern day past which last night's games are assumed over.
    #:
    #: The live fetch asks ESPN for a two-day window so a game that started
    #: yesterday and is still running is not lost. ESPN rejects date *ranges*,
    #: so that window is split into one request per day -- doubling every live
    #: poll. Measured 2026-09-19: 1,858 requests per rig spent on yesterday's
    #: date, which after breakfast holds nothing but final games.
    #:
    #: No sport on these boards runs six hours past midnight, and one that
    #: somehow did is still covered: a game already being tracked keeps its own
    #: day in the window regardless of the hour.
    _LOOKBACK_CUTOFF_HOUR: ClassVar[int] = 6

    def _needs_previous_day(self, now: datetime) -> bool:
        """Whether the previous Eastern day can still hold a live game."""
        if now.hour < self._LOOKBACK_CUTOFF_HOUR:
            return True
        previous = (now - timedelta(days=1)).strftime("%Y%m%d")
        for game in (getattr(self, "live_games", None) or []):
            start: Any = game.get("start_time_utc") if hasattr(game, "get") else None
            try:
                if start.astimezone(now.tzinfo).strftime("%Y%m%d") == previous:
                    return True
            except (AttributeError, ValueError, OSError, OverflowError):
                continue
        return False

    def _background_fetches_espn_ranges(self) -> bool:
        """Can the core's background service fetch an ESPN date range?

        Cores from before the 2026-09-15 fix send a season range to ESPN as-is,
        which now answers 400 for every sport. On those cores the managers fetch
        the season themselves with _fetch_season_directly instead.
        """
        service = getattr(self, "background_service", None)
        return bool(getattr(service, "handles_espn_date_ranges", False))

    def _schedule_cache_key(self, datestring: str) -> str:
        """The canonical cache key for this league's schedule over
        ``datestring`` (``espn_scoreboard_cache_key``)."""
        return espn_scoreboard_cache_key(self.sport, self.league, datestring)

    def _cached_schedule(self, cache_key: str, legacy_keys: Iterable[str] = ()) -> Any:
        """What ``self.cache_manager.get(cache_key)`` returns, falling back
        to each of ``legacy_keys`` (the plugin's pre-canonical keys) in turn.

        The same read the managers made before -- same default max age, a
        stored ttl still wins -- so moving to the canonical key changes
        where a schedule is cached, not for how long. A read from an old key
        is counted (``legacy_cache_hits``) so it is visible when the
        fallback can go. A miss on the canonical key also deletes the same
        window's copy from the day before (see the module docstring).
        """
        cached = self.cache_manager.get(cache_key)
        if cached:
            return cached
        self._retire_previous_window(cache_key)
        for legacy in legacy_keys:
            if not legacy or legacy == cache_key:
                continue
            cached = self.cache_manager.get(legacy)
            if cached:
                try:
                    get_fetch_service().note_cache_hit(
                        _ESPN_SITE, legacy=True, avoided_request=False)
                except Exception:  # noqa: BLE001 - counting never breaks a read
                    pass
                return cached
        return None

    def _retire_previous_window(self, cache_key: str) -> None:
        previous = _previous_window_key(cache_key)
        delete = getattr(self.cache_manager, "delete", None)
        if previous is None or not callable(delete):
            return
        try:
            delete(previous)
        except Exception as e:  # noqa: BLE001 - housekeeping only
            self.logger.debug(f"Could not delete old schedule copy {previous}: {e}")

    def _fetch_season_directly(
        self,
        url: str,
        datestring: str,
        cache_key: Optional[str],
        label: str,
        ttl: Optional[int] = None,
    ) -> Optional[Dict]:
        """Fetch a season schedule on this thread, in chunks ESPN accepts, and cache it.

        ``label`` names the schedule in log lines, e.g. ``"2026 season"``.
        ``cache_key=None`` caches it under the canonical key
        (``espn_scoreboard_cache_key`` for ``url``'s sport and league).
        """
        if cache_key is None:
            cache_key = (espn_scoreboard_cache_key_for_url(url, datestring)
                         or self._schedule_cache_key(datestring))
        try:
            data = fetch_espn_scoreboard(
                self.session,
                url,
                params={"dates": datestring, "limit": ESPN_MAX_LIMIT},
                headers=self.headers,
                timeout=30,
                logger=self.logger,
            )
        except Exception as e:
            self.logger.error(f"Failed to fetch {label} schedule: {e}")
            return None
        if ttl is None:
            self.cache_manager.set(cache_key, data)
        else:
            self.cache_manager.set(cache_key, data, ttl=ttl)
        self.logger.info(
            f"Fetched {label} schedule: {len(data.get('events', []))} events"
        )
        return data
