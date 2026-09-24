"""Tests for the web interface's in-memory cache helpers."""
import sys
import threading
from typing import Iterator

import pytest

from web_interface import cache as cache_module
from web_interface.cache import (
    TTLCache, delete_cached, get_cached, invalidate_cache, set_cached,
)


@pytest.fixture(autouse=True)
def clean_cache() -> Iterator[None]:
    """Start and finish every test with an empty cache."""
    invalidate_cache()
    yield
    invalidate_cache()


def test_set_and_get() -> None:
    """A cached value is returned before its TTL expires."""
    set_cached('key', 'value')
    assert get_cached('key') == 'value'


def test_get_missing_returns_none() -> None:
    """Reading an unknown key returns None."""
    assert get_cached('missing') is None


def test_delete_cached_removes_key() -> None:
    """delete_cached removes exactly the named key."""
    set_cached('fonts_catalog', ['a-font'])
    delete_cached('fonts_catalog')
    assert get_cached('fonts_catalog') is None


def test_delete_cached_missing_key_is_noop() -> None:
    """Deleting a key that was never set must not raise."""
    delete_cached('never-set')


def test_invalidate_cache_pattern() -> None:
    """Pattern invalidation removes matching keys and keeps the rest."""
    set_cached('fonts_catalog', 1)
    set_cached('plugins_list', 2)
    invalidate_cache('fonts')
    assert get_cached('fonts_catalog') is None
    assert get_cached('plugins_list') == 2


# ---------------------------------------------------------------------------
# Expiry. set_cached used to accept ttl_seconds and ignore it; only the TTL a
# reader passed to get_cached counted, and get_cached defaulted to 60s.
# ---------------------------------------------------------------------------

class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> _Clock:
    return _Clock()


def test_entry_expires_after_its_ttl(clock: _Clock) -> None:
    c = TTLCache(clock=clock)
    c.set('k', 'v', ttl=10)
    clock.now += 9.9
    assert c.get('k') == 'v'
    clock.now += 0.1
    assert c.get('k') is None


def test_default_ttl_applies_when_none_given(clock: _Clock) -> None:
    c = TTLCache(default_ttl=5, clock=clock)
    c.set('k', 'v')
    clock.now += 4.9
    assert c.get('k') == 'v'
    clock.now += 0.1
    assert c.get('k') is None


def test_reader_max_age_can_only_shorten(clock: _Clock) -> None:
    c = TTLCache(clock=clock)
    c.set('k', 'v', ttl=10)
    clock.now += 5
    assert c.get('k', max_age=6) == 'v'
    assert c.get('k', max_age=5) is None
    clock.now += 5
    assert c.get('k', max_age=60) is None, "a reader extended a 10s entry"


def test_set_cached_ttl_is_honoured(monkeypatch: pytest.MonkeyPatch, clock: _Clock) -> None:
    monkeypatch.setattr(cache_module, '_default_cache', TTLCache(clock=clock))
    set_cached('short', 1, ttl_seconds=2)
    set_cached('long', 2, ttl_seconds=300)
    clock.now += 2
    assert get_cached('short') is None, "set_cached ignored its ttl_seconds"
    clock.now += 100  # past the old implicit 60s read default
    assert get_cached('long') == 2


def test_get_cached_ttl_still_bounds_the_read(monkeypatch: pytest.MonkeyPatch, clock: _Clock) -> None:
    """The existing callers pass the TTL on both sides; that keeps working."""
    monkeypatch.setattr(cache_module, '_default_cache', TTLCache(clock=clock))
    set_cached('system_status', {'cpu': 1}, ttl_seconds=10)
    clock.now += 9
    assert get_cached('system_status', ttl_seconds=10) == {'cpu': 1}
    clock.now += 1
    assert get_cached('system_status', ttl_seconds=10) is None


def test_peek_returns_the_last_value_after_expiry(clock: _Clock) -> None:
    c = TTLCache(clock=clock)
    assert c.peek('k', 'fallback') == 'fallback'
    c.set('k', True, ttl=1)
    clock.now += 5
    assert c.get('k') is None
    assert c.peek('k', False) is True


def test_falsy_values_are_cached(clock: _Clock) -> None:
    c = TTLCache(clock=clock)
    c.set('k', False, ttl=10)
    assert c.get('k', default='miss') is False


def test_clear_pattern_on_instance() -> None:
    c = TTLCache()
    c.set('fonts_catalog', 1)
    c.set('system_status', 2)
    c.clear('fonts')
    assert c.peek('fonts_catalog') is None
    assert c.get('system_status') == 2
    c.clear()
    assert c.peek('system_status') is None


def test_concurrent_expiry_reads_and_writes_do_not_raise() -> None:
    """The old dicts deleted expired keys inside get; two threads reading the
    same expired key (or one reading while another invalidated) could raise
    KeyError, which the endpoints turned into a 500."""
    c = TTLCache()
    keys = [f'k{n}' for n in range(8)]
    errors = []
    stop = threading.Event()

    def reader() -> None:
        try:
            while not stop.is_set():
                for key in keys:
                    c.get(key, max_age=0)  # always expired for this reader
                    c.get(key)
                    c.peek(key)
                c.clear('k1')
        except Exception as exc:  # pragma: no cover - the failure being tested
            errors.append(exc)

    def writer() -> None:
        try:
            for i in range(20000):
                key = keys[i % len(keys)]
                c.set(key, i, ttl=0 if i % 2 else 60)
                if i % 7 == 0:
                    c.delete(key)
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    # Switch threads as often as possible so an unlocked check-then-act
    # actually gets interleaved within the test's run time.
    old_interval = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        readers = [threading.Thread(target=reader) for _ in range(4)]
        for t in readers:
            t.start()
        writer()
        stop.set()
        for t in readers:
            t.join()
    finally:
        sys.setswitchinterval(old_interval)
    assert errors == []
