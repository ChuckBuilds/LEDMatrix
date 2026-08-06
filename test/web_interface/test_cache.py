"""Tests for the web interface's in-memory cache helpers."""
from typing import Iterator

import pytest

from web_interface.cache import delete_cached, get_cached, invalidate_cache, set_cached


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
