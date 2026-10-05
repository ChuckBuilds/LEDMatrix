"""Which games involve a favourite team, and which of them to show (sports family 6).

The scoreboards' favourite matching, reconciled in ledmatrix-plugins
(family 6) from seven ``_is_favorite_game`` bodies, two
``_select_games_for_display`` and three ``_select_recent_games_for_display``
into one each, and copied here under their existing names:

- ``SportsFavoritesMixin`` (``SportsCore``): ``_is_favorite_game(game)``,
  asked by ``SportsCoreSharedMixin._favorites_first``, the switch-mode
  favourite boost (``SportsHelpersMixin._next_switch_index``), the
  non-favourite live dwell (``SportsGameRulesMixin._effective_live_duration``)
  and the plugins' live rotation; and ``_favorite_code(value)``, the
  normalisation both sides of every comparison go through.
- ``SportsUpcomingFavoritesMixin`` (``SportsUpcoming``):
  ``_select_games_for_display``, the favourites-only pick of upcoming games.
- ``SportsRecentFavoritesMixin`` (``SportsRecent``):
  ``_select_recent_games_for_display``, the same for finished games, most
  recent first.

Each mixin carries only what its class already had, so no manager gains a
method it did not have.

THE RULE
--------
Each side of a game is named by ``_favorite_key(game, side)``, the override
point ``SportsHelpersMixin`` (``src.common.sports_helpers``) has carried since
3.5.0: the team abbreviation by default. A sport whose abbreviations are not
unique overrides it -- NRL returns the ESPN team id (and None when the id is
missing), because "NEW" is both Newcastle and New Zealand. That value and every
entry of ``favorite_teams`` are compared as ``_favorite_code`` leaves them:
stripped and upper-cased, a blank or missing value matching nothing. So
" bos" in the config matches BOS.

The selection methods give each favourite team up to the per-team limit
(``upcoming_games_to_show`` / ``recent_games_to_show``); a game between two
favourites counts for both. Only a game with an id can be a duplicate: two
games without one are two games.

A new module rather than more methods on ``sports_shared`` or
``sports_helpers``, for the reason ``sports_helpers`` gives: a missing module
fails at load, where the version checks see it; a missing method fails
mid-update.

WHAT A HOST MUST PROVIDE
------------------------
Derived by walking every ``self.<attr>`` the mixins read; the host-contract
test in ``test/test_sports_favorites.py`` fails if a read is added without
being listed here.

- ``favorite_teams`` -- the resolved favourites list (``_is_favorite_game``).
  The selection methods are handed the list instead.
- ``_favorite_key`` -- ``SportsHelpersMixin`` supplies the default.
- ``_favorite_code`` -- from ``SportsFavoritesMixin``, which the Upcoming and
  Recent classes inherit through their ``SportsCore``.
- ``logger`` -- the selection methods log each pick at DEBUG and a summary at
  INFO.
- ``upcoming_games_to_show`` (Upcoming) and ``recent_games_to_show`` (Recent)
  -- the per-team limits.

The methods read the game dict's ``id`` and ``start_time_utc`` (selection),
whatever ``_favorite_key`` reads (``home_abbr`` / ``away_abbr`` by default),
and ``home_abbr`` / ``away_abbr`` again for the DEBUG line; any may be missing.

BASE ORDER
----------
No other mixin defines these methods, so the position in the bases does not
change which body runs; a method on the plugin's own class still wins. The
mixins have no ``__init__`` and no state.
"""

import logging
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional


class SportsFavoritesMixin:
    """``SportsCore``'s favourite check. See module docstring."""

    # The host contract, declared for type checking only.
    favorite_teams: List[str]
    _favorite_key: Callable[[Dict, str], Optional[str]]

    @staticmethod
    def _favorite_code(value) -> Optional[str]:
        """``value`` as favourites are compared: stripped and upper-cased.

        None for a missing or blank value, which matches nothing.
        """
        if value is None:
            return None
        return str(value).strip().upper() or None

    def _is_favorite_game(self, game: Dict) -> bool:
        """Does either side of this game belong to a favourite team?

        ``_favorite_key`` names each side (the abbreviation; nrl overrides it
        with the ESPN team id), and both it and ``favorite_teams`` are compared
        as ``_favorite_code`` normalises them, so " bos" matches BOS.
        """
        favorites = {self._favorite_code(team) for team in self.favorite_teams or ()}
        favorites.discard(None)
        return any(
            self._favorite_code(self._favorite_key(game, side)) in favorites
            for side in ("home", "away")
        )


class SportsUpcomingFavoritesMixin:
    """``SportsUpcoming``'s favourites-only pick. See module docstring."""

    # The host contract, declared for type checking only.
    logger: logging.Logger
    upcoming_games_to_show: int
    _favorite_key: Callable[[Dict, str], Optional[str]]
    _favorite_code: Callable[[object], Optional[str]]

    def _select_games_for_display(
        self, processed_games: List[Dict], favorite_teams: List[str]
    ) -> List[Dict]:
        """
        Single-pass game selection with proper deduplication and counting.

        When a game involves two favorite teams, it counts toward BOTH teams' limits.
        This prevents unexpected game counts from the multi-pass algorithm.
        Teams are matched as _is_favorite_game matches them. Only a game with
        an id can be a duplicate: two games without one are two games.
        """
        sorted_games = sorted(
            processed_games,
            key=lambda g: g.get("start_time_utc")
            or datetime.max.replace(tzinfo=timezone.utc),
        )

        if not favorite_teams:
            return sorted_games

        selected_games = []
        selected_ids = set()
        team_counts: Dict[Optional[str], int] = {
            code: 0 for code in map(self._favorite_code, favorite_teams) if code
        }

        for game in sorted_games:
            game_id = game.get("id")
            if game_id is not None and game_id in selected_ids:
                continue

            home = self._favorite_code(self._favorite_key(game, "home"))
            away = self._favorite_code(self._favorite_key(game, "away"))

            home_fav = home in team_counts
            away_fav = away in team_counts

            if not home_fav and not away_fav:
                continue

            home_needs = home_fav and team_counts[home] < self.upcoming_games_to_show
            away_needs = away_fav and team_counts[away] < self.upcoming_games_to_show

            if home_needs or away_needs:
                selected_games.append(game)
                if game_id is not None:
                    selected_ids.add(game_id)
                if home_fav:
                    team_counts[home] += 1
                if away_fav:
                    team_counts[away] += 1

                self.logger.debug(
                    f"Selected game {game.get('away_abbr')}@{game.get('home_abbr')}: "
                    f"team_counts={team_counts}"
                )

            if all(c >= self.upcoming_games_to_show for c in team_counts.values()):
                self.logger.debug("All favorite teams satisfied, stopping selection")
                break

        self.logger.info(
            f"Selected {len(selected_games)} games for {len(favorite_teams)} "
            f"favorite teams: {team_counts}"
        )
        return selected_games


class SportsRecentFavoritesMixin:
    """``SportsRecent``'s favourites-only pick. See module docstring."""

    # The host contract, declared for type checking only.
    logger: logging.Logger
    recent_games_to_show: int
    _favorite_key: Callable[[Dict, str], Optional[str]]
    _favorite_code: Callable[[object], Optional[str]]

    def _select_recent_games_for_display(
        self, processed_games: List[Dict], favorite_teams: List[str]
    ) -> List[Dict]:
        """
        Single-pass game selection for recent games with proper deduplication.

        When a game involves two favorite teams, it counts toward BOTH teams' limits.
        Games are sorted by most recent first.
        Teams are matched as _is_favorite_game matches them. Only a game with
        an id can be a duplicate: two games without one are two games.
        """
        sorted_games = sorted(
            processed_games,
            key=lambda g: g.get("start_time_utc")
            or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )

        if not favorite_teams:
            return sorted_games

        selected_games = []
        selected_ids = set()
        team_counts: Dict[Optional[str], int] = {
            code: 0 for code in map(self._favorite_code, favorite_teams) if code
        }

        for game in sorted_games:
            game_id = game.get("id")
            if game_id is not None and game_id in selected_ids:
                continue

            home = self._favorite_code(self._favorite_key(game, "home"))
            away = self._favorite_code(self._favorite_key(game, "away"))

            home_fav = home in team_counts
            away_fav = away in team_counts

            if not home_fav and not away_fav:
                continue

            home_needs = home_fav and team_counts[home] < self.recent_games_to_show
            away_needs = away_fav and team_counts[away] < self.recent_games_to_show

            if home_needs or away_needs:
                selected_games.append(game)
                if game_id is not None:
                    selected_ids.add(game_id)
                if home_fav:
                    team_counts[home] += 1
                if away_fav:
                    team_counts[away] += 1

                self.logger.debug(
                    f"Selected recent game {game.get('away_abbr')}@{game.get('home_abbr')}: "
                    f"team_counts={team_counts}"
                )

            if all(c >= self.recent_games_to_show for c in team_counts.values()):
                self.logger.debug("All favorite teams satisfied, stopping selection")
                break

        self.logger.info(
            f"Selected {len(selected_games)} recent games for {len(favorite_teams)} "
            f"favorite teams: {team_counts}"
        )
        return selected_games


__all__ = ["SportsFavoritesMixin", "SportsUpcomingFavoritesMixin", "SportsRecentFavoritesMixin"]
