"""src.common.sports_card_wrappers: each delegation, and the host contract.

Every method here forwards to the ``sports_card`` function it names with the
host's ``config`` and ``logger``. The tests pin what each returns for a
configured card, so a delegation that passes the wrong thing -- an empty
config, the other side, a dropped default -- fails here rather than as a
wrong colour on a panel.
"""

import ast
import logging
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import ImageFont

from src.common import sports_card, sports_card_wrappers, sports_game_renderer
from src.common.sports_card_wrappers import SportsCardWrappersMixin
from src.common.sports_game_renderer import SportsGameRendererMixin

CONFIG = {
    "timezone": "America/Chicago",
    "favorite_teams": ["BOS"],
    "scroll_card": {"vs_text": "@", "upcoming_center": "date_time",
                    "date_format": "weekday", "time_format": "24h"},
    "customization": {
        "score_text": {"text_color": [9, 9, 9]},
        "favorite_result_colors": {"enabled": True, "win_color": [0, 200, 0]},
    },
}

GAME = {"home_abbr": "BOS", "away_abbr": "NYY", "home_score": "3", "away_score": "1",
        "start_time_utc": "2026-09-19T23:00:00Z"}


class Host(SportsCardWrappersMixin):
    """The documented contract, and not one attribute more."""

    _FONT_NAME_ALIASES = dict(sports_card.FONT_NAME_ALIASES)
    _FONT_PIXEL_GRID = dict(sports_card.FONT_PIXEL_GRID)

    def __init__(self, config=CONFIG):
        self.config = config
        self.logger = logging.getLogger("test.sports_card_wrappers")
        self.fonts = {"score": ImageFont.load_default()}


class TestDelegations:
    def test_card_options(self):
        host = Host()
        assert host._scroll_card_option("vs_text", "VS") == "@"
        assert host._scroll_card_option("missing", "fallback") == "fallback"
        assert host._vs_text() == "@"
        assert host._upcoming_center_mode() == "date_time"

    def test_dates_and_times(self):
        host = Host()
        assert host._card_tzinfo() == ZoneInfo("America/Chicago")
        assert host._weekday_for(GAME) == "Sat"          # 18:00 in Chicago
        assert host._format_game_time("7:05 PM") == "19:05"
        assert host._format_game_date("9/19", GAME) == sports_card.format_game_date(
            CONFIG, host.logger, "9/19", GAME)
        assert host._format_game_date("9/19", GAME) != "9/19"

    def test_colours(self):
        host = Host()
        assert tuple(host._element_color("score_text")) == (9, 9, 9)
        assert tuple(host._element_color("missing_element", (1, 2, 3))) == (1, 2, 3)
        assert tuple(host._font_color(host.fonts["score"])) == (9, 9, 9)
        assert host._coerce_rgb([300, -1, "7"], (1, 2, 3)) == (255, 0, 7)

    def test_favourites(self):
        host = Host()
        assert host._side_is_favorite(GAME, "home", {"BOS"}) is True
        assert host._side_is_favorite(GAME, "away", {"BOS"}) is False
        assert host._side_score(GAME, "home") == 3
        assert host._favorite_result(GAME) == "win"
        assert host._recent_score_color(GAME, (1, 1, 1)) == (0, 200, 0)
        assert host._score_color_for(GAME, "recent") == (0, 200, 0)
        assert tuple(host._score_color_for(GAME, "live")) == (9, 9, 9)

    def test_fonts(self):
        host = Host()
        font = host.fonts["score"]
        unshared = host._unshare_element_fonts({"score": font, "time": font})
        assert set(unshared) == {"score", "time"}

    def test_crisp_size_uses_the_hosts_own_tables(self):
        class NoTables(Host):
            _FONT_NAME_ALIASES = {}
            _FONT_PIXEL_GRID = {}

        assert Host._crisp_size("PressStart2P-Regular.ttf", 9) == 8   # snapped
        assert NoTables._crisp_size("PressStart2P-Regular.ttf", 9) == 9  # unknown face


class TestComposition:
    def test_it_supplies_what_the_geometry_mixin_needs(self):
        # sports_game_renderer's docstring lists these as host-provided.
        for name in ("_scroll_card_option", "_upcoming_center_mode", "_vs_text",
                     "_element_color", "_format_game_date", "_format_game_time"):
            assert f"``{name}``" in sports_game_renderer.__doc__
            assert name in SportsCardWrappersMixin.__dict__

    def test_the_two_mixins_share_no_names(self):
        ours = {n for n in SportsCardWrappersMixin.__dict__ if not n.startswith("__")}
        theirs = {n for n in SportsGameRendererMixin.__dict__ if not n.startswith("__")}
        assert ours & theirs == set()

    def test_a_renderers_own_method_wins(self):
        class Renderer(SportsCardWrappersMixin, SportsGameRendererMixin):
            def _vs_text(self):
                return "v"

            def __init__(self):
                self.config, self.logger = CONFIG, logging.getLogger("t")

        assert Renderer()._vs_text() == "v"
        assert Renderer()._upcoming_center_mode() == "date_time"


# ---------------------------------------------------------------------------
# Host contract
# ---------------------------------------------------------------------------

def _self_reads():
    """Every ``self.X`` / ``cls.X`` / ``getattr(self, "X")`` the mixin reads."""
    tree = ast.parse(Path(sports_card_wrappers.__file__).read_text(encoding="utf-8"))
    cls = next(n for n in tree.body
               if isinstance(n, ast.ClassDef) and n.name == "SportsCardWrappersMixin")
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
        needed = _self_reads() - set(dir(SportsCardWrappersMixin))
        undocumented = sorted(n for n in needed if f"``{n}``" not in sports_card_wrappers.__doc__)
        assert undocumented == [], f"read but not in the host contract: {undocumented}"

    def test_the_mixin_creates_no_attributes_of_its_own(self):
        for name in ("config", "logger", "fonts", "_FONT_NAME_ALIASES", "_FONT_PIXEL_GRID"):
            assert not hasattr(SportsCardWrappersMixin, name)
