"""
Starlark Apps Plugin for LEDMatrix

Manages and displays Starlark (.star) apps from Tronbyte/Tidbyt community.
Provides seamless widget import without modification.

API Version: 1.0.0
"""

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple
from PIL import Image

from src.plugin_system.base_plugin import BasePlugin
from .pixlet_renderer import PixletRenderer
from .frame_extractor import FrameExtractor

logger = logging.getLogger(__name__)


class StarlarkApp:
    """Represents a single installed Starlark app."""

    def __init__(self, app_id: str, app_dir: Path, manifest: Dict[str, Any]):
        """
        Initialize a Starlark app instance.

        Args:
            app_id: Unique identifier for this app
            app_dir: Directory containing the app files
            manifest: App metadata from manifest
        """
        self.app_id = app_id
        self.app_dir = app_dir
        self.manifest = manifest
        self.star_file = app_dir / manifest.get("star_file", f"{app_id}.star")
        self.config_file = app_dir / "config.json"
        self.schema_file = app_dir / "schema.json"
        self.cache_file = app_dir / "cached_render.webp"

        # Load app configuration
        self.config = self._load_config()
        self.schema = self._load_schema()

        # Runtime state
        self.frames: Optional[List[Tuple[Image.Image, int]]] = None
        self.current_frame_index = 0
        self.last_frame_time = 0
        self.last_render_time = 0

    def _load_config(self) -> Dict[str, Any]:
        """Load app configuration from config.json."""
        if self.config_file.exists():
            try:
                with open(self.config_file, 'r') as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Could not load config for {self.app_id}: {e}")
        return {}

    def _load_schema(self) -> Optional[Dict[str, Any]]:
        """Load app schema from schema.json."""
        if self.schema_file.exists():
            try:
                with open(self.schema_file, 'r') as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Could not load schema for {self.app_id}: {e}")
        return None

    def save_config(self) -> bool:
        """Save current configuration to file."""
        try:
            with open(self.config_file, 'w') as f:
                json.dump(self.config, f, indent=2)
            return True
        except Exception as e:
            logger.exception(f"Could not save config for {self.app_id}: {e}")
            return False

    def is_enabled(self) -> bool:
        """Check if app is enabled."""
        return self.manifest.get("enabled", True)

    def get_render_interval(self) -> int:
        """Get render interval in seconds."""
        default = 300
        try:
            value = self.manifest.get("render_interval", default)
            interval = int(value)
        except (ValueError, TypeError):
            interval = default
        
        # Clamp to safe range: min 5, max 3600
        return max(5, min(interval, 3600))

    def get_display_duration(self) -> int:
        """Get display duration in seconds."""
        default = 15
        try:
            value = self.manifest.get("display_duration", default)
            duration = int(value)
        except (ValueError, TypeError):
            duration = default
        
        # Clamp to safe range: min 1, max 600
        return max(1, min(duration, 600))

    def should_render(self, current_time: float) -> bool:
        """Check if app should be re-rendered based on interval."""
        interval = self.get_render_interval()
        return (current_time - self.last_render_time) >= interval


class StarlarkAppsPlugin(BasePlugin):
    """
    Starlark Apps Manager plugin.

    Manages Starlark (.star) apps and renders them using Pixlet.
    Each installed app becomes a dynamic display mode.
    """

    def __init__(self, plugin_id: str, config: Dict[str, Any],
                 display_manager, cache_manager, plugin_manager):
        """Initialize the Starlark Apps plugin."""
        super().__init__(plugin_id, config, display_manager, cache_manager, plugin_manager)

        # Initialize components
        self.pixlet = PixletRenderer(
            pixlet_path=config.get("pixlet_path"),
            timeout=config.get("render_timeout", 30)
        )
        self.extractor = FrameExtractor(
            default_frame_delay=config.get("default_frame_delay", 50)
        )

        # App storage
        self.apps_dir = self._get_apps_directory()
        self.manifest_file = self.apps_dir / "manifest.json"
        self.apps: Dict[str, StarlarkApp] = {}

        # Display state
        self.current_app: Optional[StarlarkApp] = None
        self.last_update_check = 0

        # Check Pixlet availability
        if not self.pixlet.is_available():
            self.logger.error("Pixlet not available - Starlark apps will not work")
            self.logger.error("Install Pixlet or place bundled binary in bin/pixlet/")
        else:
            version = self.pixlet.get_version()
            self.logger.info(f"Pixlet available: {version}")

        # Calculate optimal magnification based on display size
        self.calculated_magnify = self._calculate_optimal_magnify()
        if self.calculated_magnify > 1:
            self.logger.info(f"Display size: {self.display_manager.matrix.width}x{self.display_manager.matrix.height}, "
                           f"recommended magnify: {self.calculated_magnify}")

        # Load installed apps
        self._load_installed_apps()

        self.logger.info(f"Starlark Apps plugin initialized with {len(self.apps)} apps")

    @property
    def modes(self) -> List[str]:
        """
        Return list of display modes (one per installed Starlark app).
        
        This allows each installed app to appear as a separate display mode
        in the schedule/rotation system.
        
        Returns:
            List of app IDs that can be used as display modes
        """
        # Return list of enabled app IDs as display modes
        return [app.app_id for app in self.apps.values() if app.is_enabled()]

    def validate_config(self) -> bool:
        """
        Validate plugin configuration.

        Ensures required configuration values are valid for Starlark apps.

        Returns:
            True if configuration is valid, False otherwise
        """
        # Call parent validation first
        if not super().validate_config():
            return False

        # Validate magnify range (0-8)
        if "magnify" in self.config:
            magnify = self.config["magnify"]
            if not isinstance(magnify, int) or magnify < 0 or magnify > 8:
                self.logger.error("magnify must be an integer between 0 and 8")
                return False

        # Validate render_timeout
        if "render_timeout" in self.config:
            timeout = self.config["render_timeout"]
            if not isinstance(timeout, (int, float)) or timeout < 5 or timeout > 120:
                self.logger.error("render_timeout must be a number between 5 and 120")
                return False

        # Validate cache_ttl
        if "cache_ttl" in self.config:
            ttl = self.config["cache_ttl"]
            if not isinstance(ttl, (int, float)) or ttl < 60 or ttl > 3600:
                self.logger.error("cache_ttl must be a number between 60 and 3600")
                return False

        # Validate scale_method
        if "scale_method" in self.config:
            method = self.config["scale_method"]
            valid_methods = ["nearest", "bilinear", "bicubic", "lanczos"]
            if method not in valid_methods:
                self.logger.error(f"scale_method must be one of: {', '.join(valid_methods)}")
                return False

        # Validate default_frame_delay
        if "default_frame_delay" in self.config:
            delay = self.config["default_frame_delay"]
            if not isinstance(delay, (int, float)) or delay < 16 or delay > 1000:
                self.logger.error("default_frame_delay must be a number between 16 and 1000")
                return False

        return True

    def _calculate_optimal_magnify(self) -> int:
        """
        Calculate optimal magnification factor based on display dimensions.

        Tronbyte apps are designed for 64x32 displays.
        This calculates what magnification would best fit the current display.

        Returns:
            Recommended magnify value (1-8)
        """
        try:
            display_width = self.display_manager.matrix.width
            display_height = self.display_manager.matrix.height

            # Tronbyte native resolution
            NATIVE_WIDTH = 64
            NATIVE_HEIGHT = 32

            # Calculate scale factors for width and height
            width_scale = display_width / NATIVE_WIDTH
            height_scale = display_height / NATIVE_HEIGHT

            # Use the smaller scale to ensure content fits
            # (prevents overflow on one dimension)
            scale_factor = min(width_scale, height_scale)

            # Round down to get integer magnify value
            magnify = int(scale_factor)

            # Clamp to reasonable range (1-8)
            magnify = max(1, min(8, magnify))

            self.logger.debug(f"Display: {display_width}x{display_height}, "
                            f"Native: {NATIVE_WIDTH}x{NATIVE_HEIGHT}, "
                            f"Calculated magnify: {magnify}")

            return magnify

        except Exception as e:
            self.logger.warning(f"Could not calculate magnify: {e}")
            return 1

    def get_magnify_recommendation(self) -> Dict[str, Any]:
        """
        Get detailed magnification recommendation for current display.

        Returns:
            Dictionary with recommendation details
        """
        try:
            display_width = self.display_manager.matrix.width
            display_height = self.display_manager.matrix.height

            NATIVE_WIDTH = 64
            NATIVE_HEIGHT = 32

            width_scale = display_width / NATIVE_WIDTH
            height_scale = display_height / NATIVE_HEIGHT

            # Calculate for different magnify values
            recommendations = []
            for magnify in range(1, 9):
                render_width = NATIVE_WIDTH * magnify
                render_height = NATIVE_HEIGHT * magnify

                # Check if this magnify fits perfectly
                perfect_fit = (render_width == display_width and render_height == display_height)

                # Check if scaling is needed
                needs_scaling = (render_width != display_width or render_height != display_height)

                # Calculate quality score (1-100)
                if perfect_fit:
                    quality_score = 100
                elif not needs_scaling:
                    quality_score = 95
                else:
                    # Score based on how close to display size
                    width_ratio = min(render_width, display_width) / max(render_width, display_width)
                    height_ratio = min(render_height, display_height) / max(render_height, display_height)
                    quality_score = int((width_ratio + height_ratio) / 2 * 100)

                recommendations.append({
                    'magnify': magnify,
                    'render_size': f"{render_width}x{render_height}",
                    'perfect_fit': perfect_fit,
                    'needs_scaling': needs_scaling,
                    'quality_score': quality_score,
                    'recommended': magnify == self.calculated_magnify
                })

            return {
                'display_size': f"{display_width}x{display_height}",
                'native_size': f"{NATIVE_WIDTH}x{NATIVE_HEIGHT}",
                'calculated_magnify': self.calculated_magnify,
                'width_scale': round(width_scale, 2),
                'height_scale': round(height_scale, 2),
                'recommendations': recommendations
            }

        except Exception as e:
            self.logger.exception(f"Error getting magnify recommendation: {e}")
            return {
                'display_size': 'unknown',
                'calculated_magnify': 1,
                'recommendations': []
            }

    def _get_effective_magnify(self) -> int:
        """
        Get the effective magnify value to use for rendering.

        Priority:
        1. User-configured magnify (if valid and in range 1-8)
        2. Auto-calculated magnify

        Returns:
            Magnify value to use
        """
        config_magnify = self.config.get("magnify")

        # Validate and clamp config_magnify
        if config_magnify is not None:
            try:
                # Convert to int if possible
                config_magnify = int(config_magnify)
                # Clamp to safe range (1-8)
                if 1 <= config_magnify <= 8:
                    return config_magnify
            except (ValueError, TypeError):
                # Non-numeric value, fall through to calculated
                pass

        # Fall back to auto-calculated value
        return self.calculated_magnify

    def _get_apps_directory(self) -> Path:
        """Get the directory for storing Starlark apps."""
        try:
            # Try to find project root
            current_dir = Path(__file__).resolve().parent
            project_root = current_dir.parent.parent
            apps_dir = project_root / "starlark-apps"
        except Exception:
            # Fallback to current working directory
            apps_dir = Path.cwd() / "starlark-apps"

        # Create directory if it doesn't exist
        apps_dir.mkdir(parents=True, exist_ok=True)
        return apps_dir

    def _sanitize_app_id(self, app_id: str) -> str:
        """
        Sanitize app_id into a safe slug for use in file paths.

        Args:
            app_id: Original app identifier

        Returns:
            Sanitized slug containing only [a-z0-9_.-] characters
        """
        if not app_id:
            raise ValueError("app_id cannot be empty")

        # Replace invalid characters with underscore
        # Allow only: lowercase letters, digits, underscore, period, hyphen
        safe_slug = re.sub(r'[^a-z0-9_.-]', '_', app_id.lower())

        # Remove leading/trailing dots, underscores, or hyphens
        safe_slug = safe_slug.strip('._-')

        # Ensure it's not empty after sanitization
        if not safe_slug:
            raise ValueError(f"app_id '{app_id}' becomes empty after sanitization")

        return safe_slug

    def _verify_path_safety(self, path: Path, base_dir: Path) -> None:
        """
        Verify that a path is within the base directory to prevent path traversal.

        Args:
            path: Path to verify
            base_dir: Base directory that path must be within

        Raises:
            ValueError: If path escapes the base directory
        """
        try:
            resolved_path = path.resolve()
            resolved_base = base_dir.resolve()

            # Check if path is relative to base directory
            if not resolved_path.is_relative_to(resolved_base):
                raise ValueError(
                    f"Path traversal detected: {resolved_path} is not within {resolved_base}"
                )
        except (ValueError, AttributeError) as e:
            # AttributeError for Python < 3.9 where is_relative_to doesn't exist
            # Fallback: check if resolved path starts with resolved base
            resolved_path = path.resolve()
            resolved_base = base_dir.resolve()

            try:
                resolved_path.relative_to(resolved_base)
            except ValueError:
                raise ValueError(
                    f"Path traversal detected: {resolved_path} is not within {resolved_base}"
                ) from e

    def _load_installed_apps(self) -> None:
        """Load all installed apps from manifest."""
        if not self.manifest_file.exists():
            # Create initial manifest
            self._save_manifest({"apps": {}})
            return

        try:
            with open(self.manifest_file, 'r') as f:
                manifest = json.load(f)

            apps_data = manifest.get("apps", {})
            for app_id, app_manifest in apps_data.items():
                try:
                    # Sanitize app_id to prevent path traversal
                    safe_app_id = self._sanitize_app_id(app_id)
                    app_dir = (self.apps_dir / safe_app_id).resolve()

                    # Verify path safety
                    self._verify_path_safety(app_dir, self.apps_dir)
                except ValueError as e:
                    self.logger.warning(f"Invalid app_id '{app_id}': {e}")
                    continue

                if not app_dir.exists():
                    self.logger.warning(f"App directory missing: {app_id}")
                    continue

                try:
                    # Use safe_app_id for internal storage to match directory structure
                    app = StarlarkApp(safe_app_id, app_dir, app_manifest)
                    self.apps[safe_app_id] = app
                    self.logger.debug(f"Loaded app: {app_id} (sanitized: {safe_app_id})")
                except Exception as e:
                    self.logger.exception(f"Error loading app {app_id}: {e}")

            self.logger.info(f"Loaded {len(self.apps)} Starlark apps")

        except Exception as e:
            self.logger.exception(f"Error loading apps manifest: {e}")

    def _save_manifest(self, manifest: Dict[str, Any]) -> bool:
        """Save apps manifest to file."""
        try:
            with open(self.manifest_file, 'w') as f:
                json.dump(manifest, f, indent=2)
            return True
        except Exception as e:
            self.logger.error(f"Error saving manifest: {e}")
            return False

    def update(self) -> None:
        """Update method - check if apps need re-rendering."""
        current_time = time.time()

        # Check apps that need re-rendering based on their intervals
        if self.config.get("auto_refresh_apps", True):
            for app in self.apps.values():
                if app.is_enabled() and app.should_render(current_time):
                    self._render_app(app, force=False)

    def display(self, force_clear: bool = False) -> None:
        """
        Display current Starlark app.

        This method is called during the display rotation.
        Displays frames from the currently active app.
        """
        try:
            if force_clear:
                self.display_manager.clear()

            # If no current app, try to select one
            if not self.current_app:
                self._select_next_app()

            if not self.current_app:
                # No apps available
                self.logger.debug("No Starlark apps to display")
                return

            # Render app if needed
            if not self.current_app.frames:
                success = self._render_app(self.current_app, force=True)
                if not success:
                    self.logger.error(f"Failed to render app: {self.current_app.app_id}")
                    return

            # Display current frame
            self._display_frame()

        except Exception as e:
            self.logger.error(f"Error displaying Starlark app: {e}")

    def _select_next_app(self) -> None:
        """Select the next enabled app for display."""
        enabled_apps = [app for app in self.apps.values() if app.is_enabled()]

        if not enabled_apps:
            self.current_app = None
            return

        # Simple rotation - could be enhanced with priorities
        if self.current_app and self.current_app in enabled_apps:
            current_idx = enabled_apps.index(self.current_app)
            next_idx = (current_idx + 1) % len(enabled_apps)
            self.current_app = enabled_apps[next_idx]
        else:
            self.current_app = enabled_apps[0]

        self.logger.debug(f"Selected app for display: {self.current_app.app_id}")

    def _render_app(self, app: StarlarkApp, force: bool = False) -> bool:
        """
        Render a Starlark app using Pixlet.

        Args:
            app: App to render
            force: Force render even if cached

        Returns:
            True if successful
        """
        try:
            current_time = time.time()

            # Check cache
            use_cache = self.config.get("cache_rendered_output", True)
            cache_ttl = self.config.get("cache_ttl", 300)

            if (not force and use_cache and app.cache_file.exists() and
                (current_time - app.last_render_time) < cache_ttl):
                # Use cached render
                self.logger.debug(f"Using cached render for: {app.app_id}")
                return self._load_frames_from_cache(app)

            # Render with Pixlet
            self.logger.info(f"Rendering app: {app.app_id}")

            # Get effective magnification factor (config or auto-calculated)
            magnify = self._get_effective_magnify()
            self.logger.debug(f"Using magnify={magnify} for {app.app_id}")

            success, error = self.pixlet.render(
                star_file=str(app.star_file),
                output_path=str(app.cache_file),
                config=app.config,
                magnify=magnify
            )

            if not success:
                self.logger.error(f"Pixlet render failed: {error}")
                return False

            # Extract frames
            success = self._load_frames_from_cache(app)
            if success:
                app.last_render_time = current_time

            return success

        except Exception as e:
            self.logger.error(f"Error rendering app {app.app_id}: {e}")
            return False

    def _load_frames_from_cache(self, app: StarlarkApp) -> bool:
        """Load frames from cached WebP file."""
        try:
            success, frames, error = self.extractor.load_webp(str(app.cache_file))

            if not success:
                self.logger.error(f"Frame extraction failed: {error}")
                return False

            # Scale frames if needed
            if self.config.get("scale_output", True):
                width = self.display_manager.matrix.width
                height = self.display_manager.matrix.height

                # Get scaling method from config
                scale_method_str = self.config.get("scale_method", "nearest")
                scale_method_map = {
                    "nearest": Image.Resampling.NEAREST,
                    "bilinear": Image.Resampling.BILINEAR,
                    "bicubic": Image.Resampling.BICUBIC,
                    "lanczos": Image.Resampling.LANCZOS
                }
                scale_method = scale_method_map.get(scale_method_str, Image.Resampling.NEAREST)

                # Check if we should center instead of scale
                if self.config.get("center_small_output", False):
                    frames = self.extractor.center_frames(frames, width, height)
                else:
                    frames = self.extractor.scale_frames(frames, width, height, scale_method)

            # Optimize frames to limit memory usage (max_frames=None means no limit)
            max_frames = self.config.get("max_frames")
            if max_frames is not None:
                frames = self.extractor.optimize_frames(frames, max_frames=max_frames)

            app.frames = frames
            app.current_frame_index = 0
            app.last_frame_time = time.time()

            self.logger.debug(f"Loaded {len(frames)} frames for {app.app_id}")
            return True

        except Exception as e:
            self.logger.error(f"Error loading frames for {app.app_id}: {e}")
            return False

    def _display_frame(self) -> None:
        """Display the current frame of the current app."""
        if not self.current_app or not self.current_app.frames:
            return

        try:
            current_time = time.time()
            frame, delay_ms = self.current_app.frames[self.current_app.current_frame_index]

            # Set frame on display manager
            self.display_manager.image = frame
            self.display_manager.update_display()

            # Check if it's time to advance to next frame
            delay_seconds = delay_ms / 1000.0
            if (current_time - self.current_app.last_frame_time) >= delay_seconds:
                self.current_app.current_frame_index = (
                    (self.current_app.current_frame_index + 1) % len(self.current_app.frames)
                )
                self.current_app.last_frame_time = current_time

        except Exception as e:
            self.logger.error(f"Error displaying frame: {e}")

    def install_app(self, app_id: str, star_file_path: str, metadata: Optional[Dict[str, Any]] = None) -> bool:
        """
        Install a new Starlark app.

        Args:
            app_id: Unique identifier for the app
            star_file_path: Path to .star file to install
            metadata: Optional metadata (name, description, etc.)

        Returns:
            True if successful
        """
        try:
            import shutil

            # Sanitize app_id to prevent path traversal
            safe_app_id = self._sanitize_app_id(app_id)

            # Create app directory with resolved path
            app_dir = (self.apps_dir / safe_app_id).resolve()
            app_dir.mkdir(parents=True, exist_ok=True)

            # Verify path safety after mkdir
            self._verify_path_safety(app_dir, self.apps_dir)

            # Copy .star file with sanitized app_id
            star_dest = app_dir / f"{safe_app_id}.star"
            # Verify star_dest path safety
            self._verify_path_safety(star_dest, self.apps_dir)
            shutil.copy2(star_file_path, star_dest)

            # Create app manifest entry
            app_manifest = {
                "name": metadata.get("name", app_id) if metadata else app_id,
                "original_id": app_id,  # Store original for reference
                "star_file": f"{safe_app_id}.star",
                "enabled": True,
                "render_interval": metadata.get("render_interval", 300) if metadata else 300,
                "display_duration": metadata.get("display_duration", 15) if metadata else 15
            }

            # Try to extract schema
            _, schema, _ = self.pixlet.extract_schema(str(star_dest))
            if schema:
                schema_path = app_dir / "schema.json"
                # Verify schema path safety
                self._verify_path_safety(schema_path, self.apps_dir)
                with open(schema_path, 'w') as f:
                    json.dump(schema, f, indent=2)

            # Create default config
            default_config = {}
            config_path = app_dir / "config.json"
            # Verify config path safety
            self._verify_path_safety(config_path, self.apps_dir)
            with open(config_path, 'w') as f:
                json.dump(default_config, f, indent=2)

            # Update manifest (use safe_app_id as key to match directory)
            with open(self.manifest_file, 'r') as f:
                manifest = json.load(f)

            manifest["apps"][safe_app_id] = app_manifest
            self._save_manifest(manifest)

            # Create app instance (use safe_app_id for internal key, original for display)
            app = StarlarkApp(safe_app_id, app_dir, app_manifest)
            self.apps[safe_app_id] = app

            self.logger.info(f"Installed Starlark app: {app_id} (sanitized: {safe_app_id})")
            return True

        except Exception as e:
            self.logger.error(f"Error installing app {app_id}: {e}")
            return False

    def uninstall_app(self, app_id: str) -> bool:
        """
        Uninstall a Starlark app.

        Args:
            app_id: App to uninstall

        Returns:
            True if successful
        """
        try:
            import shutil

            if app_id not in self.apps:
                self.logger.warning(f"App not found: {app_id}")
                return False

            # Remove from current app if selected
            if self.current_app and self.current_app.app_id == app_id:
                self.current_app = None

            # Remove from apps dict
            app = self.apps.pop(app_id)

            # Remove directory
            if app.app_dir.exists():
                shutil.rmtree(app.app_dir)

            # Update manifest
            with open(self.manifest_file, 'r') as f:
                manifest = json.load(f)

            if app_id in manifest["apps"]:
                del manifest["apps"][app_id]
                self._save_manifest(manifest)

            self.logger.info(f"Uninstalled Starlark app: {app_id}")
            return True

        except Exception as e:
            self.logger.error(f"Error uninstalling app {app_id}: {e}")
            return False

    def get_display_duration(self) -> float:
        """Get display duration for current app."""
        if self.current_app:
            return float(self.current_app.get_display_duration())
        return self.config.get('display_duration', 15.0)

    def get_info(self) -> Dict[str, Any]:
        """Return plugin info for web UI."""
        info = super().get_info()
        info.update({
            'pixlet_available': self.pixlet.is_available(),
            'pixlet_version': self.pixlet.get_version(),
            'installed_apps': len(self.apps),
            'enabled_apps': len([a for a in self.apps.values() if a.is_enabled()]),
            'current_app': self.current_app.app_id if self.current_app else None,
            'apps': {
                app_id: {
                    'name': app.manifest.get('name', app_id),
                    'enabled': app.is_enabled(),
                    'has_frames': app.frames is not None
                }
                for app_id, app in self.apps.items()
            }
        })
        return info
