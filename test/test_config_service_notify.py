"""ConfigService notifies subscribers outside its lock, in order.

Subscribers ran while _load_config held the service's lock. The display's
per-plugin subscriber is PluginManager.apply_config_change, which waits up to
PLUGIN_LOCK_TIMEOUT (5 s) for a busy plugin. The same save that toggles a
plugin's ``enabled`` flags a reconcile, and the render thread runs it: its
get_config() -- and the unsubscribe() of a plugin it disables -- waited behind
every slow callback, freezing the panel for up to 5 s per busy plugin.

What callers could rely on before still holds: one reload's notifications
finish before the next reload's start, and a callback unsubscribe() removed is
not running, and will not run, once unsubscribe() returns.
"""

import itertools
import json
import os
import threading
import time

import pytest

from src.config_manager import ConfigManager
from src.config_service import ConfigService

SLOW = 2.0  # how long a blocked callback waits before giving up


@pytest.fixture
def service(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"display": {"brightness": 50},
                                       "weather": {"enabled": True}}),
                           encoding="utf-8")
    manager = ConfigManager(str(config_path), str(tmp_path / "config_secrets.json"))
    manager.template_path = str(tmp_path / "no-template.json")
    svc = ConfigService(manager, enable_hot_reload=False)
    yield svc, config_path
    svc.shutdown()


_saves = itertools.count(1)


def _save(config_path, **sections):
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config.update(sections)
    config_path.write_text(json.dumps(config), encoding="utf-8")
    # ConfigManager re-reads only when (mtime, size) moves. Two quick saves of
    # the same size can share an mtime tick (about 16 ms on Windows), so step
    # it forward explicitly.
    st = config_path.stat()
    os.utime(config_path, ns=(st.st_atime_ns, st.st_mtime_ns + next(_saves) * 50_000_000))


def _reload_in_background(svc):
    thread = threading.Thread(target=svc._load_config, daemon=True)
    thread.start()
    return thread


def test_get_config_does_not_wait_for_a_slow_subscriber(service):
    svc, config_path = service
    entered, release = threading.Event(), threading.Event()

    def slow(_old, _new):
        entered.set()
        release.wait(SLOW)

    svc.subscribe(slow, plugin_id="weather")
    _save(config_path, weather={"enabled": False})
    reload = _reload_in_background(svc)
    assert entered.wait(SLOW)

    start = time.monotonic()
    config = svc.get_config()
    waited = time.monotonic() - start
    release.set()
    reload.join(SLOW)

    assert waited < 0.5
    # Swapped before anyone was told: a subscriber that reads it sees the new one.
    assert config["weather"]["enabled"] is False


def test_unsubscribing_another_callback_does_not_wait(service):
    svc, config_path = service
    entered, release = threading.Event(), threading.Event()

    def slow(_old, _new):
        entered.set()
        release.wait(SLOW)

    def other(_old, _new):
        pass

    svc.subscribe(slow, plugin_id="weather")
    svc.subscribe(other, plugin_id="clock")
    _save(config_path, weather={"enabled": False})
    reload = _reload_in_background(svc)
    assert entered.wait(SLOW)

    start = time.monotonic()
    svc.unsubscribe(other, plugin_id="clock")
    waited = time.monotonic() - start
    release.set()
    reload.join(SLOW)

    assert waited < 0.5


def test_a_callback_unsubscribed_mid_notification_is_not_called(service):
    svc, config_path = service
    entered, release = threading.Event(), threading.Event()
    called = []

    def slow_global(_old, _new):  # global subscribers are notified first
        entered.set()
        release.wait(SLOW)

    def weather(_old, _new):
        called.append("weather")

    svc.subscribe(slow_global)
    svc.subscribe(weather, plugin_id="weather")
    _save(config_path, weather={"enabled": False})
    reload = _reload_in_background(svc)
    assert entered.wait(SLOW)

    svc.unsubscribe(weather, plugin_id="weather")
    release.set()
    reload.join(SLOW)

    assert called == []


def test_unsubscribe_waits_for_its_own_callback_to_return(service):
    svc, config_path = service
    entered, release = threading.Event(), threading.Event()
    returned = threading.Event()

    def slow(_old, _new):
        entered.set()
        release.wait(SLOW)
        returned.set()

    svc.subscribe(slow, plugin_id="weather")
    _save(config_path, weather={"enabled": False})
    reload = _reload_in_background(svc)
    assert entered.wait(SLOW)

    threading.Timer(0.2, release.set).start()
    svc.unsubscribe(slow, plugin_id="weather")

    assert returned.is_set()
    reload.join(SLOW)


def test_a_callback_may_read_config_and_unsubscribe_itself(service):
    svc, config_path = service
    seen = []

    def once(_old, _new):
        seen.append(svc.get_config()["weather"]["enabled"])
        svc.unsubscribe(once, plugin_id="weather")

    svc.subscribe(once, plugin_id="weather")
    _save(config_path, weather={"enabled": False})
    reload = _reload_in_background(svc)
    reload.join(SLOW)

    assert not reload.is_alive()
    assert seen == [False]


def test_two_reloads_notify_in_order(service):
    svc, config_path = service
    entered, release = threading.Event(), threading.Event()
    seen = []

    def record(old, new):
        seen.append((old["brightness"], new["brightness"]))
        if len(seen) == 1:
            entered.set()
            release.wait(SLOW)

    svc.subscribe(record, plugin_id="display")
    _save(config_path, display={"brightness": 60})
    first = _reload_in_background(svc)
    assert entered.wait(SLOW)

    _save(config_path, display={"brightness": 100})
    second = _reload_in_background(svc)
    time.sleep(0.2)
    release.set()
    first.join(SLOW)
    second.join(SLOW)

    assert seen == [(50, 60), (60, 100)]
