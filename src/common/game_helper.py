"""
Game Helper

Handles common game data extraction and processing for LED matrix plugins.
Extracted from LEDMatrix core to provide reusable functionality for plugins.
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple
import pytz


class GameHelper:
    """
    Helper class for game data extraction and processing.
    
    Provides functionality for:
    - Extracting game details from ESPN API responses
    - Filtering games by various criteria
    - Processing game data for display
    - Time zone handling and date formatting
    """
    
    def __init__(self, timezone_str: str = 'UTC', logger: Optional[logging.Logger] = None):
        """
        Initialize the GameHelper.
        
        Args:
            timezone_str: Timezone string for date/time processing
            logger: Optional logger instance
        """
        self.logger = logger or logging.getLogger(__name__)
        self.timezone = self._get_timezone(timezone_str)
    
    def extract_game_details(self, event: Dict[str, Any], sport: str = None) -> Optional[Dict[str, Any]]:
        """
        Extract game details from ESPN event data.
        
        Args:
            event: ESPN event data
            sport: Sport type for sport-specific processing
            
        Returns:
            Processed game details or None if extraction fails
        """
        if not event:
            return None
        
        try:
            competition = event.get("competitions", [{}])[0]
            status = competition.get("status", {})
            competitors = competition.get("competitors", [])
            game_date_str = event.get("date", "")
            
            if not competitors or len(competitors) < 2:
                self.logger.warning(f"Insufficient competitor data in event: {event.get('id')}")
                return None
            
            # Find home and away teams
            home_team = next((c for c in competitors if c.get("homeAway") == "home"), None)
            away_team = next((c for c in competitors if c.get("homeAway") == "away"), None)
            
            if not home_team or not away_team:
                self.logger.warning(f"Could not find home/away teams in event: {event.get('id')}")
                return None
            
            # Extract basic team info
            home_abbr = self._extract_team_abbreviation(home_team)
            away_abbr = self._extract_team_abbreviation(away_team)
            
            # Parse game time
            start_time_utc = self._parse_game_time(game_date_str)
            game_time, game_date = self._format_game_time(start_time_utc)
            
            # Extract records
            home_record = self._extract_team_record(home_team)
            away_record = self._extract_team_record(away_team)
            
            # Determine game state
            game_state = self._determine_game_state(status)
            
            # Build game details
            details = {
                "id": event.get("id"),
                "game_time": game_time,
                "game_date": game_date,
                "start_time_utc": start_time_utc,
                "status_text": status.get("type", {}).get("shortDetail", ""),
                "is_live": game_state["is_live"],
                "is_final": game_state["is_final"],
                "is_upcoming": game_state["is_upcoming"],
                "is_halftime": game_state["is_halftime"],
                "is_period_break": game_state["is_period_break"],
                "home_abbr": home_abbr,
                "home_id": home_team.get("id"),
                "home_score": str(home_team.get("score", "0")),
                "home_record": home_record,
                "away_abbr": away_abbr,
                "away_id": away_team.get("id"),
                "away_score": str(away_team.get("score", "0")),
                "away_record": away_record,
                "is_within_window": True,
            }
            
            # Add sport-specific details
            if sport:
                details.update(self._extract_sport_specific_details(event, sport))
            
            return details
            
        except Exception as e:
            self.logger.error(f"Error extracting game details: {e} from event: {event.get('id')}", exc_info=True)
            return None
    
    def filter_live_games(self, games: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Filter games to only include live games.
        
        Args:
            games: List of game dictionaries
            
        Returns:
            List of live games
        """
        return [game for game in games if game.get('is_live', False)]
    
    def filter_final_games(self, games: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Filter games to only include final games.
        
        Args:
            games: List of game dictionaries
            
        Returns:
            List of final games
        """
        return [game for game in games if game.get('is_final', False)]
    
    def filter_upcoming_games(self, games: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Filter games to only include upcoming games.
        
        Args:
            games: List of game dictionaries
            
        Returns:
            List of upcoming games
        """
        return [game for game in games if game.get('is_upcoming', False)]
    
    def filter_favorite_teams(self, games: List[Dict[str, Any]], 
                              favorite_teams: List[str]) -> List[Dict[str, Any]]:
        """
        Filter games to only include games with favorite teams.
        
        Args:
            games: List of game dictionaries
            favorite_teams: List of favorite team abbreviations
            
        Returns:
            List of games involving favorite teams
        """
        if not favorite_teams:
            return games
        
        return [game for game in games 
                if game.get('home_abbr') in favorite_teams or 
                   game.get('away_abbr') in favorite_teams]
    
    def filter_recent_games(self, games: List[Dict[str, Any]], 
                           days_back: int = 7) -> List[Dict[str, Any]]:
        """
        Filter games to only include recent games within specified days.
        
        Args:
            games: List of game dictionaries
            days_back: Number of days to look back
            
        Returns:
            List of recent games
        """
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=days_back)
        
        recent_games = []
        for game in games:
            start_time = game.get('start_time_utc')
            if start_time and start_time >= cutoff_date:
                recent_games.append(game)
        
        return recent_games
    
    def sort_games_by_time(self, games: List[Dict[str, Any]], 
                          reverse: bool = False) -> List[Dict[str, Any]]:
        """
        Sort games by start time.
        
        Args:
            games: List of game dictionaries
            reverse: If True, sort in descending order (newest first)
            
        Returns:
            Sorted list of games
        """
        def get_start_time(game):
            start_time = game.get('start_time_utc')
            if start_time:
                return start_time
            # Fallback to current time for games without start time
            return datetime.now(timezone.utc)
        
        return sorted(games, key=get_start_time, reverse=reverse)
    
    def process_games(self, events: List[Dict[str, Any]], sport: str = None) -> List[Dict[str, Any]]:
        """
        Process a list of ESPN events into game details.
        
        Args:
            events: List of ESPN event data
            sport: Sport type for processing
            
        Returns:
            List of processed game details
        """
        games = []
        
        for event in events:
            game = self.extract_game_details(event, sport)
            if game:
                games.append(game)
        
        return games
    
    def get_game_summary(self, game: Dict[str, Any]) -> str:
        """
        Get a text summary of a game.
        
        Args:
            game: Game dictionary
            
        Returns:
            Text summary of the game
        """
        home_abbr = game.get('home_abbr', 'HOME')
        away_abbr = game.get('away_abbr', 'AWAY')
        home_score = game.get('home_score', '0')
        away_score = game.get('away_score', '0')
        status = game.get('status_text', '')
        
        if game.get('is_live'):
            return f"{away_abbr} {away_score} @ {home_abbr} {home_score} ({status})"
        elif game.get('is_final'):
            return f"{away_abbr} {away_score} @ {home_abbr} {home_score} (Final)"
        else:
            return f"{away_abbr} @ {home_abbr} ({status})"
    
    def _extract_team_abbreviation(self, team_data: Dict[str, Any]) -> str:
        """Extract team abbreviation from team data."""
        try:
            return team_data.get("team", {}).get("abbreviation", "")
        except (KeyError, AttributeError):
            # Fallback to first 3 characters of team name
            team_name = team_data.get("team", {}).get("name", "UNK")
            return team_name[:3].upper()
    
    def _extract_team_record(self, team_data: Dict[str, Any]) -> str:
        """Extract team record from team data."""
        try:
            records = team_data.get('records', [])
            if records and len(records) > 0:
                record = records[0].get('summary', '')
                # Don't show "0-0" records
                if record in {"0-0", "0-0-0"}:
                    return ''
                return record
        except (KeyError, AttributeError, IndexError):
            pass
        return ''
    
    def _parse_game_time(self, game_date_str: str) -> Optional[datetime]:
        """Parse game time string to UTC datetime."""
        if not game_date_str:
            return None
        
        try:
            # Handle ISO format with Z suffix
            if game_date_str.endswith('Z'):
                game_date_str = game_date_str.replace('Z', '+00:00')
            
            dt = datetime.fromisoformat(game_date_str)
            # Ensure the datetime is UTC-aware (fromisoformat may create timezone-aware but not pytz.UTC)
            if dt.tzinfo is None:
                # If naive, assume it's UTC
                return dt.replace(tzinfo=pytz.UTC)
            else:
                # Convert to pytz.UTC for consistency
                return dt.astimezone(pytz.UTC)
        except ValueError:
            self.logger.warning(f"Could not parse game date: {game_date_str}")
            return None
    
    def _format_game_time(self, start_time_utc: Optional[datetime]) -> Tuple[str, str]:
        """Format game time for display."""
        if not start_time_utc:
            return "", ""
        
        try:
            local_time = start_time_utc.astimezone(self.timezone)
            game_time = local_time.strftime("%I:%M%p").lstrip('0')
            game_date = local_time.strftime("%B %d")
            return game_time, game_date
        except Exception as e:
            self.logger.error(f"Error formatting game time: {e}")
            return "", ""
    
    def _determine_game_state(self, status: Dict[str, Any]) -> Dict[str, bool]:
        """Determine game state from status data."""
        status_type = status.get("type", {})
        state = status_type.get("state", "")
        name = status_type.get("name", "").lower()
        
        return {
            "is_live": state == "in",
            "is_final": state == "post",
            "is_upcoming": state == "pre" or name in ['scheduled', 'pre-game', 'status_scheduled'],
            "is_halftime": state == "halftime" or name == "status_halftime",
            "is_period_break": name == "status_end_period",
        }
    
    def _extract_sport_specific_details(self, event: Dict[str, Any], sport: str) -> Dict[str, Any]:
        """Extract sport-specific game details."""
        details = {}
        
        if sport == "basketball":
            details.update(self._extract_basketball_details(event))
        elif sport == "football":
            details.update(self._extract_football_details(event))
        elif sport == "hockey":
            details.update(self._extract_hockey_details(event))
        elif sport == "baseball":
            details.update(self._extract_baseball_details(event))
        
        return details
    
    def _extract_basketball_details(self, event: Dict[str, Any]) -> Dict[str, Any]:
        """Extract basketball-specific details."""
        details = {}
        
        try:
            competition = event.get("competitions", [{}])[0]
            status = competition.get("status", {})
            
            # Period information
            period = status.get("period", 0)
            if period > 0:
                if period <= 4:
                    details["period_text"] = f"Q{period}"
                else:
                    details["period_text"] = f"OT{period - 4}"
            else:
                details["period_text"] = "Start"
            
            # Clock
            details["clock"] = status.get("displayClock", "0:00")
            
        except (KeyError, IndexError):
            pass
        
        return details
    
    def _extract_football_details(self, event: Dict[str, Any]) -> Dict[str, Any]:
        """Extract football-specific details."""
        details = {}
        
        try:
            competition = event.get("competitions", [{}])[0]
            status = competition.get("status", {})
            
            # Quarter information
            period = status.get("period", 0)
            if period > 0:
                if period <= 4:
                    details["period_text"] = f"Q{period}"
                else:
                    details["period_text"] = f"OT{period - 4}"
            else:
                details["period_text"] = "Start"
            
            # Clock
            details["clock"] = status.get("displayClock", "0:00")
            
        except (KeyError, IndexError):
            pass
        
        return details
    
    def _extract_hockey_details(self, event: Dict[str, Any]) -> Dict[str, Any]:
        """Extract hockey-specific details."""
        details = {}
        
        try:
            competition = event.get("competitions", [{}])[0]
            status = competition.get("status", {})
            
            # Period information
            period = status.get("period", 0)
            if period > 0:
                if period <= 3:
                    details["period_text"] = f"P{period}"
                else:
                    details["period_text"] = f"OT{period - 3}"
            else:
                details["period_text"] = "Start"
            
            # Clock
            details["clock"] = status.get("displayClock", "0:00")
            
        except (KeyError, IndexError):
            pass
        
        return details
    
    def _extract_baseball_details(self, event: Dict[str, Any]) -> Dict[str, Any]:
        """Extract baseball-specific details."""
        details = {}
        
        try:
            competition = event.get("competitions", [{}])[0]
            status = competition.get("status", {})
            
            # Inning information
            period = status.get("period", 0)
            if period > 0:
                details["period_text"] = f"INN {period}"
            else:
                details["period_text"] = "Start"
            
            # Clock
            details["clock"] = status.get("displayClock", "0:00")
            
        except (KeyError, IndexError):
            pass
        
        return details
    
    def _get_timezone(self, timezone_str: str) -> pytz.BaseTzInfo:
        """Get timezone object from string."""
        try:
            return pytz.timezone(timezone_str)
        except pytz.UnknownTimeZoneError:
            self.logger.warning(f"Unknown timezone: {timezone_str}, using UTC")
            return pytz.utc
