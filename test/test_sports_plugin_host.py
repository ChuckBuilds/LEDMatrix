"""src.common.sports_plugin_host: behaviour and host contract.

Ported from the scoreboards' own tests of the same methods
(test_vegas_priority_weight.py in each, test_switch_refresh_off_render_thread.py
in baseball, basketball, football, hockey, lacrosse and ufc), against a stub
host carrying only the documented contract. Each plugin's data shape is
covered: managers as attributes and in a dict (nrl, afl), favourite fighters
(ufc), team ids (nrl), cricket's nested sides, and the celebration snapshot.
"""

import ast
import threading
import time
from pathlib import Path

import pytest

from src.common import sports_plugin_host
from src.common.sports_plugin_host import SportsPluginHostMixin


class Host(SportsPluginHostMixin):
    """The documented contract, and nothing else the mixin could lean on."""

    def __init__(self, live_priority=True, live_content=True, vegas=None,
                 enabled=True, dynamic=True):
        self.has_live_priority = lambda: live_priority
        self.has_live_content = lambda: live_content
        self.global_config = {"display": {"vegas_scroll": vegas or {}}}
        self.is_enabled = enabled
        self.supports_dynamic_duration = lambda: dynamic
        self.refreshed = []
        self.release = threading.Event()
        self.release.set()

    def _ensure_manager_updated(self, manager):
        self.release.wait(5)
        self.refreshed.append(manager)


class LiveManager:
    def __init__(self, games=(), favorites=(), attr="live_games",
                 fav_attr="favorite_teams", celebrating=None):
        setattr(self, attr, list(games))
        setattr(self, fav_attr, list(favorites))
        if celebrating is not None:
            self.active_celebration = {"game": celebrating, "started_at": 0}


def _game(home="DAL", away="PHI", **extra):
    return {"home_abbr": home, "away_abbr": away, **extra}


# ---------------------------------------------------------------------------
# get_vegas_priority_weight
# ---------------------------------------------------------------------------

class TestVegasPriorityWeight:
    def test_nothing_live_has_no_opinion(self):
        assert Host(live_content=False).get_vegas_priority_weight() is None
        assert Host(live_priority=False).get_vegas_priority_weight() is None

    def test_a_live_game_without_a_favourite_gets_the_live_weight(self):
        host = Host(vegas={"live_weight": 3, "favorite_live_weight": 5})
        host.nfl_live = LiveManager([_game()], ["NYG"])
        assert host.get_vegas_priority_weight() == 3

    def test_a_favourite_playing_gets_the_favourite_weight(self):
        host = Host(vegas={"live_weight": 3, "favorite_live_weight": 7})
        host.nfl_live = LiveManager([_game()], ["dal"])
        assert host.get_vegas_priority_weight() == 7

    def test_defaults_when_the_config_says_nothing(self):
        host = Host()
        host.nfl_live = LiveManager([_game()], ["NYG"])
        assert host.get_vegas_priority_weight() == 3
        host.nfl_live.favorite_teams = ["DAL"]
        assert host.get_vegas_priority_weight() == 5

    def test_matching_ignores_case_and_space(self):
        host = Host()
        host.nfl_live = LiveManager([_game()], ["  dAl "])
        assert host.get_vegas_priority_weight() == 5

    def test_managers_inside_a_dict_are_found(self):
        """nrl and afl keep their managers in ``self._managers``."""
        host = Host()
        host._managers = {"live": LiveManager([_game()], ["PHI"])}
        assert host.get_vegas_priority_weight() == 5

    def test_a_later_manager_is_still_found(self):
        host = Host()
        host.a = LiveManager([], ["DAL"])
        host.b = LiveManager([_game()], ["DAL"])
        assert host.get_vegas_priority_weight() == 5

    def test_junk_in_the_game_list_is_skipped(self):
        host = Host()
        host.nfl_live = LiveManager(["not-a-dict", None], ["DAL"])
        assert host.get_vegas_priority_weight() == 3

    def test_an_exception_is_none_not_a_raise(self):
        host = Host()
        host.has_live_content = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
        assert host.get_vegas_priority_weight() is None


class TestFavoriteTeamIsLive:
    def test_ufc_fighters(self):
        host = Host()
        host.ufc_live = LiveManager(
            [{"fighter1_name": "Jon Jones", "fighter2_name": "Stipe Miocic"}],
            ["jon jones"], fav_attr="favorite_fighters")
        assert host._favorite_team_is_live() is True

    def test_nrl_team_ids(self):
        host = Host()
        host._managers = {"live": LiveManager([_game(home_id="17", away_id="9")], ["9"])}
        assert host._favorite_team_is_live() is True

    def test_cricket_nested_sides_match_by_substring(self):
        host = Host()
        host.cricket = LiveManager(
            [{"teams": [{"name": "India Women", "abbr": "INDW"}, "junk"]}],
            ["india"], attr="live_matches")
        assert host._favorite_team_is_live() is True

    def test_a_celebrating_game_counts_after_it_leaves_live_games(self):
        host = Host()
        host.nfl_live = LiveManager([], ["DAL"], celebrating=_game())
        assert host._favorite_team_is_live() is True

    def test_no_favourites_or_only_blank_ones(self):
        host = Host()
        host.nfl_live = LiveManager([_game()], ["", None])
        assert host._favorite_team_is_live() is False
        host.nfl_live.favorite_teams = []
        assert host._favorite_team_is_live() is False

    def test_scan_targets_walk_attributes_and_dict_values(self):
        host = Host()
        inner = object()
        host.plain = 1
        host.bag = {"x": inner}
        targets = list(host._favorite_scan_targets())
        assert 1 in targets and host.bag in targets and inner in targets


# ---------------------------------------------------------------------------
# _dispatch_switch_refresh
# ---------------------------------------------------------------------------

class TestDispatchSwitchRefresh:
    def test_runs_off_the_calling_thread(self):
        host = Host()
        host.release.clear()
        manager = object()
        started = time.monotonic()
        host._dispatch_switch_refresh(manager)
        assert time.monotonic() - started < 1.0       # did not wait on the update
        assert host.refreshed == []
        host.release.set()
        host._switch_refresh_threads[id(manager)].join(5)
        assert host.refreshed == [manager]

    def test_one_refresh_per_manager_at_a_time(self):
        host = Host()
        host.release.clear()
        manager = object()
        host._dispatch_switch_refresh(manager)
        first = host._switch_refresh_threads[id(manager)]
        host._switch_refresh_at[id(manager)] = 0.0    # past the gap, still running
        host._dispatch_switch_refresh(manager)
        assert host._switch_refresh_threads[id(manager)] is first
        host.release.set()
        first.join(5)
        assert host.refreshed == [manager]

    def test_dispatches_are_rate_limited(self):
        host = Host()
        manager = object()
        host._dispatch_switch_refresh(manager)
        host._switch_refresh_threads[id(manager)].join(5)
        host._dispatch_switch_refresh(manager)        # inside the gap
        assert host.refreshed == [manager]
        host._switch_refresh_at[id(manager)] -= host._SWITCH_REFRESH_MIN_GAP_SECONDS
        host._dispatch_switch_refresh(manager)
        host._switch_refresh_threads[id(manager)].join(5)
        assert host.refreshed == [manager, manager]

    def test_threads_are_daemons_named_for_the_manager(self):
        host = Host()

        class NFLLiveManager:
            pass

        manager = NFLLiveManager()
        host._dispatch_switch_refresh(manager)
        thread = host._switch_refresh_threads[id(manager)]
        thread.join(5)
        assert thread.daemon and thread.name == "SwitchRefresh-NFLLiveManager"

    def test_the_gap_is_a_class_setting(self):
        assert SportsPluginHostMixin._SWITCH_REFRESH_MIN_GAP_SECONDS == 5.0


# ---------------------------------------------------------------------------
# The small ones
# ---------------------------------------------------------------------------

class TestSmallHelpers:
    def test_content_type_is_multi(self):
        assert Host().get_vegas_content_type() == "multi"

    @pytest.mark.parametrize("enabled,dynamic,expected", [
        (True, True, True), (True, False, False), (False, True, False)])
    def test_dynamic_feature_enabled(self, enabled, dynamic, expected):
        assert Host(enabled=enabled, dynamic=dynamic)._dynamic_feature_enabled() is expected

    def test_total_games_takes_the_first_list(self):
        manager = type("M", (), {"live_games": None, "games_list": [1, 2],
                                 "recent_games": [1, 2, 3]})()
        assert Host._get_total_games_for_manager(manager) == 2
        assert Host._get_total_games_for_manager(None) == 0
        assert Host._get_total_games_for_manager(object()) == 0

    def test_manager_key(self):
        class NHLRecentManager:
            pass

        assert Host._build_manager_key("nhl_recent", NHLRecentManager()) == "nhl_recent:NHLRecentManager"
        assert Host._build_manager_key("nhl_recent", None) == "nhl_recent:None"


def test_it_overrides_base_plugin_when_listed_first():
    from src.plugin_system.base_plugin import BasePlugin

    class Plugin(SportsPluginHostMixin, BasePlugin):
        def update(self):
            pass

        def display(self, force_clear=False):
            pass

    assert Plugin.get_vegas_content_type is SportsPluginHostMixin.get_vegas_content_type
    assert Plugin.get_vegas_priority_weight is SportsPluginHostMixin.get_vegas_priority_weight


# ---------------------------------------------------------------------------
# Host contract
# ---------------------------------------------------------------------------

def _self_reads(module, class_name):
    """Every ``self.X`` / ``cls.X`` / ``getattr(self, "X")`` a mixin reads."""
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == class_name)
    names = set()
    for node in ast.walk(cls):
        if (isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load)
                and isinstance(node.value, ast.Name) and node.value.id in ("self", "cls")):
            names.add(node.attr)
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "getattr" and len(node.args) >= 2
                and isinstance(node.args[0], ast.Name) and node.args[0].id == "self"
                and isinstance(node.args[1], ast.Constant)):
            names.add(node.args[1].value)
    return names


class TestHostContract:
    def test_every_host_read_is_documented(self):
        needed = _self_reads(sports_plugin_host, "SportsPluginHostMixin") - set(dir(SportsPluginHostMixin))
        undocumented = sorted(n for n in needed if f"``{n}" not in sports_plugin_host.__doc__)
        assert undocumented == [], f"read but not in the host contract: {undocumented}"

    def test_the_mixin_creates_no_attributes_of_its_own(self):
        for name in ("logger", "is_enabled", "global_config", "_ensure_manager_updated",
                     "has_live_priority", "has_live_content", "supports_dynamic_duration"):
            assert not hasattr(SportsPluginHostMixin, name)
        assert "__init__" not in vars(SportsPluginHostMixin)
