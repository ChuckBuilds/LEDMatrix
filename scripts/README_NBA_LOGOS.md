# NBA Logo Downloader

This script downloads all NBA team logos from the ESPN API and saves
them in the `assets/sports/nba_logos/` directory.

> **Heads up:** the NBA leaderboard and basketball scoreboards now
> live as plugins in the
> [`ledmatrix-plugins`](https://github.com/ChuckBuilds/ledmatrix-plugins)
> repo (`basketball-scoreboard`, `ledmatrix-leaderboard`). Those
> plugins download the logos they need automatically on first display.
> This standalone script is mainly useful when you want to pre-populate
> the assets directory ahead of time, or for development/debugging.

All commands below should be run from the LEDMatrix project root.

## Usage

### Basic Usage
```bash
python3 scripts/download_nba_logos.py
```

### Force Re-download
If you want to re-download all logos (even if they already exist):
```bash
python3 scripts/download_nba_logos.py --force
```

### Quiet Mode
Reduce logging output:
```bash
python3 scripts/download_nba_logos.py --quiet
```

### Combined Options
```bash
python3 scripts/download_nba_logos.py --force --quiet
```

## What It Does

1. **Fetches NBA Team Data**: Gets the complete list of NBA teams from ESPN API
2. **Downloads Logos**: Downloads each team's logo from ESPN's servers
3. **Saves Locally**: Saves logos as `{team_abbr}.png` in `assets/sports/nba_logos/`
4. **Skips Existing**: By default, skips teams that already have logos
5. **Rate Limiting**: Includes small delays between downloads to be respectful to the API

## Expected Output

```
🏀 Starting NBA logo download...
Target directory: assets/sports/nba_logos/
Force download: False
✅ NBA logo download complete!
📊 Summary: 30 downloaded, 0 failed
🎉 NBA logos are now ready for use in the leaderboard!
```

## File Structure

After running the script, you'll have:
```
assets/sports/nba_logos/
├── ATL.png  # Atlanta Hawks
├── BOS.png  # Boston Celtics
├── BKN.png  # Brooklyn Nets
├── CHA.png  # Charlotte Hornets
├── CHI.png  # Chicago Bulls
├── CLE.png  # Cleveland Cavaliers
├── DAL.png  # Dallas Mavericks
├── DEN.png  # Denver Nuggets
├── DET.png  # Detroit Pistons
├── GSW.png  # Golden State Warriors
├── HOU.png  # Houston Rockets
├── IND.png  # Indiana Pacers
├── LAC.png  # LA Clippers
├── LAL.png  # Los Angeles Lakers
├── MEM.png  # Memphis Grizzlies
├── MIA.png  # Miami Heat
├── MIL.png  # Milwaukee Bucks
├── MIN.png  # Minnesota Timberwolves
├── NOP.png  # New Orleans Pelicans
├── NYK.png  # New York Knicks
├── OKC.png  # Oklahoma City Thunder
├── ORL.png  # Orlando Magic
├── PHI.png  # Philadelphia 76ers
├── PHX.png  # Phoenix Suns
├── POR.png  # Portland Trail Blazers
├── SAC.png  # Sacramento Kings
├── SAS.png  # San Antonio Spurs
├── TOR.png  # Toronto Raptors
├── UTA.png  # Utah Jazz
└── WAS.png  # Washington Wizards
```

## Integration with NBA plugins

Once the logos are in `assets/sports/nba_logos/`, both the
`basketball-scoreboard` and `ledmatrix-leaderboard` plugins will pick
them up automatically and skip their own first-run download. This is
useful if you want to deploy a Pi without internet access to ESPN, or
if you want to preview the display on your dev machine without
waiting for downloads.

## Troubleshooting

### "Import error: No module named 'requests'"
Make sure you're running this from the LEDMatrix project directory where all dependencies are installed.

### "Permission denied" errors
Make sure the script has write permissions to the `assets/sports/nba_logos/` directory.

### Some logos fail to download
This is normal - some teams might have temporary API issues or the ESPN API might be rate-limiting. The script will continue with the successful downloads.

## Requirements

- Python 3.9+ (matches the project's overall minimum)
- `requests` library (already in `requirements.txt`)
- Write access to `assets/sports/nba_logos/` directory
