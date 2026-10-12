"""Which teams are ranked, and in which poll (sports family 8).

The scoreboards' rankings code, reconciled in ledmatrix-plugins (family 8)
from three, three and two bodies into one each, and copied here under the
existing names. All of it lives on the plugins' ``SportsCore``, so there is
one mixin, ``SportsRankingsMixin``:

- ``_fetch_team_rankings()``: the top-division poll from the data source,
  hourly, as ``_team_rankings_cache`` ({abbreviation: rank}, what the rank
  badge draws) and ``_ranked_team_ids`` ({ESPN team id: rank}). Only for a
  league ``_league_has_rankings()`` says publishes a poll.
- ``_choose_poll(rankings_data)``: the first block ESPN lists that is a
  top-division poll; seedings and the polls of lower divisions are stepped
  over, ESPN's order is otherwise kept (so the CFP rankings take over from AP
  when ESPN fronts them). ``choose_top_division_poll`` below is the same rule
  for code with no scoreboard to hand (the dynamic team resolvers).
- ``_poll_season_ended(poll, now)``: whether the poll's season is over.
- ``_load_division_team_ids()``: team ids per college football division
  (FBS, FCS) from ESPN's group rosters, for the season year.
- ``_ranked_positions(game)``, ``_is_ranked_game(game)``, ``_best_rank(game)``:
  the poll positions either side holds, by team id first, abbreviation second.
- ``_passes_other_filters(game)``: whether a non-favourite game is worth a
  slot under ``other_games_min_quality`` and ``other_games_divisions``.

THE RULES (decided 2026-10-12)
------------------------------
Ranks are kept by ESPN team id as well as by abbreviation, and a game with ids
is ranked by id: abbreviations are not unique (ESPN lists Ohio State and Ohio
State Newark both as OSU) and an id cannot rank the wrong school. The badge
still draws from the abbreviation table.

A poll whose season has ended loads nothing. ESPN keeps serving the last poll
it published until the next one (on 2026-10-12 college basketball's /rankings
was still April's final poll, college lacrosse's May's), and every block names
its season with an ``endDate``. Past it the table stays empty, so no badge
shows and the "ranked" filter fails open, until the new season's first poll
arrives. Read from the payload, not a calendar, so no league needs its own
season dates; a block without a readable ``endDate`` counts as current.

The division rosters are asked for the season year (July onward is this
year's season): college football's season runs into January, and the next
season's groups do not exist then. Only college football has division
rosters (``_DIVISION_GROUPS_BY_LEAGUE``), so this is football's rule as is,
not an override point.

Everything fails open: no poll, an empty roster or a failed request allows
every game rather than blanking the board.

The data source decides the endpoint (``data_source.fetch_standings``, still
per plugin); every scoreboard asks /rankings first for a college or NCAA
league. A standings table is never turned into ranks.

A new module rather than more methods on ``sports_shared``, for the reason
``sports_helpers`` gives: a missing module fails at load, where the version
checks see it; a missing method fails mid-update.

WHAT A HOST MUST PROVIDE
------------------------
Derived by walking every ``self.<attr>`` the mixin reads; the host-contract
test in ``test/test_sports_rankings.py`` fails if a read is added without being
listed here. Every scoreboard ``SportsCore`` supplies all of them.

- ``logger``, ``sport``, ``league``.
- ``data_source`` -- ``fetch_standings(sport, league)`` returns the payload
  (``{}`` on failure).
- ``session`` -- ``get(url, params=..., timeout=...)`` for the division
  rosters; ``cache_manager`` (may be ``None``) -- ``get(key)`` and
  ``set(key, value, ttl=...)`` keep them a day.
- ``_team_rankings_cache``, ``_ranked_team_ids`` and
  ``_rankings_cache_timestamp`` -- the tables and when they were fetched
  (0: never). Written here. ``_rankings_cache_duration`` -- seconds between
  fetches.
- ``_division_team_ids`` (``None``: never loaded) and ``_division_loaded_at``
  -- the rosters and the monotonic time they were loaded. Written here.
- ``other_games_min_quality`` and ``other_games_divisions`` -- the filters.
- ``_league_has_rankings`` and ``_game_divisions`` -- from
  ``SportsCoreSharedMixin``.
- ``_rankings_loaded`` -- from ``SportsRotationMixin`` (football-scoreboard
  overrides it to count the id table too).

The methods read the game dict's ``home_id``, ``away_id``, ``home_abbr`` and
``away_abbr``; any may be missing.

BASE ORDER
----------
No other mixin defines these names, so the position in the bases does not
change which body runs; a method on the plugin's own class still wins. The
mixin has no ``__init__``; the state it writes is the host's.
"""

import logging
import re
import time
from datetime import datetime, timezone
from typing import Any, Callable, ClassVar, Dict, List, Optional, Tuple

_LOGGER = logging.getLogger(__name__)


class SportsRankingsMixin:
    """``SportsCore``'s rankings. See module docstring."""

    # The host contract, declared for type checking only.
    logger: Any
    sport: str
    league: str
    data_source: Any
    session: Any
    cache_manager: Any
    _team_rankings_cache: Dict[str, int]
    _ranked_team_ids: Dict[int, int]
    _rankings_cache_timestamp: float
    _rankings_cache_duration: float
    _division_team_ids: Optional[Dict[str, set]]
    _division_loaded_at: float
    other_games_min_quality: str
    other_games_divisions: Any
    _league_has_rankings: Callable[[], bool]
    _game_divisions: Callable[[Dict], Optional[set]]
    _rankings_loaded: Callable[[], bool]

    # Which ranking block the badge reads. ESPN answers /rankings with more
    # than one block for several leagues, and the FIRST is not always a poll:
    # men's and women's college hockey front "NCAA Men's/Women's Hockey
    # Tournament Seedings", and college lacrosse publishes seedings beside its
    # Inside Lacrosse poll. College football fronts the AP Top 25 but also
    # carries the FCS and Division II polls and gains the CFP rankings in
    # November. An EXCLUDE list, not an allow list, so a poll ESPN invents
    # still counts; type first and name second, because the Division II block
    # types as "afca" while the FBS coaches poll -- also an AFCA poll -- types
    # as "usa".
    _NON_TOP_POLL_TYPES: ClassVar[frozenset] = frozenset({"tournament", "fcs"})
    _NON_TOP_POLL_NAMES: ClassVar[Tuple[str, ...]] = (
        "tournament", "seedings", "fcs",
        "division ii", "division iii", "div ii", "div iii",
    )

    # ESPN group ids for the college football divisions, from its own group
    # rosters (disjoint: 148 FBS team ids, 130 FCS). Keyed by league because
    # no other league publishes usable rosters: college baseball and lacrosse
    # answer HTTP 500, college basketball and hockey an empty list.
    _DIVISION_GROUPS_BY_LEAGUE: ClassVar[Dict[str, Dict[str, int]]] = {
        "college-football": {"fbs": 80, "fcs": 81},
    }
    _DIVISION_CACHE_TTL: ClassVar[int] = 24 * 60 * 60
    # A lookup that came back empty is retried on this shorter clock.
    _DIVISION_RETRY_SECONDS: ClassVar[int] = 10 * 60

    def _choose_poll(self, rankings_data: Optional[List[Dict]]) -> Dict:
        """The first block ESPN lists that is an actual top-division poll.

        ESPN's own order is otherwise kept, so whichever poll it fronts is the
        one that drives the badge.
        """
        for block in rankings_data or []:
            name = str(block.get("name") or "").lower()
            kind = str(block.get("type") or "").lower()
            if kind in self._NON_TOP_POLL_TYPES or any(
                marker in name for marker in self._NON_TOP_POLL_NAMES
            ):
                self.logger.debug(
                    "%s: skipping %s -- not a top-division poll",
                    getattr(self, "league", "?"), block.get("name") or kind)
                continue
            return block
        return {}

    def _fetch_team_rankings(self) -> Dict[str, int]:
        """Fetch team rankings using the new architecture components."""
        current_time = time.time()

        # No poll, no fetch. _league_has_rankings already gated the
        # quality-filter call site; the show_ranking call sites did not, so
        # ticking "Show Ranking" on a league that publishes no poll sent two
        # requests an hour to endpoints that cannot answer. Gating here rather
        # than at each call site covers every caller at once and cannot drift
        # apart again.
        #
        # It is also the setting's only honest answer on those leagues: the
        # rank badge replaces the record, so an empty table meant "Show
        # Ranking" quietly erased the records "Show Records" was drawing. The
        # schema now hides the toggle wherever the league has no poll, but a
        # config saved before that still carries it.
        if not self._league_has_rankings():
            return self._team_rankings_cache

        # Check if we have cached rankings that are still valid
        # Gate on when the last look happened, not on whether it found
        # anything. Professional leagues publish no poll, so `rankings`
        # comes back empty -- and an empty dict is falsy, so this guard
        # never short-circuited and the standings endpoint was re-fetched
        # on every update instead of hourly. Measured 2026-09-19 on an
        # MLB-only rig: 453 standings requests in a day against the 24
        # the one-hour duration intends. An empty result is a result.
        if (
            self._rankings_cache_timestamp
            and current_time - self._rankings_cache_timestamp
            < self._rankings_cache_duration
        ):
            return self._team_rankings_cache

        try:
            data = self.data_source.fetch_standings(self.sport, self.league)

            rankings: Dict[str, int] = {}
            ranked_ids: Dict[int, int] = {}
            poll = self._choose_poll(data.get("rankings", []))
            if self._poll_season_ended(poll, current_time):
                self.logger.info(
                    "%s: %s is from a season that has ended; no rank badges "
                    "until the new season's first poll", self.league,
                    poll.get("name") or "the poll",
                )
                poll = {}

            for team_data in poll.get("ranks", []):
                team_info = team_data.get("team", {})
                team_abbr = team_info.get("abbreviation", "")
                current_rank = team_data.get("current", 0)
                if current_rank <= 0:
                    continue
                if team_abbr:
                    rankings[team_abbr] = current_rank
                # Kept by id as well as by abbreviation. The badge draws the
                # abbreviation, but abbreviations are not unique across
                # divisions and the FBS and FCS schedules arrive in the SAME
                # scoreboard payload -- ESPN has SDSU for San Diego State and
                # SDST for South Dakota State today, and nothing promises it
                # stays that way. An id cannot rank the wrong school.
                try:
                    ranked_ids[int(team_info.get("id"))] = current_rank
                except (TypeError, ValueError):
                    pass

            # Cache the results
            self._team_rankings_cache = rankings
            self._ranked_team_ids = ranked_ids
            self._rankings_cache_timestamp = current_time

            if rankings:
                self.logger.info(
                    "%s: %d ranked teams loaded from %s", self.league,
                    len(rankings), poll.get("name") or "an unnamed poll",
                )
            else:
                self.logger.debug("Fetched rankings for 0 teams")
            return rankings

        except Exception as e:
            self.logger.error(f"Error fetching team rankings: {e}")
            return {}

    @staticmethod
    def _poll_season_ended(poll: Dict, now: float) -> bool:
        """Is this poll from a season that is already over?

        ESPN keeps serving the last poll it published until the next one
        appears: in October, college basketball's /rankings is still April's
        final poll and college lacrosse's is May's. Every block names its
        season with an ``endDate``; once that has passed, the badge would rank
        teams on a finished season, so the table stays empty until the new
        season's first poll arrives. Asked of the data rather than a calendar,
        so no league needs its own season dates. A block without a readable
        ``endDate`` counts as current, as before.
        """
        season = poll.get("season")
        end = str(season.get("endDate") or "") if isinstance(season, dict) else ""
        try:
            ended = datetime.strptime(end[:16], "%Y-%m-%dT%H:%M")
        except ValueError:
            return False
        return now > ended.replace(tzinfo=timezone.utc).timestamp()

    def _load_division_team_ids(self) -> Dict[str, set]:
        """Team ids per college division. Two requests a day, one league.

        Returns empty sets on any failure -- the caller treats "unknown" as
        "allowed", because a division lookup that fails must not blank the
        board.

        The in-memory copy expires like the stored one. Holding it for the life
        of the process meant two things, both silent: a board that happened to
        be offline for the first lookup had division filtering disabled until
        someone restarted the service, which on a display running for weeks is
        indefinitely; and a roster that changed between seasons was never
        picked up. A failed lookup is retried sooner than a good one, so a
        blip costs minutes rather than a day, without retrying per frame.
        """
        now = time.monotonic()
        if self._division_team_ids is not None:
            resolved = any(self._division_team_ids.values())
            age_limit = self._DIVISION_CACHE_TTL if resolved else self._DIVISION_RETRY_SECONDS
            if now - self._division_loaded_at < age_limit:
                return self._division_team_ids
        self._division_team_ids = {}
        self._division_loaded_at = now
        groups = self._DIVISION_GROUPS_BY_LEAGUE.get((self.league or "").lower())
        if not groups:
            return self._division_team_ids     # no divisions to speak of
        for name, group in groups.items():
            ids = set()
            key = f"{self.league}_division_teams_{group}"
            try:
                cached = self.cache_manager.get(key) if self.cache_manager else None
                if cached:
                    ids = {int(i) for i in cached}
                else:
                    # The SEASON year, not the calendar year. College
                    # football's 2026 season runs into January 2027, and asking
                    # for season 2027 in January returns groups that do not
                    # exist yet: the roster comes back empty, division
                    # filtering fails open, and it does so through the bowls
                    # and the playoff -- the weeks anyone is watching.
                    today = datetime.now()
                    season = today.year if today.month >= 7 else today.year - 1
                    url = (
                        "https://sports.core.api.espn.com/v2/sports/"
                        f"{self.sport}/leagues/{self.league}/seasons/"
                        f"{season}/types/2/groups/{group}/teams"
                    )
                    resp = self.session.get(url, params={"limit": 300}, timeout=15)
                    resp.raise_for_status()
                    for item in resp.json().get("items", []):
                        found = re.search(r"/teams/(\d+)", item.get("$ref", ""))
                        if found:
                            ids.add(int(found.group(1)))
                    if ids and self.cache_manager:
                        self.cache_manager.set(
                            key, sorted(ids), ttl=self._DIVISION_CACHE_TTL
                        )
            except Exception as exc:
                self.logger.warning(
                    "Could not resolve %s teams for %s (%s); division filtering "
                    "will allow everything", name, self.league, exc
                )
            self._division_team_ids[name] = ids
        return self._division_team_ids

    def _ranked_positions(self, game: Dict) -> List[int]:
        """Poll positions held by either side: ids first, abbreviations second.

        The id table is authoritative whenever the game carries ids and a poll
        has been parsed, because it is built from a top-division poll alone --
        so an FCS side cannot appear in it however it is abbreviated, and an
        abbreviation shared across divisions cannot promote the wrong school.
        The abbreviation table stays as the fallback for a game dict with no
        ids on it.
        """
        by_id = getattr(self, "_ranked_team_ids", None) or {}
        ids = []
        for side in ("home_id", "away_id"):
            try:
                ids.append(int(game.get(side)))  # type: ignore[arg-type]  # None: caught below
            except (TypeError, ValueError):
                ids = []
                break
        if by_id and ids:
            return [rank for rank in (by_id.get(i, 0) for i in ids) if rank]
        by_abbr = getattr(self, "_team_rankings_cache", None) or {}
        return [rank for rank in (by_abbr.get(game.get("home_abbr"), 0),
                                  by_abbr.get(game.get("away_abbr"), 0)) if rank]

    def _is_ranked_game(self, game: Dict) -> bool:
        return bool(self._ranked_positions(game))

    def _best_rank(self, game: Dict) -> int:
        """The better of the two sides' poll positions, or 99 if neither ranks."""
        found = self._ranked_positions(game)
        return min(found) if found else 99

    def _passes_other_filters(self, game: Dict) -> bool:
        """Is this non-favourite game worth one of the remaining slots?

        "ranked" means ranked in the TOP division's poll -- see _choose_poll.
        An FCS side ranked in its own poll does not qualify the game it is
        playing in, which for college football is most often an FCS school
        visiting an unranked FBS one.

        Every check fails OPEN. If rankings could not be fetched or the
        division rosters did not resolve, the game is allowed: a board showing
        filler is a poor board, but a board showing nothing is a broken one.
        """
        if self.other_games_min_quality == "ranked":
            if self._rankings_loaded() and not self._is_ranked_game(game):
                return False

        wanted = self.other_games_divisions
        if wanted:
            present = self._game_divisions(game)
            if present is not None and not (present & set(wanted)):
                return False
        return True


class _PollChooser(SportsRankingsMixin):
    """Just enough host for ``_choose_poll``."""

    def __init__(self, logger: Any, league: str) -> None:
        self.logger = logger
        self.league = league


def choose_top_division_poll(rankings_data: Optional[List[Dict]], logger: Any = None,
                             label: Optional[str] = None) -> Dict:
    """``SportsRankingsMixin._choose_poll`` for code without a scoreboard.

    The one rule, for the dynamic team resolvers that expand ``AP_TOP_25`` and
    the like, so the badge and the favourites they resolve read the same poll.
    ``label`` names the league in the debug line for a skipped block.
    """
    return _PollChooser(logger or _LOGGER, label or "?")._choose_poll(rankings_data)
