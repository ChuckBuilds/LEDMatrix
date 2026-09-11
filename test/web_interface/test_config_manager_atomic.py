"""
Tests for atomic configuration save functionality.
"""

import unittest
import tempfile
import shutil
import json
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import src.config_manager_atomic as atomic_module
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


class TestBackupVersionsAreUnique(unittest.TestCase):
    """
    A backup's version is its identity: save_config_atomic() hands the path
    back, rollback_config(backup_version=...) looks it up, and the paired
    secrets backup is found by reusing the string. Two saves in the same second
    used to produce the same filename, so the second overwrote the first and a
    rollback to the earlier version restored the later content.
    """

    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp())
        self.config_path = self.temp_dir / "config.json"
        self.backup_dir = self.temp_dir / "backups"
        self.config_path.write_text(json.dumps({"duration": 15}))
        self.manager = AtomicConfigManager(
            config_path=str(self.config_path),
            backup_dir=str(self.backup_dir),
            max_backups=10,
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def _version_of(self, backup_path):
        return Path(backup_path).name.split('.backup.', 1)[-1]

    def test_two_backups_in_the_same_second_are_two_files(self):
        first = self.manager._create_backup()
        self.config_path.write_text(json.dumps({"duration": 99}))
        second = self.manager._create_backup()

        self.assertNotEqual(first, second)
        self.assertEqual(2, len(list(self.backup_dir.glob("config.json.backup.*"))))

    def test_a_later_backup_does_not_overwrite_an_earlier_one(self):
        first = self.manager._create_backup()
        self.config_path.write_text(json.dumps({"duration": 99}))
        self.manager._create_backup()

        # The path the caller is still holding must hold what was backed up.
        self.assertEqual({"duration": 15}, json.loads(Path(first).read_text()))

    def test_rollback_to_an_earlier_version_restores_that_version(self):
        result1 = self.manager.save_config_atomic({"duration": 45}, create_backup=True)
        self.manager.save_config_atomic({"duration": 20}, create_backup=True)

        # result1's backup was taken before that save, so it holds duration 15.
        self.assertTrue(
            self.manager.rollback_config(
                backup_version=self._version_of(result1.backup_path)
            )
        )
        self.assertEqual({"duration": 15}, json.loads(self.config_path.read_text()))

    def test_the_reported_version_is_the_one_in_the_filename(self):
        # rollback_config() matches on this string, so it has to be the name on
        # disk and not a restamp of the file's mtime.
        self.manager._create_backup()
        self.config_path.write_text(json.dumps({"duration": 99}))
        self.manager._create_backup()

        backups = self.manager.list_backups()
        self.assertEqual(2, len(backups))
        for backup in backups:
            self.assertEqual(self._version_of(backup.path), backup.version)
        self.assertEqual(2, len({b.version for b in backups}))

    def test_backups_are_ordered_newest_first_within_the_same_second(self):
        older = self.manager._create_backup()
        newer = self.manager._create_backup()

        listed = [b.path for b in self.manager.list_backups()]
        self.assertEqual([newer, older], listed)

    def test_a_legacy_second_granularity_backup_is_still_restorable(self):
        # Backups written before the version gained microseconds must stay
        # usable, or an upgrade silently strips a rig's restore points.
        legacy = self.backup_dir / "config.json.backup.20240101_120000"
        legacy.write_text(json.dumps({"duration": 7}))

        versions = {b.version for b in self.manager.list_backups()}
        self.assertIn("20240101_120000", versions)

        self.assertTrue(self.manager.rollback_config(backup_version="20240101_120000"))
        self.assertEqual({"duration": 7}, json.loads(self.config_path.read_text()))

    def test_an_unrecognized_backup_name_is_still_listed_and_restorable(self):
        # Hand-copied or renamed files get ordered by mtime, but keep the
        # version string they have on disk so they can still be named.
        odd = self.backup_dir / "config.json.backup.hand-copied"
        odd.write_text(json.dumps({"duration": 3}))

        self.assertIn("hand-copied", {b.version for b in self.manager.list_backups()})
        self.assertTrue(self.manager.rollback_config(backup_version="hand-copied"))
        self.assertEqual({"duration": 3}, json.loads(self.config_path.read_text()))

    def test_a_collision_suffix_never_costs_a_restore_point(self):
        # Freeze the clock so every backup wants the identical filename.
        frozen = datetime(2026, 1, 2, 3, 4, 5, 678901)

        class FrozenDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return frozen

        paths = []
        with patch.object(atomic_module, 'datetime', FrozenDatetime):
            for i in range(3):
                self.config_path.write_text(json.dumps({"duration": i}))
                paths.append(self.manager._create_backup())

        self.assertEqual(3, len(set(paths)))
        self.assertEqual(
            [0, 1, 2],
            [json.loads(Path(p).read_text())["duration"] for p in paths],
        )


if __name__ == '__main__':
    unittest.main()

