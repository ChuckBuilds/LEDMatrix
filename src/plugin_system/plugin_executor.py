"""
Plugin Executor

Handles plugin execution (update() and display() calls) with timeout handling,
error isolation, and performance monitoring.
"""

import time
import signal
from typing import Any, Optional, Dict, Callable
from threading import Thread, Event
import logging

from src.exceptions import PluginError
from src.logging_config import get_logger


class TimeoutError(Exception):
    """Raised when a plugin operation times out."""
    pass


class PluginExecutor:
    """Handles plugin execution with timeout and error isolation."""
    
    def __init__(
        self,
        default_timeout: float = 30.0,
        logger: Optional[logging.Logger] = None
    ) -> None:
        """
        Initialize the plugin executor.
        
        Args:
            default_timeout: Default timeout in seconds for plugin operations
            logger: Optional logger instance
        """
        self.default_timeout = default_timeout
        self.logger = logger or get_logger(__name__)
    
    def execute_with_timeout(
        self,
        operation: Callable[[], Any],
        timeout: Optional[float] = None,
        plugin_id: Optional[str] = None
    ) -> Any:
        """
        Execute a plugin operation with timeout.
        
        Args:
            operation: Function to execute
            timeout: Timeout in seconds (None = use default)
            plugin_id: Optional plugin ID for logging
            
        Returns:
            Result of operation
            
        Raises:
            TimeoutError: If operation times out
            PluginError: If operation raises an exception
        """
        timeout = timeout or self.default_timeout
        plugin_context = f"plugin {plugin_id}" if plugin_id else "plugin"
        
        # Use threading-based timeout (more reliable than signal-based)
        result_container = {'value': None, 'exception': None, 'completed': False}
        
        def target():
            try:
                result_container['value'] = operation()
                result_container['completed'] = True
            except Exception as e:
                result_container['exception'] = e
                result_container['completed'] = True
        
        thread = Thread(target=target, daemon=True)
        thread.start()
        thread.join(timeout=timeout)
        
        if not result_container['completed']:
            error_msg = f"{plugin_context} operation timed out after {timeout}s"
            self.logger.error(error_msg)
            raise TimeoutError(error_msg)
        
        if result_container['exception']:
            error = result_container['exception']
            error_msg = f"{plugin_context} operation failed: {error}"
            self.logger.error(error_msg, exc_info=True)
            raise PluginError(error_msg, plugin_id=plugin_id) from error
        
        return result_container['value']
    
    def execute_update(
        self,
        plugin: Any,
        plugin_id: str,
        timeout: Optional[float] = None
    ) -> bool:
        """
        Execute plugin update() method with error handling.
        
        Args:
            plugin: Plugin instance
            plugin_id: Plugin identifier
            timeout: Timeout in seconds (None = use default)
            
        Returns:
            True if update succeeded, False otherwise
        """
        try:
            start_time = time.time()
            self.execute_with_timeout(
                lambda: plugin.update(),
                timeout=timeout,
                plugin_id=plugin_id
            )
            duration = time.time() - start_time
            
            if duration > 5.0:  # Warn if update takes more than 5 seconds
                self.logger.warning(
                    "Plugin %s update() took %.2fs (consider optimizing)",
                    plugin_id,
                    duration
                )
            
            return True
        except TimeoutError:
            self.logger.error("Plugin %s update() timed out", plugin_id)
            return False
        except PluginError:
            # Already logged in execute_with_timeout
            return False
        except Exception as e:
            self.logger.error(
                "Unexpected error executing update() for plugin %s: %s",
                plugin_id,
                e,
                exc_info=True
            )
            return False
    
    def execute_display(
        self,
        plugin: Any,
        plugin_id: str,
        force_clear: bool = False,
        display_mode: Optional[str] = None,
        timeout: Optional[float] = None
    ) -> bool:
        """
        Execute plugin display() method with error handling.
        
        Args:
            plugin: Plugin instance
            plugin_id: Plugin identifier
            force_clear: Whether to force clear display
            display_mode: Optional display mode parameter
            timeout: Timeout in seconds (None = use default)
            
        Returns:
            True if display succeeded, False otherwise
        """
        try:
            start_time = time.time()
            
            # Check if plugin accepts display_mode parameter
            import inspect
            sig = inspect.signature(plugin.display)
            has_display_mode = 'display_mode' in sig.parameters
            
            # Capture the return value from the plugin's display() method
            if has_display_mode and display_mode:
                result = self.execute_with_timeout(
                    lambda: plugin.display(display_mode=display_mode, force_clear=force_clear),
                    timeout=timeout,
                    plugin_id=plugin_id
                )
            else:
                result = self.execute_with_timeout(
                    lambda: plugin.display(force_clear=force_clear),
                    timeout=timeout,
                    plugin_id=plugin_id
                )
            
            duration = time.time() - start_time
            
            if duration > 2.0:  # Warn if display takes more than 2 seconds
                self.logger.warning(
                    "Plugin %s display() took %.2fs (consider optimizing)",
                    plugin_id,
                    duration
                )
            
            # Return the actual result from the plugin's display() method
            # If it's a boolean, use it directly. Otherwise, treat None/other as True for backward compatibility
            if isinstance(result, bool):
                self.logger.debug(f"Plugin {plugin_id} display() returned boolean: {result}")
                return result
            # For backward compatibility: if plugin returns None or something else, treat as success
            self.logger.debug(f"Plugin {plugin_id} display() returned non-boolean: {result}, treating as True")
            return True
        except TimeoutError:
            self.logger.error("Plugin %s display() timed out", plugin_id)
            return False
        except PluginError:
            # Already logged in execute_with_timeout
            return False
        except Exception as e:
            self.logger.error(
                "Unexpected error executing display() for plugin %s: %s",
                plugin_id,
                e,
                exc_info=True
            )
            return False
    
    def execute_safe(
        self,
        operation: Callable[[], Any],
        plugin_id: str,
        operation_name: str = "operation",
        timeout: Optional[float] = None,
        default_return: Any = None
    ) -> Any:
        """
        Execute an operation safely, returning default on error.
        
        Args:
            operation: Function to execute
            plugin_id: Plugin identifier
            operation_name: Name of operation for logging
            timeout: Timeout in seconds (None = use default)
            default_return: Value to return on error
            
        Returns:
            Result of operation or default_return on error
        """
        try:
            return self.execute_with_timeout(
                operation,
                timeout=timeout,
                plugin_id=plugin_id
            )
        except (TimeoutError, PluginError, Exception) as e:
            self.logger.warning(
                "Plugin %s %s failed, using default return: %s",
                plugin_id,
                operation_name,
                e
            )
            return default_return

