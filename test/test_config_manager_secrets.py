"""
Tests for the ConfigManager secrets round-trip and the load_config fast path.

The contract under test: config_secrets.json values are deep-merged INTO the
in-memory config at load time, and stripped back OUT before anything is
written to config.json — so secrets live in exactly one file on disk. This
suite pins that round-trip plus its sharp edges, including the guard that a
save REFUSES (ConfigError) when the secrets file exists but can't be loaded,
rather than leaking merged secrets into config.json in plaintext.

Complements test_config_manager.py, which covers loading/migration/validation.
"""

import json
import os

import pytest

from src.config_manager import ConfigManager
from src.exceptions import ConfigError


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

    def test_corrupt_secrets_file_refuses_save_no_plaintext_leak(self, tmp_path):
        # Regression guard: when the secrets file exists but is corrupt at
        # save time, stripping is impossible — the save must raise instead of
        # writing the merged secrets into config.json in plaintext (the
        # historical behavior).
        manager = make_manager(
            tmp_path,
            config={"weather": {"city": "Austin"}},
            secrets={"weather": {"api_key": "s3cret"}},
        )
        loaded = manager.load_config()
        (tmp_path / "config_secrets.json").write_text("{corrupt")

        with pytest.raises(ConfigError):
            manager.save_config(loaded)

        # On-disk config untouched: no secret leaked.
        on_disk = json.loads((tmp_path / "config.json").read_text())
        assert "api_key" not in on_disk.get("weather", {})

    def test_corrupt_secrets_file_refuses_atomic_save_too(self, tmp_path):
        # Same refusal on the atomic save path, which shared the leak.
        manager = make_manager(
            tmp_path,
            config={"weather": {"city": "Austin"}},
            secrets={"weather": {"api_key": "s3cret"}},
        )
        loaded = manager.load_config()
        (tmp_path / "config_secrets.json").write_text("{corrupt")

        with pytest.raises(ConfigError):
            manager.save_config_atomic(loaded)

        on_disk = json.loads((tmp_path / "config.json").read_text())
        assert "api_key" not in on_disk.get("weather", {})


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


class TestArraySecretStripAndMerge:
    """Array-item secrets round-trip (parallel-placeholder lists).

    secret_helpers.separate_secrets emits array secrets as a list parallel
    to the regular list, with {} for items that carry no secrets. Strip
    must remove the secret fields from config.json while preserving item
    indices; load must merge them back into the right items. The regular
    list's length is authoritative in both directions.
    """

    def test_strip_removes_array_item_secrets_keeps_indices(self, tmp_path):
        manager = make_manager(tmp_path)
        data = {"plugin": {"accounts": [
            {"name": "a", "token": "ta"},
            {"name": "b"},
        ]}}
        secrets = {"plugin": {"accounts": [{"token": "ta"}, {}]}}
        stripped = manager._strip_secrets_recursive(data, secrets)
        assert stripped == {"plugin": {"accounts": [{"name": "a"}, {"name": "b"}]}}

    def test_strip_keeps_all_placeholder_items(self, tmp_path):
        # Even when every item strips to nothing extra, the list survives
        # with its indices — required for merge-on-load alignment.
        manager = make_manager(tmp_path)
        data = {"accounts": [{"token": "t1"}, {"token": "t2"}]}
        secrets = {"accounts": [{"token": "t1"}, {"token": "t2"}]}
        stripped = manager._strip_secrets_recursive(data, secrets)
        assert stripped == {"accounts": [{}, {}]}

    def test_strip_whole_scalar_array_secret_drops_key(self, tmp_path):
        # A list of secret scalars is a whole-key secret, not the parallel
        # shape — the key must vanish from config.json entirely.
        manager = make_manager(tmp_path)
        data = {"recovery_codes": ["a", "b"], "city": "Austin"}
        secrets = {"recovery_codes": ["a", "b"]}
        stripped = manager._strip_secrets_recursive(data, secrets)
        assert stripped == {"city": "Austin"}

    def test_strip_shape_mismatch_drops_key(self, tmp_path):
        # Conservative contract: if the shapes disagree, never leak.
        manager = make_manager(tmp_path)
        data = {"accounts": {"name": "not-a-list"}}
        secrets = {"accounts": [{"token": "t"}]}
        stripped = manager._strip_secrets_recursive(data, secrets)
        assert stripped == {}

    def test_strip_ignores_extra_secrets_entries(self, tmp_path):
        # Regular list length is authoritative: a user deleted an item.
        manager = make_manager(tmp_path)
        data = {"accounts": [{"name": "a", "token": "ta"}]}
        secrets = {"accounts": [{"token": "ta"}, {"token": "tb"}]}
        stripped = manager._strip_secrets_recursive(data, secrets)
        assert stripped == {"accounts": [{"name": "a"}]}

    def test_merge_restores_array_item_secrets(self, tmp_path):
        manager = make_manager(tmp_path)
        target = {"accounts": [{"name": "a"}, {"name": "b"}]}
        manager._deep_merge(target, {"accounts": [{"token": "ta"}, {}]})
        assert target == {"accounts": [
            {"name": "a", "token": "ta"},
            {"name": "b"},
        ]}

    def test_merge_ignores_extra_secrets_entries_with_warning(self, tmp_path, caplog):
        manager = make_manager(tmp_path)
        target = {"accounts": [{"name": "a"}]}
        with caplog.at_level("WARNING"):
            manager._deep_merge(
                target, {"accounts": [{"token": "ta"}, {"token": "ghost"}]})
        assert target == {"accounts": [{"name": "a", "token": "ta"}]}
        assert any("longer than the config list" in r.message for r in caplog.records)

    def test_merge_non_dict_item_replaced_by_secret(self, tmp_path):
        # Shape drift inside the list: the secret wins for that index.
        manager = make_manager(tmp_path)
        target = {"accounts": ["oddball", {"name": "b"}]}
        manager._deep_merge(target, {"accounts": [{"token": "ta"}, {}]})
        assert target == {"accounts": [{"token": "ta"}, {"name": "b"}]}

    def test_merge_whole_scalar_array_still_replaces(self, tmp_path):
        # Legacy behavior preserved: a non-parallel list replaces wholesale.
        manager = make_manager(tmp_path)
        target = {"recovery_codes": ["old"]}
        manager._deep_merge(target, {"recovery_codes": ["new1", "new2"]})
        assert target == {"recovery_codes": ["new1", "new2"]}

    def test_full_save_load_round_trip(self, tmp_path):
        # End to end on real files: save strips array secrets out of
        # config.json; load merges them back into the right items.
        manager = make_manager(
            tmp_path,
            config={"plugin": {"accounts": [
                {"name": "a", "token": "s3cret-a"},
                {"name": "b", "token": "s3cret-b"},
            ]}},
            secrets={"plugin": {"accounts": [
                {"token": "s3cret-a"}, {"token": "s3cret-b"},
            ]}},
        )
        loaded = manager.load_config()
        assert loaded["plugin"]["accounts"][0]["token"] == "s3cret-a"

        manager.save_config(loaded)

        raw = (tmp_path / "config.json").read_text()
        assert "s3cret" not in raw
        on_disk = json.loads(raw)
        assert on_disk["plugin"]["accounts"] == [{"name": "a"}, {"name": "b"}]

        # A fresh manager (constructed directly — make_manager would
        # overwrite the just-saved config.json) re-merges from the secrets
        # file on load.
        fresh = ConfigManager(config_path=str(tmp_path / "config.json"),
                              secrets_path=str(tmp_path / "config_secrets.json"))
        fresh.template_path = str(tmp_path / "no-template.json")
        reloaded = fresh.load_config()
        assert reloaded["plugin"]["accounts"] == [
            {"name": "a", "token": "s3cret-a"},
            {"name": "b", "token": "s3cret-b"},
        ]
