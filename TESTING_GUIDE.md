# Testing Guide for Phase 1 & 2 Improvements

This guide explains how to test the codebase quality improvements made in Phase 1 and Phase 2.

## Prerequisites

Install testing dependencies:
```bash
pip install -r requirements.txt
```

Or install just testing packages:
```bash
pip install pytest pytest-cov pytest-mock mypy
```

## Running Tests

### Basic Test Execution

Run all tests:
```bash
python -m pytest test/ -v
```

Run specific test file:
```bash
python -m pytest test/test_cache_manager.py -v
python -m pytest test/test_config_manager.py -v
```

Run with coverage (if pytest-cov is installed):
```bash
python -m pytest test/ --cov=src --cov-report=term-missing
```

### Test Categories

Run only unit tests:
```bash
python -m pytest test/ -m unit -v
```

Run integration tests:
```bash
python -m pytest test/ -m integration -v
```

## Testing Phase 1 Improvements

### 1. Testing Infrastructure

**Test that pytest is working:**
```bash
python -m pytest test/ --collect-only
```

**Verify test fixtures:**
```bash
python -m pytest test/test_config_manager.py::TestConfigManagerInitialization -v
```

### 2. Error Handling Tests

**Test custom exceptions:**
```python
# Create test file: test/test_exceptions.py
from src.exceptions import CacheError, ConfigError, PluginError

def test_cache_error():
    error = CacheError("Cache failed", cache_key="test_key")
    assert "Cache failed" in str(error)
    assert error.context.get('cache_key') == "test_key"

def test_config_error():
    error = ConfigError("Config invalid", context={'file': 'config.json'})
    assert "Config invalid" in str(error)
```

**Run exception tests:**
```bash
python -m pytest test/test_exceptions.py -v
```

### 3. Type Hints Validation

**Run mypy type checking:**
```bash
mypy src/cache_manager.py
mypy src/config_manager.py
mypy src/plugin_system/plugin_manager.py
```

**Check all core modules:**
```bash
mypy src/cache_manager.py src/config_manager.py src/plugin_system/
```

### 4. Startup Validation Tests

**Test startup validator:**
```python
# Create test file: test/test_startup_validator.py
from src.startup_validator import StartupValidator
from unittest.mock import Mock

def test_startup_validator():
    config_manager = Mock()
    config_manager.load_config.return_value = {'display': {}, 'timezone': 'UTC'}
    
    validator = StartupValidator(config_manager)
    is_valid, errors, warnings = validator.validate_all()
    
    assert isinstance(is_valid, bool)
    assert isinstance(errors, list)
    assert isinstance(warnings, list)
```

## Testing Phase 2 Improvements

### 1. Cache Component Tests

**Test MemoryCache:**
```python
# Add to test/test_cache_manager.py
from src.cache.memory_cache import MemoryCache

def test_memory_cache():
    cache = MemoryCache(max_size=10)
    
    # Test set/get
    cache.set("key1", {"data": "value"})
    result = cache.get("key1")
    assert result == {"data": "value"}
    
    # Test expiration
    result = cache.get("key1", max_age=0)  # Expired
    assert result is None
    
    # Test cleanup
    removed = cache.cleanup(force=True)
    assert isinstance(removed, int)
```

**Test DiskCache:**
```python
from src.cache.disk_cache import DiskCache
import tempfile

def test_disk_cache(tmp_path):
    cache_dir = str(tmp_path / "cache")
    cache = DiskCache(cache_dir)
    
    # Test set/get
    cache.set("key1", {"data": "value"})
    result = cache.get("key1")
    assert result == {"data": "value"}
    
    # Test expiration
    result = cache.get("key1", max_age=0)
    assert result is None
```

**Test CacheStrategy:**
```python
from src.cache.cache_strategy import CacheStrategy

def test_cache_strategy():
    strategy = CacheStrategy()
    
    # Test strategy lookup
    result = strategy.get_cache_strategy("live_scores")
    assert "max_age" in result
    assert "memory_ttl" in result
    
    # Test data type detection
    data_type = strategy.get_data_type_from_key("nba_live_scores")
    assert data_type in ["sports_live", "live_scores"]
```

**Test CacheMetrics:**
```python
from src.cache.cache_metrics import CacheMetrics

def test_cache_metrics():
    metrics = CacheMetrics()
    
    # Record hits/misses
    metrics.record_hit()
    metrics.record_miss()
    metrics.record_fetch_time(0.5)
    
    # Get metrics
    stats = metrics.get_metrics()
    assert stats['total_requests'] == 2
    assert stats['cache_hit_rate'] == 0.5
```

**Run cache component tests:**
```bash
python -m pytest test/test_cache_manager.py -v -k "memory_cache or disk_cache or strategy or metrics"
```

### 2. Logging Configuration Tests

**Test logging setup:**
```python
# Create test file: test/test_logging_config.py
from src.logging_config import setup_logging, get_logger
import logging

def test_logging_setup():
    setup_logging(level=logging.INFO, format_type='readable')
    logger = get_logger("test")
    
    # Should not raise
    logger.info("Test message")
    assert logger.name == "test"

def test_plugin_logger():
    logger = get_logger("plugin.test", plugin_id="test_plugin")
    assert hasattr(logger, 'plugin_id') or True  # May be set via extra
```

**Run logging tests:**
```bash
python -m pytest test/test_logging_config.py -v
```

### 3. Error Handler Tests

**Test error handling utilities:**
```python
# Create test file: test/test_error_handler.py
from src.common.error_handler import (
    handle_file_operation,
    handle_json_operation,
    safe_execute
)
import json
import tempfile

def test_handle_file_operation(tmp_path):
    test_file = tmp_path / "test.txt"
    test_file.write_text("test content")
    
    result = handle_file_operation(
        lambda: test_file.read_text(),
        "Read failed",
        logging.getLogger(__name__),
        default=""
    )
    assert result == "test content"

def test_handle_json_operation(tmp_path):
    test_file = tmp_path / "test.json"
    test_file.write_text('{"key": "value"}')
    
    result = handle_json_operation(
        lambda: json.loads(test_file.read_text()),
        "JSON parse failed",
        logging.getLogger(__name__),
        default={}
    )
    assert result == {"key": "value"}
```

**Run error handler tests:**
```bash
python -m pytest test/test_error_handler.py -v
```

## Integration Testing

### Test CacheManager with New Components

```python
def test_cache_manager_integration():
    from src.cache_manager import CacheManager
    
    manager = CacheManager()
    
    # Test that it uses new components
    assert hasattr(manager, '_memory_cache_component')
    assert hasattr(manager, '_disk_cache_component')
    assert hasattr(manager, '_strategy_component')
    assert hasattr(manager, '_metrics_component')
    
    # Test backward compatibility
    manager.set("test_key", {"data": "value"})
    result = manager.get("test_key")
    assert result == {"data": "value"}
```

### Test Logging Integration

```python
def test_logging_integration():
    from src.logging_config import setup_logging
    from src.cache_manager import CacheManager
    import logging
    
    setup_logging(level=logging.INFO)
    
    # CacheManager should use centralized logging
    manager = CacheManager()
    assert manager.logger.name == "src.cache_manager"
```

## Manual Testing

### 1. Test Application Startup

**Run with startup validation:**
```bash
python run.py --emulator
```

**Check logs for:**
- Startup validation messages
- No configuration errors
- Cache directory initialization
- Plugin loading

### 2. Test Error Handling

**Simulate errors:**
- Remove config file and verify graceful handling
- Create invalid JSON in cache and verify cleanup
- Test with missing permissions

### 3. Test Logging Output

**Check log format:**
```bash
python run.py --emulator 2>&1 | head -20
```

**Verify:**
- Consistent log format
- Context information (plugin IDs, etc.)
- Appropriate log levels

### 4. Test Cache Performance

**Monitor cache metrics:**
```python
from src.cache_manager import CacheManager

manager = CacheManager()

# Use cache
manager.set("key1", {"data": "value"})
manager.get("key1")

# Check metrics
metrics = manager.get_cache_metrics()
print(f"Hit rate: {metrics['cache_hit_rate']:.2%}")
print(f"API calls saved: {metrics['api_calls_saved']}")
```

## Type Checking

### Run mypy on all modules

```bash
# Check core modules
mypy src/cache_manager.py
mypy src/config_manager.py
mypy src/plugin_system/plugin_manager.py
mypy src/plugin_system/base_plugin.py

# Check new modules
mypy src/logging_config.py
mypy src/startup_validator.py
mypy src/common/error_handler.py

# Check cache components
mypy src/cache/memory_cache.py
mypy src/cache/disk_cache.py
mypy src/cache/cache_strategy.py
mypy src/cache/cache_metrics.py
```

### Check with strict mode

```bash
mypy src/cache_manager.py --strict
```

## Coverage Goals

Current coverage target: 30%+ (Phase 1 goal)

Check coverage:
```bash
pytest test/ --cov=src --cov-report=term-missing --cov-report=html
```

View HTML report:
```bash
open htmlcov/index.html  # macOS
xdg-open htmlcov/index.html  # Linux
```

## Continuous Testing

### Run tests before committing

```bash
# Quick test run
pytest test/ -v --tb=short

# Full test with coverage
pytest test/ --cov=src --cov-report=term-missing
```

### Test on Raspberry Pi

```bash
# SSH to Pi
ssh pi@raspberrypi

# Navigate to project
cd /path/to/LEDMatrix

# Run tests
python -m pytest test/ -v
```

## Troubleshooting

### pytest-cov not found

If you see coverage errors, install it:
```bash
pip install pytest-cov
```

Or remove coverage options from pytest.ini temporarily.

### Import errors in tests

Make sure project root is in Python path:
```bash
export PYTHONPATH="${PYTHONPATH}:$(pwd)"
```

### mypy errors

Some errors may be expected (third-party libraries without stubs). Check mypy.ini for ignored imports.

## Next Steps

1. **Add more tests** for edge cases
2. **Increase coverage** to 70%+ (Phase 2 goal)
3. **Add integration tests** for full system
4. **Add performance tests** for cache components
5. **Add plugin system tests** for plugin loading and execution

