"""Tests for the opt-in sports capabilities (phase B2).

Two properties matter beyond "the code works":

1. **Opting out is structural.** A mode class that does not mix in
   ``CelebrationMixin`` must have none of its attributes or methods — not
   merely a disabled flag. ``TestOptOutIsStructural`` asserts that directly,
   because it is the property the whole mixin design exists to buy.

2. **The promoted behavior matches the plugin copies.** These bodies came from
   afl/soccer/nrl (goal dialect) and football (score dialect); the tests pin
   the reconciled behavior of both, including the three seams where the
   lineages genuinely disagreed.

See docs/SPORTS_UNIFICATION.md.
"""

import sys
from unittest.mock import MagicMock

import pytest

sys.modules.setdefault("rgbmatrix", MagicMock())

from src.base_classes.sports.capabilities import (  # noqa: E402
    CelebrationMixin,
    RotationStrategy,
    SimpleRotation,
    SmoothWeightedRotation,
    WeightedCycleRotation,
    get_rotation_strategy,
    register_rotation_strategy,
)


def game(gid, home="HOM", away="AWY", home_score=0, away_score=0, **extra):
    g = {
        "id": gid,
        "home_abbr": home,
        "away_abbr": away,
        "home_id": f"{gid}-h",
        "away_id": f"{gid}-a",
        "home_score": home_score,
        "away_score": away_score,
    }
    g.update(extra)
    return g


# ---------------------------------------------------------------------------
# Rotation strategies
# ---------------------------------------------------------------------------

def boost(favorites, factor=3):
    """A weight_for callable of the shape the plugins supply."""
    return lambda g: factor if g.get("home_abbr") in favorites else 1


class TestRegistry:
    @pytest.mark.parametrize("name,cls", [
        ("simple", SimpleRotation),
        ("weighted", WeightedCycleRotation),
        ("swrr", SmoothWeightedRotation),
    ])
    def test_builtin_names_resolve(self, name, cls):
        assert isinstance(get_rotation_strategy(name), cls)

    def test_unknown_name_falls_back_to_simple(self):
        """The name comes from user config; a typo should cost the boost, not
        the scoreboard."""
        assert isinstance(get_rotation_strategy("typo"), SimpleRotation)

    def test_a_plugin_can_register_its_own(self):
        class MyRotation(SimpleRotation):
            pass

        register_rotation_strategy("test-only", MyRotation)
        try:
            assert isinstance(get_rotation_strategy("test-only"), MyRotation)
            assert MyRotation.name == "test-only"
        finally:
            from src.base_classes.sports.capabilities import rotation
            rotation._REGISTRY.pop("test-only", None)

    def test_empty_name_is_rejected(self):
        with pytest.raises(ValueError):
            register_rotation_strategy("", SimpleRotation)

    def test_weight_for_is_optional(self):
        """Default weights are equal, so every strategy degenerates to a plain
        round robin — the pre-boost behavior."""
        games = [game("a"), game("b"), game("c")]
        for name in ("simple", "weighted", "swrr"):
            assert get_rotation_strategy(name).schedule(games) == ["a", "b", "c"]


class TestWeights:
    def test_games_without_an_id_are_skipped(self):
        strategy = get_rotation_strategy("weighted")
        assert strategy.weights([game("a"), {"home_abbr": "X"}]) == {"a": 1}

    @pytest.mark.parametrize("bad", [0, -5])
    def test_non_positive_weights_are_clamped_to_one(self, bad):
        """A zero weight would starve the game out of the rotation entirely and
        collapse total_weight — no caller means that."""
        strategy = get_rotation_strategy("weighted", weight_for=lambda g: bad)
        assert strategy.weights([game("a")]) == {"a": 1}

    @pytest.mark.parametrize("bad", [None, "three", object()])
    def test_unusable_weights_fall_back_to_one(self, bad):
        strategy = get_rotation_strategy("weighted", weight_for=lambda g: bad)
        assert strategy.weights([game("a")]) == {"a": 1}


class TestSimpleRotation:
    def test_one_pass_in_feed_order(self):
        games = [game("a"), game("b"), game("c")]
        assert SimpleRotation().schedule(games) == ["a", "b", "c"]

    def test_weights_are_ignored(self):
        games = [game("a", home="FAV"), game("b")]
        strategy = SimpleRotation(weight_for=boost({"FAV"}, 5))
        assert strategy.schedule(games) == ["a", "b"]

    def test_empty(self):
        assert SimpleRotation().schedule([]) == []
        assert SimpleRotation().next_game([]) is None


class TestWeightedCycleRotation:
    def test_no_boost_is_a_single_pass(self):
        games = [game("a"), game("b"), game("c")]
        strategy = WeightedCycleRotation(weight_for=boost({"NONE"}))
        assert strategy.schedule(games) == ["a", "b", "c"]

    def test_favorite_gets_boost_many_slots(self):
        games = [game("a", home="FAV"), game("b")]
        order = WeightedCycleRotation(weight_for=boost({"FAV"}, 3)).schedule(games)
        assert len(order) == 4
        assert order.count("a") == 3
        assert order.count("b") == 1

    def test_repeats_are_spaced_not_clumped(self):
        """The point of SWRR over naive repetition: 'aaab' is what we must NOT
        produce."""
        games = [game("a", home="FAV"), game("b")]
        order = WeightedCycleRotation(weight_for=boost({"FAV"}, 3)).schedule(games)
        assert order != ["a", "a", "a", "b"]
        assert order[0] == "a", "highest weight is scheduled first"

    def test_is_stateless_across_calls(self):
        games = [game("a", home="FAV"), game("b")]
        strategy = WeightedCycleRotation(weight_for=boost({"FAV"}, 3))
        assert strategy.schedule(games) == strategy.schedule(games)

    def test_next_game_returns_the_first_of_the_cycle(self):
        games = [game("a"), game("b", home="FAV")]
        strategy = WeightedCycleRotation(weight_for=boost({"FAV"}, 4))
        assert strategy.next_game(games)["id"] == "b"

    def test_empty(self):
        assert WeightedCycleRotation().schedule([]) == []


class TestSmoothWeightedRotation:
    def test_no_boost_is_plain_round_robin(self):
        games = [game("a"), game("b"), game("c")]
        strategy = SmoothWeightedRotation()
        assert [strategy.next_game(games)["id"] for _ in range(6)] == [
            "a", "b", "c", "a", "b", "c"]

    def test_favorite_wins_the_share_over_a_long_run(self):
        games = [game("a", home="FAV"), game("b")]
        strategy = SmoothWeightedRotation(weight_for=boost({"FAV"}, 3))
        picks = [strategy.next_game(games)["id"] for _ in range(40)]
        assert picks.count("a") == 30
        assert picks.count("b") == 10

    def test_no_clustering_seam_across_cycle_boundaries(self):
        """The property that motivates keeping this strategy separate from the
        precomputed one: state persists, so there is no restart every N picks
        and therefore no place where repeats bunch up."""
        games = [game("a", home="FAV"), game("b")]
        strategy = SmoothWeightedRotation(weight_for=boost({"FAV"}, 3))
        picks = [strategy.next_game(games)["id"] for _ in range(40)]
        assert "aaaa" not in "".join(picks)

    def test_a_new_favorite_is_queued_first(self):
        """A favorite's game that has just gone live starts at weight 0, gets
        its full weight on the next call, and so wins the first pick after it
        appears — without a special-cased branch."""
        games = [game("a"), game("b")]
        strategy = SmoothWeightedRotation(weight_for=boost({"FAV"}, 5))
        for _ in range(3):
            strategy.next_game(games)
        games.append(game("c", home="FAV"))
        assert strategy.next_game(games)["id"] == "c"

    def test_state_for_games_no_longer_live_is_dropped(self):
        games = [game("a"), game("b")]
        strategy = SmoothWeightedRotation()
        strategy.next_game(games)
        strategy.next_game([game("a")])
        assert set(strategy._current) == {"a"}

    def test_reset_clears_state(self):
        games = [game("a"), game("b")]
        strategy = SmoothWeightedRotation()
        strategy.next_game(games)
        strategy.reset()
        assert strategy._current == {}
        assert strategy.next_game(games)["id"] == "a"

    def test_schedule_previews_without_perturbing_state(self):
        games = [game("a", home="FAV"), game("b")]
        strategy = SmoothWeightedRotation(weight_for=boost({"FAV"}, 3))
        preview = strategy.schedule(games)
        actual = [strategy.next_game(games)["id"] for _ in range(len(preview))]
        assert preview == actual

    def test_empty(self):
        assert SmoothWeightedRotation().next_game([]) is None
        assert SmoothWeightedRotation().schedule([]) == []

    def test_games_without_ids_are_ignored(self):
        assert SmoothWeightedRotation().next_game([{"home_abbr": "X"}]) is None


class TestStrategiesAgreeWithinACycle:
    """The survey's core finding: the 'three dialects' are one algorithm. They
    must produce the same order within a cycle; they differ only at the
    boundary, which is why both shapes survive."""

    @pytest.mark.parametrize("factor", [2, 3, 5])
    def test_first_cycle_matches(self, factor):
        games = [game("a", home="FAV"), game("b"), game("c")]
        weight_for = boost({"FAV"}, factor)
        assert (SmoothWeightedRotation(weight_for=weight_for).schedule(games)
                == WeightedCycleRotation(weight_for=weight_for).schedule(games))


# ---------------------------------------------------------------------------
# Differential: core strategies vs. the plugin implementations they replace
# ---------------------------------------------------------------------------

BOOST = 3


def _is_fav(g):
    return g.get("home_abbr") == "FAV"


def _weight_for(g):
    return BOOST if _is_fav(g) else 1


class _PluginSwrr:
    """afl / nrl / soccer ``_swrr_advance``, transcribed verbatim."""

    favorite_live_boost = BOOST

    def _is_favorite_game(self, g):
        return _is_fav(g)

    def advance(self, games):
        if not games:
            return None
        weights = {}
        for g in games:
            gid = g.get("id")
            if gid is None:
                continue
            weights[gid] = self.favorite_live_boost if self._is_favorite_game(g) else 1
        if not weights:
            return None
        if not hasattr(self, "_swrr_weights"):
            self._swrr_weights = {}
        self._swrr_weights = {
            gid: w for gid, w in self._swrr_weights.items() if gid in weights}
        for gid, w in weights.items():
            self._swrr_weights[gid] = self._swrr_weights.get(gid, 0) + w
        total_weight = sum(weights.values())
        ids_in_order = [g.get("id") for g in games if g.get("id") in weights]
        best_gid = max(ids_in_order, key=lambda gid: self._swrr_weights[gid])
        self._swrr_weights[best_gid] -= total_weight
        return next(g for g in games if g.get("id") == best_gid)


def _plugin_weighted_schedule(games):
    """football / baseball / basketball ``_build_weighted_schedule``, verbatim."""
    if not games:
        return []
    weights = {g["id"]: (BOOST if _is_fav(g) else 1) for g in games}
    total_weight = sum(weights.values())
    if total_weight <= len(games):
        return [g["id"] for g in games]
    current_weight = {gid: 0 for gid in weights}
    schedule = []
    for _ in range(total_weight):
        for gid in weights:
            current_weight[gid] += weights[gid]
        picked = max(current_weight, key=lambda gid: current_weight[gid])
        current_weight[picked] -= total_weight
        schedule.append(picked)
    return schedule


def _plugin_rotation_schedule(games):
    """hockey ``_build_rotation_schedule``, transcribed verbatim."""
    weights = [(g["id"], BOOST if _is_fav(g) else 1) for g in games]
    total_weight = sum(w for _, w in weights)
    if not weights or total_weight <= 0:
        return [g["id"] for g in games]
    current_weights = {gid: 0 for gid, _ in weights}
    schedule = []
    for _ in range(total_weight):
        best_id, best_current = None, None
        for gid, w in weights:
            current_weights[gid] += w
            if best_current is None or current_weights[gid] > best_current:
                best_id, best_current = gid, current_weights[gid]
        current_weights[best_id] -= total_weight
        schedule.append(best_id)
    return schedule


def _cases():
    """Every live-game shape up to 4 games: each either a favorite or not.

    Exhaustive rather than random so the gate is deterministic — a rotation
    regression must fail the same way on every run.
    """
    import itertools
    for size in range(1, 5):
        for flags in itertools.product(("FAV", "OTH"), repeat=size):
            yield [game(f"g{i}", home=abbr) for i, abbr in enumerate(flags)]


class TestMatchesThePluginImplementations:
    """The promotion is only safe if these reproduce the plugin copies exactly.

    B5 deletes the bundled copies on the strength of this: each core strategy is
    checked against the verbatim source it replaces, over every live-game shape
    up to four games.
    """

    @pytest.mark.parametrize("games", list(_cases()))
    def test_swrr_matches_the_incremental_plugin_picker(self, games):
        plugin = _PluginSwrr()
        core = SmoothWeightedRotation(weight_for=_weight_for)
        # 60 picks: long enough to cross many cycle boundaries, where a
        # state-handling divergence would show up.
        assert ([plugin.advance(games)["id"] for _ in range(60)]
                == [core.next_game(games)["id"] for _ in range(60)])

    @pytest.mark.parametrize("games", list(_cases()))
    def test_weighted_matches_the_football_lineage(self, games):
        assert (_plugin_weighted_schedule(games)
                == WeightedCycleRotation(weight_for=_weight_for).schedule(games))

    @pytest.mark.parametrize("games", list(_cases()))
    def test_weighted_matches_hockeys_loop_shape(self, games):
        assert (_plugin_rotation_schedule(games)
                == WeightedCycleRotation(weight_for=_weight_for).schedule(games))


# ---------------------------------------------------------------------------
# Celebrations
# ---------------------------------------------------------------------------

class _FakeLive:
    """Stand-in for SportsLive: just the surface the mixin touches."""

    def __init__(self, mode_config=None, favorite_teams=None):
        self.mode_config = mode_config or {}
        self.favorite_teams = favorite_teams or []
        self.logger = MagicMock()
        self.display_manager = MagicMock()
        self.is_enabled = True
        self.current_game = None
        self.last_game_switch = 0
        self.display_calls = []

    def _favorite_key(self, game, side):
        return game.get(f"{side}_abbr")

    def display(self, force_clear=False):
        self.display_calls.append(force_clear)
        return True


class _Celebrating(CelebrationMixin, _FakeLive):
    pass


class _Coalescing(CelebrationMixin, _FakeLive):
    COALESCE_SCORING_SEQUENCE = True

    def score_phrase(self, points, team_abbr):
        return "TOUCHDOWN!" if points >= 6 else f"{team_abbr} FIELD GOAL!"


class _ById(CelebrationMixin, _FakeLive):
    """The nrl shape: ambiguous abbreviations, so favorites match on team id."""

    def _favorite_key(self, game, side):
        return game.get(f"{side}_id")


@pytest.fixture
def celebrating():
    def _build(cls=_Celebrating, mode_config=None, favorites=None):
        return cls(mode_config=mode_config, favorite_teams=favorites)
    return _build


class TestOptOutIsStructural:
    """The property the mixin design exists to buy: a class that does not opt in
    has none of this code — not a disabled flag, not an unused attribute."""

    def test_a_non_celebrating_class_has_no_celebration_surface(self):
        plain = _FakeLive()
        for attribute in ("active_celebration", "_score_baselines",
                          "celebration_enabled", "celebration_duration",
                          "_check_for_score", "_check_for_win",
                          "has_active_celebration", "_draw_celebration_layout"):
            assert not hasattr(plain, attribute), (
                f"{attribute} leaked onto a class that never opted in")

    def test_the_mixin_is_absent_from_a_non_celebrating_mro(self):
        assert CelebrationMixin not in _FakeLive.__mro__
        assert CelebrationMixin in _Celebrating.__mro__

    def test_mixin_does_not_require_the_base_to_know_about_it(self):
        """SportsLive must carry no celebration hooks — that would be the
        god-class shape the mixin replaces."""
        from src.base_classes.sports import SportsLive
        source = __import__("inspect").getsource(SportsLive)
        assert "celebration" not in source.lower()


class TestCelebrationConfig:
    def test_defaults(self, celebrating):
        manager = celebrating()
        assert manager.celebration_enabled is True
        assert manager.celebration_duration == 8
        assert manager.celebrate_opponent_scores is False
        assert manager.active_celebration is None

    def test_reads_the_goal_spelling_of_the_opponent_key(self, celebrating):
        """The soccer lineage's published schema says `celebrate_opponent_goals`;
        adopting the mixin must not silently reset users' setting."""
        manager = celebrating(mode_config={"celebrate_opponent_goals": True})
        assert manager.celebrate_opponent_scores is True

    def test_reads_the_score_spelling_of_the_opponent_key(self, celebrating):
        manager = celebrating(mode_config={"celebrate_opponent_scores": True})
        assert manager.celebrate_opponent_scores is True

    def test_score_spelling_wins_when_both_are_present(self, celebrating):
        manager = celebrating(mode_config={"celebrate_opponent_scores": False,
                                           "celebrate_opponent_goals": True})
        assert manager.celebrate_opponent_scores is False


class TestScoreDetection:
    def test_first_sighting_never_celebrates(self, celebrating):
        """A game already in progress at boot must not false-fire."""
        manager = celebrating()
        manager._check_for_score(game("g1", home_score=3, away_score=1))
        assert manager.active_celebration is None
        assert manager._score_baselines["g1"] == {"away": 1, "home": 3}

    def test_increment_arms_a_celebration(self, celebrating):
        manager = celebrating()
        manager._check_for_score(game("g1", home_score=0, away_score=0))
        manager._check_for_score(game("g1", home_score=1, away_score=0))
        assert manager.active_celebration["kind"] == "score"
        assert manager.active_celebration["scored_side"] == "home"

    def test_no_change_does_not_fire(self, celebrating):
        manager = celebrating()
        manager._check_for_score(game("g1", home_score=2))
        manager._check_for_score(game("g1", home_score=2))
        assert manager.active_celebration is None

    def test_decrement_rebases_silently(self, celebrating):
        """A disallowed goal / correction must not celebrate, and must not leave
        a stale baseline that fires on the way back up."""
        manager = celebrating()
        manager._check_for_score(game("g1", home_score=2))
        manager._check_for_score(game("g1", home_score=1))
        assert manager.active_celebration is None
        assert manager._score_baselines["g1"]["home"] == 1

    def test_disabled_never_fires(self, celebrating):
        manager = celebrating(mode_config={"celebration_enabled": False})
        manager._check_for_score(game("g1", home_score=0))
        manager._check_for_score(game("g1", home_score=1))
        assert manager.active_celebration is None

    def test_game_without_an_id_is_ignored(self, celebrating):
        manager = celebrating()
        manager._check_for_score({"home_score": 1, "away_score": 0})
        assert manager.active_celebration is None

    @pytest.mark.parametrize("score", [None, "", "not-a-number-at-all"])
    def test_unusable_scores_are_ignored(self, celebrating, score):
        manager = celebrating()
        manager._check_for_score(game("g1", home_score=score))
        assert manager._score_baselines == {}

    @pytest.mark.parametrize("raw,expected", [
        ("7", 7), (7, 7), (7.0, 7), ("  7 ", 7), ("7 (SO)", 7),
        ({"value": 7}, 7), ({"displayValue": "7"}, 7),
    ])
    def test_score_coercion(self, raw, expected):
        assert CelebrationMixin._score_to_int(raw) == expected

    def test_away_side_is_detected(self, celebrating):
        manager = celebrating()
        manager._check_for_score(game("g1", away_score=0))
        manager._check_for_score(game("g1", away_score=1))
        assert manager.active_celebration["scored_side"] == "away"


class TestWhoGetsCelebrated:
    def test_no_favorites_celebrates_everyone(self, celebrating):
        """The user opted to show this game at all, so any score in it counts."""
        manager = celebrating(favorites=[])
        manager._check_for_score(game("g1", home="XXX", home_score=0))
        manager._check_for_score(game("g1", home="XXX", home_score=1))
        assert manager.active_celebration is not None

    def test_favorite_scores(self, celebrating):
        manager = celebrating(favorites=["FAV"])
        manager._check_for_score(game("g1", home="FAV", home_score=0))
        manager._check_for_score(game("g1", home="FAV", home_score=1))
        assert manager.active_celebration is not None

    def test_opponent_suppressed_by_default(self, celebrating):
        manager = celebrating(favorites=["FAV"])
        manager._check_for_score(game("g1", home="OPP", away="FAV", home_score=0))
        manager._check_for_score(game("g1", home="OPP", away="FAV", home_score=1))
        assert manager.active_celebration is None

    def test_opponent_celebrated_when_opted_in(self, celebrating):
        manager = celebrating(mode_config={"celebrate_opponent_scores": True},
                              favorites=["FAV"])
        manager._check_for_score(game("g1", home="OPP", away="FAV", home_score=0))
        manager._check_for_score(game("g1", home="OPP", away="FAV", home_score=1))
        assert manager.active_celebration is not None

    def test_matching_goes_through_the_favorite_key_seam(self, celebrating):
        """nrl matches on team id because its abbreviations are ambiguous
        ('NEW' is both Newcastle and New Zealand). Core must not care why."""
        manager = celebrating(_ById, favorites=["g1-h"])
        manager._check_for_score(game("g1", home="NEW", home_score=0))
        manager._check_for_score(game("g1", home="NEW", home_score=1))
        assert manager.active_celebration is not None

    def test_favorite_key_seam_also_excludes(self, celebrating):
        manager = celebrating(_ById, favorites=["someone-else"])
        manager._check_for_score(game("g1", home="NEW", home_score=0))
        manager._check_for_score(game("g1", home="NEW", home_score=1))
        assert manager.active_celebration is None


class TestPhrasing:
    def test_default_phrase_is_sport_neutral(self, celebrating):
        manager = celebrating()
        manager._check_for_score(game("g1", home="HOM", home_score=0))
        manager._check_for_score(game("g1", home="HOM", home_score=1))
        assert manager.active_celebration["phrase"] == "HOM SCORES!"

    def test_score_phrase_hook_sees_the_points_delta(self, celebrating):
        manager = celebrating(_Coalescing)
        manager._check_for_score(game("g1", home_score=0))
        manager._check_for_score(game("g1", home_score=6))
        assert manager.active_celebration["phrase"] == "TOUCHDOWN!"

    def test_score_phrase_hook_distinguishes_smaller_plays(self, celebrating):
        manager = celebrating(_Coalescing)
        manager._check_for_score(game("g1", home_score=0))
        manager._check_for_score(game("g1", home_score=3))
        assert manager.active_celebration["phrase"] == "HOM FIELD GOAL!"

    def test_win_phrase(self, celebrating):
        manager = celebrating(favorites=["HOM"])
        manager._check_for_score(game("g1", home_score=1))
        manager._check_for_win(game("g1", home_score=2, away_score=1))
        assert manager.active_celebration["phrase"] == "HOM WINS!"


class TestCoalescing:
    def test_off_by_default_two_goals_are_two_celebrations(self, celebrating):
        """Soccer/afl/nrl: consecutive increments are distinct events, so
        suppressing the second would swallow a real goal."""
        manager = celebrating()
        manager._check_for_score(game("g1", home_score=0))
        manager._check_for_score(game("g1", home_score=1))
        first = manager.active_celebration["started_at"]
        manager._check_for_score(game("g1", home_score=2))
        assert manager.active_celebration["started_at"] != first
        assert manager.active_celebration["home_score"] == 2

    def test_on_suppresses_the_extra_point_follow_up(self, celebrating):
        """Football: a touchdown lands as +6, then +1 seconds later. One
        takeover per scoring sequence."""
        manager = celebrating(_Coalescing)
        manager._check_for_score(game("g1", home_score=0))
        manager._check_for_score(game("g1", home_score=6))
        armed = manager.active_celebration
        manager._check_for_score(game("g1", home_score=7))
        assert manager.active_celebration is armed

    def test_suppression_still_advances_the_baseline(self, celebrating):
        """Nothing may re-fire once the window closes."""
        manager = celebrating(_Coalescing)
        manager._check_for_score(game("g1", home_score=0))
        manager._check_for_score(game("g1", home_score=6))
        manager._check_for_score(game("g1", home_score=7))
        assert manager._score_baselines["g1"]["home"] == 7


class TestWinDetection:
    def test_win_requires_a_baseline(self, celebrating):
        """A game seen for the first time already-final (board started after
        full time) must not fire."""
        manager = celebrating(favorites=["HOM"])
        manager._check_for_win(game("g1", home_score=3, away_score=1))
        assert manager.active_celebration is None

    def test_win_fires_once_only(self, celebrating):
        manager = celebrating(favorites=["HOM"])
        manager._check_for_score(game("g1", home_score=1))
        manager._check_for_win(game("g1", home_score=3, away_score=1))
        manager.active_celebration = None
        manager._check_for_win(game("g1", home_score=3, away_score=1))
        assert manager.active_celebration is None

    def test_draw_does_not_celebrate(self, celebrating):
        manager = celebrating(favorites=["HOM"])
        manager._check_for_score(game("g1", home_score=1))
        manager._check_for_win(game("g1", home_score=2, away_score=2))
        assert manager.active_celebration is None

    def test_win_is_gated_strictly_on_favorites(self, celebrating):
        """Unlike scores, a win with no favorites configured does NOT celebrate:
        every game ends, so the fallback would be constant noise."""
        manager = celebrating(favorites=[])
        manager._check_for_score(game("g1", home_score=1))
        manager._check_for_win(game("g1", home_score=3, away_score=1))
        assert manager.active_celebration is None

    def test_losing_favorite_does_not_celebrate(self, celebrating):
        manager = celebrating(favorites=["HOM"])
        manager._check_for_score(game("g1", home_score=1))
        manager._check_for_win(game("g1", home_score=1, away_score=4))
        assert manager.active_celebration is None

    def test_away_favorite_wins(self, celebrating):
        manager = celebrating(favorites=["AWY"])
        manager._check_for_score(game("g1", away_score=1))
        manager._check_for_win(game("g1", home_score=1, away_score=4))
        assert manager.active_celebration["scored_side"] == "away"


class TestCelebrationSnapshot:
    def test_the_game_is_snapshotted_not_referenced(self, celebrating):
        """A win must survive the game leaving live_games."""
        manager = celebrating()
        live = game("g1", home_score=0)
        manager._check_for_score(live)
        live = game("g1", home_score=1)
        manager._check_for_score(live)
        live["home_abbr"] = "MUTATED"
        assert manager.active_celebration["game"]["home_abbr"] == "HOM"

    def test_focus_is_pinned_to_the_involved_game(self, celebrating):
        manager = celebrating()
        manager._check_for_score(game("g1", home_score=0))
        manager._check_for_score(game("g1", home_score=1))
        assert manager.current_game["id"] == "g1"


class TestDisplayTakeover:
    def test_no_celebration_defers_to_the_scorebug(self, celebrating):
        manager = celebrating()
        assert manager.display(force_clear=True) is True
        assert manager.display_calls == [True]

    def test_active_celebration_takes_over(self, celebrating):
        manager = celebrating()
        manager._check_for_score(game("g1", home_score=0))
        manager._check_for_score(game("g1", home_score=1))
        manager._draw_celebration_layout = MagicMock()
        assert manager.display() is True
        assert manager.display_calls == [], "the scorebug must not also render"
        manager._draw_celebration_layout.assert_called_once()

    def test_expired_celebration_clears_and_defers(self, celebrating):
        manager = celebrating(mode_config={"celebration_duration": 0})
        manager._check_for_score(game("g1", home_score=0))
        manager._check_for_score(game("g1", home_score=1))
        assert manager.display() is True
        assert manager.active_celebration is None
        assert manager.display_calls == [False]

    def test_expiry_resets_the_dwell_clock(self, celebrating):
        """So the scorebug resumes on the scoring game for a full duration
        before rotation can move on."""
        manager = celebrating(mode_config={"celebration_duration": 0})
        manager._check_for_score(game("g1", home_score=0))
        manager._check_for_score(game("g1", home_score=1))
        manager.display()
        assert manager.last_game_switch > 0

    def test_a_render_failure_falls_through_to_the_scorebug(self, celebrating):
        """A broken celebration must never blank the display."""
        manager = celebrating()
        manager._check_for_score(game("g1", home_score=0))
        manager._check_for_score(game("g1", home_score=1))
        manager._draw_celebration_layout = MagicMock(side_effect=RuntimeError("boom"))
        assert manager.display() is True
        assert manager.display_calls == [False]

    def test_disabled_manager_renders_nothing(self, celebrating):
        manager = celebrating()
        manager.is_enabled = False
        assert manager.display() is False


class TestFitFont:
    def test_returns_the_first_font_that_fits(self, celebrating):
        manager = celebrating()
        draw = MagicMock()
        draw.textlength.side_effect = [100, 20]
        big, small = MagicMock(), MagicMock()
        assert manager._fit_font(draw, "GOAL", 50, [big, small]) is small

    def test_falls_back_to_the_smallest(self, celebrating):
        manager = celebrating()
        draw = MagicMock()
        draw.textlength.return_value = 999
        big, small = MagicMock(), MagicMock()
        assert manager._fit_font(draw, "GOAL", 50, [big, small]) is small


class TestCapabilityExports:
    @pytest.mark.parametrize("name", [
        "CelebrationMixin", "RotationStrategy", "SimpleRotation",
        "SmoothWeightedRotation", "WeightedCycleRotation",
        "get_rotation_strategy", "register_rotation_strategy",
    ])
    def test_public_name_is_importable(self, name):
        """Plugins import these behind a guarded fallback; the names are the
        contract."""
        import src.base_classes.sports.capabilities as capabilities
        assert hasattr(capabilities, name)

    def test_rotation_strategy_base_requires_a_schedule(self):
        with pytest.raises(NotImplementedError):
            RotationStrategy().schedule([game("a")])
