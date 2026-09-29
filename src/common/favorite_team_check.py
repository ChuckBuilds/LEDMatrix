"""
Explain an empty screen: a wrong team code, or a season that has not started.

Favourite teams are matched by exact ESPN abbreviation, so a plausible-looking
code silently matches nothing and the plugin shows an empty screen with no hint
that the code is at fault. The codes are not always guessable — ESPN calls
Alabama ``ALA`` rather than ``BAMA``, and Golden State ``GS`` rather than
``GSW``. Between seasons a perfectly correct code produces the same empty
screen for a completely different reason, and the two were indistinguishable
from the logs.

This module is diagnostics only. It runs on a daemon thread, once per league per
process, and every failure is swallowed: it must never delay a frame or change
what is displayed.
"""

import difflib
import logging
import re
import threading
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Set, Tuple

TEAMS_URL = "https://site.api.espn.com/apis/site/v2/sports/{path}/teams?limit=1000"
SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/{path}/scoreboard"
REQUEST_TIMEOUT = 15


class FavoriteTeamCheck:
    """
    Validates configured favourite team codes against ESPN, and says so in the log.

    ``leagues`` maps the plugin's own league key to a
    ``(human readable name, ESPN sport/league path)`` pair, e.g.
    ``{'nhl': ('NHL', 'hockey/nhl')}``.
    """

    # How far out the next fixture has to be before it is worth mentioning.
    # An off day or two is normal mid-season and saying so would just be noise.
    GAP_DAYS = 3

    def __init__(self, logger: Optional[logging.Logger],
                 leagues: Dict[str, Tuple[str, str]]) -> None:
        self.logger = logger or logging.getLogger(__name__)
        self.leagues = leagues
        self._checked: Set[str] = set()
        self._lock = threading.Lock()

    def reset(self) -> None:
        """Re-check on the next call, e.g. after the user edits the config."""
        with self._lock:
            self._checked.clear()

    def schedule(self, league_key: str, favorites: Iterable[str]) -> None:
        """Check one league in the background, at most once per process."""
        try:
            favorites = [str(f) for f in (favorites or []) if str(f).strip()]
            if not favorites or league_key not in self.leagues:
                return
            with self._lock:
                if league_key in self._checked:
                    return
                self._checked.add(league_key)
            threading.Thread(
                target=self._run, args=(league_key, favorites),
                name="favorite-team-check", daemon=True,
            ).start()
        except Exception:
            pass  # nosec B110 - a diagnostic must never be the reason an update fails  # nosemgrep

    def _run(self, league_key: str, favorites) -> None:
        try:
            self._check(league_key, favorites)
        except Exception as exc:
            self.logger.debug("Favorite team check failed for %s: %s",
                              league_key, exc)

    def _check(self, league_key: str, favorites) -> None:
        name, path = self.leagues[league_key]

        try:
            teams = self._fetch_teams(path)
        except Exception as exc:
            self.logger.debug("Could not verify %s favorite teams: %s", name, exc)
            return
        if not teams:
            # Some ESPN endpoints (college lacrosse) return no teams at all.
            # Nothing can be concluded, so say nothing.
            return

        # Dynamic groups like AP_TOP_25 are expanded elsewhere; they are not
        # team codes and must not be reported as bad ones.
        codes = [f for f in favorites if not self._is_dynamic(f)]
        recognised = [f for f in codes if f in teams]
        unknown = [f for f in codes if f not in teams]

        for code in unknown:
            self.logger.warning(
                "%s favorite team %r is not a %s team code.%s "
                "Every code this league accepts is listed at %s.",
                name, code, name, self._suggest(code, teams),
                TEAMS_URL.format(path=path),
            )

        if codes and not recognised:
            self.logger.warning(
                "%s has no recognised favorite teams, so nothing will be shown "
                "for it. Codes must be ESPN abbreviations, e.g. %s.",
                name, ", ".join("{} ({})".format(a, n)
                                for a, n in list(sorted(teams.items()))[:3]),
            )
            return

        if not recognised:
            return

        # Codes are fine, so check the other cause of an empty screen.
        try:
            note = self._schedule_note(path)
        except Exception as exc:
            self.logger.debug("Could not check the %s schedule: %s", name, exc)
            return

        if note:
            self.logger.info(
                "%s favorite teams %s look correct, but %s. An empty display "
                "until then is expected, not a configuration problem.",
                name, ", ".join(recognised), note,
            )
        else:
            self.logger.info("%s favorite teams recognised: %s",
                             name, ", ".join(recognised))

    @staticmethod
    def _is_dynamic(code: str) -> bool:
        upper = (code or "").strip().upper()
        return upper.startswith("AP_") or upper.startswith("TOP_") or "TOP_" in upper

    @staticmethod
    def _fetch_teams(path: str) -> Dict[str, str]:
        """ESPN's {abbreviation: display name} for a league.

        ``limit=1000`` is required: the default page size truncates the NCAA
        responses to roughly half their teams, which makes valid codes look wrong.
        """
        import requests

        payload = requests.get(TEAMS_URL.format(path=path),
                               timeout=REQUEST_TIMEOUT).json()
        entries = payload['sports'][0]['leagues'][0]['teams']
        return {
            t['team']['abbreviation']: t['team']['displayName']
            for t in entries if t.get('team', {}).get('abbreviation')
        }

    @classmethod
    def _schedule_note(cls, path: str) -> Optional[str]:
        """
        Why the league has nothing to show, as a clause, or ``None`` if it does.

        Two things make this harder than reading ``events``:

        * An out-of-season league does not come back empty. ESPN rolls the
          scoreboard forward to the next day that has fixtures, so in July the
          NHL endpoint returns seven September games. Emptiness cannot be the
          signal; the date of those games is, and it is more useful anyway.
        * A *finished* season rolls nowhere and returns its last game instead,
          months in the past — so dates have to be filtered to the future
          before the soonest one means anything.
        """
        import requests

        payload = requests.get(SCOREBOARD_URL.format(path=path),
                               timeout=REQUEST_TIMEOUT).json()

        event_dates = [cls._parse_date(e.get('date'))
                       for e in payload.get('events') or []]
        calendar_dates = []
        for entry in (payload.get('leagues') or [{}])[0].get('calendar') or []:
            calendar_dates.append(cls._parse_date(
                entry if isinstance(entry, str) else entry.get('startDate')))

        # Count the last day as current, rather than filtering on "later than
        # right now": a game that began a few hours ago still means the league
        # has something on, and dropping it would report a live slate as a
        # finished season. A day's grace also keeps this correct whatever the
        # user's timezone, since these timestamps are UTC.
        now = datetime.now(timezone.utc)

        def future(candidates):
            return sorted(d for d in candidates if d and (now - d).days < 1)

        # Events are fixtures; the calendar is week and phase boundaries,
        # which routinely open days before their first game (an NFL week 1
        # calendar entry starts the weekend before the Thursday opener).
        # Reading the two together reported the earliest boundary as a game
        # date -- "nothing on until 06 September" for a league whose first
        # snap is the 10th. The calendar only gets a say when the scoreboard
        # has no events at all to roll forward to: events that exist but are
        # all in the past mean the season is over, and an offseason calendar
        # phase must not be dressed up as its next game.
        if any(event_dates):
            upcoming = future(event_dates)
        else:
            upcoming = future(calendar_dates)
        if not upcoming:
            if not any(event_dates) and not any(calendar_dates):
                return None  # Nothing published either way; draw no conclusion.
            return ("the season has finished and the next one's fixtures are "
                    "not published yet")

        # A day or two out is just an off day, and saying so would be noise.
        if (upcoming[0] - now).days < cls.GAP_DAYS:
            return None
        return "the league has nothing on until {}".format(
            upcoming[0].strftime('%d %B %Y'))

    @staticmethod
    def _parse_date(raw) -> Optional[datetime]:
        if not raw or not isinstance(raw, str):
            return None
        try:
            return datetime.fromisoformat(raw.replace('Z', '+00:00'))
        except ValueError:
            return None

    @classmethod
    def _suggest(cls, code: str, teams: Dict[str, str]) -> str:
        """Nearest matching code for a typo, as a ready-to-log clause."""
        upper = (code or "").strip().upper()
        if not upper or code in teams:
            return ""

        # Right code, wrong case — matching is case-sensitive. Guard on the case
        # actually differing, so a valid code never draws this message.
        for abbr in teams:
            if abbr.upper() == upper:
                return " Codes are case-sensitive; use {!r} ({}).".format(
                    abbr, teams[abbr])

        ranked = cls._rank(upper, (a for a, n in teams.items()
                                   if cls._abbreviates(upper, n)), teams)
        if not ranked:
            # Nicknames are often a fragment of a word rather than its initials:
            # 'BAMA' sits inside 'Alabama' but abbreviates nothing in it. Require
            # three characters, since shorter fragments match far too much.
            if len(upper) >= 3:
                ranked = cls._rank(
                    upper,
                    (a for a, n in teams.items()
                     if any(upper in w for w in cls._words(n))),
                    teams)

        if len(ranked) == 1:
            return " Closest match is {!r} ({}).".format(
                ranked[0], teams[ranked[0]])
        if ranked:
            return " Did you mean {}?".format(", ".join(
                "{!r} ({})".format(a, teams[a]) for a in ranked[:3]))

        # Otherwise fall back to similarity, against names before codes: a name
        # gives more characters to compare and so produces fewer ties.
        names = {n.upper(): a for a, n in teams.items()}
        hits = difflib.get_close_matches(upper, list(names), n=1, cutoff=0.6)
        if hits:
            abbr = names[hits[0]]
            return " Closest match is {!r} ({}).".format(abbr, teams[abbr])

        code_hits = difflib.get_close_matches(upper, list(teams), n=1, cutoff=0.6)
        if code_hits:
            return " Closest match is {!r} ({}).".format(
                code_hits[0], teams[code_hits[0]])
        return ""

    @staticmethod
    def _words(name: str):
        return [w for w in re.split(r'[^A-Za-z0-9]+', (name or '').upper()) if w]

    @classmethod
    def _rank(cls, code: str, candidates, teams: Dict[str, str]):
        """
        Order candidate codes best-first.

        A code that picks up the *first* word of the name wins, because that is
        how people shorten team names: 'SCAR' for South Carolina starts at
        'South', whereas for Rutgers Scarlet Knights it starts mid-name. Without
        this the tie is broken alphabetically and the obvious answer can land
        third in the list.
        """
        def key(abbr):
            words = cls._words(teams.get(abbr, ''))
            first_word_hit = bool(words) and words[0].startswith(code[:1])
            return (not first_word_hit, len(abbr), abbr)

        return sorted(set(candidates), key=key)

    @staticmethod
    def _abbreviates(code: str, name: str) -> bool:
        """
        Whether ``code`` reads as an abbreviation of ``name``.

        Each part of the code must be a prefix of one of the name's words, taken
        in order — which is how people actually shorten team names. Plain string
        similarity is no use for three-letter codes: 'MUN' scores identically
        against 'MAN' and 'SUN', so Manchester United and Sunderland tie and the
        suggestion is a coin flip. This rule separates them, because 'MUN'
        splits as M-anchester UN-ited while Sunderland has no word starting M.
        """
        words = [w for w in re.split(r'[^A-Za-z0-9]+', (name or '').upper()) if w]

        def consume(rest: str, remaining: List[str]) -> bool:
            if not rest:
                return True
            if not remaining:
                return False
            head, tail = remaining[0], remaining[1:]
            # Skip this word entirely, as in "Manchester United" -> "UTD".
            if consume(rest, tail):
                return True
            for size in range(1, min(len(rest), len(head)) + 1):
                if head.startswith(rest[:size]) and consume(rest[size:], tail):
                    return True
            return False

        return consume((code or "").strip().upper(), words)
