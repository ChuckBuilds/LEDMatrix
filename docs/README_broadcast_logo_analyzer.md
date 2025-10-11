# Broadcast Logo Analyzer

This script analyzes broadcast channel logos to ensure we have proper logos for every game and identifies missing or problematic logos that might show as white boxes.

## Important Notes

**This script must be run on the Raspberry Pi** where the LEDMatrix project is located, as it needs to access the actual logo files in the `assets/broadcast_logos/` directory.

## Usage

### On Raspberry Pi (Recommended)

```bash
# SSH into your Raspberry Pi
ssh pi@your-pi-ip

# Navigate to the LEDMatrix project directory
cd /path/to/LEDMatrix

# Run the analyzer
python test/analyze_broadcast_logos.py
```

### Local Testing (Optional)

If you want to test the script logic locally, you can:

1. Copy some logo files from your Pi to your local machine
2. Place them in `assets/broadcast_logos/` directory
3. Run the script locally

## What the Script Does

1. **Checks Logo Mappings**: Verifies all broadcast channel names in `BROADCAST_LOGO_MAP` have corresponding logo files
2. **Validates File Existence**: Ensures all referenced logo files actually exist
3. **Analyzes Logo Quality**: 
   - Checks dimensions (too small/large)
   - Analyzes transparency handling
   - Detects potential white box issues
   - Measures content density
4. **Identifies Issues**:
   - Missing logos
   - Problematic logos (corrupted, too transparent, etc.)
   - Orphaned logo files (exist but not mapped)
5. **Generates Report**: Creates both console output and JSON report

## Output

The script generates:
- **Console Report**: Detailed analysis with recommendations
- **JSON Report**: `test/broadcast_logo_analysis.json` with structured data

## Common Issues Found

- **White Boxes**: Usually caused by:
  - Missing logo files
  - Corrupted image files
  - Images that are mostly transparent
  - Images with very low content density
- **Missing Logos**: Broadcast channels that don't have corresponding logo files
- **Orphaned Logos**: Logo files that exist but aren't mapped to any broadcast channel

## Recommendations

The script provides specific recommendations for each issue found, such as:
- Adding missing logo files
- Fixing problematic logos
- Optimizing logo dimensions
- Ensuring proper transparency handling

## Example Output

```
BROADCAST LOGO ANALYSIS REPORT
================================================================================

SUMMARY:
  Total broadcast mappings: 44
  Existing logos: 40
  Missing logos: 2
  Problematic logos: 2
  Orphaned logos: 1

MISSING LOGOS (2):
--------------------------------------------------
  New Channel -> newchannel.png
    Expected: /path/to/LEDMatrix/assets/broadcast_logos/newchannel.png

PROBLEMATIC LOGOS (2):
--------------------------------------------------
  ESPN -> espn
    Issue: Very low content density: 2.1%
    Recommendation: Logo may appear as a white box - check content
```

## Troubleshooting

If you see errors about missing dependencies:
```bash
pip install Pillow
```

If the script can't find the broadcast logos directory, ensure you're running it from the LEDMatrix project root directory.
