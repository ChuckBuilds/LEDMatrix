"""SchemaManager.get_schema_path resolves plugin directories like the loader.

It only tried ``<dir>/<plugin_id>``, while the loader (plugin_dirs.py) also
finds a plugin by its manifest ``id`` and under ``ledmatrix-<id>``. A plugin
installed under either of those loaded fine but had no settings form and was
never schema-validated. And every lookup of a plugin with no schema logged a
WARNING, once per call.
"""

import json
import logging

from src.plugin_system.schema_manager import SchemaManager

SCHEMA = {"type": "object", "properties": {"enabled": {"type": "boolean"}}}


def _write_plugin(base, dir_name, plugin_id, schema=True):
    plugin_dir = base / dir_name
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "manifest.json").write_text(json.dumps({"id": plugin_id}))
    if schema:
        (plugin_dir / "config_schema.json").write_text(json.dumps(SCHEMA))
    return plugin_dir


def test_finds_schema_in_ledmatrix_prefixed_dir(tmp_path):
    configured = tmp_path / "plugin-repos"
    plugin_dir = _write_plugin(configured, "ledmatrix-stocks", "stocks")
    sm = SchemaManager(plugins_dir=configured, project_root=tmp_path)
    assert sm.get_schema_path("stocks") == plugin_dir / "config_schema.json"


def test_finds_schema_by_manifest_id(tmp_path):
    configured = tmp_path / "plugin-repos"
    plugin_dir = _write_plugin(configured, "some-other-name", "weather")
    sm = SchemaManager(plugins_dir=configured, project_root=tmp_path)
    assert sm.get_schema_path("weather") == plugin_dir / "config_schema.json"


def test_plugins_dir_still_wins_over_plugin_repos(tmp_path):
    in_plugins = _write_plugin(tmp_path / "plugins", "ledmatrix-dupe", "dupe")
    _write_plugin(tmp_path / "plugin-repos", "dupe", "dupe")
    sm = SchemaManager(plugins_dir=None, project_root=tmp_path)
    assert sm.get_schema_path("dupe") == in_plugins / "config_schema.json"


def test_miss_is_logged_once_at_debug_and_cached(tmp_path, caplog):
    _write_plugin(tmp_path / "plugin-repos", "noschema", "noschema", schema=False)
    sm = SchemaManager(plugins_dir=tmp_path / "plugin-repos", project_root=tmp_path,
                       logger=logging.getLogger("test_schema_path_resolution"))
    with caplog.at_level(logging.DEBUG, logger="test_schema_path_resolution"):
        for _ in range(3):
            assert sm.get_schema_path("noschema") is None
    misses = [r for r in caplog.records if "Schema file not found" in r.getMessage()]
    assert len(misses) == 1
    assert misses[0].levelno == logging.DEBUG


def test_invalidate_cache_forgets_a_miss(tmp_path):
    configured = tmp_path / "plugin-repos"
    plugin_dir = _write_plugin(configured, "later", "later", schema=False)
    sm = SchemaManager(plugins_dir=configured, project_root=tmp_path)
    assert sm.get_schema_path("later") is None

    (plugin_dir / "config_schema.json").write_text(json.dumps(SCHEMA))
    sm.invalidate_cache("later")
    assert sm.get_schema_path("later") == plugin_dir / "config_schema.json"
