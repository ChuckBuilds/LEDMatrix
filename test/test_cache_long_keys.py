"""A cache key too long to be a filename still gets a cache file.

The calendar plugin's key joins every calendar id the user picked; on a real
install it passed 300 bytes, and since ext4 caps a filename at 255 every write
failed with ENAMETOOLONG -- logged as "permission denied", every update.
"""

import logging
import os
from unittest.mock import patch

from src.cache.disk_cache import DiskCache, _MAX_KEY_FILENAME_BYTES, _filename_stem
from src.cache_manager import CacheManager

# The shape of the key that failed on hdpi, ids anonymised.
CALENDAR_KEY = (
    "calendar_events_someone@example.com_en.usa#holiday@group.v.calendar.google.com_"
    "family13997378751670666433@group.calendar.google.com_ncaaf_-m-07kbp5_"
    "%47eorgia+%42ulldogs+football#sports@group.v.calendar.google.com_nfl_-m-07l24_"
    "%54ampa+%42ay+%42uccaneers#sports@group.v.calendar.google.com_primary"
)

# ext4/xfs/btrfs NAME_MAX; set()'s temp file adds 15 bytes to the stem.
NAME_MAX = 255
TEMP_OVERHEAD = len(".") + len(".json") + len(".") + 8


def test_the_real_key_was_too_long_to_write():
    assert len((CALENDAR_KEY + ".json").encode()) > NAME_MAX - 10


def test_a_long_key_round_trips(tmp_path):
    cache = DiskCache(str(tmp_path))
    cache.set(CALENDAR_KEY, {"events": [1, 2, 3]})

    assert cache.get(CALENDAR_KEY, max_age=None) == {"events": [1, 2, 3]}
    path = cache.get_cache_path(CALENDAR_KEY)
    assert os.path.isfile(path)
    stem = os.path.basename(path)[:-len(".json")]
    assert len(stem.encode()) + TEMP_OVERHEAD <= NAME_MAX


def test_short_keys_keep_their_filename(tmp_path):
    cache = DiskCache(str(tmp_path))
    exactly = "k" * _MAX_KEY_FILENAME_BYTES
    assert cache.get_cache_path("weather_current") == str(tmp_path / "weather_current.json")
    assert cache.get_cache_path(exactly) == str(tmp_path / f"{exactly}.json")
    assert cache.get_cache_path(exactly + "k") != str(tmp_path / f"{exactly}k.json")


def test_long_keys_sharing_a_prefix_stay_apart(tmp_path):
    cache = DiskCache(str(tmp_path))
    first, second = CALENDAR_KEY + "_a", CALENDAR_KEY + "_b"
    cache.set(first, {"which": "a"})
    cache.set(second, {"which": "b"})

    assert cache.get_cache_path(first) != cache.get_cache_path(second)
    assert cache.get(first, max_age=None) == {"which": "a"}
    assert cache.get(second, max_age=None) == {"which": "b"}


def test_the_prefix_never_splits_a_character():
    key = "news_" + "é" * 300  # two bytes each, so the cut lands mid-character
    stem = _filename_stem(key)

    assert stem.startswith("news_é")
    assert len(stem.encode("utf-8")) <= _MAX_KEY_FILENAME_BYTES
    stem.encode("utf-8").decode("utf-8")  # well-formed


def test_a_stem_listed_by_the_web_ui_deletes_the_same_file(tmp_path):
    with patch('src.cache_manager.CacheManager._get_writable_cache_dir', return_value=str(tmp_path)):
        manager = CacheManager()
    try:
        manager.save_cache(CALENDAR_KEY, {"events": []})

        listed = [entry["key"] for entry in manager.list_cache_files()]
        assert len(listed) == 1
        manager.clear_cache(listed[0])

        assert [n for n in os.listdir(tmp_path) if n.endswith(".json")] == []
    finally:
        manager.stop_cleanup_thread()


def test_a_failed_write_names_the_real_error(tmp_path, monkeypatch, caplog):
    blocker = tmp_path / "a-file"
    blocker.write_text("")
    # No writable fallback either, so set() gives up and says why.
    monkeypatch.setattr(os.path, "expanduser", lambda _p: str(blocker / "home"))
    cache = DiskCache(str(tmp_path / "missing"))

    with caplog.at_level(logging.WARNING):
        cache.set("weather_current", {"t": 1})

    gave_up = [r.getMessage() for r in caplog.records if "Could not write cache" in r.getMessage()]
    assert len(gave_up) == 1
    assert "permission denied" not in gave_up[0]
    assert os.strerror(2) in gave_up[0]  # ENOENT: the directory does not exist
