"""The shared sports.py mixins: their host contract, and _plugin_dir.

Two things are worth testing here and the rest is not. The 45 method bodies
moved verbatim from the plugins, so they are covered by the plugins' own tests
and by 176 byte-identical safety-harness renders. What is genuinely new is:

1. The contract. Every ``self.<CONSTANT>`` a mixin reads must be defined on the
   mixin, or a host that does not happen to declare it raises AttributeError at
   runtime. Two were missed on the first pass (_QUALITY_CHOICES and
   _RANKING_COVERAGE_SECONDS); the eight plugins all declare them, so nothing
   failed -- it would only have bitten a ninth. The test derives the list rather
   than restating it, so the next omission fails here instead of in the field.

2. ``_plugin_dir``. This is the only line of genuinely new logic in the move. In
   sports.py these methods found config_schema.json with ``__file__``; here that
   is src/common/, so the plugin directory has to be recovered from the
   instance -- and getting it wrong is silent, costing grid-snapped font sizes
   (measured at 81% anti-aliased edges) rather than raising.
"""

import ast
import os
import sys
import types
from abc import ABC

import pytest

from src.common import sports_shared
from src.common.sports_shared import (
    SportsCoreSharedMixin, SportsLiveSharedMixin, SportsRecentSharedMixin)

MIXINS = (SportsCoreSharedMixin, SportsLiveSharedMixin, SportsRecentSharedMixin)


def _constants_read_by_mixins():
    """Every ALL-CAPS ``self.X`` the mixin bodies read, found by parsing them."""
    tree = ast.parse(open(sports_shared.__file__).read())
    names = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "self"
                and node.attr.upper() == node.attr):
            names.add(node.attr)
    return names


class TestHostContract:
    def test_every_constant_read_is_also_defined(self):
        # Otherwise a host that does not declare it raises AttributeError the
        # first time the code path runs -- which for these is mid-render.
        missing = sorted(
            name for name in _constants_read_by_mixins()
            if not any(hasattr(m, name) for m in MIXINS))
        assert missing == [], (
            f"read but never defined on a mixin: {missing}. Give each a default "
            f"on SportsCoreSharedMixin and document it in the module docstring.")

    @pytest.mark.parametrize("name,expected", [
        ("_QUALITY_CHOICES", frozenset({"any", "ranked"})),
        ("_RANKING_COVERAGE_SECONDS", 3600),
        ("_SCORE_PROBE_TEXT", "00-00"),
        ("_FONT_DESIGN_HEIGHT", 32),
    ])
    def test_defaults_match_what_the_plugins_ship(self, name, expected):
        # The eight plugins declare their own copies, which shadow these. The
        # values must still agree, or a ninth plugin inheriting the default
        # behaves differently from the eight.
        assert getattr(SportsCoreSharedMixin, name) == expected

    def test_only_the_recent_mixin_carries_a_constructor(self):
        # SportsCore and SportsLive keep their own __init__ -- those differ per
        # plugin. SportsRecent.__init__ was one of the 48 byte-identical bodies,
        # so it moved with the rest; that is deliberate, not an oversight.
        assert "__init__" not in SportsCoreSharedMixin.__dict__
        assert "__init__" not in SportsLiveSharedMixin.__dict__
        assert "__init__" in SportsRecentSharedMixin.__dict__

    def test_the_recent_constructor_still_chains_to_the_host(self):
        """Its zero-arg super() binds to where it is DEFINED, not where it is used.

        Moving a body containing bare ``super()`` is the one move that can
        change meaning: the compiler closes over __class__ = the defining class,
        so after the move that is SportsRecentSharedMixin rather than the
        plugin's SportsRecent. It still works only because the mixin is listed
        first, leaving the host class next in the MRO -- adopt it in the other
        order and the chain silently skips the host's __init__.
        """
        calls = []

        class Host:
            def __init__(self, config, display_manager, cache_manager, logger, sport_key):
                calls.append(sport_key)
                self.mode_config = {}

        class Recent(SportsRecentSharedMixin, Host):
            pass

        inst = Recent({}, None, None, None, "nhl")
        assert calls == ["nhl"], "the host constructor must still run"
        assert inst.current_game_index == 0
        assert inst.update_interval == 3600
        assert inst._zero_clock_timestamps == {}

    def test_adopting_the_recent_mixin_second_would_skip_the_host(self):
        # The failure mode the ordering above prevents, pinned so nobody
        # "tidies" the base list.
        calls = []

        class Host:
            def __init__(self, *a):
                calls.append(a)
                self.mode_config = {}

        class Wrong(Host, SportsRecentSharedMixin):
            pass

        Wrong({}, None, None, None, "nhl")
        # Host.__init__ wins and the mixin's setup never runs at all.
        assert not hasattr(Wrong({}, None, None, None, "nhl"), "current_game_index")


class _Host(SportsCoreSharedMixin):
    pass


def _write_plugin(tmp_path, name="fakeplug", schema=True):
    """A throwaway package on sys.path, with or without a config_schema.json."""
    d = tmp_path / name
    d.mkdir()
    (d / "__init__.py").write_text("")
    (d / "mod.py").write_text("class Leaf:\n    pass\n")
    if schema:
        (d / "config_schema.json").write_text(
            '{"properties": {"customization": {"properties": '
            '{"score": {"properties": {"font_size": {"default": 16}}}}}}}')
    return d


class TestPluginDir:
    def test_it_finds_the_directory_holding_config_schema_json(self, tmp_path, monkeypatch):
        d = _write_plugin(tmp_path)
        monkeypatch.syspath_prepend(str(tmp_path))
        mod = __import__("fakeplug.mod", fromlist=["Leaf"])
        host = type("H", (mod.Leaf, SportsCoreSharedMixin), {})()
        assert host._plugin_dir() == str(d)

    def test_a_class_built_by_type_still_resolves(self, tmp_path, monkeypatch):
        # SportsCore is an ABC, so type(name, bases, ns) reports __module__ as
        # "abc" rather than the plugin -- which is exactly what the plugins'
        # own tests build. Walking the MRO is what steps past it.
        d = _write_plugin(tmp_path, "abcplug")
        monkeypatch.syspath_prepend(str(tmp_path))
        mod = __import__("abcplug.mod", fromlist=["Leaf"])

        class Base(SportsCoreSharedMixin, mod.Leaf, ABC):
            pass

        synthetic = type("Probe", (Base,), {})
        assert synthetic.__module__ == "abc", "precondition: the trap this guards"
        assert synthetic.__new__(synthetic)._plugin_dir() == str(d)

    def test_it_returns_none_when_no_schema_is_anywhere_on_the_mro(self, tmp_path, monkeypatch):
        d = _write_plugin(tmp_path, "noschema", schema=False)
        monkeypatch.syspath_prepend(str(tmp_path))
        mod = __import__("noschema.mod", fromlist=["Leaf"])
        host = type("H", (mod.Leaf, SportsCoreSharedMixin), {})()
        # None rather than a wrong guess: _schema_font_size then caches empty
        # and every element keeps its own default.
        assert host._plugin_dir() is None

    def test_it_never_returns_the_core_module_directory(self):
        # The bug this replaced: __file__ pointed at src/common/, so the schema
        # was never found and font sizes silently stopped snapping to the grid.
        host = _Host()
        core_common = os.path.dirname(os.path.abspath(sports_shared.__file__))
        assert host._plugin_dir() != core_common

    def test_a_module_with_no_file_is_skipped_not_crashed_on(self, monkeypatch):
        # Namespace packages and some frozen/dynamic modules have no __file__.
        ghost = types.ModuleType("ghost_no_file")
        if hasattr(ghost, "__file__"):
            del ghost.__file__
        monkeypatch.setitem(sys.modules, "ghost_no_file", ghost)
        cls = type("H", (SportsCoreSharedMixin,), {"__module__": "ghost_no_file"})
        assert cls.__new__(cls)._plugin_dir() is None


class TestSchemaFontSize:
    def test_it_reads_the_plugin_schema_not_the_cores(self, tmp_path, monkeypatch):
        d = _write_plugin(tmp_path, "sizeplug")
        monkeypatch.syspath_prepend(str(tmp_path))
        mod = __import__("sizeplug.mod", fromlist=["Leaf"])
        host = type("H", (mod.Leaf, SportsCoreSharedMixin), {})()
        assert host._schema_font_size("score") == 16

    def test_an_unknown_element_is_none(self, tmp_path, monkeypatch):
        _write_plugin(tmp_path, "unkplug")
        monkeypatch.syspath_prepend(str(tmp_path))
        mod = __import__("unkplug.mod", fromlist=["Leaf"])
        host = type("H", (mod.Leaf, SportsCoreSharedMixin), {})()
        assert host._schema_font_size("nonesuch") is None

    def test_an_empty_key_is_none_without_touching_the_disk(self):
        assert _Host()._schema_font_size("") is None

    def test_a_missing_schema_degrades_to_none_rather_than_raising(self, tmp_path, monkeypatch):
        _write_plugin(tmp_path, "bareplug", schema=False)
        monkeypatch.syspath_prepend(str(tmp_path))
        mod = __import__("bareplug.mod", fromlist=["Leaf"])
        host = type("H", (mod.Leaf, SportsCoreSharedMixin), {})()
        assert host._schema_font_size("score") is None


class _LiveHost(SportsLiveSharedMixin):
    """The documented contract for the live mixin, and nothing else."""

    def __init__(self, no_data_interval=300, stale_game_timeout=600, over=()):
        self.no_data_interval = no_data_interval
        self.stale_game_timeout = stale_game_timeout
        self.game_update_timestamps = {}
        self._over = set(over)

        class _L:
            def __getattr__(self, _n):
                return lambda *a, **k: None
        self.logger = _L()

    def _is_game_really_over(self, game):
        return game.get("id") in self._over


class TestLiveMixin:
    """These three moved to the core, so they are tested here.

    They were already covered by hockey's and lacrosse's own tests, but those
    two plugins disable live mode in their safety-harness fixtures, so the 176
    renders never exercise this path. Testing the mixin directly means the
    coverage no longer depends on which plugin happens to have a unit test.
    """

    def test_a_stale_game_is_dropped_and_forgotten(self):
        h = _LiveHost(stale_game_timeout=600)
        import time as _t
        h.game_update_timestamps["g1"] = {"last_seen": _t.time() - 5000}
        games = [{"id": "g1", "home_abbr": "H", "away_abbr": "A"}]
        h._detect_stale_games(games)
        assert games == []
        assert "g1" not in h.game_update_timestamps, "its timestamp must go too"

    def test_a_fresh_game_survives(self):
        h = _LiveHost(stale_game_timeout=600)
        import time as _t
        h.game_update_timestamps["g1"] = {"last_seen": _t.time() - 5}
        games = [{"id": "g1"}]
        h._detect_stale_games(games)
        assert len(games) == 1

    def test_a_game_never_seen_is_not_treated_as_stale(self):
        # last_seen 0 means "no reading", not "seen at the epoch".
        h = _LiveHost()
        games = [{"id": "g1"}]
        h._detect_stale_games(games)
        assert len(games) == 1

    def test_a_game_with_no_id_is_left_alone(self):
        h = _LiveHost()
        games = [{"home_abbr": "H"}]
        h._detect_stale_games(games)
        assert len(games) == 1

    def test_a_finished_game_is_dropped_even_when_fresh(self):
        h = _LiveHost(over=("g2",))
        games = [{"id": "g1"}, {"id": "g2"}]
        h._detect_stale_games(games)
        assert [g["id"] for g in games] == ["g1"]

    def test_removing_several_does_not_skip_any(self):
        # It iterates a copy for exactly this reason; mutating the live list
        # while looping would step over the element after each removal.
        h = _LiveHost(over=("g1", "g2", "g3"))
        games = [{"id": "g1"}, {"id": "g2"}, {"id": "g3"}]
        h._detect_stale_games(games)
        assert games == []

    def test_the_idle_interval_escalates_with_the_empty_streak(self):
        h = _LiveHost(no_data_interval=60)
        h.live_idle_max_interval = 100000
        base = h._idle_live_interval()
        h._empty_live_streak = 6
        short = h._idle_live_interval()
        h._empty_live_streak = 24
        long = h._idle_live_interval()
        assert base < short < long

    def test_the_ceiling_bounds_even_the_unescalated_interval(self):
        # base > ceiling is a reachable config: the two settings are
        # independent integers with no cross-validation. Returning base
        # unclamped made the wait SHRINK as the streak grew.
        h = _LiveHost(no_data_interval=3600)
        h.live_idle_max_interval = 900
        h._empty_live_streak = 0
        assert h._idle_live_interval() == 900
        h._empty_live_streak = 24
        assert h._idle_live_interval() == 900

    def test_finding_a_live_game_resets_the_streak(self):
        h = _LiveHost()
        h._note_live_fetch(False)
        h._note_live_fetch(False)
        assert h._empty_live_streak == 2
        h._note_live_fetch(True)
        assert h._empty_live_streak == 0

    def test_the_streak_starts_from_absent_state(self):
        # The host is not required to pre-declare _empty_live_streak.
        h = _LiveHost()
        assert not hasattr(h, "_empty_live_streak")
        h._note_live_fetch(False)
        assert h._empty_live_streak == 1
