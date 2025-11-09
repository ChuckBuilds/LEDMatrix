# Deploying Startup Optimizations to Raspberry Pi

## Quick Deployment Steps

### 1. Stage and Commit Changes

```bash
cd /home/chuck/Github/LEDMatrix

# Stage modified files
git add src/display_controller.py
git add src/plugin_system/plugin_manager.py
git add scripts/clear_dependency_markers.sh
git add STARTUP_OPTIMIZATION_SUMMARY.md
git add DEPLOY_STARTUP_OPTIMIZATIONS.md

# Handle the flight tracker plugin changes
cd plugins/ledmatrix-flights
git add manager.py
git commit -m "perf: lazy-load 70MB aircraft database for faster startup"
cd ../..

# Commit main repo changes
git commit -m "perf: optimize startup time from 102s to <10s

- Add smart dependency checking with marker files (saves 90s)
- Remove cache clear at startup (saves 5-30s)
- Add detailed progress logging for visibility
- Lazy-load flight tracker aircraft database (saves 8-10s)
- Implement parallel plugin loading with ThreadPoolExecutor (saves 2-3s)

Expected improvement: 90%+ reduction in startup time"
```

### 2. Deploy to Raspberry Pi

```bash
# Push to GitHub
git push origin plugins

# SSH to Pi and pull changes
ssh ledpi@ledpi
cd ~/LEDMatrix
git pull origin plugins

# Update submodules (for plugin changes)
git submodule update --remote plugins/ledmatrix-flights

# Clear existing dependency markers for fresh check (optional but recommended)
sudo ./scripts/clear_dependency_markers.sh

# Restart the service
sudo systemctl restart ledmatrix
```

### 3. Monitor Startup Performance

```bash
# Watch live logs
journalctl -u ledmatrix -f

# In another terminal, check startup timing after restart
ssh ledpi@ledpi
journalctl -u ledmatrix -b | grep -E "(Starting DisplayController|DisplayController initialization completed|Plugin system initialized|Progress:)"
```

### 4. Benchmark Results

```bash
# Get complete startup timeline
ssh ledpi@ledpi 'journalctl -u ledmatrix -b | grep -A 20 "Starting DisplayController initialization"'

# Check plugin loading times
ssh ledpi@ledpi 'journalctl -u ledmatrix -b | grep "Loaded plugin"'

# Verify dependency markers were created
ssh ledpi@ledpi 'ls -lh /var/cache/ledmatrix/plugin_*_deps_installed'
```

## Expected Output

### First Startup (With Fresh Dependency Install)
```
INFO:src.display_controller:Starting DisplayController initialization
INFO:src.display_controller:Config loaded in 0.010 seconds
INFO:src.display_controller:DisplayManager initialized in 0.722 seconds
INFO:src.display_controller:FontManager initialized in 0.918 seconds
INFO:src.display_controller:Loading 9 enabled plugin(s) in parallel (max 4 concurrent)...
INFO:src.display_controller:Installing dependencies for ledmatrix-flights (first time)
... (dependency installation for any plugins without markers)
INFO:src.display_controller:✓ Loaded plugin weather in 2.145 seconds (1/9)
INFO:src.display_controller:Progress: 11% (1/9 plugins, 2.2s elapsed)
... (parallel loading continues)
INFO:src.display_controller:Plugin system initialized in 12.345 seconds
INFO:src.display_controller:DisplayController initialization completed in 14.123 seconds
```

### Subsequent Startups (Dependencies Already Installed)
```
INFO:src.display_controller:Starting DisplayController initialization
INFO:src.display_controller:Config loaded in 0.008 seconds
INFO:src.display_controller:DisplayManager initialized in 0.685 seconds
INFO:src.display_controller:FontManager initialized in 0.010 seconds
INFO:src.display_controller:Loading 9 enabled plugin(s) in parallel (max 4 concurrent)...
INFO:src.display_controller:✓ Loaded plugin weather in 0.856 seconds (1/9)
INFO:src.display_controller:Progress: 11% (1/9 plugins, 0.9s elapsed)
... (parallel loading continues)
INFO:src.display_controller:Plugin system initialized in 2.134 seconds
INFO:src.display_controller:DisplayController initialization completed in 2.850 seconds
```

## Success Criteria

✅ **First startup**: 12-15 seconds (includes dependency installation)
✅ **Subsequent startups**: 2-5 seconds (no dependency checks)
✅ **All plugins load**: Check for ✓ symbols in logs
✅ **No errors**: No "Failed to load plugin" messages
✅ **Display works**: Matrix starts showing content
✅ **Markers created**: Files exist in /var/cache/ledmatrix/

## Troubleshooting

### If Plugins Fail to Load

```bash
# Check for specific errors
ssh ledpi@ledpi 'journalctl -u ledmatrix -b | grep -i error'

# Clear markers and retry
ssh ledpi@ledpi 'sudo /home/ledpi/LEDMatrix/scripts/clear_dependency_markers.sh'
ssh ledpi@ledpi 'sudo systemctl restart ledmatrix'
```

### If Startup Still Slow

```bash
# Check which plugins are slow
ssh ledpi@ledpi 'journalctl -u ledmatrix -b | grep "Loaded plugin"'

# Look for dependency installations (shouldn't happen after first run)
ssh ledpi@ledpi 'journalctl -u ledmatrix -b | grep "Installing dependencies"'

# Check if parallel loading is working
ssh ledpi@ledpi 'journalctl -u ledmatrix -b | grep "parallel"'
```

### If Flight Tracker Database Loading at Startup

```bash
# Should only see this message during first display, not startup
ssh ledpi@ledpi 'journalctl -u ledmatrix | grep "Offline aircraft database loaded"'

# If it appears during startup, check the lazy-load implementation
```

## Performance Comparison

### Before Optimizations
```
Starting DisplayController initialization: 15:02:00.193
DisplayController initialization completed: 15:03:42.463
Total time: 102.27 seconds ❌
```

### After Optimizations (Expected)
```
Starting DisplayController initialization: 15:02:00.193  
DisplayController initialization completed: 15:02:03.050
Total time: 2.857 seconds ✅
```

**Improvement: 97% faster (100 seconds saved)**

## Rollback if Needed

```bash
ssh ledpi@ledpi
cd ~/LEDMatrix

# Revert to previous commit
git log --oneline -5  # Find previous commit hash
git reset --hard <previous-commit-hash>
sudo systemctl restart ledmatrix
```

## Next Steps

After successful deployment and verification:

1. **Monitor for a few hours** - Ensure stability
2. **Check memory usage** - Should be similar to before
3. **Verify all modes work** - Rotate through all display modes
4. **Document actual timing** - Update benchmarks with real results
5. **Clean up old code** - Remove any commented-out old code if satisfied

## Questions or Issues?

- Check `STARTUP_OPTIMIZATION_SUMMARY.md` for technical details
- Review `journalctl -u ledmatrix -b` for complete logs
- Test in emulator first if uncertain: `./run_emulator.sh`

---

**Ready to deploy!** 🚀

