#!/usr/bin/env python3
"""
Diagnostic script to test plugin system import
Run this on the Raspberry Pi to diagnose import issues
"""

import sys
import os
from pathlib import Path

print("=== Plugin System Import Diagnostic ===\n")

# Get project directory
project_dir = os.path.dirname(os.path.abspath(__file__))
print(f"Project directory: {project_dir}")

# Add to path
if project_dir not in sys.path:
    sys.path.insert(0, project_dir)
    print(f"Added {project_dir} to sys.path")

print(f"\nPython path:")
for i, path in enumerate(sys.path[:5]):
    print(f"  {i}: {path}")

# Check if src/__init__.py exists
src_init = Path(project_dir) / "src" / "__init__.py"
print(f"\nsrc/__init__.py exists: {src_init.exists()}")
if src_init.exists():
    print(f"  Path: {src_init}")
    print(f"  Size: {src_init.stat().st_size} bytes")

# Check if src/plugin_system/__init__.py exists
plugin_init = Path(project_dir) / "src" / "plugin_system" / "__init__.py"
print(f"\nsrc/plugin_system/__init__.py exists: {plugin_init.exists()}")
if plugin_init.exists():
    print(f"  Path: {plugin_init}")
    print(f"  Size: {plugin_init.stat().st_size} bytes")

# Try importing src
print("\n=== Testing imports ===")
try:
    import src
    print("✓ Successfully imported 'src'")
    print(f"  src.__file__ = {src.__file__}")
except Exception as e:
    print(f"✗ Failed to import 'src': {e}")
    sys.exit(1)

# Try importing src.plugin_system
try:
    from src import plugin_system
    print("✓ Successfully imported 'src.plugin_system'")
    print(f"  plugin_system.__file__ = {plugin_system.__file__}")
except Exception as e:
    print(f"✗ Failed to import 'src.plugin_system': {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Try importing PluginManager
try:
    from src.plugin_system import PluginManager
    print("✓ Successfully imported 'PluginManager'")
    print(f"  PluginManager class: {PluginManager}")
except Exception as e:
    print(f"✗ Failed to import 'PluginManager': {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

print("\n=== All imports successful! ===")

