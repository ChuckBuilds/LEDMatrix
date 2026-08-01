"""Score / win celebration takeover — an opt-in capability.

Four of the nine scoreboards celebrate (afl, nrl, soccer, football); the other
five do not. This is a **mixin** rather than a flag inside ``SportsLive`` so the
five that do not opt in have none of this code in their MRO: a bug here cannot
reach hockey, and hockey's config never grows keys it ignores.

Usage — mix in *before* the mode class so its ``display`` runs first::

    class SoccerLive(CelebrationMixin, SportsLive):
        def score_phrase(self, points, team_abbr):
            return secrets.choice(("GOOOOAAALLL!", f"{team_abbr} SCORES!"))

The two lineages spelled this differently (``_check_for_goal`` /
``celebrate_opponent_goals`` in the soccer lineage, ``_check_for_score`` /
``celebrate_opponent_scores`` in football) but the bodies were identical apart
from three things, each of which is a seam here rather than a branch:

* **wording** — :meth:`score_phrase`, the hook football uses to say "TOUCHDOWN"
  from the points delta and soccer uses to say "GOOOOAAALLL";
* **follow-up suppression** — :attr:`COALESCE_SCORING_SEQUENCE`, on for football
  where a touchdown lands as +6 then +1 a few seconds later, off elsewhere where
  two quick goals are two real events;
* **team identity** — matching goes through ``_favorite_key``, so nrl can match
  on team id (its abbreviations are ambiguous) without core knowing why.

The config keys are read under both spellings, so a plugin adopting the mixin
keeps working with the ``*_goals`` keys already in its published schema.
"""

from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Optional

from PIL import Image, ImageDraw


class CelebrationMixin:
    """Full-screen takeover when a tracked team scores or wins."""

    #: Collapse increments that land while a celebration is already on screen
    #: into that one celebration. True for sports where a single scoring play
    #: arrives as more than one score update (football: touchdown +6, then the
    #: extra point +1). False where consecutive increments are distinct events —
    #: suppressing there would swallow a real goal.
    COALESCE_SCORING_SEQUENCE = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        mode_config = getattr(self, "mode_config", {}) or {}
        self.celebration_enabled = mode_config.get("celebration_enabled", True)
        self.celebration_duration = mode_config.get("celebration_duration", 8)
        # Both spellings: the soccer lineage ships `celebrate_opponent_goals`,
        # football ships `celebrate_opponent_scores`. Whichever the plugin's
        # schema declares is the one its users have set.
        self.celebrate_opponent_scores = mode_config.get(
            "celebrate_opponent_scores",
            mode_config.get("celebrate_opponent_goals", False),
        )
        # Per-game score baselines: {game_id: {"away": int, "home": int}}
        self._score_baselines: Dict[str, Dict[str, int]] = {}
        # The active celebration (a game *snapshot*, so a win survives the game
        # leaving live_games) or None. See _start_celebration for the shape.
        self.active_celebration: Optional[Dict[str, Any]] = None

    # ------------------------------------------------------------------
    # Override points
    # ------------------------------------------------------------------

    def score_phrase(self, points: int, team_abbr: str) -> str:
        """The wording for a score celebration.

        ``points`` is the score delta that triggered it, which sports with
        variable-value scores use to name the play. The default is deliberately
        sport-neutral; every celebrating plugin overrides it.
        """
        return f"{team_abbr} SCORES!"

    def win_phrase(self, team_abbr: str) -> str:
        """The wording for a win celebration."""
        return f"{team_abbr} WINS!"

    def _is_favorite(self, key: Optional[str]) -> bool:
        """Whether ``key`` (whatever ``_favorite_key`` returns) is a favorite."""
        return bool(self.favorite_teams) and key in self.favorite_teams

    # ------------------------------------------------------------------
    # Detection
    # ------------------------------------------------------------------

    @staticmethod
    def _score_to_int(score) -> Optional[int]:
        """Coerce an ESPN score value (str / int / dict) to an int, or None."""
        try:
            if score is None:
                return None
            if isinstance(score, str):
                s = score.strip()
                if not s:
                    return None
                try:
                    return int(float(s))
                except ValueError:
                    numbers = re.findall(r"\d+", s)
                    return int(numbers[0]) if numbers else None
            if isinstance(score, dict):
                return int(float(score.get("value", score.get("displayValue", 0))))
            return int(float(score))
        except (ValueError, TypeError):
            return None

    def _should_celebrate_for(self, game: Dict, side: str) -> bool:
        """Whether a score by ``side`` in ``game`` should trigger a celebration."""
        if self._is_favorite(self._favorite_key(game, side)):
            return True
        if not self.favorite_teams:
            # No favorites configured: the user opted to show this game, so
            # celebrate any score in it.
            return True
        # Favorites exist but this team isn't one -> it's the opponent.
        return self.celebrate_opponent_scores

    def has_active_celebration(self) -> bool:
        """True while a celebration is within its display window."""
        celebration = self.active_celebration
        return bool(celebration) and (
            time.time() - celebration["started_at"] < self.celebration_duration
        )

    def _check_for_score(self, game: Dict) -> None:
        """Compare a live game's score against its baseline and arm a
        celebration when a celebratable team's score increases."""
        if not self.celebration_enabled:
            return
        game_id = game.get("id")
        if not game_id:
            return
        away = self._score_to_int(game.get("away_score"))
        home = self._score_to_int(game.get("home_score"))
        if away is None or home is None:
            return

        baseline = self._score_baselines.get(game_id)
        # Always refresh the baseline: a first sighting must never celebrate (a
        # game already in progress at boot would false-fire), and a decrement
        # (VAR, a correction) just re-bases silently.
        self._score_baselines[game_id] = {"away": away, "home": home}
        if baseline is None:
            return

        away_delta = away - baseline["away"]
        home_delta = home - baseline["home"]
        if away_delta <= 0 and home_delta <= 0:
            return

        # One takeover per scoring sequence, where the sport has such a thing.
        # The baseline is already advanced above, so nothing re-fires later.
        if self.COALESCE_SCORING_SEQUENCE and self.has_active_celebration():
            return

        scored_side = None
        points = 0
        if away_delta > 0 and self._should_celebrate_for(game, "away"):
            scored_side, points = "away", away_delta
        if scored_side is None and home_delta > 0 and self._should_celebrate_for(
            game, "home"
        ):
            scored_side, points = "home", home_delta
        if scored_side is None:
            return

        self._start_celebration(
            game,
            "score",
            scored_side=scored_side,
            team_abbr=game.get(f"{scored_side}_abbr", ""),
            away_score=away,
            home_score=home,
            points=points,
        )

    def _check_for_win(self, game: Dict) -> None:
        """When a game we were tracking live goes final, arm a win celebration
        if a favorite won. Fires at most once per game."""
        if not self.celebration_enabled:
            return
        game_id = game.get("id")
        if not game_id:
            return
        # Only celebrate wins for games we actually watched go live: one seen
        # for the first time already-final (the board started after full time)
        # has no baseline and must not fire.
        if game_id not in self._score_baselines:
            return
        # Consume the baseline so this can only fire once.
        self._score_baselines.pop(game_id, None)

        away = self._score_to_int(game.get("away_score"))
        home = self._score_to_int(game.get("home_score"))
        if away is None or home is None:
            return

        if away > home:
            winner_side = "away"
        elif home > away:
            winner_side = "home"
        else:
            return  # draw -> no win celebration

        # Wins are gated strictly on favorites: every game ends, so the
        # "no favorites -> celebrate all" score fallback would be far too noisy.
        if not self._is_favorite(self._favorite_key(game, winner_side)):
            return

        self._start_celebration(
            game,
            "win",
            scored_side=winner_side,
            team_abbr=game.get(f"{winner_side}_abbr", ""),
            away_score=away,
            home_score=home,
        )

    def _start_celebration(
        self,
        game: Dict,
        kind: str,
        scored_side: str,
        team_abbr: str,
        away_score: int,
        home_score: int,
        points: int = 0,
    ) -> None:
        """Arm a celebration. ``scored_side`` ('away'/'home') is the side whose
        score digit gets highlighted."""
        phrase = (
            self.win_phrase(team_abbr)
            if kind == "win"
            else self.score_phrase(points, team_abbr)
        )

        self.active_celebration = {
            "kind": kind,
            "game": dict(game),  # snapshot: survives the game leaving live_games
            "scored_side": scored_side,
            "team_abbr": team_abbr,
            "away_score": away_score,
            "home_score": home_score,
            "started_at": time.time(),
            "phrase": phrase,
        }
        # Pin focus to the involved game so the post-celebration scorebug
        # resumes on it.
        self.current_game = dict(game)
        self.logger.info(
            f"Celebration ({kind}) armed: {phrase} "
            f"[{game.get('away_abbr')} {away_score}-{home_score} {game.get('home_abbr')}]"
        )

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def _fit_font(self, draw, text: str, max_width: int, fonts: List):
        """The first font whose rendered ``text`` fits ``max_width``, falling
        back to the last (smallest) font."""
        for font in fonts:
            if draw.textlength(text, font=font) <= max_width - 2:
                return font
        return fonts[-1]

    def _draw_celebration_layout(
        self, celebration: Dict, force_clear: bool = False
    ) -> None:
        """Render the full-screen score/win takeover."""
        if force_clear:
            self.display_manager.clear()

        display_width = (
            self.display_manager.matrix.width
            if hasattr(self.display_manager, "matrix") and self.display_manager.matrix
            else self.display_width
        )
        display_height = (
            self.display_manager.matrix.height
            if hasattr(self.display_manager, "matrix") and self.display_manager.matrix
            else self.display_height
        )

        elapsed = time.time() - celebration["started_at"]
        game = celebration["game"]

        # Background: a brief color flash for the first ~1.2s, then black.
        bg = (0, 0, 0, 255)
        if elapsed < 1.2 and int(elapsed / 0.2) % 2 == 0:
            bg = (12, 12, 48, 255)
        main_img = Image.new("RGBA", (display_width, display_height), bg)
        overlay = Image.new("RGBA", (display_width, display_height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        # Logos at the edges (best-effort: a logo failure must not blank the
        # celebration).
        try:
            center_y = display_height // 2
            home_logo = self._load_and_resize_logo(
                game.get("home_id"), game.get("home_abbr"),
                game.get("home_logo_path"), game.get("home_logo_url"),
            )
            away_logo = self._load_and_resize_logo(
                game.get("away_id"), game.get("away_abbr"),
                game.get("away_logo_path"), game.get("away_logo_url"),
            )
            if home_logo:
                main_img.paste(
                    home_logo,
                    (display_width - home_logo.width + 2, center_y - home_logo.height // 2),
                    home_logo,
                )
            if away_logo:
                main_img.paste(
                    away_logo, (-2, center_y - away_logo.height // 2), away_logo
                )
        except Exception as e:
            self.logger.debug(f"Celebration logo load failed: {e}")

        # Phrase across the top, shrunk to fit the panel width.
        phrase = celebration["phrase"]
        phrase_font = self._fit_font(
            draw, phrase, display_width, [self.fonts["time"], self.fonts["status"]]
        )
        phrase_width = draw.textlength(phrase, font=phrase_font)
        self._draw_text_with_outline(
            draw, phrase, ((display_width - phrase_width) // 2, 1), phrase_font
        )

        # Score centered low, with the scoring/winning side's digit pulsing in a
        # highlight color so the change reads at a glance.
        away_text = str(celebration["away_score"])
        home_text = str(celebration["home_score"])
        score_font = self.fonts["score"]
        segments = [
            (away_text, celebration["scored_side"] == "away"),
            ("-", False),
            (home_text, celebration["scored_side"] == "home"),
        ]
        total_width = sum(draw.textlength(seg, font=score_font) for seg, _ in segments)
        highlight = (255, 255, 0) if int(elapsed * 4) % 2 == 0 else (255, 170, 0)
        x = (display_width - total_width) // 2
        y = display_height - 14
        for seg, is_highlight in segments:
            color = highlight if is_highlight else (255, 255, 255)
            self._draw_text_with_outline(draw, seg, (int(x), y), score_font, fill=color)
            x += draw.textlength(seg, font=score_font)

        main_img = Image.alpha_composite(main_img, overlay).convert("RGB")
        self.display_manager.image = main_img
        self.display_manager.update_display()

    def display(self, force_clear: bool = False) -> bool:
        """Render an active celebration as a full-screen takeover; otherwise
        defer to the normal live scorebug."""
        if not self.is_enabled:
            return False
        celebration = self.active_celebration
        if celebration:
            if time.time() - celebration["started_at"] < self.celebration_duration:
                try:
                    self._draw_celebration_layout(celebration, force_clear)
                    return True
                except Exception as e:
                    self.logger.error(f"Error drawing celebration: {e}", exc_info=True)
            else:
                self.active_celebration = None
                # Reset the dwell so the scorebug resumes on the scoring/winning
                # game for a full duration before rotation can move on.
                self.last_game_switch = time.time()
        return super().display(force_clear)
