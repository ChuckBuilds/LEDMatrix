"""Live-rotation strategies — which live game to show next.

The nine plugin copies grew three spellings of this, and the survey behind
``docs/SPORTS_UNIFICATION.md`` found they are all the *same* Smooth Weighted
Round-Robin algorithm in two shapes:

* an **incremental picker** that holds weight state across calls and answers
  "what next?" one game at a time (afl / nrl / soccer's ``_swrr_advance``), and
* a **precomputed cycle** that returns a full list of game ids up front
  (football / baseball / basketball's ``_build_weighted_schedule`` and hockey's
  ``_build_rotation_schedule``, which differ only in loop shape).

They agree *within* a cycle — SWRR is deterministic — and differ only at cycle
boundaries, where the incremental form has no seam and the precomputed form
restarts. That is a real behavioral difference, so core ships both rather than
declaring a winner, and a plugin picks one by name:

    self.rotation = get_rotation_strategy("swrr", weight_for=self._live_weight)

Core never learns which sport is asking. A plugin with a genuinely novel
ordering registers its own strategy instead of core growing a branch::

    register_rotation_strategy("my-order", MyRotation)
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional, Type


def _game_id(game: Dict) -> Optional[str]:
    """The rotation key for a game, or None if it has no usable id."""
    return game.get("id")


class RotationStrategy:
    """Base class for live-rotation ordering.

    Subclasses implement :meth:`schedule`; :meth:`next_game` has a working
    default derived from it. Strategies whose natural shape is incremental
    override :meth:`next_game` instead and derive :meth:`schedule`.

    :param weight_for: callable mapping a game dict to a positive integer
        weight — how many turns it gets per turn of a weight-1 game. Supplied by
        the host so the *favorites* policy stays with the plugin and this module
        stays free of any notion of what a favorite is. Defaults to equal
        weights, which makes every strategy a plain round robin.
    """

    #: Name this strategy is registered under. Set by :func:`register_rotation_strategy`.
    name: str = ""

    #: Ceiling on a per-game weight. A cycle is ``sum(weights)`` long and each
    #: step scans every game, so an unbounded weight — a misread config field,
    #: say — would spin the display thread for an unbounded time. On a Pi that
    #: stalls rendering outright, so the bound is clamped like the floor is.
    MAX_WEIGHT = 16

    def __init__(self, weight_for: Optional[Callable[[Dict], int]] = None):
        self._weight_for = weight_for or (lambda game: 1)

    def weights(self, games: List[Dict]) -> Dict[str, int]:
        """``{game_id: weight}`` for games that have an id, in ``games`` order.

        A weight below 1 is clamped up: a zero or negative weight would starve
        a game out of the rotation entirely, which no caller means to express
        and which would make ``total_weight`` collapse. It is clamped down at
        :attr:`MAX_WEIGHT` for the reason documented there.
        """
        weights: Dict[str, int] = {}
        for game in games:
            gid = _game_id(game)
            if gid is None:
                continue
            try:
                weight = int(self._weight_for(game))
            except (TypeError, ValueError):
                weight = 1
            weights[gid] = min(self.MAX_WEIGHT, max(1, weight))
        return weights

    def schedule(self, games: List[Dict]) -> List[str]:
        """Game ids in display order for one cycle. Ids may repeat."""
        raise NotImplementedError

    def next_game(self, games: List[Dict]) -> Optional[Dict]:
        """The next game to display, or None when there is nothing to show."""
        order = self.schedule(games)
        if not order:
            return None
        by_id = {gid: g for g in games if (gid := _game_id(g)) is not None}
        return by_id.get(order[0])

    def reset(self) -> None:
        """Drop any accumulated state. Stateless strategies need do nothing."""


class SimpleRotation(RotationStrategy):
    """Plain round robin: every live game once per cycle, weights ignored.

    The fallback for a plugin that wants strictly even rotation regardless of
    favorites.
    """

    def schedule(self, games: List[Dict]) -> List[str]:
        return [gid for g in games if (gid := _game_id(g)) is not None]


class WeightedCycleRotation(RotationStrategy):
    """Precomputed SWRR cycle — the football / baseball / basketball / hockey shape.

    Returns a full cycle of ``sum(weights)`` ids with repeats spaced evenly
    rather than clumped, highest weight scheduled first. When no game carries a
    boost the cycle degenerates to a single pass in ``games`` order, which is
    exactly the plain round robin it replaced.
    """

    def schedule(self, games: List[Dict]) -> List[str]:
        weights = self.weights(games)
        if not weights:
            return []
        total_weight = sum(weights.values())
        if total_weight <= len(weights):
            # No boost in effect — plain order, one pass. (Also the guard that
            # keeps the loop below from being O(total_weight) for nothing.)
            return list(weights)

        current = {gid: 0 for gid in weights}
        order: List[str] = []
        for _ in range(total_weight):
            for gid, weight in weights.items():
                current[gid] += weight
            picked = max(current, key=lambda gid: current[gid])
            current[picked] -= total_weight
            order.append(picked)
        return order


class SmoothWeightedRotation(RotationStrategy):
    """Incremental SWRR — the afl / nrl / soccer shape.

    Weight state persists across calls, so there is no fixed-length cycle and
    therefore no clustering seam at a cycle boundary. A game seen for the first
    time starts at weight 0 and receives its full weight on the next call, so a
    favorite's game that has just gone live naturally wins the first pick after
    it appears — "queued first on refresh" without a special-cased branch.

    State for games no longer live is dropped on each call, so a long-running
    board does not accumulate entries for finished games.
    """

    def __init__(self, weight_for: Optional[Callable[[Dict], int]] = None):
        super().__init__(weight_for)
        self._current: Dict[str, int] = {}

    def reset(self) -> None:
        self._current = {}

    def next_game(self, games: List[Dict]) -> Optional[Dict]:
        if not games:
            return None
        weights = self.weights(games)
        if not weights:
            return None

        # Keep state only for games still live.
        self._current = {
            gid: value for gid, value in self._current.items() if gid in weights
        }
        for gid, weight in weights.items():
            self._current[gid] = self._current.get(gid, 0) + weight

        total_weight = sum(weights.values())
        # Iterate in `games` order so ties break toward the feed's ordering,
        # which is what the plugin copies did and what makes the no-boost case
        # identical to a plain round robin.
        ids_in_order = [gid for g in games if (gid := _game_id(g)) in weights]
        best = max(ids_in_order, key=lambda gid: self._current[gid])
        self._current[best] -= total_weight
        return next(g for g in games if _game_id(g) == best)

    def schedule(self, games: List[Dict]) -> List[str]:
        """One cycle's worth of picks, without disturbing live state.

        Derived by running the picker forward on a copy, so the returned order
        is exactly what repeated :meth:`next_game` calls would produce from the
        current state — callers can use it to preview or log the rotation
        without perturbing it.
        """
        weights = self.weights(games)
        if not weights:
            return []
        # type(self), not this class: a subclass that overrides next_game must
        # be previewed through its own ordering, or the returned order is not
        # the one repeated next_game calls would produce — which is exactly
        # what this method promises.
        preview = type(self)(self._weight_for)
        preview._current = dict(self._current)
        order: List[str] = []
        for _ in range(sum(weights.values())):
            picked = preview.next_game(games)
            if picked is None:
                break
            order.append(_game_id(picked))
        return order


_REGISTRY: Dict[str, Type[RotationStrategy]] = {}


def register_rotation_strategy(name: str, factory: Type[RotationStrategy]) -> None:
    """Register a rotation strategy under ``name``.

    When a plugin needs an ordering that core does not ship, it registers its
    own here instead of core growing a sport-specific branch. Re-registering a
    name replaces it, so a plugin may also override a built-in for itself.
    """
    if not name:
        raise ValueError("rotation strategy name must be a non-empty string")
    # Fail at registration, not at the first schedule() call several frames
    # later, where the cause is no longer on the stack.
    if not (isinstance(factory, type) and issubclass(factory, RotationStrategy)):
        raise TypeError(
            f"rotation strategy {name!r} must be a RotationStrategy subclass, "
            f"got {factory!r}"
        )
    factory.name = name
    _REGISTRY[name] = factory


def get_rotation_strategy(
    name: str, weight_for: Optional[Callable[[Dict], int]] = None
) -> RotationStrategy:
    """Build the strategy registered under ``name``.

    Falls back to ``"simple"`` for an unknown name rather than raising: the name
    arrives from user config, and a typo should cost the boost, not the
    scoreboard.
    """
    factory = _REGISTRY.get(name) or _REGISTRY["simple"]
    return factory(weight_for=weight_for)


register_rotation_strategy("simple", SimpleRotation)
register_rotation_strategy("weighted", WeightedCycleRotation)
register_rotation_strategy("swrr", SmoothWeightedRotation)
