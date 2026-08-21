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

import pytest

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


def _plugin(tmp_path, name, body):
    d = tmp_path / name
    d.mkdir()
    (d / "manifest.json").write_text(json.dumps(body))
    return d


VALID = {"name": "V", "version": "1.0.0", "class_name": "X", "display_modes": ["m"]}


@pytest.mark.parametrize("body", [None, [1, 2], "not an object", 42, True])
def test_a_manifest_that_is_not_an_object_is_skipped_not_fatal(tmp_path, caplog, body):
    """json.load accepts any JSON value, not just objects.

    manifest.get('id') then raised AttributeError, which nothing here caught --
    the outer handler takes OSError/PermissionError only. A single malformed
    manifest aborted the entire scan, so every other plugin on disk, however
    healthy, silently failed to register.
    """
    _plugin(tmp_path, "aaa-good", dict(VALID, id="aaa-good"))
    _plugin(tmp_path, "mmm-bad", body)
    _plugin(tmp_path, "zzz-good", dict(VALID, id="zzz-good"))

    pm = _manager(tmp_path)
    with caplog.at_level(logging.WARNING, logger="test.discovery"):
        found = pm._scan_directory_for_plugins(tmp_path)

    assert sorted(found) == ["aaa-good", "zzz-good"], (
        "one unusable manifest took the healthy plugins down with it")
    joined = " ".join(r.message for r in caplog.records)
    assert "mmm-bad" in joined, f"the skip was silent; log said: {joined!r}"


def test_the_bad_manifest_is_named_with_what_it_actually_was(tmp_path, caplog):
    _plugin(tmp_path, "listy", [1, 2])
    pm = _manager(tmp_path)
    with caplog.at_level(logging.WARNING, logger="test.discovery"):
        pm._scan_directory_for_plugins(tmp_path)
    joined = " ".join(r.message for r in caplog.records)
    assert "listy" in joined and "list" in joined, (
        f"the warning does not say what the manifest was: {joined!r}")
