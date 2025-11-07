#!/usr/bin/env python3
import logging
import sys
import os
import argparse

# Parse command-line arguments BEFORE any imports
parser = argparse.ArgumentParser(description='LEDMatrix Display Controller')
parser.add_argument('-e', '--emulator', action='store_true',
                    help='Run in emulator mode (uses pygame/RGBMatrixEmulator instead of hardware)')
args = parser.parse_args()

# Set emulator mode if requested (must be done BEFORE any imports that check EMULATOR env var)
if args.emulator:
    os.environ["EMULATOR"] = "true"
    print("=" * 60)
    print("LEDMatrix Emulator Mode Enabled")
    print("=" * 60)
    print("Using pygame/RGBMatrixEmulator for display")
    print("Press ESC to exit\n")

# Add project directory to Python path
project_dir = os.path.dirname(os.path.abspath(__file__))
if project_dir not in sys.path:
    sys.path.insert(0, project_dir)

# Debug: Print Python path and file checks (will show in systemd logs)
print(f"DEBUG: Project directory: {project_dir}", flush=True)
print(f"DEBUG: Python path[0]: {sys.path[0]}", flush=True)
print(f"DEBUG: Current working directory: {os.getcwd()}", flush=True)
print(f"DEBUG: EMULATOR mode: {os.environ.get('EMULATOR', 'false')}", flush=True)
print(f"DEBUG: src/__init__.py exists: {os.path.exists(os.path.join(project_dir, 'src', '__init__.py'))}", flush=True)
print(f"DEBUG: src/plugin_system/__init__.py exists: {os.path.exists(os.path.join(project_dir, 'src', 'plugin_system', '__init__.py'))}", flush=True)
print(f"DEBUG: src/plugin_system directory exists: {os.path.exists(os.path.join(project_dir, 'src', 'plugin_system'))}", flush=True)

# Additional debugging for plugin system
try:
    import sys
    plugin_system_path = os.path.join(project_dir, 'src', 'plugin_system')
    if plugin_system_path not in sys.path:
        sys.path.insert(0, plugin_system_path)
        print(f"DEBUG: Added plugin_system path to sys.path: {plugin_system_path}", flush=True)

    # Try to import the plugin system directly to get better error info
    print("DEBUG: Attempting to import src.plugin_system...", flush=True)
    from src.plugin_system import PluginManager
    print("DEBUG: Plugin system import successful", flush=True)
except ImportError as e:
    print(f"DEBUG: Plugin system import failed: {e}", flush=True)
    print(f"DEBUG: Import error details: {type(e).__name__}", flush=True)
except Exception as e:
    print(f"DEBUG: Unexpected error during plugin system import: {e}", flush=True)

# Configure logging before importing any other modules
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s.%(msecs)03d - %(levelname)s:%(name)s:%(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    stream=sys.stdout  # Explicitly set to stdout
)

# Now import the display controller
from src.display_controller import main

if __name__ == "__main__":
    main() 