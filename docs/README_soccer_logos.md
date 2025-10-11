# Soccer Logo Checker and Downloader

## Overview

The `check_soccer_logos.py` script automatically checks for missing logos of major teams from supported soccer leagues and downloads them from ESPN API if missing.

## Supported Leagues

- **Premier League** (eng.1) - 20 teams
- **La Liga** (esp.1) - 15 teams  
- **Bundesliga** (ger.1) - 15 teams
- **Serie A** (ita.1) - 14 teams
- **Ligue 1** (fra.1) - 12 teams
- **Liga Portugal** (por.1) - 15 teams
- **Champions League** (uefa.champions) - 13 major teams
- **Europa League** (uefa.europa) - 11 major teams
- **MLS** (usa.1) - 25 teams

**Total: 140 major teams across 9 leagues**

## Usage

```bash
cd test
python check_soccer_logos.py
```

## What It Does

1. **Checks Existing Logos**: Scans `assets/sports/soccer_logos/` for existing logo files
2. **Identifies Missing Logos**: Compares against the list of major teams
3. **Downloads from ESPN**: Automatically fetches missing logos from ESPN API
4. **Creates Placeholders**: If download fails, creates colored placeholder logos
5. **Provides Summary**: Shows detailed statistics of the process

## Output

The script provides detailed logging showing:
- ✅ Existing logos found
- ⬇️ Successfully downloaded logos  
- ❌ Failed downloads (with placeholders created)
- 📊 Summary statistics

## Example Output

```
🔍 Checking por.1 (Liga Portugal)
📊 Found 2 existing logos, 13 missing
✅ Existing: BEN, POR
❌ Missing: ARO (Arouca), BRA (SC Braga), CHA (Chaves), ...

Downloading ARO (Arouca) from por.1
✅ Successfully downloaded ARO (Arouca)
...

📈 SUMMARY
✅ Existing logos: 25
⬇️ Downloaded: 115  
❌ Failed downloads: 0
📊 Total teams checked: 140
```

## Logo Storage

All logos are stored in: `assets/sports/soccer_logos/`

Format: `{TEAM_ABBREVIATION}.png` (e.g., `BEN.png`, `POR.png`, `LIV.png`)

## Integration with LEDMatrix

These logos are automatically used by the soccer manager when displaying:
- Live games
- Recent games  
- Upcoming games
- Odds ticker
- Leaderboards

The system will automatically download missing logos on-demand during normal operation, but this script ensures all major teams have logos available upfront.

## Notes

- **Real Logos**: Downloaded from ESPN's official API
- **Placeholders**: Created for teams not found in ESPN data
- **Caching**: Logos are cached locally to avoid repeated downloads
- **Format**: All logos converted to RGBA PNG format for LEDMatrix compatibility
- **Size**: Logos are optimized for LED matrix display (typically 36x36 pixels)

## Troubleshooting

If downloads fail:
1. Check internet connectivity
2. Verify ESPN API is accessible
3. Some teams may not be in current league rosters
4. Placeholder logos will be created as fallback

The script is designed to be robust and will always provide some form of logo for every team.
