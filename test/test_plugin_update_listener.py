"""PluginManager's update listeners: told the moment a plugin's data may have changed.

Vegas live elements redraw a plugin when its update() completes. Before these
listeners the only signal was a set drained by the Vegas tick every ~4s; the
listener hears it at once. It is called on the update worker with the
plugin's lock still held, which is why a listener may only hand off.
"""
import os
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.plugin_system.base_plugin import BasePlugin  # noqa: E402
from src.plugin_system.plugin_manager import PluginManager  # noqa: E402
from src.plugin_system.plugin_state import PluginState  # noqa: E402


@pytest.fixture
def pm(tmp_path):
    manager = PluginManager(plugins_dir=str(tmp_path), config_manager=None,
                            display_manager=None, cache_manager=None)
    yield manager
    manager.stop_update_worker()


class _Plugin:
    def __init__(self, fail=False):
        self.enabled = True
        self.fail = fail
        self.updates = 0

    def update(self):
        self.updates += 1
        if self.fail:
            raise RuntimeError("no data")

    def display(self, force_clear=False):
        return True


def _install(pm, plugin, plugin_id="p"):
    pm.plugins[plugin_id] = plugin
    pm._update_interval_cache[plugin_id] = 0.01
    pm.state_manager.set_state(plugin_id, PluginState.ENABLED)
    return plugin_id


def _wait(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_a_completed_update_calls_the_listener_with_the_lock_held(pm):
    plugin_id = _install(pm, _Plugin())
    heard = []

    def listener(pid):
        heard.append((pid, pm.get_plugin_lock(pid).locked()))

    pm.add_update_listener(listener)
    pm.run_scheduled_updates()
    assert _wait(lambda: heard)
    assert heard[0] == (plugin_id, True)
    # The poll still sees it too: the set is filled before listeners run.
    assert plugin_id in pm.drain_completed_updates()


def test_a_failed_update_is_not_reported(pm):
    _install(pm, _Plugin(fail=True))
    heard = []
    pm.add_update_listener(heard.append)
    pm.run_scheduled_updates()
    assert _wait(lambda: pm.plugins["p"].updates == 1)
    time.sleep(0.1)
    assert heard == []


def test_a_listener_that_raises_does_not_stop_the_others(pm):
    heard = []

    def broken(_pid):
        raise ValueError("listener bug")

    pm.add_update_listener(broken)
    pm.add_update_listener(heard.append)
    pm._note_update_completed("p")     # must not raise
    assert heard == ["p"]
    assert "p" in pm.drain_completed_updates()


def test_adding_twice_calls_once_and_removing_stops_it(pm):
    heard = []
    pm.add_update_listener(heard.append)
    pm.add_update_listener(heard.append)
    pm.notify_data_changed("x")
    assert heard == ["x"]
    pm.remove_update_listener(heard.append)
    pm.notify_data_changed("y")
    assert heard == ["x"]


def test_notify_data_changed_reaches_listeners_but_not_the_poll(pm):
    heard = []
    pm.add_update_listener(heard.append)
    pm.notify_data_changed("q")
    assert heard == ["q"]
    assert pm.drain_completed_updates() == []


def test_a_plugin_can_report_data_that_arrived_on_its_own_thread(pm):
    class Pushed(BasePlugin):
        def update(self):
            pass

        def display(self, force_clear=False):
            pass

    plugin = Pushed.__new__(Pushed)
    plugin.plugin_id = "pushed"
    plugin.plugin_manager = pm
    heard = []
    pm.add_update_listener(heard.append)
    thread = threading.Thread(target=plugin.notify_vegas_data_changed)
    thread.start()
    thread.join()
    assert heard == ["pushed"]


def test_a_bare_manager_has_no_listeners_to_call():
    manager = PluginManager.__new__(PluginManager)
    manager._completed_updates = set()
    manager._completed_updates_lock = threading.Lock()
    manager._note_update_completed("p")
    manager.add_update_listener(lambda pid: None)
    manager.remove_update_listener(lambda pid: None)
