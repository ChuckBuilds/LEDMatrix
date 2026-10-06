"""
Plugin operation type definitions.

Defines the types of operations that can be performed on plugins
and their associated data structures.
"""

from enum import Enum
from dataclasses import dataclass, field
from typing import Dict, Any, Optional
from datetime import datetime
import uuid


class OperationType(Enum):
    """Types of plugin operations."""
    INSTALL = "install"
    UNINSTALL = "uninstall"


class OperationStatus(Enum):
    """Status of an operation."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class PluginOperation:
    """Represents a plugin operation to be executed."""
    operation_type: OperationType
    plugin_id: str
    operation_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    parameters: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.now)
    status: OperationStatus = OperationStatus.PENDING
    progress: float = 0.0  # 0.0 to 1.0
    message: str = ""
    error: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert operation to dictionary for serialization.

        Parameters whose name starts with ``_`` are internal and left out:
        PluginOperationQueue keeps the operation's callback there as
        ``_callback`` until its worker runs it, and a pending operation's
        status answered 500 because that function cannot be serialized.
        """
        return {
            'operation_id': self.operation_id,
            'operation_type': self.operation_type.value,
            'plugin_id': self.plugin_id,
            'parameters': {key: value for key, value in self.parameters.items()
                           if not str(key).startswith('_')},
            'status': self.status.value,
            'progress': self.progress,
            'message': self.message,
            'error': self.error,
            'result': self.result,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'started_at': self.started_at.isoformat() if self.started_at else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
        }
