"""When Vegas live elements are on, and that everywhere else nothing changes.

Live elements run only when the config allows them and nothing rules them
out: multi-display sync (the follower mirrors whole strips only), swap mode,
the offscreen kill switch, or a display manager with no off-screen canvas.
While they are on, the coordinator listens for plugin updates and moves each
plugin's data epoch on; while off, the adapter never asks for elements.
"""
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.vegas_mode.coordinator import VegasModeCoordinator  # noqa: E402


class _DM:
    width, height = 128, 32

    def __init__(self, offscreen=True):
        self.image = Image.new("RGB", (128, 32))
        if offscreen:
            self.offscreen = self._offscreen

    @contextmanager
    def _offscreen(self, width=None, height=None):
        yield SimpleNamespace(image=Image.new("RGB", (width or 128, 32)))

    def set_scrolling_state(self, *a, **k):
        pass

    def update_display(self):
        pass


class _PM:
    def __init__(self):
        self.plugins = {}
        self.listeners = []

    def add_update_listener(self, fn):
        if fn not in self.listeners:
            self.listeners.append(fn)

    def remove_update_listener(self, fn):
        self.listeners = [f for f in self.listeners if f != fn]


def _coordinator(dm=None, **vegas):
    vegas.setdefault("enabled", True)
    config = {"display": {"vegas_scroll": vegas}}
    return VegasModeCoordinator(config, dm or _DM(), _PM())


def test_on_by_default():
    c = _coordinator()
    c._apply_live_state()
    assert c.live_active
    assert c.plugin_adapter.live_elements_enabled
    assert c._on_plugin_data_changed in c.plugin_manager.listeners


@pytest.mark.parametrize("why,vegas,dm", [
    ("switched off", {"live_refresh": False}, None),
    ("swap mode", {"continuous_scroll": False}, None),
    ("offscreen kill switch", {"offscreen_prefetch": False}, None),
    ("no offscreen canvas", {}, _DM(offscreen=False)),
])
def test_off_when_ruled_out(why, vegas, dm):
    c = _coordinator(dm, **vegas)
    c._apply_live_state()
    assert not c.live_active
    assert not c.plugin_adapter.live_elements_enabled
    assert c.plugin_manager.listeners == []


@pytest.mark.parametrize("role", ["leader", "follower"])
def test_off_whenever_sync_is_configured(role):
    c = _coordinator()
    c.set_sync_manager(SimpleNamespace(role=role))
    c._apply_live_state()
    assert not c.live_active
    assert not c.plugin_adapter.live_elements_enabled


def test_a_standalone_sync_manager_does_not_count():
    from src.common.sync_manager import SyncRole
    c = _coordinator()
    c.set_sync_manager(SimpleNamespace(role=SyncRole.STANDALONE))
    c._apply_live_state()
    assert c.live_active


def test_stopping_switches_it_off_and_stops_listening():
    c = _coordinator()
    c._apply_live_state()
    c._is_active = True
    c.stop()
    assert not c.live_active and not c.plugin_adapter.live_elements_enabled
    assert c.plugin_manager.listeners == []


def test_a_config_change_can_switch_it_off_mid_run():
    c = _coordinator()
    c._apply_live_state()
    c._is_active = True
    c.stream_manager.refresh = MagicMock()
    c.update_config({"display": {"vegas_scroll": {"enabled": True,
                                                   "live_refresh": False}}})
    c._apply_pending_config()
    assert not c.live_active
    assert c.plugin_manager.listeners == []


def test_an_update_moves_the_plugins_epoch_on():
    c = _coordinator()
    c._apply_live_state()
    before = c.live_epochs.get("p")
    for listener in c.plugin_manager.listeners:
        listener("p")
    assert c.live_epochs.get("p") > before
    assert c.plugin_adapter.live_epochs is c.live_epochs


def test_the_state_change_is_logged_once(caplog):
    c = _coordinator(live_refresh=False)
    with caplog.at_level("INFO"):
        c._apply_live_state()
        c._apply_live_state()
    lines = [r.message for r in caplog.records if "live elements" in r.message]
    assert lines == ["Vegas live elements off: switched off (vegas_scroll.live_refresh)"]
