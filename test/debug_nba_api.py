#!/usr/bin/env python3
"""
Diagnostic script to examine NBA API data structure and identify the missing 'id' field issue.
"""
import requests
import json
import logging
from typing import Dict, Any

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def fetch_nba_teams_data() -> Dict[str, Any]:
    """Fetch NBA teams data from ESPN API."""
    teams_url = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams"

    try:
        logger.info(f"Fetching NBA teams data from: {teams_url}")
        response = requests.get(teams_url, timeout=30)
        response.raise_for_status()
        data = response.json()

        logger.info(f"Successfully fetched NBA teams data")
        logger.info(f"Response structure keys: {list(data.keys())}")

        # Examine the structure
        sports = data.get('sports', [])
        if sports:
            logger.info(f"Number of sports: {len(sports)}")
            sport = sports[0]
            logger.info(f"Sport keys: {list(sport.keys())}")

            leagues = sport.get('leagues', [])
            if leagues:
                league = leagues[0]
                logger.info(f"League keys: {list(league.keys())}")

                teams = league.get('teams', [])
                logger.info(f"Number of teams: {len(teams)}")

                if teams:
                    # Examine first team structure
                    first_team = teams[0]
                    logger.info(f"First team keys: {list(first_team.keys())}")

                    team_data = first_team.get('team', {})
                    logger.info(f"Team data keys: {list(team_data.keys())}")

                    # Check for id field
                    team_id = team_data.get('id')
                    team_abbr = team_data.get('abbreviation')
                    team_name = team_data.get('name')

                    logger.info(f"Sample team: ID={team_id}, ABBR={team_abbr}, NAME={team_name}")

                    if team_id:
                        logger.info(f"Team ID field exists: {team_id}")
                    else:
                        logger.error("Team ID field is missing!")

                    # Check a few more teams to confirm structure
                    for i in range(min(5, len(teams))):
                        team = teams[i].get('team', {})
                        logger.info(f"Team {i+1}: ID={team.get('id')}, ABBR={team.get('abbreviation')}")

        return data

    except Exception as e:
        logger.error(f"Error fetching NBA teams data: {e}")
        return {}

def fetch_nba_standings_data() -> Dict[str, Any]:
    """Fetch NBA standings data from ESPN API."""
    standings_url = "https://site.api.espn.com/apis/v2/sports/basketball/nba/standings"

    try:
        logger.info(f"Fetching NBA standings data from: {standings_url}")
        response = requests.get(standings_url, timeout=30)
        response.raise_for_status()
        data = response.json()

        logger.info(f"Successfully fetched NBA standings data")
        logger.info(f"Response structure keys: {list(data.keys())}")

        # Check if standings has entries (direct structure)
        if 'standings' in data and 'entries' in data['standings']:
            entries = data['standings']['entries']
            logger.info(f"Number of standings entries (direct): {len(entries)}")

            if entries:
                # Examine first entry structure
                first_entry = entries[0]
                logger.info(f"First entry keys: {list(first_entry.keys())}")

                team_data = first_entry.get('team', {})
                logger.info(f"Team data keys: {list(team_data.keys())}")

                # Check for id field
                team_id = team_data.get('id')
                team_abbr = team_data.get('abbreviation')
                team_name = team_data.get('displayName')

                logger.info(f"Sample standings team: ID={team_id}, ABBR={team_abbr}, NAME={team_name}")

                if team_id:
                    logger.info(f"Standings team ID field exists: {team_id}")
                else:
                    logger.error("Standings team ID field is missing!")

        # Check children structure (divisions/conferences)
        if 'children' in data:
            children = data.get('children', [])
            logger.info(f"Number of children (divisions/conferences): {len(children)}")

            for i, child in enumerate(children):
                logger.info(f"Child {i+1} keys: {list(child.keys())}")
                child_name = child.get('displayName', 'Unknown')
                logger.info(f"Child {i+1} name: {child_name}")

                if 'standings' in child and 'entries' in child['standings']:
                    entries = child['standings']['entries']
                    logger.info(f"Child {i+1} has {len(entries)} entries")

                    if entries:
                        # Examine first entry in this child
                        first_entry = entries[0]
                        logger.info(f"Child {i+1} first entry keys: {list(first_entry.keys())}")

                        team_data = first_entry.get('team', {})
                        logger.info(f"Child {i+1} team data keys: {list(team_data.keys())}")

                        # Check for id field
                        team_id = team_data.get('id')
                        team_abbr = team_data.get('abbreviation')
                        team_name = team_data.get('displayName')

                        logger.info(f"Child {i+1} sample team: ID={team_id}, ABBR={team_abbr}, NAME={team_name}")

                        if team_id:
                            logger.info(f"Child {i+1} team ID field exists: {team_id}")
                        else:
                            logger.error(f"Child {i+1} team ID field is missing!")

        return data

    except Exception as e:
        logger.error(f"Error fetching NBA standings data: {e}")
        return {}

def main():
    """Main diagnostic function."""
    logger.info("Starting NBA API data structure diagnosis")

    # Fetch teams data
    teams_data = fetch_nba_teams_data()

    # Fetch standings data
    standings_data = fetch_nba_standings_data()

    # Summary
    logger.info("Diagnosis complete")
    logger.info("Check the logs above to see if team 'id' fields are present")
    logger.info("The leaderboard manager needs team 'id' fields for logo fetching")

if __name__ == "__main__":
    main()
