"""Core settings in config.json must never be reported as orphaned plugins.

Observed on a v3.4.0 device: the web UI showed "Plugin Config Warning -- In
config but not installed: auto_update. Reinstall via the Plugin Store, or
remove these entries from config.json." ``auto_update`` is the top-level core
section #581 added for weekly automatic updates. Reconciliation kept its own
private list of non-plugin keys, which #581 did not know to extend, so any
top-level dict it did not list was treated as a plugin id. Following the
warning's advice deletes a real core setting.

These tests pin the shared list in src/core_config_keys.py to the sources that
actually define top-level keys, so the next new core setting cannot regress it.
"""

import json
import re
import sys
from pathlib import Path
from unittest.mock import MagicMock, Mock

import pytest
from flask import Flask

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src.core_config_keys import CORE_CONFIG_KEYS  # noqa: E402
from src.plugin_system.state_reconciliation import (  # noqa: E402
    InconsistencyType,
    StateReconciliation,
)

ON_DISK = InconsistencyType.PLUGIN_MISSING_ON_DISK
IN_CONFIG = InconsistencyType.PLUGIN_MISSING_IN_CONFIG

TEMPLATE = json.loads((REPO / "config" / "config.template.json").read_text(encoding="utf-8"))
BUNDLED_PLUGINS = {p.parent.name for p in (REPO / "plugin-repos").glob("*/manifest.json")}

# Top-level keys of the hdpi device's config.json (v3.4.0 fddb0e06), keys only.
DEVICE_CONFIG_KEYS = [
    'web_display_autostart', 'schedule', 'dim_schedule', 'timezone', 'target_fps',
    'location', 'display', 'sync', 'plugin_system', 'web-ui-info', 'starlark-apps',
    'football-scoreboard', 'hockey-scoreboard', 'lacrosse-scoreboard', 'afl-scoreboard',
    'nrl-scoreboard', 'soccer-scoreboard', 'baseball-scoreboard', 'ledmatrix-flights',
    'birdnet-go', 'geochron', 'incoming-packages', 'jellyfin-now-playing', 'tide-display',
    'ledmatrix-leaderboard', 'ledmatrix-music', 'odds-ticker', 'stock-news',
    'basketball-scoreboard', 'ledmatrix-stocks', 'ledmatrix-weather', 'news',
    'clock-simple', 'christmas-countdown', '7-segment-clock', 'calendar', 'on-air',
    'static-image', 'ledmatrix-dresden-departures', 'olympics', 'ledmatrix-elections',
    'f1-scoreboard', 'hello-world', 'tidbyt-baseball-scoreboard', 'youtube-stats',
    'mqtt-notifications', 'march-madness', 'pga-tour-leaderboard', 'nfl-draft',
    'pomodoro-timer', 'countdown', 'gif-player', 'of-the-day', 'text-display',
    'masters-tournament', 'cricket-scoreboard', 'ufc-scoreboard', 'auto_update',
]
DEVICE_SECRETS_KEYS = ['github', 'incoming-packages', 'jellyfin-now-playing']
DEVICE_INSTALLED = [k for k in DEVICE_CONFIG_KEYS if k not in CORE_CONFIG_KEYS] + [
    'ledmatrix-flights.standalone-backup-migrating']


def _device_config():
    """Device-shaped config: real key set, placeholder values."""
    cfg = {}
    for key in DEVICE_CONFIG_KEYS:
        template_value = TEMPLATE.get(key)
        cfg[key] = template_value if template_value is not None else {"enabled": True}
    return cfg


class _ConfigManager:
    def __init__(self, config, secrets_path=None):
        self._config = config
        self._secrets_path = secrets_path
        self.saved = []

    def load_config(self):
        return dict(self._config)

    def save_config(self, config):
        self.saved.append(dict(config))
        self._config = dict(config)

    def get_secrets_path(self):
        return self._secrets_path


def _install(plugins_dir, plugin_id):
    d = plugins_dir / plugin_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "manifest.json").write_text(json.dumps({"id": plugin_id, "version": "1.0.0"}),
                                     encoding="utf-8")


def _reconcile(tmp_path, config, installed=(), secrets=None):
    plugins_dir = tmp_path / "plugin-repos"
    plugins_dir.mkdir(exist_ok=True)
    for pid in installed:
        _install(plugins_dir, pid)
    secrets_path = tmp_path / "config_secrets.json"
    secrets_path.write_text(json.dumps(secrets or {}), encoding="utf-8")
    state_manager = Mock()
    state_manager.get_all_states.return_value = {}
    plugin_manager = Mock()
    plugin_manager.plugin_manifests = {}
    reconciler = StateReconciliation(
        state_manager=state_manager,
        config_manager=_ConfigManager(config, str(secrets_path)),
        plugin_manager=plugin_manager,
        plugins_dir=plugins_dir,
    )
    return reconciler, reconciler.reconcile_state()


def _ids(result, kind):
    return sorted(i.plugin_id for i in result.inconsistencies_found
                  if i.inconsistency_type == kind)


class TestTheListCoversEveryCoreKey:
    def test_every_template_top_level_key_is_core_or_a_bundled_plugin(self):
        unaccounted = [k for k in TEMPLATE
                       if k not in CORE_CONFIG_KEYS and k not in BUNDLED_PLUGINS]
        assert unaccounted == [], (
            "config.template.json has top-level keys that are neither in "
            "src/core_config_keys.py nor a plugin under plugin-repos/; "
            "reconciliation will report them as plugins missing from disk")

    def test_the_template_keys_this_bug_was_about_are_covered(self):
        for key in ("auto_update", "sync", "schedule", "dim_schedule", "location",
                    "display", "plugin_system", "timezone", "target_fps",
                    "web_display_autostart"):
            assert key in CORE_CONFIG_KEYS, key

    def test_keys_written_by_the_general_settings_save_are_core(self):
        """A new setting can be added to the save endpoint without touching
        the template; catch that too."""
        source = (REPO / "web_interface" / "blueprints" / "api_v3" / "config.py").read_text(
            encoding="utf-8")
        written = set(re.findall(r"current_config\[['\"]([A-Za-z0-9_-]+)['\"]\]\s*=", source))
        assert "auto_update" in written, "pattern no longer finds the writes; update the test"
        assert written - CORE_CONFIG_KEYS == set()

    def test_no_core_key_is_also_a_bundled_plugin(self):
        assert CORE_CONFIG_KEYS & BUNDLED_PLUGINS == set()


class TestReconciliationIgnoresCoreKeys:
    def test_auto_update_is_not_reported(self, tmp_path):
        _, result = _reconcile(tmp_path, {"auto_update": {"enabled": False}})
        assert "auto_update" not in [i.plugin_id for i in result.inconsistencies_found]

    def test_the_whole_template_yields_only_its_bundled_plugins(self, tmp_path):
        template_plugins = {k for k in TEMPLATE if k in BUNDLED_PLUGINS}
        reconciler, result = _reconcile(tmp_path, dict(TEMPLATE), installed=template_plugins)
        assert result.inconsistencies_found == []
        assert set(reconciler._get_config_state()) == template_plugins

    @pytest.mark.parametrize("key", sorted(CORE_CONFIG_KEYS))
    def test_no_core_key_is_ever_a_plugin_missing_from_disk(self, tmp_path, key):
        _, result = _reconcile(tmp_path, {key: {"enabled": True}})
        assert _ids(result, ON_DISK) == []

    def test_a_genuinely_orphaned_plugin_is_still_reported(self, tmp_path):
        config = {"auto_update": {"enabled": False}, "display": {"hardware": {}},
                  "ghost-plugin": {"enabled": True}, "odds-ticker": {"enabled": True}}
        _, result = _reconcile(tmp_path, config, installed=["odds-ticker"])
        assert _ids(result, ON_DISK) == ["ghost-plugin"]
        assert [i.plugin_id for i in result.inconsistencies_manual] == ["ghost-plugin"]

    def test_the_device_config_reports_nothing(self, tmp_path):
        _, result = _reconcile(
            tmp_path, _device_config(), installed=DEVICE_INSTALLED,
            secrets={k: {"api_key": "x"} for k in DEVICE_SECRETS_KEYS})
        assert result.inconsistencies_found == []
        assert result.reconciliation_successful


class TestPluginSecretsDoNotHideThePlugin:
    """Plugin secrets are namespaced by plugin id in config_secrets.json. The
    device had 'incoming-packages' and 'jellyfin-now-playing' there; ignoring
    every secrets key reported both installed plugins as missing from config on
    every run (auto-"fixed" as a no-op, so it never reached the banner)."""

    def test_installed_plugin_with_secrets_is_a_plugin(self, tmp_path):
        config = {"incoming-packages": {"enabled": True, "api_key": "x"}}
        reconciler, result = _reconcile(tmp_path, config, installed=["incoming-packages"],
                                        secrets={"incoming-packages": {"api_key": "x"}})
        assert result.inconsistencies_found == []
        assert "incoming-packages" in reconciler._get_config_state()

    def test_a_secrets_only_key_is_still_not_a_plugin(self, tmp_path):
        # The #557 phantom: a non-plugin 'data' key in the secrets file.
        _, result = _reconcile(tmp_path, {"data": {"mode": "nfl_recent"}},
                               secrets={"data": {"mode": "nfl_recent"}})
        assert result.inconsistencies_found == []


class TestAPluginNamedLikeACoreKey:
    """A plugin whose id collides with a core key has no config section of its
    own. Reconciliation must neither nag about it nor write a stub over (or
    in place of) the core setting."""

    def test_installed_plugin_named_like_a_core_key_is_not_reported(self, tmp_path):
        config = {"sync": {"role": "standalone"}}
        reconciler, result = _reconcile(tmp_path, config, installed=["sync"])
        assert result.inconsistencies_found == []
        assert reconciler.config_manager.saved == []
        assert reconciler.config_manager.load_config() == config

    def test_core_key_absent_from_config_is_not_stubbed(self, tmp_path):
        reconciler, result = _reconcile(tmp_path, {}, installed=["display"])
        assert result.inconsistencies_found == []
        assert reconciler.config_manager.saved == []

    def test_fix_refuses_to_write_a_core_key(self, tmp_path):
        reconciler, _ = _reconcile(tmp_path, {})
        inc = Mock(inconsistency_type=IN_CONFIG, plugin_id="auto_update")
        assert reconciler._fix_inconsistency(inc) is False
        assert reconciler.config_manager.saved == []


class TestTheStatusEndpoint:
    @pytest.fixture
    def get_status(self, tmp_path, monkeypatch):
        import tempfile
        from web_interface.blueprints.api_v3 import api_v3

        monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))

        def _get(verdict, config, installed=(), secrets=None):
            (tmp_path / "ledmatrix_reconciliation.json").write_text(
                json.dumps(verdict), encoding="utf-8")
            plugins_dir = tmp_path / "plugin-repos"
            plugins_dir.mkdir(exist_ok=True)
            for pid in installed:
                _install(plugins_dir, pid)
            secrets_path = tmp_path / "config_secrets.json"
            secrets_path.write_text(json.dumps(secrets or {}), encoding="utf-8")
            cm = MagicMock()
            cm.load_config.return_value = config
            cm.get_secrets_path.return_value = str(secrets_path)
            pm = MagicMock()
            pm.plugins_dir = str(plugins_dir)
            monkeypatch.setattr(api_v3, "config_manager", cm, raising=False)
            monkeypatch.setattr(api_v3, "plugin_manager", pm, raising=False)
            app = Flask(__name__)
            app.config["TESTING"] = True
            app.register_blueprint(api_v3, url_prefix="/api/v3")
            resp = app.test_client().get("/api/v3/plugins/reconciliation-status")
            return resp.get_json()["data"]

        return _get

    def test_the_verdict_the_device_served_now_clears(self, get_status):
        """The exact stored verdict from the device, written by the old build,
        is dropped against the device's config -- no restart needed."""
        verdict = {"done": True, "fixed_count": 3, "successful": False, "unresolved": [
            {"description": "Plugin auto_update in config but not on disk",
             "plugin_id": "auto_update", "type": ON_DISK.value}]}
        data = get_status(verdict, _device_config(), installed=DEVICE_INSTALLED,
                          secrets={k: {} for k in DEVICE_SECRETS_KEYS})
        assert data["unresolved"] == []

    def test_a_real_orphan_still_reaches_the_banner(self, get_status):
        verdict = {"done": True, "unresolved": [
            {"plugin_id": "auto_update", "type": ON_DISK.value},
            {"plugin_id": "ghost-plugin", "type": ON_DISK.value}]}
        config = _device_config()
        config["ghost-plugin"] = {"enabled": True}
        data = get_status(verdict, config, installed=DEVICE_INSTALLED)
        assert [e["plugin_id"] for e in data["unresolved"]] == ["ghost-plugin"]
