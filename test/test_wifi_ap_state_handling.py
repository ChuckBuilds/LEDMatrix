"""WiFi / AP-mode fixes around the web UI and the monitor daemon.

* disconnect_from_network ran an AP-mode check that could never enable the
  AP: the web routes build a fresh WiFiManager per request, whose grace
  counter starts at 0 and needs 3 consecutive checks. It only added sleeps.
* The monitor daemon read wifi_config.json once at startup, so the web UI's
  auto-enable toggle (which only writes the file) had no effect until the
  daemon restarted.
* The captive-portal endpoints only checked hostapd, but enable_ap_mode falls
  back to an nmcli AP; with that AP up, phones got "internet works".
* The LED status file path was a module global set by whichever WiFiManager
  was built first, and the config path fell back to /home/ledpi/LEDMatrix.
"""

import importlib.util
import json
import logging
import os
import pathlib
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import src.wifi_manager as wm  # noqa: E402
from src.wifi_manager import WiFiManager, WiFiStatus  # noqa: E402

REPO = Path(__file__).resolve().parents[1]


def _ok(stdout=""):
    return SimpleNamespace(returncode=0, stdout=stdout, stderr="")


def _manager(config_path):
    with patch("src.wifi_manager.subprocess.run", return_value=_ok("wlan0\n")):
        manager = WiFiManager(config_path=config_path)
    manager._wifi_interface = "wlan0"
    manager.has_nmcli = True
    return manager


class TestDisconnect:
    def test_no_ap_check_and_no_extra_sleep(self, tmp_path):
        manager = _manager(tmp_path / "config" / "wifi_config.json")
        manager.get_wifi_status = MagicMock(
            return_value=WiFiStatus(connected=True, ssid="home"))
        manager._find_profile_for_ssid = MagicMock(return_value=None)
        manager.check_and_manage_ap_mode = MagicMock()
        sleeps = []
        with patch("src.wifi_manager.subprocess.run", return_value=_ok()), \
             patch("src.wifi_manager.time.sleep", side_effect=sleeps.append):
            ok, _message = manager.disconnect_from_network()
        assert ok
        manager.check_and_manage_ap_mode.assert_not_called()
        assert sum(sleeps) == 2


class TestLedStatusFile:
    def test_each_manager_writes_next_to_its_own_config(self, tmp_path, monkeypatch):
        monkeypatch.setattr(wm, "LED_STATUS_FILE", None)
        first = _manager(tmp_path / "a" / "wifi_config.json")
        second = _manager(tmp_path / "b" / "wifi_config.json")
        second._show_led_message("hello", duration=3)
        assert json.loads((tmp_path / "b" / "wifi_status.json").read_text())["message"] == "hello"
        assert not (tmp_path / "a" / "wifi_status.json").exists()
        first._show_led_message("from a")
        assert (tmp_path / "a" / "wifi_status.json").exists()

    def test_the_default_manager_writes_where_the_display_reads(self, tmp_path):
        manager = _manager(tmp_path / "config" / "wifi_config.json")
        assert manager._led_status_file == tmp_path / "config" / "wifi_status.json"
        # And for the default config, that is get_wifi_status_path().
        assert wm.get_wifi_config_path().parent / "wifi_status.json" == wm.get_wifi_status_path()


class TestConfigPathFallback:
    def test_without_a_config_dir_the_project_root_is_used(self, monkeypatch):
        monkeypatch.delenv("LEDMATRIX_ROOT", raising=False)
        # A fresh checkout has no config/ yet; the fallback was a hardcoded
        # /home/ledpi/LEDMatrix, which is nobody's checkout on most installs.
        monkeypatch.setattr(pathlib.Path, "exists", lambda self: False)
        assert wm.get_wifi_config_path() == REPO / "config" / "wifi_config.json"


def _load_daemon():
    """Import scripts/utils/wifi_monitor_daemon.py without its /var/log
    FileHandler, which cannot be opened off the Pi."""
    path = REPO / "scripts" / "utils" / "wifi_monitor_daemon.py"
    spec = importlib.util.spec_from_file_location("wifi_monitor_daemon_under_test", path)
    module = importlib.util.module_from_spec(spec)
    with patch.object(logging, "FileHandler", lambda *a, **k: logging.NullHandler()):
        spec.loader.exec_module(module)
    return module


class TestDaemonReloadsConfig:
    @pytest.fixture
    def daemon(self, tmp_path):
        module = _load_daemon()
        config_path = tmp_path / "wifi_config.json"
        config_path.write_text(json.dumps({"auto_enable_ap_mode": True}))

        manager = SimpleNamespace(config_path=config_path,
                                  config={"auto_enable_ap_mode": True})
        manager._load_config = MagicMock(side_effect=lambda: manager.config.update(
            json.loads(config_path.read_text())))
        daemon = module.WiFiMonitorDaemon.__new__(module.WiFiMonitorDaemon)
        daemon.wifi_manager = manager
        daemon._config_mtime = daemon._config_file_mtime()
        return daemon, manager, config_path

    def _rewrite(self, config_path, data):
        before = config_path.stat().st_mtime_ns
        config_path.write_text(json.dumps(data))
        os.utime(config_path, ns=(before + 10**9, before + 10**9))

    def test_a_changed_file_is_reread(self, daemon):
        daemon, manager, config_path = daemon
        self._rewrite(config_path, {"auto_enable_ap_mode": False})
        daemon._reload_config_if_changed()
        assert manager.config["auto_enable_ap_mode"] is False

    def test_an_unchanged_file_is_not(self, daemon):
        daemon, manager, _ = daemon
        daemon._reload_config_if_changed()
        daemon._reload_config_if_changed()
        manager._load_config.assert_not_called()

    def test_the_loop_rereads_before_each_check(self, tmp_path):
        module = _load_daemon()
        daemon = module.WiFiMonitorDaemon.__new__(module.WiFiMonitorDaemon)
        daemon.check_interval = 0
        daemon.running = True
        daemon.last_state = None
        daemon._consecutive_internet_failures = 0
        daemon._nm_restart_threshold = 5
        order = []
        manager = MagicMock()
        manager.config = {}
        manager.get_wifi_status.return_value = WiFiStatus(connected=False)
        manager._is_ethernet_connected.return_value = False
        manager.check_and_manage_ap_mode_with_state.side_effect = lambda: (
            order.append("check"), (False, WiFiStatus(connected=False), False, False))[1]
        daemon.wifi_manager = manager
        daemon._reload_config_if_changed = lambda: order.append("reload")

        def stop(_seconds):
            daemon.running = False
        with patch.object(module.time, "sleep", side_effect=stop):
            daemon.run()
        assert order == ["reload", "check"]


class TestCaptivePortalSeesTheNmcliAp:
    @pytest.fixture
    def web_app(self, monkeypatch):
        import web_interface.app as web_app
        from web_interface.cache import TTLCache
        monkeypatch.setattr(web_app, "_service_status_cache", TTLCache())
        monkeypatch.setattr(web_app, "_SYSTEMCTL", "/bin/systemctl")
        monkeypatch.setattr(web_app, "_NMCLI", "/usr/bin/nmcli")
        return web_app

    def _run(self, active_connections):
        calls = []

        def run(argv, **kwargs):
            calls.append(argv)
            if "is-active" in argv:
                return _ok("inactive\n")
            return _ok(active_connections)
        return calls, run

    def test_an_nmcli_ap_counts_as_ap_mode(self, web_app, monkeypatch):
        calls, run = self._run("LEDMatrix-Setup-AP:802-11-wireless\n")
        monkeypatch.setattr(web_app.subprocess, "run", run)
        assert web_app.is_ap_mode_active() is True
        # Cached: these endpoints are hit per request.
        web_app.is_ap_mode_active()
        assert len(calls) == 2

    def test_an_ordinary_wifi_connection_does_not(self, web_app, monkeypatch):
        _, run = self._run("home:802-11-wireless\nWired connection 1:802-3-ethernet\n")
        monkeypatch.setattr(web_app.subprocess, "run", run)
        assert web_app.is_ap_mode_active() is False

    def test_the_detection_endpoints_redirect(self, web_app, monkeypatch):
        _, run = self._run("LEDMatrix-Setup-AP:802-11-wireless\n")
        monkeypatch.setattr(web_app.subprocess, "run", run)
        web_app.app.config["TESTING"] = True
        with web_app.app.test_client() as client:
            response = client.get("/generate_204")
        assert response.status_code == 302
