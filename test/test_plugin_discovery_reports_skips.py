#!/usr/bin/env python3
"""Discovery must say when it skips a directory.

A plugin can be enabled in config, enabled in plugin state, present on disk
with a valid entry point -- and simply absent from the running process, with
nothing in the journal to say why. Working that out afterwards meant comparing
cache-file mtimes to find when it had last run.

Two paths were silent. A directory with no manifest.json was ignored, and --
quieter still -- a manifest that parsed but carried no "id" was read
successfully and then dropped on the floor.
"""
import json
import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.plugin_system.plugin_manager import PluginManager  # noqa: E402


def _manager(tmp_path):
    pm = PluginManager.__new__(PluginManager)
    pm.plugins_dir = tmp_path
    pm.logger = logging.getLogger("test.discovery")
    pm.plugin_manifests = {}
    pm.plugin_directories = {}
    pm._discovery_lock = __import__("threading").RLock()
    pm._skip_reported = set()
    pm.schema_manager = MagicMock()
    return pm


def test_a_directory_without_a_manifest_is_reported(tmp_path, caplog):
    (tmp_path / "not-a-plugin").mkdir()
    pm = _manager(tmp_path)
    with caplog.at_level(logging.WARNING, logger="test.discovery"):
        pm._scan_directory_for_plugins(tmp_path)
    joined = " ".join(r.message for r in caplog.records)
    assert "not-a-plugin" in joined and "manifest" in joined, (
        f"skip was silent; log said: {joined!r}")


def test_a_manifest_without_an_id_is_reported(tmp_path, caplog):
    d = tmp_path / "idless"
    d.mkdir()
    (d / "manifest.json").write_text(json.dumps({"name": "No Id", "version": "1.0.0"}))
    pm = _manager(tmp_path)
    with caplog.at_level(logging.WARNING, logger="test.discovery"):
        pm._scan_directory_for_plugins(tmp_path)
    joined = " ".join(r.message for r in caplog.records)
    assert "idless" in joined and "id" in joined, (
        f"a parsed-but-unusable manifest vanished silently; log said: {joined!r}")


def test_a_good_plugin_still_registers(tmp_path, caplog):
    d = tmp_path / "real-plugin"
    d.mkdir()
    (d / "manifest.json").write_text(json.dumps(
        {"id": "real-plugin", "name": "Real", "version": "1.0.0"}))
    pm = _manager(tmp_path)
    pm._scan_directory_for_plugins(tmp_path)
    assert "real-plugin" in pm.plugin_manifests, "a valid plugin was not registered"


def test_the_warning_does_not_repeat_on_every_scan(tmp_path, caplog):
    """Discovery runs on every web UI page load and every config reconcile.

    Warning unconditionally would put a line in the journal each time someone
    opened a page -- the same log-volume problem this is meant to help
    diagnose.
    """
    (tmp_path / "not-a-plugin").mkdir()
    pm = _manager(tmp_path)
    with caplog.at_level(logging.WARNING, logger="test.discovery"):
        for _ in range(5):
            pm._scan_directory_for_plugins(tmp_path)
    hits = [r for r in caplog.records if "not-a-plugin" in r.message]
    assert len(hits) == 1, f"warned {len(hits)} times across 5 scans"
