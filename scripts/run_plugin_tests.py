#!/usr/bin/env python3
"""
Plugin Test Runner

Discovers and runs tests for LEDMatrix plugins.
Supports both unittest and pytest.
"""

import sys
import os
import argparse
import subprocess
from pathlib import Path
from typing import Optional

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def discover_plugin_tests(plugins_dir: Path, plugin_id: Optional[str] = None) -> list:
    """
    Discover test files in plugin directories.
    
    Args:
        plugins_dir: Plugins directory path
        plugin_id: Optional specific plugin ID to test
    
    Returns:
        List of test file paths
    """
    test_files = []
    
    if plugin_id:
        # Test specific plugin
        plugin_dir = plugins_dir / plugin_id
        if plugin_dir.exists():
            test_files.extend(_find_tests_in_dir(plugin_dir))
    else:
        # Test all plugins
        for item in plugins_dir.iterdir():
            if not item.is_dir():
                continue
            if item.name.startswith('.') or item.name.startswith('_'):
                continue
            test_files.extend(_find_tests_in_dir(item))
    
    return test_files


def _find_tests_in_dir(directory: Path) -> list:
    """Find test files in a directory."""
    test_files = []
    
    # Look for test files
    patterns = ['test_*.py', '*_test.py', 'tests/test_*.py', 'tests/*_test.py']
    
    for pattern in patterns:
        if '/' in pattern:
            # Subdirectory pattern
            subdir, file_pattern = pattern.split('/', 1)
            test_dir = directory / subdir
            if test_dir.exists():
                test_files.extend(test_dir.glob(file_pattern))
        else:
            # Direct pattern
            test_files.extend(directory.glob(pattern))
    
    return sorted(set(test_files))


def run_unittest_tests(test_files: list, verbose: bool = False) -> int:
    """
    Run tests using unittest.
    
    Args:
        test_files: List of test file paths
        verbose: Enable verbose output
    
    Returns:
        Exit code (0 for success, non-zero for failure)
    """
    import unittest
    
    # Discover tests
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    
    for test_file in test_files:
        # Import the test module
        module_name = test_file.stem
        spec = importlib.util.spec_from_file_location(module_name, test_file)
        if spec and spec.loader:
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            
            # Load tests from module
            tests = loader.loadTestsFromModule(module)
            suite.addTests(tests)
    
    # Run tests
    runner = unittest.TextTestRunner(verbosity=2 if verbose else 1)
    result = runner.run(suite)
    
    return 0 if result.wasSuccessful() else 1


def run_pytest_tests(test_files: list, verbose: bool = False, coverage: bool = False) -> int:
    """
    Run tests using pytest.
    
    Args:
        test_files: List of test file paths
        verbose: Enable verbose output
        coverage: Generate coverage report
    
    Returns:
        Exit code (0 for success, non-zero for failure)
    """
    import pytest
    
    args = []
    
    if verbose:
        args.append('-v')
    else:
        args.append('-q')
    
    if coverage:
        args.extend(['--cov', 'plugins', '--cov-report', 'html', '--cov-report', 'term'])
    
    # Add test files
    args.extend([str(f) for f in test_files])
    
    # Run pytest
    exit_code = pytest.main(args)
    return exit_code


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description='Run LEDMatrix plugin tests')
    parser.add_argument('--plugin', '-p', help='Test specific plugin ID')
    parser.add_argument('--plugins-dir', '-d', default='plugins', 
                       help='Plugins directory (default: plugins)')
    parser.add_argument('--runner', '-r', choices=['unittest', 'pytest', 'auto'],
                       default='auto', help='Test runner to use (default: auto)')
    parser.add_argument('--verbose', '-v', action='store_true',
                       help='Enable verbose output')
    parser.add_argument('--coverage', '-c', action='store_true',
                       help='Generate coverage report (pytest only)')
    
    args = parser.parse_args()
    
    plugins_dir = Path(args.plugins_dir)
    if not plugins_dir.exists():
        print(f"Error: Plugins directory not found: {plugins_dir}")
        return 1
    
    # Discover tests
    test_files = discover_plugin_tests(plugins_dir, args.plugin)
    
    if not test_files:
        if args.plugin:
            print(f"No tests found for plugin: {args.plugin}")
        else:
            print("No test files found in plugins directory")
        return 0
    
    print(f"Found {len(test_files)} test file(s)")
    for test_file in test_files:
        print(f"  - {test_file}")
    print()
    
    # Determine runner
    runner = args.runner
    if runner == 'auto':
        # Try pytest first, fall back to unittest
        try:
            import pytest
            runner = 'pytest'
        except ImportError:
            runner = 'unittest'
    
    # Run tests
    if runner == 'pytest':
        import importlib.util
        return run_pytest_tests(test_files, args.verbose, args.coverage)
    else:
        import importlib.util
        return run_unittest_tests(test_files, args.verbose)


if __name__ == '__main__':
    import importlib.util
    from typing import Optional
    sys.exit(main())

