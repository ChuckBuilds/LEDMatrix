"""Live Vegas cards for the scoreboards (src/common/sports_vegas.py, sports_scroll).

A scoreboard adopts live cards by implementing make_vegas_renderer(); the
shared code then draws one card per game, only when what the card shows
changed, keyed by the game so the ticker can swap it in place.
"""
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.common import sports_vegas  # noqa: E402
from src.common.sports_scroll import SportsScrollDisplay, SportsScrollDisplayManager  # noqa: E402
from src.common.sports_shared import SportsLiveSharedMixin  # noqa: E402
from src.plugin_system.vegas_elements import VegasElement  # noqa: E402

W, H = 128, 32


def _game(gid, league="nfl", home=0, away=0, state="in", clock="10:00", odds=None):
    return {"id": gid, "league": league, "home_abbr": "HOM", "away_abbr": "AWY",
            "home_score": home, "away_score": away,
            "status": {"state": state}, "clock": clock, "odds": odds}


class _Renderer:
    def __init__(self, width):
        self.width = width
        self.calls = []
        self.rankings = None

    def render_game_card(self, game, game_type):
        self.calls.append((game["id"], game_type, game.get("odds")))
        return Image.new("RGB", (self.width, H), (game["home_score"] * 10 % 255, 0, 0))

    def set_rankings_cache(self, rankings):
        self.rankings = rankings


class _Display(SportsScrollDisplay):
    made = 0

    def make_vegas_renderer(self, card_width, rankings_cache=None):
        type(self).made += 1
        self.renderer = _Renderer(card_width)
        return self.renderer

    def _load_separator_icons(self):
        self._separator_icons = {"nfl": Image.new("RGBA", (10, 10), (255, 255, 255, 255)),
                                 "ncaa_fb": Image.new("RGBA", (10, 10), (0, 255, 0, 255))}


class _Plain(SportsScrollDisplay):
    pass


def _dm():
    return SimpleNamespace(width=W, height=H, matrix=None, refresh_hz=100.0,
                           set_scrolling_state=lambda *a, **k: None)


def _display(cls=_Display, **config):
    return cls(_dm(), config)


def _fp(game):
    return (game["home_score"], game["away_score"], game["clock"],
            bool(game.get("odds")))


# -- building blocks ------------------------------------------------------------


def test_game_keys_name_the_game():
    assert sports_vegas.game_key(_game("401")) == "game:nfl:401"
    assert sports_vegas.game_key({"league": "mlb", "away_abbr": "A", "home_abbr": "B",
                                  "start_time": "2026-09-30T19:00Z"}) \
        == "game:mlb:A@B:2026-09-30T19:00Z"


def test_dedupe_keeps_the_liveliest_copy_in_first_seen_order():
    games = [_game("1", state="post", home=3), _game("2", state="pre"),
             _game("1", state="in", home=2)]
    kept = sports_vegas.dedupe_games(games)
    assert [g["id"] for g in kept] == ["1", "2"]
    assert kept[0]["status"]["state"] == "in"


def test_the_card_cache_draws_only_on_a_new_fingerprint():
    cache = sports_vegas.VegasCardCache()
    drawn = []

    def render(value):
        drawn.append(value)
        return Image.new("RGB", (4, H))

    first = cache.element("k", 1, lambda: render(1))
    again = cache.element("k", 1, lambda: render(2))
    changed = cache.element("k", 2, lambda: render(3))
    assert drawn == [1, 3]
    assert isinstance(first, VegasElement) and first.version == 1
    assert again.image is first.image and changed.version == 2


def test_the_card_cache_forgets_games_not_retained_and_is_bounded():
    cache = sports_vegas.VegasCardCache(max_entries=3)
    for key in "abcde":
        cache.element(key, 0, lambda: Image.new("RGB", (4, H)))
    assert len(cache) == 3
    cache.retain(["e"])
    assert len(cache) == 1


def test_sticky_odds_fill_a_gap_and_then_expire():
    odds = sports_vegas.StickyOdds(ttl_s=60)
    game = _game("1", odds={"spread": -3})
    assert odds.apply("k", game, now=0)["odds"] == {"spread": -3}
    later = _game("1", odds=None)
    refilled = odds.apply("k", later, now=30)
    assert refilled["odds"] == {"spread": -3}
    assert later["odds"] is None                  # the feed's dict is untouched
    assert odds.apply("k", later, now=100)["odds"] is None


# -- SportsScrollDisplay.build_vegas_elements ---------------------------------------


def test_a_slate_becomes_keyed_cards_with_league_separators():
    display = _display()
    games = [_game("1"), _game("2"), _game("3", league="ncaa_fb")]
    elements = display.build_vegas_elements(games, ["nfl", "ncaa_fb"], fingerprint=_fp)
    keys = [e.key for e in elements]
    assert keys == ["sep:0:nfl", "game:nfl:1", "game:nfl:2",
                    "sep:1:ncaa_fb", "game:ncaa_fb:3"]
    assert [e.live for e in elements] == [False, True, True, False, True]
    card = elements[1]
    assert card.image.size == (display._get_scroll_settings()["game_card_width"], H)


def test_only_changed_games_are_redrawn():
    display = _display()
    games = [_game("1"), _game("2"), _game("3")]
    display.build_vegas_elements(games, ["nfl"], fingerprint=_fp)
    renderer = display.renderer
    assert len(renderer.calls) == 3
    display.build_vegas_elements(games, ["nfl"], fingerprint=_fp)
    assert len(renderer.calls) == 3               # nothing changed
    games[1] = _game("2", home=7)
    display.build_vegas_elements(games, ["nfl"], fingerprint=_fp)
    assert [c[0] for c in renderer.calls[3:]] == ["2"]


def test_a_clock_change_redraws_that_card_when_the_fingerprint_has_the_clock():
    display = _display()
    display.build_vegas_elements([_game("1"), _game("2")], ["nfl"], fingerprint=_fp)
    display.build_vegas_elements([_game("1", clock="9:41"), _game("2")], ["nfl"],
                                 fingerprint=_fp)
    assert [c[0] for c in display.renderer.calls[2:]] == ["1"]


def test_the_renderer_is_built_once_not_per_slate():
    _Display.made = 0
    display = _display()
    for _ in range(3):
        display.build_vegas_elements([_game("1")], ["nfl"], fingerprint=_fp)
    assert _Display.made == 1


def test_a_card_keeps_its_odds_through_a_poll_without_them():
    display = _display()
    display.build_vegas_elements([_game("1", odds={"spread": -3})], ["nfl"], fingerprint=_fp)
    display.build_vegas_elements([_game("1", odds=None)], ["nfl"], fingerprint=_fp)
    assert len(display.renderer.calls) == 1       # no redraw without the odds


def test_game_dicts_are_never_mutated():
    display = _display()
    game = _game("1", odds=None)
    snapshot = dict(game)
    display.build_vegas_elements([game], ["nfl"], fingerprint=_fp)
    assert game == snapshot


def test_a_sport_without_a_renderer_stays_on_its_ordinary_content():
    manager = SportsScrollDisplayManager(_dm(), {})
    manager.display_class = _Plain
    assert manager.get_vegas_elements_for("mixed", [_game("1")], ["nfl"]) is None


def test_a_renderer_that_fails_falls_back_instead_of_raising():
    class Broken(_Display):
        def render_vegas_card(self, renderer, game):
            raise KeyError("home_logo")

    manager = SportsScrollDisplayManager(_dm(), {})
    manager.display_class = Broken
    assert manager.get_vegas_elements_for("mixed", [_game("1")], ["nfl"]) is None


def test_the_manager_hands_back_the_elements():
    manager = SportsScrollDisplayManager(_dm(), {})
    manager.display_class = _Display
    elements = manager.get_vegas_elements_for("mixed", [_game("1")], ["nfl"],
                                              fingerprint=_fp)
    assert [e.key for e in elements] == ["sep:0:nfl", "game:nfl:1"]


# -- the finished-game capture ------------------------------------------------------


class _Live(SportsLiveSharedMixin):
    def __init__(self):
        self.live_games = [_game("1"), _game("2")]


def test_a_game_that_was_live_is_remembered_when_it_ends():
    live = _Live()
    live._record_finished_game(_game("1", state="post", home=24))
    live._record_finished_game(_game("99", state="post"))     # never live here
    finished = live.finished_games_snapshot()
    assert [g["id"] for g in finished] == ["1"]
    assert finished[0]["home_score"] == 24
    finished[0]["league"] = "changed"
    assert live.finished_games_snapshot()[0]["league"] == "nfl"   # copies


def test_a_finished_game_is_forgotten_after_its_ttl(monkeypatch):
    live = _Live()
    live._record_finished_game(_game("1", state="post"))
    real = time.monotonic
    monkeypatch.setattr(time, "monotonic", lambda: real() + live.FINISHED_GAME_TTL + 1)
    assert live.finished_games_snapshot() == []


def test_final_replaces_the_live_card_in_place():
    # The same key, so the ticker swaps the card rather than adding one.
    display = _display()
    before = display.build_vegas_elements([_game("1", home=7)], ["nfl"], fingerprint=_fp)
    after = display.build_vegas_elements(
        sports_vegas.dedupe_games([_game("1", state="post", home=14)]), ["nfl"],
        fingerprint=_fp)
    assert before[-1].key == after[-1].key
    assert before[-1].version != after[-1].version


def test_settings_are_looked_up_once_per_league_not_per_update(monkeypatch):
    display = _display()
    calls = []
    real = display._get_scroll_settings
    monkeypatch.setattr(display, "_get_scroll_settings",
                        lambda league=None: calls.append(league) or real(league))
    slate = [_game("1"), _game("2", league="ncaa_fb")]
    for _ in range(3):
        display.build_vegas_elements(slate, ["nfl", "ncaa_fb"], fingerprint=_fp)
    assert sorted(calls) == ["ncaa_fb", "nfl"]


def test_each_card_takes_its_own_leagues_width():
    display = _display(nfl={"scroll_settings": {"game_card_width": 100}},
                       ncaa_fb={"scroll_settings": {"game_card_width": 140}})
    display.SCROLL_LEAGUE_KEYS = ("nfl", "ncaa_fb")
    slate = [_game("1"), _game("2", league="ncaa_fb")]
    widths = {e.key: e.image.width
              for e in display.build_vegas_elements(slate, ["nfl", "ncaa_fb"], fingerprint=_fp)
              if e.live}
    # ...and keeps it when the other league has nothing to show.
    alone = display.build_vegas_elements(slate[1:], ["ncaa_fb"], fingerprint=_fp)
    assert widths == {"game:nfl:1": 100, "game:ncaa_fb:2": 140}
    assert [e.image.width for e in alone if e.live] == [140]


def test_a_rank_change_redraws_the_card():
    display = _display()
    display.build_vegas_elements([_game("1")], ["nfl"], {"HOM": 5}, fingerprint=_fp)
    drawn = display._vegas_cards.renders
    display.build_vegas_elements([_game("1")], ["nfl"], {"HOM": 5}, fingerprint=_fp)
    assert display._vegas_cards.renders == drawn
    display.build_vegas_elements([_game("1")], ["nfl"], {"HOM": 3}, fingerprint=_fp)
    assert display._vegas_cards.renders == drawn + 1


def test_finished_games_come_back_as_recent_games_of_their_league():
    live = _Live()
    live._record_finished_game(dict(_game("1", home=24), is_final=True))
    finished = sports_vegas.finished_games([("ncaa_fb", live), ("nfl", None)])
    assert [(g["id"], g["league"], g["status"]["state"], g["is_live"])
            for g in finished] == [("1", "ncaa_fb", "post", False)]


def test_a_game_only_judged_over_is_not_drawn_final():
    # A tied end of regulation looks over to the heuristics; overtime may follow.
    live = _Live()
    live._record_finished_game(_game("1", home=24, clock="0:00"))
    finished = sports_vegas.finished_games([("nfl", live)])
    assert finished[0]["status"]["state"] == "in"
    assert not finished[0].get("is_final")
    # ...and when play resumes, the live list's copy is the one kept.
    resumed = _game("1", home=24, clock="15:00")
    merged, _ = sports_vegas.with_finished_games([resumed], ["nfl"], finished)
    assert sports_vegas.dedupe_games(merged) == [resumed]


def test_the_snapshot_copes_with_games_recorded_while_it_reads():
    # A baseball league finishing its update in the background records games
    # off the plugin's lock while the ticker takes snapshots.
    import sys
    import threading
    live = _Live()
    live.live_games = [_game(str(i)) for i in range(400)]
    errors = []
    stop = threading.Event()

    def record():
        for i in range(400):
            live._record_finished_game(dict(_game(str(i)), is_final=True))
        stop.set()

    def read():
        while not stop.is_set():
            try:
                live.finished_games_snapshot()
            except RuntimeError as exc:
                errors.append(exc)
                return

    previous = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        readers = [threading.Thread(target=read) for _ in range(2)]
        for t in readers:
            t.start()
        record()
        for t in readers:
            t.join(10)
    finally:
        sys.setswitchinterval(previous)
    assert not errors
    assert len(live.finished_games_snapshot()) == 400


def test_a_held_game_takes_the_final_details_but_keeps_its_expiry(monkeypatch):
    live = _Live()
    live._record_finished_game(_game("1", home=24, clock="0:00"))   # judged over
    live.live_games = []                                            # dropped
    now = time.monotonic()
    monkeypatch.setattr(time, "monotonic", lambda: now + 60)
    live._record_finished_game(dict(_game("1", home=27), is_final=True))
    finished = sports_vegas.finished_games([("nfl", live)])
    assert finished[0]["home_score"] == 27 and finished[0]["status"]["state"] == "post"
    monkeypatch.setattr(time, "monotonic", lambda: now + live.FINISHED_GAME_TTL + 1)
    assert live.finished_games_snapshot() == []


def _slate(*games):
    return [_game(gid, league=league, state=state) for gid, league, state in games]


@pytest.mark.parametrize("games,leagues,expected_ids,expected_leagues", [
    # After the league's live games, ahead of its recent and upcoming ones.
    (_slate(("2", "nfl", "in"), ("3", "nfl", "post"), ("4", "nfl", "pre"),
            ("5", "ncaa_fb", "in")),
     ["nfl", "ncaa_fb"], ["2", "F", "3", "4", "5"], ["nfl", "ncaa_fb"]),
    # A league whose games were all live.
    (_slate(("2", "nfl", "in"), ("5", "ncaa_fb", "in")),
     ["nfl", "ncaa_fb"], ["2", "F", "5"], ["nfl", "ncaa_fb"]),
    # No live games: first in its league.
    (_slate(("5", "ncaa_fb", "pre"), ("3", "nfl", "post")),
     ["ncaa_fb", "nfl"], ["5", "F", "3"], ["ncaa_fb", "nfl"]),
    # Its league has nothing else to show.
    (_slate(("5", "ncaa_fb", "pre")), ["ncaa_fb"], ["5", "F"], ["ncaa_fb", "nfl"]),
])
def test_a_finished_game_goes_where_its_live_card_was(games, leagues, expected_ids,
                                                      expected_leagues):
    final = _game("F", league="nfl", state="post")
    merged, merged_leagues = sports_vegas.with_finished_games(games, leagues, [final])
    assert [g["id"] for g in merged] == expected_ids
    assert merged_leagues == expected_leagues


def test_the_default_fingerprint_follows_every_field_and_ignores_key_order():
    game = _game("1", odds={"spread": -3, "details": ["a", "b"]})
    same = dict(reversed(list(game.items())))
    assert sports_vegas.game_fingerprint(game) == sports_vegas.game_fingerprint(same)
    hash(sports_vegas.game_fingerprint(game))
    for field, value in [("clock", "9:59"), ("odds", {"spread": -3, "details": ["a"]}),
                         ("status", {"state": "post"})]:
        assert sports_vegas.game_fingerprint(dict(game, **{field: value})) != \
            sports_vegas.game_fingerprint(game)


def test_no_finished_games_leaves_the_slate_alone():
    games = _slate(("2", "nfl", "in"))
    merged, leagues = sports_vegas.with_finished_games(games, ["nfl"], [])
    assert merged == games and merged is not games and leagues == ["nfl"]


@pytest.mark.parametrize("state,expected", [("in", "live"), ("post", "recent"),
                                            ("pre", "upcoming")])
def test_the_default_card_type_follows_the_game_state(state, expected):
    display = _display()
    display.build_vegas_elements([_game("1", state=state)], ["nfl"], fingerprint=_fp)
    assert display.renderer.calls[0][1] == expected


def test_ranks_cleared_since_are_not_kept_by_the_reused_renderer():
    display = _display()
    display.build_vegas_elements([_game("1")], ["nfl"], {"HOM": 5}, fingerprint=_fp)
    assert display.renderer.rankings == {"HOM": 5}
    display.build_vegas_elements([_game("1")], ["nfl"], None, fingerprint=_fp)
    assert display.renderer.rankings == {}
