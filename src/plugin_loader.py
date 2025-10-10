import os
import json
import importlib.util
import logging
import sys
from typing import Dict, Any, Optional, List
from pathlib import Path

logger = logging.getLogger(__name__)

class PluginLoader:
    """
    Plugin loading system that handles font registration and plugin lifecycle.

    This class is responsible for:
    - Loading plugin manifests
    - Registering plugin fonts with FontManager
    - Managing plugin lifecycle
    - Providing plugin API access
    """

    def __init__(self, display_manager, config: Dict[str, Any]):
        self.display_manager = display_manager
        self.config = config
        self.loaded_plugins: Dict[str, Any] = {}
        self.plugin_manifests: Dict[str, Dict[str, Any]] = {}

    def load_plugin(self, plugin_path: str, plugin_id: str) -> bool:
        """
        Load a plugin from a directory path.

        Args:
            plugin_path: Path to plugin directory
            plugin_id: Unique identifier for the plugin

        Returns:
            True if loading successful, False otherwise
        """
        try:
            plugin_dir = Path(plugin_path)
            if not plugin_dir.exists():
                logger.error(f"Plugin directory not found: {plugin_path}")
                return False

            # Load manifest
            manifest_path = plugin_dir / "manifest.json"
            if not manifest_path.exists():
                logger.error(f"Manifest not found: {manifest_path}")
                return False

            with open(manifest_path, 'r') as f:
                manifest = json.load(f)

            # Validate manifest
            if not self._validate_manifest(manifest, plugin_id):
                return False

            # Store manifest
            self.plugin_manifests[plugin_id] = manifest

            # Register fonts if present
            if "fonts" in manifest:
                success = self.display_manager.register_plugin_fonts(plugin_id, manifest["fonts"])
                if not success:
                    logger.warning(f"Font registration failed for plugin {plugin_id}, but continuing...")

            # Register font defaults if present
            if "font_defaults" in manifest:
                self.display_manager.register_plugin_font_defaults(plugin_id, manifest["font_defaults"])

            # Load plugin class
            entry_point = manifest.get("entry_point", "manager.py")
            class_name = manifest.get("class_name", f"{plugin_id.title()}Plugin")

            plugin_module = self._load_plugin_module(plugin_dir / entry_point, plugin_id)
            if not plugin_module:
                return False

            plugin_class = getattr(plugin_module, class_name, None)
            if not plugin_class:
                logger.error(f"Plugin class '{class_name}' not found in {entry_point}")
                return False

            # Instantiate plugin
            plugin_instance = plugin_class(self.display_manager, self.config)

            # Store loaded plugin
            self.loaded_plugins[plugin_id] = {
                "instance": plugin_instance,
                "manifest": manifest,
                "path": plugin_path
            }

            logger.info(f"Successfully loaded plugin: {plugin_id}")
            return True

        except Exception as e:
            logger.error(f"Error loading plugin {plugin_id}: {e}", exc_info=True)
            return False

    def _validate_manifest(self, manifest: Dict[str, Any], plugin_id: str) -> bool:
        """Validate plugin manifest structure."""
        required_fields = ["name", "version", "entry_point", "class_name"]

        for field in required_fields:
            if field not in manifest:
                logger.error(f"Missing required field '{field}' in plugin manifest for {plugin_id}")
                return False

        # Check for font manifest structure if fonts are defined
        if "fonts" in manifest:
            if not isinstance(manifest["fonts"], dict):
                logger.error("Font manifest must be a dictionary")
                return False

            if "fonts" not in manifest["fonts"]:
                logger.error("Font manifest must contain 'fonts' array")
                return False

            # Validate each font definition
            fonts = manifest["fonts"]["fonts"]
            for i, font_def in enumerate(fonts):
                if not isinstance(font_def, dict):
                    logger.error(f"Font definition {i} must be a dictionary")
                    return False

                if "family" not in font_def or "source" not in font_def:
                    logger.error(f"Font definition {i} missing required fields 'family' or 'source'")
                    return False

        # Check for font defaults structure if font_defaults are defined
        if "font_defaults" in manifest:
            if not isinstance(manifest["font_defaults"], dict):
                logger.error("Font defaults must be a dictionary")
                return False

            # Validate each font default
            for element_key, default_config in manifest["font_defaults"].items():
                if not isinstance(default_config, dict):
                    logger.error(f"Font default for '{element_key}' must be a dictionary")
                    return False

                # Check for required fields in font defaults
                if "family" not in default_config or "size_token" not in default_config:
                    logger.error(f"Font default for '{element_key}' missing required fields 'family' or 'size_token'")
                    return False

        return True

    def _load_plugin_module(self, module_path: Path, plugin_id: str):
        """Load a Python module from file path."""
        try:
            spec = importlib.util.spec_from_file_location(f"plugin_{plugin_id}", module_path)
            if spec is None:
                logger.error(f"Could not create module spec for {module_path}")
                return None

            module = importlib.util.module_from_spec(spec)

            # Add plugin directory to sys.path temporarily
            plugin_dir = str(module_path.parent)
            if plugin_dir not in sys.path:
                sys.path.insert(0, plugin_dir)

            try:
                spec.loader.exec_module(module)
            finally:
                # Remove from sys.path
                if plugin_dir in sys.path:
                    sys.path.remove(plugin_dir)

            return module

        except Exception as e:
            logger.error(f"Error loading plugin module {module_path}: {e}")
            return None

    def unload_plugin(self, plugin_id: str) -> bool:
        """Unload a plugin and clean up resources."""
        try:
            if plugin_id not in self.loaded_plugins:
                logger.warning(f"Plugin {plugin_id} not loaded")
                return True

            plugin_info = self.loaded_plugins[plugin_id]

            # Call plugin cleanup if available
            if hasattr(plugin_info["instance"], "cleanup"):
                try:
                    plugin_info["instance"].cleanup()
                except Exception as e:
                    logger.error(f"Error during plugin cleanup for {plugin_id}: {e}")

            # Unregister fonts
            self.display_manager.unregister_plugin_fonts(plugin_id)

            # Unregister font defaults
            self.display_manager.unregister_plugin_font_defaults(plugin_id)

            # Remove from loaded plugins
            del self.loaded_plugins[plugin_id]
            if plugin_id in self.plugin_manifests:
                del self.plugin_manifests[plugin_id]

            logger.info(f"Successfully unloaded plugin: {plugin_id}")
            return True

        except Exception as e:
            logger.error(f"Error unloading plugin {plugin_id}: {e}")
            return False

    def get_loaded_plugins(self) -> List[str]:
        """Get list of loaded plugin IDs."""
        return list(self.loaded_plugins.keys())

    def get_plugin_info(self, plugin_id: str) -> Optional[Dict[str, Any]]:
        """Get information about a loaded plugin."""
        if plugin_id not in self.loaded_plugins:
            return None

        plugin_info = self.loaded_plugins[plugin_id]
        return {
            "id": plugin_id,
            "name": plugin_info["manifest"].get("name"),
            "version": plugin_info["manifest"].get("version"),
            "description": plugin_info["manifest"].get("description"),
            "loaded": True
        }

    def reload_plugin(self, plugin_id: str) -> bool:
        """Reload a plugin (unload and reload)."""
        try:
            if plugin_id not in self.loaded_plugins:
                logger.error(f"Cannot reload plugin {plugin_id}: not currently loaded")
                return False

            plugin_info = self.loaded_plugins[plugin_id]
            plugin_path = plugin_info["path"]

            # Unload
            success = self.unload_plugin(plugin_id)
            if not success:
                return False

            # Reload
            return self.load_plugin(plugin_path, plugin_id)

        except Exception as e:
            logger.error(f"Error reloading plugin {plugin_id}: {e}")
            return False

    def get_plugin_fonts(self, plugin_id: str) -> List[str]:
        """Get list of fonts registered by a plugin."""
        return self.display_manager.get_plugin_fonts(plugin_id)

    def list_all_fonts(self) -> Dict[str, List[str]]:
        """Get all available fonts organized by source."""
        return self.display_manager.list_available_fonts()
