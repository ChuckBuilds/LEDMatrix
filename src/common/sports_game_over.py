"""Whether a game ESPN still lists as live has in fact ended (sports family 5).

``SportsGameOverMixin._is_game_really_over`` is the scoreboards'
``SportsLive._is_game_really_over``, reconciled in ledmatrix-plugins
``claude/family5-reconcile`` from five bodies into one and copied here under
its existing name. ``SportsLiveSharedMixin._detect_stale_games``
(``src.common.sports_shared``) calls it on every live game, and the plugins'
live-priority filters call it too, to drop a game ESPN still reports as
in progress.

A game is over when its period text says final. From period ``FINAL_PERIOD``
on, a clock reading 0:00 ends it too, unless the score is level: a tie at the
end of regulation goes to overtime (or a shootout), and a game that does end
tied says final. Only a clock *string* is read ("0:00" and ":00" are zero;
":40", "0.0" and ESPN's "-" between MMA rounds are not), and a missing or
unreadable score leaves the decision to the clock.

``FINAL_PERIOD`` is the one per-sport fact, a class attribute rather than a
sport-name branch. The scoreboards declare it on their ``SportsLive``:

- 3: hockey;
- 4: basketball, football, lacrosse;
- ``None`` (this default; the clock never ends a game): afl, nrl and soccer,
  whose clocks count up; baseball, which has innings; ufc, whose bouts end
  only on ESPN's final status.

A sport can still override the method and defer to it, as baseball's
``BaseballLive`` does to end postponed and suspended games first.

A new module rather than another method on ``sports_shared``, for the reason
``sports_helpers`` gives: a missing module fails at load, where the version
checks see it; a missing method fails mid-update.

WHAT A HOST MUST PROVIDE
------------------------
Derived by walking every ``self.<attr>`` the mixin reads; the host-contract
test in ``test/test_sports_game_over.py`` fails if a read is added without
being listed here.

- ``logger`` -- a ``logging.Logger``; the method logs its verdict at DEBUG.
- ``FINAL_PERIOD`` -- defaulted here to ``None``; set it on the host class.

The method reads the game dict's ``away_abbr``, ``home_abbr``,
``period_text``, ``period``, ``clock``, ``away_score`` and ``home_score``
(``_extract_game_details_common``'s keys); any of them may be missing or
null.

BASE ORDER
----------
List the mixin before ``SportsLiveSharedMixin`` --
``class SportsLive(SportsGameOverMixin, SportsLiveSharedMixin, SportsCore)`` --
so the shared mixin's ``_detect_stale_games`` finds this method through the
MRO. Neither shared mixin defines it, so the order does not change which body
runs today; it keeps the method next to its caller should one ever be added
there. A method on the plugin's own class still wins, and its ``super()``
reaches this one. The mixin has no ``__init__`` and no state.
"""

import logging
from typing import Dict, Optional


class SportsGameOverMixin:
    """The live manager's "is this game really over?" check. See module docstring."""

    # The host contract, declared for type checking only.
    logger: logging.Logger

    #: Period from which a 0:00 clock ends a game; None: the clock never does.
    FINAL_PERIOD: Optional[int] = None

    def _is_game_really_over(self, game: Dict) -> bool:
        """Whether a game ESPN still lists as live has in fact ended.

        It has when its period text says final. From period ``FINAL_PERIOD``
        on, a clock at 0:00 ends it too, unless the score is level: a tie at
        the end of regulation goes to overtime, and a game that does end tied
        says final. With ``FINAL_PERIOD = None`` the clock never ends a game.
        """
        game_str = f"{game.get('away_abbr')}@{game.get('home_abbr')}"

        # ESPN can send the key as null, and .get()'s default only covers a
        # missing key, so a None here crashed the whole live update.
        raw_period_text = game.get("period_text")
        period_text = raw_period_text.lower() if isinstance(raw_period_text, str) else ""
        if "final" in period_text:
            self.logger.debug(
                f"_is_game_really_over({game_str}): "
                f"returning True - 'final' in period_text='{period_text}'"
            )
            return True

        # Same for a null or non-numeric period: treat it as period 0.
        try:
            period = int(game.get("period") or 0)
        except (TypeError, ValueError, OverflowError):
            period = 0
        # Only a clock string is read: "0:00" and ":00" are zero; ":40" is not.
        clock = game.get("clock")
        clock_at_zero = isinstance(clock, str) and clock.replace(":", "").strip() in ("000", "00")

        if self.FINAL_PERIOD is not None and period >= self.FINAL_PERIOD and clock_at_zero:
            try:
                tied = int(game["away_score"]) == int(game["home_score"])
            except (KeyError, TypeError, ValueError, OverflowError):
                tied = False  # a missing or unreadable score leaves it to the clock
            if not tied:
                self.logger.debug(
                    f"_is_game_really_over({game_str}): "
                    f"returning True - clock at 0:00 (clock='{clock}', period={period})"
                )
                return True
            self.logger.debug(
                f"_is_game_really_over({game_str}): "
                f"returning False - tied at 0:00 (period={period}), overtime next"
            )
            return False

        self.logger.debug(
            f"_is_game_really_over({game_str}): returning False"
        )
        return False


__all__ = ["SportsGameOverMixin"]
