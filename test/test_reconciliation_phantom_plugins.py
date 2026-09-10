"""Tests for plugin-state reconciliation reporting phantom inconsistencies.

Observed on a device running four installed, configured, working plugins: the
web UI showed "Stale plugin config entries found: football-scoreboard,
odds-ticker, data, ledmatrix-weather, starlark-apps. Remove them from
config.json" for hours. Three separate defects combined to produce that:

1. load_config() merges config_secrets.json into the config it returns, and the
   ignore list named only 'github' and 'youtube'. A 'data' key in the secrets
   file therefore became a phantom plugin, reported as in-config-not-on-disk.

2. The auto-fix for "on disk but not in config" assigned
   ``config[plugin_id] = {'enabled': False}`` unconditionally. When detection was
   wrong, that traded a plugin's real configuration (4.9KB of league settings in
   the reported case) for a stub. It only avoided firing there because the write
   failed with EACCES.

3. The verdict is a snapshot written once per run and served to the UI from a
   status file, and a run that fails to apply a fix also declines to retry. So a
   condition that had since resolved was reported indefinitely.
"""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from src.plugin_system.state_reconciliation import (
    InconsistencyType,
    StateReconciliation,
    still_unresolved,
)


class _ConfigManager:
    """Minimal config manager with the secrets path the real one exposes."""

    def __init__(self, config, secrets_path=None):
        self._config = config
        self._secrets_path = secrets_path
        self.saved = []

    def load_config(self):
        return dict(self._config)

    def save_config(self, config):
        self.saved.append(config)
        self._config = dict(config)

    def get_secrets_path(self):
        return self._secrets_path


def _reconciler(config, plugins_dir, secrets_path=None):
    return StateReconciliation(
        state_manager=Mock(),
        config_manager=_ConfigManager(config, secrets_path),
        plugin_manager=Mock(),
        plugins_dir=plugins_dir,
    )


class TestSecretsKeysAreNotPlugins:
    def test_secrets_key_is_not_treated_as_a_plugin(self, tmp_path):
        secrets = tmp_path / "config_secrets.json"
        # The shape actually found on the device: display state written here.
        secrets.write_text(json.dumps({"data": {"mode": "nfl_recent"},
                                       "timestamp": 1.0}), encoding="utf-8")
        config = {
            "data": {"mode": "nfl_recent"},
            "football-scoreboard": {"enabled": True, "nfl": {"favorite_teams": ["TB"]}},
        }
        state = _reconciler(config, tmp_path, secrets).\
            _get_config_state()

        assert "data" not in state, "secrets key leaked in as a phantom plugin"
        assert "football-scoreboard" in state

    def test_missing_secrets_file_is_survivable(self, tmp_path):
        state = _reconciler({"odds-ticker": {"enabled": True}}, tmp_path,
                            tmp_path / "does-not-exist.json")._get_config_state()
        assert "odds-ticker" in state

    def test_non_path_secrets_location_is_survivable(self, tmp_path):
        """Regression: a non-path return raised TypeError out of the helper,
        which the broad handler in _get_config_state() swallowed as "Error
        reading config state" -- emptying the config state and making every
        downstream detection wrong."""
        r = _reconciler({"odds-ticker": {"enabled": True}}, tmp_path,
                        secrets_path=Mock())
        assert "odds-ticker" in r._get_config_state()

    def test_config_manager_without_secrets_path_is_survivable(self, tmp_path):
        r = StateReconciliation(state_manager=Mock(),
                                config_manager=Mock(spec=["load_config", "save_config"]),
                                plugin_manager=Mock(), plugins_dir=tmp_path)
        r.config_manager.load_config.return_value = {"odds-ticker": {"enabled": True}}
        assert "odds-ticker" in r._get_config_state()


class TestFixNeverClobbersRealConfig:
    def _missing_in_config(self, plugin_id):
        inc = Mock()
        inc.inconsistency_type = InconsistencyType.PLUGIN_MISSING_IN_CONFIG
        inc.plugin_id = plugin_id
        return inc

    def test_existing_entry_is_not_overwritten(self, tmp_path):
        rich = {"enabled": True, "nfl": {"favorite_teams": ["TB"], "show_odds": True}}
        r = _reconciler({"football-scoreboard": dict(rich)}, tmp_path)

        assert r._fix_inconsistency(self._missing_in_config("football-scoreboard")) is True
        # The regression: this wrote {'enabled': False} over the real settings.
        assert r.config_manager.saved == []
        assert r.config_manager.load_config()["football-scoreboard"] == rich

    def test_genuinely_absent_plugin_is_still_added(self, tmp_path):
        r = _reconciler({"other-plugin": {"enabled": True}}, tmp_path)

        assert r._fix_inconsistency(self._missing_in_config("new-plugin")) is True
        assert len(r.config_manager.saved) == 1
        assert r.config_manager.load_config()["new-plugin"] == {"enabled": False}
        # and it must not disturb what was already there
        assert r.config_manager.load_config()["other-plugin"] == {"enabled": True}


class TestStaleFindingsAreDropped:
    IN_CONFIG = InconsistencyType.PLUGIN_MISSING_IN_CONFIG.value
    ON_DISK = InconsistencyType.PLUGIN_MISSING_ON_DISK.value

    def test_resolved_missing_in_config_is_dropped(self):
        entries = [{"plugin_id": "football-scoreboard", "type": self.IN_CONFIG}]
        assert still_unresolved(entries, {"football-scoreboard"}, set()) == []

    def test_genuinely_missing_in_config_is_kept(self):
        entries = [{"plugin_id": "football-scoreboard", "type": self.IN_CONFIG}]
        assert still_unresolved(entries, set(), set()) == entries

    def test_resolved_missing_on_disk_is_dropped(self):
        entries = [{"plugin_id": "odds-ticker", "type": self.ON_DISK}]
        assert still_unresolved(entries, set(), {"odds-ticker"}) == []

    def test_genuinely_missing_on_disk_is_kept(self):
        entries = [{"plugin_id": "data", "type": self.ON_DISK}]
        assert still_unresolved(entries, set(), {"odds-ticker"}) == entries

    def test_unrecheckable_kinds_are_kept(self):
        # Filtering must only ever remove what it can prove stale.
        entries = [{"plugin_id": "x", "type": "plugin_version_mismatch"}]
        assert still_unresolved(entries, set(), set()) == entries

    def test_the_reported_device_state_clears_every_false_finding(self):
        """The exact verdict the device served, against its real state."""
        entries = [
            {"plugin_id": "football-scoreboard", "type": self.IN_CONFIG},
            {"plugin_id": "odds-ticker", "type": self.IN_CONFIG},
            {"plugin_id": "data", "type": self.ON_DISK},
            {"plugin_id": "ledmatrix-weather", "type": self.IN_CONFIG},
            {"plugin_id": "starlark-apps", "type": self.IN_CONFIG},
        ]
        installed = {"football-scoreboard", "ledmatrix-weather", "odds-ticker",
                     "starlark-apps", "web-ui-info"}
        live = still_unresolved(entries, installed, installed)

        # All four "installed but not in config" findings were false and clear.
        # 'data' legitimately is not installed, so this filter keeps it; it stops
        # being reported because _get_config_state() no longer invents it from the
        # secrets file, which takes effect on the next reconciliation run.
        assert [e["plugin_id"] for e in live] == ["data"]
