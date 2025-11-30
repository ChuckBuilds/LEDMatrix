"""
Structured error handling for web interface.

Provides error codes, categories, and consistent error response formatting.
"""

from enum import Enum
from typing import Dict, Any, Optional, List
from dataclasses import dataclass


class ErrorCategory(Enum):
    """Error categories for classification."""
    CONFIGURATION = "configuration"
    PLUGIN = "plugin"
    VALIDATION = "validation"
    NETWORK = "network"
    PERMISSION = "permission"
    SYSTEM = "system"
    UNKNOWN = "unknown"


class ErrorCode(Enum):
    """Error codes for specific error types."""
    # Configuration errors
    CONFIG_SAVE_FAILED = "CONFIG_SAVE_FAILED"
    CONFIG_LOAD_FAILED = "CONFIG_LOAD_FAILED"
    CONFIG_VALIDATION_FAILED = "CONFIG_VALIDATION_FAILED"
    CONFIG_ROLLBACK_FAILED = "CONFIG_ROLLBACK_FAILED"
    
    # Plugin errors
    PLUGIN_NOT_FOUND = "PLUGIN_NOT_FOUND"
    PLUGIN_INSTALL_FAILED = "PLUGIN_INSTALL_FAILED"
    PLUGIN_UPDATE_FAILED = "PLUGIN_UPDATE_FAILED"
    PLUGIN_UNINSTALL_FAILED = "PLUGIN_UNINSTALL_FAILED"
    PLUGIN_LOAD_FAILED = "PLUGIN_LOAD_FAILED"
    PLUGIN_OPERATION_CONFLICT = "PLUGIN_OPERATION_CONFLICT"
    
    # Validation errors
    VALIDATION_ERROR = "VALIDATION_ERROR"
    SCHEMA_VALIDATION_FAILED = "SCHEMA_VALIDATION_FAILED"
    INVALID_INPUT = "INVALID_INPUT"
    
    # Network errors
    NETWORK_ERROR = "NETWORK_ERROR"
    API_ERROR = "API_ERROR"
    TIMEOUT = "TIMEOUT"
    
    # Permission errors
    PERMISSION_DENIED = "PERMISSION_DENIED"
    FILE_PERMISSION_ERROR = "FILE_PERMISSION_ERROR"
    
    # System errors
    SYSTEM_ERROR = "SYSTEM_ERROR"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
    
    # Unknown errors
    UNKNOWN_ERROR = "UNKNOWN_ERROR"


@dataclass
class WebInterfaceError:
    """
    Structured error for web interface responses.
    
    Provides consistent error format with error codes, categories,
    messages, and context.
    """
    error_code: ErrorCode
    message: str
    category: ErrorCategory
    details: Optional[str] = None
    context: Optional[Dict[str, Any]] = None
    suggested_fixes: Optional[List[str]] = None
    original_error: Optional[Exception] = None
    
    def __init__(
        self,
        error_code: ErrorCode,
        message: str,
        category: Optional[ErrorCategory] = None,
        details: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
        suggested_fixes: Optional[List[str]] = None,
        original_error: Optional[Exception] = None
    ):
        self.error_code = error_code
        self.message = message
        self.category = category or self._infer_category(error_code)
        self.details = details
        self.context = context or {}
        self.suggested_fixes = suggested_fixes or self._get_default_suggestions(error_code)
        self.original_error = original_error
    
    def _infer_category(self, error_code: ErrorCode) -> ErrorCategory:
        """Infer error category from error code."""
        code_str = error_code.value
        
        if code_str.startswith("CONFIG_"):
            return ErrorCategory.CONFIGURATION
        elif code_str.startswith("PLUGIN_"):
            return ErrorCategory.PLUGIN
        elif code_str.startswith("VALIDATION_") or code_str.startswith("SCHEMA_") or code_str == "INVALID_INPUT":
            return ErrorCategory.VALIDATION
        elif code_str.startswith("NETWORK_") or code_str == "API_ERROR" or code_str == "TIMEOUT":
            return ErrorCategory.NETWORK
        elif code_str.startswith("PERMISSION_") or code_str == "FILE_PERMISSION_ERROR":
            return ErrorCategory.PERMISSION
        elif code_str.startswith("SYSTEM_") or code_str == "SERVICE_UNAVAILABLE":
            return ErrorCategory.SYSTEM
        else:
            return ErrorCategory.UNKNOWN
    
    def _get_default_suggestions(self, error_code: ErrorCode) -> List[str]:
        """Get default suggested fixes for error code."""
        suggestions_map = {
            ErrorCode.CONFIG_SAVE_FAILED: [
                "Check file permissions on config directory",
                "Check available disk space",
                "Verify config file is not locked by another process"
            ],
            ErrorCode.CONFIG_LOAD_FAILED: [
                "Check config file exists and is readable",
                "Verify config file is valid JSON",
                "Check file permissions"
            ],
            ErrorCode.CONFIG_VALIDATION_FAILED: [
                "Review validation errors above",
                "Check config against schema",
                "Verify all required fields are present"
            ],
            ErrorCode.PLUGIN_NOT_FOUND: [
                "Verify plugin is installed",
                "Check plugin ID is correct",
                "Refresh plugin list"
            ],
            ErrorCode.PLUGIN_INSTALL_FAILED: [
                "Check internet connection",
                "Verify plugin repository URL is correct",
                "Check available disk space",
                "Review plugin installation logs"
            ],
            ErrorCode.PLUGIN_OPERATION_CONFLICT: [
                "Wait for current operation to complete",
                "Cancel conflicting operation if needed",
                "Check operation status"
            ],
            ErrorCode.VALIDATION_ERROR: [
                "Review validation errors",
                "Check input format and types",
                "Verify required fields are provided"
            ],
            ErrorCode.PERMISSION_DENIED: [
                "Check file/directory permissions",
                "Verify user has required access",
                "Check if running with correct user"
            ],
            ErrorCode.NETWORK_ERROR: [
                "Check internet connection",
                "Verify API endpoint is accessible",
                "Check firewall settings"
            ],
            ErrorCode.TIMEOUT: [
                "Retry the operation",
                "Check network connection",
                "Verify service is responding"
            ],
        }
        
        return suggestions_map.get(error_code, ["Review error details and try again"])
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert error to dictionary for JSON response."""
        result = {
            "status": "error",
            "error_code": self.error_code.value,
            "error_category": self.category.value,
            "message": self.message,
        }
        
        if self.details:
            result["details"] = self.details
        
        if self.context:
            result["context"] = self.context
        
        if self.suggested_fixes:
            result["suggested_fixes"] = self.suggested_fixes
        
        return result
    
    @classmethod
    def from_exception(
        cls,
        exception: Exception,
        error_code: Optional[ErrorCode] = None,
        context: Optional[Dict[str, Any]] = None
    ) -> 'WebInterfaceError':
        """
        Create WebInterfaceError from an exception.
        
        Args:
            exception: Exception to convert
            error_code: Optional specific error code
            context: Optional additional context
        """
        # Infer error code from exception type if not provided
        if not error_code:
            error_code = cls._infer_error_code(exception)
        
        # Build context
        error_context = context or {}
        error_context['exception_type'] = type(exception).__name__
        
        return cls(
            error_code=error_code,
            message=str(exception),
            details=cls._get_exception_details(exception),
            context=error_context,
            original_error=exception
        )
    
    @classmethod
    def _infer_error_code(cls, exception: Exception) -> ErrorCode:
        """Infer error code from exception type."""
        exception_name = type(exception).__name__
        
        if "Config" in exception_name:
            return ErrorCode.CONFIG_SAVE_FAILED
        elif "Plugin" in exception_name:
            return ErrorCode.PLUGIN_LOAD_FAILED
        elif "Permission" in exception_name or "Access" in exception_name:
            return ErrorCode.PERMISSION_DENIED
        elif "Validation" in exception_name or "Schema" in exception_name:
            return ErrorCode.VALIDATION_ERROR
        elif "Network" in exception_name or "Connection" in exception_name:
            return ErrorCode.NETWORK_ERROR
        elif "Timeout" in exception_name:
            return ErrorCode.TIMEOUT
        else:
            return ErrorCode.UNKNOWN_ERROR
    
    @classmethod
    def _get_exception_details(cls, exception: Exception) -> Optional[str]:
        """Get additional details from exception."""
        if hasattr(exception, 'context') and isinstance(exception.context, dict):
            # Extract relevant details from exception context
            details_parts = []
            for key, value in exception.context.items():
                if key not in ['exception_type']:
                    details_parts.append(f"{key}: {value}")
            if details_parts:
                return "; ".join(details_parts)
        
        return None

