"""
Every write of config.json / config_secrets.json goes through one durable
writer, atomic_write_text() in src/config_manager_atomic.py: temp file in the
same directory, fsync, rename, directory fsync.

These pin what that buys on a device that loses power mid-save (the old file
survives intact), what it costs the SD card (the unchanged secrets file isn't
rewritten, backup rotation doesn't open every backup), and that the backups
it keeps are still the five config.json.backup.<version> files anything
restoring from them expects.
"""

import json
import os
import re
import stat
import threading
from pathlib import Path

import pytest

import src.config_manager_atomic as atomic_module
from src.config_manager import ConfigManager
from src.config_manager_atomic import AtomicConfigManager, SaveResultStatus, atomic_write_text
from src.exceptions import ConfigError

ORIGINAL = {"timezone": "America/Chicago", "display": {"hardware": {"rows": 32}}}

POSIX_ONLY = pytest.mark.skipif(os.name == 'nt', reason="POSIX file modes and directory fsync")


def make_manager(tmp_path, secrets=None):
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps(ORIGINAL, indent=4))
    secrets_file = tmp_path / "config_secrets.json"
    if secrets is not None:
        secrets_file.write_text(json.dumps(secrets, indent=4))
    manager = ConfigManager(config_path=str(config_file), secrets_path=str(secrets_file))
    manager.template_path = str(tmp_path / "no-template.json")
    return manager


def temp_leftovers(directory):
    return [p.name for p in Path(directory).iterdir() if '.tmp.' in p.name]


class TestPowerCutLeavesTheOldConfig:
    """A save that dies before the rename must leave config.json exactly as it was."""

    def test_save_config_that_fails_midway_keeps_the_old_file(self, tmp_path):
        # json.dump streams: an unencodable value deep in the dict used to
        # leave config.json truncated at the point the encoder gave up.
        manager = make_manager(tmp_path)
        before = (tmp_path / "config.json").read_bytes()

        with pytest.raises(ConfigError):
            manager.save_config({"timezone": "UTC", "zzz": object()})

        assert (tmp_path / "config.json").read_bytes() == before
        assert temp_leftovers(tmp_path) == []

    def test_save_config_that_dies_before_the_rename_keeps_the_old_file(self, tmp_path, monkeypatch):
        manager = make_manager(tmp_path)
        before = (tmp_path / "config.json").read_bytes()

        def power_cut(*args, **kwargs):
            raise OSError("power lost")

        monkeypatch.setattr(atomic_module.os, "replace", power_cut)

        with pytest.raises(ConfigError):
            manager.save_config({"timezone": "UTC"})

        assert (tmp_path / "config.json").read_bytes() == before
        assert temp_leftovers(tmp_path) == []

    def test_save_raw_file_content_that_dies_before_the_rename_keeps_the_old_file(self, tmp_path, monkeypatch):
        manager = make_manager(tmp_path)
        before = (tmp_path / "config.json").read_bytes()

        def power_cut(*args, **kwargs):
            raise OSError("power lost")

        monkeypatch.setattr(atomic_module.os, "replace", power_cut)

        with pytest.raises(ConfigError):
            manager.save_raw_file_content("main", {"timezone": "UTC"})

        assert (tmp_path / "config.json").read_bytes() == before
        assert temp_leftovers(tmp_path) == []

    def test_save_config_atomic_that_dies_before_the_rename_keeps_the_old_file(self, tmp_path, monkeypatch):
        manager = make_manager(tmp_path)
        before = json.loads((tmp_path / "config.json").read_text())

        real_replace = os.replace

        def power_cut(src, dst, *args, **kwargs):
            if Path(dst).name == "config.json":
                raise OSError("power lost")
            return real_replace(src, dst, *args, **kwargs)

        monkeypatch.setattr(atomic_module.os, "replace", power_cut)

        result = manager.save_config_atomic({"timezone": "UTC"}, create_backup=False)

        assert result.status == SaveResultStatus.FAILED
        assert json.loads((tmp_path / "config.json").read_text()) == before
        assert temp_leftovers(tmp_path) == []


class TestTheDataIsOnDiskBeforeTheRename:
    def test_the_temp_file_is_fsynced_before_it_replaces_the_config(self, tmp_path, monkeypatch):
        events = []
        real_fsync, real_replace = os.fsync, os.replace

        def fsync(fd):
            events.append("fsync")
            return real_fsync(fd)

        def replace(src, dst, *args, **kwargs):
            events.append("replace")
            return real_replace(src, dst, *args, **kwargs)

        monkeypatch.setattr(atomic_module.os, "fsync", fsync)
        monkeypatch.setattr(atomic_module.os, "replace", replace)

        make_manager(tmp_path).save_config({"timezone": "UTC"})

        assert "replace" in events
        assert "fsync" in events[:events.index("replace")]
        assert json.loads((tmp_path / "config.json").read_text()) == {"timezone": "UTC"}

    @POSIX_ONLY
    def test_the_directory_is_fsynced_after_the_rename(self, tmp_path, monkeypatch):
        events = []
        real_fsync, real_replace, real_open = os.fsync, os.replace, os.open
        directory_fds = set()

        def open_(path, flags, *args, **kwargs):
            fd = real_open(path, flags, *args, **kwargs)
            if Path(path) == tmp_path:
                directory_fds.add(fd)
            return fd

        def fsync(fd):
            events.append("dir-fsync" if fd in directory_fds else "fsync")
            return real_fsync(fd)

        def replace(src, dst, *args, **kwargs):
            events.append("replace")
            return real_replace(src, dst, *args, **kwargs)

        monkeypatch.setattr(atomic_module.os, "open", open_)
        monkeypatch.setattr(atomic_module.os, "fsync", fsync)
        monkeypatch.setattr(atomic_module.os, "replace", replace)

        atomic_write_text(tmp_path / "config.json", "{}")

        assert events == ["fsync", "replace", "dir-fsync"]


class TestPermissionsSurviveTheRename:
    @POSIX_ONLY
    def test_config_and_secrets_get_their_shared_modes_before_the_rename(self, tmp_path, monkeypatch):
        # mkstemp creates 0600. If the chmod came after the rename, the other
        # service could open the new file in between and be refused.
        seen = {}
        real_replace = os.replace

        def replace(src, dst, *args, **kwargs):
            seen[Path(dst).name] = stat.S_IMODE(os.stat(src).st_mode)
            return real_replace(src, dst, *args, **kwargs)

        monkeypatch.setattr(atomic_module.os, "replace", replace)

        manager = make_manager(tmp_path, secrets={"weather": {"api_key": "k"}})
        manager.save_raw_file_content("main", {"timezone": "UTC"})
        manager.save_raw_file_content("secrets", {"weather": {"api_key": "new"}})

        assert seen == {"config.json": 0o644, "config_secrets.json": 0o640}
        assert stat.S_IMODE(os.stat(tmp_path / "config.json").st_mode) == 0o644
        assert stat.S_IMODE(os.stat(tmp_path / "config_secrets.json").st_mode) == 0o640

    @POSIX_ONLY
    def test_a_directory_named_secrets_does_not_lock_down_config_json(self, tmp_path):
        # get_config_file_mode() looks for "secrets" anywhere in the string it
        # is given; handed the full path, an install under e.g.
        # ~/secrets-lab/LEDMatrix made config.json 0o640.
        directory = tmp_path / "secrets-lab"
        directory.mkdir()

        atomic_write_text(directory / "config.json", "{}")

        assert stat.S_IMODE(os.stat(directory / "config.json").st_mode) == 0o644

    def test_a_root_save_hands_the_file_back_to_its_previous_owner(self, tmp_path, monkeypatch):
        # The display service runs as root. A rename gives the file to the
        # writer, so without this a root save would leave config.json owned by
        # root and the web user could only ever replace it, never edit it.
        target = tmp_path / "config.json"
        target.write_text("{}")
        previous = target.stat()
        chowns = []

        monkeypatch.setattr(atomic_module.os, "geteuid", lambda: 0, raising=False)
        monkeypatch.setattr(atomic_module.os, "chown",
                            lambda path, uid, gid: chowns.append((Path(path), uid, gid)),
                            raising=False)
        monkeypatch.setattr("src.common.permission_utils.get_shared_group_gid", lambda: None)

        atomic_write_text(target, '{"timezone": "UTC"}')

        assert len(chowns) == 1
        temp_path, uid, gid = chowns[0]
        assert temp_path.parent == tmp_path and temp_path.name.startswith(".config.json.tmp.")
        assert (uid, gid) == (previous.st_uid, previous.st_gid)

    def test_a_non_root_save_never_tries_to_chown(self, tmp_path, monkeypatch):
        target = tmp_path / "config.json"
        target.write_text("{}")
        chowns = []

        monkeypatch.setattr(atomic_module.os, "geteuid", lambda: 1000, raising=False)
        monkeypatch.setattr(atomic_module.os, "chown",
                            lambda *args: chowns.append(args), raising=False)

        atomic_write_text(target, '{"timezone": "UTC"}')

        assert chowns == []


@pytest.mark.skipif(os.name != 'nt', reason="only Windows refuses to rename over an open file")
class TestWindowsReaderHoldingTheFile:
    def test_a_save_waits_out_a_reader_instead_of_failing(self, tmp_path):
        target = tmp_path / "config.json"
        target.write_text("{}")
        reader = open(target)
        closer = threading.Timer(0.15, reader.close)
        closer.start()
        try:
            atomic_write_text(target, '{"timezone": "UTC"}')
        finally:
            closer.join()
            reader.close()
        assert json.loads(target.read_text()) == {"timezone": "UTC"}


class TestSecretsAreOnlyRewrittenWhenTheyChange:
    @pytest.fixture
    def writes(self, monkeypatch):
        written = []
        real = atomic_module.atomic_write_text

        def spy(path, text, mode=None):
            written.append(Path(path).name)
            return real(path, text, mode)

        monkeypatch.setattr(atomic_module, "atomic_write_text", spy)
        return written

    def test_config_manager_save_leaves_the_secrets_file_alone(self, tmp_path, writes):
        secrets = {"weather": {"api_key": "k"}}
        manager = make_manager(tmp_path, secrets=secrets)

        config = manager.load_config()
        config["timezone"] = "UTC"
        result = manager.save_config_atomic(config)

        assert result.status == SaveResultStatus.SUCCESS
        assert writes == ["config.json"]
        assert json.loads((tmp_path / "config_secrets.json").read_text()) == secrets
        on_disk = json.loads((tmp_path / "config.json").read_text())
        assert on_disk["timezone"] == "UTC" and "api_key" not in on_disk.get("weather", {})

    def test_identical_secrets_are_not_rewritten(self, tmp_path, writes):
        secrets = {"weather": {"api_key": "k"}}
        make_manager(tmp_path, secrets=secrets)
        manager = AtomicConfigManager(str(tmp_path / "config.json"),
                                      str(tmp_path / "config_secrets.json"))

        manager.save_config_atomic({"timezone": "UTC"}, new_secrets=dict(secrets))

        assert writes == ["config.json"]

    def test_changed_secrets_are_written(self, tmp_path, writes):
        make_manager(tmp_path, secrets={"weather": {"api_key": "old"}})
        manager = AtomicConfigManager(str(tmp_path / "config.json"),
                                      str(tmp_path / "config_secrets.json"))

        manager.save_config_atomic({"timezone": "UTC"}, new_secrets={"weather": {"api_key": "new"}})

        assert writes == ["config.json", "config_secrets.json"]
        assert json.loads((tmp_path / "config_secrets.json").read_text()) == {"weather": {"api_key": "new"}}


class TestBackupRotation:
    VERSION = re.compile(r"^\d{8}_\d{6}_\d{6}(-\d+)?$")

    def test_five_newest_backups_are_kept_under_the_same_names(self, tmp_path):
        manager = make_manager(tmp_path, secrets={"weather": {"api_key": "k"}})
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        for version in ("20240101_120000", "20240102_120000"):
            (backup_dir / f"config.json.backup.{version}").write_text("{}")
            (backup_dir / f"config_secrets.json.backup.{version}").write_text("{}")

        created = []
        for i in range(6):
            result = manager.save_config_atomic({"timezone": f"tz{i}"})
            assert result.status == SaveResultStatus.SUCCESS
            created.append(Path(result.backup_path).name)

        config_backups = sorted(p.name for p in backup_dir.glob("config.json.backup.*"))
        secrets_backups = sorted(p.name for p in backup_dir.glob("config_secrets.json.backup.*"))

        assert config_backups == sorted(created[-5:])
        for name in config_backups:
            assert self.VERSION.match(name[len("config.json.backup."):])
        assert secrets_backups == sorted(
            n.replace("config.json.backup.", "config_secrets.json.backup.") for n in created[-5:]
        )
        assert [b.path for b in manager.list_backups()] == [
            str(backup_dir / n) for n in reversed(created[-5:])
        ]

    def test_rotation_does_not_open_the_backups(self, tmp_path, monkeypatch):
        manager = make_manager(tmp_path)
        for _ in range(3):
            manager.save_config_atomic({"timezone": "UTC"})

        parsed = []
        monkeypatch.setattr(AtomicConfigManager, "_validate_config_file",
                            lambda self, path: parsed.append(path))

        result = manager.save_config_atomic({"timezone": "UTC"})
        assert result.status == SaveResultStatus.SUCCESS
        assert parsed == []

    def test_a_rollback_still_restores_from_a_rotated_backup(self, tmp_path):
        manager = make_manager(tmp_path)
        for i in range(7):
            manager.save_config_atomic({"timezone": f"tz{i}"})

        assert manager.rollback_config()
        # The newest backup was taken just before the last save.
        assert json.loads((tmp_path / "config.json").read_text()) == {"timezone": "tz5"}
