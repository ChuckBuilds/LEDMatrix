"""
Baseball Base Classes

This module provides baseball-specific base classes that extend the core sports functionality
with baseball-specific logic for innings, outs, bases, strikes, balls, etc.
"""

import logging
import time
from typing import Any, Dict, Optional

from PIL import Image, ImageDraw, ImageFont

from src.base_classes.data_sources import ESPNDataSource
from src.base_classes.sports import SportsCore, SportsLive, SportsRecent


class Baseball(SportsCore):
    """Base class for baseball sports with common functionality."""

    def __init__(
        self,
        config: Dict[str, Any],
        display_manager,
        cache_manager,
        logger: logging.Logger,
        sport_key: str,
    ):
        super().__init__(config, display_manager, cache_manager, logger, sport_key)
        # Baseball-specific configuration
        self.show_innings = self.mode_config.get("show_innings", True)
        self.show_outs = self.mode_config.get("show_outs", True)
        self.show_bases = self.mode_config.get("show_bases", True)
        self.show_count = self.mode_config.get("show_count", True)
        self.show_pitcher_batter = self.mode_config.get("show_pitcher_batter", False)
        self.show_series_summary = self.mode_config.get("show_series_summary", False)
        self.data_source = ESPNDataSource(logger)
        self.sport = "baseball"

    def _get_baseball_display_text(self, game: Dict) -> str:
        """Get baseball-specific display text."""
        try:
            display_parts = []

            # Inning information
            if self.show_innings:
                inning = game.get("inning", "")
                if inning:
                    display_parts.append(f"Inning: {inning}")

            # Outs information
            if self.show_outs:
                outs = game.get("outs", 0)
                if outs is not None:
                    display_parts.append(f"Outs: {outs}")

            # Bases information
            if self.show_bases:
                bases = game.get("bases", "")
                if bases:
                    display_parts.append(f"Bases: {bases}")

            # Count information
            if self.show_count:
                strikes = game.get("strikes", 0)
                balls = game.get("balls", 0)
                if strikes is not None and balls is not None:
                    display_parts.append(f"Count: {balls}-{strikes}")

            # Pitcher/Batter information
            if self.show_pitcher_batter:
                pitcher = game.get("pitcher", "")
                batter = game.get("batter", "")
                if pitcher:
                    display_parts.append(f"Pitcher: {pitcher}")
                if batter:
                    display_parts.append(f"Batter: {batter}")

            return " | ".join(display_parts) if display_parts else ""

        except Exception as e:
            self.logger.error(f"Error getting baseball display text: {e}")
            return ""

    def _is_baseball_game_live(self, game: Dict) -> bool:
        """Check if a baseball game is currently live."""
        try:
            # Check if game is marked as live
            is_live = game.get("is_live", False)
            if is_live:
                return True

            # Check inning to determine if game is active
            inning = game.get("inning", "")
            if inning and inning != "Final":
                return True

            return False

        except Exception as e:
            self.logger.error(f"Error checking if baseball game is live: {e}")
            return False

    def _get_baseball_game_status(self, game: Dict) -> str:
        """Get baseball-specific game status."""
        try:
            status = game.get("status_text", "")
            inning = game.get("inning", "")

            if self._is_baseball_game_live(game):
                if inning:
                    return f"Live - {inning}"
                else:
                    return "Live"
            elif game.get("is_final", False):
                return "Final"
            elif game.get("is_upcoming", False):
                return "Upcoming"
            else:
                return status

        except Exception as e:
            self.logger.error(f"Error getting baseball game status: {e}")
            return ""

    def _extract_game_details(self, game_event: Dict) -> Optional[Dict]:
        """Extract relevant game details from ESPN NCAA FB API response."""
        details, home_team, away_team, status, situation = (
            self._extract_game_details_common(game_event)
        )
        if details is None or home_team is None or away_team is None or status is None:
            return
        try:
            # print(status["type"]["state"])
            # exit()
            game_status = status["type"]["name"].lower()
            status_state = status["type"]["state"].lower()
            # Get team abbreviations
            home_abbr = home_team["team"]["abbreviation"]
            away_abbr = away_team["team"]["abbreviation"]

            # Check if this is a favorite team game
            is_favorite_game = (
                home_abbr in self.favorite_teams or away_abbr in self.favorite_teams
            )

            # Log all teams found for debugging
            self.logger.debug(
                f"Found game: {away_abbr} @ {home_abbr} (Status: {game_status}, State: {status_state})"
            )

            # Only log detailed information for favorite teams
            if is_favorite_game:
                self.logger.debug(f"Full status data: {game_event['status']}")
                self.logger.debug(f"Status type: {game_status}, State: {status_state}")
                self.logger.debug(f"Status detail: {status['type'].get('detail', '')}")
                self.logger.debug(
                    f"Status shortDetail: {status['type'].get('shortDetail', '')}"
                )
            series = game_event["competitions"][0].get("series", None)
            series_summary = ""
            if series:
                # Log the full series object structure for debugging
                self.logger.debug(f"Series object found: {series}")
                series_summary = series.get("summary", "")
            # Get game state information
            if status_state == "in":
                # For live games, get detailed state
                inning = game_event["status"].get(
                    "period", 1
                )  # Get inning from status period

                # Get inning information from status
                status_detail = status["type"].get("detail", "").lower()
                status_short = status["type"].get("shortDetail", "").lower()

                if is_favorite_game:
                    self.logger.debug(
                        f"Raw status detail: {status['type'].get('detail')}"
                    )
                    self.logger.debug(
                        f"Raw status short: {status['type'].get('shortDetail')}"
                    )

                # Determine inning half from status information
                inning_half = "top"  # Default

                # Handle end of inning: next inning is top
                if "end" in status_detail or "end" in status_short:
                    inning_half = "top"
                    inning = (
                        game_event["status"].get("period", 1) + 1
                    )  # Use period and increment for next inning
                    if is_favorite_game:
                        self.logger.debug(
                            f"Detected end of inning. Setting to Top {inning}"
                        )
                # Handle middle of inning: next is bottom of current inning
                elif "mid" in status_detail or "mid" in status_short:
                    inning_half = "bottom"
                    if is_favorite_game:
                        self.logger.debug(
                            f"Detected middle of inning. Setting to Bottom {inning}"
                        )
                # Handle bottom of inning
                elif (
                    "bottom" in status_detail
                    or "bot" in status_detail
                    or "bottom" in status_short
                    or "bot" in status_short
                ):
                    inning_half = "bottom"
                    if is_favorite_game:
                        self.logger.debug(f"Detected bottom of inning: {inning}")
                # Handle top of inning
                elif "top" in status_detail or "top" in status_short:
                    inning_half = "top"
                    if is_favorite_game:
                        self.logger.debug(f"Detected top of inning: {inning}")

                if is_favorite_game:
                    self.logger.debug(f"Status detail: {status_detail}")
                    self.logger.debug(f"Status short: {status_short}")
                    self.logger.debug(f"Determined inning: {inning_half} {inning}")

                # Get count and bases from situation
                situation = game_event["competitions"][0].get("situation", {})

                if is_favorite_game:
                    self.logger.debug(f"Full situation data: {situation}")

                # Get count from the correct location in the API response
                count = situation.get("count", {})
                balls = count.get("balls", 0)
                strikes = count.get("strikes", 0)
                outs = situation.get("outs", 0)

                # Add detailed logging for favorite team games
                if is_favorite_game:
                    self.logger.debug(f"Full situation data: {situation}")
                    self.logger.debug(f"Count object: {count}")
                    self.logger.debug(
                        f"Raw count values - balls: {balls}, strikes: {strikes}"
                    )
                    self.logger.debug(f"Raw outs value: {outs}")

                # Try alternative locations for count data
                if balls == 0 and strikes == 0:
                    # First try the summary field
                    if "summary" in situation:
                        try:
                            count_summary = situation["summary"]
                            balls, strikes = map(int, count_summary.split("-"))
                            if is_favorite_game:
                                self.logger.debug(
                                    f"Using summary count: {count_summary}"
                                )
                        except (ValueError, AttributeError):
                            if is_favorite_game:
                                self.logger.debug("Could not parse summary count")
                    else:
                        # Check if count is directly in situation
                        balls = situation.get("balls", 0)
                        strikes = situation.get("strikes", 0)
                        if is_favorite_game:
                            self.logger.debug(
                                f"Using direct situation count: balls={balls}, strikes={strikes}"
                            )
                            self.logger.debug(
                                f"Full situation keys: {list(situation.keys())}"
                            )

                if is_favorite_game:
                    self.logger.debug(f"Final count: balls={balls}, strikes={strikes}")

                # Get base runners
                bases_occupied = [
                    situation.get("onFirst", False),
                    situation.get("onSecond", False),
                    situation.get("onThird", False),
                ]

                if is_favorite_game:
                    self.logger.debug(f"Bases occupied: {bases_occupied}")
            else:
                # Default values for non-live games
                inning = 1
                inning_half = "top"
                balls = 0
                strikes = 0
                outs = 0
                bases_occupied = [False, False, False]

            details.update(
                {
                    "status": game_status,
                    "status_state": status_state,
                    "inning": inning,
                    "inning_half": inning_half,
                    "balls": balls,
                    "strikes": strikes,
                    "outs": outs,
                    "bases_occupied": bases_occupied,
                    "start_time": game_event["date"],
                    "series_summary": series_summary,
                }
            )

            # Basic validation (can be expanded)
            if not details["home_abbr"] or not details["away_abbr"]:
                self.logger.warning(
                    f"Missing team abbreviation in event: {details['id']}"
                )
                return None

            self.logger.debug(
                f"Extracted: {details['away_abbr']}@{details['home_abbr']}, Status: {status['type']['name']}, Live: {details['is_live']}, Final: {details['is_final']}, Upcoming: {details['is_upcoming']}"
            )

            return details
        except Exception as e:
            # Log the problematic event structure if possible
            self.logger.error(
                f"Error extracting game details: {e} from event: {game_event.get('id')}",
                exc_info=True,
            )
            return None

    def display_series_summary(self, game: dict, draw_overlay: ImageDraw.ImageDraw):
        if not self.show_series_summary:
            return
        
        series_summary = game.get("series_summary", "")
        if not series_summary:
            return
            
        # Use small font for series summary
        try:
            font = ImageFont.truetype("assets/fonts/4x6-font.ttf", 6)
        except IOError:
            font = ImageFont.load_default()
            
        # Calculate positioning
        bbox = draw_overlay.textbbox((0, 0), series_summary, font=font)
        height = bbox[3] - bbox[1]
        width = draw_overlay.textlength(series_summary, font=font)
        
        # Position at bottom center
        x = (self.display_width - width) // 2
        y = self.display_height - height - 1  # 1 pixel from bottom
        
        # Draw with yellow color and black outline
        self._draw_text_with_outline(
            draw_overlay, series_summary, (x, y), font, fill=(255, 255, 0)
        )

class BaseballRecent(Baseball, SportsRecent):
    """Base class for recent baseball games."""

    def __init__(
        self,
        config: Dict[str, Any],
        display_manager,
        cache_manager,
        logger: logging.Logger,
        sport_key: str,
    ):
        super().__init__(config, display_manager, cache_manager, logger, sport_key)


    def _custom_scorebug_layout(self, game: dict, draw_overlay: ImageDraw.ImageDraw):
        self.display_series_summary(game, draw_overlay)


class BaseballLive(Baseball, SportsLive):
    """Base class for live baseball games."""

    def __init__(
        self,
        config: Dict[str, Any],
        display_manager,
        cache_manager,
        logger: logging.Logger,
        sport_key: str,
    ):
        super().__init__(config, display_manager, cache_manager, logger, sport_key)

    def _test_mode_update(self):
        if self.current_game and self.current_game["is_live"]:
            # self.current_game["bases_occupied"] = [
            #     random.choice([True, False]) for _ in range(3)
            # ]
            # self.current_game["balls"] = random.choice([1, 2, 3])
            # self.current_game["strikes"] = random.choice([1, 2])
            # self.current_game["outs"] = random.choice([1, 2])
            if self.current_game["inning_half"] == "top":
                self.current_game["inning_half"] = "bottom"
            else:
                self.current_game["inning_half"] = "top"
                self.current_game["inning"] += 1
            self.current_game["balls"] = (self.current_game["balls"] + 1) % 4
            self.current_game["strikes"] = (self.current_game["strikes"] + 1) % 3
            self.current_game["outs"] = (self.current_game["outs"] + 1) % 3
            self.current_game["bases_occupied"] = [
                not b for b in self.current_game["bases_occupied"]
            ]
            if self.current_game["inning"] % 2 == 0:
                self.current_game["home_score"] = str(
                    int(self.current_game["home_score"]) + 1
                )
            else:
                self.current_game["away_score"] = str(
                    int(self.current_game["away_score"]) + 1
                )

    def _draw_scorebug_layout(self, game: Dict, force_clear: bool = False) -> None:
        """Draw the detailed scorebug layout for a live NCAA FB game."""  # Updated docstring
        try:
            main_img = Image.new(
                "RGBA", (self.display_width, self.display_height), (0, 0, 0, 255)
            )
            overlay = Image.new(
                "RGBA", (self.display_width, self.display_height), (0, 0, 0, 0)
            )
            draw_overlay = ImageDraw.Draw(
                overlay
            )  # Draw text elements on overlay first

            home_logo = self._load_and_resize_logo(
                game["home_id"],
                game["home_abbr"],
                game["home_logo_path"],
                game.get("home_logo_url"),
            )
            away_logo = self._load_and_resize_logo(
                game["away_id"],
                game["away_abbr"],
                game["away_logo_path"],
                game.get("away_logo_url"),
            )

            if not home_logo or not away_logo:
                self.logger.error(
                    f"Failed to load logos for live game: {game.get('id')}"
                )  # Changed log prefix
                # Draw placeholder text if logos fail
                draw_final = ImageDraw.Draw(main_img.convert("RGB"))
                self._draw_text_with_outline(
                    draw_final, "Logo Error", (5, 5), self.fonts["status"]
                )
                self.display_manager.image.paste(main_img.convert("RGB"), (0, 0))
                self.display_manager.update_display()
                return

            center_y = self.display_height // 2

            # Draw logos (shifted slightly more inward than NHL perhaps)
            home_x = (
                self.display_width - home_logo.width + 10
            )  # adjusted from 18 # Adjust position as needed
            home_y = center_y - (home_logo.height // 2)
            main_img.paste(home_logo, (home_x, home_y), home_logo)

            away_x = -10  # adjusted from 18 # Adjust position as needed
            away_y = center_y - (away_logo.height // 2)
            main_img.paste(away_logo, (away_x, away_y), away_logo)

            # --- Live Game Specific Elements ---

            # Define default text color
            text_color = (255, 255, 255)

            # Draw Inning (Top Center)
            inning_half = game["inning_half"]
            inning_num = game["inning"]
            if game["is_final"]:
                inning_text = "FINAL"
            else:
                inning_half_indicator = (
                    "▲" if game["inning_half"].lower() == "top" else "▼"
                )
                inning_num = game["inning"]
                inning_text = f"{inning_half_indicator}{inning_num}"

            inning_bbox = draw_overlay.textbbox(
                (0, 0), inning_text, font=self.display_manager.font
            )
            inning_width = inning_bbox[2] - inning_bbox[0]
            inning_x = (self.display_width - inning_width) // 2
            inning_y = 1  # Position near top center
            # draw_overlay.text((inning_x, inning_y), inning_text, fill=(255, 255, 255), font=self.display_manager.font)
            self._draw_text_with_outline(
                draw_overlay,
                inning_text,
                (inning_x, inning_y),
                self.display_manager.font,
            )

            # --- REVISED BASES AND OUTS DRAWING ---
            bases_occupied = game["bases_occupied"]  # [1st, 2nd, 3rd]
            outs = game.get("outs", 0)
            inning_half = game["inning_half"]

            # Define geometry
            base_diamond_size = 7
            out_circle_diameter = 3
            out_vertical_spacing = 2  # Space between out circles
            spacing_between_bases_outs = (
                3  # Horizontal space between base cluster and out column
            )
            base_vert_spacing = 1  # Internal vertical space in base cluster
            base_horiz_spacing = 1  # Internal horizontal space in base cluster

            # Calculate cluster dimensions
            base_cluster_height = (
                base_diamond_size + base_vert_spacing + base_diamond_size
            )
            base_cluster_width = (
                base_diamond_size + base_horiz_spacing + base_diamond_size
            )
            out_cluster_height = 3 * out_circle_diameter + 2 * out_vertical_spacing
            out_cluster_width = out_circle_diameter

            # Calculate overall start positions
            overall_start_y = (
                inning_bbox[3] + 0
            )  # Start immediately below inning text (moved up 3 pixels)

            # Center the BASE cluster horizontally
            bases_origin_x = (self.display_width - base_cluster_width) // 2

            # Determine relative positions for outs based on inning half
            if inning_half == "top":  # Away batting, outs on left
                outs_column_x = (
                    bases_origin_x - spacing_between_bases_outs - out_cluster_width
                )
            else:  # Home batting, outs on right
                outs_column_x = (
                    bases_origin_x + base_cluster_width + spacing_between_bases_outs
                )

            # Calculate vertical alignment offset for outs column (center align with bases cluster)
            outs_column_start_y = (
                overall_start_y + (base_cluster_height // 2) - (out_cluster_height // 2)
            )

            # --- Draw Bases (Diamonds) ---
            base_color_occupied = (255, 255, 255)
            base_color_empty = (255, 255, 255)  # Outline color
            h_d = base_diamond_size // 2

            # 2nd Base (Top center relative to bases_origin_x)
            c2x = bases_origin_x + base_cluster_width // 2
            c2y = overall_start_y + h_d
            poly2 = [
                (c2x, overall_start_y),
                (c2x + h_d, c2y),
                (c2x, c2y + h_d),
                (c2x - h_d, c2y),
            ]
            if bases_occupied[1]:
                draw_overlay.polygon(poly2, fill=base_color_occupied)
            else:
                draw_overlay.polygon(poly2, outline=base_color_empty)

            base_bottom_y = c2y + h_d  # Bottom Y of 2nd base diamond

            # 3rd Base (Bottom left relative to bases_origin_x)
            c3x = bases_origin_x + h_d
            c3y = base_bottom_y + base_vert_spacing + h_d
            poly3 = [
                (c3x, base_bottom_y + base_vert_spacing),
                (c3x + h_d, c3y),
                (c3x, c3y + h_d),
                (c3x - h_d, c3y),
            ]
            if bases_occupied[2]:
                draw_overlay.polygon(poly3, fill=base_color_occupied)
            else:
                draw_overlay.polygon(poly3, outline=base_color_empty)

            # 1st Base (Bottom right relative to bases_origin_x)
            c1x = bases_origin_x + base_cluster_width - h_d
            c1y = base_bottom_y + base_vert_spacing + h_d
            poly1 = [
                (c1x, base_bottom_y + base_vert_spacing),
                (c1x + h_d, c1y),
                (c1x, c1y + h_d),
                (c1x - h_d, c1y),
            ]
            if bases_occupied[0]:
                draw_overlay.polygon(poly1, fill=base_color_occupied)
            else:
                draw_overlay.polygon(poly1, outline=base_color_empty)

            # --- Draw Outs (Vertical Circles) ---
            circle_color_out = (255, 255, 255)
            circle_color_empty_outline = (100, 100, 100)

            for i in range(3):
                cx = outs_column_x
                cy = outs_column_start_y + i * (
                    out_circle_diameter + out_vertical_spacing
                )
                coords = [cx, cy, cx + out_circle_diameter, cy + out_circle_diameter]
                if i < outs:
                    draw_overlay.ellipse(coords, fill=circle_color_out)
                else:
                    draw_overlay.ellipse(coords, outline=circle_color_empty_outline)

            # --- Draw Balls-Strikes Count (BDF Font) ---
            balls = game.get("balls", 0)
            strikes = game.get("strikes", 0)

            # Add debug logging for count with cooldown
            current_time = time.time()
            if (
                game["home_abbr"] in self.favorite_teams
                or game["away_abbr"] in self.favorite_teams
            ) and current_time - self.last_count_log_time >= self.count_log_interval:
                self.logger.debug(f"Displaying count: {balls}-{strikes}")
                self.logger.debug(
                    f"Raw count data: balls={game.get('balls')}, strikes={game.get('strikes')}"
                )
                self.last_count_log_time = current_time

            count_text = f"{balls}-{strikes}"
            bdf_font = self.display_manager.calendar_font
            bdf_font.set_char_size(height=7 * 64)  # Set 7px height
            count_text_width = self.display_manager.get_text_width(count_text, bdf_font)

            # Position below the base/out cluster
            cluster_bottom_y = (
                overall_start_y + base_cluster_height
            )  # Find the bottom of the taller part (bases)
            count_y = cluster_bottom_y + 2  # Start 2 pixels below cluster

            # Center horizontally within the BASE cluster width
            count_x = bases_origin_x + (base_cluster_width - count_text_width) // 2

            # Ensure draw object is set and draw text
            self.display_manager.draw = draw_overlay
            # self.display_manager._draw_bdf_text(count_text, count_x, count_y, text_color, font=bdf_font)
            # Use _draw_text_with_outline for count text
            # self._draw_text_with_outline(draw, count_text, (count_x, count_y), bdf_font, fill=text_color)

            # Draw Balls-Strikes Count with outline using BDF font
            # Define outline color (consistent with _draw_text_with_outline default)
            outline_color_for_bdf = (0, 0, 0)

            # Draw outline
            for dx_offset, dy_offset in [
                (-1, -1),
                (-1, 0),
                (-1, 1),
                (0, -1),
                (0, 1),
                (1, -1),
                (1, 0),
                (1, 1),
            ]:
                self.display_manager._draw_bdf_text(
                    count_text,
                    count_x + dx_offset,
                    count_y + dy_offset,
                    color=outline_color_for_bdf,
                    font=bdf_font,
                )

            # Draw main text
            self.display_manager._draw_bdf_text(
                count_text, count_x, count_y, color=text_color, font=bdf_font
            )

            # Draw Team:Score at the bottom (matching main branch format)
            score_font = self.display_manager.font  # Use PressStart2P
            outline_color = (0, 0, 0)
            score_text_color = (
                255,
                255,
                255,
            )  # Use a specific name for score text color

            # Helper function for outlined text
            def draw_bottom_outlined_text(x, y, text):
                self._draw_text_with_outline(
                    draw_overlay,
                    text,
                    (x, y),
                    score_font,
                    fill=score_text_color,
                    outline_color=outline_color,
                )

            away_abbr = game["away_abbr"]
            home_abbr = game["home_abbr"]
            away_score_str = str(game["away_score"])
            home_score_str = str(game["home_score"])

            away_text = f"{away_abbr}:{away_score_str}"
            home_text = f"{home_abbr}:{home_score_str}"

            # Calculate Y position (bottom edge)
            # Get font height (approximate or precise)
            try:
                font_height = score_font.getbbox("A")[3] - score_font.getbbox("A")[1]
            except AttributeError:
                font_height = 8  # Fallback for default font
            score_y = (
                self.display_height - font_height - 2
            )  # 2 pixels padding from bottom

            # Away Team:Score (Bottom Left)
            away_score_x = 2  # 2 pixels padding from left
            draw_bottom_outlined_text(away_score_x, score_y, away_text)

            # Home Team:Score (Bottom Right)
            home_text_bbox = draw_overlay.textbbox((0, 0), home_text, font=score_font)
            home_text_width = home_text_bbox[2] - home_text_bbox[0]
            home_score_x = (
                self.display_width - home_text_width - 2
            )  # 2 pixels padding from right
            draw_bottom_outlined_text(home_score_x, score_y, home_text)

            # Draw gambling odds if available
            if "odds" in game and game["odds"]:
                self._draw_dynamic_odds(
                    draw_overlay, game["odds"], self.display_width, self.display_height
                )

            # Composite the text overlay onto the main image
            main_img = Image.alpha_composite(main_img, overlay)
            main_img = main_img.convert("RGB")  # Convert for display

            # Display the final image
            self.display_manager.image.paste(main_img, (0, 0))
            self.display_manager.update_display()  # Update display here for live

        except Exception as e:
            self.logger.error(
                f"Error displaying live Baseball game: {e}", exc_info=True
            )  # Changed log prefix
