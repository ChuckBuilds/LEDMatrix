"""The game dict every scoreboard screen draws (sports family 10).

The scoreboards' ``_extract_game_details_common``, reconciled in
ledmatrix-plugins (family 10) from nine bodies into one, and copied here
under the existing names with the helpers it calls. It lives on the plugins'
``SportsCore``, so there is one mixin, ``SportsViewModelMixin``:

- ``_extract_game_details_common(game_event)``: one ESPN scoreboard event to
  ``(details, home competitor, away competitor, status, situation)``, or five
  Nones when the event has no competition, status, date or home/away pair.
  Each sport's ``_extract_game_details`` adds its extras to ``details``.
- ``_status_is_final(status_type)``: whether ``status.type`` is a result.
- ``_score_text(score)``: a competitor's score as integer text.
- ``_team_logo_url(competitor)``, ``_extract_team_record(competitor)``.
- ``_logo_filename(abbr, team_id)``: the seam below.

THE KEYS
--------
Additive only: a key is never renamed or dropped, because every renderer
(``sports.py``'s scorebug, ``game_renderer.py``'s card, ``sports_card``,
``sports_game_renderer``, ``sports_shared``, ``sports_game_over``,
``sports_vegas``...) reads them. A sport's extras (down and distance, innings,
shots, tournament seeds...) belong in its own ``_extract_game_details``.

==================  ==================  ==========================================
key                 type                value (default)
==================  ==================  ==========================================
id                  str | None          the event id
game_time           str                 local start, "7:00PM" ("" without a date)
game_date           str                 local "MM/DD"; "M/D" with
                                        ``display.use_short_date_format`` ("")
start_time_utc      datetime | None     aware, UTC; a time without an offset
                                        is read as UTC (None if unparseable)
status_text         str                 ESPN ``status.type.shortDetail``
is_live             bool                state "in", not a called-off status
is_final            bool                ``_status_is_final(status.type)``
is_upcoming         bool                state "pre", or a scheduled status name
is_halftime         bool                state "halftime" or STATUS_HALFTIME
is_period_break     bool                STATUS_END_PERIOD
broadcast           str                 ``str()`` of ESPN's field (""); unread
home_abbr/away_abbr str                 abbreviation, else the name's first 3
home_id/away_id     str | None          competitor id, else its team's id
home_score/         str                 ``_score_text``: integer text ("0")
away_score
home_logo_path/     Path                ``logo_dir / _logo_filename(abbr, id)``
away_logo_path
home_logo_url/      str | None          ``_team_logo_url``: ``team.logo``, else
away_logo_url                           ``team.logos[0].href`` (None)
home_record/        str                 ``_extract_team_record``; "0-0" and
away_record                             "0-0-0" read as "" ("")
is_within_window    bool                always True; unread
favorite_teams      list                the resolved favourites, for the card's
                                        result colour ([])
period              int                 0 until the sport sets it
clock               str                 "" until the sport sets it
==================  ==================  ==========================================

``period_text`` is not set here: each sport's extractor sets it, and the
renderers read its absence (baseball's) as "draw ESPN's status text", which
is how an extra-inning final reads "Final/10".

THE RULES (decided 2026-10-13)
------------------------------
A game is final when its state is "post", ESPN has not marked it
``completed: false`` (a payload without the flag is judged on state and name
alone), and its status is not one of ``_NOT_PLAYED_STATUS_NAMES``: postponed,
canceled, suspended, abandoned (``_CALLED_OFF_STATUS_NAMES``), delayed, or a
pending forfeit or no-contest. A forfeit is a result. A called-off game is
never live, even while ESPN still reports it "in". Scores are integer text in
every sport: 21, "21.0" and {"value": 21} are "21"; a missing, empty or
non-numeric score is "0". Halftime reads "HALF" with no clock in every
sport's ``period_text`` (each sport's own extractor, not this module).

THE SEAM
--------
``_logo_filename(abbr, team_id)``: the file under ``logo_dir`` a team's logo
is kept in. Default ``<ABBR>.png`` (``LogoDownloader.normalize_abbreviation``);
NRL overrides it with ``<ABBR>_<id>.png`` because its abbreviations are not
unique ("NEW" is two clubs).

A new module rather than more methods on ``sports_fetch``, for the reason
``sports_helpers`` gives: a missing module fails at load, where the version
checks see it; a missing method fails mid-update.

WHAT A HOST MUST PROVIDE
------------------------
Derived by walking every ``self.<attr>`` the mixin reads; the host-contract
test in ``test/test_sports_view_model.py`` fails if a read is added without
being listed here. Every scoreboard ``SportsCore`` supplies all of them.

- ``logger``, ``config`` (``display.use_short_date_format``), ``logo_dir``
  (a ``Path``), ``favorite_teams`` (a list or None), ``_get_timezone``
  (a callable returning the zone game times are shown in).

BASE ORDER
----------
No other mixin defines these names, so the position in the bases does not
change which body runs; a method on the plugin's own class still wins. The
mixin has no ``__init__``.
"""

import re
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, ClassVar, Dict, List, Optional, Tuple

import pytz

from src.logo_downloader import LogoDownloader


class SportsViewModelMixin:
    """``SportsCore``'s game dict. See module docstring."""

    # The host contract, declared for type checking only.
    logger: Any
    config: Dict[str, Any]
    logo_dir: Path
    favorite_teams: Optional[List[str]]
    _get_timezone: Callable[[], Any]

    #: ESPN status names for a game called off: never live, never a result.
    #: ESPN files a postponed or washed-out game under state "post" with both
    #: scores "0" (so "post" alone drew it on Recent as "Final 0-0"), and a
    #: suspended one can still read "in".
    _CALLED_OFF_STATUS_NAMES: ClassVar[frozenset] = frozenset({
        "STATUS_POSTPONED", "STATUS_CANCELED", "STATUS_CANCELLED",
        "STATUS_SUSPENDED", "STATUS_ABANDONED",
    })
    #: Status names under state "post" that are not a result. A forfeit
    #: (STATUS_FORFEIT) is one; a pending forfeit or no-contest is not.
    _NOT_PLAYED_STATUS_NAMES: ClassVar[frozenset] = _CALLED_OFF_STATUS_NAMES | frozenset({
        "STATUS_DELAYED", "STATUS_FORFEIT_PENDING", "STATUS_NO_CONTEST_PENDING",
    })

    @classmethod
    def _status_is_final(cls, status_type: Any) -> bool:
        """Whether an ESPN ``status.type`` is a game played to a result.

        State "post", a ``completed`` flag that is not False, and a name
        outside ``_NOT_PLAYED_STATUS_NAMES``. A payload without the flag is
        judged on state and name alone; ESPN sends False for a postponement.
        """
        if not isinstance(status_type, dict) or status_type.get("state") != "post":
            return False
        if status_type.get("completed") is False:
            return False
        return str(status_type.get("name") or "").upper() not in cls._NOT_PLAYED_STATUS_NAMES

    def _score_text(self, score: Any) -> str:
        """A competitor's score as integer text: "21" for 21, "21.0" or
        {"value": 21.0}; "0" when it is missing or holds no number."""
        if isinstance(score, dict):
            value = score.get("value", 0)
            score = value if value != 0 else score.get("displayValue", score.get("score", 0))
        if isinstance(score, str):
            text = score.strip()
            try:
                score = float(text)
            except ValueError:
                digits = re.findall(r"\d+", text)
                if not digits:
                    if text:
                        self.logger.warning(f"Could not extract score from string: {text!r}")
                    return "0"
                score = float(digits[0])
        if score is None:
            return "0"
        try:
            return str(int(float(score)))
        except (TypeError, ValueError, OverflowError) as e:
            self.logger.warning(f"Error extracting score: {e}, score value: {score!r}")
            return "0"

    @staticmethod
    def _team_logo_url(competitor: Dict) -> Optional[str]:
        """The team's ``logo``, else the first of its ``logos``, else None."""
        team = competitor.get("team") or {}
        logos = team.get("logos") or []
        first = logos[0] if logos and isinstance(logos[0], dict) else {}
        return team.get("logo") or first.get("href")

    @staticmethod
    def _extract_team_record(team_data: Dict) -> str:
        """Extract the overall record string from a competitor/team object.

        The ESPN scoreboard API uses ``records`` (plural) with a ``summary``
        field, while the team-schedule API uses ``record`` (singular) with a
        ``displayValue`` field.  This helper handles both formats so that
        records display correctly regardless of which API provided the data.
        """
        # Scoreboard API format: records[0].summary  (e.g. "10-2")
        records = team_data.get("records")
        if records and isinstance(records, list) and len(records) > 0:
            return records[0].get("summary", "")  # type: ignore[no-any-return]

        # Team-schedule API format: record[0].displayValue  (e.g. "7-0")
        record = team_data.get("record")
        if record and isinstance(record, list) and len(record) > 0:
            return record[0].get("displayValue", record[0].get("summary", ""))  # type: ignore[no-any-return]

        return ""

    def _logo_filename(self, abbr: str, team_id: Any) -> str:
        """Seam: the file a team's logo is kept in under ``logo_dir``."""
        return f"{LogoDownloader.normalize_abbreviation(abbr)}.png"

    def _extract_game_details_common(
        self, game_event: Dict
    ) -> Tuple[Optional[Dict], Optional[Dict], Optional[Dict], Optional[Dict], Optional[Dict]]:
        """The game dict every renderer reads, built from one ESPN event.

        Returns (details, home competitor, away competitor, status,
        situation), or five Nones when the event has no competition, status,
        date or home/away pair. Keys are additive only: never rename or drop
        one. Sport extras belong in the sport's ``_extract_game_details``.
        """
        if not game_event:
            return None, None, None, None, None
        try:
            competitions = game_event.get("competitions", [])
            if not competitions:
                self.logger.warning(f"No competitions data for game {game_event.get('id', 'unknown')}")
                return None, None, None, None, None
            competition = competitions[0]
            status = competition.get("status")
            if not status:
                self.logger.warning(f"No status data for game {game_event.get('id', 'unknown')}")
                return None, None, None, None, None
            competitors = competition.get("competitors", [])
            game_date_str = game_event["date"]
            situation = competition.get("situation")
            start_time_utc = None
            try:
                dt = datetime.fromisoformat(game_date_str.replace("Z", "+00:00"))
                # A time without an offset is read as UTC, as ESPN sends it.
                start_time_utc = (dt.replace(tzinfo=pytz.UTC) if dt.tzinfo is None
                                  else dt.astimezone(pytz.UTC))
            except ValueError:
                self.logger.warning(f"Could not parse game date: {game_date_str}")

            home_team = next(
                (c for c in competitors if c.get("homeAway") == "home"), None
            )
            away_team = next(
                (c for c in competitors if c.get("homeAway") == "away"), None
            )
            if not home_team or not away_team:
                self.logger.warning(
                    f"Could not find home or away team in event: {game_event.get('id')}"
                )
                return None, None, None, None, None

            try:
                home_abbr = home_team["team"]["abbreviation"]
            except KeyError:
                home_abbr = home_team["team"]["name"][:3]
            try:
                away_abbr = away_team["team"]["abbreviation"]
            except KeyError:
                away_abbr = away_team["team"]["name"][:3]
            home_id = home_team.get("id") or home_team["team"].get("id")
            away_id = away_team.get("id") or away_team["team"].get("id")

            game_time, game_date = "", ""
            if start_time_utc:
                local_time = start_time_utc.astimezone(self._get_timezone())
                game_time = local_time.strftime("%I:%M%p").lstrip("0")
                if self.config.get("display", {}).get("use_short_date_format", False):
                    # %-m/%-d are glibc extensions: strftime raises ValueError on
                    # Windows and musl. Build the same text portably instead.
                    game_date = f"{local_time.month}/{local_time.day}"
                else:
                    game_date = local_time.strftime("%m/%d")

            home_record = self._extract_team_record(home_team)
            away_record = self._extract_team_record(away_team)
            # Don't show "0-0" records - set to blank instead
            if home_record in {"0-0", "0-0-0"}:
                home_record = ""
            if away_record in {"0-0", "0-0-0"}:
                away_record = ""

            status_type = status["type"]
            status_name = str(status_type.get("name") or "").upper()
            details = {
                "id": game_event.get("id"),
                "game_time": game_time,
                "game_date": game_date,
                "start_time_utc": start_time_utc,
                "status_text": status_type["shortDetail"],  # e.g. "Final", "7:30 PM", "Q1 12:34"
                "is_live": (status_type["state"] == "in"
                            and status_name not in self._CALLED_OFF_STATUS_NAMES),
                "is_final": self._status_is_final(status_type),
                "is_upcoming": (
                    status_type["state"] == "pre"
                    or status_type["name"].lower()
                    in ["scheduled", "pre-game", "status_scheduled"]
                ),
                "is_halftime": status_type["state"] == "halftime"
                or status_type["name"] == "STATUS_HALFTIME",
                "is_period_break": status_type["name"] == "STATUS_END_PERIOD",
                # str(): ESPN's undocumented payloads have served this field as
                # an object in some sports. Plain game metadata: nothing reads it.
                "broadcast": str(competition.get("broadcast") or ""),
                "home_abbr": home_abbr,
                "home_id": home_id,
                "home_score": self._score_text(home_team.get("score")),
                "home_logo_path": self.logo_dir / Path(self._logo_filename(home_abbr, home_id)),
                "home_logo_url": self._team_logo_url(home_team),
                "home_record": home_record,
                "away_record": away_record,
                "away_abbr": away_abbr,
                "away_id": away_id,
                "away_score": self._score_text(away_team.get("score")),
                "away_logo_path": self.logo_dir / Path(self._logo_filename(away_abbr, away_id)),
                "away_logo_url": self._team_logo_url(away_team),
                "is_within_window": True,  # Whether game is within display window
                # The resolved favorites for this league (dynamic groups such
                # as AP_TOP_25 already expanded). Carried on the game so the
                # scroll/Vegas renderer, which only ever sees the game dict and
                # the raw config, can color a final score by the result.
                "favorite_teams": list(self.favorite_teams or []),
                # Defaults; a sport's _extract_game_details sets its own. No
                # period_text: the renderers read its absence (baseball) as
                # "draw ESPN's status text", e.g. "Final/10".
                "period": 0,
                "clock": "",
            }
            return details, home_team, away_team, status, situation
        except Exception as e:
            self.logger.error(
                f"Error extracting game details: {e} from event: {game_event.get('id')}",
                exc_info=True,
            )
            return None, None, None, None, None
