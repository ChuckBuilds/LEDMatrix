"""
Plugin operation type definitions.

Defines the types of operations that can be performed on plugins
and their associated data structures.
"""

from enum import Enum
from dataclasses import dataclass, field
from typing import Dict, Any, Optional, List
from datetime import datetime
import uuid


class OperationType(Enum):
    """Types of plugin operations."""
    INSTALL = "install"
    UPDATE = "update"
    UNINSTALL = "uninstall"
    ENABLE = "enable"
    DISABLE = "disable"
    CONFIGURE = "configure"


class OperationStatus(Enum):
    """Status of an operation."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


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
        """Convert operation to dictionary for serialization."""
        return {
            'operation_id': self.operation_id,
            'operation_type': self.operation_type.value,
            'plugin_id': self.plugin_id,
            'parameters': self.parameters,
            'status': self.status.value,
            'progress': self.progress,
            'message': self.message,
            'error': self.error,
            'result': self.result,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'started_at': self.started_at.isoformat() if self.started_at else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'PluginOperation':
        """Create operation from dictionary."""
        op = cls(
            operation_type=OperationType(data['operation_type']),
            plugin_id=data['plugin_id'],
            operation_id=data.get('operation_id', str(uuid.uuid4())),
            parameters=data.get('parameters', {}),
            status=OperationStatus(data.get('status', 'pending')),
            progress=data.get('progress', 0.0),
            message=data.get('message', ''),
            error=data.get('error'),
            result=data.get('result'),
        )
        
        # Parse datetime fields
        if data.get('created_at'):
            op.created_at = datetime.fromisoformat(data['created_at'])
        if data.get('started_at'):
            op.started_at = datetime.fromisoformat(data['started_at'])
        if data.get('completed_at'):
            op.completed_at = datetime.fromisoformat(data['completed_at'])
        
        return op

