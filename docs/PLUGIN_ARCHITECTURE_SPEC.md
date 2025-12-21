# LEDMatrix Plugin Architecture Specification

## Executive Summary

This document outlines the transformation of the LEDMatrix project into a modular, plugin-based architecture that enables user-created displays. The goal is to create a flexible, extensible system similar to Home Assistant Community Store (HACS) where users can discover, install, and manage custom display managers from GitHub repositories.

### Key Decisions

1. **Gradual Migration**: Existing managers remain in core while new plugin infrastructure is built
2. **Migration Required**: Breaking changes with migration tools provided
3. **GitHub-Based Store**: Simple discovery system, packages served from GitHub repos
4. **Plugin Location**: `./plugins/` directory in project root

---

## Table of Contents

1. [Current Architecture Analysis](#current-architecture-analysis)
2. [Plugin System Design](#plugin-system-design)
3. [Plugin Store & Discovery](#plugin-store--discovery)
4. [Web UI Transformation](#web-ui-transformation)
5. [Migration Strategy](#migration-strategy)
6. [Plugin Developer Guidelines](#plugin-developer-guidelines)
7. [Technical Implementation Details](#technical-implementation-details)
8. [Best Practices & Standards](#best-practices--standards)
9. [Security Considerations](#security-considerations)
10. [Implementation Roadmap](#implementation-roadmap)

---

## 1. Current Architecture Analysis

### Current System Overview

**Core Components:**
- `display_controller.py`: Main orchestrator, hardcoded manager instantiation
- `display_manager.py`: Handles LED matrix rendering
- `config_manager.py`: Loads config from JSON files
- `cache_manager.py`: Caching layer for API calls
- `web_interface_v2.py`: Web UI with hardcoded manager references

**Manager Pattern:**
- All managers follow similar initialization: `__init__(config, display_manager, cache_manager)`
- Common methods: `update()` for data fetching, `display()` for rendering
- Located in `src/` with various naming conventions
- Hardcoded imports in display_controller and web_interface

**Configuration:**
- Monolithic `config.json` with sections for each manager
- Template-based updates via `config.template.json`
- Secrets in separate `config_secrets.json`

### Pain Points

1. **Tight Coupling**: Display controller has hardcoded imports for ~40+ managers
2. **Monolithic Config**: 650+ line config file, hard to navigate
3. **No Extensibility**: Users can't add custom displays without modifying core
4. **Update Conflicts**: Config template merges can fail with custom setups
5. **Scaling Issues**: Adding new displays requires core code changes

---

## 2. Plugin System Design

### Plugin Architecture

```
plugins/
├── clock-simple/
│   ├── manifest.json          # Plugin metadata
│   ├── manager.py              # Main plugin class
│   ├── requirements.txt        # Python dependencies
│   ├── assets/                 # Plugin-specific assets
│   │   └── fonts/
│   ├── config_schema.json      # JSON schema for validation
│   └── README.md               # Documentation
│
├── nhl-scoreboard/
│   ├── manifest.json
│   ├── manager.py
│   ├── requirements.txt
│   ├── assets/
│   │   └── logos/
│   └── README.md
│
└── weather-animated/
    ├── manifest.json
    ├── manager.py
    ├── requirements.txt
    ├── assets/
    │   └── animations/
    └── README.md
```

### Plugin Manifest Structure

```json
{
  "id": "clock-simple",
  "name": "Simple Clock",
  "version": "1.0.0",
  "author": "ChuckBuilds",
  "description": "A simple clock display with date",
  "homepage": "https://github.com/ChuckBuilds/ledmatrix-clock-simple",
  "entry_point": "manager.py",
  "class_name": "SimpleClock",
  "category": "time",
  "tags": ["clock", "time", "date"],
  "compatible_versions": [">=2.0.0"],
  "min_ledmatrix_version": "2.0.0",
  "max_ledmatrix_version": "3.0.0",
  "requires": {
    "python": ">=3.9",
    "display_size": {
      "min_width": 64,
      "min_height": 32
    }
  },
  "config_schema": "config_schema.json",
  "assets": {
    "fonts": ["assets/fonts/clock.bdf"],
    "images": []
  },
  "update_interval": 1,
  "default_duration": 15,
  "display_modes": ["clock"],
  "api_requirements": []
}
```

### Base Plugin Interface

```python
# src/plugin_system/base_plugin.py

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
import logging

class BasePlugin(ABC):
    """
    Base class that all plugins must inherit from.
    Provides standard interface and helper methods.
    """
    
    def __init__(self, plugin_id: str, config: Dict[str, Any], 
                 display_manager, cache_manager, plugin_manager):
        """
        Standard initialization for all plugins.
        
        Args:
            plugin_id: Unique identifier for this plugin instance
            config: Plugin-specific configuration
            display_manager: Shared display manager instance
            cache_manager: Shared cache manager instance
            plugin_manager: Reference to plugin manager for inter-plugin communication
        """
        self.plugin_id = plugin_id
        self.config = config
        self.display_manager = display_manager
        self.cache_manager = cache_manager
        self.plugin_manager = plugin_manager
        self.logger = logging.getLogger(f"plugin.{plugin_id}")
        self.enabled = config.get('enabled', True)
        
    @abstractmethod
    def update(self) -> None:
        """
        Fetch/update data for this plugin.
        Called based on update_interval in manifest.
        """
        pass
    
    @abstractmethod
    def display(self, force_clear: bool = False) -> None:
        """
        Render this plugin's display.
        Called during rotation or on-demand.
        
        Args:
            force_clear: If True, clear display before rendering
        """
        pass
    
    def get_display_duration(self) -> float:
        """
        Get the display duration for this plugin instance.
        Can be overridden based on dynamic content.
        
        Returns:
            Duration in seconds
        """
        return self.config.get('display_duration', 15.0)
    
    def validate_config(self) -> bool:
        """
        Validate plugin configuration against schema.
        Called during plugin loading.
        
        Returns:
            True if config is valid
        """
        # Implementation uses config_schema.json
        return True
    
    def cleanup(self) -> None:
        """
        Cleanup resources when plugin is unloaded.
        Override if needed.
        """
        pass
    
    def get_info(self) -> Dict[str, Any]:
        """
        Return plugin info for display in web UI.
        
        Returns:
            Dict with name, version, status, etc.
        """
        return {
            'id': self.plugin_id,
            'enabled': self.enabled,
            'config': self.config
        }
```

### Plugin Manager

```python
# src/plugin_system/plugin_manager.py

import os
import json
import importlib
import sys
from pathlib import Path
from typing import Dict, List, Optional, Any
import logging

class PluginManager:
    """
    Manages plugin discovery, loading, and lifecycle.
    """
    
    def __init__(self, plugins_dir: str = "plugins", 
                 config_manager=None, display_manager=None, cache_manager=None):
        self.plugins_dir = Path(plugins_dir)
        self.config_manager = config_manager
        self.display_manager = display_manager
        self.cache_manager = cache_manager
        self.logger = logging.getLogger(__name__)
        
        # Active plugins
        self.plugins: Dict[str, Any] = {}
        self.plugin_manifests: Dict[str, Dict] = {}
        
        # Ensure plugins directory exists
        self.plugins_dir.mkdir(exist_ok=True)
        
    def discover_plugins(self) -> List[str]:
        """
        Scan plugins directory for installed plugins.
        
        Returns:
            List of plugin IDs
        """
        discovered = []
        
        if not self.plugins_dir.exists():
            self.logger.warning(f"Plugins directory not found: {self.plugins_dir}")
            return discovered
        
        for item in self.plugins_dir.iterdir():
            if not item.is_dir():
                continue
            
            manifest_path = item / "manifest.json"
            if manifest_path.exists():
                try:
                    with open(manifest_path, 'r') as f:
                        manifest = json.load(f)
                    plugin_id = manifest.get('id')
                    if plugin_id:
                        discovered.append(plugin_id)
                        self.plugin_manifests[plugin_id] = manifest
                        self.logger.info(f"Discovered plugin: {plugin_id}")
                except Exception as e:
                    self.logger.error(f"Error reading manifest in {item}: {e}")
        
        return discovered
    
    def load_plugin(self, plugin_id: str) -> bool:
        """
        Load a plugin by ID.
        
        Args:
            plugin_id: Plugin identifier
            
        Returns:
            True if loaded successfully
        """
        if plugin_id in self.plugins:
            self.logger.warning(f"Plugin {plugin_id} already loaded")
            return True
        
        manifest = self.plugin_manifests.get(plugin_id)
        if not manifest:
            self.logger.error(f"No manifest found for plugin: {plugin_id}")
            return False
        
        try:
            # Add plugin directory to Python path
            plugin_dir = self.plugins_dir / plugin_id
            sys.path.insert(0, str(plugin_dir))
            
            # Import the plugin module
            entry_point = manifest.get('entry_point', 'manager.py')
            module_name = entry_point.replace('.py', '')
            module = importlib.import_module(module_name)
            
            # Get the plugin class
            class_name = manifest.get('class_name')
            if not class_name:
                self.logger.error(f"No class_name in manifest for {plugin_id}")
                return False
            
            plugin_class = getattr(module, class_name)
            
            # Get plugin config
            plugin_config = self.config_manager.load_config().get(plugin_id, {})
            
            # Instantiate the plugin
            plugin_instance = plugin_class(
                plugin_id=plugin_id,
                config=plugin_config,
                display_manager=self.display_manager,
                cache_manager=self.cache_manager,
                plugin_manager=self
            )
            
            # Validate configuration
            if not plugin_instance.validate_config():
                self.logger.error(f"Config validation failed for {plugin_id}")
                return False
            
            self.plugins[plugin_id] = plugin_instance
            self.logger.info(f"Loaded plugin: {plugin_id} v{manifest.get('version')}")
            return True
            
        except Exception as e:
            self.logger.error(f"Error loading plugin {plugin_id}: {e}", exc_info=True)
            return False
        finally:
            # Clean up Python path
            if str(plugin_dir) in sys.path:
                sys.path.remove(str(plugin_dir))
    
    def unload_plugin(self, plugin_id: str) -> bool:
        """
        Unload a plugin by ID.
        
        Args:
            plugin_id: Plugin identifier
            
        Returns:
            True if unloaded successfully
        """
        if plugin_id not in self.plugins:
            self.logger.warning(f"Plugin {plugin_id} not loaded")
            return False
        
        try:
            plugin = self.plugins[plugin_id]
            plugin.cleanup()
            del self.plugins[plugin_id]
            self.logger.info(f"Unloaded plugin: {plugin_id}")
            return True
        except Exception as e:
            self.logger.error(f"Error unloading plugin {plugin_id}: {e}")
            return False
    
    def reload_plugin(self, plugin_id: str) -> bool:
        """
        Reload a plugin (unload and load).
        
        Args:
            plugin_id: Plugin identifier
            
        Returns:
            True if reloaded successfully
        """
        if plugin_id in self.plugins:
            if not self.unload_plugin(plugin_id):
                return False
        return self.load_plugin(plugin_id)
    
    def get_plugin(self, plugin_id: str) -> Optional[Any]:
        """
        Get a loaded plugin instance.
        
        Args:
            plugin_id: Plugin identifier
            
        Returns:
            Plugin instance or None
        """
        return self.plugins.get(plugin_id)
    
    def get_all_plugins(self) -> Dict[str, Any]:
        """
        Get all loaded plugins.
        
        Returns:
            Dict of plugin_id: plugin_instance
        """
        return self.plugins
    
    def get_enabled_plugins(self) -> List[str]:
        """
        Get list of enabled plugin IDs.
        
        Returns:
            List of plugin IDs
        """
        return [pid for pid, plugin in self.plugins.items() if plugin.enabled]
```

### Display Controller Integration

```python
# Modified src/display_controller.py

class DisplayController:
    def __init__(self):
        # ... existing initialization ...
        
        # Initialize plugin system
        self.plugin_manager = PluginManager(
            plugins_dir="plugins",
            config_manager=self.config_manager,
            display_manager=self.display_manager,
            cache_manager=self.cache_manager
        )
        
        # Discover and load plugins
        discovered = self.plugin_manager.discover_plugins()
        logger.info(f"Discovered {len(discovered)} plugins")
        
        for plugin_id in discovered:
            if self.config.get(plugin_id, {}).get('enabled', False):
                self.plugin_manager.load_plugin(plugin_id)
        
        # Build available modes from plugins + legacy managers
        self.available_modes = []
        
        # Add legacy managers (existing code)
        if self.clock: self.available_modes.append('clock')
        # ... etc ...
        
        # Add plugin modes
        for plugin_id, plugin in self.plugin_manager.get_all_plugins().items():
            if plugin.enabled:
                manifest = self.plugin_manager.plugin_manifests.get(plugin_id, {})
                display_modes = manifest.get('display_modes', [plugin_id])
                self.available_modes.extend(display_modes)
    
    def display_mode(self, mode: str, force_clear: bool = False):
        """
        Render a specific mode (legacy or plugin).
        """
        # Check if it's a plugin mode
        for plugin_id, plugin in self.plugin_manager.get_all_plugins().items():
            manifest = self.plugin_manager.plugin_manifests.get(plugin_id, {})
            if mode in manifest.get('display_modes', []):
                plugin.display(force_clear=force_clear)
                return
        
        # Fall back to legacy manager handling
        if mode == 'clock' and self.clock:
            self.clock.display_time(force_clear=force_clear)
        # ... etc ...
```

### Base Classes and Code Reuse

#### Philosophy: Core Provides Stable Plugin API

The core LEDMatrix provides stable base classes and utilities for common plugin types. This approach balances code reuse with plugin independence.

#### Plugin API Base Classes

```
src/
├── plugin_system/
│   ├── base_plugin.py              # Core plugin interface (required)
│   └── base_classes/               # Optional base classes for common use cases
│       ├── __init__.py
│       ├── sports_plugin.py        # Generic sports displays
│       ├── hockey_plugin.py        # Hockey-specific features
│       ├── basketball_plugin.py    # Basketball-specific features
│       ├── baseball_plugin.py      # Baseball-specific features
│       ├── football_plugin.py      # Football-specific features
│       └── display_helpers.py      # Common rendering utilities
```

#### Sports Plugin Base Class

```python
# src/plugin_system/base_classes/sports_plugin.py

from src.plugin_system.base_plugin import BasePlugin
from typing import List, Dict, Any, Optional
import requests

class SportsPlugin(BasePlugin):
    """
    Base class for sports-related plugins.
    
    API Version: 1.0.0
    Stability: Stable - maintains backward compatibility
    
    Provides common functionality:
    - Favorite team filtering
    - ESPN API integration
    - Standard game data structures
    - Common rendering methods
    """
    
    API_VERSION = "1.0.0"
    
    def __init__(self, plugin_id, config, display_manager, cache_manager, plugin_manager):
        super().__init__(plugin_id, config, display_manager, cache_manager, plugin_manager)
        
        # Standard sports plugin configuration
        self.favorite_teams = config.get('favorite_teams', [])
        self.show_favorite_only = config.get('show_favorite_teams_only', True)
        self.show_odds = config.get('show_odds', True)
        self.show_records = config.get('show_records', True)
        self.logo_dir = config.get('logo_dir', 'assets/sports/logos')
    
    def filter_by_favorites(self, games: List[Dict]) -> List[Dict]:
        """
        Filter games to show only favorite teams.
        
        Args:
            games: List of game dictionaries
            
        Returns:
            Filtered list of games
        """
        if not self.show_favorite_only or not self.favorite_teams:
            return games
        
        return [g for g in games if self._is_favorite_game(g)]
    
    def _is_favorite_game(self, game: Dict) -> bool:
        """Check if game involves a favorite team."""
        home_team = game.get('home_team', '')
        away_team = game.get('away_team', '')
        return home_team in self.favorite_teams or away_team in self.favorite_teams
    
    def fetch_espn_data(self, sport: str, endpoint: str = "scoreboard", 
                        params: Dict = None) -> Optional[Dict]:
        """
        Fetch data from ESPN API.
        
        Args:
            sport: Sport identifier (e.g., 'hockey/nhl', 'basketball/nba')
            endpoint: API endpoint (default: 'scoreboard')
            params: Query parameters
            
        Returns:
            API response data or None on error
        """
        url = f"https://site.api.espn.com/apis/site/v2/sports/{sport}/{endpoint}"
        cache_key = f"espn_{sport}_{endpoint}"
        
        # Try cache first
        cached = self.cache_manager.get(cache_key, max_age=60)
        if cached:
            return cached
        
        try:
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            # Cache the response
            self.cache_manager.set(cache_key, data)
            
            return data
        except Exception as e:
            self.logger.error(f"Error fetching ESPN data: {e}")
            return None
    
    def render_team_logo(self, team_abbr: str, x: int, y: int, size: int = 16):
        """
        Render a team logo at specified position.
        
        Args:
            team_abbr: Team abbreviation
            x, y: Position on display
            size: Logo size in pixels
        """
        from pathlib import Path
        from PIL import Image
        
        # Try plugin assets first
        logo_path = Path(self.plugin_id) / "assets" / "logos" / f"{team_abbr}.png"
        
        # Fall back to core assets
        if not logo_path.exists():
            logo_path = Path(self.logo_dir) / f"{team_abbr}.png"
        
        if logo_path.exists():
            try:
                logo = Image.open(logo_path)
                logo = logo.resize((size, size), Image.LANCZOS)
                self.display_manager.image.paste(logo, (x, y))
            except Exception as e:
                self.logger.error(f"Error rendering logo for {team_abbr}: {e}")
    
    def render_score(self, away_team: str, away_score: int, 
                     home_team: str, home_score: int, 
                     x: int, y: int):
        """
        Render a game score in standard format.
        
        Args:
            away_team, away_score: Away team info
            home_team, home_score: Home team info
            x, y: Position on display
        """
        # Render away team
        self.render_team_logo(away_team, x, y)
        self.display_manager.draw_text(
            f"{away_score}",
            x=x + 20, y=y + 4,
            color=(255, 255, 255)
        )
        
        # Render home team
        self.render_team_logo(home_team, x + 40, y)
        self.display_manager.draw_text(
            f"{home_score}",
            x=x + 60, y=y + 4,
            color=(255, 255, 255)
        )
```

#### Hockey Plugin Base Class

```python
# src/plugin_system/base_classes/hockey_plugin.py

from src.plugin_system.base_classes.sports_plugin import SportsPlugin
from typing import Dict, List, Optional

class HockeyPlugin(SportsPlugin):
    """
    Base class for hockey plugins (NHL, NCAA Hockey, etc).
    
    API Version: 1.0.0
    Provides hockey-specific features:
    - Period handling
    - Power play indicators
    - Shots on goal display
    """
    
    def __init__(self, plugin_id, config, display_manager, cache_manager, plugin_manager):
        super().__init__(plugin_id, config, display_manager, cache_manager, plugin_manager)
        
        # Hockey-specific config
        self.show_shots = config.get('show_shots_on_goal', True)
        self.show_power_play = config.get('show_power_play', True)
    
    def fetch_hockey_games(self, league: str = "nhl") -> List[Dict]:
        """
        Fetch hockey games from ESPN.
        
        Args:
            league: League identifier (nhl, college-hockey)
            
        Returns:
            List of standardized game dictionaries
        """
        sport = f"hockey/{league}"
        data = self.fetch_espn_data(sport)
        
        if not data:
            return []
        
        return self._parse_hockey_games(data.get('events', []))
    
    def _parse_hockey_games(self, events: List[Dict]) -> List[Dict]:
        """
        Parse ESPN hockey events into standardized format.
        
        Returns:
            List of dicts with keys: id, home_team, away_team, home_score,
            away_score, period, clock, status, power_play, shots
        """
        games = []
        
        for event in events:
            try:
                competition = event['competitions'][0]
                
                game = {
                    'id': event['id'],
                    'home_team': competition['competitors'][0]['team']['abbreviation'],
                    'away_team': competition['competitors'][1]['team']['abbreviation'],
                    'home_score': int(competition['competitors'][0]['score']),
                    'away_score': int(competition['competitors'][1]['score']),
                    'status': competition['status']['type']['state'],
                    'period': competition.get('period', 0),
                    'clock': competition.get('displayClock', ''),
                    'power_play': self._extract_power_play(competition),
                    'shots': self._extract_shots(competition)
                }
                
                games.append(game)
            except (KeyError, IndexError, ValueError) as e:
                self.logger.error(f"Error parsing hockey game: {e}")
                continue
        
        return games
    
    def render_hockey_game(self, game: Dict, x: int = 0, y: int = 0):
        """
        Render a hockey game in standard format.
        
        Args:
            game: Game dictionary (from _parse_hockey_games)
            x, y: Position on display
        """
        # Render score
        self.render_score(
            game['away_team'], game['away_score'],
            game['home_team'], game['home_score'],
            x, y
        )
        
        # Render period and clock
        if game['status'] == 'in':
            period_text = f"P{game['period']} {game['clock']}"
            self.display_manager.draw_text(
                period_text,
                x=x, y=y + 20,
                color=(255, 255, 0)
            )
        
        # Render power play indicator
        if self.show_power_play and game.get('power_play'):
            self.display_manager.draw_text(
                "PP",
                x=x + 80, y=y + 20,
                color=(255, 0, 0)
            )
        
        # Render shots
        if self.show_shots and game.get('shots'):
            shots_text = f"SOG: {game['shots']['away']}-{game['shots']['home']}"
            self.display_manager.draw_text(
                shots_text,
                x=x, y=y + 28,
                color=(200, 200, 200),
                small_font=True
            )
    
    def _extract_power_play(self, competition: Dict) -> Optional[str]:
        """Extract power play information from competition data."""
        # Implementation details...
        return None
    
    def _extract_shots(self, competition: Dict) -> Optional[Dict]:
        """Extract shots on goal from competition data."""
        # Implementation details...
        return None
```

#### Using Base Classes in Plugins

**Example: NHL Scores Plugin**

```python
# plugins/nhl-scores/manager.py

from src.plugin_system.base_classes.hockey_plugin import HockeyPlugin

class NHLScoresPlugin(HockeyPlugin):
    """
    NHL Scores plugin using stable hockey base class.
    
    Inherits all hockey functionality, just needs to implement
    update() and display() for NHL-specific behavior.
    """
    
    def update(self):
        """Fetch NHL games using inherited method."""
        self.games = self.fetch_hockey_games(league="nhl")
        
        # Filter to favorites
        if self.show_favorite_only:
            self.games = self.filter_by_favorites(self.games)
        
        self.logger.info(f"Fetched {len(self.games)} NHL games")
    
    def display(self, force_clear=False):
        """Display NHL games using inherited rendering."""
        if force_clear:
            self.display_manager.clear()
        
        if not self.games:
            self._show_no_games()
            return
        
        # Show first game using inherited method
        self.render_hockey_game(self.games[0], x=0, y=5)
        
        self.display_manager.update_display()
    
    def _show_no_games(self):
        """Show no games message."""
        self.display_manager.draw_text(
            "No NHL games",
            x=5, y=15,
            color=(255, 255, 255)
        )
```

**Example: Custom Hockey Plugin (NCAA Hockey)**

```python
# plugins/ncaa-hockey/manager.py

from src.plugin_system.base_classes.hockey_plugin import HockeyPlugin

class NCAAHockeyPlugin(HockeyPlugin):
    """
    NCAA Hockey plugin - different league, same base class.
    """
    
    def update(self):
        """Fetch NCAA hockey games."""
        self.games = self.fetch_hockey_games(league="college-hockey")
        self.games = self.filter_by_favorites(self.games)
    
    def display(self, force_clear=False):
        """Display using inherited hockey rendering."""
        if force_clear:
            self.display_manager.clear()
        
        if self.games:
            # Use inherited rendering method
            self.render_hockey_game(self.games[0], x=0, y=5)
        
        self.display_manager.update_display()
```

#### API Versioning and Compatibility

**Manifest declares required API version:**

```json
{
  "id": "nhl-scores",
  "plugin_api_version": "1.0.0",
  "compatible_versions": [">=2.0.0"]
}
```

**Plugin Manager checks compatibility:**

```python
# In plugin_manager.py

def load_plugin(self, plugin_id: str) -> bool:
    manifest = self.plugin_manifests.get(plugin_id)
    
    # Check API compatibility
    required_api = manifest.get('plugin_api_version', '1.0.0')
    
    from src.plugin_system.base_classes.sports_plugin import SportsPlugin
    current_api = SportsPlugin.API_VERSION
    
    if not self._is_api_compatible(required_api, current_api):
        self.logger.error(
            f"Plugin {plugin_id} requires API {required_api}, "
            f"but {current_api} is available. Please update plugin or core."
        )
        return False
    
    # Continue loading...
    return True

def _is_api_compatible(self, required: str, current: str) -> bool:
    """
    Check if required API version is compatible with current.
    Uses semantic versioning: MAJOR.MINOR.PATCH
    
    - Same major version = compatible
    - Different major version = incompatible (breaking changes)
    """
    req_major = int(required.split('.')[0])
    cur_major = int(current.split('.')[0])
    
    return req_major == cur_major
```

#### Handling API Changes

**Non-Breaking Changes (Minor/Patch versions):**

```python
# v1.0.0 -> v1.1.0 (new optional parameter)
class HockeyPlugin:
    def render_hockey_game(self, game, x=0, y=0, show_penalties=False):
        # Added optional parameter, old code still works
        pass
```

**Breaking Changes (Major version):**

```python
# v1.x.x
class HockeyPlugin:
    def render_hockey_game(self, game, x=0, y=0):
        pass

# v2.0.0 (breaking change)
class HockeyPlugin:
    API_VERSION = "2.0.0"
    
    def render_hockey_game(self, game, position=(0, 0), style="default"):
        # Changed signature - plugins need updates
        pass
```

Plugins requiring v1.x would fail to load with v2.0.0 core, prompting user to update.

#### Benefits of This Approach

1. **No Code Duplication**: Plugins import from core
2. **Consistent Behavior**: All hockey plugins render the same way
3. **Easy Updates**: Bug fixes in base classes benefit all plugins
4. **Smaller Plugins**: No need to bundle common code
5. **Clear API Contract**: Versioned, stable interface
6. **Flexibility**: Plugins can override any method

#### When NOT to Use Base Classes

Plugins should implement BasePlugin directly when:

- Creating completely custom displays (no common patterns)
- Needing full control over every aspect
- Prototyping new display types
- External data sources (not ESPN)

Example:
```python
# plugins/custom-animation/manager.py

from src.plugin_system.base_plugin import BasePlugin

class CustomAnimationPlugin(BasePlugin):
    """Fully custom plugin - doesn't need sports base classes."""
    
    def update(self):
        # Custom data fetching
        pass
    
    def display(self, force_clear=False):
        # Custom rendering
        pass
```

#### Migration Strategy for Existing Base Classes

**Current base classes** (`src/base_classes/`):
- `sports.py`
- `hockey.py`
- `basketball.py`
- etc.

**Phase 1**: Create new plugin-specific base classes
- Keep old ones for backward compatibility
- New base classes in `src/plugin_system/base_classes/`

**Phase 2**: Migrate existing managers
- Legacy managers still use old base classes
- New plugins use new base classes

**Phase 3**: Deprecate old base classes (v3.0)
- Remove old `src/base_classes/`
- All code uses plugin system base classes

---

## 3. Plugin Store & Discovery

### Store Architecture (HACS-inspired)

The plugin store will be a simple GitHub-based discovery system where:

1. **Central Registry**: A GitHub repo (`ChuckBuilds/ledmatrix-plugin-registry`) contains a JSON file listing approved plugins
2. **Plugin Repos**: Individual GitHub repos contain plugin code
3. **Installation**: Clone/download plugin repos directly to `./plugins/` directory
4. **Updates**: Git pull or re-download from GitHub

### Registry Structure

```json
// ledmatrix-plugin-registry/plugins.json
{
  "version": "1.0.0",
  "plugins": [
    {
      "id": "clock-simple",
      "name": "Simple Clock",
      "description": "A simple clock display with date",
      "author": "ChuckBuilds",
      "category": "time",
      "tags": ["clock", "time", "date"],
      "repo": "https://github.com/ChuckBuilds/ledmatrix-clock-simple",
      "branch": "main",
      "versions": [
        {
          "version": "1.0.0",
          "ledmatrix_min_version": "2.0.0",
          "released": "2025-01-15",
          "download_url": "https://github.com/ChuckBuilds/ledmatrix-clock-simple/archive/refs/tags/v1.0.0.zip"
        }
      ],
      "stars": 45,
      "downloads": 1234,
      "last_updated": "2025-01-15",
      "verified": true
    },
    {
      "id": "weather-animated",
      "name": "Animated Weather",
      "description": "Weather display with animated icons",
      "author": "SomeUser",
      "category": "weather",
      "tags": ["weather", "animated", "forecast"],
      "repo": "https://github.com/SomeUser/ledmatrix-weather-animated",
      "branch": "main",
      "versions": [
        {
          "version": "2.1.0",
          "ledmatrix_min_version": "2.0.0",
          "released": "2025-01-10",
          "download_url": "https://github.com/SomeUser/ledmatrix-weather-animated/archive/refs/tags/v2.1.0.zip"
        }
      ],
      "stars": 89,
      "downloads": 2341,
      "last_updated": "2025-01-10",
      "verified": true
    }
  ]
}
```

### Plugin Store Manager

```python
# src/plugin_system/store_manager.py

import requests
import subprocess
import shutil
from pathlib import Path
from typing import List, Dict, Optional
import logging

class PluginStoreManager:
    """
    Manages plugin discovery, installation, and updates from GitHub.
    """
    
    REGISTRY_URL = "https://raw.githubusercontent.com/ChuckBuilds/ledmatrix-plugin-registry/main/plugins.json"
    
    def __init__(self, plugins_dir: str = "plugins"):
        self.plugins_dir = Path(plugins_dir)
        self.logger = logging.getLogger(__name__)
        self.registry_cache = None
        
    def fetch_registry(self, force_refresh: bool = False) -> Dict:
        """
        Fetch the plugin registry from GitHub.
        
        Args:
            force_refresh: Force refresh even if cached
            
        Returns:
            Registry data
        """
        if self.registry_cache and not force_refresh:
            return self.registry_cache
        
        try:
            response = requests.get(self.REGISTRY_URL, timeout=10)
            response.raise_for_status()
            self.registry_cache = response.json()
            self.logger.info(f"Fetched registry with {len(self.registry_cache['plugins'])} plugins")
            return self.registry_cache
        except Exception as e:
            self.logger.error(f"Error fetching registry: {e}")
            return {"plugins": []}
    
    def search_plugins(self, query: str = "", category: str = "", tags: List[str] = []) -> List[Dict]:
        """
        Search for plugins in the registry.
        
        Args:
            query: Search query string
            category: Filter by category
            tags: Filter by tags
            
        Returns:
            List of matching plugins
        """
        registry = self.fetch_registry()
        plugins = registry.get('plugins', [])
        
        results = []
        for plugin in plugins:
            # Category filter
            if category and plugin.get('category') != category:
                continue
            
            # Tags filter
            if tags and not any(tag in plugin.get('tags', []) for tag in tags):
                continue
            
            # Query search
            if query:
                query_lower = query.lower()
                if not any([
                    query_lower in plugin.get('name', '').lower(),
                    query_lower in plugin.get('description', '').lower(),
                    query_lower in plugin.get('id', '').lower()
                ]):
                    continue
            
            results.append(plugin)
        
        return results
    
    def install_plugin(self, plugin_id: str) -> bool:
        """
        Install a plugin from GitHub.
        Always clones or downloads the latest commit from the repository's default branch.
        
        Args:
            plugin_id: Plugin identifier
            
        Returns:
            True if installed successfully
        """
        registry = self.fetch_registry()
        plugin_info = next((p for p in registry['plugins'] if p['id'] == plugin_id), None)
        
        if not plugin_info:
            self.logger.error(f"Plugin not found in registry: {plugin_id}")
            return False
        
        try:
            # Get version info
            if version == "latest":
                version_info = plugin_info['versions'][0]  # First is latest
            else:
                version_info = next((v for v in plugin_info['versions'] if v['version'] == version), None)
                if not version_info:
                    self.logger.error(f"Version not found: {version}")
                    return False
            
            # Get repo URL
            repo_url = plugin_info['repo']
            
            # Clone or download
            plugin_path = self.plugins_dir / plugin_id
            
            if plugin_path.exists():
                self.logger.warning(f"Plugin directory already exists: {plugin_id}")
                shutil.rmtree(plugin_path)
            
            # Try git clone first
            try:
                subprocess.run(
                    ['git', 'clone', '--depth', '1', '--branch', version_info['version'], 
                     repo_url, str(plugin_path)],
                    check=True,
                    capture_output=True
                )
                self.logger.info(f"Cloned plugin {plugin_id} v{version_info['version']}")
            except (subprocess.CalledProcessError, FileNotFoundError):
                # Fall back to download
                self.logger.info("Git not available, downloading zip...")
                download_url = version_info['download_url']
                response = requests.get(download_url, timeout=30)
                response.raise_for_status()
                
                # Extract zip (implementation needed)
                # ...
            
            # Install Python dependencies
            requirements_file = plugin_path / "requirements.txt"
            if requirements_file.exists():
                subprocess.run(
                    ['pip3', 'install', '--break-system-packages', '-r', str(requirements_file)],
                    check=True
                )
                self.logger.info(f"Installed dependencies for {plugin_id}")
            
            self.logger.info(f"Successfully installed plugin: {plugin_id}")
            return True
            
        except Exception as e:
            self.logger.error(f"Error installing plugin {plugin_id}: {e}")
            return False
    
    def uninstall_plugin(self, plugin_id: str) -> bool:
        """
        Uninstall a plugin.
        
        Args:
            plugin_id: Plugin identifier
            
        Returns:
            True if uninstalled successfully
        """
        plugin_path = self.plugins_dir / plugin_id
        
        if not plugin_path.exists():
            self.logger.warning(f"Plugin not found: {plugin_id}")
            return False
        
        try:
            shutil.rmtree(plugin_path)
            self.logger.info(f"Uninstalled plugin: {plugin_id}")
            return True
        except Exception as e:
            self.logger.error(f"Error uninstalling plugin {plugin_id}: {e}")
            return False
    
    def update_plugin(self, plugin_id: str) -> bool:
        """
        Update a plugin to the latest version.
        
        Args:
            plugin_id: Plugin identifier
            
        Returns:
            True if updated successfully
        """
        plugin_path = self.plugins_dir / plugin_id
        
        if not plugin_path.exists():
            self.logger.error(f"Plugin not installed: {plugin_id}")
            return False
        
        try:
            # Try git pull first
            git_dir = plugin_path / ".git"
            if git_dir.exists():
                result = subprocess.run(
                    ['git', '-C', str(plugin_path), 'pull'],
                    capture_output=True,
                    text=True
                )
                if result.returncode == 0:
                    self.logger.info(f"Updated plugin {plugin_id} via git pull")
                    return True
            
            # Fall back to re-download
            self.logger.info(f"Re-downloading plugin {plugin_id}")
            return self.install_plugin(plugin_id)
            
        except Exception as e:
            self.logger.error(f"Error updating plugin {plugin_id}: {e}")
            return False
    
    def install_from_url(self, repo_url: str, plugin_id: str = None) -> bool:
        """
        Install a plugin directly from a GitHub URL (for custom/unlisted plugins).
        
        Args:
            repo_url: GitHub repository URL
            plugin_id: Optional custom plugin ID (extracted from manifest if not provided)
            
        Returns:
            True if installed successfully
        """
        try:
            # Clone to temporary location
            temp_dir = self.plugins_dir / ".temp_install"
            if temp_dir.exists():
                shutil.rmtree(temp_dir)
            
            subprocess.run(
                ['git', 'clone', '--depth', '1', repo_url, str(temp_dir)],
                check=True,
                capture_output=True
            )
            
            # Read manifest to get plugin ID
            manifest_path = temp_dir / "manifest.json"
            if not manifest_path.exists():
                self.logger.error("No manifest.json found in repository")
                shutil.rmtree(temp_dir)
                return False
            
            with open(manifest_path, 'r') as f:
                manifest = json.load(f)
            
            plugin_id = plugin_id or manifest.get('id')
            if not plugin_id:
                self.logger.error("No plugin ID found in manifest")
                shutil.rmtree(temp_dir)
                return False
            
            # Move to plugins directory
            final_path = self.plugins_dir / plugin_id
            if final_path.exists():
                shutil.rmtree(final_path)
            
            shutil.move(str(temp_dir), str(final_path))
            
            # Install dependencies
            requirements_file = final_path / "requirements.txt"
            if requirements_file.exists():
                subprocess.run(
                    ['pip3', 'install', '--break-system-packages', '-r', str(requirements_file)],
                    check=True
                )
            
            self.logger.info(f"Installed plugin from URL: {plugin_id}")
            return True
            
        except Exception as e:
            self.logger.error(f"Error installing from URL: {e}")
            if temp_dir.exists():
                shutil.rmtree(temp_dir)
            return False
```

---

## 4. Web UI Transformation

### New Web UI Structure

The web UI needs significant updates to support dynamic plugin management:

**New Sections:**
1. **Plugin Store** - Browse, search, install plugins
2. **Plugin Manager** - View installed, enable/disable, configure
3. **Display Rotation** - Drag-and-drop ordering of active displays
4. **Plugin Settings** - Dynamic configuration UI generated from schemas

### Plugin Store UI (React Component Structure)

```javascript
// New: templates/src/components/PluginStore.jsx

import React, { useState, useEffect } from 'react';

export default function PluginStore() {
    const [plugins, setPlugins] = useState([]);
    const [search, setSearch] = useState('');
    const [category, setCategory] = useState('all');
    const [loading, setLoading] = useState(false);
    
    useEffect(() => {
        fetchPlugins();
    }, []);
    
    const fetchPlugins = async () => {
        setLoading(true);
        try {
            const response = await fetch('/api/plugins/store/list');
            const data = await response.json();
            setPlugins(data.plugins);
        } catch (error) {
            console.error('Error fetching plugins:', error);
        } finally {
            setLoading(false);
        }
    };
    
    const installPlugin = async (pluginId) => {
        try {
            const response = await fetch('/api/plugins/install', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ plugin_id: pluginId })
            });
            
            if (response.ok) {
                alert('Plugin installed successfully!');
                // Refresh plugin list
                fetchPlugins();
            }
        } catch (error) {
            console.error('Error installing plugin:', error);
        }
    };
    
    const filteredPlugins = plugins.filter(plugin => {
        const matchesSearch = search === '' || 
            plugin.name.toLowerCase().includes(search.toLowerCase()) ||
            plugin.description.toLowerCase().includes(search.toLowerCase());
        
        const matchesCategory = category === 'all' || plugin.category === category;
        
        return matchesSearch && matchesCategory;
    });
    
    return (
        <div className="plugin-store">
            <div className="store-header">
                <h1>Plugin Store</h1>
                <div className="store-controls">
                    <input
                        type="text"
                        placeholder="Search plugins..."
                        value={search}
                        onChange={(e) => setSearch(e.target.value)}
                        className="search-input"
                    />
                    <select
                        value={category}
                        onChange={(e) => setCategory(e.target.value)}
                        className="category-select"
                    >
                        <option value="all">All Categories</option>
                        <option value="time">Time</option>
                        <option value="weather">Weather</option>
                        <option value="sports">Sports</option>
                        <option value="finance">Finance</option>
                        <option value="entertainment">Entertainment</option>
                    </select>
                </div>
            </div>
            
            {loading ? (
                <div className="loading">Loading plugins...</div>
            ) : (
                <div className="plugin-grid">
                    {filteredPlugins.map(plugin => (
                        <PluginCard
                            key={plugin.id}
                            plugin={plugin}
                            onInstall={installPlugin}
                        />
                    ))}
                </div>
            )}
        </div>
    );
}

function PluginCard({ plugin, onInstall }) {
    return (
        <div className="plugin-card">
            <div className="plugin-header">
                <h3>{plugin.name}</h3>
                {plugin.verified && <span className="verified-badge">✓ Verified</span>}
            </div>
            <p className="plugin-author">by {plugin.author}</p>
            <p className="plugin-description">{plugin.description}</p>
            <div className="plugin-meta">
                <span className="meta-item">⭐ {plugin.stars}</span>
                <span className="meta-item">📥 {plugin.downloads}</span>
                <span className="meta-item">{plugin.category}</span>
            </div>
            <div className="plugin-tags">
                {plugin.tags.map(tag => (
                    <span key={tag} className="tag">{tag}</span>
                ))}
            </div>
            <div className="plugin-actions">
                <button 
                    className="btn-primary"
                    onClick={() => onInstall(plugin.id)}
                >
                    Install
                </button>
                <a 
                    href={plugin.repo}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="btn-secondary"
                >
                    View on GitHub
                </a>
            </div>
        </div>
    );
}
```

### Plugin Manager UI

```javascript
// New: templates/src/components/PluginManager.jsx

import React, { useState, useEffect } from 'react';
import { DragDropContext, Droppable, Draggable } from 'react-beautiful-dnd';

export default function PluginManager() {
    const [installedPlugins, setInstalledPlugins] = useState([]);
    const [rotationOrder, setRotationOrder] = useState([]);
    
    useEffect(() => {
        fetchInstalledPlugins();
    }, []);
    
    const fetchInstalledPlugins = async () => {
        try {
            const response = await fetch('/api/plugins/installed');
            const data = await response.json();
            setInstalledPlugins(data.plugins);
            setRotationOrder(data.rotation_order || []);
        } catch (error) {
            console.error('Error fetching installed plugins:', error);
        }
    };
    
    const togglePlugin = async (pluginId, enabled) => {
        try {
            await fetch('/api/plugins/toggle', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ plugin_id: pluginId, enabled })
            });
            fetchInstalledPlugins();
        } catch (error) {
            console.error('Error toggling plugin:', error);
        }
    };
    
    const uninstallPlugin = async (pluginId) => {
        if (!confirm(`Are you sure you want to uninstall ${pluginId}?`)) {
            return;
        }
        
        try {
            await fetch('/api/plugins/uninstall', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ plugin_id: pluginId })
            });
            fetchInstalledPlugins();
        } catch (error) {
            console.error('Error uninstalling plugin:', error);
        }
    };
    
    const handleDragEnd = async (result) => {
        if (!result.destination) return;
        
        const newOrder = Array.from(rotationOrder);
        const [removed] = newOrder.splice(result.source.index, 1);
        newOrder.splice(result.destination.index, 0, removed);
        
        setRotationOrder(newOrder);
        
        try {
            await fetch('/api/plugins/rotation-order', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ order: newOrder })
            });
        } catch (error) {
            console.error('Error saving rotation order:', error);
        }
    };
    
    return (
        <div className="plugin-manager">
            <h1>Installed Plugins</h1>
            
            <div className="plugins-list">
                {installedPlugins.map(plugin => (
                    <div key={plugin.id} className="plugin-item">
                        <div className="plugin-info">
                            <h3>{plugin.name}</h3>
                            <p>{plugin.description}</p>
                            <span className="version">v{plugin.version}</span>
                        </div>
                        <div className="plugin-controls">
                            <label className="toggle-switch">
                                <input
                                    type="checkbox"
                                    checked={plugin.enabled}
                                    onChange={(e) => togglePlugin(plugin.id, e.target.checked)}
                                />
                                <span className="slider"></span>
                            </label>
                            <button
                                className="btn-config"
                                onClick={() => openPluginConfig(plugin.id)}
                            >
                                ⚙️ Configure
                            </button>
                            <button
                                className="btn-danger"
                                onClick={() => uninstallPlugin(plugin.id)}
                            >
                                🗑️ Uninstall
                            </button>
                        </div>
                    </div>
                ))}
            </div>
            
            <h2>Display Rotation Order</h2>
            <DragDropContext onDragEnd={handleDragEnd}>
                <Droppable droppableId="rotation">
                    {(provided) => (
                        <div
                            {...provided.droppableProps}
                            ref={provided.innerRef}
                            className="rotation-list"
                        >
                            {rotationOrder.map((pluginId, index) => {
                                const plugin = installedPlugins.find(p => p.id === pluginId);
                                if (!plugin || !plugin.enabled) return null;
                                
                                return (
                                    <Draggable
                                        key={pluginId}
                                        draggableId={pluginId}
                                        index={index}
                                    >
                                        {(provided) => (
                                            <div
                                                ref={provided.innerRef}
                                                {...provided.draggableProps}
                                                {...provided.dragHandleProps}
                                                className="rotation-item"
                                            >
                                                <span className="drag-handle">⋮⋮</span>
                                                <span>{plugin.name}</span>
                                                <span className="duration">{plugin.display_duration}s</span>
                                            </div>
                                        )}
                                    </Draggable>
                                );
                            })}
                            {provided.placeholder}
                        </div>
                    )}
                </Droppable>
            </DragDropContext>
        </div>
    );
}
```

### API Endpoints for Web UI

```python
# New endpoints in web_interface_v2.py

@app.route('/api/plugins/store/list', methods=['GET'])
def api_plugin_store_list():
    """Get list of available plugins from store."""
    try:
        store_manager = PluginStoreManager()
        registry = store_manager.fetch_registry()
        return jsonify({
            'status': 'success',
            'plugins': registry.get('plugins', [])
        })
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

@app.route('/api/plugins/install', methods=['POST'])
def api_plugin_install():
    """Install a plugin from the store."""
    try:
        data = request.get_json()
        plugin_id = data.get('plugin_id')
        version = data.get('version', 'latest')
        
        store_manager = PluginStoreManager()
        success = store_manager.install_plugin(plugin_id)
        
        if success:
            # Reload plugin manager to discover new plugin
            global plugin_manager
            plugin_manager.discover_plugins()
            
            return jsonify({
                'status': 'success',
                'message': f'Plugin {plugin_id} installed successfully'
            })
        else:
            return jsonify({
                'status': 'error',
                'message': f'Failed to install plugin {plugin_id}'
            }), 500
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

@app.route('/api/plugins/installed', methods=['GET'])
def api_plugins_installed():
    """Get list of installed plugins."""
    try:
        global plugin_manager
        plugins = []
        
        for plugin_id, plugin in plugin_manager.get_all_plugins().items():
            manifest = plugin_manager.plugin_manifests.get(plugin_id, {})
            plugins.append({
                'id': plugin_id,
                'name': manifest.get('name', plugin_id),
                'version': manifest.get('version', ''),
                'description': manifest.get('description', ''),
                'author': manifest.get('author', ''),
                'enabled': plugin.enabled,
                'display_duration': plugin.get_display_duration()
            })
        
        # Get rotation order from config
        config = config_manager.load_config()
        rotation_order = config.get('display', {}).get('plugin_rotation_order', [])
        
        return jsonify({
            'status': 'success',
            'plugins': plugins,
            'rotation_order': rotation_order
        })
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

@app.route('/api/plugins/toggle', methods=['POST'])
def api_plugin_toggle():
    """Enable or disable a plugin."""
    try:
        data = request.get_json()
        plugin_id = data.get('plugin_id')
        enabled = data.get('enabled', True)
        
        # Update config
        config = config_manager.load_config()
        if plugin_id not in config:
            config[plugin_id] = {}
        config[plugin_id]['enabled'] = enabled
        config_manager.save_config(config)
        
        # Reload plugin
        global plugin_manager
        if enabled:
            plugin_manager.load_plugin(plugin_id)
        else:
            plugin_manager.unload_plugin(plugin_id)
        
        return jsonify({
            'status': 'success',
            'message': f'Plugin {plugin_id} {"enabled" if enabled else "disabled"}'
        })
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

@app.route('/api/plugins/uninstall', methods=['POST'])
def api_plugin_uninstall():
    """Uninstall a plugin."""
    try:
        data = request.get_json()
        plugin_id = data.get('plugin_id')
        
        # Unload first
        global plugin_manager
        plugin_manager.unload_plugin(plugin_id)
        
        # Uninstall
        store_manager = PluginStoreManager()
        success = store_manager.uninstall_plugin(plugin_id)
        
        if success:
            return jsonify({
                'status': 'success',
                'message': f'Plugin {plugin_id} uninstalled successfully'
            })
        else:
            return jsonify({
                'status': 'error',
                'message': f'Failed to uninstall plugin {plugin_id}'
            }), 500
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

@app.route('/api/plugins/rotation-order', methods=['POST'])
def api_plugin_rotation_order():
    """Update plugin rotation order."""
    try:
        data = request.get_json()
        order = data.get('order', [])
        
        # Update config
        config = config_manager.load_config()
        if 'display' not in config:
            config['display'] = {}
        config['display']['plugin_rotation_order'] = order
        config_manager.save_config(config)
        
        return jsonify({
            'status': 'success',
            'message': 'Rotation order updated'
        })
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

@app.route('/api/plugins/install-from-url', methods=['POST'])
def api_plugin_install_from_url():
    """Install a plugin from a custom GitHub URL."""
    try:
        data = request.get_json()
        repo_url = data.get('repo_url')
        
        if not repo_url:
            return jsonify({
                'status': 'error',
                'message': 'repo_url is required'
            }), 400
        
        store_manager = PluginStoreManager()
        success = store_manager.install_from_url(repo_url)
        
        if success:
            # Reload plugin manager
            global plugin_manager
            plugin_manager.discover_plugins()
            
            return jsonify({
                'status': 'success',
                'message': 'Plugin installed from URL successfully'
            })
        else:
            return jsonify({
                'status': 'error',
                'message': 'Failed to install plugin from URL'
            }), 500
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500
```

---

## 5. Migration Strategy

### Phase 1: Core Plugin Infrastructure (v2.0.0)

**Goal**: Build plugin system alongside existing managers

**Changes**:
1. Create `src/plugin_system/` module
2. Implement `BasePlugin`, `PluginManager`, `PluginStoreManager`
3. Add `plugins/` directory support
4. Modify `display_controller.py` to load both legacy and plugins
5. Update web UI to show plugin store tab

**Backward Compatibility**: 100% - all existing managers still work

### Phase 2: Example Plugins (v2.1.0)

**Goal**: Create reference plugins and migration examples

**Create Official Plugins**:
1. `ledmatrix-clock-simple` - Simple clock (migrated from existing)
2. `ledmatrix-weather-basic` - Basic weather display
3. `ledmatrix-stocks-ticker` - Stock ticker
4. `ledmatrix-nhl-scores` - NHL scoreboard

**Changes**:
- Document plugin creation process
- Create plugin templates
- Update wiki with plugin development guide

**Backward Compatibility**: 100% - plugins are additive

### Phase 3: Migration Tools (v2.2.0)

**Goal**: Provide tools to migrate existing setups

**Migration Script**:
```python
# scripts/migrate_to_plugins.py

import json
from pathlib import Path

def migrate_config():
    """
    Migrate existing config.json to plugin-based format.
    """
    config_path = Path("config/config.json")
    with open(config_path, 'r') as f:
        config = json.load(f)
    
    # Create migration plan
    migration_map = {
        'clock': 'clock-simple',
        'weather': 'weather-basic',
        'stocks': 'stocks-ticker',
        'nhl_scoreboard': 'nhl-scores',
        # ... etc
    }
    
    # Install recommended plugins
    from src.plugin_system.store_manager import PluginStoreManager
    store = PluginStoreManager()
    
    for legacy_key, plugin_id in migration_map.items():
        if config.get(legacy_key, {}).get('enabled', False):
            print(f"Migrating {legacy_key} to plugin {plugin_id}")
            store.install_plugin(plugin_id)
            
            # Migrate config section
            if legacy_key in config:
                config[plugin_id] = config[legacy_key]
    
    # Save migrated config
    with open("config/config.json.migrated", 'w') as f:
        json.dump(config, f, indent=2)
    
    print("Migration complete! Review config.json.migrated")

if __name__ == "__main__":
    migrate_config()
```

**User Instructions**:
```bash
# 1. Backup existing config
cp config/config.json config/config.json.backup

# 2. Run migration script
python3 scripts/migrate_to_plugins.py

# 3. Review migrated config
cat config/config.json.migrated

# 4. Apply migration
mv config/config.json.migrated config/config.json

# 5. Restart service
sudo systemctl restart ledmatrix
```

### Phase 4: Deprecation (v2.5.0)

**Goal**: Mark legacy managers as deprecated

**Changes**:
- Add deprecation warnings to legacy managers
- Update documentation to recommend plugins
- Create migration guide in wiki

**Backward Compatibility**: 95% - legacy still works but shows warnings

### Phase 5: Plugin-Only (v3.0.0)

**Goal**: Remove legacy managers from core

**Breaking Changes**:
- Remove hardcoded manager imports from `display_controller.py`
- Remove legacy manager files from `src/`
- Package legacy managers as official plugins
- Update config template to plugin-based format

**Migration Required**: Users must run migration script

---

## 6. Plugin Developer Guidelines

### Creating a New Plugin

#### Step 1: Plugin Structure

```bash
# Create plugin directory
mkdir -p plugins/my-plugin
cd plugins/my-plugin

# Create required files
touch manifest.json
touch manager.py
touch requirements.txt
touch config_schema.json
touch README.md
```

#### Step 2: Manifest

```json
{
  "id": "my-plugin",
  "name": "My Custom Display",
  "version": "1.0.0",
  "author": "YourName",
  "description": "A custom display for LEDMatrix",
  "homepage": "https://github.com/YourName/ledmatrix-my-plugin",
  "entry_point": "manager.py",
  "class_name": "MyPluginManager",
  "category": "custom",
  "tags": ["custom", "example"],
  "compatible_versions": [">=2.0.0"],
  "min_ledmatrix_version": "2.0.0",
  "max_ledmatrix_version": "3.0.0",
  "requires": {
    "python": ">=3.9",
    "display_size": {
      "min_width": 64,
      "min_height": 32
    }
  },
  "config_schema": "config_schema.json",
  "assets": {},
  "update_interval": 60,
  "default_duration": 15,
  "display_modes": ["my-plugin"],
  "api_requirements": []
}
```

#### Step 3: Manager Implementation

```python
# manager.py

from src.plugin_system.base_plugin import BasePlugin
import time

class MyPluginManager(BasePlugin):
    """
    Example plugin that displays custom content.
    """
    
    def __init__(self, plugin_id, config, display_manager, cache_manager, plugin_manager):
        super().__init__(plugin_id, config, display_manager, cache_manager, plugin_manager)
        
        # Plugin-specific initialization
        self.message = config.get('message', 'Hello, World!')
        self.color = tuple(config.get('color', [255, 255, 255]))
        self.last_update = 0
    
    def update(self):
        """
        Update plugin data.
        Called based on update_interval in manifest.
        """
        # Fetch or update data here
        self.last_update = time.time()
        self.logger.info(f"Updated {self.plugin_id}")
    
    def display(self, force_clear=False):
        """
        Render the plugin display.
        """
        if force_clear:
            self.display_manager.clear()
        
        # Get display dimensions
        width = self.display_manager.width
        height = self.display_manager.height
        
        # Draw custom content
        self.display_manager.draw_text(
            self.message,
            x=width // 2,
            y=height // 2,
            color=self.color,
            centered=True
        )
        
        # Update the physical display
        self.display_manager.update_display()
        
        self.logger.debug(f"Displayed {self.plugin_id}")
```

#### Step 4: Configuration Schema

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "properties": {
    "enabled": {
      "type": "boolean",
      "default": true,
      "description": "Enable or disable this plugin"
    },
    "message": {
      "type": "string",
      "default": "Hello, World!",
      "description": "Message to display"
    },
    "color": {
      "type": "array",
      "items": {
        "type": "integer",
        "minimum": 0,
        "maximum": 255
      },
      "minItems": 3,
      "maxItems": 3,
      "default": [255, 255, 255],
      "description": "RGB color for text"
    },
    "display_duration": {
      "type": "number",
      "default": 15,
      "minimum": 1,
      "description": "How long to display in seconds"
    }
  },
  "required": ["enabled"]
}
```

#### Step 5: README

```markdown
# My Custom Display Plugin

A custom display plugin for LEDMatrix.

## Installation

From the LEDMatrix web UI:
1. Go to Plugin Store
2. Search for "My Custom Display"
3. Click Install

Or install from command line:
```bash
cd /path/to/LEDMatrix
python3 -c "from src.plugin_system.store_manager import PluginStoreManager; PluginStoreManager().install_plugin('my-plugin')"
```

## Configuration

Add to `config/config.json`:

```json
{
  "my-plugin": {
    "enabled": true,
    "message": "Hello, World!",
    "color": [255, 255, 255],
    "display_duration": 15
  }
}
```

## Options

- `message` (string): Text to display
- `color` (array): RGB color [R, G, B]
- `display_duration` (number): Display time in seconds

## License

MIT
```

### Publishing a Plugin

#### Step 1: Create GitHub Repository

```bash
# Initialize git
git init
git add .
git commit -m "Initial commit"

# Create on GitHub and push
git remote add origin https://github.com/YourName/ledmatrix-my-plugin.git
git push -u origin main
```

#### Step 2: Create Release

```bash
# Tag version
git tag -a v1.0.0 -m "Version 1.0.0"
git push origin v1.0.0
```

Create release on GitHub with:
- Release notes
- Installation instructions
- Screenshots/GIFs

#### Step 3: Submit to Registry

Create pull request to `ChuckBuilds/ledmatrix-plugin-registry` adding your plugin:

```json
{
  "id": "my-plugin",
  "name": "My Custom Display",
  "description": "A custom display for LEDMatrix",
  "author": "YourName",
  "category": "custom",
  "tags": ["custom", "example"],
  "repo": "https://github.com/YourName/ledmatrix-my-plugin",
  "branch": "main",
  "versions": [
    {
      "version": "1.0.0",
      "ledmatrix_min_version": "2.0.0",
      "released": "2025-01-15",
      "download_url": "https://github.com/YourName/ledmatrix-my-plugin/archive/refs/tags/v1.0.0.zip"
    }
  ],
  "verified": false
}
```

---

## 7. Technical Implementation Details

### Configuration Management

**Old Way** (monolithic):
```json
{
  "clock": { "enabled": true },
  "weather": { "enabled": true },
  "nhl_scoreboard": { "enabled": true }
}
```

**New Way** (plugin-based):
```json
{
  "plugins": {
    "clock-simple": { "enabled": true },
    "weather-basic": { "enabled": true },
    "nhl-scores": { "enabled": true }
  },
  "display": {
    "plugin_rotation_order": [
      "clock-simple",
      "weather-basic",
      "nhl-scores"
    ]
  }
}
```

### Dependency Management

Each plugin manages its own dependencies via `requirements.txt`:

```txt
# plugins/nhl-scores/requirements.txt
requests>=2.28.0
pytz>=2022.1
```

During installation:
```python
subprocess.run([
    'pip3', 'install', 
    '--break-system-packages', 
    '-r', 'plugins/nhl-scores/requirements.txt'
])
```

### Asset Management

Plugins can include their own assets:

```
plugins/nhl-scores/
├── assets/
│   ├── logos/
│   │   ├── TB.png
│   │   └── DAL.png
│   └── fonts/
│       └── sports.bdf
```

Access in plugin:
```python
def get_asset_path(self, relative_path):
    """Get absolute path to plugin asset."""
    plugin_dir = Path(__file__).parent
    return plugin_dir / "assets" / relative_path

# Usage
logo_path = self.get_asset_path("logos/TB.png")
```

### Caching Integration

Plugins use the shared cache manager:

```python
def update(self):
    cache_key = f"{self.plugin_id}_data"
    
    # Try to get cached data
    cached = self.cache_manager.get(cache_key, max_age=3600)
    if cached:
        self.data = cached
        return
    
    # Fetch fresh data
    self.data = self._fetch_from_api()
    
    # Cache it
    self.cache_manager.set(cache_key, self.data)
```

### Inter-Plugin Communication

Plugins can communicate through the plugin manager:

```python
# In plugin A
other_plugin = self.plugin_manager.get_plugin('plugin-b')
if other_plugin:
    data = other_plugin.get_shared_data()

# In plugin B
def get_shared_data(self):
    return {'temperature': 72, 'conditions': 'sunny'}
```

### Error Handling

Plugins should handle errors gracefully:

```python
def display(self, force_clear=False):
    try:
        # Plugin logic
        self._render_content()
    except Exception as e:
        self.logger.error(f"Error in display: {e}", exc_info=True)
        # Show error message on display
        self.display_manager.clear()
        self.display_manager.draw_text(
            f"Error: {self.plugin_id}",
            x=5, y=15,
            color=(255, 0, 0)
        )
        self.display_manager.update_display()
```

---

## 8. Best Practices & Standards

### Plugin Best Practices

1. **Follow BasePlugin Interface**: Always extend `BasePlugin` and implement required methods
2. **Validate Configuration**: Use config schemas to validate user settings
3. **Handle Errors Gracefully**: Never crash the entire system
4. **Use Logging**: Log important events and errors
5. **Cache Appropriately**: Use cache manager for API responses
6. **Clean Up Resources**: Implement `cleanup()` for resource disposal
7. **Document Everything**: Provide clear README and code comments
8. **Test on Hardware**: Test on actual Raspberry Pi with LED matrix
9. **Version Properly**: Use semantic versioning
10. **Respect Resources**: Be mindful of CPU, memory, and API quotas

### Coding Standards

```python
# Good: Clear, documented, error-handled
class MyPlugin(BasePlugin):
    """
    Custom plugin that displays messages.
    
    Configuration:
        message (str): Message to display
        color (tuple): RGB color tuple
    """
    
    def __init__(self, plugin_id, config, display_manager, cache_manager, plugin_manager):
        super().__init__(plugin_id, config, display_manager, cache_manager, plugin_manager)
        self.message = config.get('message', 'Default')
        self.validate_color(config.get('color', (255, 255, 255)))
    
    def validate_color(self, color):
        """Validate color is proper RGB tuple."""
        if not isinstance(color, (list, tuple)) or len(color) != 3:
            raise ValueError("Color must be RGB tuple")
        if not all(0 <= c <= 255 for c in color):
            raise ValueError("Color values must be 0-255")
        self.color = tuple(color)
    
    def update(self):
        """Update plugin data."""
        try:
            # Update logic
            pass
        except Exception as e:
            self.logger.error(f"Update failed: {e}")
    
    def display(self, force_clear=False):
        """Display plugin content."""
        try:
            if force_clear:
                self.display_manager.clear()
            
            self.display_manager.draw_text(
                self.message,
                x=5, y=15,
                color=self.color
            )
            self.display_manager.update_display()
        except Exception as e:
            self.logger.error(f"Display failed: {e}")
```

### Testing Guidelines

```python
# test/test_my_plugin.py

import unittest
from unittest.mock import Mock, MagicMock
import sys
sys.path.insert(0, 'plugins/my-plugin')
from manager import MyPluginManager

class TestMyPlugin(unittest.TestCase):
    def setUp(self):
        """Set up test fixtures."""
        self.config = {
            'enabled': True,
            'message': 'Test',
            'color': [255, 0, 0]
        }
        self.display_manager = Mock()
        self.cache_manager = Mock()
        self.plugin_manager = Mock()
        
        self.plugin = MyPluginManager(
            plugin_id='my-plugin',
            config=self.config,
            display_manager=self.display_manager,
            cache_manager=self.cache_manager,
            plugin_manager=self.plugin_manager
        )
    
    def test_initialization(self):
        """Test plugin initializes correctly."""
        self.assertEqual(self.plugin.message, 'Test')
        self.assertEqual(self.plugin.color, (255, 0, 0))
    
    def test_display_calls_manager(self):
        """Test display method calls display manager."""
        self.plugin.display()
        self.display_manager.draw_text.assert_called_once()
        self.display_manager.update_display.assert_called_once()
    
    def test_invalid_color_raises_error(self):
        """Test invalid color configuration raises error."""
        bad_config = {'color': [300, 0, 0]}
        with self.assertRaises(ValueError):
            MyPluginManager(
                'test', bad_config, 
                self.display_manager, 
                self.cache_manager,
                self.plugin_manager
            )

if __name__ == '__main__':
    unittest.main()
```

---

## 9. Security Considerations

### Plugin Verification

**Verified Plugins**:
- Reviewed by maintainers
- Follow best practices
- No known security issues
- Marked with ✓ badge in store

**Unverified Plugins**:
- User-contributed
- Not reviewed
- Install at own risk
- Show warning before installation

### Code Review Process

Before marking a plugin as verified:

1. **Code Review**: Manual inspection of code
2. **Dependency Audit**: Check all requirements
3. **Permission Check**: Verify minimal permissions
4. **API Key Safety**: Ensure no hardcoded secrets
5. **Resource Usage**: Check for excessive CPU/memory use
6. **Testing**: Test on actual hardware

### Sandboxing Considerations

Current implementation has NO sandboxing. Plugins run with same permissions as main process.

**Future Enhancement** (v3.x):
- Run plugins in separate processes
- Limit file system access
- Rate limit API calls
- Monitor resource usage
- Kill misbehaving plugins

### User Guidelines

**For Users**:
1. Only install plugins from trusted sources
2. Review plugin permissions before installing
3. Check plugin ratings and reviews
4. Keep plugins updated
5. Report suspicious plugins

**For Developers**:
1. Never include hardcoded API keys
2. Minimize required permissions
3. Use secure API practices
4. Validate all user inputs
5. Handle errors gracefully

---

## 10. Implementation Roadmap

### Timeline

**Phase 1: Foundation (Weeks 1-3)**
- Create plugin system infrastructure
- Implement BasePlugin, PluginManager, StoreManager
- Update display_controller for plugin support
- Basic web UI for plugin management

**Phase 2: Example Plugins (Weeks 4-5)**
- Create 4-5 reference plugins
- Migrate existing managers as examples
- Write developer documentation
- Create plugin templates

**Phase 3: Store Integration (Weeks 6-7)**
- Set up plugin registry repo
- Implement store API
- Build web UI for store
- Add search and filtering

**Phase 4: Migration Tools (Weeks 8-9)**
- Create migration script
- Test with existing installations
- Write migration guide
- Update documentation

**Phase 5: Testing & Polish (Weeks 10-12)**
- Comprehensive testing on Pi hardware
- Bug fixes
- Performance optimization
- Documentation improvements

**Phase 6: Release v2.0.0 (Week 13)**
- Tag release
- Publish documentation
- Announce to community
- Gather feedback

### Success Metrics

**Technical**:
- 100% backward compatibility in v2.0
- <100ms plugin load time
- <5% performance overhead
- Zero critical bugs in first month

**User Adoption**:
- 10+ community-created plugins in 3 months
- 50%+ of users install at least one plugin
- Positive feedback on ease of use

**Developer Experience**:
- Clear documentation
- Responsive to plugin dev questions
- Regular updates to plugin system

---

## Appendix A: File Structure Comparison

### Before (v1.x)

```
LEDMatrix/
├── src/
│   ├── clock.py
│   ├── weather_manager.py
│   ├── stock_manager.py
│   ├── nhl_managers.py
│   ├── nba_managers.py
│   ├── mlb_manager.py
│   └── ... (40+ manager files)
├── config/
│   ├── config.json (650+ lines)
│   └── config.template.json
└── web_interface_v2.py (hardcoded imports)
```

### After (v2.0+)

```
LEDMatrix/
├── src/
│   ├── plugin_system/
│   │   ├── __init__.py
│   │   ├── base_plugin.py
│   │   ├── plugin_manager.py
│   │   └── store_manager.py
│   ├── display_controller.py (plugin-aware)
│   └── ... (core components only)
├── plugins/
│   ├── clock-simple/
│   ├── weather-basic/
│   ├── nhl-scores/
│   └── ... (user-installed plugins)
├── config/
│   └── config.json (minimal core config)
└── web_interface_v2.py (dynamic plugin loading)
```

---

## Appendix B: Example Plugin: NHL Scoreboard

Complete example of migrating NHL scoreboard to plugin:

**Directory Structure**:
```
plugins/nhl-scores/
├── manifest.json
├── manager.py
├── requirements.txt
├── config_schema.json
├── assets/
│   └── logos/
│       ├── TB.png
│       └── ... (NHL team logos)
└── README.md
```

**manifest.json**:
```json
{
  "id": "nhl-scores",
  "name": "NHL Scoreboard",
  "version": "1.0.0",
  "author": "ChuckBuilds",
  "description": "Display NHL game scores and schedules",
  "homepage": "https://github.com/ChuckBuilds/ledmatrix-nhl-scores",
  "entry_point": "manager.py",
  "class_name": "NHLScoresPlugin",
  "category": "sports",
  "tags": ["nhl", "hockey", "sports", "scores"],
  "compatible_versions": [">=2.0.0"],
  "requires": {
    "python": ">=3.9",
    "display_size": {
      "min_width": 64,
      "min_height": 32
    }
  },
  "config_schema": "config_schema.json",
  "assets": {
    "logos": "assets/logos/"
  },
  "update_interval": 60,
  "default_duration": 30,
  "display_modes": ["nhl_live", "nhl_recent", "nhl_upcoming"],
  "api_requirements": ["ESPN API"]
}
```

**requirements.txt**:
```txt
requests>=2.28.0
pytz>=2022.1
```

**manager.py** (abbreviated):
```python
from src.plugin_system.base_plugin import BasePlugin
import requests
from datetime import datetime
from pathlib import Path

class NHLScoresPlugin(BasePlugin):
    """NHL Scoreboard plugin for LEDMatrix."""
    
    ESPN_NHL_URL = "https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/scoreboard"
    
    def __init__(self, plugin_id, config, display_manager, cache_manager, plugin_manager):
        super().__init__(plugin_id, config, display_manager, cache_manager, plugin_manager)
        
        self.favorite_teams = config.get('favorite_teams', [])
        self.show_favorite_only = config.get('show_favorite_teams_only', True)
        self.games = []
    
    def update(self):
        """Fetch NHL games from ESPN API."""
        cache_key = f"{self.plugin_id}_games"
        
        # Try cache first
        cached = self.cache_manager.get(cache_key, max_age=60)
        if cached:
            self.games = cached
            self.logger.debug("Using cached NHL data")
            return
        
        try:
            response = requests.get(self.ESPN_NHL_URL, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            self.games = self._process_games(data.get('events', []))
            
            # Cache the results
            self.cache_manager.set(cache_key, self.games)
            
            self.logger.info(f"Fetched {len(self.games)} NHL games")
        except Exception as e:
            self.logger.error(f"Error fetching NHL data: {e}")
    
    def _process_games(self, events):
        """Process raw ESPN data into game objects."""
        games = []
        for event in events:
            # Extract game info
            # ... (implementation)
            pass
        return games
    
    def display(self, force_clear=False):
        """Display NHL scores."""
        if force_clear:
            self.display_manager.clear()
        
        if not self.games:
            self._show_no_games()
            return
        
        # Show first game (or cycle through)
        game = self.games[0]
        self._display_game(game)
        
        self.display_manager.update_display()
    
    def _display_game(self, game):
        """Render a single game."""
        # Load team logos
        away_logo = self._get_logo(game['away_team'])
        home_logo = self._get_logo(game['home_team'])
        
        # Draw logos and scores
        # ... (implementation)
    
    def _get_logo(self, team_abbr):
        """Get team logo from assets."""
        logo_path = Path(__file__).parent / "assets" / "logos" / f"{team_abbr}.png"
        if logo_path.exists():
            return logo_path
        return None
    
    def _show_no_games(self):
        """Show 'no games' message."""
        self.display_manager.draw_text(
            "No NHL games",
            x=5, y=15,
            color=(255, 255, 255)
        )
```

---

## Conclusion

This specification outlines a comprehensive transformation of the LEDMatrix project into a modular, extensible platform. The plugin architecture enables:

- **User Extensibility**: Anyone can create custom displays
- **Easy Distribution**: GitHub-based store for discovery and installation
- **Backward Compatibility**: Gradual migration path for existing users
- **Community Growth**: Lower barrier to contribution
- **Better Maintenance**: Smaller core, cleaner codebase

The gradual migration approach ensures existing users aren't disrupted while new users benefit from the improved architecture.

**Next Steps**:
1. Review and refine this specification
2. Begin Phase 1 implementation
3. Create prototype plugins for testing
4. Gather community feedback
5. Iterate and improve

---

**Document Version**: 1.0.0  
**Last Updated**: 2025-01-09  
**Author**: AI Assistant (Claude)  
**Status**: Draft for Review

