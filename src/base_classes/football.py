from typing import Dict, Any, Optional, List
from src.display_manager import DisplayManager
from src.cache_manager import CacheManager
from datetime import datetime, timezone, timedelta
import logging
from PIL import Image, ImageDraw, ImageFont
import time
from src.base_classes.data_sources import ESPNDataSource
from src.base_classes.sports import SportsCore, SportsLive

class Football(SportsCore):
    """Base class for football sports with common functionality."""
    
    def __init__(self, config: Dict[str, Any], display_manager: DisplayManager, cache_manager: CacheManager, logger: logging.Logger, sport_key: str):
        super().__init__(config, display_manager, cache_manager, logger, sport_key)
        self.data_source = ESPNDataSource(logger)
        self.sport = "football"

    def _extract_game_details(self, game_event: Dict) -> Optional[Dict]:
        """Extract relevant game details from ESPN NCAA FB API response."""
        details, home_team, away_team, status, situation = self._extract_game_details_common(game_event)
        if details is None or home_team is None or away_team is None or status is None:
            return
        try:
            competition = game_event["competitions"][0]
            status = competition["status"]

            # --- Football Specific Details (Likely same for NFL/NCAAFB) ---
            down_distance_text = ""
            down_distance_text_long = ""
            possession_indicator = None # Default to None
            scoring_event = ""  # Track scoring events
            home_timeouts = 0
            away_timeouts = 0
            is_redzone = False
            posession = None

            if situation and status["type"]["state"] == "in":
                # down = situation.get("down")
                down_distance_text = situation.get("shortDownDistanceText")
                down_distance_text_long = situation.get("downDistanceText")
                # distance = situation.get("distance")
                
                # Detect scoring events from status detail
                status_detail = status["type"].get("detail", "").lower()
                status_short = status["type"].get("shortDetail", "").lower()
                is_redzone = situation.get("isRedZone")
                posession = situation.get("possession")
                
                # Check for scoring events in status text
                if any(keyword in status_detail for keyword in ["touchdown", "td"]):
                    scoring_event = "TOUCHDOWN"
                elif any(keyword in status_detail for keyword in ["field goal", "fg"]):
                    scoring_event = "FIELD GOAL"
                elif any(keyword in status_detail for keyword in ["extra point", "pat", "point after"]):
                    scoring_event = "PAT"
                elif any(keyword in status_short for keyword in ["touchdown", "td"]):
                    scoring_event = "TOUCHDOWN"
                elif any(keyword in status_short for keyword in ["field goal", "fg"]):
                    scoring_event = "FIELD GOAL"
                elif any(keyword in status_short for keyword in ["extra point", "pat"]):
                    scoring_event = "PAT"

                # Determine possession based on team ID
                possession_team_id = situation.get("possession")
                if possession_team_id:
                    if possession_team_id == home_team.get("id"):
                        possession_indicator = "home"
                    elif possession_team_id == away_team.get("id"):
                        possession_indicator = "away"

                home_timeouts = situation.get("homeTimeouts", 3) # Default to 3 if not specified
                away_timeouts = situation.get("awayTimeouts", 3) # Default to 3 if not specified


            # Format period/quarter
            period = status.get("period", 0)
            period_text = ""
            if status["type"]["state"] == "in":
                if period == 0:
                    period_text = "Start" # Before kickoff
                elif period >= 1 and period <= 4:
                    period_text = f"Q{period}" # OT starts after Q4
                elif period > 4:
                    period_text = f"OT{period - 4}" # OT starts after Q4
            elif status["type"]["state"] == "halftime" or status["type"]["name"] == "STATUS_HALFTIME": # Check explicit halftime state
                period_text = "HALF"
            elif status["type"]["state"] == "post":
                 if period > 4 : period_text = "Final/OT"
                 else: period_text = "Final"
            elif status["type"]["state"] == "pre":
                period_text = details.get("game_time", "") # Show time for upcoming

            details.update({
                "period": period,
                "period_text": period_text, # Formatted quarter/status
                "clock": status.get("displayClock", "0:00"),
                "home_timeouts": home_timeouts,
                "away_timeouts": away_timeouts,
                "down_distance_text": down_distance_text, # Added Down/Distance
                "down_distance_text_long": down_distance_text_long,
                "is_redzone": is_redzone,
                "possession": posession, # ID of team with possession
                "possession_indicator": possession_indicator, # Added for easy home/away check
                "scoring_event": scoring_event, # Track scoring events (TOUCHDOWN, FIELD GOAL, PAT)
            })

            # Basic validation (can be expanded)
            if not details['home_abbr'] or not details['away_abbr']:
                 self.logger.warning(f"Missing team abbreviation in event: {details['id']}")
                 return None

            self.logger.debug(f"Extracted: {details['away_abbr']}@{details['home_abbr']}, Status: {status['type']['name']}, Live: {details['is_live']}, Final: {details['is_final']}, Upcoming: {details['is_upcoming']}")

            return details
        except Exception as e:
            # Log the problematic event structure if possible
            logging.error(f"Error extracting game details: {e} from event: {game_event.get('id')}", exc_info=True)
            return None

class FootballLive(Football, SportsLive):
    def __init__(self, config: Dict[str, Any], display_manager: DisplayManager, cache_manager: CacheManager, logger: logging.Logger, sport_key: str):
        super().__init__(config, display_manager, cache_manager, logger, sport_key)

    def _test_mode_update(self):
        if self.current_game and self.current_game["is_live"]:
            try:
                minutes, seconds = map(int, self.current_game["clock"].split(':'))
                seconds -= 1
                if seconds < 0:
                    seconds = 59
                    minutes -= 1
                    if minutes < 0:
                        # Simulate end of quarter/game
                        if self.current_game["period"] < 4: # Q4 is period 4
                            self.current_game["period"] += 1
                            # Update period_text based on new period
                            if self.current_game["period"] == 1: self.current_game["period_text"] = "Q1"
                            elif self.current_game["period"] == 2: self.current_game["period_text"] = "Q2"
                            elif self.current_game["period"] == 3: self.current_game["period_text"] = "Q3"
                            elif self.current_game["period"] == 4: self.current_game["period_text"] = "Q4"
                            # Reset clock for next quarter (e.g., 15:00)
                            minutes, seconds = 15, 0
                        else:
                            # Simulate game end
                            self.current_game["is_live"] = False
                            self.current_game["is_final"] = True
                            self.current_game["period_text"] = "Final"
                            minutes, seconds = 0, 0
                self.current_game["clock"] = f"{minutes:02d}:{seconds:02d}"
                # Simulate down change occasionally
                if seconds % 15 == 0:
                        self.current_game["down_distance_text"] = f"{['1st','2nd','3rd','4th'][seconds % 4]} & {seconds % 10 + 1}"
                self.current_game["status_text"] = f"{self.current_game['period_text']} {self.current_game['clock']}"

                # Display update handled by main loop or explicit call if needed immediately
                # self.display(force_clear=True) # Only if immediate update is desired here

            except ValueError:
                self.logger.warning("Test mode: Could not parse clock") # Changed log prefix
        # No actual display call here, let main loop handle it
        

    def _draw_scorebug_layout(self, game: Dict, force_clear: bool = False) -> None:
        """Draw the detailed scorebug layout for a live NCAA FB game.""" # Updated docstring
        try:
            main_img = Image.new('RGBA', (self.display_width, self.display_height), (0, 0, 0, 255))
            overlay = Image.new('RGBA', (self.display_width, self.display_height), (0, 0, 0, 0))
            draw_overlay = ImageDraw.Draw(overlay) # Draw text elements on overlay first

            home_logo = self._load_and_resize_logo(game["home_id"], game["home_abbr"], game["home_logo_path"], game.get("home_logo_url"))
            away_logo = self._load_and_resize_logo(game["away_id"], game["away_abbr"], game["away_logo_path"], game.get("away_logo_url"))

            if not home_logo or not away_logo:
                self.logger.error(f"Failed to load logos for live game: {game.get('id')}") # Changed log prefix
                # Draw placeholder text if logos fail
                draw_final = ImageDraw.Draw(main_img.convert('RGB'))
                self._draw_text_with_outline(draw_final, "Logo Error", (5,5), self.fonts['status'])
                self.display_manager.image.paste(main_img.convert('RGB'), (0, 0))
                self.display_manager.update_display()
                return

            center_y = self.display_height // 2

            # Draw logos (shifted slightly more inward than NHL perhaps)
            home_x = self.display_width - home_logo.width + 10 #adjusted from 18 # Adjust position as needed
            home_y = center_y - (home_logo.height // 2)
            main_img.paste(home_logo, (home_x, home_y), home_logo)

            away_x = -10 #adjusted from 18 # Adjust position as needed
            away_y = center_y - (away_logo.height // 2)
            main_img.paste(away_logo, (away_x, away_y), away_logo)

            # --- Draw Text Elements on Overlay ---
            # Note: Rankings are now handled in the records/rankings section below

            # Scores (centered, slightly above bottom)
            home_score = str(game.get("home_score", "0"))
            away_score = str(game.get("away_score", "0"))
            score_text = f"{away_score}-{home_score}"
            score_width = draw_overlay.textlength(score_text, font=self.fonts['score'])
            score_x = (self.display_width - score_width) // 2
            score_y = (self.display_height // 2) - 3 #centered #from 14 # Position score higher
            self._draw_text_with_outline(draw_overlay, score_text, (score_x, score_y), self.fonts['score'])

            # Period/Quarter and Clock (Top center)
            period_clock_text = f"{game.get('period_text', '')} {game.get('clock', '')}".strip()
            if game.get("is_halftime"): \
                period_clock_text = "Halftime" # Override for halftime
            elif game.get("is_period_break"):
                period_clock_text = game.get("status_text", "Period Break")

            status_width = draw_overlay.textlength(period_clock_text, font=self.fonts['time'])
            status_x = (self.display_width - status_width) // 2
            status_y = 1 # Position at top
            self._draw_text_with_outline(draw_overlay, period_clock_text, (status_x, status_y), self.fonts['time'])

            # Down & Distance or Scoring Event (Below Period/Clock)
            scoring_event = game.get("scoring_event", "")
            down_distance = game.get("down_distance_text", "")
            if self.display_width > 128:
                down_distance = game.get("down_distance_text_long", "")
            
            # Show scoring event if detected, otherwise show down & distance
            if scoring_event and game.get("is_live"):
                # Display scoring event with special formatting
                event_width = draw_overlay.textlength(scoring_event, font=self.fonts['detail'])
                event_x = (self.display_width - event_width) // 2
                event_y = (self.display_height) - 7
                
                # Color coding for different scoring events
                if scoring_event == "TOUCHDOWN":
                    event_color = (255, 215, 0)  # Gold
                elif scoring_event == "FIELD GOAL":
                    event_color = (0, 255, 0)    # Green
                elif scoring_event == "PAT":
                    event_color = (255, 165, 0)  # Orange
                else:
                    event_color = (255, 255, 255)  # White
                
                self._draw_text_with_outline(draw_overlay, scoring_event, (event_x, event_y), self.fonts['detail'], fill=event_color)
            elif down_distance and game.get("is_live"): # Only show if live and available
                dd_width = draw_overlay.textlength(down_distance, font=self.fonts['detail'])
                dd_x = (self.display_width - dd_width) // 2
                dd_y = (self.display_height)- 7 # Top of D&D text
                down_color = (200, 200, 0) if not game.get("is_redzone", False) else (255,0,0) # Yellowish text
                self._draw_text_with_outline(draw_overlay, down_distance, (dd_x, dd_y), self.fonts['detail'], fill=down_color)

                # Possession Indicator (small football icon)
                possession = game.get("possession_indicator")
                if possession: # Only draw if possession is known
                    ball_radius_x = 3  # Wider for football shape
                    ball_radius_y = 2  # Shorter for football shape
                    ball_color = (139, 69, 19) # Brown color for the football
                    lace_color = (255, 255, 255) # White for laces

                    # Approximate height of the detail font (4x6 font at size 6 is roughly 6px tall)
                    detail_font_height_approx = 6
                    ball_y_center = dd_y + (detail_font_height_approx // 2) # Center ball vertically with D&D text

                    possession_ball_padding = 3 # Pixels between D&D text and ball

                    if possession == "away":
                        # Position ball to the left of D&D text
                        ball_x_center = dd_x - possession_ball_padding - ball_radius_x
                    elif possession == "home":
                        # Position ball to the right of D&D text
                        ball_x_center = dd_x + dd_width + possession_ball_padding + ball_radius_x
                    else:
                        ball_x_center = 0 # Should not happen / no indicator

                    if ball_x_center > 0: # Draw if position is valid
                        # Draw the football shape (ellipse)
                        draw_overlay.ellipse(
                            (ball_x_center - ball_radius_x, ball_y_center - ball_radius_y,  # x0, y0
                             ball_x_center + ball_radius_x, ball_y_center + ball_radius_y), # x1, y1
                            fill=ball_color, outline=(0,0,0)
                        )
                        # Draw a simple horizontal lace
                        draw_overlay.line(
                            (ball_x_center - 1, ball_y_center, ball_x_center + 1, ball_y_center),
                            fill=lace_color, width=1
                        )

            # Timeouts (Bottom corners) - 3 small bars per team
            timeout_bar_width = 4
            timeout_bar_height = 2
            timeout_spacing = 1
            timeout_y = self.display_height - timeout_bar_height - 1 # Bottom edge

            # Away Timeouts (Bottom Left)
            away_timeouts_remaining = game.get("away_timeouts", 0)
            for i in range(3):
                to_x = 2 + i * (timeout_bar_width + timeout_spacing)
                color = (255, 255, 255) if i < away_timeouts_remaining else (80, 80, 80) # White if available, gray if used
                draw_overlay.rectangle([to_x, timeout_y, to_x + timeout_bar_width, timeout_y + timeout_bar_height], fill=color, outline=(0,0,0))

             # Home Timeouts (Bottom Right)
            home_timeouts_remaining = game.get("home_timeouts", 0)
            for i in range(3):
                to_x = self.display_width - 2 - timeout_bar_width - (2-i) * (timeout_bar_width + timeout_spacing)
                color = (255, 255, 255) if i < home_timeouts_remaining else (80, 80, 80) # White if available, gray if used
                draw_overlay.rectangle([to_x, timeout_y, to_x + timeout_bar_width, timeout_y + timeout_bar_height], fill=color, outline=(0,0,0))

            # Draw odds if available
            if 'odds' in game and game['odds']:
                self._draw_dynamic_odds(draw_overlay, game['odds'], self.display_width, self.display_height)

            # Draw records or rankings if enabled
            if self.show_records or self.show_ranking:
                try:
                    record_font = ImageFont.truetype("assets/fonts/4x6-font.ttf", 6)
                    self.logger.debug(f"Loaded 6px record font successfully")
                except IOError:
                    record_font = ImageFont.load_default()
                    self.logger.warning(f"Failed to load 6px font, using default font (size: {record_font.size})")
                
                # Get team abbreviations
                away_abbr = game.get('away_abbr', '')
                home_abbr = game.get('home_abbr', '')
                
                record_bbox = draw_overlay.textbbox((0,0), "0-0", font=record_font)
                record_height = record_bbox[3] - record_bbox[1]
                record_y = self.display_height - record_height - 4
                self.logger.debug(f"Record positioning: height={record_height}, record_y={record_y}, display_height={self.display_height}")

                # Display away team info
                if away_abbr:
                    if self.show_ranking and self.show_records:
                        # When both rankings and records are enabled, rankings replace records completely
                        away_rank = self._team_rankings_cache.get(away_abbr, 0)
                        if away_rank > 0:
                            away_text = f"#{away_rank}"
                        else:
                            # Show nothing for unranked teams when rankings are prioritized
                            away_text = ''
                    elif self.show_ranking:
                        # Show ranking only if available
                        away_rank = self._team_rankings_cache.get(away_abbr, 0)
                        if away_rank > 0:
                            away_text = f"#{away_rank}"
                        else:
                            away_text = ''
                    elif self.show_records:
                        # Show record only when rankings are disabled
                        away_text = game.get('away_record', '')
                    else:
                        away_text = ''
                    
                    if away_text:
                        away_record_x = 3
                        self.logger.debug(f"Drawing away ranking '{away_text}' at ({away_record_x}, {record_y}) with font size {record_font.size if hasattr(record_font, 'size') else 'unknown'}")
                        self._draw_text_with_outline(draw_overlay, away_text, (away_record_x, record_y), record_font)

                # Display home team info
                if home_abbr:
                    if self.show_ranking and self.show_records:
                        # When both rankings and records are enabled, rankings replace records completely
                        home_rank = self._team_rankings_cache.get(home_abbr, 0)
                        if home_rank > 0:
                            home_text = f"#{home_rank}"
                        else:
                            # Show nothing for unranked teams when rankings are prioritized
                            home_text = ''
                    elif self.show_ranking:
                        # Show ranking only if available
                        home_rank = self._team_rankings_cache.get(home_abbr, 0)
                        if home_rank > 0:
                            home_text = f"#{home_rank}"
                        else:
                            home_text = ''
                    elif self.show_records:
                        # Show record only when rankings are disabled
                        home_text = game.get('home_record', '')
                    else:
                        home_text = ''
                    
                    if home_text:
                        home_record_bbox = draw_overlay.textbbox((0,0), home_text, font=record_font)
                        home_record_width = home_record_bbox[2] - home_record_bbox[0]
                        home_record_x = self.display_width - home_record_width - 3
                        self.logger.debug(f"Drawing home ranking '{home_text}' at ({home_record_x}, {record_y}) with font size {record_font.size if hasattr(record_font, 'size') else 'unknown'}")
                        self._draw_text_with_outline(draw_overlay, home_text, (home_record_x, record_y), record_font)

            # Composite the text overlay onto the main image
            main_img = Image.alpha_composite(main_img, overlay)
            main_img = main_img.convert('RGB') # Convert for display

            # Display the final image
            # #region agent log
            import json
            import time
            try:
                with open('/home/chuck/Github/LEDMatrix/.cursor/debug.log', 'a') as f:
                    f.write(json.dumps({
                        "sessionId": "debug-session",
                        "runId": "run1",
                        "hypothesisId": "C",
                        "location": "football.py:390",
                        "message": "About to update display",
                        "data": {
                            "force_clear": force_clear,
                            "game": game.get('away_abbr', '') + "@" + game.get('home_abbr', '')
                        },
                        "timestamp": int(time.time() * 1000)
                    }) + "\n")
            except: pass
            # #endregion
            self.display_manager.image.paste(main_img, (0, 0))
            self.display_manager.update_display() # Update display here for live
            # #region agent log
            try:
                with open('/home/chuck/Github/LEDMatrix/.cursor/debug.log', 'a') as f:
                    f.write(json.dumps({
                        "sessionId": "debug-session",
                        "runId": "run1",
                        "hypothesisId": "C",
                        "location": "football.py:392",
                        "message": "After update display",
                        "data": {
                            "force_clear": force_clear
                        },
                        "timestamp": int(time.time() * 1000)
                    }) + "\n")
            except: pass
            # #endregion

        except Exception as e:
            self.logger.error(f"Error displaying live Football game: {e}", exc_info=True) # Changed log prefix
