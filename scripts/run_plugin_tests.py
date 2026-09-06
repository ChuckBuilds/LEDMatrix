#!/usr/bin/env python3
"""
Plugin Test Runner

Discovers and runs tests for LEDMatrix plugins.
Supports both unittest and pytest.
"""

import os
import re
import subprocess  # nosec B404 - runs repo-local test files, never a shell
import sys
import argparse
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


def _is_script_style(path) -> bool:
    """True when a test file is a standalone script, not a pytest module.

    Most plugin tests are written as `def main()` plus an `if __name__ ==
    "__main__"` guard and signal through an exit code. pytest collects zero
    items from those, so handing them to pytest printed "no tests ran" and this
    runner reported success over work it had not done -- 151 of 248 files on a
    fully populated rig.
    """
    try:
        src = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    has_pytest_items = re.search(r"^\s*(def test_|class Test|async def test_)", src, re.M)
    has_main_guard = "__main__" in src and "__name__" in src
    return bool(has_main_guard and not has_pytest_items)


def run_script_tests(test_files: list, verbose: bool = False) -> int:
    """Run standalone test scripts, honouring the 0 pass / 2 skip / 1 fail
    convention that ledmatrix-plugins' own runner established.

    Scripts opt into skipping by printing "SKIP: <reason>" and exiting 2 --
    a script that needs a tty or an LED matrix is not a regression.
    """
    env = dict(os.environ)
    # Prepend rather than setdefault. An inherited PYTHONPATH -- a developer's
    # shell, a tox run, another checkout -- otherwise wins outright, and the
    # subprocess imports a different copy of the core than the one under test.
    # That is exactly the failure ledmatrix-plugins#467 describes, and it is
    # invisible: the tests pass or fail against a tree nobody meant to test.
    inherited = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (f"{PROJECT_ROOT}{os.pathsep}{inherited}"
                         if inherited else str(PROJECT_ROOT))
    env["LEDMATRIX_CORE"] = str(PROJECT_ROOT)

    passed = skipped = failed = 0
    failures = []
    for path in test_files:
        try:
            # nosec B603 - fixed interpreter (sys.executable) plus a test path
            # this script discovered by globbing the repo; argument list, no
            # shell, so nothing is word-split or expanded.
            proc = subprocess.run([sys.executable, str(path)],  # nosec B603
                                  cwd=str(Path(path).parent),
                                  capture_output=True, text=True, env=env,
                                  stdin=subprocess.DEVNULL, timeout=300)
            rc = proc.returncode
            tail = " | ".join((proc.stdout or proc.stderr or "").strip().splitlines()[-2:])[:200]
        except subprocess.TimeoutExpired:
            rc, tail = 1, "timed out after 300s"
        if rc == 0:
            passed += 1
            label = "pass"
        elif rc == 2:
            skipped += 1
            label = "SKIP"
        else:
            failed += 1
            label = "FAIL"
            failures.append(f"{Path(path).name}: exit {rc} | {tail}")
        if verbose or rc != 0:
            print(f"  [{label}] {Path(path).name}" + (f" -- {tail}" if rc != 0 else ""))

    print(f"\n{passed} passed, {skipped} skipped, {failed} failed (scripts)")
    for f in failures:
        print(f"  - {f}", file=sys.stderr)
    return 1 if failed else 0


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
    parser.add_argument('--plugins-dir', '-d', default=None,
                       help='Plugins directory (default: auto-detect plugins/ or plugin-repos/)')
    parser.add_argument('--runner', '-r', choices=['unittest', 'pytest', 'auto'],
                       default='auto', help='Test runner to use (default: auto)')
    parser.add_argument('--verbose', '-v', action='store_true',
                       help='Enable verbose output')
    parser.add_argument('--coverage', '-c', action='store_true',
                       help='Generate coverage report (pytest only)')
    
    args = parser.parse_args()
    
    if args.plugins_dir:
        plugins_dir = Path(args.plugins_dir)
    else:
        # Auto-detect: prefer plugins/ if it has content, then plugin-repos/
        plugins_path = PROJECT_ROOT / 'plugins'
        plugin_repos_path = PROJECT_ROOT / 'plugin-repos'
        try:
            has_plugins = plugins_path.exists() and any(
                p for p in plugins_path.iterdir()
                if p.is_dir() and not p.name.startswith('.')
            )
        except PermissionError:
            print(f"Warning: cannot read {plugins_path}, falling back to plugin-repos/")
            has_plugins = False
        if has_plugins:
            plugins_dir = plugins_path
        elif plugin_repos_path.exists():
            plugins_dir = plugin_repos_path
        else:
            plugins_dir = plugins_path

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
    
    scripts = [f for f in test_files if _is_script_style(f)]
    modules = [f for f in test_files if f not in scripts]

    print(f"Found {len(test_files)} test file(s)"
          + (f" -- {len(modules)} collectable, {len(scripts)} standalone script(s)"
             if scripts else ""))
    for test_file in test_files:
        print(f"  - {test_file}")
    print()

    # Determine runner
    runner = args.runner
    if runner == 'auto':
        # Try pytest first, fall back to unittest
        try:
            runner = 'pytest'
        except ImportError:
            runner = 'unittest'

    # Standalone scripts cannot be collected by pytest or unittest -- run them
    # as the scripts they are. Doing this rather than silently collecting zero
    # items is the whole point: this runner used to report success having
    # executed nothing.
    rc = 0
    if scripts:
        rc |= run_script_tests(scripts, args.verbose)

    if modules:
        if runner == 'pytest':
            rc |= run_pytest_tests(modules, args.verbose, args.coverage)
        else:
            rc |= run_unittest_tests(modules, args.verbose)
    return rc


if __name__ == '__main__':
    import importlib.util
    from typing import Optional
    sys.exit(main())

