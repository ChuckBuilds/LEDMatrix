# Merge Conflict Resolution Plan: plugins → main

## Overview
This document outlines the plan to resolve merge conflicts when merging the `plugins` branch into `main`. The conflicts occur because the plugins branch refactored the architecture from built-in managers to a plugin-based system.

## Conflicted Files

### 1. `src/clock.py`
**Status**: EXISTS in `main`, DELETED in `plugins`

**Reason for Conflict**: 
- In `main`: Clock functionality is implemented as a built-in manager (`src/clock.py`)
- In `plugins`: Clock functionality has been migrated to a plugin (`plugins/clock-simple/manager.py`)

**Resolution Strategy**: 
- ✅ **DELETE** `src/clock.py` from `main` when merging
- The plugin version at `plugins/clock-simple/manager.py` replaces this file
- Functionality is preserved in the plugin architecture

**Action Required**:
```bash
git rm src/clock.py
```

**Verification**:
- Ensure `plugins/clock-simple/` plugin exists and works
- Verify clock functionality works via the plugin system

---

### 2. `src/news_manager.py`
**Status**: EXISTS in `main`, DELETED in `plugins`

**Reason for Conflict**: 
- In `main`: News functionality is implemented as a built-in manager (`src/news_manager.py`)
- In `plugins`: News functionality has been migrated to a plugin (`plugins/ledmatrix-news/manager.py`)

**Resolution Strategy**: 
- ✅ **DELETE** `src/news_manager.py` from `main` when merging
- The plugin version at `plugins/ledmatrix-news/manager.py` replaces this file
- Functionality is preserved in the plugin architecture

**Action Required**:
```bash
git rm src/news_manager.py
```

**Verification**:
- Ensure `plugins/ledmatrix-news/` plugin exists and works
- Verify news functionality works via the plugin system

---

### 3. `README.md`
**Status**: SIGNIFICANTLY DIFFERENT in both branches

**Main Differences**:

| Aspect | `main` branch | `plugins` branch |
|--------|--------------|------------------|
| Introduction | Has detailed "Core Features" section with screenshots | Has "Plugins Version is HERE!" introduction |
| Architecture | Describes built-in managers | Describes plugin-based architecture |
| Website Link | Includes link to website write-up | Removed website link, added ko-fi link |
| Content Focus | Feature showcase with images | Plugin system explanation |

**Resolution Strategy**: 
- ✅ **KEEP** `plugins` branch version as the base (it's current and accurate for plugin architecture)
- ⚠️ **CONSIDER** preserving valuable content from `main`:
  - The detailed "Core Features" section with screenshots might be valuable for documentation
  - The website write-up link might be worth preserving
  - However, since plugins branch is more current and accurate, prefer plugins version

**Recommended Approach**:
1. Keep plugins branch README.md as-is (it's current and accurate)
2. The old "Core Features" section in main is outdated for the plugin architecture
3. If website link is important, it can be added back to plugins version separately

**Action Required**:
```bash
# Accept plugins branch version
git checkout --theirs README.md
# OR manually review and merge, keeping plugins version as base
```

**Verification**:
- README.md accurately describes the plugin architecture
- All installation and configuration instructions are current
- Links are working

---

## Step-by-Step Resolution Process

### Step 1: Checkout main branch and prepare for merge
```bash
git checkout main
git fetch origin
git merge origin/plugins --no-commit --no-ff
```

### Step 2: Resolve file deletion conflicts
```bash
# Remove files that were migrated to plugins
git rm src/clock.py
git rm src/news_manager.py
```

### Step 3: Resolve README.md conflict
```bash
# Option A: Accept plugins version (recommended)
git checkout --theirs README.md

# Option B: Manually review and merge
# Edit README.md to combine best of both if needed
```

### Step 4: Verify no references to deleted files
```bash
# Check if any code references the deleted files
grep -r "from src.clock import" .
grep -r "from src.news_manager import" .
grep -r "import src.clock" .
grep -r "import src.news_manager" .

# If found, these need to be updated to use plugins instead
```

### Step 5: Test the resolved merge
```bash
# Verify plugins are loaded correctly
python3 -c "from src.plugin_system.plugin_manager import PluginManager; print('OK')"

# Check that clock-simple plugin exists
ls -la plugins/clock-simple/

# Check that ledmatrix-news plugin exists  
ls -la plugins/ledmatrix-news/
```

### Step 6: Complete the merge
```bash
git add .
git commit -m "Merge plugins into main: Remove deprecated managers, keep plugin-based README"
```

---

## Verification Checklist

- [ ] `src/clock.py` is deleted (functionality in `plugins/clock-simple/`)
- [ ] `src/news_manager.py` is deleted (functionality in `plugins/ledmatrix-news/`)
- [ ] `README.md` reflects plugin architecture (plugins branch version)
- [ ] No import statements reference deleted files
- [ ] Clock plugin works correctly
- [ ] News plugin works correctly
- [ ] All tests pass (if applicable)
- [ ] Documentation is accurate

---

## Notes

1. **No Code Changes Required**: The deletions are safe because:
   - Clock functionality exists in `plugins/clock-simple/manager.py`
   - News functionality exists in `plugins/ledmatrix-news/manager.py`
   - The plugin system loads these automatically

2. **README.md Decision**: Keeping plugins version is recommended because:
   - It accurately describes the current plugin-based architecture
   - The old "Core Features" section describes the old architecture
   - Users need current installation/configuration instructions

3. **Potential Issues**:
   - If any code in `main` still imports these files, those imports need to be removed
   - Configuration references to old managers may need updating
   - Documentation references may need updating

---

## Related Files to Check (Not Conflicted but Related)

These files might reference the deleted managers and should be checked:

- `display_controller.py` - May have references to Clock or NewsManager
- `config/config.json` - May have config sections for clock/news_manager
- Any test files that might test these managers
- Documentation files that reference these managers

