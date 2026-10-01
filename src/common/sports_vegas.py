"""Live Vegas cards for the sports scoreboards.

A scoreboard hands the Vegas ticker one card per game. As live elements
(src/plugin_system/vegas_elements.py) those cards change on the panel while
they scroll: a goal redraws its game's card and the ticker swaps it in place.
This module is what every scoreboard needs for that and would otherwise write
nine times:

- :func:`game_key` -- a stable key per game, so the ticker can tell which card
  a redraw belongs to however the slate is re-sorted.
- :class:`VegasCardCache` -- draws a card only when what it shows changed
  (its fingerprint), so an unchanged slate costs a dictionary lookup per game
  and a changed one only the cards that changed.
- :class:`StickyOdds` -- live odds are fetched only for games near the front
  of the rotation, so a card's odds come and go between polls; this keeps the
  last odds for a while instead of redrawing the card without them.
- :func:`dedupe_games` -- a game present in two managers' lists (live and
  recent, around the final whistle) appears once, its liveliest copy.
- :func:`finished_games` / :func:`with_finished_games` -- a game that has just
  gone final keeps its card, now showing FINAL, where its live card was,
  until the recent list (refreshed about hourly) takes it over.
- :func:`game_fingerprint` -- what a card is redrawn on by default: the whole
  game dict, frozen hashable.

SportsScrollDisplay.build_vegas_elements (src/common/sports_scroll.py) puts
them together; a plugin adopts it by implementing make_vegas_renderer().
"""

from __future__ import annotations

import time
from collections import OrderedDict
from typing import Any, Callable, Dict, Hashable, Iterable, List, Optional, Tuple

from PIL import Image

#: Which copy of a duplicated game wins: the liveliest.
_STATE_PRIORITY = {'in': 3, 'post': 2, 'pre': 1}


def _state(game: Dict[str, Any]) -> str:
    status = game.get('status')
    state = status.get('state') if isinstance(status, dict) else status
    if isinstance(state, str):
        return state
    if game.get('is_live'):
        return 'in'
    if game.get('is_final'):
        return 'post'
    return 'pre'


def _freeze(value: Any) -> Any:
    """A hashable, order-stable copy of feed data."""
    if isinstance(value, dict):
        return tuple(sorted((str(k), _freeze(v)) for k, v in value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(v) for v in value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)


def game_fingerprint(game: Dict[str, Any]) -> Hashable:
    """Everything in a game dict, hashable: a card drawn from it changes only if this does.

    The default card version. Nothing a card could draw is left out, so no
    field is ever frozen on the panel; the cost is a redraw when a field the
    card does not draw changes too, which feed data rarely does between polls.
    """
    frozen: Hashable = _freeze(game)
    return frozen


def game_key(game: Dict[str, Any]) -> str:
    """A key that names this game and nothing else, across polls.

    ``game:<league>:<id>`` from the feed's own id. A game without one falls
    back to its teams and start time, which is stable for the life of a game.
    """
    league = game.get('league') or 'game'
    game_id = game.get('id') or game.get('game_id')
    if game_id not in (None, ''):
        return f"game:{league}:{game_id}"
    away = game.get('away_abbr') or game.get('away_team') or '?'
    home = game.get('home_abbr') or game.get('home_team') or '?'
    start = game.get('start_time_utc') or game.get('start_time') or ''
    return f"game:{league}:{away}@{home}:{start}"


def dedupe_games(games: Iterable[Dict[str, Any]],
                 key_fn: Callable[[Dict[str, Any]], str] = game_key) -> List[Dict[str, Any]]:
    """Each game once, in first-seen order, keeping its liveliest copy.

    Around a final whistle a game can be in the live list (last poll) and the
    recent list (next poll) at once; two cards with one key would be refused
    by the ticker, and showing the game twice is wrong anyway.
    """
    chosen: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
    for game in games:
        key = key_fn(game)
        current = chosen.get(key)
        if current is None or _STATE_PRIORITY.get(_state(game), 0) > \
                _STATE_PRIORITY.get(_state(current), 0):
            chosen[key] = game
    return list(chosen.values())


def finished_games(
        live_managers: Iterable[Tuple[str, Any]]) -> List[Dict[str, Any]]:
    """Games that just left these live managers' lists, final ones as recent games.

    ``live_managers`` pairs each league with its live manager (None is
    skipped). Each manager reports what SportsLiveSharedMixin recorded
    (finished_games_snapshot, copies), with its league. A final game is
    drawn as a recent card. One a poll only judged over -- a tied end of
    regulation looks like that too -- keeps its last live state, so its card
    never says FINAL early; if play resumes the live list has it again, and
    dedupe_games keeps that copy.
    """
    finished: List[Dict[str, Any]] = []
    for league, manager in live_managers:
        snapshot = getattr(manager, 'finished_games_snapshot', None)
        if not callable(snapshot):
            continue
        for game in snapshot():
            game['league'] = league
            if game.get('is_final'):
                status = game.get('status')
                status = dict(status) if isinstance(status, dict) else {}
                status['state'] = 'post'
                game.update(status=status, is_live=False)
            finished.append(game)
    return finished


def with_finished_games(
    games: List[Dict[str, Any]], leagues: List[str],
    finished: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """The slate with games that just went final where their live cards were.

    A slate lists each league's games together, live ones first. Each
    finished game goes after its league's live games, ahead of the rest; a
    league with no games left in the slate is added at the end. A finished
    game the slate also has (the recent list caught up) is left for
    dedupe_games, which keeps one copy.
    """
    if not finished:
        return list(games), list(leagues)
    pending: "OrderedDict[Any, List[Dict[str, Any]]]" = OrderedDict()
    for game in finished:
        pending.setdefault(game.get('league'), []).append(game)
    merged: List[Dict[str, Any]] = []
    for index, game in enumerate(games):
        league = game.get('league')
        if league in pending and _state(game) != 'in':
            merged.extend(pending.pop(league))
        merged.append(game)
        following = games[index + 1] if index + 1 < len(games) else None
        if league in pending and (following is None or following.get('league') != league):
            merged.extend(pending.pop(league))       # the league's games were all live
    leagues = list(leagues)
    for league, rest in pending.items():
        merged.extend(rest)
        if league not in leagues:
            leagues.append(league)
    return merged, leagues


class VegasCardCache:
    """Cards drawn once per fingerprint, kept for as long as their game is.

    ``element(key, fingerprint, render)`` returns a VegasElement whose image is
    ``render()``'s -- called only when the fingerprint differs from the one the
    cached card was drawn for. The fingerprint is also the element's version,
    so the ticker skips unchanged cards without comparing pixels.

    Bounded: keys not passed to :meth:`retain` after a slate are dropped, and
    at most ``max_entries`` are ever held (oldest first).
    """

    def __init__(self, max_entries: int = 96) -> None:
        self.max_entries = max(1, int(max_entries))
        self._cards: "OrderedDict[str, Tuple[Hashable, Image.Image]]" = OrderedDict()
        self.renders = 0

    def element(self, key: str, fingerprint: Hashable,
                render: Callable[[], Image.Image], live: bool = True) -> Any:
        from src.plugin_system.vegas_elements import VegasElement

        cached = self._cards.get(key)
        if cached is not None and cached[0] == fingerprint:
            self._cards.move_to_end(key)
            image = cached[1]
        else:
            image = render()
            self.renders += 1
            self._cards[key] = (fingerprint, image)
            self._cards.move_to_end(key)
            while len(self._cards) > self.max_entries:
                self._cards.popitem(last=False)
        return VegasElement(key=key, image=image, version=fingerprint, live=live)

    def retain(self, keys: Iterable[str]) -> None:
        """Forget every card whose key is not in ``keys``."""
        keep = set(keys)
        for key in [k for k in self._cards if k not in keep]:
            self._cards.pop(key, None)

    def clear(self) -> None:
        self._cards.clear()

    def __len__(self) -> int:
        return len(self._cards)


class StickyOdds:
    """Keep a game's last odds on its card while a live poll leaves them out.

    Live odds are fetched only for games near the front of the rotation
    (src/common/sports_fetch.py), so the same game's dict has odds on one poll
    and none on the next. Drawn as-is that redraws the card every poll with
    the odds flickering in and out. ``apply`` returns the game with its last
    non-empty odds put back, for up to ``ttl_s`` seconds after they were seen.
    """

    def __init__(self, ttl_s: float = 600.0) -> None:
        self.ttl_s = float(ttl_s)
        self._seen: Dict[str, Tuple[float, Any]] = {}

    def apply(self, key: str, game: Dict[str, Any],
              now: Optional[float] = None) -> Dict[str, Any]:
        now = time.monotonic() if now is None else now
        odds = game.get('odds')
        if odds:
            self._seen[key] = (now, odds)
            return game
        seen = self._seen.get(key)
        if seen is None or now - seen[0] > self.ttl_s:
            self._seen.pop(key, None)
            return game
        refilled = dict(game)
        refilled['odds'] = seen[1]
        return refilled

    def retain(self, keys: Iterable[str]) -> None:
        keep = set(keys)
        for key in [k for k in self._seen if k not in keep]:
            self._seen.pop(key, None)
