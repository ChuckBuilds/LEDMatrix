"""The shared card geometry, exercised against a host that supplies only what
the mixin's contract names.

The point of these is the contract, not the arithmetic. The mixin reaches for
``display_width``, ``fonts``, ``config`` and six ``sports_card`` delegations
through ``self``, and the eight plugins are what actually provide them. A
stub host that provides exactly the documented surface and nothing else is
what catches the mixin quietly growing a dependency the plugins do not have.
"""

import pytest
from PIL import Image, ImageDraw, ImageFont

from src.common.sports_game_renderer import SportsGameRendererMixin


class Host(SportsGameRendererMixin):
    """The documented contract, and not one attribute more."""

    def __init__(self, width=128, height=32, config=None, scroll=None):
        self.display_width = width
        self.display_height = height
        self.config = config or {}
        self.logger = _Logger()
        self._team_rankings_cache = {}
        self._scroll = scroll or {}
        font = ImageFont.load_default()
        self.fonts = {'score': font, 'time': font, 'detail': font}
        self.drawn = []

    # -- the six sports_card delegations the mixin calls --
    def _scroll_card_option(self, key, default=None):
        return self._scroll.get(key, default)

    def _upcoming_center_mode(self):
        return self._scroll.get('upcoming_center', 'vs')

    def _vs_text(self):
        return self._scroll.get('vs_text', 'VS')

    def _element_color(self, element):
        return (255, 255, 255)

    def _format_game_date(self, raw, game):
        return raw

    def _format_game_time(self, raw):
        return raw

    # -- the one hook whose body genuinely varies per plugin --
    def _draw_text_with_outline(self, draw, text, position, font,
                                fill=None, outline_color=(0, 0, 0)):
        self.drawn.append((text, position))


class _Logger:
    def debug(self, *a, **k):
        pass


def _draw():
    return ImageDraw.Draw(Image.new("RGB", (256, 64)))


class TestCenterGap:
    def test_explicit_center_gap_wins_outright(self):
        assert Host(scroll={'center_gap': 31})._center_gap_width() == 31

    def test_explicit_zero_restores_edge_to_edge_logos(self):
        # 0 is a real setting, not a falsy miss -- the guard is `>= 0`.
        assert Host(scroll={'center_gap': 0})._center_gap_width() == 0

    def test_otherwise_it_scales_with_card_width_within_the_clamp(self):
        h = Host(width=512)
        # 512 * 0.28 = 143, clamped to the 40px ceiling.
        assert h._center_gap_width() >= h.CENTER_GAP_MIN_PX

    def test_the_gap_never_ends_up_narrower_than_the_score(self):
        # This is the bug the measurement exists to prevent: a derived gap
        # smaller than the rendered score drew the score over the logos.
        h = Host(width=64)
        assert h._center_gap_width() >= h._score_reserve_width()

    def test_a_junk_ratio_falls_back_to_the_floor(self):
        h = Host(scroll={'center_gap_ratio': 'wide'})
        assert h._center_gap_width() == h.CENTER_GAP_MIN_PX


class TestNonFiniteSettings:
    """inf reaches int() and raises OverflowError, which the old
    `except (TypeError, ValueError)` did not catch -- so one bad config value
    aborted the entire card render rather than falling back."""

    @pytest.mark.parametrize("bad", [float("inf"), float("-inf")])
    def test_a_non_finite_center_gap_falls_back(self, bad):
        h = Host(scroll={'center_gap': bad})
        assert h._center_gap_width() >= h.CENTER_GAP_MIN_PX

    @pytest.mark.parametrize("bad", [float("inf"), float("-inf"), float("nan")])
    def test_a_non_finite_ratio_falls_back_to_the_floor(self, bad):
        h = Host(scroll={'center_gap_ratio': bad})
        assert h._center_gap_width() == h.CENTER_GAP_MIN_PX

    @pytest.mark.parametrize("bad", [float("inf"), float("-inf")])
    def test_non_finite_clamp_bounds_fall_back(self, bad):
        h = Host(scroll={'center_gap_min': bad, 'center_gap_max': bad})
        assert h._center_gap_width() == h.CENTER_GAP_MIN_PX

    @pytest.mark.parametrize("bad", [float("inf"), float("-inf"), "inf", "-inf", "nan"])
    def test_a_non_finite_layout_offset_gives_the_default(self, bad):
        cfg = {'customization': {'layout': {'score': {'x_offset': bad}}}}
        assert Host(config=cfg)._layout_offset('score', 'x_offset', 7) == 7

    def test_a_finite_value_is_still_honoured(self):
        # The guard must not swallow ordinary settings.
        assert Host(scroll={'center_gap': 31})._center_gap_width() == 31
        cfg = {'customization': {'layout': {'score': {'x_offset': -3}}}}
        assert Host(config=cfg)._layout_offset('score', 'x_offset', 7) == -3


class TestScoreReserve:
    def test_it_measures_the_probe_plus_both_gutters(self):
        h = Host()
        assert h._score_reserve_width() > 2 * h._SCORE_LOGO_GUTTER_PX

    def test_a_wider_probe_reserves_more(self):
        class Wide(Host):
            _SCORE_PROBE = "000-000"
        assert Wide()._score_reserve_width() > Host()._score_reserve_width()

    def test_an_unmeasurable_font_reserves_nothing_rather_than_raising(self):
        h = Host()
        h.fonts = {'score': object()}
        assert h._score_reserve_width() == 0


class TestLogoSlot:
    def test_the_slot_is_what_is_left_after_the_gap(self):
        h = Host(width=128, scroll={'center_gap': 40})
        assert h._logo_slot_width() == 44

    def test_it_is_not_capped_at_the_card_height(self):
        # The height cap is what froze logos at 46px on a 128px card.
        h = Host(width=512, height=32, scroll={'center_gap': 40})
        assert h._logo_slot_width() > h.display_height

    def test_a_gap_wider_than_the_card_still_leaves_a_usable_slot(self):
        assert Host(width=64, scroll={'center_gap': 200})._logo_slot_width() == 8

    def test_the_cache_key_is_scoped_to_the_slot_not_just_the_name(self):
        # One cache dict is shared by renderers of different card widths.
        narrow = Host(width=64, scroll={'center_gap': 20})._logo_cache_key("NYY")
        wide = Host(width=256, scroll={'center_gap': 20})._logo_cache_key("NYY")
        assert narrow != wide


class TestLayoutOffset:
    def _host(self, value):
        return Host(config={'customization': {'layout': {'score': {'x_offset': value}}}})

    def test_it_reads_the_same_block_as_the_full_screen_scorebug(self):
        assert self._host(5)._layout_offset('score', 'x_offset') == 5

    def test_a_string_offset_from_the_web_ui_is_coerced(self):
        assert self._host("-3")._layout_offset('score', 'x_offset') == -3

    def test_a_bool_is_not_silently_an_offset_of_one(self):
        assert self._host(True)._layout_offset('score', 'x_offset', 9) == 9

    @pytest.mark.parametrize("cfg", [{}, {'customization': {}},
                                     {'customization': {'layout': {}}}])
    def test_a_missing_block_gives_the_default(self, cfg):
        assert Host(config=cfg)._layout_offset('score', 'x_offset', 7) == 7

    def test_an_unparseable_offset_gives_the_default(self):
        assert self._host("left")._layout_offset('score', 'x_offset', 4) == 4


class TestUpcomingCenter:
    def test_none_draws_nothing(self):
        h = Host(scroll={'upcoming_center': 'none'})
        h._draw_upcoming_center(_draw(), {})
        assert h.drawn == []

    def test_vs_is_the_default_and_never_a_score(self):
        # An upcoming game has not started; the extractor's 0-0 is noise.
        h = Host()
        h._draw_upcoming_center(_draw(), {'home_score': 0, 'away_score': 0})
        assert [t for t, _ in h.drawn] == ['VS']

    def test_an_empty_vs_string_draws_nothing(self):
        h = Host(scroll={'vs_text': ''})
        h._draw_upcoming_center(_draw(), {})
        assert h.drawn == []

    def test_date_time_stacks_both_lines(self):
        h = Host(scroll={'upcoming_center': 'date_time'})
        h._draw_upcoming_center(_draw(), {'game_date': 'Sep 19', 'game_time': '7:00 PM'})
        assert [t for t, _ in h.drawn] == ['Sep 19', '7:00 PM']

    def test_hiding_both_lines_draws_nothing(self):
        h = Host(scroll={'upcoming_center': 'date_time',
                         'show_date': False, 'show_time': False})
        h._draw_upcoming_center(_draw(), {'game_date': 'Sep 19', 'game_time': '7:00 PM'})
        assert h.drawn == []


class TestUpcomingStatus:
    def test_time_on_top_and_date_below_by_default(self):
        h = Host()
        h._draw_upcoming_game_status(_draw(), {'game_date': 'Sep 19', 'game_time': '7:00 PM'})
        assert [t for t, _ in h.drawn] == ['7:00 PM', 'Sep 19']

    def test_swap_date_time_reverses_them(self):
        h = Host(scroll={'swap_date_time': True})
        h._draw_upcoming_game_status(_draw(), {'game_date': 'Sep 19', 'game_time': '7:00 PM'})
        assert [t for t, _ in h.drawn] == ['Sep 19', '7:00 PM']

    def test_it_stays_out_of_the_way_when_the_centre_already_has_them(self):
        # Otherwise the date and time print twice on the same card.
        h = Host(scroll={'upcoming_center': 'date_time'})
        h._draw_upcoming_game_status(_draw(), {'game_date': 'Sep 19', 'game_time': '7:00 PM'})
        assert h.drawn == []

    def test_the_bottom_line_is_measured_not_a_fixed_offset(self):
        # A fixed -7 ran "Sep 19" past the card wherever the detail font is
        # 10px rather than 6px.
        h = Host(height=64)
        h._draw_upcoming_game_status(_draw(), {'game_date': 'Sep 19', 'game_time': '7:00 PM'})
        bottom_y = h.drawn[1][1][1]
        assert 0 <= bottom_y < h.display_height


class TestRankings:
    def test_set_rankings_cache_replaces_the_cache(self):
        h = Host()
        h.set_rankings_cache({'UGA': 1})
        assert h._team_rankings_cache == {'UGA': 1}


class TestContract:
    def test_the_mixin_carries_no_state_of_its_own(self):
        # Adoption must be one line on the class statement; a mixin with an
        # __init__ would force eight constructors to cooperate.
        assert '__init__' not in SportsGameRendererMixin.__dict__

    def test_a_host_providing_the_documented_surface_needs_nothing_more(self):
        # Host defines exactly what the module docstring names. If the mixin
        # grows a new self.* dependency, this is what fails.
        h = Host()
        h._center_gap_width()
        h._logo_slot_width()
        h._logo_cache_key("X")
        h._layout_offset('score', 'x_offset')
        h._upcoming_date_and_time({})
        h._draw_upcoming_center(_draw(), {})
        h._draw_upcoming_game_status(_draw(), {})
        h.set_rankings_cache({})
