# How to Run Tests for LEDMatrix

This guide explains how to use the test suite for the LEDMatrix project.

## Prerequisites

### 1. Install Test Dependencies

Make sure you have the testing packages installed:

```bash
# Install all dependencies including test packages
pip install -r requirements.txt -r requirements-test.txt
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

`pytest.ini` declares the markers `unit`, `integration`, `hardware`, `slow`
and `plugin` (with `--strict-markers`, so a typo in a marker name is an
error). Few tests are marked: only a handful carry `unit`, and none currently
carry `integration`, `slow` or `hardware`, so `-m integration` and `-m slow`
select nothing. Select tests by file, directory or `-k` instead.

```bash
# What CI runs for the core suites (excludes anything marked hardware)
pytest -m "not hardware" test/ --ignore=test/plugins

# Tests whose name matches an expression
pytest -k "config and not secrets"
```

### Run Tests in a Directory

```bash
# Run all tests in the test directory
pytest test/

# Run plugin tests only
pytest test/plugins/

# Run web interface tests only
pytest test/web_interface/

# Run web interface integration tests
pytest test/web_interface/integration/
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

Coverage is not collected by a plain `pytest` run: `pytest.ini` deliberately
has no coverage flags, so local runs stay fast. Ask for it explicitly
(needs `pytest-cov`, which is in `requirements-test.txt`):

```bash
# Terminal summary
pytest --cov=src --cov=web_interface --cov-report=term test/ --ignore=test/plugins

# HTML report in htmlcov/
pytest --cov=src --cov=web_interface --cov-report=html test/ --ignore=test/plugins
```

Then open `htmlcov/index.html` in your browser (`xdg-open` on Linux, `open`
on macOS, `start` on Windows).

### Coverage Threshold

The only threshold is in CI: the core unit-test job in
[`.github/workflows/test.yml`](../.github/workflows/test.yml) runs with
`--cov-fail-under=52`. To check it locally, add that flag to the command
above.

## Common Test Scenarios

### Run Tests After Making Changes

```bash
# Quick run: just the tests for the area you changed
pytest test/test_config_manager.py

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
├── conftest.py                          # Shared fixtures and configuration
├── test_display_controller.py           # Display controller tests
├── test_display_manager.py              # Display manager tests
├── test_plugin_system.py                # Plugin system tests
├── test_plugin_loader.py                # Plugin discovery/loading tests
├── test_plugin_loading_failures.py      # Plugin failure-mode tests
├── test_cache_manager.py                # Cache manager tests
├── test_config_manager.py               # Config manager tests
├── test_config_service.py               # Config service tests
├── test_config_validation_edge_cases.py # Config edge cases
├── test_font_manager.py                 # Font manager tests
├── test_text_helper.py                  # Text helper tests
├── test_error_handling.py               # Error handling tests
├── test_error_aggregator.py             # Error aggregation tests
├── test_schema_manager.py               # Schema manager tests
├── test_web_api.py                      # Web API tests
├── plugins/                             # Plugin rendering suites
│   ├── test_plugin_matrix.py            # Every discovered plugin, across panel sizes
│   ├── test_harness.py
│   └── test_visual_rendering.py
└── web_interface/
    ├── test_config_manager_atomic.py
    ├── test_state_reconciliation.py
    ├── test_plugin_operation_queue.py
    ├── test_dedup_unique_arrays.py
    └── integration/                     # Web interface integration tests
        ├── test_config_flows.py
        └── test_plugin_operations.py
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
# Make sure you're in the project root (wherever you cloned it)
cd ~/LEDMatrix

# Check Python path
python -c "import sys; print(sys.path)"

# Run pytest from project root
pytest
```

### Missing Dependencies

If tests fail due to missing packages:

```bash
# Install all dependencies
pip install -r requirements.txt -r requirements-test.txt

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

# Coverage is opt-in; ask for it explicitly
pytest --cov=src --cov=web_interface --cov-report=html
```

## Continuous Integration

The repo runs the pytest suite via
[`.github/workflows/test.yml`](../.github/workflows/test.yml) on every
push and pull request: a plugin-safety job that runs `test/plugins/`, and a
core unit-test job that runs the whole `test/` tree except `test/plugins/`
with `-m "not hardware"` and enforces coverage (`--cov-fail-under=52`). New
test files are picked up automatically. Release version consistency is checked by
[`.github/workflows/release-version-check.yml`](../.github/workflows/release-version-check.yml).
Bandit, flake8, mypy and gitleaks run as pre-commit hooks (see
`.pre-commit-config.yaml`), not in CI.

## Best Practices

1. **Run tests before committing**:
   ```bash
   pytest test/test_<area>.py  # Quick check of what you touched
   ```

2. **Run full suite before pushing**:
   ```bash
   pytest  # Full test suite (add --cov flags for coverage)
   ```

3. **Fix failing tests immediately** - Don't let them accumulate

4. **Keep coverage above threshold** - CI fails below 52%

5. **Write tests for new features** - Add tests when adding new functionality

## Quick Reference

```bash
# Most common commands
pytest                    # Run all tests (no coverage)
pytest -v                 # Verbose output
pytest test/test_x.py     # Run one file
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
