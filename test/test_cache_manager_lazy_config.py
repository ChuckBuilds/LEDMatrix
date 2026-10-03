"""CacheManager builds its ConfigManager on first use, not in __init__.

Every CacheManager built a ConfigManager and loaded the whole config for a
cache strategy that no longer reads it. The attribute stays public -- the
sports plugins resolve the global timezone through
``cache_manager.config_manager`` -- so it is now built on first access.
"""

from unittest.mock import MagicMock, patch

import pytest

import src.config_manager as config_manager_module
from src.cache_manager import CacheManager


@pytest.fixture
def built(monkeypatch):
    """Count ConfigManager constructions and load_config calls."""
    made = []

    class CountingConfigManager:
        def __init__(self):
            made.append(self)
            self.loads = 0

        def load_config(self):
            self.loads += 1
            return {}

    monkeypatch.setattr(config_manager_module, "ConfigManager", CountingConfigManager)
    return made


@pytest.fixture
def manager(tmp_path):
    with patch('src.cache_manager.CacheManager._get_writable_cache_dir',
               return_value=str(tmp_path)):
        cm = CacheManager()
    cm.stop_cleanup_thread()
    return cm


def test_construction_does_not_load_the_config(built, manager):
    manager.set("k", {"v": 1})
    assert manager.get("k") == {"v": 1}
    assert manager.get_cache_strategy("sports_live")["max_age"] > 0
    assert built == []


def test_first_access_builds_and_loads_it_once(built, manager):
    first = manager.config_manager
    assert manager.config_manager is first
    assert getattr(manager, "config_manager", None) is first
    assert len(built) == 1 and first.loads == 1


def test_assignment_still_wins(built, manager):
    replacement = MagicMock()
    manager.config_manager = replacement
    assert manager.config_manager is replacement
    assert built == []


def test_an_unimportable_config_manager_is_none(manager, monkeypatch):
    import builtins
    real_import = builtins.__import__

    def refuse(name, *args, **kwargs):
        if name == "src.config_manager":
            raise ImportError("no config manager here")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", refuse)
    assert manager.config_manager is None


def test_a_failed_load_is_retried_on_the_next_access(manager, monkeypatch):
    attempts = []

    class Flaky:
        def load_config(self):
            attempts.append(1)
            if len(attempts) == 1:
                raise RuntimeError("config.json unreadable")
            return {}

    monkeypatch.setattr(config_manager_module, "ConfigManager", Flaky)
    with pytest.raises(RuntimeError):
        manager.config_manager
    assert isinstance(manager.config_manager, Flaky)
    assert len(attempts) == 2


def test_a_manager_made_without_init_still_answers(built):
    bare = CacheManager.__new__(CacheManager)
    bare.logger = MagicMock()
    assert bare.config_manager is built[0]
