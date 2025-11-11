import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from PIL import Image, ImageDraw, ImageFont

from src.base_classes.data_sources import ESPNDataSource
from src.base_classes.sports import SportsCore, SportsLive, SportsRecent, SportsUpcoming
from src.cache_manager import CacheManager
from src.display_manager import DisplayManager
from src.logo_downloader import LogoDownloader


class MMA(SportsCore):
    """Base class for MMA sports with common functionality."""

    def __init__(
        self,
        config: Dict[str, Any],
        display_manager: DisplayManager,
        cache_manager: CacheManager,
        logger: logging.Logger,
        sport_key: str,
    ):
        super().__init__(config, display_manager, cache_manager, logger, sport_key)
        self.data_source = ESPNDataSource(logger)
        self.sport = "mma"
        self.favorite_fighters = [
            f.lower() for f in self.mode_config.get("favorite_fighters", [])
        ]
        self.favorite_weight_class = [
            wc.lower() for wc in self.mode_config.get("favorite_weight_class", [])
        ]

    def _load_and_resize_logo(
        self, fighter_id: str, fighter_name: str, logo_path: Path, logo_url: str
    ) -> Optional[Image.Image]:
        """Load and resize a team logo, with caching and automatic download if missing."""
        self.logger.debug(f"Logo path: {logo_path}")
        if fighter_id in self._logo_cache:
            self.logger.debug(f"Using cached logo for {fighter_name}")
            return self._logo_cache[fighter_id]

        try:
            # If no variation found, try to download missing logo
            if not logo_path.exists():
                self.logger.info(
                    f"Logo not found for {fighter_name} at {logo_path}. Attempting to download."
                )
                # Try to download the logo from ESPN API (this will create placeholder if download fails)

                # Get logo directory
                if not self.logo_dir.exists():
                    self.logo_dir.mkdir()

                response = self.session.get(logo_url, headers=self.headers, timeout=120)
                response.raise_for_status()
                # Verify it's actually an image
                content_type = response.headers.get("content-type", "").lower()
                if not any(
                    img_type in content_type
                    for img_type in [
                        "image/png",
                        "image/jpeg",
                        "image/jpg",
                        "image/gif",
                    ]
                ):
                    self.logger.warning(
                        f"Downloaded content for {fighter_name} is not an image: {content_type}"
                    )
                    return

                with logo_path.open(mode="wb") as f:
                    f.write(response.content)

            # Verify and convert the downloaded image to RGBA format
            try:
                with Image.open(logo_path) as img:
                    # Convert to RGBA to avoid PIL warnings about palette images with transparency
                    if img.mode in ("P", "LA", "L"):
                        # Convert palette or grayscale images to RGBA
                        img = img.convert("RGBA")
                    elif img.mode == "RGB":
                        # Convert RGB to RGBA (add alpha channel)
                        img = img.convert("RGBA")
                    elif img.mode != "RGBA":
                        # For any other mode, convert to RGBA
                        img = img.convert("RGBA")

                    # Save the converted image
                    img.save(logo_path, "PNG")

                    self.logger.info(
                        f"Successfully downloaded and converted logo for {fighter_name} -> {logo_path.name}"
                    )
            except Exception as e:
                self.logger.error(
                    f"Downloaded file for {fighter_name} is not a valid image or conversion failed: {e}"
                )
                try:
                    logo_path.unlink()  # Remove invalid file
                except:
                    pass
                return
            # Only try to open the logo if the file exists
            if logo_path.exists():
                logo = Image.open(logo_path)
            else:
                self.logger.error(
                    f"Logo file still doesn't exist at {logo_path} after download attempt"
                )
                return None
            if logo.mode != "RGBA":
                logo = logo.convert("RGBA")

            max_width = int(self.display_width * 1.5)
            max_height = int(self.display_height * 1.5)
            logo.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)
            self._logo_cache[fighter_id] = logo
            return logo

        except Exception as e:
            self.logger.error(
                f"Error loading logo for {fighter_name}: {e}", exc_info=True
            )
            return None

    def _extract_game_details(self, game_event: dict) -> dict | None:
        if not game_event:
            return None
        try:
            competition = game_event["competitions"][0]
            status = competition["status"]
            competitors = competition["competitors"]
            game_date_str = game_event["date"]
            start_time_utc = None
            try:
                start_time_utc = datetime.fromisoformat(
                    game_date_str.replace("Z", "+00:00")
                )
            except ValueError:
                logging.warning(f"Could not parse game date: {game_date_str}")

            try:
                fight_class = competition["type"]["abbreviation"]
            except KeyError:
                fight_class = ""

            fighter1 = next((c for c in competitors if c.get("order") == 1), None)
            fighter2 = next((c for c in competitors if c.get("order") == 2), None)

            if not fighter1 or not fighter2:
                self.logger.warning(
                    f"Could not find Fighter 1 or 2 in event: {competition.get('id')}"
                )
                return None

            try:
                fighter1_name = fighter1["athlete"]["fullName"]
            except KeyError:
                fighter1_name = ""
            try:
                fighter2_name = fighter2["athlete"]["fullName"]
            except KeyError:
                fighter2_name = ""

            # Check if this is a favorite team game BEFORE doing expensive logging
            is_favorite_game = (
                fighter1_name.lower() in self.favorite_fighters
                or fighter2_name.lower() in self.favorite_fighters
            ) or fight_class.lower() in self.favorite_weight_class

            # Only log debug info for favorite team games
            if is_favorite_game:
                self.logger.debug(
                    f"Processing favorite fights: {competition.get('id')}"
                )
                self.logger.debug(
                    f"Found teams: {fighter1_name} vs {fighter2_name}, Status: {status['type']['name']}, State: {status['type']['state']}"
                )

            game_time, game_date = "", ""
            if start_time_utc:
                local_time = start_time_utc.astimezone(self._get_timezone())
                game_time = local_time.strftime("%I:%M%p").lstrip("0")

                # Check date format from config
                use_short_date_format = self.config.get("display", {}).get(
                    "use_short_date_format", False
                )
                if use_short_date_format:
                    game_date = local_time.strftime("%-m/%-d")
                else:
                    game_date = self.display_manager.format_date_with_ordinal(
                        local_time
                    )

            fighter1_record = (
                fighter1.get("records", [{}])[0].get("summary", "")
                if fighter1.get("records")
                else ""
            )
            fighter2_record = (
                fighter2.get("records", [{}])[0].get("summary", "")
                if fighter2.get("records")
                else ""
            )

            # Don't show "0-0" records - set to blank instead
            if fighter1_record in {"0-0", "0-0-0"}:
                fighter1_record = ""
            if fighter2_record in {"0-0", "0-0-0"}:
                fighter2_record = ""

            details = {
                "event_id": game_event.get("id"),
                "id": competition.get("id"),
                "game_time": game_time,
                "game_date": game_date,
                "start_time_utc": start_time_utc,
                "status_text": status["type"][
                    "shortDetail"
                ],  # e.g., "Final", "7:30 PM", "Q1 12:34"
                "is_live": status["type"]["state"] == "in",
                "is_final": status["type"]["state"] == "post",
                "is_upcoming": (
                    status["type"]["state"] == "pre"
                    or status["type"]["name"].lower()
                    in ["scheduled", "pre-game", "status_scheduled"]
                ),
                "is_period_break": status["type"]["name"]
                == "STATUS_END_PERIOD",  # Added Period Break check
                "fight_class": fight_class,
                "fighter1_name": fighter1_name,
                "fighter1_id": fighter1["id"],
                # "home_score": home_team.get("score", "0"),
                "fighter1_image_path": self.logo_dir
                / Path(f"{fighter1.get('id')}.png"),
                "fighter1_image_url": f"https://a.espncdn.com/combiner/i?img=/i/headshots/mma/players/full/{fighter1.get('id')}.png",
                "fighter1_country_url": fighter1.get("athlete", {})
                .get("flag", {})
                .get("href", ""),
                "fighter1_record": fighter1_record,
                "fighter2_name": fighter2_name,
                "fighter2_id": fighter2["id"],
                # "home_score": home_team.get("score", "0"),
                "fighter2_image_path": self.logo_dir
                / Path(f"{fighter2.get('id')}.png"),
                "fighter2_image_url": f"https://a.espncdn.com/combiner/i?img=/i/headshots/mma/players/full/{fighter2.get('id')}.png",
                "fighter2_country_url": fighter2.get("athlete", {})
                .get("flag", {})
                .get("href", ""),
                "fighter2_record": fighter2_record,
                "is_within_window": True,  # Whether game is within display window
            }
            return details
        except Exception as e:
            # Log the problematic event structure if possible
            logging.error(
                f"Error extracting game details: {e} from event: {game_event.get('event_id')} - {competition.get('id')}",
                exc_info=True,
            )
            return None


class MMARecent(MMA, SportsRecent):
    def __init__(
        self,
        config: Dict[str, Any],
        display_manager: DisplayManager,
        cache_manager: CacheManager,
        logger: logging.Logger,
        sport_key: str,
    ):
        super().__init__(config, display_manager, cache_manager, logger, sport_key)

    def _draw_scorebug_layout(self, game: Dict, force_clear: bool = False) -> None:
        """Draw the layout for a recently completed NCAA FB game."""  # Updated docstring
        try:
            main_img = Image.new(
                "RGBA", (self.display_width, self.display_height), (0, 0, 0, 255)
            )
            overlay = Image.new(
                "RGBA", (self.display_width, self.display_height), (0, 0, 0, 0)
            )
            draw_overlay = ImageDraw.Draw(overlay)

            fighter1_image = self._load_and_resize_logo(
                game["fighter1_id"],
                game["fighter1_name"],
                game["fighter1_image_path"],
                game["fighter1_image_url"],
            )
            fighter2_image = self._load_and_resize_logo(
                game["fighter2_id"],
                game["fighter2_name"],
                game["fighter2_image_path"],
                game["fighter2_image_url"],
            )

            # fighter1_flag_image = self._load_and_resize_logo(
            #     game["fighter1_id"],
            #     game["fighter1_name"],
            #     game["fighter1_image_path"],
            #     game["fighter1_country_url"],
            # )
            # fighter2_flag_image = self._load_and_resize_logo(
            #     game["fighter1_id"]+"_flag",
            #     game["fighter1_name"],
            #     game["fighter1_image_path"],
            #     game["fighter2_country_url"],
            # )

            if not fighter1_image or not fighter2_image:
                self.logger.error(
                    f"Failed to load logos for game: {game.get('id')}"
                )  # Changed log prefix
                draw_final = ImageDraw.Draw(main_img.convert("RGB"))
                self._draw_text_with_outline(
                    draw_final, "Logo Error", (5, 5), self.fonts["status"]
                )
                self.display_manager.image.paste(main_img.convert("RGB"), (0, 0))
                self.display_manager.update_display()
                return

            center_y = self.display_height // 2

            # MLB-style logo positions
            home_x = (
                self.display_width
                - fighter1_image.width
                + fighter1_image.width // 4
                + 2
            )
            home_y = center_y - (fighter1_image.height // 2)
            main_img.paste(fighter1_image, (home_x, home_y), fighter1_image)

            away_x = -2 - fighter2_image.width // 4
            away_y = center_y - (fighter2_image.height // 2)
            main_img.paste(fighter2_image, (away_x, away_y), fighter2_image)
            # Final Scores (Centered, same position as live)
            score_text = f"Some score here"
            score_width = draw_overlay.textlength(score_text, font=self.fonts["score"])
            score_x = (self.display_width - score_width) // 2
            score_y = self.display_height - 14
            self._draw_text_with_outline(
                draw_overlay, score_text, (score_x, score_y), self.fonts["score"]
            )

            # "Final" text (Top center)
            status_text = game.get(
                "period_text", "Final"
            )  # Use formatted period text (e.g., "Final/OT") or default "Final"
            status_width = draw_overlay.textlength(status_text, font=self.fonts["time"])
            status_x = (self.display_width - status_width) // 2
            status_y = 1
            self._draw_text_with_outline(
                draw_overlay, status_text, (status_x, status_y), self.fonts["time"]
            )

            # if 'odds' in game and game['odds']:
            #     self._draw_dynamic_odds(draw_overlay, game['odds'], self.display_width, self.display_height)

            # Draw records or rankings if enabled
            if self.show_records:
                try:
                    record_font = ImageFont.truetype("assets/fonts/4x6-font.ttf", 6)
                    self.logger.debug(f"Loaded 6px record font successfully")
                except IOError:
                    record_font = ImageFont.load_default()
                    self.logger.warning(
                        f"Failed to load 6px font, using default font (size: {record_font.size})"
                    )

                # Get team abbreviations
                fighter1_record = game.get("fighter1_record", "")
                fighter2_record = game.get("fighter2_record", "")

                record_bbox = draw_overlay.textbbox((0, 0), "0-0-0", font=record_font)
                record_height = record_bbox[3] - record_bbox[1]
                record_y = self.display_height - record_height
                self.logger.debug(
                    f"Record positioning: height={record_height}, record_y={record_y}, display_height={self.display_height}"
                )

                # Display away team info
                if fighter1_record:
                    away_text = fighter1_record
                    away_record_x = 0
                    self.logger.debug(
                        f"Drawing away ranking '{away_text}' at ({away_record_x}, {record_y}) with font size {record_font.size if hasattr(record_font, 'size') else 'unknown'}"
                    )
                    self._draw_text_with_outline(
                        draw_overlay, away_text, (away_record_x, record_y), record_font
                    )

                # Display home team info
                if fighter2_record:
                    home_text = fighter2_record
                    home_record_bbox = draw_overlay.textbbox(
                        (0, 0), home_text, font=record_font
                    )
                    home_record_width = home_record_bbox[2] - home_record_bbox[0]
                    home_record_x = self.display_width - home_record_width
                    self.logger.debug(
                        f"Drawing away ranking '{away_text}' at ({away_record_x}, {record_y}) with font size {record_font.size if hasattr(record_font, 'size') else 'unknown'}"
                    )
                    self._draw_text_with_outline(
                        draw_overlay, home_text, (home_record_x, record_y), record_font
                    )

            self._custom_scorebug_layout(game, draw_overlay)
            # Composite and display
            main_img = Image.alpha_composite(main_img, overlay)
            main_img = main_img.convert("RGB")
            self.display_manager.image.paste(main_img, (0, 0))
            self.display_manager.update_display()  # Update display here

        except Exception as e:
            self.logger.error(
                f"Error displaying recent game: {e}", exc_info=True
            )  # Changed log prefix

    def update(self):
        """Update recent games data."""
        if not self.is_enabled:
            return
        current_time = time.time()
        if current_time - self.last_update < self.update_interval:
            return

        self.last_update = current_time  # Update time even if fetch fails

        try:
            data = self._fetch_data()  # Uses shared cache
            if not data or "events" not in data:
                self.logger.warning(
                    "No events found in shared data."
                )  # Changed log prefix
                if not self.games_list:
                    self.current_game = None  # Clear display if no games were showing
                return

            events = data["events"]
            self.logger.info(
                f"Processing {len(events)} events from shared data."
            )  # Changed log prefix

            # Define date range for "recent" games (last 21 days to capture games from 3 weeks ago)
            now = datetime.now(timezone.utc)
            recent_cutoff = now - timedelta(days=21)
            self.logger.info(
                f"Current time: {now}, Recent cutoff: {recent_cutoff} (21 days ago)"
            )

            # Process games and filter for final games, date range & favorite teams
            processed_games = []
            flattened_events = [
                {
                    **{k: v for k, v in event.items() if k != "competitions"},
                    "competitions": [comp],
                }
                for event in data["events"]
                for comp in event.get("competitions", [])
            ]
            for event in flattened_events:
                game = self._extract_game_details(event)
                # Filter criteria: must be final AND within recent date range
                if game and game["is_final"]:
                    game_time = game.get("start_time_utc")
                    if game_time and game_time >= recent_cutoff:
                        processed_games.append(game)
            # Filter for favorite teams
            if self.favorite_teams:
                # Get all games involving favorite teams
                favorite_team_games = [
                    game
                    for game in processed_games
                    if (
                        game["fighter1_name"].lower() in self.favorite_fighters
                        or game["fighter2_name"].lower() in self.favorite_fighters
                    )
                    or game["fight_class"].lower() in self.favorite_weight_class
                ]
                self.logger.info(
                    f"Found {len(favorite_team_games)} favorite team games out of {len(processed_games)} total final games within last 21 days"
                )

                # Sort the final list by game time (most recent first)
                favorite_team_games.sort(
                    key=lambda g: g.get("start_time_utc")
                    or datetime.min.replace(tzinfo=timezone.utc),
                    reverse=True,
                )

                # Select one game per favorite team (most recent game for each team)
                team_games = []
                for fighter in self.favorite_fighters:
                    # Find games where this team is playing
                    team_specific_games = [
                        game
                        for game in favorite_team_games
                        if (
                            game["fighter1_name"].lower() == fighter.lower()
                            or game["fighter2_name"].lower() == fighter.lower()
                        )
                    ]

                    if team_specific_games:
                        # Sort by game time and take the most recent
                        team_specific_games.sort(
                            key=lambda g: g.get("start_time_utc")
                            or datetime.min.replace(tzinfo=timezone.utc),
                            reverse=True,
                        )
                        team_games.append(team_specific_games[0])

                for wc in self.favorite_weight_class:
                    # Find games where this team is playing
                    team_specific_games = [
                        game
                        for game in favorite_team_games
                        if game["fight_class"].lower() == wc.lower()
                    ]

                    if team_specific_games:
                        # Sort by game time and take the most recent
                        team_specific_games.sort(
                            key=lambda g: g.get("start_time_utc")
                            or datetime.min.replace(tzinfo=timezone.utc),
                            reverse=True,
                        )
                        team_games.append(team_specific_games[0])

                team_games = list(set(team_games))
                # Debug: Show which games are selected for display
                for i, game in enumerate(team_games):
                    self.logger.info(
                        f"Game {i+1} for display: {game['away_abbr']} @ {game['home_abbr']} - {game.get('start_time_utc')} - Score: {game['away_score']}-{game['home_score']}"
                    )
            else:
                team_games = (
                    processed_games  # Show all recent games if no favorites defined
                )
                self.logger.info(
                    f"Found {len(processed_games)} total final games within last 21 days (no favorite teams configured)"
                )
                # Sort by game time, most recent first
                team_games.sort(
                    key=lambda g: g.get("start_time_utc")
                    or datetime.min.replace(tzinfo=timezone.utc),
                    reverse=True,
                )
                # Limit to the specified number of recent games
                team_games = team_games[: self.recent_games_to_show]

            # Check if the list of games to display has changed
            new_game_ids = {g["id"] for g in team_games}
            current_game_ids = {g["id"] for g in self.games_list}

            if new_game_ids != current_game_ids:
                self.logger.info(
                    f"Found {len(team_games)} final games within window for display."
                )  # Changed log prefix
                self.games_list = team_games
                # Reset index if list changed or current game removed
                if (
                    not self.current_game
                    or not self.games_list
                    or self.current_game["id"] not in new_game_ids
                ):
                    self.current_game_index = 0
                    self.current_game = self.games_list[0] if self.games_list else None
                    self.last_game_switch = current_time  # Reset switch timer
                else:
                    # Try to maintain position if possible
                    try:
                        self.current_game_index = next(
                            i
                            for i, g in enumerate(self.games_list)
                            if g["id"] == self.current_game["id"]
                        )
                        self.current_game = self.games_list[
                            self.current_game_index
                        ]  # Update data just in case
                    except StopIteration:
                        self.current_game_index = 0
                        self.current_game = self.games_list[0]
                        self.last_game_switch = current_time

            elif self.games_list:
                # List content is same, just update data for current game
                self.current_game = self.games_list[self.current_game_index]

            if not self.games_list:
                self.logger.info(
                    "No relevant recent games found to display."
                )  # Changed log prefix
                self.current_game = None  # Ensure display clears if no games

        except Exception as e:
            self.logger.error(
                f"Error updating recent games: {e}", exc_info=True
            )  # Changed log prefix
            # Don't clear current game on error, keep showing last known state
            # self.current_game = None # Decide if we want to clear display on error


class MMAUpcoming(MMA, SportsUpcoming):
    def __init__(
        self,
        config: Dict[str, Any],
        display_manager: DisplayManager,
        cache_manager: CacheManager,
        logger: logging.Logger,
        sport_key: str,
    ):
        super().__init__(config, display_manager, cache_manager, logger, sport_key)


class MMALive(MMA, SportsLive):
    def __init__(
        self,
        config: Dict[str, Any],
        display_manager: DisplayManager,
        cache_manager: CacheManager,
        logger: logging.Logger,
        sport_key: str,
    ):
        super().__init__(config, display_manager, cache_manager, logger, sport_key)

    def _test_mode_update(self):
        if self.current_game and self.current_game["is_live"]:
            # For testing, we'll just update the clock to show it's working
            minutes = int(self.current_game["clock"].split(":")[0])
            seconds = int(self.current_game["clock"].split(":")[1])
            seconds -= 1
            if seconds < 0:
                seconds = 59
                minutes -= 1
                if minutes < 0:
                    minutes = 19
                    if self.current_game["period"] < 3:
                        self.current_game["period"] += 1
                    else:
                        self.current_game["period"] = 1
            self.current_game["clock"] = f"{minutes:02d}:{seconds:02d}"
            # Always update display in test mode

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

            # --- Draw Text Elements on Overlay ---
            # Note: Rankings are now handled in the records/rankings section below

            # Period/Quarter and Clock (Top center)
            period_clock_text = (
                f"{game.get('period_text', '')} {game.get('clock', '')}".strip()
            )
            if game.get("is_period_break"):
                period_clock_text = game.get("status_text", "Period Break")

            status_width = draw_overlay.textlength(
                period_clock_text, font=self.fonts["time"]
            )
            status_x = (self.display_width - status_width) // 2
            status_y = 1  # Position at top
            self._draw_text_with_outline(
                draw_overlay,
                period_clock_text,
                (status_x, status_y),
                self.fonts["time"],
            )

            # Scores (centered, slightly above bottom)
            home_score = str(game.get("home_score", "0"))
            away_score = str(game.get("away_score", "0"))
            score_text = f"{away_score}-{home_score}"
            score_width = draw_overlay.textlength(score_text, font=self.fonts["score"])
            score_x = (self.display_width - score_width) // 2
            score_y = (
                self.display_height // 2
            ) - 3  # centered #from 14 # Position score higher
            self._draw_text_with_outline(
                draw_overlay, score_text, (score_x, score_y), self.fonts["score"]
            )

            # Shots on Goal
            if self.show_shots_on_goal:
                shots_font = ImageFont.truetype("assets/fonts/4x6-font.ttf", 6)
                home_shots = str(game.get("home_shots", "0"))
                away_shots = str(game.get("away_shots", "0"))
                shots_text = f"{away_shots}   SHOTS   {home_shots}"
                shots_bbox = draw_overlay.textbbox((0, 0), shots_text, font=shots_font)
                shots_height = shots_bbox[3] - shots_bbox[1]
                shots_y = self.display_height - shots_height - 1
                shots_width = draw_overlay.textlength(shots_text, font=shots_font)
                shots_x = (self.display_width - shots_width) // 2
                self._draw_text_with_outline(
                    draw_overlay, shots_text, (shots_x, shots_y), shots_font
                )

            # Draw odds if available
            if "odds" in game and game["odds"]:
                self._draw_dynamic_odds(
                    draw_overlay, game["odds"], self.display_width, self.display_height
                )

            # Draw records or rankings if enabled
            if self.show_records or self.show_ranking:
                try:
                    record_font = ImageFont.truetype("assets/fonts/4x6-font.ttf", 6)
                    self.logger.debug(f"Loaded 6px record font successfully")
                except IOError:
                    record_font = ImageFont.load_default()
                    self.logger.warning(
                        f"Failed to load 6px font, using default font (size: {record_font.size})"
                    )

                # Get team abbreviations
                away_abbr = game.get("away_abbr", "")
                home_abbr = game.get("home_abbr", "")

                record_bbox = draw_overlay.textbbox((0, 0), "0-0", font=record_font)
                record_height = record_bbox[3] - record_bbox[1]
                record_y = self.display_height - record_height - 1
                self.logger.debug(
                    f"Record positioning: height={record_height}, record_y={record_y}, display_height={self.display_height}"
                )

                # Display away team info
                if away_abbr:
                    if self.show_ranking and self.show_records:
                        # When both rankings and records are enabled, rankings replace records completely
                        away_rank = self._team_rankings_cache.get(away_abbr, 0)
                        if away_rank > 0:
                            away_text = f"#{away_rank}"
                        else:
                            # Show nothing for unranked teams when rankings are prioritized
                            away_text = ""
                    elif self.show_ranking:
                        # Show ranking only if available
                        away_rank = self._team_rankings_cache.get(away_abbr, 0)
                        if away_rank > 0:
                            away_text = f"#{away_rank}"
                        else:
                            away_text = ""
                    elif self.show_records:
                        # Show record only when rankings are disabled
                        away_text = game.get("away_record", "")
                    else:
                        away_text = ""

                    if away_text:
                        away_record_x = 3
                        self.logger.debug(
                            f"Drawing away ranking '{away_text}' at ({away_record_x}, {record_y}) with font size {record_font.size if hasattr(record_font, 'size') else 'unknown'}"
                        )
                        self._draw_text_with_outline(
                            draw_overlay,
                            away_text,
                            (away_record_x, record_y),
                            record_font,
                        )

                # Display home team info
                if home_abbr:
                    if self.show_ranking and self.show_records:
                        # When both rankings and records are enabled, rankings replace records completely
                        home_rank = self._team_rankings_cache.get(home_abbr, 0)
                        if home_rank > 0:
                            home_text = f"#{home_rank}"
                        else:
                            # Show nothing for unranked teams when rankings are prioritized
                            home_text = ""
                    elif self.show_ranking:
                        # Show ranking only if available
                        home_rank = self._team_rankings_cache.get(home_abbr, 0)
                        if home_rank > 0:
                            home_text = f"#{home_rank}"
                        else:
                            home_text = ""
                    elif self.show_records:
                        # Show record only when rankings are disabled
                        home_text = game.get("home_record", "")
                    else:
                        home_text = ""

                    if home_text:
                        home_record_bbox = draw_overlay.textbbox(
                            (0, 0), home_text, font=record_font
                        )
                        home_record_width = home_record_bbox[2] - home_record_bbox[0]
                        home_record_x = self.display_width - home_record_width - 3
                        self.logger.debug(
                            f"Drawing home ranking '{home_text}' at ({home_record_x}, {record_y}) with font size {record_font.size if hasattr(record_font, 'size') else 'unknown'}"
                        )
                        self._draw_text_with_outline(
                            draw_overlay,
                            home_text,
                            (home_record_x, record_y),
                            record_font,
                        )

            # Composite the text overlay onto the main image
            main_img = Image.alpha_composite(main_img, overlay)
            main_img = main_img.convert("RGB")  # Convert for display

            # Display the final image
            self.display_manager.image.paste(main_img, (0, 0))
            self.display_manager.update_display()  # Update display here for live

        except Exception as e:
            self.logger.error(
                f"Error displaying live Hockey game: {e}", exc_info=True
            )  # Changed log prefix
