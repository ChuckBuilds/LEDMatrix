"""Which games a scoreboard shows, for how long, and what its scorebug dates say.

Four ``sports.py`` methods are identical (executable AST, docstrings
stripped, decorators compared) in every scoreboard that carries them, and
were copied here from ledmatrix-plugins ``56c4f15`` (origin/main,
2026-09-30) under their existing names. They split into two mixins because
their carriers differ, and a plugin should not gain an override it did not
have:

``SportsCardOptionsMixin`` -- afl, baseball, basketball, football, hockey,
lacrosse, nrl and soccer (ufc draws no team scorebug):

- ``_card_option`` -- reads one ``scroll_card`` key through
  ``SportsCoreSharedMixin._card_option``, but never lets the upcoming
  scorebug lose both its date and its time (the combination a settings-form
  bug saved for a whole cohort of boards);
- ``_recent_date_text`` -- the date line of the full-screen recent scorebug.

``SportsGameRulesMixin`` -- all nine:

- ``_filtered_or_all`` (all but football, which has no such method) -- the
  quality and division filters on a board with no favourites, failing open
  to every game rather than a blank panel;
- ``_effective_live_duration`` (all but ufc, which has none) -- how long a
  live game stays up: ``non_favorite_live_game_duration`` for a
  non-favourite when favourites are set, else ``game_display_duration``.
  afl, nrl and soccer carry it on ``SportsCore``, the other five on
  ``SportsLive``; the bodies are the same.

The plugin missing a method gains one it never calls, which changes nothing:
nothing in that plugin, nor in core, calls it.

A new module rather than more methods on ``sports_shared``, for the reason
``sports_helpers`` gives: a missing module fails at load, where the version
checks see it; a missing method fails mid-update.

WHAT A HOST MUST PROVIDE
------------------------
Derived by walking every ``self.<attr>`` the mixins read; the host-contract
test in ``test/test_sports_display_rules.py`` fails if a read is added
without being listed here.

``SportsCardOptionsMixin``:

- ``SportsCoreSharedMixin`` (``src.common.sports_shared``) in the MRO
  **after** this mixin: ``_card_option`` calls that mixin's ``_card_option``
  and ``_switch_upcoming_center`` by name, and ``_recent_date_text`` its
  ``_format_game_date``. List this mixin first --
  ``class SportsCore(SportsCardOptionsMixin, SportsGameRulesMixin,
  SportsFetchMixin, SportsCoreSharedMixin, SportsHelpersMixin, ABC)`` --
  or ``SportsCoreSharedMixin._card_option`` wins and the rescue is lost.
  Through it: ``config`` (the ``scroll_card`` block it reads).

``SportsGameRulesMixin``:

- ``_passes_other_filters(game)`` -- the plugin's own quality/division
  filter (``_filtered_or_all``).
- ``_check_ranking_coverage(games)`` -- from ``SportsCoreSharedMixin``.
- ``favorite_teams``, ``game_display_duration`` and
  ``_is_favorite_game(game)``; ``non_favorite_live_game_duration`` read with
  ``getattr`` (``_effective_live_duration``).

Neither mixin has an ``__init__`` or state. A method on the plugin's own
class still wins over either.
"""

from typing import Any, Callable, Dict, List, Optional

from src.common.sports_shared import SportsCoreSharedMixin


class SportsCardOptionsMixin:
    """The scorebug's ``scroll_card`` reads. See module docstring."""

    # The host contract, declared for type checking only.
    _format_game_date: Callable[..., str]

    def _card_option(self, key: str, default: Any = None) -> Any:
        """Read one scroll_card key, never blanking the upcoming scorebug.

        With the middle set to "date and time" and both of those lines
        switched off, the full-screen upcoming scorebug is two logos and
        "Next Game" with nothing to say when the game is. Nobody picks that
        on purpose -- "vs" and "none" are the settings for a card without the
        stack -- yet a whole cohort of boards has it: switch_show_date/_time
        shipped while the core's settings form still drew keys missing from
        the saved config as unchecked boxes, so the next Save wrote both as
        false (fixed in LEDMatrix #597). That one combination therefore reads
        as both on. Hiding either line alone, or both under "vs" or "none",
        is still honoured.
        """
        # The mixin named outright, not super(): tests lift this method onto
        # stand-in classes that are not SportsCore subclasses.
        base = SportsCoreSharedMixin._card_option
        keys = ("switch_show_date", "switch_show_time")
        value = base(self, key, default)  # type: ignore[arg-type]
        if (key in keys and not value
                and not any(base(self, k, True) for k in keys)  # type: ignore[arg-type]
                and SportsCoreSharedMixin._switch_upcoming_center(self) == "date_time"):  # type: ignore[arg-type]
            return True
        return value

    def _recent_date_text(self, game: Optional[Dict]) -> str:
        """When a finished game was played, for the full-screen scorebug.

        Formatted by switch_date_format, like the upcoming scorebug, so the
        two dates on this display agree; its "numeric" default returns the
        extractor's "9/23" unchanged. ``switch_recent_show_date`` (default
        true) is the off switch.
        """
        if not self._card_option("switch_recent_show_date", True):
            return ""
        return self._format_game_date(str((game or {}).get("game_date") or ""), game)


class SportsGameRulesMixin:
    """Which games are worth showing, and for how long. See module docstring."""

    # The host contract, declared for type checking only.
    favorite_teams: List[str]
    game_display_duration: float
    _passes_other_filters: Callable[[Dict], bool]
    _check_ranking_coverage: Callable[[List[Dict]], None]
    _is_favorite_game: Callable[[Dict], bool]

    def _filtered_or_all(self, games: List[Dict]) -> List[Dict]:
        """The games worth watching, or all of them if that leaves none.

        With no favourites configured every game selected is a non-favourite
        game, so the quality and division settings have to apply here too. They
        governed only the top-up slice, which this branch never uses, so a
        board with an empty favourites list had both settings silently inert --
        it could ask for ranked games only and still get the next N kickoffs.

        Fails open as a whole, not just per check. `_passes_other_filters`
        allows a game whose data could not be resolved, but a filter working
        exactly as asked can still match nothing on a given day, and here there
        is no favourite left to carry the mode -- an empty list is a blank
        panel rather than a short one.
        """
        kept = [g for g in games if self._passes_other_filters(g)]
        self._check_ranking_coverage(games)
        return kept or games

    def _effective_live_duration(self, game) -> float:
        """How long the given live game should stay on screen before rotating.

        Non-favorite live games use non_favorite_live_game_duration, but only
        when it is set (> 0) AND favorite teams are configured. With no favorites
        (or the knob at 0) every live game uses game_display_duration - identical
        to the prior single-duration behavior. When show_favorite_teams_only is
        on, non-favorite games are never shown, so this naturally never fires."""
        non_fav = getattr(self, "non_favorite_live_game_duration", 0) or 0
        if (
            non_fav > 0
            and self.favorite_teams
            and game is not None
            and not self._is_favorite_game(game)
        ):
            return non_fav
        return self.game_display_duration


__all__ = ["SportsCardOptionsMixin", "SportsGameRulesMixin"]
