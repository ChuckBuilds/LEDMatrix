"""src.common.sports_live_scroll: behaviour and host contract.

Ported from the eight scoreboards' test_live_scroll_refresh.py, against a
stub host carrying only the documented contract: what counts as a change
(the clock and the display pipeline's decoration do not), the rate limit and
its duty-cycle scaling, the marquee keeping its place across a rebuild, both
manager shapes (a league registry; afl/nrl's ``_get_manager``), and the
refresh that runs before the fingerprint.
"""

import ast
import threading
import time
from pathlib import Path

import pytest

from src.common import sports_live_scroll
from src.common.sports_live_scroll import SportsLiveScrollMixin
from src.common.sports_plugin_host import SportsPluginHostMixin

KEY = "live"


def game(gid="1", home="2", away="1", **extra):
    g = {"id": gid, "home_score": home, "away_score": away,
         "period": 3, "period_text": "3rd",
         "clock": "12:04", "status_text": "12:04 - 3rd",
         "is_final": False, "is_halftime": False,
         "home_abbr": "AAA", "away_abbr": "BBB"}
    g.update(extra)
    return g


class _Manager:
    def __init__(self, games=()):
        self.live_games = list(games)


class _Helper:
    """Stands in for ScrollHelper, including the reset that makes this hard."""

    def __init__(self):
        self.scroll_position = 0.0
        self.total_distance_scrolled = 0.0
        self.total_scroll_width = 5000
        self.scroll_complete = True

    def set_scrolling_image(self, width=5000):
        self.total_scroll_width = width
        self.scroll_position = 0.0
        self.total_distance_scrolled = 0.0
        self.scroll_complete = False


class _Log:
    def __init__(self):
        self.lines = []

    def info(self, msg, *args):
        self.lines.append(msg % args)

    def debug(self, msg, *args):
        self.lines.append(msg % args)


class Host(SportsLiveScrollMixin):
    """The documented contract (registry shape)."""

    LIVE_VOLATILE_FIELDS = frozenset({"clock", "status_text", "display_clock",
                                      "league", "status"})

    def __init__(self, games=(), helper=None, second_league_games=()):
        self._league_registry = {
            "primary": {"enabled": True, "managers": {"live": _Manager(games)}},
            "disabled": {"enabled": False,
                         "managers": {"live": _Manager(second_league_games)}},
        }
        self._live_scroll_fingerprints = {}
        self._live_scroll_rebuilt_at = {}
        self._live_scroll_rebuild_cost = {}
        self.logger = _Log()
        self.dispatched = []

        class _SM:
            def get_scroll_display(self, mode_type):
                return type("SD", (), {"scroll_helper": helper})()

        self._scroll_manager = _SM() if helper else None

    def _dispatch_switch_refresh(self, manager):
        self.dispatched.append(manager)

    def _games(self):
        return self._league_registry["primary"]["managers"]["live"].live_games

    def _set(self, games):
        self._league_registry["primary"]["managers"]["live"].live_games = list(games)


def fresh(games=(), **kw):
    host = Host(games, **kw)
    host._note_live_scroll_built(KEY, "live", host._live_scroll_fingerprint())
    host._live_scroll_rebuilt_at[KEY] = 0.0        # past the rate-limit floor
    return host


class TestManagers:
    def test_the_enabled_league_only(self):
        host = Host([game()], second_league_games=[game(gid="9")])
        assert [m.live_games for m in host._live_scroll_managers()] == [host._games()]

    def test_one_league_by_name(self):
        host = Host([game()])
        assert len(host._live_scroll_managers("primary")) == 1
        assert host._live_scroll_managers("nope") == []

    def test_the_single_league_shape(self):
        host = Host([game()])
        host._league_registry = None
        only = _Manager([game()])
        host._get_manager = lambda mode: only if mode == "live" else None
        assert host._live_scroll_managers() == [only]

    def test_a_failing_accessor_is_no_managers(self):
        host = Host()
        host._league_registry = {}

        def broken(mode):
            raise KeyError(mode)

        host._get_manager = broken
        assert host._live_scroll_managers() == []

    def test_neither_shape_is_inert(self):
        host = Host()
        host._league_registry = None
        assert host._live_scroll_managers() == []


class TestWhatCountsAsAChange:
    def test_nothing_changed(self):
        assert not fresh([game()])._live_scroll_needs_rebuild(KEY, "live")

    def test_the_clock_ticking_is_not_a_rebuild(self):
        host = fresh([game()])
        host._set([game(clock="11:58", status_text="11:58 - 3rd")])
        assert not host._live_scroll_needs_rebuild(KEY, "live")

    @pytest.mark.parametrize("change", [
        {"home": "3"}, {"period_text": "OT", "period": 4}, {"is_final": True},
        {"is_halftime": True}, {"situation": "power play"},
        {"some_new_field_a_card_draws": "x"}])
    def test_anything_else_is(self, change):
        host = fresh([game()])
        host._set([game(**change)])
        assert host._live_scroll_needs_rebuild(KEY, "live")

    def test_a_second_game_going_live(self):
        host = fresh([game()])
        host._set([game(), game(gid="2")])
        assert host._live_scroll_needs_rebuild(KEY, "live")

    def test_the_pipelines_decoration_is_not_a_change(self):
        host = fresh([game()])
        host._set([dict(game(), league="nhl", status={"state": "in"})])
        assert not host._live_scroll_needs_rebuild(KEY, "live")
        host = fresh([dict(game(), league="nhl", status={"state": "in"})])
        host._set([game()])
        assert not host._live_scroll_needs_rebuild(KEY, "live")

    def test_the_hosts_volatile_fields_are_the_ones_read(self):
        """afl, nrl and soccer also ignore period_text; that stays theirs."""

        class ClockInLabel(Host):
            LIVE_VOLATILE_FIELDS = Host.LIVE_VOLATILE_FIELDS | {"period_text"}

        host = ClockInLabel([game()])
        host._note_live_scroll_built(KEY, "live")
        host._live_scroll_rebuilt_at[KEY] = 0.0
        host._set([game(period_text="3rd 11:58")])
        assert not host._live_scroll_needs_rebuild(KEY, "live")

    @pytest.mark.parametrize("mode", ["recent", "upcoming"])
    def test_other_modes_never_rebuild(self, mode):
        host = fresh([game()])
        host._set([game(home="5")])
        assert not host._live_scroll_needs_rebuild(KEY, mode)

    def test_a_first_build_is_not_a_change(self):
        assert not Host([game()])._live_scroll_needs_rebuild(KEY, "live")

    def test_a_non_dict_game_still_fingerprints(self):
        assert Host._fingerprint_games(["odd", None]) == tuple(sorted(
            ((("<not-a-dict>", "odd"),), (("<not-a-dict>", "None"),))))


class TestRateLimit:
    def test_a_change_inside_the_floor_is_deferred_not_lost(self):
        host = Host([game()])
        host._note_live_scroll_built(KEY, "live", host._live_scroll_fingerprint())
        host._set([game(home="3")])
        assert not host._live_scroll_needs_rebuild(KEY, "live")
        host._live_scroll_rebuilt_at[KEY] = 0.0
        assert host._live_scroll_needs_rebuild(KEY, "live")

    def test_an_expensive_rebuild_raises_the_floor(self):
        host = fresh([game()])
        host._live_scroll_rebuild_cost[KEY] = 0.463      # 0.463 x 20 = 9.3s
        host._live_scroll_rebuilt_at[KEY] = time.time() - 6.0
        host._set([game(home="9")])
        assert not host._live_scroll_needs_rebuild(KEY, "live")
        host._live_scroll_rebuilt_at[KEY] = time.time() - 10.0
        assert host._live_scroll_needs_rebuild(KEY, "live")

    def test_a_cheap_rebuild_stays_on_the_minimum(self):
        host = fresh([game()])
        host._live_scroll_rebuild_cost[KEY] = 0.029
        host._live_scroll_rebuilt_at[KEY] = time.time() - 6.0
        host._set([game(home="9")])
        assert host._live_scroll_needs_rebuild(KEY, "live")

    def test_the_constants(self):
        assert SportsLiveScrollMixin.LIVE_SCROLL_REBUILD_MIN_SECONDS == 5.0
        assert SportsLiveScrollMixin.LIVE_SCROLL_REBUILD_DUTY_DIVISOR == 20.0

    def test_noting_a_non_live_build_records_nothing(self):
        host = Host([game()])
        host._note_live_scroll_built(KEY, "recent")
        assert host._live_scroll_fingerprints == {} and host._live_scroll_rebuilt_at == {}


class TestPreservingScrollPosition:
    def test_position_and_progress_survive(self):
        helper = _Helper()
        host = Host([game()], helper=helper)
        helper.scroll_position = helper.total_distance_scrolled = 812.0
        with host._preserving_scroll_position("live", active=True):
            helper.set_scrolling_image()
        assert helper.scroll_position == 812.0
        assert helper.total_distance_scrolled == 812.0
        assert helper.scroll_complete is False
        assert any("rebuilt the live strip in place at position 812" in line
                   for line in host.logger.lines)

    def test_clamped_to_a_shorter_strip(self):
        helper = _Helper()
        host = Host([game()], helper=helper)
        helper.scroll_position = 1300.0
        with host._preserving_scroll_position("live", active=True):
            helper.set_scrolling_image(width=1200)
        assert helper.scroll_position == 1199

    def test_a_first_build_starts_at_zero(self):
        helper = _Helper()
        host = Host([game()], helper=helper)
        helper.scroll_position = 500.0
        with host._preserving_scroll_position("live", active=False):
            helper.set_scrolling_image()
        assert helper.scroll_position == 0.0

    def test_no_scroll_manager_is_survivable_and_still_costed(self):
        host = Host([game()], helper=None)
        with host._preserving_scroll_position("live", active=True, scroll_key="nhl_live"):
            pass
        assert "nhl_live" in host._live_scroll_rebuild_cost

    def test_the_cost_is_keyed_by_mode_without_a_scroll_key(self):
        host = Host([game()])
        with host._preserving_scroll_position("live", active=False):
            pass
        assert set(host._live_scroll_rebuild_cost) == {"live"}


class TestRefresh:
    def test_every_live_manager_is_dispatched(self):
        host = Host([game()])
        host._refresh_live_scroll_managers()
        assert host.dispatched == [host._league_registry["primary"]["managers"]["live"]]

    def test_a_dispatch_error_is_logged_not_raised(self):
        host = Host([game()])

        def broken(manager):
            raise RuntimeError("can't start new thread")

        host._dispatch_switch_refresh = broken
        host._refresh_live_scroll_managers()
        assert any("Live scroll refresh skipped" in line for line in host.logger.lines)

    def test_with_the_host_mixin_it_runs_off_thread(self):
        """The real pairing: _dispatch_switch_refresh from sports_plugin_host."""

        class Plugin(SportsPluginHostMixin, SportsLiveScrollMixin):
            LIVE_VOLATILE_FIELDS = Host.LIVE_VOLATILE_FIELDS

            def __init__(self):
                self.manager = _Manager([game()])
                self._league_registry = {"a": {"enabled": True,
                                               "managers": {"live": self.manager}}}
                self.logger = _Log()
                self.updated = threading.Event()

            def _ensure_manager_updated(self, manager):
                self.updated.set()

        plugin = Plugin()
        plugin._refresh_live_scroll_managers()
        assert plugin.updated.wait(5)


# ---------------------------------------------------------------------------
# Host contract
# ---------------------------------------------------------------------------

def _self_reads():
    tree = ast.parse(Path(sports_live_scroll.__file__).read_text(encoding="utf-8"))
    cls = next(n for n in tree.body
               if isinstance(n, ast.ClassDef) and n.name == "SportsLiveScrollMixin")
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
        needed = _self_reads() - set(dir(SportsLiveScrollMixin))
        undocumented = sorted(n for n in needed if f"``{n}" not in sports_live_scroll.__doc__)
        assert undocumented == [], f"read but not in the host contract: {undocumented}"

    def test_the_mixin_creates_no_attributes_of_its_own(self):
        for name in ("logger", "LIVE_VOLATILE_FIELDS", "_live_scroll_fingerprints",
                     "_live_scroll_rebuilt_at", "_live_scroll_rebuild_cost",
                     "_dispatch_switch_refresh"):
            assert not hasattr(SportsLiveScrollMixin, name)
        assert "__init__" not in vars(SportsLiveScrollMixin)

    def test_no_name_in_common_with_the_host_mixin(self):
        ours = {n for n in vars(SportsLiveScrollMixin) if not n.startswith("__")}
        theirs = {n for n in vars(SportsPluginHostMixin) if not n.startswith("__")}
        assert ours & theirs == set()
