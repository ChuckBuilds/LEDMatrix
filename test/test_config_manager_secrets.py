"""
Tests for the ConfigManager secrets round-trip and the load_config fast path.

The contract under test: config_secrets.json values are deep-merged INTO the
in-memory config at load time, and stripped back OUT before anything is
written to config.json — so secrets live in exactly one file on disk. This
suite pins that round-trip plus its known sharp edges (some marked as
SUSPECTED BUG and characterized rather than fixed).

Complements test_config_manager.py, which covers loading/migration/validation.
"""

import json
import os

import pytest

from src.config_manager import ConfigManager


def make_manager(tmp_path, config=None, secrets=None):
    """A ConfigManager over tmp_path files, template migration neutralized."""
    config_file = tmp_path / "config.json"
    secrets_file = tmp_path / "config_secrets.json"
    config_file.write_text(json.dumps(config if config is not None else {}))
    if secrets is not None:
        secrets_file.write_text(json.dumps(secrets))
    manager = ConfigManager(config_path=str(config_file),
                            secrets_path=str(secrets_file))
    # Point the (CWD-relative) template at nothing so migration never runs —
    # these tests assert exact on-disk contents.
    manager.template_path = str(tmp_path / "no-template.json")
    return manager


class TestLoadMergesSecrets:
    def test_secrets_deep_merged_into_config(self, tmp_path):
        manager = make_manager(
            tmp_path,
            config={"weather": {"city": "Austin"}, "timezone": "UTC"},
            secrets={"weather": {"api_key": "s3cret"}},
        )
        loaded = manager.load_config()
        assert loaded["weather"] == {"city": "Austin", "api_key": "s3cret"}
        assert loaded["timezone"] == "UTC"

    def test_secret_scalar_overrides_config_value(self, tmp_path):
        manager = make_manager(
            tmp_path,
            config={"weather": {"api_key": "YOUR_API_KEY"}},
            secrets={"weather": {"api_key": "real-key"}},
        )
        assert manager.load_config()["weather"]["api_key"] == "real-key"

    def test_missing_secrets_file_loads_config_fine(self, tmp_path):
        manager = make_manager(tmp_path, config={"timezone": "UTC"})
        assert manager.load_config() == {"timezone": "UTC"}

    def test_corrupt_secrets_file_loads_config_without_secrets(self, tmp_path):
        manager = make_manager(tmp_path, config={"timezone": "UTC"})
        (tmp_path / "config_secrets.json").write_text("{not json")
        loaded = manager.load_config()
        assert loaded["timezone"] == "UTC"


class TestSaveStripsSecrets:
    def test_round_trip_keeps_secrets_out_of_config_json(self, tmp_path):
        manager = make_manager(
            tmp_path,
            config={"weather": {"city": "Austin"}},
            secrets={"weather": {"api_key": "s3cret"}},
        )
        loaded = manager.load_config()
        assert loaded["weather"]["api_key"] == "s3cret"  # merged in memory

        manager.save_config(loaded)

        on_disk = json.loads((tmp_path / "config.json").read_text())
        assert "api_key" not in on_disk.get("weather", {})
        assert on_disk["weather"]["city"] == "Austin"
        # In-memory config still carries the secret for runtime use.
        assert manager.config["weather"]["api_key"] == "s3cret"

    def test_group_dropped_when_only_secrets_remain(self, tmp_path):
        # _strip_secrets_recursive drops a group entirely when nothing
        # non-secret is left in it.
        manager = make_manager(
            tmp_path,
            config={},
            secrets={"weather": {"api_key": "s3cret"}},
        )
        manager.save_config({"weather": {"api_key": "s3cret"}, "timezone": "UTC"})
        on_disk = json.loads((tmp_path / "config.json").read_text())
        assert on_disk == {"timezone": "UTC"}

    def test_scalar_secret_key_stripped_at_top_level(self, tmp_path):
        manager = make_manager(tmp_path, config={}, secrets={"token": "t"})
        manager.save_config({"token": "t", "timezone": "UTC"})
        on_disk = json.loads((tmp_path / "config.json").read_text())
        assert on_disk == {"timezone": "UTC"}

    def test_unreadable_secrets_file_writes_secrets_to_config_json(self, tmp_path):
        # SUSPECTED BUG (characterized, not fixed): when the secrets file is
        # unreadable/corrupt at save time, save_config proceeds without
        # stripping — writing the merged secrets into config.json in
        # plaintext. The code comments acknowledge the tradeoff (it prevents
        # data loss); this test pins the behavior so any future change to it
        # is deliberate.
        manager = make_manager(
            tmp_path,
            config={"weather": {"city": "Austin"}},
            secrets={"weather": {"api_key": "s3cret"}},
        )
        loaded = manager.load_config()
        (tmp_path / "config_secrets.json").write_text("{corrupt")

        manager.save_config(loaded)

        on_disk = json.loads((tmp_path / "config.json").read_text())
        assert on_disk["weather"].get("api_key") == "s3cret"  # leaked


class TestLoadFastPath:
    def test_unchanged_files_return_cached_dict(self, tmp_path):
        manager = make_manager(tmp_path, config={"timezone": "UTC"})
        first = manager.load_config()
        second = manager.load_config()
        assert second is first  # same aliased dict, no re-read

    def test_touching_secrets_file_invalidates_cache(self, tmp_path):
        manager = make_manager(
            tmp_path,
            config={"weather": {}},
            secrets={"weather": {"api_key": "old"}},
        )
        assert manager.load_config()["weather"]["api_key"] == "old"

        secrets_file = tmp_path / "config_secrets.json"
        secrets_file.write_text(json.dumps({"weather": {"api_key": "new"}}))
        # Force a different mtime_ns in case the write landed within the
        # filesystem's timestamp granularity.
        os.utime(secrets_file, ns=(1, 1))

        assert manager.load_config()["weather"]["api_key"] == "new"

    def test_same_mtime_same_size_change_served_stale(self, tmp_path):
        # Characterized fast-path blind spot: the signature is (mtime_ns,
        # size) only, so a same-length content swap with a forged identical
        # mtime is not detected. Real writes bump mtime_ns, so this is
        # acceptable — but it is a contract worth pinning.
        manager = make_manager(tmp_path, config={"timezone": "AAA"})
        config_file = tmp_path / "config.json"
        os.utime(config_file, ns=(1_000_000_000, 1_000_000_000))
        manager._loaded_sig = None
        first = manager.load_config()
        assert first["timezone"] == "AAA"

        config_file.write_text(json.dumps({"timezone": "BBB"}))  # same length
        os.utime(config_file, ns=(1_000_000_000, 1_000_000_000))

        assert manager.load_config()["timezone"] == "AAA"  # stale, by design
