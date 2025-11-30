"""
Plugin operation queue manager.

Serializes plugin operations to prevent conflicts and provides
status tracking and cancellation support.
"""

import threading
import queue
import time
from typing import Dict, Optional, List, Callable, Any
from datetime import datetime, timedelta
from pathlib import Path
import json

from src.plugin_system.operation_types import (
    PluginOperation, OperationType, OperationStatus
)
from src.logging_config import get_logger


class PluginOperationQueue:
    """
    Manages a queue of plugin operations, executing them serially
    to prevent conflicts.
    
    Features:
    - Serialized execution (one operation at a time)
    - Prevents concurrent operations on same plugin
    - Operation status tracking
    - Operation cancellation
    - Operation history
    """
    
    def __init__(
        self,
        history_file: Optional[str] = None,
        max_history: int = 100
    ):
        """
        Initialize operation queue.
        
        Args:
            history_file: Optional path to file for persisting operation history
            max_history: Maximum number of operations to keep in history
        """
        self.logger = get_logger(__name__)
        self.history_file = Path(history_file) if history_file else None
        self.max_history = max_history
        
        # Operation tracking
        self._operations: Dict[str, PluginOperation] = {}
        self._operation_queue: queue.Queue = queue.Queue()
        self._active_operations: Dict[str, PluginOperation] = {}  # plugin_id -> operation
        self._operation_history: List[PluginOperation] = []
        
        # Threading
        self._lock = threading.RLock()
        self._worker_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        
        # Load history from file if it exists
        if self.history_file and self.history_file.exists():
            self._load_history()
        
        # Start worker thread
        self._start_worker()
    
    def enqueue_operation(
        self,
        operation_type: OperationType,
        plugin_id: str,
        parameters: Optional[Dict] = None,
        operation_callback: Optional[Callable[[PluginOperation], Dict[str, Any]]] = None
    ) -> str:
        """
        Enqueue a plugin operation.
        
        Args:
            operation_type: Type of operation to perform
            plugin_id: Plugin identifier
            parameters: Optional operation parameters
            operation_callback: Optional callback function to execute the operation.
                             If None, operation will be queued but not executed.
        
        Returns:
            Operation ID for tracking
        """
        with self._lock:
            # Check if plugin already has an active operation
            if plugin_id in self._active_operations:
                active_op = self._active_operations[plugin_id]
                if active_op.status in [OperationStatus.PENDING, OperationStatus.RUNNING]:
                    raise ValueError(
                        f"Plugin {plugin_id} already has an active operation: "
                        f"{active_op.operation_id} ({active_op.operation_type.value})"
                    )
            
            # Create operation
            operation = PluginOperation(
                operation_type=operation_type,
                plugin_id=plugin_id,
                parameters=parameters or {},
            )
            
            # Store callback if provided
            if operation_callback:
                operation.parameters['_callback'] = operation_callback
            
            # Store operation
            self._operations[operation.operation_id] = operation
            
            # Enqueue
            self._operation_queue.put(operation)
            self.logger.info(
                f"Enqueued {operation_type.value} operation for plugin {plugin_id} "
                f"(operation_id: {operation.operation_id})"
            )
            
            return operation.operation_id
    
    def get_operation_status(self, operation_id: str) -> Optional[PluginOperation]:
        """
        Get status of an operation.
        
        Args:
            operation_id: Operation identifier
        
        Returns:
            PluginOperation if found, None otherwise
        """
        with self._lock:
            return self._operations.get(operation_id)
    
    def cancel_operation(self, operation_id: str) -> bool:
        """
        Cancel a pending operation.
        
        Args:
            operation_id: Operation identifier
        
        Returns:
            True if operation was cancelled, False if not found or already running
        """
        with self._lock:
            operation = self._operations.get(operation_id)
            if not operation:
                return False
            
            if operation.status == OperationStatus.RUNNING:
                self.logger.warning(
                    f"Cannot cancel running operation {operation_id}"
                )
                return False
            
            if operation.status == OperationStatus.PENDING:
                operation.status = OperationStatus.CANCELLED
                operation.completed_at = datetime.now()
                operation.message = "Operation cancelled by user"
                self._add_to_history(operation)
                self.logger.info(f"Cancelled operation {operation_id}")
                return True
            
            return False
    
    def get_operation_history(self, limit: int = 50) -> List[PluginOperation]:
        """
        Get operation history.
        
        Args:
            limit: Maximum number of operations to return
        
        Returns:
            List of operations, sorted by creation time (newest first)
        """
        with self._lock:
            # Sort by creation time (newest first)
            history = sorted(
                self._operation_history,
                key=lambda op: op.created_at,
                reverse=True
            )
            return history[:limit]
    
    def get_active_operations(self) -> List[PluginOperation]:
        """
        Get all currently active operations (pending or running).
        
        Returns:
            List of active operations
        """
        with self._lock:
            active = []
            for operation in self._operations.values():
                if operation.status in [OperationStatus.PENDING, OperationStatus.RUNNING]:
                    active.append(operation)
            return active
    
    def _start_worker(self) -> None:
        """Start the worker thread that processes operations."""
        if self._worker_thread and self._worker_thread.is_alive():
            return
        
        self._stop_event.clear()
        self._worker_thread = threading.Thread(
            target=self._worker_loop,
            daemon=True,
            name="PluginOperationQueueWorker"
        )
        self._worker_thread.start()
        self.logger.info("Started plugin operation queue worker thread")
    
    def _worker_loop(self) -> None:
        """Worker thread loop that processes queued operations."""
        while not self._stop_event.is_set():
            try:
                # Get next operation (with timeout to allow checking stop event)
                try:
                    operation = self._operation_queue.get(timeout=1.0)
                except queue.Empty:
                    continue
                
                # Check if operation was cancelled
                if operation.status == OperationStatus.CANCELLED:
                    self._operation_queue.task_done()
                    continue
                
                # Execute operation
                self._execute_operation(operation)
                
                self._operation_queue.task_done()
                
            except Exception as e:
                self.logger.error(f"Error in operation queue worker: {e}", exc_info=True)
    
    def _execute_operation(self, operation: PluginOperation) -> None:
        """
        Execute a plugin operation.
        
        Args:
            operation: Operation to execute
        """
        with self._lock:
            # Check if plugin already has active operation
            if operation.plugin_id in self._active_operations:
                active_op = self._active_operations[operation.plugin_id]
                if active_op.operation_id != operation.operation_id:
                    # Different operation for same plugin - mark as failed
                    operation.status = OperationStatus.FAILED
                    operation.error = f"Plugin {operation.plugin_id} has another active operation"
                    operation.completed_at = datetime.now()
                    self._add_to_history(operation)
                    return
            
            # Mark as running
            operation.status = OperationStatus.RUNNING
            operation.started_at = datetime.now()
            operation.progress = 0.0
            self._active_operations[operation.plugin_id] = operation
        
        try:
            self.logger.info(
                f"Executing {operation.operation_type.value} operation for "
                f"plugin {operation.plugin_id} (operation_id: {operation.operation_id})"
            )
            
            # Get callback from parameters
            callback = operation.parameters.pop('_callback', None)
            
            if callback:
                # Execute callback
                operation.progress = 0.1
                result = callback(operation)
                
                # Update operation with result
                operation.progress = 1.0
                operation.status = OperationStatus.COMPLETED
                operation.result = result
                operation.message = result.get('message', 'Operation completed successfully')
                
            else:
                # No callback - mark as completed (operation was just queued)
                operation.progress = 1.0
                operation.status = OperationStatus.COMPLETED
                operation.message = "Operation queued (no callback provided)"
            
        except Exception as e:
            self.logger.error(
                f"Error executing operation {operation.operation_id}: {e}",
                exc_info=True
            )
            operation.status = OperationStatus.FAILED
            operation.error = str(e)
            operation.message = f"Operation failed: {str(e)}"
        
        finally:
            with self._lock:
                operation.completed_at = datetime.now()
                
                # Remove from active operations
                if operation.plugin_id in self._active_operations:
                    if self._active_operations[operation.plugin_id].operation_id == operation.operation_id:
                        del self._active_operations[operation.plugin_id]
                
                # Add to history
                self._add_to_history(operation)
                
                # Save history to file
                self._save_history()
    
    def _add_to_history(self, operation: PluginOperation) -> None:
        """Add operation to history, maintaining max_history limit."""
        self._operation_history.append(operation)
        
        # Trim history if needed
        if len(self._operation_history) > self.max_history:
            # Remove oldest operations
            self._operation_history.sort(key=lambda op: op.created_at)
            self._operation_history = self._operation_history[-self.max_history:]
    
    def _save_history(self) -> None:
        """Save operation history to file."""
        if not self.history_file:
            return
        
        try:
            with self._lock:
                # Convert operations to dicts
                history_data = [op.to_dict() for op in self._operation_history]
            
            # Ensure directory exists
            self.history_file.parent.mkdir(parents=True, exist_ok=True)
            
            # Write to file
            with open(self.history_file, 'w') as f:
                json.dump(history_data, f, indent=2)
            
        except Exception as e:
            self.logger.warning(f"Error saving operation history: {e}")
    
    def _load_history(self) -> None:
        """Load operation history from file."""
        if not self.history_file or not self.history_file.exists():
            return
        
        try:
            with open(self.history_file, 'r') as f:
                history_data = json.load(f)
            
            with self._lock:
                self._operation_history = [
                    PluginOperation.from_dict(op_data)
                    for op_data in history_data
                ]
            
            self.logger.info(f"Loaded {len(self._operation_history)} operations from history")
            
        except Exception as e:
            self.logger.warning(f"Error loading operation history: {e}")
    
    def shutdown(self) -> None:
        """Shutdown the operation queue and worker thread."""
        self.logger.info("Shutting down plugin operation queue")
        self._stop_event.set()
        
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=5.0)
        
        # Save history one last time
        self._save_history()

