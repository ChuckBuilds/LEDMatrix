"""The scroll/Vegas card geometry the sports scoreboards all share.

Eight scoreboards -- afl, baseball, basketball, football, hockey, lacrosse,
nrl and soccer -- each carried their own ``game_renderer.py``. After the card
helpers moved to ``src/common/sports_card.py`` the settings lookups were
shared, but the *geometry* was not: nine methods that decide how wide the
centre gap is, how much room a logo gets, and where an upcoming card's date
and time land were still eight separate copies. Five were byte-identical
across all eight plugins; the other four were identical in seven, with a
different single outlier each time.

That last detail is why this is a mixin rather than free functions. There is
no per-sport branching to write -- baseball needs its own
``_draw_upcoming_game_status`` and ``_logo_slot_width``, hockey its own
``_upcoming_date_and_time``, football its own ``_score_reserve_width``, and
every one of those is an ordinary override. The seven that agree inherit and
say nothing.

It is deliberately *only* a mixin: no ``__init__``, no state of its own. The
plugins' constructors differ in six ways and none of that difference is worth
unifying, so adoption is one line on the class statement plus deleting the
methods that now come from here.

What a host class must provide
------------------------------
Attributes: ``display_width``, ``display_height``, ``config``, ``fonts``,
``logger``, ``_team_rankings_cache``.

Methods: ``_draw_text_with_outline(draw, text, position, font, fill=None,
outline_color=(0, 0, 0))`` -- the one hook whose body genuinely varies -- plus
the ``sports_card`` delegations ``_scroll_card_option``,
``_upcoming_center_mode``, ``_vs_text``, ``_element_color``,
``_format_game_date`` and ``_format_game_time``.
"""

from typing import ClassVar, Dict, Tuple

from PIL import Image, ImageDraw


class SportsGameRendererMixin:
    """Shared card geometry for the sports scoreboards. See module docstring."""

    #: Centre strip as a fraction of card width, before clamping.
    CENTER_GAP_RATIO: ClassVar[float] = 0.28
    #: Clamp floor for the derived centre gap.
    CENTER_GAP_MIN_PX: ClassVar[int] = 22
    #: Clamp ceiling for the derived centre gap.
    CENTER_GAP_MAX_PX: ClassVar[int] = 40
    #: Breathing room kept between the score and each logo.
    _SCORE_LOGO_GUTTER_PX: ClassVar[int] = 4
    #: Widest score the centre strip must fit. Leagues that can reach three
    #: digits a side override this with "000-000".
    _SCORE_PROBE: ClassVar[str] = "00-00"

    # ---- geometry ------------------------------------------------------

    def _score_reserve_width(self) -> int:
        """Centre strip the score actually needs, measured rather than assumed.

        The gap was derived from the card width alone (width x
        CENTER_GAP_RATIO, clamped to CENTER_GAP_MAX_PX) while the score's size
        comes from config and the element-style resolver. Nothing compared the
        two, so any score wider than the clamp was drawn over the logos.
        Measuring it keeps the strip wide enough for whatever font is in play.
        """
        try:
            probe = ImageDraw.Draw(Image.new("RGB", (4, 4)))
            width = probe.textlength(self._SCORE_PROBE, font=self.fonts['score'])
            return int(width) + 2 * self._SCORE_LOGO_GUTTER_PX
        except Exception:
            self.logger.debug("Score reserve measurement failed", exc_info=True)
            return 0

    def _center_gap_width(self) -> int:
        """Width of the middle strip kept clear of logos.

        ``scroll_card.center_gap`` pins it outright; otherwise it scales with
        the card width between the configurable min and max. 0 restores
        edge-to-edge logos.
        """
        configured = self._scroll_card_option("center_gap")
        if isinstance(configured, (int, float)) and configured >= 0:
            return int(configured)
        ratio = self._scroll_card_option("center_gap_ratio", self.CENTER_GAP_RATIO)
        low = self._scroll_card_option("center_gap_min", self.CENTER_GAP_MIN_PX)
        high = self._scroll_card_option("center_gap_max", self.CENTER_GAP_MAX_PX)
        try:
            scaled = round(self.display_width * float(ratio))
            derived = int(max(int(low), min(int(high), scaled)))
            # A strip narrower than the score is the bug, not a style choice.
            # An explicit ``center_gap`` is still honoured above, including 0.
            return max(derived, self._score_reserve_width())
        except (TypeError, ValueError):
            return self.CENTER_GAP_MIN_PX

    def _logo_slot_width(self) -> int:
        """Per-side logo slot, leaving the center gap clear.

        No longer capped at display_height: the card is sized as two
        full-height logos plus the measured gap, so what is left after the gap
        is exactly the logo's share. The cap was what froze the logos at 46px
        on the old flat 128px card.
        """
        available = (self.display_width - self._center_gap_width()) // 2
        return max(8, available)

    def _logo_cache_key(self, name: str) -> str:
        """Cache key scoped to the logo slot.

        One cache dict is shared by renderers built for different card widths,
        so a logo sized for a wide slot must not be handed to a narrow one.
        """
        return f"{name}@{self._logo_slot_width()}x{self.display_height}"

    def _layout_offset(self, element: str, axis: str, default: int = 0) -> int:
        """X/Y nudge for one element, from customization.layout.

        Same block the full-screen scorebug reads (sports.py
        _get_layout_offset), so a nudge configured in the web UI now moves
        the element on the scroll/Vegas card too -- previously the schema
        advertised these offsets but this renderer ignored them.
        """
        try:
            layout = (self.config or {}).get("customization", {}).get("layout", {})
            value = (layout.get(element) or {}).get(axis, default)
            if isinstance(value, bool):
                return default
            if isinstance(value, (int, float)):
                return int(value)
            if isinstance(value, str):
                return int(float(value))
        except (TypeError, ValueError):
            pass
        return default

    # ---- upcoming cards ------------------------------------------------

    def _upcoming_date_and_time(self, game: Dict) -> Tuple[str, str]:
        """(date, time) for an upcoming card, from the extractor's flat keys."""
        return (
            str(game.get("game_date", "") or ""),
            str(game.get("game_time", "") or ""),
        )

    def _draw_upcoming_center(self, draw: "ImageDraw.ImageDraw", game: Dict) -> None:
        """Draw the middle of an upcoming card.

        Never a score: an upcoming game has not started, so the extractor's
        0-0 is noise. Either the VS text (default), the date and time stacked,
        or nothing at all.
        """
        mode = self._upcoming_center_mode()
        if mode == "none":
            return

        if mode == "vs":
            vs_text = self._vs_text()
            if not vs_text:
                return
            vs_width = draw.textlength(vs_text, font=self.fonts['score'])
            vs_x = (self.display_width - vs_width) // 2 + self._layout_offset('score', 'x_offset')
            vs_y = (self.display_height // 2) - 3 + self._layout_offset('score', 'y_offset')
            self._draw_text_with_outline(
                draw, vs_text, (vs_x, vs_y), self.fonts['score'],
                fill=self._element_color('score_text')
            )
            return

        date_text, time_text = self._upcoming_date_and_time(game)
        lines = []
        if self._scroll_card_option("show_date", True):
            lines.append(self._format_game_date(date_text, game))
        if self._scroll_card_option("show_time", True):
            lines.append(self._format_game_time(time_text))
        lines = [t for t in lines if t]
        if not lines:
            return
        font = self.fonts.get('detail') or self.fonts['time']
        line_h = 7
        top = (self.display_height // 2) - (len(lines) * line_h) // 2
        top += self._layout_offset('score', 'y_offset')
        for i, line in enumerate(lines):
            width = draw.textlength(line, font=font)
            x = (self.display_width - width) // 2 + self._layout_offset('score', 'x_offset')
            self._draw_text_with_outline(
                draw, line, (x, top + i * line_h), font,
                fill=self._element_color('detail_text')
            )

    def _draw_upcoming_game_status(self, draw: "ImageDraw.ImageDraw", game: Dict) -> None:
        """Draw the date and time around an upcoming card.

        Time top and date bottom by default; scroll_card.swap_date_time puts
        the date on top instead. Skipped when the pair is stacked in the
        middle, which would otherwise print them twice.
        """
        if self._upcoming_center_mode() == "date_time":
            return

        date_raw, time_raw = self._upcoming_date_and_time(game)
        date_text = (self._format_game_date(date_raw, game)
                     if self._scroll_card_option("show_date", True) else "")
        time_text = (self._format_game_time(time_raw)
                     if self._scroll_card_option("show_time", True) else "")

        if self._scroll_card_option("swap_date_time", False):
            top_text, top_el, bottom_text, bottom_el = (
                date_text, 'date', time_text, 'time')
            top_font = self.fonts.get('detail') or self.fonts['time']
            bottom_font = self.fonts['time']
            top_color, bottom_color = 'detail_text', 'period_text'
        else:
            top_text, top_el, bottom_text, bottom_el = (
                time_text, 'time', date_text, 'date')
            top_font = self.fonts['time']
            bottom_font = self.fonts.get('detail') or self.fonts['time']
            top_color, bottom_color = 'period_text', 'detail_text'

        if top_text:
            top_width = draw.textlength(top_text, font=top_font)
            top_x = (self.display_width - top_width) // 2 + self._layout_offset(top_el, 'x_offset')
            top_y = 1 + self._layout_offset(top_el, 'y_offset')
            self._draw_text_with_outline(
                draw, top_text, (top_x, top_y), top_font,
                fill=self._element_color(top_color)
            )

        if bottom_text:
            bottom_width = draw.textlength(bottom_text, font=bottom_font)
            bottom_x = ((self.display_width - bottom_width) // 2
                        + self._layout_offset(bottom_el, 'x_offset'))
            # Measured, not a fixed -7: the detail font is 6px in most plugins
            # but 10px in soccer and nrl, where "Sep 19" ran past the card.
            ink_bottom = draw.textbbox((0, 0), bottom_text, font=bottom_font)[3]
            bottom_y = (max(0, self.display_height - ink_bottom - 1)
                        + self._layout_offset(bottom_el, 'y_offset'))
            self._draw_text_with_outline(
                draw, bottom_text, (bottom_x, bottom_y), bottom_font,
                fill=self._element_color(bottom_color)
            )

    # ---- rankings ------------------------------------------------------

    def set_rankings_cache(self, rankings: Dict[str, int]) -> None:
        """Set the team rankings cache for display."""
        self._team_rankings_cache = rankings
