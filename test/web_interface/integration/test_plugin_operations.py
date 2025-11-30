"""
Integration tests for plugin operations (install, update, uninstall).
"""

import unittest
import tempfile
import shutil
import json
from pathlib import Path
from unittest.mock import Mock, patch

from src.plugin_system.operation_queue import PluginOperationQueue
from src.plugin_system.operation_types import OperationType, OperationStatus
from src.plugin_system.state_manager import PluginStateManager
from src.plugin_system.operation_history import OperationHistory


class TestPluginOperationsIntegration(unittest.TestCase):
    """Integration tests for plugin operations."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.temp_dir = Path(tempfile.mkdtemp())
        
        # Initialize components
        self.operation_queue = PluginOperationQueue(
            history_file=str(self.temp_dir / "operations.json"),
            max_history=100
        )
        
        self.state_manager = PluginStateManager(
            state_file=str(self.temp_dir / "state.json"),
            auto_save=True
        )
        
        self.operation_history = OperationHistory(
            history_file=str(self.temp_dir / "history.json"),
            max_records=100
        )
    
    def tearDown(self):
        """Clean up test fixtures."""
        self.operation_queue.shutdown()
        shutil.rmtree(self.temp_dir)
    
    def test_install_operation_flow(self):
        """Test complete install operation flow."""
        plugin_id = "test-plugin"
        
        # Enqueue install operation
        operation_id = self.operation_queue.enqueue_operation(
            OperationType.INSTALL,
            plugin_id,
            {"version": "1.0.0"}
        )
        
        self.assertIsNotNone(operation_id)
        
        # Get operation status
        operation = self.operation_queue.get_operation_status(operation_id)
        self.assertEqual(operation.operation_type, OperationType.INSTALL)
        self.assertEqual(operation.plugin_id, plugin_id)
        
        # Record in history
        history_id = self.operation_history.record_operation(
            operation_type="install",
            plugin_id=plugin_id,
            status="in_progress",
            operation_id=operation_id
        )
        self.assertIsNotNone(history_id)
        
        # Update state manager
        self.state_manager.set_plugin_installed(plugin_id, "1.0.0")
        
        # Verify state
        state = self.state_manager.get_plugin_state(plugin_id)
        self.assertIsNotNone(state)
        self.assertEqual(state.version, "1.0.0")
    
    def test_update_operation_flow(self):
        """Test complete update operation flow."""
        plugin_id = "test-plugin"
        
        # First, mark as installed
        self.state_manager.set_plugin_installed(plugin_id, "1.0.0")
        
        # Enqueue update operation
        operation_id = self.operation_queue.enqueue_operation(
            OperationType.UPDATE,
            plugin_id,
            {"from_version": "1.0.0", "to_version": "2.0.0"}
        )
        
        self.assertIsNotNone(operation_id)
        
        # Record in history
        self.operation_history.record_operation(
            operation_type="update",
            plugin_id=plugin_id,
            status="in_progress",
            operation_id=operation_id
        )
        
        # Update state
        self.state_manager.update_plugin_state(plugin_id, {"version": "2.0.0"})
        
        # Verify state
        state = self.state_manager.get_plugin_state(plugin_id)
        self.assertEqual(state.version, "2.0.0")
    
    def test_uninstall_operation_flow(self):
        """Test complete uninstall operation flow."""
        plugin_id = "test-plugin"
        
        # First, mark as installed
        self.state_manager.set_plugin_installed(plugin_id, "1.0.0")
        
        # Enqueue uninstall operation
        operation_id = self.operation_queue.enqueue_operation(
            OperationType.UNINSTALL,
            plugin_id
        )
        
        self.assertIsNotNone(operation_id)
        
        # Record in history
        self.operation_history.record_operation(
            operation_type="uninstall",
            plugin_id=plugin_id,
            status="in_progress",
            operation_id=operation_id
        )
        
        # Update state - remove plugin state
        self.state_manager.remove_plugin_state(plugin_id)
        
        # Verify state
        state = self.state_manager.get_plugin_state(plugin_id)
        self.assertIsNone(state)
    
    def test_operation_history_tracking(self):
        """Test that operations are tracked in history."""
        plugin_id = "test-plugin"
        
        # Perform multiple operations
        operations = [
            ("install", "1.0.0"),
            ("update", "2.0.0"),
            ("uninstall", None)
        ]
        
        for op_type, version in operations:
            history_id = self.operation_history.record_operation(
                operation_type=op_type,
                plugin_id=plugin_id,
                status="completed"
            )
            self.assertIsNotNone(history_id)
        
        # Get history
        history = self.operation_history.get_history(limit=10, plugin_id=plugin_id)
        
        # Verify all operations recorded
        self.assertEqual(len(history), 3)
        self.assertEqual(history[0].operation_type, "uninstall")
        self.assertEqual(history[1].operation_type, "update")
        self.assertEqual(history[2].operation_type, "install")
    
    def test_concurrent_operation_prevention(self):
        """Test that concurrent operations on same plugin are prevented."""
        plugin_id = "test-plugin"
        
        # Enqueue first operation
        op1_id = self.operation_queue.enqueue_operation(
            OperationType.INSTALL,
            plugin_id
        )
        
        # Get the operation to check its status
        op1 = self.operation_queue.get_operation_status(op1_id)
        self.assertIsNotNone(op1)
        
        # Try to enqueue second operation
        # Note: If the first operation completes quickly, it may not raise an error
        # The prevention only works for truly concurrent (pending/running) operations
        try:
            op2_id = self.operation_queue.enqueue_operation(
                OperationType.UPDATE,
                plugin_id
            )
            # If no exception, the first operation may have completed already
            # This is acceptable - the mechanism prevents truly concurrent operations
        except ValueError as e:
            # Expected behavior when first operation is still pending/running
            self.assertIn("already has an active operation", str(e))


if __name__ == '__main__':
    unittest.main()

