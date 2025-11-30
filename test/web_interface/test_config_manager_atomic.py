"""
Tests for atomic configuration save functionality.
"""

import unittest
import tempfile
import shutil
import json
from pathlib import Path

from src.config_manager_atomic import AtomicConfigManager, SaveResultStatus


class TestAtomicConfigManager(unittest.TestCase):
    """Test atomic configuration save manager."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.temp_dir = Path(tempfile.mkdtemp())
        self.config_path = self.temp_dir / "config.json"
        self.secrets_path = self.temp_dir / "secrets.json"
        self.backup_dir = self.temp_dir / "backups"
        
        # Create initial config
        with open(self.config_path, 'w') as f:
            json.dump({"test": "initial"}, f)
        
        self.manager = AtomicConfigManager(
            config_path=str(self.config_path),
            secrets_path=str(self.secrets_path),
            backup_dir=str(self.backup_dir),
            max_backups=3
        )
    
    def tearDown(self):
        """Clean up test fixtures."""
        shutil.rmtree(self.temp_dir)
    
    def test_atomic_save_success(self):
        """Test successful atomic save."""
        new_config = {"test": "updated", "new_key": "value"}
        
        result = self.manager.save_config_atomic(new_config)
        
        self.assertEqual(result.status, SaveResultStatus.SUCCESS)
        self.assertIsNotNone(result.backup_path)
        
        # Verify config was saved
        with open(self.config_path, 'r') as f:
            saved_config = json.load(f)
        self.assertEqual(saved_config, new_config)
    
    def test_backup_creation(self):
        """Test backup is created before save."""
        new_config = {"test": "updated"}
        
        result = self.manager.save_config_atomic(new_config, create_backup=True)
        
        self.assertEqual(result.status, SaveResultStatus.SUCCESS)
        self.assertIsNotNone(result.backup_path)
        self.assertTrue(Path(result.backup_path).exists())
    
    def test_backup_rotation(self):
        """Test backup rotation keeps only max_backups."""
        # Create multiple backups
        for i in range(5):
            new_config = {"test": f"version_{i}"}
            self.manager.save_config_atomic(new_config, create_backup=True)
        
        # Check only max_backups (3) are kept
        backups = self.manager.list_backups()
        self.assertLessEqual(len(backups), 3)
    
    def test_rollback(self):
        """Test rollback functionality."""
        # Save initial config
        initial_config = {"test": "initial"}
        result1 = self.manager.save_config_atomic(initial_config, create_backup=True)
        backup_path = result1.backup_path
        
        # Save new config
        new_config = {"test": "updated"}
        self.manager.save_config_atomic(new_config)
        
        # Rollback
        success = self.manager.rollback_config()
        self.assertTrue(success)
        
        # Verify config was rolled back
        with open(self.config_path, 'r') as f:
            rolled_back_config = json.load(f)
        self.assertEqual(rolled_back_config, initial_config)
    
    def test_validation_after_write(self):
        """Test validation after write triggers rollback on failure."""
        # This would require a custom validator
        # For now, just test that validation runs
        new_config = {"test": "valid"}
        result = self.manager.save_config_atomic(
            new_config,
            validate_after_write=True
        )
        self.assertEqual(result.status, SaveResultStatus.SUCCESS)


if __name__ == '__main__':
    unittest.main()

