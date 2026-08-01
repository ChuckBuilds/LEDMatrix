"""The three display modes layered on SportsCore: SportsUpcoming,
SportsRecent and SportsLive. Split out of the former
``src/base_classes/sports.py``; see docs/SPORTS_UNIFICATION.md.
"""

import logging
import time
from abc import abstractmethod
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from PIL import Image, ImageDraw, ImageFont

from src.cache_manager import CacheManager
from src.display_manager import DisplayManager

from .core import SportsCore


class SportsUpcoming(SportsCore):
    SKIN_MODE = "upcoming"

    def __init__(self, config: Dict[str, Any], display_manager: DisplayManager, cache_manager: CacheManager, logger: logging.Logger, sport_key: str):
        super().__init__(config, display_manager, cache_manager, logger, sport_key)
        self.upcoming_games = [] # Store all fetched upcoming games initially
        self.games_list = [] # Filtered list for display (favorite teams)
        self.current_game_index = 0
        self.last_update = 0
        self.update_interval = self.mode_config.get("upcoming_update_interval", 3600) # Check for recent games every hour
        self.last_log_time = 0
        self.log_interval = 300
        self.last_warning_time = 0
        self.warning_cooldown = 300
        self.last_game_switch = 0
        self.game_display_duration = 15 # Display each upcoming game for 15 seconds

    def _select_games_for_display(
        self, processed_games: List[Dict], favorite_teams: List[str]
    ) -> List[Dict]:
        """
        Single-pass game selection with proper deduplication and counting.

        When a game involves two favorite teams, it counts toward BOTH teams' limits.
        This prevents unexpected game counts from the multi-pass algorithm.

        Team identity goes through the ``_favorite_key`` override point rather
        than reading ``home_abbr``/``away_abbr`` directly, because abbreviations
        are not unique in every league (NRL matches on team ID instead).
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
        team_counts = {team: 0 for team in favorite_teams}

        for game in sorted_games:
            game_id = game.get("id")
            if game_id in selected_ids:
                continue

            home = self._favorite_key(game, "home")
            away = self._favorite_key(game, "away")

            home_fav = home in favorite_teams
            away_fav = away in favorite_teams

            if not home_fav and not away_fav:
                continue

            home_needs = home_fav and team_counts[home] < self.upcoming_games_to_show
            away_needs = away_fav and team_counts[away] < self.upcoming_games_to_show

            if home_needs or away_needs:
                selected_games.append(game)
                selected_ids.add(game_id)
                if home_fav:
                    team_counts[home] += 1
                if away_fav:
                    team_counts[away] += 1

                self.logger.debug(
                    f"Selected game {away}@{home}: team_counts={team_counts}"
                )

            if all(c >= self.upcoming_games_to_show for c in team_counts.values()):
                self.logger.debug("All favorite teams satisfied, stopping selection")
                break

        self.logger.info(
            f"Selected {len(selected_games)} games for {len(favorite_teams)} "
            f"favorite teams: {team_counts}"
        )
        return selected_games

    def update(self):
        """Update upcoming games data."""
        if not self.is_enabled: return
        current_time = time.time()
        if current_time - self.last_update < self.update_interval:
            return

        self.last_update = current_time
        
        # Fetch rankings if enabled
        if self.show_ranking:
            self._fetch_team_rankings()
        
        try:
            data = self._fetch_data() # Uses shared cache
            if not data or 'events' not in data:
                self.logger.warning("No events found in shared data.") # Changed log prefix
                if not self.games_list: self.current_game = None
                return

            events = data['events']
            # self.logger.info(f"Processing {len(events)} events from shared data.") # Changed log prefix

            processed_games = []
            favorite_games_found = 0
            all_upcoming_games = 0  # Count all upcoming games regardless of favorites
            
            for event in events:
                game = self._extract_game_details(event)
                # Count all upcoming games for debugging
                if game and game['is_upcoming']:
                    all_upcoming_games += 1
                    
                # Filter criteria: must be upcoming ('pre' state)
                if game and game['is_upcoming']:
                    # Only fetch odds for games that will be displayed
                    if self.show_favorite_teams_only:
                        if not self.favorite_teams:
                            continue
                        if game['home_abbr'] not in self.favorite_teams and game['away_abbr'] not in self.favorite_teams:
                            continue
                    processed_games.append(game)
                    # Count favorite team games for logging
                    if (game['home_abbr'] in self.favorite_teams or 
                        game['away_abbr'] in self.favorite_teams):
                        favorite_games_found += 1
                    if self.show_odds:
                        self._fetch_odds(game)

            # Enhanced logging for debugging
            self.logger.info(f"Found {all_upcoming_games} total upcoming games in data")
            self.logger.info(f"Found {len(processed_games)} upcoming games after filtering")

            if processed_games:
                for game in processed_games[:3]:  # Show first 3
                    self.logger.info(f"  {game['away_abbr']}@{game['home_abbr']} - {game['start_time_utc']}")
            
            if self.favorite_teams and all_upcoming_games > 0:
                self.logger.info(f"Favorite teams: {self.favorite_teams}")
                self.logger.info(f"Found {favorite_games_found} favorite team upcoming games")

            # Filter for favorite teams only if the config is set
            if self.show_favorite_teams_only:
                # Select N games per favorite team (where N = upcoming_games_to_show)
                # Example: upcoming_games_to_show=2 with 3 favorite teams = up to 6 games total
                team_games = []
                for team in self.favorite_teams:
                    # Find games where this team is playing                  
                    if team_specific_games := [game for game in processed_games if game['home_abbr'] == team or game['away_abbr'] == team]:
                        # Sort by game time and take the earliest N games
                        team_specific_games.sort(key=lambda g: g.get('start_time_utc') or datetime.max.replace(tzinfo=timezone.utc))
                        # Take up to upcoming_games_to_show games for this team
                        team_games.extend(team_specific_games[:self.upcoming_games_to_show])
                
                # Sort the final list by game time (earliest first)
                team_games.sort(key=lambda g: g.get('start_time_utc') or datetime.max.replace(tzinfo=timezone.utc))
                # Remove duplicates (in case a game involves multiple favorite teams)
                seen_ids = set()
                unique_team_games = []
                for game in team_games:
                    if game['id'] not in seen_ids:
                        seen_ids.add(game['id'])
                        unique_team_games.append(game)
                team_games = unique_team_games
            else:
                team_games = processed_games # Show all upcoming if no favorites
                # Sort by game time, earliest first
                team_games.sort(key=lambda g: g.get('start_time_utc') or datetime.max.replace(tzinfo=timezone.utc))
                # Limit to the specified number of upcoming games
                team_games = team_games[:self.upcoming_games_to_show]

            # Log changes or periodically
            should_log = (
                 current_time - self.last_log_time >= self.log_interval or
                 len(team_games) != len(self.games_list) or
                 any(g1['id'] != g2.get('id') for g1, g2 in zip(self.games_list, team_games)) or
                 (not self.games_list and team_games)
             )

            # Check if the list of games to display has changed
            new_game_ids = {g['id'] for g in team_games}
            current_game_ids = {g['id'] for g in self.games_list}

            if new_game_ids != current_game_ids:
                 self.logger.info(f"Found {len(team_games)} upcoming games within window for display.") # Changed log prefix
                 self.games_list = team_games
                 if not self.current_game or not self.games_list or self.current_game['id'] not in new_game_ids:
                      self.current_game_index = 0
                      self.current_game = self.games_list[0] if self.games_list else None
                      self.last_game_switch = current_time
                 else:
                      try:
                           self.current_game_index = next(i for i, g in enumerate(self.games_list) if g['id'] == self.current_game['id'])
                           self.current_game = self.games_list[self.current_game_index]
                      except StopIteration:
                           self.current_game_index = 0
                           self.current_game = self.games_list[0]
                           self.last_game_switch = current_time

            elif self.games_list:
                 self.current_game = self.games_list[self.current_game_index] # Update data

            if not self.games_list:
                 self.logger.info("No relevant upcoming games found to display.") # Changed log prefix
                 self.current_game = None

            if should_log and not self.games_list:
                 # Log favorite teams only if no games are found and logging is needed
                 self.logger.debug(f"Favorite teams: {self.favorite_teams}") # Changed log prefix
                 self.logger.debug(f"Total upcoming games before filtering: {len(processed_games)}") # Changed log prefix
                 self.last_log_time = current_time
            elif should_log:
                self.last_log_time = current_time

        except Exception as e:
            self.logger.error(f"Error updating upcoming games: {e}", exc_info=True) # Changed log prefix
            # self.current_game = None # Decide if clear on error

    def _draw_scorebug_layout(self, game: Dict, force_clear: bool = False) -> None:
        """Draw the layout for an upcoming NCAA FB game.""" # Updated docstring
        try:
            main_img = Image.new('RGBA', (self.display_width, self.display_height), (0, 0, 0, 255))
            overlay = Image.new('RGBA', (self.display_width, self.display_height), (0, 0, 0, 0))
            draw_overlay = ImageDraw.Draw(overlay)

            home_logo = self._load_and_resize_logo(game["home_id"], game["home_abbr"], game["home_logo_path"], game.get("home_logo_url"))
            away_logo = self._load_and_resize_logo(game["away_id"], game["away_abbr"], game["away_logo_path"], game.get("away_logo_url"))

            if not home_logo or not away_logo:
                self.logger.error(f"Failed to load logos for game: {game.get('id')}") # Changed log prefix
                draw_final = ImageDraw.Draw(main_img.convert('RGB'))
                self._draw_text_with_outline(draw_final, "Logo Error", (5,5), self.fonts['status'])
                self.display_manager.image.paste(main_img.convert('RGB'), (0, 0))
                self.display_manager.update_display()
                return

            center_y = self.display_height // 2

            # MLB-style logo positions
            home_x = self.display_width - home_logo.width + 2
            home_y = center_y - (home_logo.height // 2)
            main_img.paste(home_logo, (home_x, home_y), home_logo)

            away_x = -2
            away_y = center_y - (away_logo.height // 2)
            main_img.paste(away_logo, (away_x, away_y), away_logo)

            # Draw Text Elements on Overlay
            game_date = game.get("game_date", "")
            game_time = game.get("game_time", "")

            # Note: Rankings are now handled in the records/rankings section below

            # "Next Game" at the top (use smaller status font)
            status_font = self.fonts['status']
            if self.display_width > 128:
                status_font = self.fonts['time']
            status_text = "Next Game"
            status_width = draw_overlay.textlength(status_text, font=status_font)
            status_x = (self.display_width - status_width) // 2
            status_y = 1 # Changed from 2
            self._draw_text_with_outline(draw_overlay, status_text, (status_x, status_y), status_font)

            # Date text (centered, below "Next Game")
            date_width = draw_overlay.textlength(game_date, font=self.fonts['time'])
            date_x = (self.display_width - date_width) // 2
            # Adjust Y position to stack date and time nicely
            date_y = center_y - 7 # Raise date slightly
            self._draw_text_with_outline(draw_overlay, game_date, (date_x, date_y), self.fonts['time'])

            # Time text (centered, below Date)
            time_width = draw_overlay.textlength(game_time, font=self.fonts['time'])
            time_x = (self.display_width - time_width) // 2
            time_y = date_y + 9 # Place time below date
            self._draw_text_with_outline(draw_overlay, game_time, (time_x, time_y), self.fonts['time'])

            # Draw odds if available
            if 'odds' in game and game['odds']:
                self._draw_dynamic_odds(draw_overlay, game['odds'], self.display_width, self.display_height)

            # Draw records or rankings if enabled
            if self.show_records or self.show_ranking:
                record_font = self.fonts.get('detail', ImageFont.load_default())

                # Get team abbreviations
                away_abbr = game.get('away_abbr', '')
                home_abbr = game.get('home_abbr', '')
                
                record_bbox = draw_overlay.textbbox((0,0), "0-0", font=record_font)
                record_height = record_bbox[3] - record_bbox[1]
                record_y = self.display_height - record_height
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
                        away_record_x = 0
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
                        home_record_x = self.display_width - home_record_width
                        self.logger.debug(f"Drawing home ranking '{home_text}' at ({home_record_x}, {record_y}) with font size {record_font.size if hasattr(record_font, 'size') else 'unknown'}")
                        self._draw_text_with_outline(draw_overlay, home_text, (home_record_x, record_y), record_font)

            # Composite and display
            main_img = Image.alpha_composite(main_img, overlay)
            main_img = main_img.convert('RGB')
            self.display_manager.image.paste(main_img, (0, 0))
            self.display_manager.update_display() # Update display here

        except Exception as e:
            self.logger.error(f"Error displaying upcoming game: {e}", exc_info=True) # Changed log prefix

    def display(self, force_clear=False) -> bool:
        """Display upcoming games, handling switching."""
        if not self.is_enabled: return False

        if not self.games_list:
            if self.current_game: self.current_game = None # Clear state if list empty
            current_time = time.time()
            # Log warning periodically if no games found
            if current_time - self.last_warning_time > self.warning_cooldown:
                self.logger.info("No upcoming games found for favorite teams to display.") # Changed log prefix
                self.last_warning_time = current_time
            return False # Skip display update

        try:
            current_time = time.time()

            # Check if it's time to switch games
            if len(self.games_list) > 1 and current_time - self.last_game_switch >= self.game_display_duration:
                self.current_game_index = (self.current_game_index + 1) % len(self.games_list)
                self.current_game = self.games_list[self.current_game_index]
                self.last_game_switch = current_time
                force_clear = True # Force redraw on switch
                
                # Log team switching with sport prefix
                if self.current_game:
                    away_abbr = self.current_game.get('away_abbr', 'UNK')
                    home_abbr = self.current_game.get('home_abbr', 'UNK')
                    sport_prefix = self.sport_key.upper() if hasattr(self, 'sport_key') else 'SPORT'
                    self.logger.info(f"[{sport_prefix} Upcoming] Showing {away_abbr} vs {home_abbr}")
                else:
                    self.logger.debug(f"Switched to game index {self.current_game_index}")

            if self.current_game:
                self._render_game(self.current_game, force_clear)
                return True
            # update_display() is called within _draw_scorebug_layout for upcoming
            return False

        except Exception as e:
            self.logger.error(f"Error in display loop: {e}", exc_info=True) # Changed log prefix
            return False


class SportsRecent(SportsCore):
    SKIN_MODE = "recent"

    def __init__(self, config: Dict[str, Any], display_manager: DisplayManager, cache_manager: CacheManager, logger: logging.Logger, sport_key: str):
        super().__init__(config, display_manager, cache_manager, logger, sport_key)
        self.recent_games = [] # Store all fetched recent games initially
        self.games_list = [] # Filtered list for display (favorite teams)
        self.current_game_index = 0
        self.last_update = 0
        self.update_interval = self.mode_config.get("recent_update_interval", 3600) # Check for recent games every hour
        self.last_game_switch = 0
        self.game_display_duration = 15 # Display each recent game for 15 seconds
        # Tracks when each game was first seen with an expired clock, keyed by
        # game id. Promoted alongside the zero-clock helpers below; without it
        # the first _get_zero_clock_duration() call raises AttributeError.
        self._zero_clock_timestamps: Dict[str, float] = {}  # Track games at 0:00

    # -- Zero-clock tracking ------------------------------------------------
    # Byte-identical in all nine plugin copies. Note that afl/nrl/soccer define
    # these but never call them — their clocks count up, so 0:00 means kickoff
    # rather than expiry (see CLOCK_COUNTS_DOWN on SportsLive). That makes the
    # pair a future `CountdownClockMixin` candidate so it stops appearing in the
    # MRO of sports that cannot use it — B2 work, not now.

    def _get_zero_clock_duration(self, game_id: str) -> float:
        """Track how long a game has been at 0:00 clock."""
        current_time = time.time()
        if game_id not in self._zero_clock_timestamps:
            self._zero_clock_timestamps[game_id] = current_time
            return 0.0
        return current_time - self._zero_clock_timestamps[game_id]

    def _clear_zero_clock_tracking(self, game_id: str) -> None:
        """Clear tracking when game clock moves away from 0:00 or game ends."""
        if game_id in self._zero_clock_timestamps:
            del self._zero_clock_timestamps[game_id]

    def _select_recent_games_for_display(
        self, processed_games: List[Dict], favorite_teams: List[str]
    ) -> List[Dict]:
        """
        Single-pass game selection for recent games with proper deduplication.

        When a game involves two favorite teams, it counts toward BOTH teams' limits.
        Games are sorted by most recent first.

        Team identity goes through the ``_favorite_key`` override point rather
        than reading ``home_abbr``/``away_abbr`` directly, because abbreviations
        are not unique in every league (NRL matches on team ID instead).
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
        team_counts = {team: 0 for team in favorite_teams}

        for game in sorted_games:
            game_id = game.get("id")
            if game_id in selected_ids:
                continue

            home = self._favorite_key(game, "home")
            away = self._favorite_key(game, "away")

            home_fav = home in favorite_teams
            away_fav = away in favorite_teams

            if not home_fav and not away_fav:
                continue

            home_needs = home_fav and team_counts[home] < self.recent_games_to_show
            away_needs = away_fav and team_counts[away] < self.recent_games_to_show

            if home_needs or away_needs:
                selected_games.append(game)
                selected_ids.add(game_id)
                if home_fav:
                    team_counts[home] += 1
                if away_fav:
                    team_counts[away] += 1

                self.logger.debug(
                    f"Selected recent game {away}@{home}: team_counts={team_counts}"
                )

            if all(c >= self.recent_games_to_show for c in team_counts.values()):
                self.logger.debug("All favorite teams satisfied, stopping selection")
                break

        self.logger.info(
            f"Selected {len(selected_games)} recent games for {len(favorite_teams)} "
            f"favorite teams: {team_counts}"
        )
        return selected_games

    def update(self):
        """Update recent games data."""
        if not self.is_enabled: return
        current_time = time.time()
        if current_time - self.last_update < self.update_interval:
            return

        self.last_update = current_time # Update time even if fetch fails
        
        # Fetch rankings if enabled
        if self.show_ranking:
            self._fetch_team_rankings()
        
        try:
            data = self._fetch_data() # Uses shared cache
            if not data or 'events' not in data:
                self.logger.warning("No events found in shared data.") # Changed log prefix
                if not self.games_list: 
                    self.current_game = None # Clear display if no games were showing
                return

            events = data['events']
            self.logger.info(f"Processing {len(events)} events from shared data.") # Changed log prefix

            # Define date range for "recent" games (last 21 days to capture games from 3 weeks ago)
            now = datetime.now(timezone.utc)
            recent_cutoff = now - timedelta(days=21)
            self.logger.info(f"Current time: {now}, Recent cutoff: {recent_cutoff} (21 days ago)")
            
            # Process games and filter for final games, date range & favorite teams
            processed_games = []
            for event in events:
                game = self._extract_game_details(event)
                # Filter criteria: must be final AND within recent date range
                if game and game['is_final']:
                    game_time = game.get('start_time_utc')
                    if game_time and game_time >= recent_cutoff:
                        processed_games.append(game)
            # Filter for favorite teams only if the config is set
            if self.show_favorite_teams_only:
                # Get all games involving favorite teams
                favorite_team_games = [game for game in processed_games
                                      if game['home_abbr'] in self.favorite_teams or
                                         game['away_abbr'] in self.favorite_teams]
                self.logger.info(f"Found {len(favorite_team_games)} favorite team games out of {len(processed_games)} total final games within last 21 days")
                
                # Select N games per favorite team (where N = recent_games_to_show)
                # Example: recent_games_to_show=1 with 2 favorite teams = 2 games total
                team_games = []
                for team in self.favorite_teams:
                    # Find games where this team is playing
                    team_specific_games = [game for game in favorite_team_games
                                          if game['home_abbr'] == team or game['away_abbr'] == team]
                    
                    if team_specific_games:
                        # Sort by game time and take the most recent N games
                        team_specific_games.sort(key=lambda g: g.get('start_time_utc') or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
                        # Take up to recent_games_to_show games for this team
                        team_games.extend(team_specific_games[:self.recent_games_to_show])
                
                # Sort the final list by game time (most recent first)
                team_games.sort(key=lambda g: g.get('start_time_utc') or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
                # Remove duplicates (in case a game involves multiple favorite teams)
                seen_ids = set()
                unique_team_games = []
                for game in team_games:
                    if game['id'] not in seen_ids:
                        seen_ids.add(game['id'])
                        unique_team_games.append(game)
                team_games = unique_team_games
                
                # Debug: Show which games are selected for display
                for i, game in enumerate(team_games):
                    self.logger.info(f"Game {i+1} for display: {game['away_abbr']} @ {game['home_abbr']} - {game.get('start_time_utc')} - Score: {game['away_score']}-{game['home_score']}")
            else:
                team_games = processed_games # Show all recent games if no favorites defined
                self.logger.info(f"Found {len(processed_games)} total final games within last 21 days (no favorite teams filtering)")
                # Sort games by start time, most recent first, and limit to recent_games_to_show
                team_games.sort(key=lambda g: g.get('start_time_utc') or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
                team_games = team_games[:self.recent_games_to_show]

            # Check if the list of games to display has changed
            new_game_ids = {g['id'] for g in team_games}
            current_game_ids = {g['id'] for g in self.games_list}

            if new_game_ids != current_game_ids:
                self.logger.info(f"Found {len(team_games)} final games within window for display.") # Changed log prefix
                self.games_list = team_games
                # Reset index if list changed or current game removed
                if not self.current_game or not self.games_list or self.current_game['id'] not in new_game_ids:
                     self.current_game_index = 0
                     self.current_game = self.games_list[0] if self.games_list else None
                     self.last_game_switch = current_time # Reset switch timer
                else:
                     # Try to maintain position if possible
                     try:
                          self.current_game_index = next(i for i, g in enumerate(self.games_list) if g['id'] == self.current_game['id'])
                          self.current_game = self.games_list[self.current_game_index] # Update data just in case
                     except StopIteration:
                          self.current_game_index = 0
                          self.current_game = self.games_list[0]
                          self.last_game_switch = current_time

            elif self.games_list:
                 # List content is same, just update data for current game
                 self.current_game = self.games_list[self.current_game_index]

            if not self.games_list:
                 self.logger.info("No relevant recent games found to display.") # Changed log prefix
                 self.current_game = None # Ensure display clears if no games

        except Exception as e:
            self.logger.error(f"Error updating recent games: {e}", exc_info=True) # Changed log prefix
            # Don't clear current game on error, keep showing last known state
            # self.current_game = None # Decide if we want to clear display on error

    def _draw_scorebug_layout(self, game: Dict, force_clear: bool = False) -> None:
        """Draw the layout for a recently completed NCAA FB game.""" # Updated docstring
        try:
            main_img = Image.new('RGBA', (self.display_width, self.display_height), (0, 0, 0, 255))
            overlay = Image.new('RGBA', (self.display_width, self.display_height), (0, 0, 0, 0))
            draw_overlay = ImageDraw.Draw(overlay)

            home_logo = self._load_and_resize_logo(game["home_id"], game["home_abbr"], game["home_logo_path"], game.get("home_logo_url"))
            away_logo = self._load_and_resize_logo(game["away_id"], game["away_abbr"], game["away_logo_path"], game.get("away_logo_url"))

            if not home_logo or not away_logo:
                self.logger.error(f"Failed to load logos for game: {game.get('id')}") # Changed log prefix
                # Draw placeholder text if logos fail (similar to live)
                draw_final = ImageDraw.Draw(main_img.convert('RGB'))
                self._draw_text_with_outline(draw_final, "Logo Error", (5,5), self.fonts['status'])
                self.display_manager.image.paste(main_img.convert('RGB'), (0, 0))
                self.display_manager.update_display()
                return

            center_y = self.display_height // 2

            # MLB-style logo positioning (closer to edges)
            home_x = self.display_width - home_logo.width + 2
            home_y = center_y - (home_logo.height // 2)
            main_img.paste(home_logo, (home_x, home_y), home_logo)

            away_x = -2
            away_y = center_y - (away_logo.height // 2)
            main_img.paste(away_logo, (away_x, away_y), away_logo)

            # Draw Text Elements on Overlay
            # Note: Rankings are now handled in the records/rankings section below

            # Final Scores (Centered, same position as live)
            home_score = str(game.get("home_score", "0"))
            away_score = str(game.get("away_score", "0"))
            score_text = f"{away_score}-{home_score}"
            score_width = draw_overlay.textlength(score_text, font=self.fonts['score'])
            score_x = (self.display_width - score_width) // 2
            score_y = self.display_height - 14
            self._draw_text_with_outline(draw_overlay, score_text, (score_x, score_y), self.fonts['score'])

            # "Final" text (Top center)
            status_text = game.get("period_text", "Final") # Use formatted period text (e.g., "Final/OT") or default "Final"
            status_width = draw_overlay.textlength(status_text, font=self.fonts['time'])
            status_x = (self.display_width - status_width) // 2
            status_y = 1
            self._draw_text_with_outline(draw_overlay, status_text, (status_x, status_y), self.fonts['time'])

            # Draw odds if available
            if 'odds' in game and game['odds']:
                self._draw_dynamic_odds(draw_overlay, game['odds'], self.display_width, self.display_height)

            # Draw records or rankings if enabled
            if self.show_records or self.show_ranking:
                record_font = self.fonts.get('detail', ImageFont.load_default())

                # Get team abbreviations
                away_abbr = game.get('away_abbr', '')
                home_abbr = game.get('home_abbr', '')
                
                record_bbox = draw_overlay.textbbox((0,0), "0-0", font=record_font)
                record_height = record_bbox[3] - record_bbox[1]
                record_y = self.display_height - record_height
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
                        away_record_x = 0
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
                        home_record_x = self.display_width - home_record_width
                        self.logger.debug(f"Drawing home ranking '{home_text}' at ({home_record_x}, {record_y}) with font size {record_font.size if hasattr(record_font, 'size') else 'unknown'}")
                        self._draw_text_with_outline(draw_overlay, home_text, (home_record_x, record_y), record_font)

            self._custom_scorebug_layout(game, draw_overlay)
            # Composite and display
            main_img = Image.alpha_composite(main_img, overlay)
            main_img = main_img.convert('RGB')
            self.display_manager.image.paste(main_img, (0, 0))
            self.display_manager.update_display() # Update display here

        except Exception as e:
            self.logger.error(f"Error displaying recent game: {e}", exc_info=True) # Changed log prefix

    def display(self, force_clear=False) -> bool:
        """Display recent games, handling switching."""
        if not self.is_enabled or not self.games_list:
            # If disabled or no games, ensure display might be cleared by main loop if needed
            # Or potentially clear it here? For now, rely on main loop/other managers.
            if not self.games_list and self.current_game:
                 self.current_game = None # Clear internal state if list becomes empty
            return False

        try:
            current_time = time.time()

            # Check if it's time to switch games
            if len(self.games_list) > 1 and current_time - self.last_game_switch >= self.game_display_duration:
                self.current_game_index = (self.current_game_index + 1) % len(self.games_list)
                self.current_game = self.games_list[self.current_game_index]
                self.last_game_switch = current_time
                force_clear = True # Force redraw on switch
                
                # Log team switching with sport prefix
                if self.current_game:
                    away_abbr = self.current_game.get('away_abbr', 'UNK')
                    home_abbr = self.current_game.get('home_abbr', 'UNK')
                    sport_prefix = self.sport_key.upper() if hasattr(self, 'sport_key') else 'SPORT'
                    self.logger.info(f"[{sport_prefix} Recent] Showing {away_abbr} vs {home_abbr}")
                else:
                    self.logger.debug(f"Switched to game index {self.current_game_index}")

            if self.current_game:
                self._render_game(self.current_game, force_clear)
                return True
            # update_display() is called within _draw_scorebug_layout for recent
            return False

        except Exception as e:
            self.logger.error(f"Error in display loop: {e}", exc_info=True) # Changed log prefix
            return False

class SportsLive(SportsCore):
    # Per-sport constants for the "is this live game actually over?" check.
    # These are values, not behavior, so they are class attributes rather than
    # override points (see docs/SPORTS_UNIFICATION.md "Override points").
    #
    # FINAL_PERIOD: the period at/after which an expired clock can mean "over".
    #   4 for four-quarter sports; hockey overrides to 3.
    # CLOCK_COUNTS_DOWN: whether "0:00" means the clock expired. False for
    #   sports whose clock counts up (soccer/afl/nrl), where 0:00 is kickoff —
    #   running the expiry branch there would evict games that just started.
    FINAL_PERIOD = 4
    CLOCK_COUNTS_DOWN = True

    def __init__(self, config: Dict[str, Any], display_manager: DisplayManager, cache_manager: CacheManager, logger: logging.Logger, sport_key: str):
        super().__init__(config, display_manager, cache_manager, logger, sport_key)
        self.update_interval = self.mode_config.get("live_update_interval", 15)
        self.no_data_interval = 300
        self.last_update = 0
        self.live_games = []
        self.current_game_index = 0
        self.last_game_switch = 0  # Will be set to current_time when games are first loaded
        self.game_display_duration = self.mode_config.get("live_game_duration", 20)
        self.last_display_update = 0
        self.last_log_time = 0
        self.log_interval = 300
        self.last_count_log_time = 0  # Track when we last logged count data
        self.count_log_interval = 5  # Only log count data every 5 seconds
        # Initialize test_mode - defaults to False (live mode)
        self.test_mode = self.mode_config.get("test_mode", False)
        # Freshness bookkeeping for _detect_stale_games():
        # {game_id: {"clock": ts, "score": ts, "last_seen": ts}}
        self.game_update_timestamps = {}
        self.stale_game_timeout = self.mode_config.get("stale_game_timeout", 300)  # 5 minutes default

    @abstractmethod
    def _test_mode_update(self) -> None:
        return

    def _is_game_really_over(self, game: Dict) -> bool:
        """Check if a game appears to be over even if API says it's live.

        Two independent signals:
        1. ``period_text`` says "final" — universal across every sport.
        2. The clock has expired at/after :attr:`FINAL_PERIOD` — only meaningful
           where :attr:`CLOCK_COUNTS_DOWN` is true.

        Fails *safe*: anything ambiguous returns False and the game keeps being
        displayed. The only caller, :meth:`_detect_stale_games`, removes games
        on a True, so a false positive silently drops a live game.
        """
        game_str = f"{game.get('away_abbr')}@{game.get('home_abbr')}"

        # `period_text` may be present-but-None; `or ""` keeps that from raising
        # AttributeError — the caller has no try/except around this call.
        period_text = (game.get("period_text") or "").lower()
        if "final" in period_text:
            self.logger.debug(
                f"_is_game_really_over({game_str}): "
                f"returning True - 'final' in period_text='{period_text}'"
            )
            return True

        if not self.CLOCK_COUNTS_DOWN:
            # Count-up clock: 0:00 means the match has not started.
            self.logger.debug(
                f"_is_game_really_over({game_str}): returning False "
                f"(count-up clock, period_text='{period_text}')"
            )
            return False

        raw_clock = game.get("clock")
        period = game.get("period", 0)

        # Only check clock-based finish if we have a valid clock string. A
        # missing or non-string clock is NOT coerced to "0:00": sports without a
        # game clock (e.g. baseball, where `period` is the inning) would
        # otherwise be declared over from the FINAL_PERIOD-th period onward.
        if isinstance(raw_clock, str) and raw_clock.strip() and period >= self.FINAL_PERIOD:
            clock = raw_clock
            clock_normalized = clock.replace(":", "").strip()
            if clock_normalized in ("000", "00") or clock in ("0:00", ":00"):
                self.logger.debug(
                    f"_is_game_really_over({game_str}): "
                    f"returning True - clock at 0:00 (clock='{clock}', period={period})"
                )
                return True

        self.logger.debug(
            f"_is_game_really_over({game_str}): returning False"
        )
        return False

    def _detect_stale_games(self, games: List[Dict]) -> None:
        """Remove games that appear stale or haven't updated.

        Mutates ``games`` **in place** and returns None. Removal is by value
        (``list.remove`` uses ``dict.__eq__``), so two structurally-equal game
        dicts in the same list would drop the first occurrence.
        """
        current_time = time.time()

        for game in games[:]:  # Copy list to iterate safely
            game_id = game.get("id")
            if not game_id:
                continue

            # Check if game data is stale
            timestamps = self.game_update_timestamps.get(game_id, {})
            last_seen = timestamps.get("last_seen", 0)

            if last_seen > 0 and current_time - last_seen > self.stale_game_timeout:
                self.logger.warning(
                    f"Removing stale game {game.get('away_abbr')}@{game.get('home_abbr')} "
                    f"(last seen {int(current_time - last_seen)}s ago)"
                )
                games.remove(game)
                if game_id in self.game_update_timestamps:
                    del self.game_update_timestamps[game_id]
                continue

            # Also check if game appears to be over
            if self._is_game_really_over(game):
                self.logger.debug(
                    f"Removing game that appears over: {game.get('away_abbr')}@{game.get('home_abbr')} "
                    f"(clock={game.get('clock')}, period={game.get('period')}, period_text={game.get('period_text')})"
                )
                games.remove(game)
                if game_id in self.game_update_timestamps:
                    del self.game_update_timestamps[game_id]

    def update(self):
        """Update live game data and handle game switching."""
        if not self.is_enabled:
            return

        # Define current_time and interval before the problematic line (originally line 455)
        # Ensure 'import time' is present at the top of the file.
        current_time = time.time()

        # Define interval using a pattern similar to NFLLiveManager's update method.
        # Uses getattr for robustness, assuming attributes for live_games, test_mode,
        # no_data_interval, and update_interval are available on self.
        _live_games_attr = self.live_games
        _test_mode_attr = self.test_mode # test_mode is often from a base class or config
        _no_data_interval_attr = self.no_data_interval # Default similar to NFLLiveManager
        _update_interval_attr = self.update_interval  # Default similar to NFLLiveManager

        interval = _no_data_interval_attr if not _live_games_attr and not _test_mode_attr else _update_interval_attr
        
        # Original line from traceback (line 455), now with variables defined:
        if current_time - self.last_update >= interval:
            self.last_update = current_time

            # Fetch rankings if enabled
            if self.show_ranking:
                self._fetch_team_rankings()

            if self.test_mode:
                # Simulate clock running down in test mode
                self._test_mode_update()
            else:
                # Fetch live game data
                data = self._fetch_data()
                new_live_games = []
                if data and "events" in data:
                    for game in data["events"]:
                        details = self._extract_game_details(game)
                        if details and (details["is_live"] or details["is_halftime"]):
                            # If show_favorite_teams_only is true, only add if it's a favorite.
                            # Otherwise, add all games.
                            if self.show_all_live or not self.show_favorite_teams_only or (self.show_favorite_teams_only and (details["home_abbr"] in self.favorite_teams or details["away_abbr"] in self.favorite_teams)):
                                if self.show_odds:
                                    self._fetch_odds(details)
                                new_live_games.append(details)
                    # Log changes or periodically
                    current_time_for_log = time.time() # Use a consistent time for logging comparison
                    should_log = (
                        current_time_for_log - self.last_log_time >= self.log_interval or
                        len(new_live_games) != len(self.live_games) or
                        any(g1['id'] != g2.get('id') for g1, g2 in zip(self.live_games, new_live_games)) or # Check if game IDs changed
                        (not self.live_games and new_live_games) # Log if games appeared
                    )

                    if should_log:
                        if new_live_games:
                            filter_text = "favorite teams" if self.show_favorite_teams_only or self.show_all_live else "all teams"
                            self.logger.info(f"Found {len(new_live_games)} live/halftime games for {filter_text}.")
                            for game_info in new_live_games: # Renamed game to game_info
                                self.logger.info(f"  - {game_info['away_abbr']}@{game_info['home_abbr']} ({game_info.get('status_text', 'N/A')})")
                        else:
                            filter_text = "favorite teams" if self.show_favorite_teams_only or self.show_all_live else "criteria"
                            self.logger.info(f"No live/halftime games found for {filter_text}.")
                        self.last_log_time = current_time_for_log


                    # Update game list and current game
                    if new_live_games:
                        # Check if the games themselves changed, not just scores/time
                        new_game_ids = {g['id'] for g in new_live_games}
                        current_game_ids = {g['id'] for g in self.live_games}

                        if new_game_ids != current_game_ids:
                            self.live_games = sorted(new_live_games, key=lambda g: g.get('start_time_utc') or datetime.now(timezone.utc)) # Sort by start time
                            # Reset index if current game is gone or list is new
                            if not self.current_game or self.current_game['id'] not in new_game_ids:
                                self.current_game_index = 0
                                self.current_game = self.live_games[0] if self.live_games else None
                                self.last_game_switch = current_time
                            else:
                                # Find current game's new index if it still exists
                                try:
                                     self.current_game_index = next(i for i, g in enumerate(self.live_games) if g['id'] == self.current_game['id'])
                                     self.current_game = self.live_games[self.current_game_index] # Update current_game with fresh data
                                     # Fix: Set last_game_switch if it's still 0 (initialized) to prevent immediate switching
                                     if self.last_game_switch == 0:
                                         self.last_game_switch = current_time
                                except StopIteration: # Should not happen if check above passed, but safety first
                                     self.current_game_index = 0
                                     self.current_game = self.live_games[0]
                                     self.last_game_switch = current_time

                        else:
                             # Just update the data for the existing games
                             temp_game_dict = {g['id']: g for g in new_live_games}
                             self.live_games = [temp_game_dict.get(g['id'], g) for g in self.live_games] # Update in place
                             if self.current_game:
                                  self.current_game = temp_game_dict.get(self.current_game['id'], self.current_game)
                             # Fix: Set last_game_switch if it's still 0 (initialized) to prevent immediate switching
                             # This handles the case where games were loaded previously but last_game_switch was never set
                             if self.last_game_switch == 0:
                                 self.last_game_switch = current_time

                        # Display update handled by main loop based on interval

                    else:
                        # No live games found
                        if self.live_games: # Were there games before?
                            self.logger.info("Live games previously showing have ended or are no longer live.") # Changed log prefix
                        self.live_games = []
                        self.current_game = None
                        self.current_game_index = 0

                else:
                    # Error fetching data or no events
                     if self.live_games: # Were there games before?
                         self.logger.warning("Could not fetch update; keeping existing live game data for now.") # Changed log prefix
                     else:
                         self.logger.warning("Could not fetch data and no existing live games.") # Changed log prefix
                         self.current_game = None # Clear current game if fetch fails and no games were active

            # Handle game switching (outside test mode check)
            # Fix: Don't check for switching if last_game_switch is still 0 (games haven't been loaded yet)
            # This prevents immediate switching when the system has been running for a while before games load
            if not self.test_mode and len(self.live_games) > 1 and self.last_game_switch > 0 and (current_time - self.last_game_switch) >= self.game_display_duration:
                self.current_game_index = (self.current_game_index + 1) % len(self.live_games)
                self.current_game = self.live_games[self.current_game_index]
                self.last_game_switch = current_time
                self.logger.info(f"Switched live view to: {self.current_game['away_abbr']}@{self.current_game['home_abbr']}") # Changed log prefix
                # Force display update via flag or direct call if needed, but usually let main loop handle
