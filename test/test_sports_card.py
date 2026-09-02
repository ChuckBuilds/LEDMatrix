"""The card helpers the eight scoreboards now share.

These bodies lived in eight byte-identical copies. Moving them here means one
fix reaches every scoreboard — and that a mistake does too, which is what this
file guards. Each case below is one the plugins' own code already handled; the
point is that it keeps handling it.

The functions take ``config``/``logger``/``fonts`` as arguments rather than
reading them off an instance, so a plugin keeps its method and delegates the
body. That is what let all eight adopt this with byte-identical renders.
"""

import logging
import json
import os

import pytest

from src.common import sports_card as C


@pytest.fixture
def log():
    return logging.getLogger("test_sports_card")


class TestSettingsLookup:
    def test_reads_the_scroll_card_block(self):
        cfg = {"scroll_card": {"vs_text": "@"}}
        assert C.scroll_card_option(cfg, "vs_text", "VS") == "@"

    @pytest.mark.parametrize("cfg", [None, {}, {"scroll_card": None},
                                     {"scroll_card": {"vs_text": None}}])
    def test_missing_or_null_falls_back(self, cfg):
        """A null in config means "unset", not "empty string"."""
        assert C.scroll_card_option(cfg, "vs_text", "VS") == "VS"

    def test_upcoming_center_rejects_unknown_modes(self):
        for bad in ("sideways", "", None, 7):
            assert C.upcoming_center_mode({"scroll_card": {"upcoming_center": bad}}) == "vs"
        assert C.upcoming_center_mode({"scroll_card": {"upcoming_center": "DATE_TIME"}}) == "date_time"


class TestColour:
    def test_rgb_list_and_hex_both_work(self):
        assert C.element_color({"customization": {"score_text": {"text_color": [1, 2, 3]}}},
                               "score_text") == (1, 2, 3)
        assert C.element_color({"customization": {"score_text": {"text_color": "#ff8000"}}},
                               "score_text") == (255, 128, 0)

    @pytest.mark.parametrize("value", ["nope", "#fff", [1, 2], None, ["a", "b", "c"]])
    def test_unusable_colour_falls_back(self, value):
        cfg = {"customization": {"score_text": {"text_color": value}}}
        assert C.element_color(cfg, "score_text", (9, 9, 9)) == (9, 9, 9)

    def test_coerce_rgb_clamps_rather_than_rejecting(self):
        assert C.coerce_rgb([300, -5, 20], (0, 0, 0)) == (255, 0, 20)

    def test_coerce_rgb_refuses_a_three_character_string(self):
        """"123" would otherwise iterate into three digits and yield a colour."""
        assert C.coerce_rgb("123", (7, 7, 7)) == (7, 7, 7)

    def test_font_colour_is_resolved_by_identity(self):
        a, b = object(), object()
        cfg = {"customization": {"score_text": {"text_color": [4, 5, 6]}}}
        assert C.font_color(cfg, {"score": a, "team": b}, a) == (4, 5, 6)

    def test_a_shared_face_gives_up_rather_than_guessing(self):
        """One object used for two elements has no single right colour."""
        shared = object()
        cfg = {"customization": {"score_text": {"text_color": [4, 5, 6]}}}
        assert C.font_color(cfg, {"score": shared, "team": shared}, shared) == (255, 255, 255)


class TestFavourites:
    GAME = {"home_abbr": "TB", "away_abbr": "NO", "home_score": "21", "away_score": "17"}

    def test_win_loss_and_tie(self):
        cfg = {"favorite_teams": ["TB"]}
        assert C.favorite_result(cfg, self.GAME) == "win"
        assert C.favorite_result({"favorite_teams": ["NO"]}, self.GAME) == "loss"
        tied = dict(self.GAME, home_score="3", away_score="3")
        assert C.favorite_result(cfg, tied) == "tie"

    def test_no_verdict_without_exactly_one_favourite_side(self):
        assert C.favorite_result({}, self.GAME) is None
        assert C.favorite_result({"favorite_teams": ["TB", "NO"]}, self.GAME) is None
        assert C.favorite_result({"favorite_teams": ["SEA"]}, self.GAME) is None

    def test_unusable_scores_give_no_verdict(self):
        bad = dict(self.GAME, home_score="x")
        assert C.favorite_result({"favorite_teams": ["TB"]}, bad) is None

    def test_nested_payload_shape_is_read_too(self):
        game = {"home_team": {"abbrev": "TB", "score": 9},
                "away_team": {"abbrev": "NO", "score": 2}}
        assert C.side_score(game, "home") == 9
        assert C.side_is_favorite(game, "home", {"TB"}) is True

    def test_matches_on_id_where_abbreviations_collide(self):
        """NRL keys favourites by ESPN id; abbreviations are not unique there."""
        game = {"home_abbr": "SYD", "home_id": "4321"}
        assert C.side_is_favorite(game, "home", {"4321"}) is True

    def test_game_and_config_favourites_are_both_used(self):
        """Games carry resolved dynamic groups; config catches later edits."""
        game = dict(self.GAME, favorite_teams=["NO"], league="nfl")
        assert set(C.favorite_teams_for({"nfl": {"favorite_teams": ["TB"]}}, game)) == {"NO", "TB"}


class TestDateAndTime:
    def test_date_formats(self, log):
        for fmt, want in [("abbrev", "Sep 5"), ("numeric", "9/5"),
                          ("day_first", "5 Sep"), ("numeric_day_first", "5/9")]:
            cfg = {"scroll_card": {"date_format": fmt}}
            assert C.format_game_date(cfg, log, "9/5") == want

    @pytest.mark.parametrize("raw", ["", "garbage", "13/40", "no/slash/here"])
    def test_unparseable_dates_pass_through(self, log, raw):
        assert C.format_game_date({}, log, raw) == raw.strip()

    def test_24h_conversion(self):
        cfg = {"scroll_card": {"time_format": "24h"}}
        assert C.format_game_time(cfg, "7:30 PM") == "19:30"
        assert C.format_game_time(cfg, "12:00 AM") == "00:00"
        assert C.format_game_time(cfg, "12:15 PM") == "12:15"

    def test_12h_is_left_alone_and_junk_survives(self):
        assert C.format_game_time({}, "7:30 PM") == "7:30 PM"
        assert C.format_game_time({"scroll_card": {"time_format": "24h"}}, "soon") == "soon"

    def test_a_bad_timezone_falls_back_to_utc(self, log):
        """A typo in config should not blank the card."""
        from datetime import timezone
        assert C.card_tzinfo({"timezone": "Not/AZone"}, log) is timezone.utc


class TestFontSizing:
    def test_snaps_to_the_faces_pixel_grid(self):
        assert C.crisp_size("4x6-font.ttf", 6) == 7        # 7px grid
        assert C.crisp_size("PressStart2P-Regular.ttf", 10) == 8
        assert C.crisp_size("PressStart2P-Regular.ttf", 13) == 16

    def test_an_unknown_face_is_never_second_guessed(self):
        assert C.crisp_size("SomeUserFont.ttf", 11) == 11

    def test_aliases_resolve_before_the_grid_lookup(self):
        assert C.crisp_size("four_by_six", 6) == C.crisp_size("4x6-font.ttf", 6)

    @pytest.mark.parametrize("desired", [0, -3, None])
    def test_unusable_sizes_pass_through_without_raising(self, desired):
        """None reached this in the field; football's variant raised TypeError."""
        assert C.crisp_size("4x6-font.ttf", desired) == desired

    def test_schema_cache_is_keyed_per_schema_not_globally(self, tmp_path):
        """Two plugins declaring different defaults must not share an answer."""
        a, b = tmp_path / "a.json", tmp_path / "b.json"
        for path, size in ((a, 11), (b, 22)):
            path.write_text(json.dumps({"properties": {"customization": {"properties": {
                "score_text": {"properties": {"font_size": {"default": size}}}}}}}))
        assert C.schema_font_size(str(a), "score_text") == 11
        assert C.schema_font_size(str(b), "score_text") == 22

    def test_a_missing_schema_is_not_an_error(self, tmp_path):
        assert C.schema_font_size(str(tmp_path / "nope.json"), "score_text") is None

    def test_a_configured_size_matching_the_schema_default_is_not_a_choice(self, tmp_path):
        """The web UI writes the whole default block on every save, so
        font_size == schema default carries no intent and must not pin the
        install to an off-grid size forever."""
        schema = tmp_path / "s.json"
        schema.write_text(json.dumps({"properties": {"customization": {"properties": {
            "score_text": {"properties": {"font_size": {"default": 10}}}}}}}))
        got = C.resolve_font_size(str(schema), {"font_size": 10}, "score_text", 10,
                                  "PressStart2P-Regular.ttf")
        assert got == 8, "a default-valued size should snap to the grid"

    def test_a_real_choice_wins(self, tmp_path):
        schema = tmp_path / "s.json"
        schema.write_text(json.dumps({"properties": {"customization": {"properties": {
            "score_text": {"properties": {"font_size": {"default": 10}}}}}}}))
        got = C.resolve_font_size(str(schema), {"font_size": 13}, "score_text", 10,
                                  "PressStart2P-Regular.ttf")
        assert got == 13, "an explicit size the user chose is not second-guessed"


class TestTables:
    def test_every_font_key_maps_to_an_element(self):
        assert set(C.ELEMENT_FOR_FONT) == {"score", "time", "team", "status", "detail", "rank"}

    def test_result_colours_cover_every_verdict(self):
        assert set(C.FAVORITE_RESULT_COLOR_DEFAULTS) == {"win", "loss", "tie"}

    def test_month_and_weekday_tables_are_complete(self):
        assert len(C.MONTH_ABBR) == 12 and len(C.WEEKDAY_ABBR) == 7
