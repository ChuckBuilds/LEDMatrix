"""The ``sports_card`` delegations every scoreboard's game renderer carries.

After the card helpers moved to ``sports_card`` (3.3.0), each of the eight
scoreboards with a ``game_renderer.py`` -- afl, baseball, basketball,
football, hockey, lacrosse, nrl and soccer -- kept one-line methods that
forward to them with its own ``config`` and ``logger``. Seventeen are
identical in all eight (executable AST, docstrings stripped) or in all but
football, and were copied here from ledmatrix-plugins ``30455671``
(origin/main, 2026-09-29) under their existing names. Football's own
``_format_game_date`` and ``_upcoming_center_mode`` (they follow the
switch-mode settings when it draws the full-screen scorebug) stay in football
and override these.

``_schema_font_size`` and ``_resolve_font_size`` look the same in every copy
but are not moved: they read ``_SCHEMA_PATH``, a module global that is each
plugin's own ``config_schema.json``.

These are the methods ``SportsGameRendererMixin`` (``sports_game_renderer``)
lists among what its host must provide, so a renderer that inherits both no
longer has to write them. Like that mixin this has no ``__init__`` and no
state. It is a separate module rather than more methods there for the reason
``sports_helpers`` gives: a missing module fails at load, where the version
checks see it; a missing method fails mid-render.

WHAT A HOST MUST PROVIDE
------------------------
Derived by walking every ``self.<attr>`` the mixin reads; the host-contract
test in ``test/test_sports_card_wrappers.py`` fails if a read is added
without being listed here.

- ``config`` and ``logger``.
- ``fonts``, read with ``getattr`` -- ``_font_color``.
- ``_FONT_NAME_ALIASES`` and ``_FONT_PIXEL_GRID`` class attributes --
  ``_crisp_size``, which passes them to ``sports_card.crisp_size`` so a
  renderer that declares extra faces keeps them.

Add it as a base of the plugin's renderer, e.g.
``class GameRenderer(SportsCardWrappersMixin, SportsGameRendererMixin)``.
The two define no name in common; a method on the plugin's own class still
wins over either.
"""

import logging
from typing import Any, ClassVar, Dict, Optional, Tuple

from src.common import sports_card as _card


class SportsCardWrappersMixin:
    """The game renderer's ``sports_card`` delegations. See module docstring."""

    # The host contract, declared for type checking only: these create no
    # attributes, so the host's own values are what the methods read.
    config: Dict[str, Any]
    logger: logging.Logger
    _FONT_NAME_ALIASES: ClassVar[Dict[str, str]]
    _FONT_PIXEL_GRID: ClassVar[Dict[str, Any]]

    # ---- fonts ---------------------------------------------------------

    @classmethod
    def _crisp_size(cls, font_file, desired):
        """``sports_card.crisp_size`` with this renderer's font tables."""
        return _card.crisp_size(font_file, desired,
                                cls._FONT_NAME_ALIASES, cls._FONT_PIXEL_GRID)

    def _unshare_element_fonts(self, fonts):
        """``sports_card.unshare_element_fonts``."""
        return _card.unshare_element_fonts(self.logger, fonts)

    def _font_color(self, font, default: Tuple[int, int, int] = (255, 255, 255)):
        """``sports_card.font_color`` for one of ``self.fonts``."""
        return _card.font_color(self.config, getattr(self, "fonts", None), font, default)

    # ---- colours and favourites ---------------------------------------

    @staticmethod
    def _coerce_rgb(value, fallback):
        """``sports_card.coerce_rgb``."""
        return _card.coerce_rgb(value, fallback)

    @staticmethod
    def _side_is_favorite(game: Dict[str, Any], side: str, favorites: set) -> bool:
        """``sports_card.side_is_favorite``."""
        return _card.side_is_favorite(game, side, favorites)

    @staticmethod
    def _side_score(game: Dict[str, Any], side: str) -> Optional[int]:
        """``sports_card.side_score``."""
        return _card.side_score(game, side)

    def _favorite_result(self, game: Dict[str, Any]) -> Optional[str]:
        """``sports_card.favorite_result``."""
        return _card.favorite_result(self.config, game)

    def _score_color_for(self, game: Dict[str, Any], game_type: str, default=None):
        """``sports_card.score_color_for``."""
        return _card.score_color_for(self.config, self.logger, game, game_type, default)

    def _recent_score_color(self, game: Dict[str, Any], default):
        """``sports_card.recent_score_color``."""
        return _card.recent_score_color(self.config, self.logger, game, default)

    def _element_color(self, element: str, default: Tuple[int, int, int] = (255, 255, 255)):
        """``sports_card.element_color``."""
        return _card.element_color(self.config, element, default)

    # ---- card options, dates and times --------------------------------

    def _scroll_card_option(self, key: str, default: Any = None) -> Any:
        """``sports_card.scroll_card_option``."""
        return _card.scroll_card_option(self.config, key, default)

    def _upcoming_center_mode(self) -> str:
        """``sports_card.upcoming_center_mode``."""
        return _card.upcoming_center_mode(self.config)

    def _vs_text(self) -> str:
        """``sports_card.vs_text``."""
        return _card.vs_text(self.config)

    def _format_game_date(self, date_text: str, game: Optional[Dict] = None) -> str:
        """``sports_card.format_game_date``."""
        return _card.format_game_date(self.config, self.logger, date_text, game)

    def _weekday_for(self, game: Optional[Dict]) -> str:
        """``sports_card.weekday_for``."""
        return _card.weekday_for(self.config, self.logger, game)

    def _card_tzinfo(self):
        """``sports_card.card_tzinfo``."""
        return _card.card_tzinfo(self.config, self.logger)

    def _format_game_time(self, time_text: str) -> str:
        """``sports_card.format_game_time``."""
        return _card.format_game_time(self.config, time_text)
