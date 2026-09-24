"""
Structured error handling for web interface.

Provides error codes and consistent error response formatting.
"""

from enum import Enum
from typing import Dict, Any, Optional, List
from dataclasses import dataclass


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
    
    Provides consistent error format with error codes, messages, and
    context.
    """
    error_code: ErrorCode
    message: str
    details: Optional[str] = None
    context: Optional[Dict[str, Any]] = None
    suggested_fixes: Optional[List[str]] = None
    original_error: Optional[Exception] = None
    
    def __init__(
        self,
        error_code: ErrorCode,
        message: str,
        details: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
        suggested_fixes: Optional[List[str]] = None,
        original_error: Optional[Exception] = None
    ):
        self.error_code = error_code
        self.message = message
        self.details = details
        self.context = context or {}
        # `is None`, not truthiness: an explicit [] means "this caller has
        # no suggestions to offer", which the default list would override.
        self.suggested_fixes = (
            suggested_fixes if suggested_fixes is not None
            else self._get_default_suggestions(error_code))
        self.original_error = original_error
    
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
        error_code: ErrorCode,
        context: Optional[Dict[str, Any]] = None
    ) -> 'WebInterfaceError':
        """
        Create WebInterfaceError from an exception.
        
        Args:
            exception: Exception to convert
            error_code: The error code to report
            context: Optional additional context
        """
        # Build context
        error_context = context or {}
        error_context['exception_type'] = type(exception).__name__
        
        return cls(
            error_code=error_code,
            message=cls._safe_message(error_code),
            details=cls._get_exception_details(exception),
            context=error_context,
            original_error=exception
        )

    @classmethod
    def _safe_message(cls, error_code: ErrorCode) -> str:
        """Get a safe, user-facing message for an error code."""
        messages = {
            ErrorCode.CONFIG_SAVE_FAILED: "Failed to save configuration",
            ErrorCode.CONFIG_LOAD_FAILED: "Failed to load configuration",
            ErrorCode.CONFIG_VALIDATION_FAILED: "Configuration validation failed",
            ErrorCode.CONFIG_ROLLBACK_FAILED: "Failed to rollback configuration",
            ErrorCode.PLUGIN_NOT_FOUND: "Plugin not found",
            ErrorCode.PLUGIN_INSTALL_FAILED: "Failed to install plugin",
            ErrorCode.PLUGIN_UPDATE_FAILED: "Failed to update plugin",
            ErrorCode.PLUGIN_UNINSTALL_FAILED: "Failed to uninstall plugin",
            ErrorCode.PLUGIN_LOAD_FAILED: "Failed to load plugin",
            ErrorCode.PLUGIN_OPERATION_CONFLICT: "A plugin operation is already in progress",
            ErrorCode.VALIDATION_ERROR: "Validation error",
            ErrorCode.SCHEMA_VALIDATION_FAILED: "Schema validation failed",
            ErrorCode.INVALID_INPUT: "Invalid input",
            ErrorCode.NETWORK_ERROR: "Network error",
            ErrorCode.API_ERROR: "API error",
            ErrorCode.TIMEOUT: "Operation timed out",
            ErrorCode.PERMISSION_DENIED: "Permission denied",
            ErrorCode.FILE_PERMISSION_ERROR: "File permission error",
            ErrorCode.SYSTEM_ERROR: "A system error occurred",
            ErrorCode.SERVICE_UNAVAILABLE: "Service unavailable",
            ErrorCode.UNKNOWN_ERROR: "An unexpected error occurred",
        }
        return messages.get(error_code, "An unexpected error occurred")

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

