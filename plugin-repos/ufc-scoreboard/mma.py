"""MMA Base Classes - Adapted from original work by Alex Resnick (legoguy1000) - PR #137"""

import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from PIL import Image, ImageDraw, ImageFont

from data_sources import ESPNDataSource
from sports import SportsCore, SportsLive, SportsRecent, SportsUpcoming


class MMA(SportsCore):
    """Base class for MMA sports with common functionality."""

    def __init__(
        self,
        config: Dict[str, Any],
        display_manager,
        cache_manager,
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

    def _custom_scorebug_layout(self, game: dict, draw: ImageDraw.ImageDraw):
        """No-op hook for subclasses to add custom scorebug elements."""
        pass

    def _load_and_resize_headshot(
        self, fighter_id: str, fighter_name: str, image_path: Path, image_url: str
    ) -> Optional[Image.Image]:
        """Load and resize a fighter headshot, with caching and automatic download if missing."""
        self.logger.debug(f"Headshot path: {image_path}")
        if fighter_id in self._logo_cache:
            self.logger.debug(f"Using cached headshot for {fighter_name}")
            return self._logo_cache[fighter_id]

        try:
            if not image_path.exists():
                self.logger.info(
                    f"Headshot not found for {fighter_name} at {image_path}. Attempting to download."
                )

                if not self.logo_dir.exists():
                    self.logo_dir.mkdir(parents=True, exist_ok=True)

                response = self.session.get(image_url, headers=self.headers, timeout=120)
                response.raise_for_status()

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
                    return None

                with image_path.open(mode="wb") as f:
                    f.write(response.content)

            # Verify and convert the downloaded image to RGBA format
            try:
                with Image.open(image_path) as img:
                    if img.mode != "RGBA":
                        img = img.convert("RGBA")
                    img.save(image_path, "PNG")

                    self.logger.info(
                        f"Successfully downloaded and converted headshot for {fighter_name} -> {image_path.name}"
                    )
            except Exception as e:
                self.logger.error(
                    f"Downloaded file for {fighter_name} is not a valid image or conversion failed: {e}"
                )
                try:
                    image_path.unlink()
                except OSError:
                    pass
                return None

            if not image_path.exists():
                self.logger.error(
                    f"Headshot file still doesn't exist at {image_path} after download attempt"
                )
                return None

            logo = Image.open(image_path)
            if logo.mode != "RGBA":
                logo = logo.convert("RGBA")

            max_width = int(self.display_width * 1.5)
            max_height = int(self.display_height * 1.5)
            logo.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)
            self._logo_cache[fighter_id] = logo
            return logo

        except Exception as e:
            self.logger.error(
                f"Error loading headshot for {fighter_name}: {e}", exc_info=True
            )
            return None

    def _extract_game_details(self, game_event: dict) -> Dict | None:
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
                self.logger.warning(f"Could not parse game date: {game_date_str}")

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
                fighter1_name_short = fighter1["athlete"]["shortName"]
            except KeyError:
                fighter1_name = ""
                fighter1_name_short = ""
            try:
                fighter2_name = fighter2["athlete"]["fullName"]
                fighter2_name_short = fighter2["athlete"]["shortName"]
            except KeyError:
                fighter2_name = ""
                fighter2_name_short = ""

            # Check if this is a favorite fighter/weight class match before doing expensive logging
            is_favorite_game = (
                fighter1_name.lower() in self.favorite_fighters
                or fighter2_name.lower() in self.favorite_fighters
            ) or fight_class.lower() in self.favorite_weight_class

            if is_favorite_game:
                self.logger.debug(
                    f"Processing favorite fight: {competition.get('id')}"
                )
                self.logger.debug(
                    f"Found fighters: {fighter1_name} vs {fighter2_name}, Status: {status['type']['name']}, State: {status['type']['state']}"
                )

            game_time, game_date = "", ""
            if start_time_utc:
                local_time = start_time_utc.astimezone(self._get_timezone())
                game_time = local_time.strftime("%I:%M%p").lstrip("0")

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
                "comp_id": competition.get("id"),
                "id": competition.get("id"),
                "game_time": game_time,
                "game_date": game_date,
                "start_time_utc": start_time_utc,
                "status_text": status["type"]["shortDetail"],
                "is_live": status["type"]["state"] == "in",
                "is_final": status["type"]["state"] == "post",
                "is_upcoming": (
                    status["type"]["state"] == "pre"
                    or status["type"]["name"].lower()
                    in ["scheduled", "pre-game", "status_scheduled"]
                ),
                "is_period_break": status["type"]["name"] == "STATUS_END_PERIOD",
                "fight_class": fight_class,
                "fighter1_name": fighter1_name,
                "fighter1_name_short": fighter1_name_short,
                "fighter1_id": fighter1["id"],
                "fighter1_image_path": self.logo_dir
                / Path(f"{fighter1.get('id')}.png"),
                "fighter1_image_url": f"https://a.espncdn.com/combiner/i?img=/i/headshots/mma/players/full/{fighter1.get('id')}.png",
                "fighter1_country_url": fighter1.get("athlete", {})
                .get("flag", {})
                .get("href", ""),
                "fighter1_record": fighter1_record,
                "fighter2_name": fighter2_name,
                "fighter2_name_short": fighter2_name_short,
                "fighter2_id": fighter2["id"],
                "fighter2_image_path": self.logo_dir
                / Path(f"{fighter2.get('id')}.png"),
                "fighter2_image_url": f"https://a.espncdn.com/combiner/i?img=/i/headshots/mma/players/full/{fighter2.get('id')}.png",
                "fighter2_country_url": fighter2.get("athlete", {})
                .get("flag", {})
                .get("href", ""),
                "fighter2_record": fighter2_record,
                "is_within_window": True,
            }
            return details
        except Exception as e:
            self.logger.error(
                f"Error extracting game details: {e} from event: {game_event.get('id')}",
                exc_info=True,
            )
            return None


class MMARecent(MMA, SportsRecent):
    def __init__(
        self,
        config: Dict[str, Any],
        display_manager,
        cache_manager,
        logger: logging.Logger,
        sport_key: str,
    ):
        super().__init__(config, display_manager, cache_manager, logger, sport_key)

    def _draw_scorebug_layout(self, game: Dict, force_clear: bool = False) -> None:
        """Draw the layout for a recently completed MMA fight."""
        try:
            main_img = Image.new(
                "RGBA", (self.display_width, self.display_height), (0, 0, 0, 255)
            )
            overlay = Image.new(
                "RGBA", (self.display_width, self.display_height), (0, 0, 0, 0)
            )
            draw_overlay = ImageDraw.Draw(overlay)

            fighter1_image = self._load_and_resize_headshot(
                game["fighter1_id"],
                game["fighter1_name"],
                game["fighter1_image_path"],
                game["fighter1_image_url"],
            )
            fighter2_image = self._load_and_resize_headshot(
                game["fighter2_id"],
                game["fighter2_name"],
                game["fighter2_image_path"],
                game["fighter2_image_url"],
            )

            if not fighter1_image or not fighter2_image:
                self.logger.error(
                    f"Failed to load headshots for fight: {game.get('id')}"
                )
                draw_final = ImageDraw.Draw(main_img.convert("RGB"))
                self._draw_text_with_outline(
                    draw_final, "Image Error", (5, 5), self.fonts["status"]
                )
                self.display_manager.image.paste(main_img.convert("RGB"), (0, 0))
                self.display_manager.update_display()
                return

            center_y = self.display_height // 2

            # Fighter 1 (right side) headshot position
            home_x = (
                self.display_width
                - fighter1_image.width
                + fighter1_image.width // 4
                + 2
                + self._get_layout_offset("home_logo", "x_offset")
            )
            home_y = center_y - (fighter1_image.height // 2) + self._get_layout_offset("home_logo", "y_offset")
            main_img.paste(fighter1_image, (home_x, home_y), fighter1_image)

            # Fighter 2 (left side) headshot position
            away_x = -2 - fighter2_image.width // 4 + self._get_layout_offset("away_logo", "x_offset")
            away_y = center_y - (fighter2_image.height // 2) + self._get_layout_offset("away_logo", "y_offset")
            main_img.paste(fighter2_image, (away_x, away_y), fighter2_image)

            # Result text (centered bottom)
            score_text = game.get("status_text", "Final")
            score_width = draw_overlay.textlength(score_text, font=self.fonts["score"])
            score_x = (self.display_width - score_width) // 2 + self._get_layout_offset("score", "x_offset")
            score_y = self.display_height - 14 + self._get_layout_offset("score", "y_offset")
            self._draw_text_with_outline(
                draw_overlay, score_text, (score_x, score_y), self.fonts["score"]
            )

            # "Final" text (top center)
            status_text = game.get("period_text", "Final")
            status_width = draw_overlay.textlength(status_text, font=self.fonts["time"])
            status_x = (self.display_width - status_width) // 2 + self._get_layout_offset("status_text", "x_offset")
            status_y = 1 + self._get_layout_offset("status_text", "y_offset")
            self._draw_text_with_outline(
                draw_overlay, status_text, (status_x, status_y), self.fonts["time"]
            )

            if "odds" in game and game["odds"]:
                self._draw_dynamic_odds(
                    draw_overlay, game["odds"], self.display_width, self.display_height
                )

            # Draw records if enabled
            if self.show_records:
                try:
                    record_font = ImageFont.truetype("assets/fonts/4x6-font.ttf", 6)
                    self.logger.debug("Loaded 6px record font successfully")
                except IOError:
                    record_font = ImageFont.load_default()
                    self.logger.warning(
                        f"Failed to load 6px font, using default font (size: {record_font.size})"
                    )

                fighter1_record = game.get("fighter1_record", "")
                fighter2_record = game.get("fighter2_record", "")

                record_bbox = draw_overlay.textbbox((0, 0), "0-0-0", font=record_font)
                record_height = record_bbox[3] - record_bbox[1]
                record_y = self.display_height - record_height
                self.logger.debug(
                    f"Record positioning: height={record_height}, record_y={record_y}, display_height={self.display_height}"
                )

                # Display fighter 2 record (left side)
                if fighter2_record:
                    fighter2_text = fighter2_record
                    fighter2_record_x = 0
                    self.logger.debug(
                        f"Drawing fighter2 record '{fighter2_text}' at ({fighter2_record_x}, {record_y}) with font size {record_font.size if hasattr(record_font, 'size') else 'unknown'}"
                    )
                    self._draw_text_with_outline(
                        draw_overlay, fighter2_text, (fighter2_record_x, record_y), record_font
                    )

                # Display fighter 1 record (right side)
                if fighter1_record:
                    fighter1_text = fighter1_record
                    fighter1_record_bbox = draw_overlay.textbbox(
                        (0, 0), fighter1_text, font=record_font
                    )
                    fighter1_record_width = fighter1_record_bbox[2] - fighter1_record_bbox[0]
                    fighter1_record_x = self.display_width - fighter1_record_width
                    self.logger.debug(
                        f"Drawing fighter1 record '{fighter1_text}' at ({fighter1_record_x}, {record_y}) with font size {record_font.size if hasattr(record_font, 'size') else 'unknown'}"
                    )
                    self._draw_text_with_outline(
                        draw_overlay, fighter1_text, (fighter1_record_x, record_y), record_font
                    )

            self._custom_scorebug_layout(game, draw_overlay)

            # Composite and display
            main_img = Image.alpha_composite(main_img, overlay)
            main_img = main_img.convert("RGB")
            self.display_manager.image.paste(main_img, (0, 0))
            self.display_manager.update_display()

        except Exception as e:
            self.logger.error(
                f"Error displaying recent fight: {e}", exc_info=True
            )

    def update(self):
        """Update recent games data."""
        if not self.is_enabled:
            return
        current_time = time.time()
        if current_time - self.last_update < self.update_interval:
            return

        self.last_update = current_time

        try:
            data = self._fetch_data()
            if not data or "events" not in data:
                self.logger.warning("No events found in shared data.")
                if not self.games_list:
                    self.current_game = None
                return

            events = data["events"]
            self.logger.info(f"Processing {len(events)} events from shared data.")

            # Define date range for "recent" fights (last 21 days)
            now = datetime.now(timezone.utc)
            recent_cutoff = now - timedelta(days=21)
            self.logger.info(
                f"Current time: {now}, Recent cutoff: {recent_cutoff} (21 days ago)"
            )

            # Process games and filter for final fights within date range
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
                if game and game["is_final"]:
                    game_time = game.get("start_time_utc")
                    if game_time and game_time >= recent_cutoff:
                        processed_games.append(game)

            # Filter for favorite fighters or weight classes
            if self.favorite_fighters or self.favorite_weight_class:
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
                    f"Found {len(favorite_team_games)} favorite fighter games out of {len(processed_games)} total final games within last 21 days"
                )

                # Sort by game time (most recent first)
                favorite_team_games.sort(
                    key=lambda g: g.get("start_time_utc")
                    or datetime.min.replace(tzinfo=timezone.utc),
                    reverse=True,
                )

                # Select one fight per favorite fighter (most recent for each)
                team_games = []
                for fighter in self.favorite_fighters:
                    team_specific_games = [
                        game
                        for game in favorite_team_games
                        if (
                            game["fighter1_name"].lower() == fighter.lower()
                            or game["fighter2_name"].lower() == fighter.lower()
                        )
                    ]

                    if team_specific_games:
                        team_specific_games.sort(
                            key=lambda g: g.get("start_time_utc")
                            or datetime.min.replace(tzinfo=timezone.utc),
                            reverse=True,
                        )
                        team_games.append(team_specific_games[0])

                for wc in self.favorite_weight_class:
                    team_specific_games = [
                        game
                        for game in favorite_team_games
                        if game["fight_class"].lower() == wc.lower()
                    ]

                    if team_specific_games:
                        team_specific_games.sort(
                            key=lambda g: g.get("start_time_utc")
                            or datetime.min.replace(tzinfo=timezone.utc),
                            reverse=True,
                        )
                        team_games.append(team_specific_games[0])

                # Deduplicate by converting to set of ids and back
                seen_ids = set()
                unique_team_games = []
                for game in team_games:
                    if game["id"] not in seen_ids:
                        seen_ids.add(game["id"])
                        unique_team_games.append(game)
                team_games = unique_team_games

                for i, game in enumerate(team_games):
                    self.logger.info(
                        f"Fight {i+1} for display: {game['fighter2_name']} vs {game['fighter1_name']} - {game.get('start_time_utc')}"
                    )
            else:
                team_games = processed_games
                self.logger.info(
                    f"Found {len(processed_games)} total final games within last 21 days (no favorite fighters configured)"
                )
                team_games.sort(
                    key=lambda g: g.get("start_time_utc")
                    or datetime.min.replace(tzinfo=timezone.utc),
                    reverse=True,
                )
                team_games = team_games[: self.recent_games_to_show]

            # Check if the list of games to display has changed
            new_game_ids = {g["id"] for g in team_games}
            current_game_ids = {g["id"] for g in self.games_list}

            if new_game_ids != current_game_ids:
                self.logger.info(
                    f"Found {len(team_games)} final fights within window for display."
                )
                self.games_list = team_games
                if (
                    not self.current_game
                    or not self.games_list
                    or self.current_game["id"] not in new_game_ids
                ):
                    self.current_game_index = 0
                    self.current_game = self.games_list[0] if self.games_list else None
                    self.last_game_switch = current_time
                else:
                    try:
                        self.current_game_index = next(
                            i
                            for i, g in enumerate(self.games_list)
                            if g["id"] == self.current_game["id"]
                        )
                        self.current_game = self.games_list[self.current_game_index]
                    except StopIteration:
                        self.current_game_index = 0
                        self.current_game = self.games_list[0]
                        self.last_game_switch = current_time

            elif self.games_list:
                self.current_game = self.games_list[self.current_game_index]

            if not self.games_list:
                self.logger.info("No relevant recent fights found to display.")
                self.current_game = None

        except Exception as e:
            self.logger.error(
                f"Error updating recent fights: {e}", exc_info=True
            )


class MMAUpcoming(MMA, SportsUpcoming):
    def __init__(
        self,
        config: Dict[str, Any],
        display_manager,
        cache_manager,
        logger: logging.Logger,
        sport_key: str,
    ):
        super().__init__(config, display_manager, cache_manager, logger, sport_key)

    def _draw_scorebug_layout(self, game: Dict, force_clear: bool = False) -> None:
        """Draw the layout for an upcoming MMA fight."""
        try:
            main_img = Image.new(
                "RGBA", (self.display_width, self.display_height), (0, 0, 0, 255)
            )
            overlay = Image.new(
                "RGBA", (self.display_width, self.display_height), (0, 0, 0, 0)
            )
            draw_overlay = ImageDraw.Draw(overlay)

            fighter1_image = self._load_and_resize_headshot(
                game["fighter1_id"],
                game["fighter1_name"],
                game["fighter1_image_path"],
                game["fighter1_image_url"],
            )
            fighter2_image = self._load_and_resize_headshot(
                game["fighter2_id"],
                game["fighter2_name"],
                game["fighter2_image_path"],
                game["fighter2_image_url"],
            )

            if not fighter1_image or not fighter2_image:
                self.logger.error(
                    f"Failed to load headshots for fight: {game.get('id')}"
                )
                draw_final = ImageDraw.Draw(main_img.convert("RGB"))
                self._draw_text_with_outline(
                    draw_final, "Image Error", (5, 5), self.fonts["status"]
                )
                self.display_manager.image.paste(main_img.convert("RGB"), (0, 0))
                self.display_manager.update_display()
                return

            center_y = self.display_height // 2

            # Fighter 1 (right side) headshot position
            home_x = (
                self.display_width
                - fighter1_image.width
                + fighter1_image.width // 4
                + 2
                + self._get_layout_offset("home_logo", "x_offset")
            )
            home_y = center_y - (fighter1_image.height // 2) + self._get_layout_offset("home_logo", "y_offset")
            main_img.paste(fighter1_image, (home_x, home_y), fighter1_image)

            # Fighter 1 short name (top left)
            fighter1_name_text = game.get("fighter1_name_short", "")
            status_x = 1 + self._get_layout_offset("status_text", "x_offset")
            status_y = 1 + self._get_layout_offset("status_text", "y_offset")
            self._draw_text_with_outline(
                draw_overlay, fighter1_name_text, (status_x, status_y), self.fonts["odds"]
            )

            # Fighter 2 (left side) headshot position
            away_x = -2 - fighter2_image.width // 4 + self._get_layout_offset("away_logo", "x_offset")
            away_y = center_y - (fighter2_image.height // 2) + self._get_layout_offset("away_logo", "y_offset")
            main_img.paste(fighter2_image, (away_x, away_y), fighter2_image)

            # Fighter 2 short name (top right)
            fighter2_name_text = game.get("fighter2_name_short", "")
            fighter2_name_width = draw_overlay.textlength(
                fighter2_name_text, font=self.fonts["odds"]
            )
            fighter2_name_x = self.display_width - fighter2_name_width - 1
            fighter2_name_y = 1
            self._draw_text_with_outline(
                draw_overlay, fighter2_name_text, (fighter2_name_x, fighter2_name_y), self.fonts["odds"]
            )

            # Date and time display (centered bottom)
            game_date = game.get("game_date", "")
            game_time = game.get("game_time", "")
            if game_date and game_time:
                score_text = f"{game_date} {game_time}"
            elif game_time:
                score_text = game_time
            elif game_date:
                score_text = game_date
            else:
                score_text = game.get("status_text", "TBD")

            score_width = draw_overlay.textlength(score_text, font=self.fonts["score"])
            score_x = (self.display_width - score_width) // 2 + self._get_layout_offset("score", "x_offset")
            score_y = self.display_height - 14 + self._get_layout_offset("score", "y_offset")
            self._draw_text_with_outline(
                draw_overlay, score_text, (score_x, score_y), self.fonts["score"]
            )

            # Fight class / status text (top center)
            status_text = game.get("fight_class", game.get("status_text", ""))
            if status_text:
                status_width = draw_overlay.textlength(status_text, font=self.fonts["time"])
                status_center_x = (self.display_width - status_width) // 2
                status_center_y = 1
                self._draw_text_with_outline(
                    draw_overlay, status_text, (status_center_x, status_center_y), self.fonts["time"]
                )

            if "odds" in game and game["odds"]:
                self._draw_dynamic_odds(
                    draw_overlay, game["odds"], self.display_width, self.display_height
                )

            # Draw records if enabled
            if self.show_records:
                try:
                    record_font = ImageFont.truetype("assets/fonts/4x6-font.ttf", 6)
                    self.logger.debug("Loaded 6px record font successfully")
                except IOError:
                    record_font = ImageFont.load_default()
                    self.logger.warning(
                        f"Failed to load 6px font, using default font (size: {record_font.size})"
                    )

                fighter1_record = game.get("fighter1_record", "")
                fighter2_record = game.get("fighter2_record", "")

                record_bbox = draw_overlay.textbbox((0, 0), "0-0-0", font=record_font)
                record_height = record_bbox[3] - record_bbox[1]
                record_y = self.display_height - record_height
                self.logger.debug(
                    f"Record positioning: height={record_height}, record_y={record_y}, display_height={self.display_height}"
                )

                # Display fighter 2 record (left side)
                if fighter2_record:
                    fighter2_text = fighter2_record
                    fighter2_record_x = 0
                    self.logger.debug(
                        f"Drawing fighter2 record '{fighter2_text}' at ({fighter2_record_x}, {record_y}) with font size {record_font.size if hasattr(record_font, 'size') else 'unknown'}"
                    )
                    self._draw_text_with_outline(
                        draw_overlay, fighter2_text, (fighter2_record_x, record_y), record_font
                    )

                # Display fighter 1 record (right side)
                if fighter1_record:
                    fighter1_text = fighter1_record
                    fighter1_record_bbox = draw_overlay.textbbox(
                        (0, 0), fighter1_text, font=record_font
                    )
                    fighter1_record_width = fighter1_record_bbox[2] - fighter1_record_bbox[0]
                    fighter1_record_x = self.display_width - fighter1_record_width
                    self.logger.debug(
                        f"Drawing fighter1 record '{fighter1_text}' at ({fighter1_record_x}, {record_y}) with font size {record_font.size if hasattr(record_font, 'size') else 'unknown'}"
                    )
                    self._draw_text_with_outline(
                        draw_overlay, fighter1_text, (fighter1_record_x, record_y), record_font
                    )

            self._custom_scorebug_layout(game, draw_overlay)

            # Composite and display
            main_img = Image.alpha_composite(main_img, overlay)
            main_img = main_img.convert("RGB")
            self.display_manager.image.paste(main_img, (0, 0))
            self.display_manager.update_display()

        except Exception as e:
            self.logger.error(
                f"Error displaying upcoming fight: {e}", exc_info=True
            )

    def update(self):
        """Update upcoming games data."""
        if not self.is_enabled:
            return
        current_time = time.time()
        if current_time - self.last_update < self.update_interval:
            return

        self.last_update = current_time

        try:
            data = self._fetch_data()
            if not data or "events" not in data:
                self.logger.warning("No events found in shared data.")
                if not self.games_list:
                    self.current_game = None
                return

            events = data["events"]
            self.logger.info(f"Processing {len(events)} events from shared data.")

            processed_games = []
            all_upcoming_games = 0
            favorite_games_found = 0
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
                if game and game["is_upcoming"]:
                    all_upcoming_games += 1
                if game and game["is_upcoming"]:
                    if self.show_favorite_teams_only and (
                        len(self.favorite_fighters) > 0
                        or len(self.favorite_weight_class) > 0
                    ):
                        if (
                            not (
                                game["fighter1_name"].lower() in self.favorite_fighters
                                or game["fighter2_name"].lower()
                                in self.favorite_fighters
                            )
                            and not game["fight_class"].lower()
                            in self.favorite_weight_class
                        ):
                            continue
                        else:
                            favorite_games_found += 1
                    if self.show_odds:
                        self._fetch_odds(game)
                    processed_games.append(game)

            self.logger.info(f"Found {all_upcoming_games} total upcoming fights in data")
            self.logger.info(
                f"Found {len(processed_games)} upcoming fights after filtering"
            )

            if processed_games:
                for game in processed_games[:3]:
                    self.logger.info(
                        f"  {game['fighter1_name']} vs {game['fighter2_name']} - {game['start_time_utc']}"
                    )

            team_games = processed_games
            team_games.sort(
                key=lambda g: g.get("start_time_utc")
                or datetime.max.replace(tzinfo=timezone.utc)
            )
            team_games = team_games[: self.upcoming_games_to_show]

            should_log = (
                current_time - self.last_log_time >= self.log_interval
                or len(team_games) != len(self.games_list)
                or any(
                    g1["id"] != g2.get("id")
                    for g1, g2 in zip(self.games_list, team_games)
                )
                or (not self.games_list and team_games)
            )

            new_game_ids = {g["id"] for g in team_games}
            current_game_ids = {g["id"] for g in self.games_list}

            if new_game_ids != current_game_ids:
                self.logger.info(
                    f"Found {len(team_games)} upcoming fights within window for display."
                )
                self.games_list = team_games
                if (
                    not self.current_game
                    or not self.games_list
                    or self.current_game["id"] not in new_game_ids
                ):
                    self.current_game_index = 0
                    self.current_game = self.games_list[0] if self.games_list else None
                    self.last_game_switch = current_time
                else:
                    try:
                        self.current_game_index = next(
                            i
                            for i, g in enumerate(self.games_list)
                            if g["id"] == self.current_game["id"]
                        )
                        self.current_game = self.games_list[self.current_game_index]
                    except StopIteration:
                        self.current_game_index = 0
                        self.current_game = self.games_list[0]
                        self.last_game_switch = current_time

            elif self.games_list:
                self.current_game = self.games_list[self.current_game_index]

            if not self.games_list:
                self.logger.info("No relevant upcoming fights found to display.")
                self.current_game = None

            if should_log and not self.games_list:
                self.logger.debug(
                    f"Favorite fighters: {self.favorite_fighters}"
                )
                self.logger.debug(
                    f"Total upcoming fights before filtering: {len(processed_games)}"
                )
                self.last_log_time = current_time
            elif should_log:
                self.last_log_time = current_time

        except Exception as e:
            self.logger.error(
                f"Error updating upcoming fights: {e}", exc_info=True
            )


class MMALive(MMA, SportsLive):
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

    def _draw_scorebug_layout(self, game: Dict, force_clear: bool = False) -> None:
        """Draw the detailed scorebug layout for a live MMA fight."""
        try:
            main_img = Image.new(
                "RGBA", (self.display_width, self.display_height), (0, 0, 0, 255)
            )
            overlay = Image.new(
                "RGBA", (self.display_width, self.display_height), (0, 0, 0, 0)
            )
            draw_overlay = ImageDraw.Draw(overlay)

            fighter1_image = self._load_and_resize_headshot(
                game["fighter1_id"],
                game["fighter1_name"],
                game["fighter1_image_path"],
                game.get("fighter1_image_url"),
            )
            fighter2_image = self._load_and_resize_headshot(
                game["fighter2_id"],
                game["fighter2_name"],
                game["fighter2_image_path"],
                game.get("fighter2_image_url"),
            )

            if not fighter1_image or not fighter2_image:
                self.logger.error(
                    f"Failed to load headshots for live fight: {game.get('id')}"
                )
                draw_final = ImageDraw.Draw(main_img.convert("RGB"))
                self._draw_text_with_outline(
                    draw_final, "Image Error", (5, 5), self.fonts["status"]
                )
                self.display_manager.image.paste(main_img.convert("RGB"), (0, 0))
                self.display_manager.update_display()
                return

            center_y = self.display_height // 2

            # Fighter 1 (right side) headshot with layout offsets
            home_x = (
                self.display_width - fighter1_image.width + 10
                + self._get_layout_offset("home_logo", "x_offset")
            )
            home_y = center_y - (fighter1_image.height // 2) + self._get_layout_offset("home_logo", "y_offset")
            main_img.paste(fighter1_image, (home_x, home_y), fighter1_image)

            # Fighter 2 (left side) headshot with layout offsets
            away_x = -10 + self._get_layout_offset("away_logo", "x_offset")
            away_y = center_y - (fighter2_image.height // 2) + self._get_layout_offset("away_logo", "y_offset")
            main_img.paste(fighter2_image, (away_x, away_y), fighter2_image)

            # Round and Clock (top center)
            period_clock_text = (
                f"{game.get('period_text', '')} {game.get('clock', '')}".strip()
            )
            if game.get("is_period_break"):
                period_clock_text = game.get("status_text", "Round Break")

            status_width = draw_overlay.textlength(
                period_clock_text, font=self.fonts["time"]
            )
            status_x = (self.display_width - status_width) // 2 + self._get_layout_offset("status_text", "x_offset")
            status_y = 1 + self._get_layout_offset("status_text", "y_offset")
            self._draw_text_with_outline(
                draw_overlay,
                period_clock_text,
                (status_x, status_y),
                self.fonts["time"],
            )

            # Scores (centered)
            home_score = str(game.get("home_score", "0"))
            away_score = str(game.get("away_score", "0"))
            score_text = f"{away_score}-{home_score}"
            score_width = draw_overlay.textlength(score_text, font=self.fonts["score"])
            score_x = (self.display_width - score_width) // 2 + self._get_layout_offset("score", "x_offset")
            score_y = (self.display_height // 2) - 3 + self._get_layout_offset("score", "y_offset")
            self._draw_text_with_outline(
                draw_overlay, score_text, (score_x, score_y), self.fonts["score"]
            )

            # Draw odds if available
            if "odds" in game and game["odds"]:
                self._draw_dynamic_odds(
                    draw_overlay, game["odds"], self.display_width, self.display_height
                )

            # Draw records if enabled
            if self.show_records:
                try:
                    record_font = ImageFont.truetype("assets/fonts/4x6-font.ttf", 6)
                    self.logger.debug("Loaded 6px record font successfully")
                except IOError:
                    record_font = ImageFont.load_default()
                    self.logger.warning(
                        f"Failed to load 6px font, using default font (size: {record_font.size})"
                    )

                fighter2_name = game.get("fighter2_name", "")
                fighter1_name = game.get("fighter1_name", "")

                record_bbox = draw_overlay.textbbox((0, 0), "0-0", font=record_font)
                record_height = record_bbox[3] - record_bbox[1]
                record_y = self.display_height - record_height - 1
                self.logger.debug(
                    f"Record positioning: height={record_height}, record_y={record_y}, display_height={self.display_height}"
                )

                # Display fighter 2 record (left side)
                if fighter2_name:
                    fighter2_record = game.get("fighter2_record", "")
                    if fighter2_record:
                        fighter2_text = fighter2_record
                        fighter2_record_x = 3
                        self.logger.debug(
                            f"Drawing fighter2 record '{fighter2_text}' at ({fighter2_record_x}, {record_y}) with font size {record_font.size if hasattr(record_font, 'size') else 'unknown'}"
                        )
                        self._draw_text_with_outline(
                            draw_overlay,
                            fighter2_text,
                            (fighter2_record_x, record_y),
                            record_font,
                        )

                # Display fighter 1 record (right side)
                if fighter1_name:
                    fighter1_record = game.get("fighter1_record", "")
                    if fighter1_record:
                        fighter1_text = fighter1_record
                        fighter1_record_bbox = draw_overlay.textbbox(
                            (0, 0), fighter1_text, font=record_font
                        )
                        fighter1_record_width = fighter1_record_bbox[2] - fighter1_record_bbox[0]
                        fighter1_record_x = self.display_width - fighter1_record_width - 3
                        self.logger.debug(
                            f"Drawing fighter1 record '{fighter1_text}' at ({fighter1_record_x}, {record_y}) with font size {record_font.size if hasattr(record_font, 'size') else 'unknown'}"
                        )
                        self._draw_text_with_outline(
                            draw_overlay,
                            fighter1_text,
                            (fighter1_record_x, record_y),
                            record_font,
                        )

            # Composite the text overlay onto the main image
            main_img = Image.alpha_composite(main_img, overlay)
            main_img = main_img.convert("RGB")

            # Display the final image
            self.display_manager.image.paste(main_img, (0, 0))
            self.display_manager.update_display()

        except Exception as e:
            self.logger.error(
                f"Error displaying live MMA fight: {e}", exc_info=True
            )
