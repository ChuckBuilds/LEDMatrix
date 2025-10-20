# Nested Config Schema - Visual Comparison

## Before: Flat Schema (32 Properties in Long List)

```
┌─────────────────────────────────────────┐
│ Plugin Configuration                     │
├─────────────────────────────────────────┤
│ ✅ Plugin Status: Enabled               │
├─────────────────────────────────────────┤
│ Display Duration: [30] seconds           │
│ Game Display Duration: [15] seconds      │
│ Update Interval: [3600] seconds          │
│                                          │
│ ☑ NFL Enabled                           │
│ NFL Favorite Teams: [TB, DAL, GB]        │
│ ☑ NFL Show Live                         │
│ ☑ NFL Show Recent                       │
│ ☑ NFL Show Upcoming                     │
│ NFL Recent Games To Show: [5]            │
│ NFL Upcoming Games To Show: [1]          │
│ ☐ NFL Show Records                      │
│ ☐ NFL Show Ranking                      │
│ ☑ NFL Show Odds                         │
│ ☑ NFL Show Favorite Teams Only          │
│ ☐ NFL Show All Live                     │
│                                          │
│ ☐ NCAA FB Enabled                       │
│ NCAA FB Favorite Teams: []               │
│ ☑ NCAA FB Show Live                     │
│ ☑ NCAA FB Show Recent                   │
│ ☑ NCAA FB Show Upcoming                 │
│ NCAA FB Recent Games To Show: [1]        │
│ NCAA FB Upcoming Games To Show: [1]      │
│ ☐ NCAA FB Show Records                  │
│ ☑ NCAA FB Show Ranking                  │
│ ☑ NCAA FB Show Odds                     │
│ ☑ NCAA FB Show Favorite Teams Only      │
│ ☐ NCAA FB Show All Live                 │
│                                          │
│           [Cancel]  [Save]               │
└─────────────────────────────────────────┘

Issues:
❌ Overwhelming - hard to scan
❌ No visual grouping
❌ Repetitive prefixes (nfl_, ncaa_fb_)
❌ Difficult to find specific settings
❌ No sense of hierarchy
```

## After: Nested Schema (Same 32 Properties, Organized)

```
┌─────────────────────────────────────────┐
│ Plugin Configuration                     │
├─────────────────────────────────────────┤
│ ✅ Plugin Status: Enabled               │
├─────────────────────────────────────────┤
│ Display Duration: [30] seconds           │
│ Game Display Duration: [15] seconds      │
│ Update Interval: [3600] seconds          │
│                                          │
├─────────────────────────────────────────┤
│ NFL Settings                           ▼ │  ← Click to expand/collapse
├─────────────────────────────────────────┤
│   ☑ Enabled                             │
│   Favorite Teams: [TB, DAL, GB]          │
│                                          │
│   ┌───────────────────────────────────┐ │
│   │ Display Modes                   ▼ │ │  ← Nested section
│   ├───────────────────────────────────┤ │
│   │   ☑ Show Live                     │ │
│   │   ☑ Show Recent                   │ │
│   │   ☑ Show Upcoming                 │ │
│   └───────────────────────────────────┘ │
│                                          │
│   ┌───────────────────────────────────┐ │
│   │ Game Limits                     ▼ │ │
│   ├───────────────────────────────────┤ │
│   │   Recent Games: [5]               │ │
│   │   Upcoming Games: [1]             │ │
│   └───────────────────────────────────┘ │
│                                          │
│   ┌───────────────────────────────────┐ │
│   │ Display Options                 ▼ │ │
│   ├───────────────────────────────────┤ │
│   │   ☐ Show Records                  │ │
│   │   ☐ Show Ranking                  │ │
│   │   ☑ Show Odds                     │ │
│   └───────────────────────────────────┘ │
│                                          │
│   ┌───────────────────────────────────┐ │
│   │ Filtering Options               ▼ │ │
│   ├───────────────────────────────────┤ │
│   │   ☑ Show Favorite Teams Only      │ │
│   │   ☐ Show All Live                 │ │
│   └───────────────────────────────────┘ │
│                                          │
├─────────────────────────────────────────┤
│ NCAA Football Settings                ▶ │  ← Collapsed by default
├─────────────────────────────────────────┤
│                                          │
│           [Cancel]  [Save]               │
└─────────────────────────────────────────┘

Benefits:
✅ Clear organization - easy to scan
✅ Logical grouping of related settings
✅ Collapsible sections hide complexity
✅ Easy to find specific settings
✅ Clear hierarchy and relationships
✅ Cleaner, more professional UI
```

## Real UI Examples

### Collapsed State (Default)
When you first open the config, major sections are collapsed:

```
┌────────────────────────────────┐
│ ✅ Plugin Status: Enabled     │
│ Display Duration: [30]         │
│                                │
│ ┏━━━━━━━━━━━━━━━━━━━━━━━━━┓  │
│ ┃ NFL Settings          ▶ ┃  │ ← Click to expand
│ ┗━━━━━━━━━━━━━━━━━━━━━━━━━┛  │
│                                │
│ ┏━━━━━━━━━━━━━━━━━━━━━━━━━┓  │
│ ┃ NCAA Football Settings▶ ┃  │
│ ┗━━━━━━━━━━━━━━━━━━━━━━━━━┛  │
│                                │
│      [Cancel]  [Save]          │
└────────────────────────────────┘
```

### Expanded State
Click a section to see its contents:

```
┌────────────────────────────────┐
│ ✅ Plugin Status: Enabled     │
│ Display Duration: [30]         │
│                                │
│ ┏━━━━━━━━━━━━━━━━━━━━━━━━━┓  │
│ ┃ NFL Settings          ▼ ┃  │ ← Expanded
│ ┣━━━━━━━━━━━━━━━━━━━━━━━━━┫  │
│ ┃ ☑ Enabled               ┃  │
│ ┃ Teams: [TB, DAL, GB]    ┃  │
│ ┃                         ┃  │
│ ┃ ┌─────────────────────┐ ┃  │
│ ┃ │ Display Modes     ▶ │ ┃  │ ← Sub-section
│ ┃ └─────────────────────┘ ┃  │
│ ┃                         ┃  │
│ ┃ ┌─────────────────────┐ ┃  │
│ ┃ │ Game Limits       ▶ │ ┃  │
│ ┃ └─────────────────────┘ ┃  │
│ ┗━━━━━━━━━━━━━━━━━━━━━━━━━┛  │
│                                │
│ ┏━━━━━━━━━━━━━━━━━━━━━━━━━┓  │
│ ┃ NCAA Football Settings▶ ┃  │
│ ┗━━━━━━━━━━━━━━━━━━━━━━━━━┛  │
│                                │
│      [Cancel]  [Save]          │
└────────────────────────────────┘
```

### Fully Expanded (All Sections Open)
User can expand everything to see all options:

```
┌────────────────────────────────┐
│ ✅ Plugin Status: Enabled     │
│ Display Duration: [30]         │
│                                │
│ ┏━━━━━━━━━━━━━━━━━━━━━━━━━┓  │
│ ┃ NFL Settings          ▼ ┃  │
│ ┣━━━━━━━━━━━━━━━━━━━━━━━━━┫  │
│ ┃ ☑ Enabled               ┃  │
│ ┃ Teams: [TB, DAL, GB]    ┃  │
│ ┃                         ┃  │
│ ┃ ┌─────────────────────┐ ┃  │
│ ┃ │ Display Modes     ▼ │ ┃  │
│ ┃ ├─────────────────────┤ ┃  │
│ ┃ │ ☑ Show Live         │ ┃  │
│ ┃ │ ☑ Show Recent       │ ┃  │
│ ┃ │ ☑ Show Upcoming     │ ┃  │
│ ┃ └─────────────────────┘ ┃  │
│ ┃                         ┃  │
│ ┃ ┌─────────────────────┐ ┃  │
│ ┃ │ Game Limits       ▼ │ ┃  │
│ ┃ ├─────────────────────┤ ┃  │
│ ┃ │ Recent: [5]         │ ┃  │
│ ┃ │ Upcoming: [1]       │ ┃  │
│ ┃ └─────────────────────┘ ┃  │
│ ┃                         ┃  │
│ ┃ ┌─────────────────────┐ ┃  │
│ ┃ │ Display Options   ▼ │ ┃  │
│ ┃ ├─────────────────────┤ ┃  │
│ ┃ │ ☐ Show Records      │ ┃  │
│ ┃ │ ☐ Show Ranking      │ ┃  │
│ ┃ │ ☑ Show Odds         │ ┃  │
│ ┃ └─────────────────────┘ ┃  │
│ ┗━━━━━━━━━━━━━━━━━━━━━━━━━┛  │
│                                │
│      [Cancel]  [Save]          │
└────────────────────────────────┘
```

## Color Coding

The UI uses color to show hierarchy:

```
┌────────────────────────────────┐
│ 🔵 Top Level (blue background) │  ← Plugin Status
├────────────────────────────────┤
│ ⚪ Top-level fields (white)    │  ← display_duration
│                                │
│ ┏━━━━━━━━━━━━━━━━━━━━━━━━━┓  │
│ ┃ ⬜ Main Section (gray)    ┃  │  ← NFL Settings
│ ┣━━━━━━━━━━━━━━━━━━━━━━━━━┫  │
│ ┃ ⚪ Section fields         ┃  │
│ ┃                           ┃  │
│ ┃ ┌─────────────────────┐   ┃  │
│ ┃ │ 🟦 Nested (darker)  │   ┃  │  ← Display Modes
│ ┃ ├─────────────────────┤   ┃  │
│ ┃ │ ⚪ Nested fields     │   ┃  │
│ ┃ └─────────────────────┘   ┃  │
│ ┗━━━━━━━━━━━━━━━━━━━━━━━━━┛  │
└────────────────────────────────┘
```

## Interaction Flow

1. **Open Config Modal**
   - Major sections collapsed
   - Quick overview of structure
   - Choose which section to configure

2. **Expand Section**
   - Click section header
   - Chevron rotates: ▶ → ▼
   - Section smoothly expands
   - Sub-sections visible but collapsed

3. **Expand Sub-section (Optional)**
   - Click sub-section header
   - Drill down to specific settings
   - Can expand multiple sub-sections

4. **Configure Settings**
   - Change values in any open section
   - Collapse sections you're done with
   - Visual feedback on all changes

5. **Save**
   - All values (nested or flat) saved
   - Nested structure preserved in config
   - Success notification

## Use Cases

### Quick Changes
```
User wants to enable NCAA Football
1. Open config
2. Click "NCAA Football Settings" ▶
3. Check "Enabled" ☑
4. Save
```

### Deep Configuration
```
User wants to configure NFL display options
1. Open config
2. Click "NFL Settings" ▶
3. Click "Display Options" ▶
4. Adjust show_records, show_ranking, show_odds
5. Save
```

### Bulk Review
```
User wants to review all settings
1. Open config
2. Expand all sections (click each header)
3. Scan through organized sections
4. Make any changes
5. Save
```

## Summary

**Nested schemas transform:**
- 32 flat properties in a list
- Into 2 collapsible main sections
- With 4 organized sub-sections each
- All in a clean, intuitive UI

**Result:**
- Same functionality
- Better organization
- Easier to use
- More professional appearance
- Scales to 100+ properties

This is especially important for complex plugins like:
- Football (32 properties)
- Baseball (100+ properties)
- Basketball (30+ properties)
- Any plugin with multiple sports/categories/modes

