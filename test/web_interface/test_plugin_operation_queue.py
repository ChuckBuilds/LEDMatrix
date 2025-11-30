"""
Tests for plugin operation queue.
"""

import unittest
import time
from src.plugin_system.operation_queue import PluginOperationQueue
from src.plugin_system.operation_types import OperationType, OperationStatus


class TestPluginOperationQueue(unittest.TestCase):
    """Test plugin operation queue."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.queue = PluginOperationQueue(max_history=10)
    
    def tearDown(self):
        """Clean up test fixtures."""
        self.queue.shutdown()
    
    def test_enqueue_operation(self):
        """Test enqueuing an operation."""
        operation_id = self.queue.enqueue_operation(
            OperationType.INSTALL,
            "test-plugin",
            {"version": "1.0.0"}
        )
        
        self.assertIsNotNone(operation_id)
        
        # Check operation status
        operation = self.queue.get_operation_status(operation_id)
        self.assertIsNotNone(operation)
        self.assertEqual(operation.operation_type, OperationType.INSTALL)
        self.assertEqual(operation.plugin_id, "test-plugin")
    
    def test_prevent_concurrent_operations(self):
        """Test that concurrent operations on same plugin are prevented."""
        # Enqueue first operation
        op1_id = self.queue.enqueue_operation(
            OperationType.INSTALL,
            "test-plugin"
        )
        
        # Try to enqueue second operation for same plugin
        with self.assertRaises(ValueError):
            self.queue.enqueue_operation(
                OperationType.UPDATE,
                "test-plugin"
            )
    
    def test_operation_cancellation(self):
        """Test cancelling a pending operation."""
        operation_id = self.queue.enqueue_operation(
            OperationType.INSTALL,
            "test-plugin"
        )
        
        # Cancel operation
        success = self.queue.cancel_operation(operation_id)
        self.assertTrue(success)
        
        # Check status
        operation = self.queue.get_operation_status(operation_id)
        self.assertEqual(operation.status, OperationStatus.CANCELLED)
    
    def test_operation_history(self):
        """Test operation history tracking."""
        # Enqueue and complete an operation
        operation_id = self.queue.enqueue_operation(
            OperationType.INSTALL,
            "test-plugin",
            operation_callback=lambda op: {"success": True}
        )
        
        # Wait for operation to complete
        time.sleep(0.5)
        
        # Check history
        history = self.queue.get_operation_history(limit=10)
        self.assertGreater(len(history), 0)
        
        # Find our operation in history
        op_in_history = next(
            (op for op in history if op.operation_id == operation_id),
            None
        )
        self.assertIsNotNone(op_in_history)


if __name__ == '__main__':
    unittest.main()

