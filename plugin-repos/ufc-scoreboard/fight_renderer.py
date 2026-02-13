"""
Fight Renderer for UFC Scoreboard Plugin

Renders individual fight cards as PIL Images for both switch mode
(one fight at a time) and scroll mode (all fights scrolling horizontally).

Based on GameRenderer pattern from football-scoreboard plugin.
UFC/MMA adaptation based on work by Alex Resnick (legoguy1000) - PR #137
"""

import logging
from pathlib import Path
from typing import Dict, Any, Optional, Union
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)


class FightRenderer:
    """
    Renders individual fight cards as PIL Images for display.

    MMA-specific rendering differences from team sports:
    - Fighter headshots instead of team logos
    - Fight results (KO/TKO, Submission, Decision) instead of scores
    - Round + time for live fights instead of period + clock
    - Weight class display
    - Fighter names displayed near headshots
    """

    def __init__(
        self,
        display_width: int,
        display_height: int,
        config: Dict[str, Any],
        headshot_cache: Optional[Dict[str, Image.Image]] = None,
        custom_logger: Optional[logging.Logger] = None,
    ):
        self.display_width = display_width
        self.display_height = display_height
        self.config = config
        self.logger = custom_logger or logger

        # Shared headshot cache for performance
        self._headshot_cache = headshot_cache if headshot_cache is not None else {}

        # Load fonts
        self.fonts = self._load_fonts()

    def _load_fonts(self) -> Dict[str, Union[ImageFont.FreeTypeFont, Any]]:
        """Load fonts from config or use defaults."""
        fonts = {}
        customization = self.config.get("customization", {})

        fighter_name_config = customization.get("fighter_name_text", {})
        status_config = customization.get("status_text", {})
        result_config = customization.get("result_text", {})
        detail_config = customization.get("detail_text", {})

        try:
            fonts["fighter_name"] = self._load_font(
                fighter_name_config, default_path="assets/fonts/4x6-font.ttf", default_size=6
            )
            fonts["status"] = self._load_font(
                status_config, default_path="assets/fonts/tom-thumb.bdf", default_size=8
            )
            fonts["result"] = self._load_font(
                result_config, default_path="assets/fonts/PressStart2P-Regular.ttf", default_size=10
            )
            fonts["detail"] = self._load_font(
                detail_config, default_path="assets/fonts/4x6-font.ttf", default_size=6
            )
            # Additional fonts
            fonts["time"] = ImageFont.truetype("assets/fonts/tom-thumb.bdf", 8)
            fonts["score"] = ImageFont.truetype("assets/fonts/PressStart2P-Regular.ttf", 10)
            fonts["odds"] = ImageFont.truetype("assets/fonts/4x6-font.ttf", 6)
            fonts["record"] = ImageFont.truetype("assets/fonts/4x6-font.ttf", 6)
            self.logger.debug("Successfully loaded fight renderer fonts")
        except Exception as e:
            self.logger.error(f"Error loading fonts: {e}, using defaults")
            try:
                fonts["fighter_name"] = ImageFont.truetype("assets/fonts/4x6-font.ttf", 6)
                fonts["status"] = ImageFont.truetype("assets/fonts/4x6-font.ttf", 6)
                fonts["result"] = ImageFont.truetype("assets/fonts/PressStart2P-Regular.ttf", 10)
                fonts["detail"] = ImageFont.truetype("assets/fonts/4x6-font.ttf", 6)
                fonts["time"] = ImageFont.truetype("assets/fonts/4x6-font.ttf", 6)
                fonts["score"] = ImageFont.truetype("assets/fonts/PressStart2P-Regular.ttf", 10)
                fonts["odds"] = ImageFont.truetype("assets/fonts/4x6-font.ttf", 6)
                fonts["record"] = ImageFont.truetype("assets/fonts/4x6-font.ttf", 6)
            except IOError:
                self.logger.warning("Fonts not found, using default PIL font.")
                default_font = ImageFont.load_default()
                for key in ["fighter_name", "status", "result", "detail", "time", "score", "odds", "record"]:
                    fonts[key] = default_font

        return fonts

    def _load_font(
        self,
        element_config: Dict[str, Any],
        default_path: str,
        default_size: int,
    ) -> ImageFont.FreeTypeFont:
        """Load a font from config or use defaults."""
        font_path = element_config.get("font", default_path)
        font_size = element_config.get("font_size", default_size)
        try:
            return ImageFont.truetype(font_path, font_size)
        except (IOError, OSError):
            self.logger.warning(f"Could not load font {font_path}, trying default")
            try:
                return ImageFont.truetype(default_path, default_size)
            except (IOError, OSError):
                return ImageFont.load_default()

    def _get_layout_offset(self, element: str, axis: str, default: int = 0) -> int:
        """Get layout offset for an element from config."""
        layout_config = self.config.get("customization", {}).get("layout", {})
        element_config = layout_config.get(element, {})
        return int(element_config.get(axis, default))

    def _draw_text_with_outline(
        self,
        draw: ImageDraw.Draw,
        text: str,
        position: tuple,
        font,
        fill=(255, 255, 255),
        outline_fill=(0, 0, 0),
    ):
        """Draw text with a black outline for readability."""
        x, y = position
        # Draw outline
        for dx, dy in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
            draw.text((x + dx, y + dy), text, font=font, fill=outline_fill)
        # Draw text
        draw.text((x, y), text, font=font, fill=fill)

    def _load_headshot(
        self,
        fighter_id: str,
        fighter_name: str,
        headshot_path: Path,
        headshot_url: str = None,
    ) -> Optional[Image.Image]:
        """Load and resize a fighter headshot with caching."""
        if fighter_id in self._headshot_cache:
            return self._headshot_cache[fighter_id]

        try:
            if not headshot_path.exists():
                from headshot_downloader import download_missing_headshot
                download_missing_headshot(fighter_id, fighter_name, headshot_path, headshot_url)

            if headshot_path.exists():
                img = Image.open(headshot_path)
                if img.mode != "RGBA":
                    img = img.convert("RGBA")

                # Scale headshot to fit display height
                max_width = int(self.display_width * 1.5)
                max_height = int(self.display_height * 1.5)
                img.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)
                self._headshot_cache[fighter_id] = img
                return img
            else:
                self.logger.error(f"Headshot not found for {fighter_name} at {headshot_path}")
                return None

        except Exception as e:
            self.logger.error(f"Error loading headshot for {fighter_name}: {e}", exc_info=True)
            return None

    def render_fight_card(
        self,
        fight: Dict[str, Any],
        fight_type: str = None,
        display_options: Dict[str, Any] = None,
    ) -> Optional[Image.Image]:
        """
        Render a fight card based on fight type.

        Args:
            fight: Fight data dictionary
            fight_type: 'live', 'recent', or 'upcoming'. Auto-detected if None.
            display_options: Display options (show_records, show_odds, etc.)

        Returns:
            PIL Image of the rendered fight card, or None on error
        """
        if display_options is None:
            display_options = {}

        # Auto-detect fight type
        if fight_type is None:
            if fight.get("is_live"):
                fight_type = "live"
            elif fight.get("is_final"):
                fight_type = "recent"
            else:
                fight_type = "upcoming"

        try:
            if fight_type == "live":
                return self._render_live_fight(fight, display_options)
            elif fight_type == "recent":
                return self._render_recent_fight(fight, display_options)
            else:
                return self._render_upcoming_fight(fight, display_options)
        except Exception as e:
            self.logger.error(f"Error rendering {fight_type} fight card: {e}", exc_info=True)
            return None

    def _render_live_fight(
        self, fight: Dict[str, Any], display_options: Dict[str, Any]
    ) -> Image.Image:
        """Render a live fight card."""
        main_img = Image.new("RGBA", (self.display_width, self.display_height), (0, 0, 0, 255))
        overlay = Image.new("RGBA", (self.display_width, self.display_height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        # Load headshots
        fighter1_img = self._load_headshot(
            fight["fighter1_id"], fight["fighter1_name"],
            fight["fighter1_image_path"], fight.get("fighter1_image_url"),
        )
        fighter2_img = self._load_headshot(
            fight["fighter2_id"], fight["fighter2_name"],
            fight["fighter2_image_path"], fight.get("fighter2_image_url"),
        )

        center_y = self.display_height // 2

        # Draw fighter1 headshot (right side)
        if fighter1_img:
            f1_x = (
                self.display_width - fighter1_img.width + fighter1_img.width // 4 + 2
                + self._get_layout_offset("fighter1_image", "x_offset")
            )
            f1_y = center_y - (fighter1_img.height // 2) + self._get_layout_offset("fighter1_image", "y_offset")
            main_img.paste(fighter1_img, (f1_x, f1_y), fighter1_img)

        # Draw fighter2 headshot (left side)
        if fighter2_img:
            f2_x = -2 - fighter2_img.width // 4 + self._get_layout_offset("fighter2_image", "x_offset")
            f2_y = center_y - (fighter2_img.height // 2) + self._get_layout_offset("fighter2_image", "y_offset")
            main_img.paste(fighter2_img, (f2_x, f2_y), fighter2_img)

        # Round + time (top center)
        status_text = fight.get("status_text", "")
        if status_text:
            status_width = draw.textlength(status_text, font=self.fonts["time"])
            sx = (self.display_width - status_width) // 2 + self._get_layout_offset("status_text", "x_offset")
            sy = 1 + self._get_layout_offset("status_text", "y_offset")
            self._draw_text_with_outline(draw, status_text, (sx, sy), self.fonts["time"])

        # Fight class (center bottom area)
        if display_options.get("show_fight_class", True):
            fight_class = fight.get("fight_class", "")
            if fight_class:
                fc_width = draw.textlength(fight_class, font=self.fonts["detail"])
                fc_x = (self.display_width - fc_width) // 2 + self._get_layout_offset("fight_class", "x_offset")
                fc_y = self.display_height - 8 + self._get_layout_offset("fight_class", "y_offset")
                self._draw_text_with_outline(draw, fight_class, (fc_x, fc_y), self.fonts["detail"])

        # Fighter names
        if display_options.get("show_fighter_names", True):
            self._draw_fighter_names(draw, fight)

        # Records
        if display_options.get("show_records", True):
            self._draw_records(draw, fight)

        # Odds
        if display_options.get("show_odds", True) and fight.get("odds"):
            self._draw_odds(draw, fight["odds"])

        # Composite
        main_img = Image.alpha_composite(main_img, overlay)
        return main_img.convert("RGB")

    def _render_recent_fight(
        self, fight: Dict[str, Any], display_options: Dict[str, Any]
    ) -> Image.Image:
        """Render a recently completed fight card."""
        main_img = Image.new("RGBA", (self.display_width, self.display_height), (0, 0, 0, 255))
        overlay = Image.new("RGBA", (self.display_width, self.display_height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        # Load headshots
        fighter1_img = self._load_headshot(
            fight["fighter1_id"], fight["fighter1_name"],
            fight["fighter1_image_path"], fight.get("fighter1_image_url"),
        )
        fighter2_img = self._load_headshot(
            fight["fighter2_id"], fight["fighter2_name"],
            fight["fighter2_image_path"], fight.get("fighter2_image_url"),
        )

        center_y = self.display_height // 2

        # Draw fighter1 headshot (right side)
        if fighter1_img:
            f1_x = (
                self.display_width - fighter1_img.width + fighter1_img.width // 4 + 2
                + self._get_layout_offset("fighter1_image", "x_offset")
            )
            f1_y = center_y - (fighter1_img.height // 2) + self._get_layout_offset("fighter1_image", "y_offset")
            main_img.paste(fighter1_img, (f1_x, f1_y), fighter1_img)

        # Draw fighter2 headshot (left side)
        if fighter2_img:
            f2_x = -2 - fighter2_img.width // 4 + self._get_layout_offset("fighter2_image", "x_offset")
            f2_y = center_y - (fighter2_img.height // 2) + self._get_layout_offset("fighter2_image", "y_offset")
            main_img.paste(fighter2_img, (f2_x, f2_y), fighter2_img)

        # Status text - "Final" or result method (top center)
        status_text = fight.get("status_text", "Final")
        status_width = draw.textlength(status_text, font=self.fonts["time"])
        sx = (self.display_width - status_width) // 2 + self._get_layout_offset("status_text", "x_offset")
        sy = 1 + self._get_layout_offset("status_text", "y_offset")
        self._draw_text_with_outline(draw, status_text, (sx, sy), self.fonts["time"])

        # Fight class (bottom center)
        if display_options.get("show_fight_class", True):
            fight_class = fight.get("fight_class", "")
            if fight_class:
                fc_width = draw.textlength(fight_class, font=self.fonts["detail"])
                fc_x = (self.display_width - fc_width) // 2 + self._get_layout_offset("fight_class", "x_offset")
                fc_y = self.display_height - 8 + self._get_layout_offset("fight_class", "y_offset")
                self._draw_text_with_outline(draw, fight_class, (fc_x, fc_y), self.fonts["detail"])

        # Records
        if display_options.get("show_records", True):
            self._draw_records(draw, fight)

        # Odds
        if display_options.get("show_odds", True) and fight.get("odds"):
            self._draw_odds(draw, fight["odds"])

        # Composite
        main_img = Image.alpha_composite(main_img, overlay)
        return main_img.convert("RGB")

    def _render_upcoming_fight(
        self, fight: Dict[str, Any], display_options: Dict[str, Any]
    ) -> Image.Image:
        """Render an upcoming fight card."""
        main_img = Image.new("RGBA", (self.display_width, self.display_height), (0, 0, 0, 255))
        overlay = Image.new("RGBA", (self.display_width, self.display_height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        # Load headshots
        fighter1_img = self._load_headshot(
            fight["fighter1_id"], fight["fighter1_name"],
            fight["fighter1_image_path"], fight.get("fighter1_image_url"),
        )
        fighter2_img = self._load_headshot(
            fight["fighter2_id"], fight["fighter2_name"],
            fight["fighter2_image_path"], fight.get("fighter2_image_url"),
        )

        center_y = self.display_height // 2

        # Draw fighter1 headshot (right side)
        if fighter1_img:
            f1_x = (
                self.display_width - fighter1_img.width + fighter1_img.width // 4 + 2
                + self._get_layout_offset("fighter1_image", "x_offset")
            )
            f1_y = center_y - (fighter1_img.height // 2) + self._get_layout_offset("fighter1_image", "y_offset")
            main_img.paste(fighter1_img, (f1_x, f1_y), fighter1_img)

        # Draw fighter2 headshot (left side)
        if fighter2_img:
            f2_x = -2 - fighter2_img.width // 4 + self._get_layout_offset("fighter2_image", "x_offset")
            f2_y = center_y - (fighter2_img.height // 2) + self._get_layout_offset("fighter2_image", "y_offset")
            main_img.paste(fighter2_img, (f2_x, f2_y), fighter2_img)

        # Fighter names near headshots
        if display_options.get("show_fighter_names", True):
            self._draw_fighter_names(draw, fight)

        # Date + Time (center)
        game_date = fight.get("game_date", "")
        game_time = fight.get("game_time", "")
        if game_date:
            date_width = draw.textlength(game_date, font=self.fonts["detail"])
            dx = (self.display_width - date_width) // 2 + self._get_layout_offset("date", "x_offset")
            dy = 1 + self._get_layout_offset("date", "y_offset")
            self._draw_text_with_outline(draw, game_date, (dx, dy), self.fonts["detail"])

        if game_time:
            time_width = draw.textlength(game_time, font=self.fonts["detail"])
            tx = (self.display_width - time_width) // 2 + self._get_layout_offset("time", "x_offset")
            ty = 8 + self._get_layout_offset("time", "y_offset")
            self._draw_text_with_outline(draw, game_time, (tx, ty), self.fonts["detail"])

        # Fight class
        if display_options.get("show_fight_class", True):
            fight_class = fight.get("fight_class", "")
            if fight_class:
                fc_width = draw.textlength(fight_class, font=self.fonts["detail"])
                fc_x = (self.display_width - fc_width) // 2 + self._get_layout_offset("fight_class", "x_offset")
                fc_y = self.display_height - 8 + self._get_layout_offset("fight_class", "y_offset")
                self._draw_text_with_outline(draw, fight_class, (fc_x, fc_y), self.fonts["detail"])

        # Records
        if display_options.get("show_records", True):
            self._draw_records(draw, fight)

        # Odds
        if display_options.get("show_odds", True) and fight.get("odds"):
            self._draw_odds(draw, fight["odds"])

        # Composite
        main_img = Image.alpha_composite(main_img, overlay)
        return main_img.convert("RGB")

    def _draw_fighter_names(self, draw: ImageDraw.Draw, fight: Dict[str, Any]):
        """Draw fighter short names near their headshots."""
        name_font = self.fonts["fighter_name"]
        y_offset = self._get_layout_offset("fighter_names", "y_offset")

        # Fighter2 name (left side)
        f2_name = fight.get("fighter2_name_short", "")
        if f2_name:
            f2_x = 1 + self._get_layout_offset("fighter_names", "fighter2_x_offset")
            f2_y = 1 + y_offset
            self._draw_text_with_outline(draw, f2_name, (f2_x, f2_y), name_font)

        # Fighter1 name (right side)
        f1_name = fight.get("fighter1_name_short", "")
        if f1_name:
            f1_width = draw.textlength(f1_name, font=name_font)
            f1_x = self.display_width - f1_width - 1 + self._get_layout_offset("fighter_names", "fighter1_x_offset")
            f1_y = 1 + y_offset
            self._draw_text_with_outline(draw, f1_name, (f1_x, f1_y), name_font)

    def _draw_records(self, draw: ImageDraw.Draw, fight: Dict[str, Any]):
        """Draw fighter records at the bottom of the card."""
        record_font = self.fonts["record"]
        y_offset = self._get_layout_offset("records", "y_offset")

        record_bbox = draw.textbbox((0, 0), "0-0-0", font=record_font)
        record_height = record_bbox[3] - record_bbox[1]
        record_y = self.display_height - record_height + y_offset

        # Fighter2 record (left side)
        f2_record = fight.get("fighter2_record", "")
        if f2_record:
            f2_x = 0 + self._get_layout_offset("records", "fighter2_x_offset")
            self._draw_text_with_outline(draw, f2_record, (f2_x, record_y), record_font)

        # Fighter1 record (right side)
        f1_record = fight.get("fighter1_record", "")
        if f1_record:
            f1_bbox = draw.textbbox((0, 0), f1_record, font=record_font)
            f1_width = f1_bbox[2] - f1_bbox[0]
            f1_x = self.display_width - f1_width + self._get_layout_offset("records", "fighter1_x_offset")
            self._draw_text_with_outline(draw, f1_record, (f1_x, record_y), record_font)

    def _draw_odds(self, draw: ImageDraw.Draw, odds: Dict[str, Any]):
        """Draw betting odds dynamically positioned."""
        odds_font = self.fonts["odds"]
        x_offset = self._get_layout_offset("odds", "x_offset")
        y_offset = self._get_layout_offset("odds", "y_offset")

        # Get moneyline odds
        home_ml = odds.get("home_team_odds", {}).get("money_line")
        away_ml = odds.get("away_team_odds", {}).get("money_line")

        if home_ml is not None and away_ml is not None:
            # Determine favored fighter
            home_favored = home_ml < 0 if isinstance(home_ml, (int, float)) else False

            # Format odds text
            fav_ml = home_ml if home_favored else away_ml
            fav_text = f"{int(fav_ml):+d}" if isinstance(fav_ml, (int, float)) else str(fav_ml)

            # Position on the favored side
            if home_favored:
                # Fighter1 (right/home) is favored - draw on right
                text_width = draw.textlength(fav_text, font=odds_font)
                ox = self.display_width - text_width - 1 + x_offset
            else:
                # Fighter2 (left/away) is favored - draw on left
                ox = 1 + x_offset

            oy = self.display_height // 2 - 3 + y_offset
            self._draw_text_with_outline(draw, fav_text, (ox, oy), odds_font)

        elif home_ml is not None:
            ml_text = f"{int(home_ml):+d}" if isinstance(home_ml, (int, float)) else str(home_ml)
            text_width = draw.textlength(ml_text, font=odds_font)
            ox = self.display_width - text_width - 1 + x_offset
            oy = self.display_height // 2 - 3 + y_offset
            self._draw_text_with_outline(draw, ml_text, (ox, oy), odds_font)

        elif away_ml is not None:
            ml_text = f"{int(away_ml):+d}" if isinstance(away_ml, (int, float)) else str(away_ml)
            ox = 1 + x_offset
            oy = self.display_height // 2 - 3 + y_offset
            self._draw_text_with_outline(draw, ml_text, (ox, oy), odds_font)
