"""Drop the parts of an ESPN scoreboard payload no scoreboard reads.

The sports scoreboards cache their Recent/Upcoming window (14 days back, 7
ahead) as the raw ESPN response, and that record stays parsed in the memory
cache for as long as it is fresh. Most of it is never drawn. Measured on hdpi
(2026-10-02) the MLB window was 3.35MB of JSON and 13.5MB of Python objects,
and the five windows together ~40MB, mostly in:

* ``competitors[].leaders`` / ``competitions[].leaders`` -- per-team and
  per-game stat leaders (28% of the MLB window)
* ``competitors[].team.links`` / ``event.links`` -- web and app URLs
* ``status.featuredAthletes`` and ``competitors[].probables`` -- athlete
  cards with headshots and season stats
* ``competitions[].headlines`` / ``highlights`` -- article and video blurbs
  (28% of the college-football window)
* ``competitions[].geoBroadcasts``

None of those keys is read by core or by any plugin in ledmatrix-plugins
(checked 2026-10-02 across every scoreboard, the odds ticker and the
leaderboard), while everything that is read -- odds, records, linescores,
situation, statistics, notes, broadcasts, venue -- is kept. Dropping them
takes the five windows from ~40MB to ~12MB of parsed objects and the files from
10.6MB to 3.0MB, so the reads that parse an expired window on the render
thread get 3-4x cheaper too.

:func:`slim_scoreboard_payload` changes the payload in place, and only ever
removes the keys listed here: anything it does not know about is left alone.
"""

from typing import Any, Dict
from urllib.parse import urlsplit

# Per level of the payload, the keys removed. Kept deliberately explicit:
# adding a key here means checking that nothing reads it first.
_EVENT_DROP = ("links",)
_COMPETITION_DROP = ("leaders", "headlines", "highlights", "geoBroadcasts")
_STATUS_DROP = ("featuredAthletes",)
_COMPETITOR_DROP = ("leaders", "probables")
_TEAM_DROP = ("links",)


def is_espn_scoreboard_url(url: Any) -> bool:
    """Whether ``url`` is an ESPN site-API scoreboard endpoint."""
    if not isinstance(url, str):
        return False
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    if host != "espn.com" and not host.endswith(".espn.com"):
        return False
    return parts.path.rstrip("/").endswith("/scoreboard")


def _drop(obj: Any, keys) -> None:
    if isinstance(obj, dict):
        for key in keys:
            obj.pop(key, None)


def slim_scoreboard_payload(payload: Any) -> Any:
    """Remove the unread parts of an ESPN scoreboard payload, in place.

    Returns ``payload`` for convenience. Anything that is not shaped like a
    scoreboard (not a dict, no ``events`` list, odd entries) is passed over
    untouched rather than raising.
    """
    if not isinstance(payload, dict):
        return payload
    events = payload.get("events")
    if not isinstance(events, list):
        return payload
    for event in events:
        if not isinstance(event, dict):
            continue
        _drop(event, _EVENT_DROP)
        competitions = event.get("competitions")
        if not isinstance(competitions, list):
            continue
        for competition in competitions:
            if not isinstance(competition, dict):
                continue
            _drop(competition, _COMPETITION_DROP)
            _drop(competition.get("status"), _STATUS_DROP)
            competitors = competition.get("competitors")
            if not isinstance(competitors, list):
                continue
            for competitor in competitors:
                if not isinstance(competitor, dict):
                    continue
                _drop(competitor, _COMPETITOR_DROP)
                _drop(competitor.get("team"), _TEAM_DROP)
    return payload


__all__ = ["is_espn_scoreboard_url", "slim_scoreboard_payload"]
