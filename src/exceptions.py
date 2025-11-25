"""
Custom exception hierarchy for LEDMatrix.

Provides specific exception types for different error categories,
enabling better error handling and debugging.
"""


class LEDMatrixError(Exception):
    """Base exception for all LEDMatrix errors."""
    
    def __init__(self, message: str, context: dict = None):
        """
        Initialize the exception.
        
        Args:
            message: Error message
            context: Optional context dictionary with additional error details
        """
        super().__init__(message)
        self.message = message
        self.context = context or {}
    
    def __str__(self) -> str:
        """Return formatted error message with context."""
        if self.context:
            context_str = ", ".join(f"{k}={v}" for k, v in self.context.items())
            return f"{self.message} ({context_str})"
        return self.message


class CacheError(LEDMatrixError):
    """Exception raised for cache-related errors."""
    
    def __init__(self, message: str, cache_key: str = None, context: dict = None):
        """
        Initialize cache error.
        
        Args:
            message: Error message
            cache_key: Optional cache key that caused the error
            context: Optional context dictionary
        """
        if cache_key:
            context = context or {}
            context['cache_key'] = cache_key
        super().__init__(message, context)
        self.cache_key = cache_key


class ConfigError(LEDMatrixError):
    """Exception raised for configuration-related errors."""
    
    def __init__(self, message: str, config_path: str = None, field: str = None, context: dict = None):
        """
        Initialize config error.
        
        Args:
            message: Error message
            config_path: Optional path to config file
            field: Optional field name that caused the error
            context: Optional context dictionary
        """
        if config_path or field:
            context = context or {}
            if config_path:
                context['config_path'] = config_path
            if field:
                context['field'] = field
        super().__init__(message, context)
        self.config_path = config_path
        self.field = field


class PluginError(LEDMatrixError):
    """Exception raised for plugin-related errors."""
    
    def __init__(self, message: str, plugin_id: str = None, context: dict = None):
        """
        Initialize plugin error.
        
        Args:
            message: Error message
            plugin_id: Optional plugin ID that caused the error
            context: Optional context dictionary
        """
        if plugin_id:
            context = context or {}
            context['plugin_id'] = plugin_id
        super().__init__(message, context)
        self.plugin_id = plugin_id


class DisplayError(LEDMatrixError):
    """Exception raised for display-related errors."""
    
    def __init__(self, message: str, display_mode: str = None, context: dict = None):
        """
        Initialize display error.
        
        Args:
            message: Error message
            display_mode: Optional display mode that caused the error
            context: Optional context dictionary
        """
        if display_mode:
            context = context or {}
            context['display_mode'] = display_mode
        super().__init__(message, context)
        self.display_mode = display_mode

