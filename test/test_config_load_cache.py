"""Tests for load_config's mtime fast path (src/config_manager.py).

load_config used to re-read + re-parse config.json, the template (with a
recursive migration diff) and secrets on EVERY call — ~30 web request
handlers call it, some 2-3x per request. The fast path skips all of it
when the three files' (mtime_ns, size) signatures are unchanged.

The invariant that matters most: cross-process freshness — a save from
the web process must be picked up by the display process's next load.
That's guaranteed because the signature is re-stat'd on every call.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.config_manager import ConfigManager  # noqa: E402


@pytest.fixture
def mgr(tmp_path):
    config = tmp_path / "config.json"
    secrets = tmp_path / "secrets.json"
    template = tmp_path / "template.json"
    config.write_text(json.dumps({"display": {"brightness": 90}, "timezone": "UTC"}))
    secrets.write_text(json.dumps({"weather": {"api_key": "sek"}}))
    template.write_text(json.dumps({"display": {"brightness": 90}, "timezone": "UTC"}))
    m = ConfigManager(config_path=str(config), secrets_path=str(secrets))
    m.template_path = str(template)
    return m, config, secrets, template


def _count_opens(monkeypatch, mgr_paths):
    """Count open() calls hitting the config files."""
    counts = {"n": 0}
    real_open = open

    def counting_open(file, *args, **kwargs):
        if str(file) in mgr_paths:
            counts["n"] += 1
        return real_open(file, *args, **kwargs)

    import builtins
    monkeypatch.setattr(builtins, "open", counting_open)
    return counts


class TestFastPath:
    def test_unchanged_files_are_not_reread(self, mgr, monkeypatch):
        m, config, secrets, template = mgr
        first = m.load_config()
        assert first["weather"]["api_key"] == "sek"  # secrets merged
        counts = _count_opens(monkeypatch, {str(config), str(secrets), str(template)})
        for _ in range(10):
            again = m.load_config()
        assert counts["n"] == 0, "fast path must not re-open any config file"
        assert again == first
        assert again is not first  # each caller gets its own copy, see below

    def test_config_change_triggers_reload(self, mgr):
        m, config, secrets, template = mgr
        m.load_config()
        data = json.loads(config.read_text())
        data["display"]["brightness"] = 55
        config.write_text(json.dumps(data))
        os.utime(config, (os.stat(config).st_atime, os.stat(config).st_mtime + 2))
        assert m.load_config()["display"]["brightness"] == 55

    def test_secrets_change_triggers_reload(self, mgr):
        m, config, secrets, template = mgr
        m.load_config()
        secrets.write_text(json.dumps({"weather": {"api_key": "NEW"}}))
        os.utime(secrets, (os.stat(secrets).st_atime, os.stat(secrets).st_mtime + 2))
        assert m.load_config()["weather"]["api_key"] == "NEW"

    def test_template_change_triggers_reload_and_migration(self, mgr):
        m, config, secrets, template = mgr
        m.load_config()
        template.write_text(json.dumps({
            "display": {"brightness": 90}, "timezone": "UTC",
            "brand_new_key": {"added": True}}))
        os.utime(template, (os.stat(template).st_atime, os.stat(template).st_mtime + 2))
        reloaded = m.load_config()
        assert reloaded.get("brand_new_key") == {"added": True}

    def test_same_second_edit_detected_via_mtime_ns_or_size(self, mgr):
        """Coarse-mtime same-second edits: size difference still busts it."""
        m, config, secrets, template = mgr
        m.load_config()
        st = os.stat(config)
        data = json.loads(config.read_text())
        data["timezone"] = "America/New_York"  # different byte length
        config.write_text(json.dumps(data))
        os.utime(config, (st.st_atime, st.st_mtime))  # force same mtime
        assert m.load_config()["timezone"] == "America/New_York"


class TestCallersGetACopy:
    """A web handler edits what load_config returned, then validates. When
    validation failed, the edit stayed in the cache the fast path serves, and
    the next unrelated save wrote it -- a nested secret included, in plain
    text, because it had never reached config_secrets.json to be stripped."""

    def test_editing_a_loaded_config_does_not_change_the_next_load(self, mgr):
        m, config, secrets, template = mgr
        loaded = m.load_config()
        loaded["display"]["brightness"] = 1
        loaded["weather"]["api_key"] = "typed-but-never-saved"
        again = m.load_config()
        assert again["display"]["brightness"] == 90
        assert again["weather"]["api_key"] == "sek"

    def test_the_full_path_also_returns_a_copy(self, mgr):
        m, config, secrets, template = mgr
        m.load_config()["display"]["brightness"] = 1  # first load: full path
        assert m.load_config()["display"]["brightness"] == 90

    def test_an_edit_never_reaches_a_later_save(self, mgr):
        m, config, secrets, template = mgr
        m.load_config()["display"]["new_secret"] = "hunter2"  # then bailed out
        m.save_config(m.load_config())  # some other handler saves
        assert "hunter2" not in config.read_text()


class TestSaveCoherence:
    def test_save_config_then_load_returns_saved_data(self, mgr, monkeypatch):
        m, config, secrets, template = mgr
        m.load_config()
        new = {"display": {"brightness": 42}, "timezone": "UTC",
               "weather": {"api_key": "sek"}}
        m.save_config(new)
        counts = _count_opens(monkeypatch, {str(config), str(secrets), str(template)})
        loaded = m.load_config()
        assert loaded["display"]["brightness"] == 42
        assert loaded["weather"]["api_key"] == "sek"  # secrets survive in memory
        assert counts["n"] == 0  # signature refreshed by save; no re-read

    def test_the_saved_dict_does_not_become_the_cache(self, mgr):
        m, config, secrets, template = mgr
        m.load_config()
        new = {"display": {"brightness": 42}, "timezone": "UTC",
               "weather": {"api_key": "sek"}}
        m.save_config(new)
        new["display"]["brightness"] = 7  # the caller keeps using its dict
        assert m.load_config()["display"]["brightness"] == 42

    def test_cross_process_save_is_picked_up(self, mgr):
        """Another process writing config.json (different mtime) must bust
        this process's fast path — the core cross-process guarantee."""
        m, config, secrets, template = mgr
        m.load_config()
        other = ConfigManager(config_path=str(config), secrets_path=str(secrets))
        other.template_path = str(template)
        other.load_config()
        other.save_config({"display": {"brightness": 11}, "timezone": "UTC",
                           "weather": {"api_key": "sek"}})
        os.utime(config, (os.stat(config).st_atime, os.stat(config).st_mtime + 2))
        assert m.load_config()["display"]["brightness"] == 11


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
