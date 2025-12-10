# How to Run Tests for LEDMatrix

This guide explains how to use the test suite for the LEDMatrix project.

## Prerequisites

### 1. Install Test Dependencies

Make sure you have the testing packages installed:

```bash
# Install all dependencies including test packages
pip install -r requirements.txt

# Or install just the test dependencies
pip install pytest pytest-cov pytest-mock pytest-timeout
```

### 2. Set Environment Variables

For tests that don't require hardware, set the emulator mode:

```bash
export EMULATOR=true
```

This ensures tests use the emulator instead of trying to access actual hardware.

## Running Tests

### Run All Tests

```bash
# From the project root directory
pytest

# Or with more verbose output
pytest -v

# Or with even more detail
pytest -vv
```

### Run Specific Test Files

```bash
# Run a specific test file
pytest test/test_display_controller.py

# Run multiple specific files
pytest test/test_display_controller.py test/test_plugin_system.py
```

### Run Specific Test Classes or Functions

```bash
# Run a specific test class
pytest test/test_display_controller.py::TestDisplayControllerModeRotation

# Run a specific test function
pytest test/test_display_controller.py::TestDisplayControllerModeRotation::test_basic_rotation
```

### Run Tests by Marker

The tests use markers to categorize them:

```bash
# Run only unit tests (fast, isolated)
pytest -m unit

# Run only integration tests
pytest -m integration

# Run tests that don't require hardware
pytest -m "not hardware"

# Run slow tests
pytest -m slow
```

### Run Tests in a Directory

```bash
# Run all tests in the test directory
pytest test/

# Run all integration tests
pytest test/integration/
```

## Understanding Test Output

### Basic Output

When you run `pytest`, you'll see:

```
test/test_display_controller.py::TestDisplayControllerInitialization::test_init_success PASSED
test/test_display_controller.py::TestDisplayControllerModeRotation::test_basic_rotation PASSED
...
```

- `PASSED` - Test succeeded
- `FAILED` - Test failed (check the error message)
- `SKIPPED` - Test was skipped (usually due to missing dependencies or conditions)
- `ERROR` - Test had an error during setup

### Verbose Output

Use `-v` or `-vv` for more detail:

```bash
pytest -vv
```

This shows:
- Full test names
- Setup/teardown information
- More detailed failure messages

### Show Print Statements

To see print statements and logging output:

```bash
pytest -s
```

Or combine with verbose:

```bash
pytest -sv
```

## Coverage Reports

The test suite is configured to generate coverage reports.

### View Coverage in Terminal

```bash
# Coverage is automatically shown when running pytest
pytest

# The output will show something like:
# ----------- coverage: platform linux, python 3.11.5 -----------
# Name                                    Stmts   Miss  Cover   Missing
# ---------------------------------------------------------------------
# src/display_controller.py                 450     120    73%   45-67, 89-102
```

### Generate HTML Coverage Report

```bash
# HTML report is automatically generated in htmlcov/
pytest

# Then open the report in your browser
# On Linux:
xdg-open htmlcov/index.html

# On macOS:
open htmlcov/index.html

# On Windows:
start htmlcov/index.html
```

The HTML report shows:
- Line-by-line coverage
- Files with low coverage highlighted
- Interactive navigation

### Coverage Threshold

The tests are configured to fail if coverage drops below 30%. To change this, edit `pytest.ini`:

```ini
--cov-fail-under=30  # Change this value
```

## Common Test Scenarios

### Run Tests After Making Changes

```bash
# Quick test run (just unit tests)
pytest -m unit

# Full test suite
pytest
```

### Debug a Failing Test

```bash
# Run with maximum verbosity and show print statements
pytest -vv -s test/test_display_controller.py::TestDisplayControllerModeRotation::test_basic_rotation

# Run with Python debugger (pdb)
pytest --pdb test/test_display_controller.py::TestDisplayControllerModeRotation::test_basic_rotation
```

### Run Tests in Parallel (Faster)

```bash
# Install pytest-xdist first
pip install pytest-xdist

# Run tests in parallel (4 workers)
pytest -n 4

# Auto-detect number of CPUs
pytest -n auto
```

### Stop on First Failure

```bash
# Stop immediately when a test fails
pytest -x

# Stop after N failures
pytest --maxfail=3
```

## Test Organization

### Test Files Structure

```
test/
├── conftest.py                    # Shared fixtures and configuration
├── test_display_controller.py     # Display controller tests
├── test_plugin_system.py          # Plugin system tests
├── test_display_manager.py        # Display manager tests
├── test_config_service.py         # Config service tests
├── test_cache_manager.py          # Cache manager tests
├── test_font_manager.py           # Font manager tests
├── test_error_handling.py         # Error handling tests
├── test_config_manager.py         # Config manager tests
├── integration/                   # Integration tests
│   ├── test_e2e.py               # End-to-end tests
│   └── test_plugin_integration.py # Plugin integration tests
├── test_error_scenarios.py        # Error scenario tests
└── test_edge_cases.py             # Edge case tests
```

### Test Categories

- **Unit Tests**: Fast, isolated tests for individual components
- **Integration Tests**: Tests that verify components work together
- **Error Scenarios**: Tests for error handling and edge cases
- **Edge Cases**: Boundary conditions and unusual inputs

## Troubleshooting

### Import Errors

If you see import errors:

```bash
# Make sure you're in the project root
cd /home/chuck/Github/LEDMatrix

# Check Python path
python -c "import sys; print(sys.path)"

# Run pytest from project root
pytest
```

### Missing Dependencies

If tests fail due to missing packages:

```bash
# Install all dependencies
pip install -r requirements.txt

# Or install specific missing package
pip install <package-name>
```

### Hardware Tests Failing

If tests that require hardware are failing:

```bash
# Set emulator mode
export EMULATOR=true

# Or skip hardware tests
pytest -m "not hardware"
```

### Coverage Not Working

If coverage reports aren't generating:

```bash
# Make sure pytest-cov is installed
pip install pytest-cov

# Run with explicit coverage
pytest --cov=src --cov-report=html
```

## Continuous Integration

Tests are configured to run automatically in CI/CD. The GitHub Actions workflow (`.github/workflows/tests.yml`) runs:

- All tests on multiple Python versions (3.10, 3.11, 3.12)
- Coverage reporting
- Uploads coverage to Codecov (if configured)

## Best Practices

1. **Run tests before committing**:
   ```bash
   pytest -m unit  # Quick check
   ```

2. **Run full suite before pushing**:
   ```bash
   pytest  # Full test suite with coverage
   ```

3. **Fix failing tests immediately** - Don't let them accumulate

4. **Keep coverage above threshold** - Aim for 70%+ coverage

5. **Write tests for new features** - Add tests when adding new functionality

## Quick Reference

```bash
# Most common commands
pytest                    # Run all tests with coverage
pytest -v                 # Verbose output
pytest -m unit           # Run only unit tests
pytest -k "test_name"    # Run tests matching pattern
pytest --cov=src         # Generate coverage report
pytest -x                # Stop on first failure
pytest --pdb              # Drop into debugger on failure
```

## Getting Help

- Check test output for error messages
- Look at the test file to understand what's being tested
- Check `conftest.py` for available fixtures
- Review `pytest.ini` for configuration options
