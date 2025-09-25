"""
Abstract API Data Extraction Layer

This module provides a pluggable system for extracting game data from different
sports APIs. Each sport can have its own extractor that handles sport-specific
fields and data structures.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List
import logging
from datetime import datetime
import pytz

class APIDataExtractor(ABC):
    """Abstract base class for API data extraction."""
    
    def __init__(self, logger: logging.Logger):
        self.logger = logger
    
    @abstractmethod
    def extract_game_details(self, game_event: Dict) -> Optional[Dict]:
        """Extract common game details from raw API data."""
        pass
    
    @abstractmethod
    def get_sport_specific_fields(self, game_event: Dict) -> Dict:
        """Extract sport-specific fields (downs, innings, periods, etc.)."""
        pass
    
    def _extract_common_details(self, game_event: Dict) -> tuple[Dict | None, Dict | None, Dict | None, Dict | None, Dict | None]:
        """Extract common game details that work across all sports."""
        if not game_event: 
            return None, None, None, None, None
            
        try:
            competition = game_event["competitions"][0]
            status = competition["status"]
            competitors = competition["competitors"]
            game_date_str = game_event["date"]
            situation = competition.get("situation")
            
            # Parse game time
            start_time_utc = None
            try:
                start_time_utc = datetime.fromisoformat(game_date_str.replace("Z", "+00:00"))
            except ValueError:
                self.logger.warning(f"Could not parse game date: {game_date_str}")

            # Extract teams
            home_team = next((c for c in competitors if c.get("homeAway") == "home"), None)
            away_team = next((c for c in competitors if c.get("homeAway") == "away"), None)

            if not home_team or not away_team:
                self.logger.warning(f"Could not find home or away team in event: {game_event.get('id')}")
                return None, None, None, None, None

            return {
                "game_event": game_event,
                "competition": competition,
                "status": status,
                "situation": situation,
                "start_time_utc": start_time_utc,
                "home_team": home_team,
                "away_team": away_team
            }, home_team, away_team, status, situation
            
        except Exception as e:
            self.logger.error(f"Error extracting common details: {e}")
            return None, None, None, None, None


class ESPNFootballExtractor(APIDataExtractor):
    """ESPN API extractor for football (NFL/NCAA)."""
    
    def extract_game_details(self, game_event: Dict) -> Optional[Dict]:
        """Extract football game details from ESPN API."""
        common_data, home_team, away_team, status, situation = self._extract_common_details(game_event)
        if not common_data:
            return None
            
        try:
            # Extract basic team info
            home_abbr = home_team["team"]["abbreviation"]
            away_abbr = away_team["team"]["abbreviation"]
            home_score = home_team.get("score", "0")
            away_score = away_team.get("score", "0")
            
            # Extract sport-specific fields
            sport_fields = self.get_sport_specific_fields(game_event)
            
            # Build game details
            details = {
                "id": game_event.get("id"),
                "home_abbr": home_abbr,
                "away_abbr": away_abbr,
                "home_score": str(home_score),
                "away_score": str(away_score),
                "home_team_name": home_team["team"].get("displayName", ""),
                "away_team_name": away_team["team"].get("displayName", ""),
                "status_text": status["type"].get("shortDetail", ""),
                "is_live": status["type"]["state"] == "in",
                "is_final": status["type"]["state"] == "post",
                "is_upcoming": status["type"]["state"] == "pre",
                **sport_fields  # Add sport-specific fields
            }
            
            return details
            
        except Exception as e:
            self.logger.error(f"Error extracting football game details: {e}")
            return None
    
    def get_sport_specific_fields(self, game_event: Dict) -> Dict:
        """Extract football-specific fields."""
        try:
            competition = game_event["competitions"][0]
            status = competition["status"]
            situation = competition.get("situation", {})
            
            sport_fields = {
                "down": "",
                "distance": "",
                "possession": "",
                "is_redzone": False,
                "home_timeouts": 0,
                "away_timeouts": 0,
                "scoring_event": ""
            }
            
            if situation and status["type"]["state"] == "in":
                sport_fields.update({
                    "down": situation.get("down", ""),
                    "distance": situation.get("distance", ""),
                    "possession": situation.get("possession", ""),
                    "is_redzone": situation.get("isRedZone", False),
                    "home_timeouts": situation.get("homeTimeouts", 0),
                    "away_timeouts": situation.get("awayTimeouts", 0)
                })
                
                # Detect scoring events
                status_detail = status["type"].get("detail", "").lower()
                if "touchdown" in status_detail or "field goal" in status_detail:
                    sport_fields["scoring_event"] = status_detail
            
            return sport_fields
            
        except Exception as e:
            self.logger.error(f"Error extracting football-specific fields: {e}")
            return {}


class ESPNBaseballExtractor(APIDataExtractor):
    """ESPN API extractor for baseball (MLB)."""
    
    def extract_game_details(self, game_event: Dict) -> Optional[Dict]:
        """Extract baseball game details from ESPN API."""
        common_data, home_team, away_team, status, situation = self._extract_common_details(game_event)
        if not common_data:
            return None
            
        try:
            # Extract basic team info
            home_abbr = home_team["team"]["abbreviation"]
            away_abbr = away_team["team"]["abbreviation"]
            home_score = home_team.get("score", "0")
            away_score = away_team.get("score", "0")
            
            # Extract sport-specific fields
            sport_fields = self.get_sport_specific_fields(game_event)
            
            # Build game details
            details = {
                "id": game_event.get("id"),
                "home_abbr": home_abbr,
                "away_abbr": away_abbr,
                "home_score": str(home_score),
                "away_score": str(away_score),
                "home_team_name": home_team["team"].get("displayName", ""),
                "away_team_name": away_team["team"].get("displayName", ""),
                "status_text": status["type"].get("shortDetail", ""),
                "is_live": status["type"]["state"] == "in",
                "is_final": status["type"]["state"] == "post",
                "is_upcoming": status["type"]["state"] == "pre",
                **sport_fields  # Add sport-specific fields
            }
            
            return details
            
        except Exception as e:
            self.logger.error(f"Error extracting baseball game details: {e}")
            return None
    
    def get_sport_specific_fields(self, game_event: Dict) -> Dict:
        """Extract baseball-specific fields."""
        try:
            competition = game_event["competitions"][0]
            status = competition["status"]
            situation = competition.get("situation", {})
            
            sport_fields = {
                "inning": "",
                "outs": 0,
                "bases": "",
                "strikes": 0,
                "balls": 0,
                "pitcher": "",
                "batter": ""
            }
            
            if situation and status["type"]["state"] == "in":
                sport_fields.update({
                    "inning": situation.get("inning", ""),
                    "outs": situation.get("outs", 0),
                    "bases": situation.get("bases", ""),
                    "strikes": situation.get("strikes", 0),
                    "balls": situation.get("balls", 0),
                    "pitcher": situation.get("pitcher", ""),
                    "batter": situation.get("batter", "")
                })
            
            return sport_fields
            
        except Exception as e:
            self.logger.error(f"Error extracting baseball-specific fields: {e}")
            return {}


class ESPNHockeyExtractor(APIDataExtractor):
    """ESPN API extractor for hockey (NHL/NCAA)."""
    
    def extract_game_details(self, game_event: Dict) -> Optional[Dict]:
        """Extract hockey game details from ESPN API."""
        common_data, home_team, away_team, status, situation = self._extract_common_details(game_event)
        if not common_data:
            return None
            
        try:
            # Extract basic team info
            home_abbr = home_team["team"]["abbreviation"]
            away_abbr = away_team["team"]["abbreviation"]
            home_score = home_team.get("score", "0")
            away_score = away_team.get("score", "0")
            
            # Extract sport-specific fields
            sport_fields = self.get_sport_specific_fields(game_event)
            
            # Build game details
            details = {
                "id": game_event.get("id"),
                "home_abbr": home_abbr,
                "away_abbr": away_abbr,
                "home_score": str(home_score),
                "away_score": str(away_score),
                "home_team_name": home_team["team"].get("displayName", ""),
                "away_team_name": away_team["team"].get("displayName", ""),
                "status_text": status["type"].get("shortDetail", ""),
                "is_live": status["type"]["state"] == "in",
                "is_final": status["type"]["state"] == "post",
                "is_upcoming": status["type"]["state"] == "pre",
                **sport_fields  # Add sport-specific fields
            }
            
            return details
            
        except Exception as e:
            self.logger.error(f"Error extracting hockey game details: {e}")
            return None
    
    def get_sport_specific_fields(self, game_event: Dict) -> Dict:
        """Extract hockey-specific fields."""
        try:
            competition = game_event["competitions"][0]
            status = competition["status"]
            situation = competition.get("situation", {})
            
            sport_fields = {
                "period": "",
                "period_text": "",
                "power_play": False,
                "penalties": "",
                "shots_on_goal": {"home": 0, "away": 0}
            }
            
            if situation and status["type"]["state"] == "in":
                period = status.get("period", 0)
                period_text = ""
                if period == 1:
                    period_text = "P1"
                elif period == 2:
                    period_text = "P2"
                elif period == 3:
                    period_text = "P3"
                elif period > 3:
                    period_text = f"OT{period-3}"
                
                sport_fields.update({
                    "period": str(period),
                    "period_text": period_text,
                    "power_play": situation.get("isPowerPlay", False),
                    "penalties": situation.get("penalties", ""),
                    "shots_on_goal": {
                        "home": situation.get("homeShots", 0),
                        "away": situation.get("awayShots", 0)
                    }
                })
            
            return sport_fields
            
        except Exception as e:
            self.logger.error(f"Error extracting hockey-specific fields: {e}")
            return {}


class SoccerAPIExtractor(APIDataExtractor):
    """Generic extractor for soccer APIs (different structure than ESPN)."""
    
    def extract_game_details(self, game_event: Dict) -> Optional[Dict]:
        """Extract soccer game details from various soccer APIs."""
        # This would need to be adapted based on the specific soccer API being used
        # For now, return a basic structure
        try:
            return {
                "id": game_event.get("id"),
                "home_abbr": game_event.get("home_team", {}).get("abbreviation", ""),
                "away_abbr": game_event.get("away_team", {}).get("abbreviation", ""),
                "home_score": str(game_event.get("home_score", "0")),
                "away_score": str(game_event.get("away_score", "0")),
                "home_team_name": game_event.get("home_team", {}).get("name", ""),
                "away_team_name": game_event.get("away_team", {}).get("name", ""),
                "status_text": game_event.get("status", ""),
                "is_live": game_event.get("is_live", False),
                "is_final": game_event.get("is_final", False),
                "is_upcoming": game_event.get("is_upcoming", False),
                **self.get_sport_specific_fields(game_event)
            }
        except Exception as e:
            self.logger.error(f"Error extracting soccer game details: {e}")
            return None
    
    def get_sport_specific_fields(self, game_event: Dict) -> Dict:
        """Extract soccer-specific fields."""
        try:
            return {
                "half": game_event.get("half", ""),
                "stoppage_time": game_event.get("stoppage_time", ""),
                "cards": {
                    "home_yellow": game_event.get("home_yellow_cards", 0),
                    "away_yellow": game_event.get("away_yellow_cards", 0),
                    "home_red": game_event.get("home_red_cards", 0),
                    "away_red": game_event.get("away_red_cards", 0)
                },
                "possession": {
                    "home": game_event.get("home_possession", 0),
                    "away": game_event.get("away_possession", 0)
                }
            }
        except Exception as e:
            self.logger.error(f"Error extracting soccer-specific fields: {e}")
            return {}


# Factory function removed - sport classes now instantiate extractors directly
