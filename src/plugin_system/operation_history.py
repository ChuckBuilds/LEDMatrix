"""
Operation history and audit log.

Tracks all plugin operations and configuration changes for debugging and auditing.
"""

import json
import threading
from typing import Dict, Any, List, Optional
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass, asdict

from src.logging_config import get_logger


@dataclass
class OperationRecord:
    """Record of an operation."""
    operation_id: str
    operation_type: str
    plugin_id: Optional[str]
    timestamp: datetime
    status: str
    user: Optional[str] = None
    details: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        result = asdict(self)
        result['timestamp'] = self.timestamp.isoformat()
        return result
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'OperationRecord':
        """Create from dictionary."""
        if isinstance(data.get('timestamp'), str):
            data['timestamp'] = datetime.fromisoformat(data['timestamp'])
        return cls(**data)


class OperationHistory:
    """
    Operation history and audit log manager.
    
    Tracks all plugin operations and configuration changes.
    """
    
    def __init__(
        self,
        history_file: Optional[str] = None,
        max_records: int = 1000,
        lazy_load: bool = False
    ):
        """
        Initialize operation history.
        
        Args:
            history_file: Path to file for persisting history
            max_records: Maximum number of records to keep
            lazy_load: If True, defer loading history file until first access
        """
        self.logger = get_logger(__name__)
        self.history_file = Path(history_file) if history_file else None
        self.max_records = max_records
        self._lazy_load = lazy_load
        self._history_loaded = False
        
        # In-memory history
        self._history: List[OperationRecord] = []
        self._lock = threading.RLock()
        
        # Load history from file if it exists (unless lazy loading)
        if not self._lazy_load and self.history_file and self.history_file.exists():
            self._load_history()
            self._history_loaded = True
    
    def _ensure_loaded(self) -> None:
        """Ensure history is loaded (for lazy loading)."""
        if not self._history_loaded and self.history_file and self.history_file.exists():
            self._load_history()
            self._history_loaded = True
    
    def record_operation(
        self,
        operation_type: str,
        plugin_id: Optional[str] = None,
        status: str = "completed",
        user: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
        operation_id: Optional[str] = None
    ) -> str:
        """
        Record an operation in history.
        
        Args:
            operation_type: Type of operation (install, update, uninstall, etc.)
            plugin_id: Plugin identifier
            status: Operation status
            user: User who performed operation
            details: Optional operation details
            error: Optional error message
            operation_id: Optional operation ID
        
        Returns:
            Operation record ID
        """
        self._ensure_loaded()
        import uuid
        record_id = operation_id or str(uuid.uuid4())
        
        record = OperationRecord(
            operation_id=record_id,
            operation_type=operation_type,
            plugin_id=plugin_id,
            timestamp=datetime.now(),
            status=status,
            user=user,
            details=details,
            error=error
        )
        
        with self._lock:
            self._history.append(record)
            
            # Trim history if needed
            if len(self._history) > self.max_records:
                self._history = self._history[-self.max_records:]
            
            # Save to file
            self._save_history()
        
        return record_id
    
    def get_history(
        self,
        limit: int = 100,
        plugin_id: Optional[str] = None,
        operation_type: Optional[str] = None
    ) -> List[OperationRecord]:
        """
        Get operation history.
        
        Args:
            limit: Maximum number of records to return
            plugin_id: Optional filter by plugin ID
            operation_type: Optional filter by operation type
        
        Returns:
            List of operation records, sorted by timestamp (newest first)
        """
        self._ensure_loaded()
        with self._lock:
            history = self._history.copy()
        
        # Apply filters
        if plugin_id:
            history = [r for r in history if r.plugin_id == plugin_id]
        
        if operation_type:
            history = [r for r in history if r.operation_type == operation_type]
        
        # Sort by timestamp (newest first)
        history.sort(key=lambda r: r.timestamp, reverse=True)
        
        return history[:limit]
    
    def _save_history(self) -> None:
        """Save history to file."""
        if not self.history_file:
            return
        
        try:
            with self._lock:
                history_data = [record.to_dict() for record in self._history]
            
            # Ensure directory exists
            self.history_file.parent.mkdir(parents=True, exist_ok=True)
            
            # Write to file
            with open(self.history_file, 'w') as f:
                json.dump(history_data, f, indent=2)
            
        except Exception as e:
            self.logger.error(f"Error saving operation history: {e}", exc_info=True)
    
    def _load_history(self) -> None:
        """Load history from file."""
        if not self.history_file or not self.history_file.exists():
            return
        
        try:
            with open(self.history_file, 'r') as f:
                history_data = json.load(f)
            
            with self._lock:
                self._history = [
                    OperationRecord.from_dict(record_data)
                    for record_data in history_data
                ]
            
            self.logger.info(f"Loaded {len(self._history)} operation records from file")
            
        except Exception as e:
            self.logger.error(f"Error loading operation history: {e}", exc_info=True)

