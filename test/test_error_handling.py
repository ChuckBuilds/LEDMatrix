import pytest
import logging
import json
import tempfile
from pathlib import Path
from src.exceptions import CacheError, ConfigError, PluginError, DisplayError, LEDMatrixError
from src.common.error_handler import (
    handle_file_operation,
    handle_json_operation,
    safe_execute,
    retry_on_failure,
    log_and_continue,
    log_and_raise
)

class TestCustomExceptions:
    """Test custom exception classes."""
    
    def test_cache_error(self):
        """Test CacheError initialization."""
        error = CacheError("Cache failed", cache_key="test_key")
        # CacheError includes context in string representation
        assert "Cache failed" in str(error)
        assert error.context.get('cache_key') == "test_key"
        
    def test_config_error(self):
        """Test ConfigError initialization."""
        error = ConfigError("Config invalid", config_path='config.json')
        # ConfigError includes context in string representation
        assert "Config invalid" in str(error)
        assert error.context.get('config_path') == 'config.json'
        
    def test_plugin_error(self):
        """Test PluginError initialization."""
        error = PluginError("Plugin crashed", plugin_id='weather')
        # PluginError includes context in string representation
        assert "Plugin crashed" in str(error)
        assert error.context.get('plugin_id') == 'weather'
        
    def test_display_error(self):
        """Test DisplayError initialization."""
        error = DisplayError("Display not found", display_mode='adafruit')
        # DisplayError includes context in string representation
        assert "Display not found" in str(error)
        assert error.context.get('display_mode') == 'adafruit'


class TestErrorHandlerUtilities:
    """Test error handler utilities."""
    
    def test_handle_file_operation_read_success(self, tmp_path):
        """Test successful file read."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("test content")
        
        result = handle_file_operation(
            lambda: test_file.read_text(),
            "Read failed",
            logging.getLogger(__name__),
            default=""
        )
        assert result == "test content"
        
    def test_handle_file_operation_read_failure(self, tmp_path):
        """Test file read failure."""
        non_existent = tmp_path / "nonexistent.txt"
        
        result = handle_file_operation(
            lambda: non_existent.read_text(),
            "Read failed",
            logging.getLogger(__name__),
            default="fallback"
        )
        assert result == "fallback"
        
    def test_handle_json_operation_success(self, tmp_path):
        """Test successful JSON parse."""
        test_file = tmp_path / "test.json"
        test_file.write_text('{"key": "value"}')
        
        result = handle_json_operation(
            lambda: json.loads(test_file.read_text()),
            "JSON parse failed",
            logging.getLogger(__name__),
            default={}
        )
        assert result == {"key": "value"}
        
    def test_handle_json_operation_failure(self, tmp_path):
        """Test JSON parse failure."""
        test_file = tmp_path / "invalid.json"
        test_file.write_text('invalid json {')
        
        result = handle_json_operation(
            lambda: json.loads(test_file.read_text()),
            "JSON parse failed",
            logging.getLogger(__name__),
            default={"default": True}
        )
        assert result == {"default": True}
        
    def test_safe_execute_success(self):
        """Test successful execution with safe_execute."""
        def success_func():
            return "success"
            
        result = safe_execute(
            success_func,
            "Execution failed",
            logging.getLogger(__name__),
            default="failed"
        )
        assert result == "success"
        
    def test_safe_execute_failure(self):
        """Test failure handling with safe_execute."""
        def failing_func():
            raise ValueError("Something went wrong")
            
        result = safe_execute(
            failing_func,
            "Execution failed",
            logging.getLogger(__name__),
            default="fallback"
        )
        assert result == "fallback"
        
