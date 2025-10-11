#!/usr/bin/env python3
"""
Test if we can import plugin_system from display_controller context
"""
import sys
import os

# Add project directory to Python path
project_dir = os.path.dirname(os.path.abspath(__file__))
if project_dir not in sys.path:
    sys.path.insert(0, project_dir)

print(f"Project dir: {project_dir}")
print(f"sys.path[0]: {sys.path[0]}")

# Test 1: Import src
print("\n=== Test 1: Import src ===")
import src
print(f"✓ src imported: {src}")

# Test 2: Import src.plugin_system
print("\n=== Test 2: Import src.plugin_system ===")
import src.plugin_system
print(f"✓ src.plugin_system imported: {src.plugin_system}")

# Test 3: Try the same import style as display_controller
print("\n=== Test 3: Import like display_controller ===")
try:
    # This is EXACTLY how display_controller.py does it
    from src.plugin_system import PluginManager
    print(f"✓ PluginManager imported: {PluginManager}")
except ImportError as e:
    print(f"✗ Failed: {e}")
    import traceback
    traceback.print_exc()

# Test 4: Now import display_controller and see if IT can import plugin_system
print("\n=== Test 4: Check display_controller's ability to import ===")
print("sys.path before importing display_controller:")
for i, p in enumerate(sys.path[:3]):
    print(f"  {i}: {p}")

from src import display_controller
print(f"✓ display_controller imported")

print("\nAll tests completed!")

