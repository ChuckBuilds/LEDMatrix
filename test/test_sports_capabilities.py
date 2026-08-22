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
import time
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

    @pytest.mark.parametrize("bad", [
        SimpleRotation(),          # an instance, not the class
        str,                       # unrelated class
        lambda **kw: None,         # a factory function
    ])
    def test_a_non_strategy_factory_is_rejected(self, bad):
        """Fail at registration, not several frames later inside schedule(),
        where the cause is no longer on the stack."""
        with pytest.raises(TypeError):
            register_rotation_strategy("bad-factory", bad)

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

    def test_huge_weights_are_clamped(self):
        """A cycle is sum(weights) long and each step scans every game, so an
        unbounded weight from a misread config spins the display thread."""
        strategy = get_rotation_strategy("weighted", weight_for=lambda g: 10_000)
        assert strategy.weights([game("a")]) == {"a": RotationStrategy.MAX_WEIGHT}

    def test_a_clamped_cycle_stays_bounded(self):
        strategy = get_rotation_strategy("weighted", weight_for=lambda g: 10_000)
        order = strategy.schedule([game("a"), game("b")])
        assert len(order) == 2 * RotationStrategy.MAX_WEIGHT


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

    def test_preview_uses_the_subclass_ordering(self):
        """schedule() promises the order repeated next_game calls produce. Built
        from the base class, a subclass that overrides next_game gets a preview
        of the wrong algorithm."""
        class Reversed(SmoothWeightedRotation):
            def next_game(self, games):
                return super().next_game(list(reversed(games)))

        games = [game("a"), game("b"), game("c")]
        strategy = Reversed()
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

    @pytest.mark.parametrize("bad", ["eight", None, {}, []])
    def test_unusable_duration_falls_back(self, celebrating, bad):
        """The duration is compared numerically on the display path, outside
        any try block — a string from a hand-edited config would propagate a
        TypeError straight out of display()."""
        manager = celebrating(mode_config={"celebration_duration": bad})
        assert manager.celebration_duration == 8.0

    @pytest.mark.parametrize("bad", [0, -5])
    def test_non_positive_duration_is_floored(self, celebrating, bad):
        """Zero or negative would arm a celebration that can never render."""
        manager = celebrating(mode_config={"celebration_duration": bad})
        assert manager.celebration_duration == 1.0

    def test_numeric_string_duration_is_accepted(self, celebrating):
        assert celebrating(
            mode_config={"celebration_duration": "12"}).celebration_duration == 12.0


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
        # A short-but-valid duration: celebration_duration is clamped to a 1.0s
        # floor, so a config of 0 does NOT expire on the next frame. Backdate
        # started_at past the window to exercise the real expiry branch —
        # otherwise the celebration is still active and this only passes
        # because _draw_celebration_layout happens to raise in the harness
        # (that path is covered by test_a_render_failure_falls_through).
        manager = celebrating(mode_config={"celebration_duration": 1})
        manager._check_for_score(game("g1", home_score=0))
        manager._check_for_score(game("g1", home_score=1))
        manager.active_celebration["started_at"] = time.time() - 2  # past the 1s window
        # Mock the layout so a render can't raise: otherwise the exception
        # branch clears the celebration too, and this test would pass whether
        # or not expiry actually fired. An expired celebration must NOT render.
        manager._draw_celebration_layout = MagicMock()
        assert manager.display() is True
        manager._draw_celebration_layout.assert_not_called()
        assert manager.active_celebration is None
        assert manager.display_calls == [False]

    def test_expiry_resets_the_dwell_clock(self, celebrating):
        """So the scorebug resumes on the scoring game for a full duration
        before rotation can move on."""
        manager = celebrating(mode_config={"celebration_duration": 1})
        manager._check_for_score(game("g1", home_score=0))
        manager._check_for_score(game("g1", home_score=1))
        manager.active_celebration["started_at"] = time.time() - 2  # past the 1s window
        manager._draw_celebration_layout = MagicMock()  # expiry, not a render failure
        manager.display()
        manager._draw_celebration_layout.assert_not_called()
        assert manager.last_game_switch > 0

    def test_a_render_failure_falls_through_to_the_scorebug(self, celebrating):
        """A broken celebration must never blank the display."""
        manager = celebrating()
        manager._check_for_score(game("g1", home_score=0))
        manager._check_for_score(game("g1", home_score=1))
        manager._draw_celebration_layout = MagicMock(side_effect=RuntimeError("boom"))
        assert manager.display() is True
        assert manager.display_calls == [False]

    def test_a_render_failure_disarms_rather_than_retrying(self, celebrating):
        """Left armed, the same render fails on every frame for the rest of the
        window — a traceback per frame, and no scorebug."""
        manager = celebrating()
        manager._check_for_score(game("g1", home_score=0))
        manager._check_for_score(game("g1", home_score=1))
        manager._draw_celebration_layout = MagicMock(side_effect=RuntimeError("boom"))
        manager.display()
        assert manager.active_celebration is None
        manager.display()
        assert manager._draw_celebration_layout.call_count == 1


class TestBaselinePruning:
    """`_score_baselines` gains an entry per game and only _check_for_win ever
    removed one, so a board running all season grows the dict without bound."""

    def test_prunes_games_no_longer_live(self, celebrating):
        manager = celebrating()
        for gid in ("g1", "g2", "g3"):
            manager._check_for_score(game(gid, home_score=1))
        manager.prune_score_baselines([game("g2")])
        assert set(manager._score_baselines) == {"g2"}

    def test_keeps_every_still_live_game(self, celebrating):
        manager = celebrating()
        for gid in ("g1", "g2"):
            manager._check_for_score(game(gid, home_score=1))
        manager.prune_score_baselines([game("g1"), game("g2")])
        assert set(manager._score_baselines) == {"g1", "g2"}

    def test_empty_live_set_clears_everything(self, celebrating):
        manager = celebrating()
        manager._check_for_score(game("g1", home_score=1))
        manager.prune_score_baselines([])
        assert manager._score_baselines == {}

    def test_pruning_does_not_disturb_a_surviving_baseline(self, celebrating):
        manager = celebrating()
        manager._check_for_score(game("g1", home_score=2))
        manager.prune_score_baselines([game("g1")])
        manager._check_for_score(game("g1", home_score=3))
        assert manager.active_celebration is not None, (
            "pruning must not drop a live game's baseline and re-trigger the "
            "first-sighting suppression")

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


# ---------------------------------------------------------------------------
# Celebrations: rendering + previously untested edges
# ---------------------------------------------------------------------------

from PIL import Image, ImageDraw, ImageFont  # noqa: E402


class _RenderableLive(_FakeLive):
    """A _FakeLive that can actually execute _draw_celebration_layout:
    real fonts, a display manager holding a real PIL image, and the two
    SportsCore drawing seams the mixin calls."""

    def __init__(self, mode_config=None, favorite_teams=None,
                 width=128, height=32, with_matrix=True):
        super().__init__(mode_config=mode_config, favorite_teams=favorite_teams)
        font = ImageFont.load_default()
        self.fonts = {"time": font, "status": font, "score": font}
        self.display_width = width
        self.display_height = height
        dm = MagicMock()
        if with_matrix:
            dm.matrix.width = width
            dm.matrix.height = height
        else:
            dm.matrix = None
        dm.image = Image.new("RGB", (width, height))
        self.display_manager = dm
        self.logo_calls = []

    def _load_and_resize_logo(self, team_id, abbr, path, url):
        self.logo_calls.append(abbr)
        logo = Image.new("RGBA", (10, 10), (0, 200, 0, 255))
        return logo

    def _draw_text_with_outline(self, draw, text, position, font, fill=(255, 255, 255)):
        draw.text(position, str(text), font=font, fill=fill)


class _RenderableCelebrating(CelebrationMixin, _RenderableLive):
    pass


def _armed(manager, *, kind="score", side="home", started_ago=0.0):
    manager._start_celebration(
        game("g1", home_score=7, away_score=3), kind,
        scored_side=side, team_abbr="HOM", away_score=3, home_score=7,
        points=7,
    )
    manager.active_celebration["started_at"] = time.time() - started_ago
    return manager.active_celebration


class TestDrawCelebrationLayout:
    """The takeover render path, executed for real (previously always
    mocked out)."""

    def test_renders_and_hands_frame_to_display_manager(self):
        manager = _RenderableCelebrating()
        celebration = _armed(manager)
        manager._draw_celebration_layout(celebration)
        # The final frame was assigned and pushed.
        assert isinstance(manager.display_manager.image, Image.Image)
        assert manager.display_manager.image.mode == "RGB"
        assert manager.display_manager.image.size == (128, 32)
        manager.display_manager.update_display.assert_called_once()
        assert manager.display_manager.image.convert("L").getbbox() is not None

    def test_force_clear_clears_display_first(self):
        manager = _RenderableCelebrating()
        celebration = _armed(manager)
        manager._draw_celebration_layout(celebration, force_clear=True)
        manager.display_manager.clear.assert_called_once()

    def test_flash_background_within_first_window(self):
        # elapsed < 1.2 with int(elapsed/0.2) even -> flash color backdrop.
        manager = _RenderableCelebrating()
        celebration = _armed(manager, started_ago=0.05)
        manager._draw_celebration_layout(celebration)
        flash = manager.display_manager.image
        # After the flash window: plain black backdrop.
        celebration["started_at"] = time.time() - 5
        manager._draw_celebration_layout(celebration)
        steady = manager.display_manager.image
        # Corner pixels (away from logos/text) show the two backgrounds.
        assert flash.getpixel((64, 30)) != steady.getpixel((64, 30)) or \
            flash.getpixel((3, 0)) != steady.getpixel((3, 0))

    def test_matrix_dims_fallback_to_display_attrs(self):
        manager = _RenderableCelebrating(width=96, height=48, with_matrix=False)
        celebration = _armed(manager)
        manager._draw_celebration_layout(celebration)
        assert manager.display_manager.image.size == (96, 48)

    def test_highlight_color_alternates_with_elapsed(self):
        manager = _RenderableCelebrating()
        celebration = _armed(manager)
        # int(elapsed*4) % 2 == 0 -> yellow; == 1 -> orange. Force each phase
        # and diff the frames.
        celebration["started_at"] = time.time() - 2.0   # 8 -> even
        manager._draw_celebration_layout(celebration)
        even = manager.display_manager.image.tobytes()
        celebration["started_at"] = time.time() - 2.25  # 9 -> odd
        manager._draw_celebration_layout(celebration)
        odd = manager.display_manager.image.tobytes()
        assert even != odd

    def test_logo_failure_still_renders_text(self):
        manager = _RenderableCelebrating()

        def boom(*a, **k):
            raise RuntimeError("disk gone")

        manager._load_and_resize_logo = boom
        celebration = _armed(manager, started_ago=5)  # steady background
        manager._draw_celebration_layout(celebration)  # must not raise
        assert manager.display_manager.image.convert("L").getbbox() is not None
        manager.display_manager.update_display.assert_called_once()


class TestCelebrationEdges:
    def test_should_celebrate_for_three_way_branch(self, celebrating):
        g = game("g1", home="FAV", away="OPP")
        favored = celebrating(favorites=["FAV"])
        assert favored._should_celebrate_for(g, "home") is True   # favorite
        assert favored._should_celebrate_for(g, "away") is False  # opponent
        favored.celebrate_opponent_scores = True
        assert favored._should_celebrate_for(g, "away") is True   # opted in
        unconfigured = celebrating(favorites=[])
        assert unconfigured._should_celebrate_for(g, "away") is True  # no favs

    def test_active_celebration_boundary_is_strict(self, celebrating):
        manager = celebrating(mode_config={"celebration_duration": 3})
        manager.active_celebration = {"started_at": time.time() - 3.0}
        # elapsed == duration -> strictly-less-than comparison says done.
        assert manager.has_active_celebration() is False
        manager.active_celebration = None
        assert manager.has_active_celebration() is False

    @pytest.mark.parametrize("value,expected", [
        ({"value": None}, None),      # int(float(None)) TypeError -> caught
        ({"value": "abc"}, None),
        ({"other": 1}, 0),            # neither key -> default 0
        ([3], None),                  # list -> TypeError -> caught
        ("-4", None),                 # regex fallback finds digits -> 4? No:
    ])
    def test_score_to_int_edges(self, value, expected):
        result = CelebrationMixin._score_to_int(value)
        if value == "-4":
            # int(float("-4")) parses directly: -4.
            assert result == -4
        else:
            assert result == expected

    def test_both_teams_scoring_prefers_away(self, celebrating):
        manager = celebrating(favorites=[])
        manager._check_for_score(game("g1", home_score=0, away_score=0))
        manager._check_for_score(game("g1", home_score=7, away_score=3))
        assert manager.active_celebration["scored_side"] == "away"

    def test_away_not_celebratable_falls_through_to_home(self, celebrating):
        manager = celebrating(favorites=["HOM"])  # away is the opponent
        manager._check_for_score(game("g1", home_score=0, away_score=0))
        manager._check_for_score(game("g1", home_score=7, away_score=3))
        assert manager.active_celebration["scored_side"] == "home"

    def test_coalesce_expired_celebration_fires_fresh(self, celebrating):
        manager = celebrating(cls=_Coalescing,
                              mode_config={"celebration_duration": 1})
        manager._check_for_score(game("g1"))
        manager._check_for_score(game("g1", home_score=6))
        first = manager.active_celebration
        assert first is not None
        first["started_at"] = time.time() - 2  # expired
        manager._check_for_score(game("g1", home_score=7))
        # A new celebration replaced the expired one (coalescing only
        # suppresses while one is actively on screen).
        assert manager.active_celebration is not first
        assert manager.active_celebration["home_score"] == 7

    def test_disabled_win_check_preserves_baseline(self, celebrating):
        manager = celebrating(favorites=["HOM"])
        manager._check_for_score(game("g1"))
        assert "g1" in manager._score_baselines
        manager.celebration_enabled = False
        manager._check_for_win(game("g1", home_score=7))
        # Early return BEFORE consuming the baseline: re-enabling later can
        # still fire for this game.
        assert "g1" in manager._score_baselines

    def test_prune_drops_baselines_for_idless_live_games(self, celebrating):
        manager = celebrating()
        manager._score_baselines = {"g1": {"away": 0, "home": 0}}
        manager.prune_score_baselines([{"no_id_here": True}])
        # live ids collapse to {None}; g1 is not live -> dropped.
        assert manager._score_baselines == {}
