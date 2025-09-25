#!/usr/bin/env python3
"""
Script to save the missing teams list to a file for future reference.
"""

import os
from pathlib import Path

def save_missing_teams():
    """Save the missing teams list to a file."""
    
    # Define the sports directories and their corresponding sections in the abbreviations file
    sports_dirs = {
        'mlb_logos': 'MLB',
        'nba_logos': 'NBA', 
        'nfl_logos': 'NFL',
        'nhl_logos': 'NHL',
        'ncaa_logos': ['NCAAF', 'NCAA Conferences/Divisions', 'NCAA_big10', 'NCAA_big12', 'NCAA_acc', 'NCAA_sec', 'NCAA_pac12', 'NCAA_american', 'NCAA_cusa', 'NCAA_mac', 'NCAA_mwc', 'NCAA_sunbelt', 'NCAA_ind', 'NCAA_ovc', 'NCAA_col', 'NCAA_usa', 'NCAA_bigw'],
        'soccer_logos': ['Soccer - Premier League (England)', 'Soccer - La Liga (Spain)', 'Soccer - Bundesliga (Germany)', 'Soccer - Serie A (Italy)', 'Soccer - Ligue 1 (France)', 'Soccer - Champions League', 'Soccer - Other Teams'],
        'milb_logos': 'MiLB'
    }
    
    # Read the abbreviations file
    abbreviations_file = Path("assets/sports/all_team_abbreviations.txt")
    if not abbreviations_file.exists():
        print("Error: all_team_abbreviations.txt not found")
        return
    
    with open(abbreviations_file, 'r') as f:
        content = f.read()
    
    # Parse teams from the abbreviations file
    teams_by_sport = {}
    current_section = None
    
    for line in content.split('\n'):
        original_line = line
        line = line.strip()
        
        # Check if this is a section header (not indented and no arrow)
        if line and not original_line.startswith('  ') and ' => ' not in line:
            current_section = line
            continue
        
        # Check if this is a team entry (indented and has arrow)
        if original_line.startswith('  ') and ' => ' in line:
            parts = line.split(' => ')
            if len(parts) == 2:
                abbr = parts[0].strip()
                team_name = parts[1].strip()
                
                if current_section not in teams_by_sport:
                    teams_by_sport[current_section] = []
                teams_by_sport[current_section].append((abbr, team_name))
    
    # Collect all missing teams
    all_missing_teams = []
    
    for logo_dir, sections in sports_dirs.items():
        logo_path = Path(f"assets/sports/{logo_dir}")
        
        if not logo_path.exists():
            print(f"⚠️  Logo directory not found: {logo_path}")
            continue
        
        # Get all PNG files in the directory
        logo_files = [f.stem for f in logo_path.glob("*.png")]
        
        # Check teams for this sport
        if isinstance(sections, str):
            sections = [sections]
        
        for section in sections:
            if section not in teams_by_sport:
                continue
            
            missing_teams = []
            
            for abbr, team_name in teams_by_sport[section]:
                # Check if logo exists (case-insensitive)
                logo_found = False
                for logo_file in logo_files:
                    if logo_file.lower() == abbr.lower():
                        logo_found = True
                        break
                
                if not logo_found:
                    missing_teams.append((abbr, team_name))
            
            if missing_teams:
                all_missing_teams.extend([(section, abbr, team_name) for abbr, team_name in missing_teams])
    
    # Sort by sport and then by team abbreviation
    all_missing_teams.sort(key=lambda x: (x[0], x[1]))
    
    # Save to file
    output_file = "missing_team_logos.txt"
    
    with open(output_file, 'w') as f:
        f.write("=" * 80 + "\n")
        f.write("MISSING TEAM LOGOS - COMPLETE LIST\n")
        f.write("=" * 80 + "\n")
        f.write(f"Total missing teams: {len(all_missing_teams)}\n")
        f.write("\n")
        
        current_sport = None
        for section, abbr, team_name in all_missing_teams:
            if section != current_sport:
                current_sport = section
                f.write(f"\n{section.upper()}:\n")
                f.write("-" * len(section) + "\n")
            
            f.write(f"  {abbr:>8} => {team_name}\n")
        
        f.write("\n" + "=" * 80 + "\n")
        f.write("SUMMARY BY SPORT:\n")
        f.write("=" * 80 + "\n")
        
        # Count by sport
        sport_counts = {}
        for section, abbr, team_name in all_missing_teams:
            if section not in sport_counts:
                sport_counts[section] = 0
            sport_counts[section] += 1
        
        for sport, count in sorted(sport_counts.items()):
            f.write(f"{sport:>30}: {count:>3} missing\n")
        
        f.write("\n" + "=" * 80 + "\n")
        f.write("FILENAMES NEEDED:\n")
        f.write("=" * 80 + "\n")
        f.write("Add these PNG files to their respective directories:\n")
        f.write("\n")
        
        for section, abbr, team_name in all_missing_teams:
            # Determine the directory based on the section
            if 'MLB' in section:
                dir_name = 'mlb_logos'
            elif 'NBA' in section:
                dir_name = 'nba_logos'
            elif 'NFL' in section:
                dir_name = 'nfl_logos'
            elif 'NHL' in section:
                dir_name = 'nhl_logos'
            elif 'NCAA' in section:
                dir_name = 'ncaa_logos'
            elif 'Soccer' in section:
                dir_name = 'soccer_logos'
            elif 'MiLB' in section:
                dir_name = 'milb_logos'
            else:
                dir_name = 'unknown'
            
            f.write(f"assets/sports/{dir_name}/{abbr}.png\n")
    
    print(f"✅ Missing teams list saved to: {output_file}")
    print(f"📊 Total missing teams: {len(all_missing_teams)}")

if __name__ == "__main__":
    save_missing_teams()
