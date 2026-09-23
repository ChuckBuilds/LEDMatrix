"""DisplayController settings that the web UI saves but the display ignored.

- Rotation & Durations: display.display_durations was never read, because
  every plugin inherits get_display_duration() and the plugin was asked first.
- WiFi status overlay: the controller looked for wifi_status.json one level
  above the repo, so WiFiManager's messages never reached the panel.
- Vegas: settings saved in the web UI never reached the running coordinator,
  and the follower's scroll-speed default (75) disagreed with Vegas's (50).
"""

import os
import threading
from unittest.mock import MagicMock

os.environ.setdefault("EMULATOR", "true")

from src.display_controller import DisplayController


def _controller(config=None, plugin_modes=None):
    dc = object.__new__(DisplayController)
    dc.config = config or {}
    dc.plugin_modes = plugin_modes or {}
    return dc


def _plugin(duration):
    plugin = MagicMock()
    plugin.get_display_duration.return_value = duration
    return plugin


class TestDisplayDuration:
    def test_saved_duration_overrides_the_plugin(self):
        dc = _controller({'display': {'display_durations': {'clock': 45}}},
                         {'clock': _plugin(15.0)})
        assert dc._get_display_duration('clock') == 45.0

    def test_unsaved_mode_uses_the_plugin_duration(self):
        dc = _controller({'display': {'display_durations': {'other': 45}}},
                         {'clock': _plugin(12.0)})
        assert dc._get_display_duration('clock') == 12.0

    def test_invalid_saved_values_fall_back_to_the_plugin(self):
        for bad in (0, -5, True, '30', None):
            dc = _controller({'display': {'display_durations': {'clock': bad}}},
                             {'clock': _plugin(12.0)})
            assert dc._get_display_duration('clock') == 12.0, bad

    def test_unknown_mode_without_a_plugin_gets_the_default(self):
        assert _controller()._get_display_duration('nothing') == 30

    def test_hot_reloaded_config_is_used(self):
        dc = _controller({}, {'clock': _plugin(15.0)})
        dc._refresh_config_cache({'display': {'display_durations': {'clock': 60}}})
        assert dc._get_display_duration('clock') == 60.0


class TestWifiStatusPath:
    def test_reader_and_writer_agree(self, test_display_controller):
        from src.wifi_manager import get_wifi_status_path
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = get_wifi_status_path()
        assert path.name == 'wifi_status.json'
        # Inside this checkout's config/, not the directory above the repo.
        assert os.path.normcase(str(path.parent.parent)) == os.path.normcase(repo)
        assert test_display_controller.wifi_status_file == path

    def test_status_message_is_written_whole(self, tmp_path, monkeypatch):
        import json
        import src.wifi_manager as wm
        target = tmp_path / 'wifi_status.json'
        monkeypatch.setattr(wm, 'LED_STATUS_FILE', target)
        # The display polls this file and deletes one it can't parse, so it
        # must appear by rename, never be seen half-written.
        renames = []
        real_replace = os.replace
        monkeypatch.setattr(wm.os, 'replace',
                            lambda src, dst: (renames.append(dst), real_replace(src, dst)))
        manager = object.__new__(wm.WiFiManager)
        manager._show_led_message('Connecting to home...', duration=10)
        assert json.loads(target.read_text())['message'] == 'Connecting to home...'
        assert renames == [target]
        assert not (tmp_path / 'wifi_status.json.tmp').exists()


class TestVegasSettings:
    def _controller(self):
        dc = _controller({'display': {'vegas_scroll': {'scroll_speed': 40}}})
        dc._reconcile_flag_lock = threading.Lock()
        dc._pending_plugin_reconcile = False
        dc._enabled_set_changed = lambda old, new: False
        dc._enabled_plugin_not_running = lambda new: False
        dc.vegas_coordinator = MagicMock()
        return dc

    def test_changed_vegas_settings_reach_the_coordinator(self):
        dc = self._controller()
        old = {'display': {'vegas_scroll': {'scroll_speed': 40}}}
        new = {'display': {'vegas_scroll': {'scroll_speed': 80}}}
        dc._controller_config_change(old, new)
        dc.vegas_coordinator.update_config.assert_called_once_with(new)
        assert dc._scroll_speed == 80.0

    def test_unrelated_saves_leave_vegas_alone(self):
        # Applying a Vegas config rebuilds the strip, so only do it on change.
        dc = self._controller()
        old = {'display': {'vegas_scroll': {'scroll_speed': 40}, 'hardware': {'brightness': 50}}}
        new = {'display': {'vegas_scroll': {'scroll_speed': 40}, 'hardware': {'brightness': 90}}}
        dc._controller_config_change(old, new)
        dc.vegas_coordinator.update_config.assert_not_called()

    def test_scroll_speed_default_matches_vegas_config(self):
        from src.vegas_mode.config import VegasModeConfig
        assert DisplayController._vegas_scroll_speed({}) == VegasModeConfig.from_config({}).scroll_speed

    def test_stopped_coordinator_applies_queued_config(self):
        dc = self._controller()
        dc.on_demand_active = False
        dc._is_vegas_mode_active()
        dc.vegas_coordinator.apply_pending_config_if_idle.assert_called_once()
