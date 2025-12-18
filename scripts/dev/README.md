# Development Scripts

This directory contains scripts and utilities for development and testing.

## Scripts

- **`dev_plugin_setup.sh`** - Sets up plugin development environment by linking plugin repositories
- **`run_emulator.sh`** - Runs the LED Matrix display in emulator mode (for development without hardware)
- **`validate_python.py`** - Validates Python files for common formatting and syntax errors

## Usage

### Plugin Development Setup
```bash
./scripts/dev/dev_plugin_setup.sh link-github <plugin-name>
```

### Running Emulator
```bash
./scripts/dev/run_emulator.sh
```

### Validating Python Files
```bash
python3 scripts/dev/validate_python.py <file.py>
```

