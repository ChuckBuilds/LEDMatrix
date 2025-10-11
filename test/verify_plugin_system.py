#!/usr/bin/env python3
"""
Plugin System Verification Script

This script verifies that the plugin system is properly installed and functional.
Run this script on the Raspberry Pi to diagnose plugin system issues.
"""

import os
import sys
import importlib.util

def check_file_exists(filepath):
    """Check if a file exists and return details."""
    exists = os.path.exists(filepath)
    if exists:
        size = os.path.getsize(filepath)
        return f"✓ EXISTS ({size} bytes)"
    else:
        return "✗ MISSING"

def check_directory_exists(dirpath):
    """Check if a directory exists and return details."""
    exists = os.path.exists(dirpath)
    if exists:
        return "✓ EXISTS"
    else:
        return "✗ MISSING"

def test_import(module_name, filepath):
    """Test importing a module and return result."""
    try:
        spec = importlib.util.spec_from_file_location(module_name, filepath)
        if spec is None:
            return f"✗ Could not create module spec for {filepath}"
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return f"✓ Successfully imported {module_name}"
    except Exception as e:
        return f"✗ Import failed: {e}"

def main():
    """Main verification function."""
    print("==========================================")
    print("Plugin System Verification")
    print("==========================================")
    print()

    # Get project directory
    project_dir = os.path.dirname(os.path.abspath(__file__))
    print(f"Project directory: {project_dir}")
    print(f"Current working directory: {os.getcwd()}")
    print(f"Python path: {sys.path[:3]}...")
    print()

    # Check key files
    print("=== File Existence Checks ===")
    files_to_check = [
        ("src/__init__.py", os.path.join(project_dir, "src", "__init__.py")),
        ("src/plugin_system/__init__.py", os.path.join(project_dir, "src", "plugin_system", "__init__.py")),
        ("src/plugin_system/base_plugin.py", os.path.join(project_dir, "src", "plugin_system", "base_plugin.py")),
        ("src/plugin_system/plugin_manager.py", os.path.join(project_dir, "src", "plugin_system", "plugin_manager.py")),
        ("plugins/hello-world/manifest.json", os.path.join(project_dir, "plugins", "hello-world", "manifest.json")),
        ("plugins/hello-world/manager.py", os.path.join(project_dir, "plugins", "hello-world", "manager.py")),
    ]

    for name, path in files_to_check:
        print(f"{name:30} {check_file_exists(path)}")
    print()

    # Check directories
    print("=== Directory Checks ===")
    dirs_to_check = [
        ("src/", os.path.join(project_dir, "src")),
        ("src/plugin_system/", os.path.join(project_dir, "src", "plugin_system")),
        ("plugins/", os.path.join(project_dir, "plugins")),
        ("plugins/hello-world/", os.path.join(project_dir, "plugins", "hello-world")),
    ]

    for name, path in dirs_to_check:
        print(f"{name:25} {check_directory_exists(path)}")
    print()

    # Test imports
    print("=== Import Tests ===")
    import_tests = [
        ("src.plugin_system", os.path.join(project_dir, "src", "plugin_system", "__init__.py")),
        ("src.plugin_system.base_plugin", os.path.join(project_dir, "src", "plugin_system", "base_plugin.py")),
        ("src.plugin_system.plugin_manager", os.path.join(project_dir, "src", "plugin_system", "plugin_manager.py")),
    ]

    for module_name, filepath in import_tests:
        result = test_import(module_name, filepath)
        print(f"{module_name:35} {result}")
    print()

    # Test plugin loading
    print("=== Plugin Loading Test ===")
    try:
        sys.path.insert(0, project_dir)
        from src.plugin_system import PluginManager
        print("✓ PluginManager import successful")

        # Try to create a PluginManager instance
        pm = PluginManager(plugins_dir=os.path.join(project_dir, "plugins"))
        discovered = pm.discover_plugins()
        print(f"✓ Plugin discovery successful: {len(discovered)} plugins found")

        for plugin_id in discovered:
            print(f"  - {plugin_id}")

    except Exception as e:
        print(f"✗ Plugin loading failed: {e}")
        import traceback
        print(f"  Traceback: {traceback.format_exc()}")
    print()

    print("=== Verification Complete ===")
    print("If any checks failed, ensure all files are properly deployed to the Pi.")
    print("You may need to run: git pull && git submodule update --init --recursive")

if __name__ == "__main__":
    main()
