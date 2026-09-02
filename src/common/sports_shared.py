"""The sports.py surface that is byte-identical in every scoreboard.

Nine plugins ship their own ``sports.py`` -- 41,326 lines in total. Comparing
executable ASTs across the eight that share a lineage, 48 method bodies are
byte-identical in all eight: 1,007 lines carried in eight copies, so 8,056
duplicated lines that must be edited eight times to fix once.

They are the parts with no sport in them. The selection and rotation engine
(``_round_robin_favorites``, ``_favorites_first``, ``_compose_selection``,
``_check_ranking_coverage``, ``_game_divisions``, ``_normalise_quality``), the
font/colour/date subsystem (``_scale_headline_fonts``, ``_scorebug_font``,
``_resolve_font_size``, ``_format_game_date``, ``_font_color``), and the
switch-mode upcoming card (``_draw_upcoming_center_switch``). Nothing here knows
what an inning or a possession is.

Mixins rather than free functions, because every one of these reads host state
-- ``self.config``, ``self.fonts``, ``self.logger``, ``self.display_width``.
Rewriting 48 bodies into free functions would be a rewrite, not a move; as
mixins the bodies move verbatim, which is what keeps the renders identical.

THREE OF THE 48 ARE DELIBERATELY LEFT BEHIND
--------------------------------------------
Byte-identical bodies are not automatically safe to move: a body can bind a
module-level name that differs per plugin, and then it only *looks* the same.

- ``_get_timezone`` calls ``resolve_timezone``, imported from a per-plugin
  module (``hockey_timezone``, ``soccer_timezone``, ...). All eight of those
  differ -- each carries its own ``_WRITEBACK_FIXED_IN`` version -- so moving
  the caller here would silently bind every scoreboard to one plugin's copy.
- ``_extract_game_details`` and ``_fetch_data`` are ``@abstractmethod`` stubs.
  They are the sport-specific contract; satisfying them from a mixin would let a
  plugin instantiate without implementing its own sport.

``_resolve_font_path`` went the other way: it is a module-level function in
sports.py rather than a method, identical in all eight, and ``_scale_headline_fonts``
needs it -- so it is inlined below rather than left behind.

WHAT A HOST MUST PROVIDE
------------------------
Enumerated by walking every ``self.<attr>`` the mixins read and subtracting what
they define, so this list is derived rather than remembered. Everything below is
supplied by all eight scoreboards today.

State: ``config``, ``fonts``, ``logger``, ``display_width``, ``display_height``,
``display_manager``, ``league``, ``sport``, ``mode_config``, ``session``,
``headers``, ``favorite_teams``, ``games_list``, ``current_game_index``,
``last_game_switch``, ``last_update``, ``update_interval``,
``no_data_interval``, ``game_display_duration``, ``stale_game_timeout``,
``other_games_min_quality``, ``schedule_lookback_days``,
``schedule_lookahead_days``, ``game_update_timestamps``,
``_zero_clock_timestamps``, ``_logo_cache``, ``_selection_pools``,
``_ranking_coverage_logged_at``, ``_empty_live_streak``, ``_last_warning_time``,
``_score_grew``.

Methods that stay per-plugin, because they are not identical across the eight
(or, for ``_get_timezone``, because they bind per-plugin modules):
``_get_layout_offset``, ``_by_importance``, ``_other_games_window``,
``_upcoming_date_and_time_text``, ``_extract_game_details_common``,
``_load_division_team_ids``, ``_get_timezone``, ``_is_favorite_game``,
``_is_game_really_over``, ``_is_ranked_game``, ``_passes_other_filters``.

Of the fourteen shared class constants, thirteen are identical everywhere and
live here. Only ``_SCORE_PROBE_TEXT`` varies -- afl and basketball reach three digits
a side and override it, the same two that override ``_SCORE_PROBE`` on
``SportsGameRendererMixin``.

DELIBERATELY NOT MERGED WITH sports_card
----------------------------------------
Fourteen of these have same-named twins in ``src/common/sports_card.py``, which
the scoreboards' ``game_renderer.py`` already uses. They are NOT wired together
here. Only five are provably equivalent by source comparison; the other nine
differ in ways inspection cannot settle, and a wrong guess silently changes what
every scoreboard draws. Merging them needs differential testing against both
implementations, and is left for its own change.
"""

from __future__ import annotations

import logging
import math
import os
import sys
import time
from abc import abstractmethod
from datetime import datetime, timedelta, timezone
from typing import Any, ClassVar, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import pytz
import requests
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

# How long a live mode may stretch its poll interval when nothing is happening,
# and the streak lengths that earn each stretch. Identical in all eight plugins.
_IDLE_SHORT_STREAK = 6
_IDLE_SHORT_FACTOR = 2
_IDLE_LONG_STREAK = 24
_IDLE_LONG_FACTOR = 6
_DEFAULT_LIVE_IDLE_MAX_SECONDS = 900


def _resolve_font_path(path: str) -> str:
    """Resolve a bundled font path without depending on the process cwd.

    These fonts ship with the LEDMatrix core, and every call site here named
    them relative to the working directory. That holds under the packaged
    systemd unit, whose WorkingDirectory is the install root, and breaks
    everywhere else -- the plugin safety harness, a manual run from $HOME, a
    unit file written without WorkingDirectory. The failure is quiet: the
    load raises, the caller falls back, and the scoreboard renders in PIL's
    default face instead of the pixel font it was laid out for.

    Resolution order matches the core's own resolver: the path as given
    first, so behaviour is unchanged wherever it already worked and a
    configured absolute path is returned untouched, then the core install
    root, then the original string so callers still raise and fall back
    exactly as they do today.
    """
    if os.path.exists(path):
        return path
    try:
        import src.font_manager as _core_fonts

        # The core grew this resolver in ChuckBuilds/LEDMatrix#425. Use it
        # when it is there so both repos stay on one definition of "install
        # root"; older cores fall through to the equivalent derivation below.
        manager = getattr(_core_fonts, "FontManager", None)
        resolver = getattr(manager, "_resolve_asset_path", None)
        if resolver is not None:
            resolved = resolver(path)
            if resolved and os.path.exists(resolved):
                return resolved
        root = os.path.dirname(os.path.dirname(os.path.abspath(_core_fonts.__file__)))
        candidate = os.path.join(root, path)
        if os.path.exists(candidate):
            return candidate
    except (ImportError, AttributeError, OSError):
        # No core on the path (standalone tooling), a core laid out
        # differently, or an unreadable install. Returning the original keeps
        # the caller's existing fallback intact.
        return path
    return path


class SportsCoreSharedMixin:
    """The ``SportsCore`` bodies identical in all eight scoreboards."""

    #: Design height the font scale is expressed against.
    _FONT_DESIGN_HEIGHT: ClassVar[int] = 32
    #: Fraction of the centre strip a score may grow into.
    _SCORE_GROWTH_BUDGET: ClassVar[float] = 0.65
    #: Widest score the scorebug sizes itself to hold. Leagues that reach three
    #: digits a side override this with "000-000".
    _SCORE_PROBE_TEXT: ClassVar[str] = "00-00"
    #: Whether this sport's scorebug draws a score at all.
    _DRAWS_SCORE: ClassVar[bool] = False
    #: Fallback (font, size) rungs for a score that will not fit.
    _NARROW_SCORE_RUNGS: ClassVar[Tuple[Tuple[str, int], ...]] = (
        ("4x6-font.ttf", 14), ("4x6-font.ttf", 7))
    #: Hard ceiling on score growth, in multiples of the configured size.
    _SCORE_MAX_GROWTH: ClassVar[int] = 2
    #: Which colour setting owns each font slot.
    _ELEMENT_FOR_FONT: ClassVar[Dict[str, str]] = {
        "score": "score_text", "time": "period_text", "team": "team_text",
        "detail": "detail_text", "status": "status_text"}
    #: Default tint for a favourite team's finished game.
    FAVORITE_RESULT_COLOR_DEFAULTS: ClassVar[Dict[str, Tuple[int, int, int]]] = {
        "win": (0, 255, 0), "loss": (255, 0, 0), "tie": (255, 200, 0)}
    _MONTH_ABBR: ClassVar[Tuple[str, ...]] = (
        "Jan", "Feb", "Mar", "Apr", "May", "Jun",
        "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
    _WEEKDAY_ABBR: ClassVar[Tuple[str, ...]] = (
        "Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
    #: Bitmap fonts snap to their native pixel grid.
    _FONT_PIXEL_GRID: ClassVar[Dict[str, int]] = {
        "PressStart2P-Regular.ttf": 8, "4x6-font.ttf": 7}
    _FONT_NAME_ALIASES: ClassVar[Dict[str, str]] = {
        "press_start": "PressStart2P-Regular.ttf", "four_by_six": "4x6-font.ttf"}
    #: Accepted values for the other-games quality filter.
    _QUALITY_CHOICES: ClassVar[frozenset] = frozenset({"any", "ranked"})
    #: How long to stay quiet between ranking-coverage warnings.
    _RANKING_COVERAGE_SECONDS: ClassVar[int] = 60 * 60

    def _get_season_schedule_dates(self) -> tuple[str, str]:
        return "", ""

    def _draw_scorebug_layout(self, game: Dict, force_clear: bool = False) -> None:
        """Placeholder draw method - subclasses should override."""
        # This base method will be simple, subclasses provide specifics
        try:
            img = Image.new("RGB", (self.display_width, self.display_height), (0, 0, 0))
            draw = ImageDraw.Draw(img)
            status = game.get("status_text", "N/A")
            self._draw_text_with_outline(draw, status, (2, 2), self.fonts["status"])
            self.display_manager.image.paste(img, (0, 0))
            # Don't call update_display here, let subclasses handle it after drawing
        except Exception as e:
            self.logger.error(
                f"Error in base _draw_scorebug_layout: {e}", exc_info=True
            )

    @classmethod
    def _crisp_size(cls, font_file, desired):
        """Snap *desired* to the nearest size *font_file* renders crisply at.

        A face with no known grid is returned unchanged, so a user-supplied
        font is never second-guessed.
        """
        font_file = cls._FONT_NAME_ALIASES.get(font_file, font_file)
        grid = cls._FONT_PIXEL_GRID.get(font_file)
        if not grid or not desired or desired <= 0:
            return desired
        return max(grid, int(round(float(desired) / grid)) * grid)

    def _plugin_dir(self) -> Optional[str]:
        """Directory of the plugin that defines this class.

        In sports.py these methods could just use ``__file__``. Here that is
        src/common/, so the plugin's own directory has to be recovered from the
        instance. ``type(self).__module__`` alone is not enough: SportsCore is
        an ABC, so a subclass built with ``type(name, bases, ns)`` -- which the
        plugins' own tests do -- reports its module as "abc". Walking the MRO
        steps past those synthetic classes to the first one whose module sits
        next to a config_schema.json, which is the real plugin.
        """
        for cls in type(self).__mro__:
            module = sys.modules.get(getattr(cls, "__module__", ""), None)
            path = getattr(module, "__file__", None)
            if not path:
                continue
            directory = os.path.dirname(os.path.abspath(path))
            if os.path.isfile(os.path.join(directory, "config_schema.json")):
                return directory
        return None

    def _schema_font_size(self, element_key):
        """The font_size this plugin's config_schema.json declares, or None."""
        if not element_key:
            return None
        cache = getattr(self.__class__, '_SCHEMA_FONT_SIZES', None)
        if cache is None:
            cache = {}
            try:
                import json
                directory = self._plugin_dir()
                if directory is None:
                    raise FileNotFoundError("no config_schema.json on the MRO")
                with open(os.path.join(directory, 'config_schema.json')) as fh:
                    schema = json.load(fh)
                props = (schema.get('properties', {})
                               .get('customization', {})
                               .get('properties', {}))
                for key, spec in props.items():
                    size = spec.get('properties', {}).get('font_size', {}).get('default')
                    if size is not None:
                        cache[key] = int(size)
            except Exception:
                cache = {}
            self.__class__._SCHEMA_FONT_SIZES = cache
        return cache.get(element_key)

    def _resolve_font_size(self, element_config, element_key, default_size, font_name):
        """Size to render at: the user's choice, or a grid-snapped default.

        A configured size counts as a real choice only when it differs from
        the schema default. The web UI writes the whole schema default block
        on every save, so "font_size == schema default" carries no intent and
        would otherwise pin every install to an anti-aliased size forever.
        """
        configured = (element_config or {}).get('font_size')
        if configured is not None:
            try:
                configured = int(configured)
                if configured != self._schema_font_size(element_key):
                    return configured
            except (TypeError, ValueError):
                pass
        return self._crisp_size(font_name, default_size)

    def _card_option(self, key: str, default: Any = None) -> Any:
        """Read one key from the scroll_card config block."""
        block = (self.config or {}).get("scroll_card")
        if isinstance(block, dict) and block.get(key) is not None:
            return block.get(key)
        return default

    def _switch_upcoming_center(self) -> str:
        """Middle of the full-screen upcoming scorebug: 'vs', 'date_time' or 'none'."""
        mode = str(self._card_option("switch_upcoming_center", "date_time")
                   or "date_time").lower()
        if mode == "inherit":
            mode = str(self._card_option("upcoming_center", "vs") or "vs").lower()
        return mode if mode in ("vs", "date_time", "none") else "date_time"

    def _vs_text(self) -> str:
        """Separator drawn between the teams -- "VS", "@", "at", anything."""
        return str(self._card_option("vs_text", "VS"))

    def _switch_date_format(self) -> str:
        """Date style for the full-screen scorebug.

        Its own key rather than the shared ``date_format`` because the two
        displays disagree about the default: the scroll card renders "Sep 19"
        while _extract_game_details_common emits "9/19", the "numeric" style,
        and this scorebug has always drawn it. Reading the shared key here
        would restyle every existing panel on update -- and "leave it alone
        when unset" is not available, because the core merges schema defaults
        into the config on every load, so the key is never actually unset.
        "inherit" opts into the scroll and Vegas setting.
        """
        fmt = str(self._card_option("switch_date_format", "numeric") or "numeric").lower()
        if fmt == "inherit":
            fmt = str(self._card_option("date_format", "abbrev") or "abbrev").lower()
        return fmt

    def _format_game_date(self, date_text: str, game: Optional[Dict] = None) -> str:
        """Format an upcoming date per scroll_card.switch_date_format."""
        raw = str(date_text or "").strip()
        if not raw:
            return raw
        fmt = self._switch_date_format()
        if fmt == "numeric":
            return raw
        parts = raw.replace("-", "/").split("/")
        if not (len(parts) >= 2 and parts[0].strip().isdigit() and parts[1].strip().isdigit()):
            return raw
        month, day = int(parts[0]), int(parts[1])
        if not 1 <= month <= 12:
            return raw
        name = self._MONTH_ABBR[month - 1]
        if fmt == "numeric_day_first":
            return f"{day}/{month}"
        if fmt == "day_first":
            return f"{day} {name}"
        if fmt == "weekday":
            weekday = self._weekday_for(game)
            return f"{weekday} {name} {day}" if weekday else f"{name} {day}"
        return f"{name} {day}"

    def _weekday_for(self, game: Optional[Dict]) -> str:
        """Weekday abbreviation from the game's start time, or ''."""
        if not game:
            return ""
        raw = game.get("start_time_utc") or game.get("start_time")
        if not raw:
            return ""
        try:
            start = raw if isinstance(raw, datetime) else datetime.fromisoformat(
                str(raw).replace("Z", "+00:00"))
            return self._WEEKDAY_ABBR[start.astimezone(self._get_timezone()).weekday()]
        except (ValueError, TypeError, OverflowError):
            return ""

    def _format_game_time(self, time_text: str) -> str:
        """Return the time as-is (12h) or converted to 24h."""
        raw = str(time_text or "").strip()
        if not raw or str(self._card_option("time_format", "12h")) != "24h":
            return raw
        cleaned = raw.upper().replace(" ", "")
        meridiem = "AM" if cleaned.endswith("AM") else "PM" if cleaned.endswith("PM") else ""
        if not meridiem:
            return raw
        try:
            hh, _, mm = cleaned[:-2].partition(":")
            hour, minute = int(hh), int(mm or 0)
        except ValueError:
            return raw
        if not (0 <= hour <= 12 and 0 <= minute <= 59):
            return raw
        hour = hour % 12 + (12 if meridiem == "PM" else 0)
        return f"{hour:02d}:{minute:02d}"

    def _scorebug_font(self, draw, text: str, width: int):
        """The face this scorebug draws its date and time in.

        Always the "time" face, which is what this display has used for both
        rows for as long as it has existed: changing switch_upcoming_center
        moves the two lines around, it is not meant to restyle them, so the
        type stays put while the placement changes.

        The single exception is text that cannot fit the panel at all. Only
        the "weekday" date can do that -- "Fri Sep 19" measures 80px in an
        8px face, on a board 64px wide -- and the smaller "detail" face is a
        better answer there than running off both edges. Every other date and
        time this display can produce fits, so in practice the face never
        changes; it is a floor, not a style rule.
        """
        font = self.fonts["time"]
        if not text:
            return font
        try:
            if draw.textlength(text, font=font) + 2 <= width:
                return font
        except (TypeError, ValueError):
            return font
        return self.fonts.get("detail") or font

    def _draw_upcoming_center_switch(self, draw, game: Dict, center_y: int,
                                     game_date: str, game_time: str,
                                     display_width: Optional[int] = None,
                                     display_height: Optional[int] = None,
                                     date_element: str = 'date',
                                     time_element: str = 'time',
                                     second_row_y_offset: bool = True) -> bool:
        """Draw the middle of the full-screen upcoming scorebug.

        Returns True when the header above it ("Next Game", or the league
        name) should still be drawn. In "vs" and "none" the date and time move
        out of the middle and into the top and bottom slots, mirroring the
        scroll card -- and the top slot is where the header used to be, so the
        caller drops it.

        ``date_element``/``time_element``/``second_row_y_offset`` exist only so
        the layout-offset keys stay exactly what each plugin's schema
        advertises; this sport's defaults are the common case.
        """
        width = self.display_width if display_width is None else display_width
        height = self.display_height if display_height is None else display_height
        mode = self._switch_upcoming_center()
        date_text, time_text = self._upcoming_date_and_time_text(
            game_date, game_time, game)
        swapped = bool(self._card_option("swap_date_time", False))

        if mode == "date_time":
            # Historically the date sat at center_y - 7 with the time 9px
            # under it, and the time's row was derived from the date's, so a
            # date y_offset moved the pair. Both still hold; the slots only
            # trade places when swap_date_time is set, and hiding one line
            # leaves the other where it was rather than re-centering the stack.
            slots = [(time_element, time_text), (date_element, date_text)] if swapped \
                else [(date_element, date_text), (time_element, time_text)]
            row_y = center_y - 7
            for index, (element, text) in enumerate(slots):
                if index:
                    row_y += 9
                    if second_row_y_offset:
                        row_y += self._get_layout_offset(element, 'y_offset')
                else:
                    row_y += self._get_layout_offset(element, 'y_offset')
                if not text:
                    continue
                font = self._scorebug_font(draw, text, width)
                text_width = draw.textlength(text, font=font)
                text_x = ((width - text_width) // 2
                          + self._get_layout_offset(element, 'x_offset'))
                self._draw_text_with_outline(
                    draw, text, (text_x, row_y), font
                )
            return True

        if mode == "vs":
            vs_text = self._vs_text()
            if vs_text:
                vs_width = draw.textlength(vs_text, font=self.fonts["score"])
                vs_x = ((width - vs_width) // 2
                        + self._get_layout_offset('score', 'x_offset'))
                vs_y = (center_y - 3
                        + self._get_layout_offset('score', 'y_offset'))
                self._draw_text_with_outline(
                    draw, vs_text, (vs_x, vs_y), self.fonts["score"]
                )

        # "vs" and "none" both push the date and time out to the edges, time
        # on top unless swap_date_time says otherwise -- the same order the
        # scroll card uses.
        if swapped:
            top_element, top_text = date_element, date_text
            bottom_element, bottom_text = time_element, time_text
        else:
            top_element, top_text = time_element, time_text
            bottom_element, bottom_text = date_element, date_text

        if top_text:
            top_font = self._scorebug_font(draw, top_text, width)
            top_width = draw.textlength(top_text, font=top_font)
            top_x = ((width - top_width) // 2
                     + self._get_layout_offset(top_element, 'x_offset'))
            top_y = 1 + self._get_layout_offset(top_element, 'y_offset')
            self._draw_text_with_outline(
                draw, top_text, (top_x, top_y), top_font
            )
        if bottom_text:
            bottom_font = self._scorebug_font(draw, bottom_text, width)
            bottom_width = draw.textlength(bottom_text, font=bottom_font)
            bottom_x = ((width - bottom_width) // 2
                        + self._get_layout_offset(bottom_element, 'x_offset'))
            # Measured, not a fixed offset: the detail font is 6px in most
            # plugins and 10px in soccer and nrl, where a fixed -7 ran the
            # date off the panel.
            ink_bottom = draw.textbbox((0, 0), bottom_text, font=bottom_font)[3]
            bottom_y = (max(0, height - ink_bottom - 1)
                        + self._get_layout_offset(bottom_element, 'y_offset'))
            self._draw_text_with_outline(
                draw, bottom_text, (bottom_x, bottom_y), bottom_font
            )
        return False

    @staticmethod
    def _coerce_rgb(value, fallback):
        """Turn a configured [R, G, B] list into a clamped (r, g, b) tuple."""
        # Checked before unpacking: a 3-character string ("123") would otherwise
        # iterate into three digits and yield a colour rather than the fallback.
        if not isinstance(value, (list, tuple)) or len(value) != 3:
            return fallback
        try:
            r, g, b = (max(0, min(255, int(channel))) for channel in value)
        except (TypeError, ValueError):
            return fallback
        return (r, g, b)

    @staticmethod
    def _side_is_favorite(game: Dict, side: str, favorites: set) -> bool:
        """Is the home/away side of this game a favorite team?

        Both the abbreviation and the ESPN id are checked, because a couple of
        leagues (NRL) match favorites by id where abbreviations collide.
        """
        for key in (f"{side}_abbr", f"{side}_id"):
            value = game.get(key)
            if value is not None and str(value).strip().upper() in favorites:
                return True
        return False

    def _favorite_result(self, game: Dict) -> Optional[str]:
        """Say how the favorite team did in a finished game.

        Returns 'win', 'loss' or 'tie', or None when there is no single team
        to root for: no favorites configured, neither side is a favorite, or
        *both* are -- a favorite-vs-favorite game has no losing side worth
        flagging in red. Also None when the scores are not usable numbers.
        """
        favorites = getattr(self, "favorite_teams", None) or []
        favorites = {str(team).strip().upper() for team in favorites if str(team).strip()}
        if not favorites:
            return None

        home_fav = self._side_is_favorite(game, "home", favorites)
        away_fav = self._side_is_favorite(game, "away", favorites)
        if home_fav == away_fav:
            return None

        try:
            # int(float(...)) to match GameRenderer._side_score exactly -- the
            # two paths must agree on what counts as a usable score.
            home_score = int(float(str(game.get("home_score", "")).strip()))
            away_score = int(float(str(game.get("away_score", "")).strip()))
        except (TypeError, ValueError):
            return None

        if home_score == away_score:
            return "tie"
        favorite_score, other_score = (
            (home_score, away_score) if home_fav else (away_score, home_score)
        )
        return "win" if favorite_score > other_score else "loss"

    def _recent_score_color(self, game: Dict, default):
        """Fill color for a finished game's score, per favorite_result_colors."""
        try:
            settings = (self.config.get("customization") or {}).get(
                "favorite_result_colors"
            ) or {}
            if not settings.get("enabled", False):
                return default
            result = self._favorite_result(game)
            if result is None:
                return default
            return self._coerce_rgb(
                settings.get(f"{result}_color"),
                self.FAVORITE_RESULT_COLOR_DEFAULTS[result],
            )
        except Exception:
            self.logger.debug(
                "Could not resolve favorite result color", exc_info=True
            )
            return default

    def _score_font_size(self) -> int:
        """Pixel size the score is currently drawn at."""
        return getattr(self.fonts.get("score"), "size", 8) or 8

    def _time_font_size(self) -> int:
        """Pixel size the clock/date face is currently drawn at."""
        return getattr(self.fonts.get("time"), "size", 8) or 8

    def _user_chose_size(self, element_key: str) -> bool:
        """True when customization.<element>.font_size is a real choice.

        The web UI's save flow writes the whole schema default block into
        config.json on every save, whether or not the user touched that
        section, so a size merely being PRESENT carries no intent. Only one
        that differs from the schema default does.
        """
        element = (self.config.get('customization', {}) or {}).get(element_key) or {}
        configured = element.get('font_size')
        if configured is None:
            return False
        try:
            return int(configured) != self._schema_font_size(element_key)
        except (TypeError, ValueError):
            return False

    def _grid_scaled_size(self, font):
        """(path, grid, size) for *font* regrown to this panel's height.

        None when the panel is at or below the design height (nothing to do),
        or when the face has no known pixel grid -- a user-supplied font is
        never second-guessed, because we do not know what it renders crisply
        at.
        """
        path = getattr(font, 'path', None)
        base = getattr(font, 'size', None)
        if not base or not isinstance(path, str):
            return None
        face = os.path.basename(path)
        grid = self._FONT_PIXEL_GRID.get(self._FONT_NAME_ALIASES.get(face, face))
        if not grid:
            return None
        scale = float(self.display_height) / (self._FONT_DESIGN_HEIGHT or 32)
        if scale <= 1.0:
            return None
        return path, grid, max(int(base), int(self._crisp_size(face, base * scale)))

    def _scale_headline_fonts(self, fonts):
        """Grow the score with the panel, and hold the clock/date below it.

        The score is the one number the card exists to show, and it was the
        only element not sized from the panel. Worse, it was not even bigger
        than its neighbours: PressStart2P renders crisply on an 8px grid, so
        the 10px default snapped to 8 -- the same 8 the period/clock above it
        and the game date below it are drawn at. Three lines of identical
        type, none of them the headline, which is what makes the score read as
        lower priority than the time and the date rather than the point of the
        card.

        So the score is sized from display_height and snapped to its face's
        pixel grid (off the grid FreeType anti-aliases the strokes, and on an
        LED matrix a part-lit pixel is a dim lamp rather than a soft edge),
        then stepped back down that grid until it fits its share of the width.
        The clock/date face is regrown the same way but held at least one grid
        step below the score, so the ranking between them is visible rather
        than implied.

        A 32-tall panel scales by exactly 1.0 and is left byte-identical; a
        size the user set explicitly is never overridden.
        """
        self._score_grew = False
        if not self._DRAWS_SCORE:
            # No score on this screen, so none of the sizing below is for it.
            return fonts
        try:
            scaled = None if self._user_chose_size('score_text') else \
                self._grid_scaled_size(fonts.get('score'))
            if scaled is not None:
                path, grid, size = scaled
                base = getattr(fonts['score'], 'size', size) or size
                size = min(size, base * self._SCORE_MAX_GROWTH)
                probe = ImageDraw.Draw(Image.new('RGB', (4, 4)))
                budget = self.display_width * self._SCORE_GROWTH_BUDGET
                # Measured from a fixed five-character score rather than the
                # live one, so the card does not resize when a side passes 9.
                while size > grid:
                    if probe.textlength(
                            self._SCORE_PROBE_TEXT,
                            font=ImageFont.truetype(path, size)) <= budget:
                        break
                    size -= grid
                if size != getattr(fonts['score'], 'size', size):
                    fonts['score'] = ImageFont.truetype(path, size)
                    self._score_grew = True

            if not self._score_grew and not self._user_chose_size('score_text') \
                    and self.display_height > self._FONT_DESIGN_HEIGHT:
                # PressStart2P could not grow inside the budget -- its next crisp
                # size is simply too wide for this panel. A narrower face still
                # can: 4x6-font at 14px is nearly as tall as PressStart2P at 16
                # and about half as wide. This matters beyond the score itself,
                # because a card whose score never grows never reserves the
                # centre either, so its logos stay at the uncapped 1.5x and are
                # drawn straight over the score -- which is what a three-digit
                # basketball score does on a 128x64 board.
                probe = ImageDraw.Draw(Image.new('RGB', (4, 4)))
                budget = self.display_width * self._SCORE_GROWTH_BUDGET
                current = getattr(fonts.get('score'), 'size', 0) or 0
                for _name, _size in self._NARROW_SCORE_RUNGS:
                    if _size <= current:
                        continue
                    _path = _resolve_font_path(f"assets/fonts/{_name}")
                    _candidate = ImageFont.truetype(_path, _size)
                    if probe.textlength(self._SCORE_PROBE_TEXT,
                                        font=_candidate) <= budget:
                        fonts['score'] = _candidate
                        self._score_grew = True
                        break

            scaled = None if self._user_chose_size('period_text') else \
                self._grid_scaled_size(fonts.get('time'))
            if scaled is not None:
                path, grid, size = scaled
                ceiling = getattr(fonts.get('score'), 'size', 0) or 0
                if ceiling and size >= ceiling:
                    size = max(grid, ceiling - grid)
                if size != getattr(fonts['time'], 'size', size):
                    fonts['time'] = ImageFont.truetype(path, size)
        except Exception:
            self.logger.debug("Headline font scaling skipped", exc_info=True)
        return fonts

    def _element_color(self, element: str, default: Tuple[int, int, int] = (255, 255, 255)):
        """Per-element text colour from customization.<element>.text_color."""
        try:
            cfg = (self.config or {}).get("customization", {}).get(element, {})
            value = cfg.get("text_color")
            if isinstance(value, (list, tuple)) and len(value) == 3:
                return tuple(max(0, min(255, int(c))) for c in value)
            if isinstance(value, str) and value.startswith("#") and len(value) == 7:
                return tuple(int(value[i:i + 2], 16) for i in (1, 3, 5))
        except (TypeError, ValueError):
            pass
        return default

    def _unshare_element_fonts(self, fonts):
        """Give each colourable element its own face object.

        The colour a draw gets is resolved from the face it was handed, and
        several of these loaders legitimately hand one object to more than one
        element -- a size resolver that lands two elements on the same face, a
        fallback that fills every key from one default, football's narrowing
        step that deliberately shrinks the clock along with the score. Sharing
        the object makes the element ambiguous and the colour unresolvable.

        Re-instantiating from the same path and size gives a distinct object
        with identical metrics, so nothing about the rendering changes; only
        the ability to tell two elements apart does. Faces that cannot be
        rebuilt (a BDF loaded through freetype.Face, anything without a usable
        path) are left shared, and their draws stay white as before.
        """
        try:
            from PIL import ImageFont as _IF
        except ImportError:  # pragma: no cover
            return fonts
        seen = {}
        for key in self._ELEMENT_FOR_FONT:
            font = fonts.get(key)
            if font is None:
                continue
            if id(font) not in seen:
                seen[id(font)] = key
                continue
            path, size = getattr(font, "path", None), getattr(font, "size", None)
            if not path or not size:
                continue
            try:
                fonts[key] = _IF.truetype(path, size)
            except (OSError, ValueError, TypeError):
                self.logger.debug(
                    "Could not un-share the %s face; it keeps the default colour", key)
        return fonts

    def _font_color(self, font, default: Tuple[int, int, int] = (255, 255, 255)):
        """Colour for whichever element owns this face.

        Matched on identity, and deliberately gives up when one object is
        shared: the last-resort font path can hand the same face to several
        keys, and there is no right answer for which element's colour that is.
        White is what those draws used before, so ambiguity costs nothing.
        """
        try:
            fonts = getattr(self, "fonts", None) or {}
            matches = [element for key, element in self._ELEMENT_FOR_FONT.items()
                       if fonts.get(key) is font]
            if len(matches) == 1:
                return self._element_color(matches[0], default)
        except (AttributeError, TypeError):
            pass
        return default

    def _draw_text_with_outline(
        self, draw, text, position, font, fill=None, outline_color=(0, 0, 0)
    ):
        """Draw text with a black outline for better readability."""
        # Disable anti-aliasing: pixel/bitmap fonts (e.g. PressStart2P) get
        # anti-aliased into dim partial-lit pixels on a 1:1 LED matrix, muddying
        # glyphs. 1-bit mode keeps strokes crisp.
        # Defaults to the configured colour for whichever element owns
        # this face rather than to white, so customization.<element>.text_color
        # reaches every draw. The schema has offered those pickers all along
        # and they only ever changed the font. An explicit fill still wins:
        # the odds colours and the favourite-result score tint mean something
        # the palette does not.
        if fill is None:
            fill = self._font_color(font)
        draw.fontmode = "1"
        x, y = position
        for dx, dy in [
            (-1, -1),
            (-1, 0),
            (-1, 1),
            (0, -1),
            (0, 1),
            (1, -1),
            (1, 0),
            (1, 1),
        ]:
            draw.text((x + dx, y + dy), text, font=font, fill=outline_color)
        draw.text((x, y), text, font=font, fill=fill)

    def _should_log(self, warning_type: str, cooldown: int = 60) -> bool:
        """Check if we should log a warning based on cooldown period."""
        current_time = time.time()
        if current_time - self._last_warning_time > cooldown:
            self._last_warning_time = current_time
            return True
        return False

    def _get_weeks_data(self) -> Optional[Dict]:
        """
        Get partial data for immediate display while background fetch is in progress.
        This fetches current/recent games only for quick response.
        """
        try:
            # Fetch current week and next few days for immediate display
            now = datetime.now(pytz.utc)
            immediate_events = []

            start_date = now - timedelta(days=self.schedule_lookback_days)
            end_date = now + timedelta(days=self.schedule_lookahead_days)
            date_str = f"{start_date.strftime('%Y%m%d')}-{end_date.strftime('%Y%m%d')}"
            url = f"https://site.api.espn.com/apis/site/v2/sports/{self.sport}/{self.league}/scoreboard"
            response = self.session.get(
                url,
                params={"dates": date_str, "limit": 1000},
                headers=self.headers,
                timeout=10,
            )
            response.raise_for_status()
            data = response.json()
            immediate_events = data.get("events", [])

            if immediate_events:
                self.logger.info(f"Fetched {len(immediate_events)} events {date_str}")
                return {"events": immediate_events}

        except requests.exceptions.RequestException as e:
            self.logger.warning(
                f"Error fetching this weeks games for {self.sport} - {self.league} - {date_str}: {e}"
            )
        return None

    def _custom_scorebug_layout(self, game: dict, draw_overlay: ImageDraw.ImageDraw):
        pass

    def cleanup(self):
        """Clean up resources when plugin is unloaded."""
        # Close HTTP session
        if hasattr(self, 'session') and self.session:
            try:
                self.session.close()
            except Exception as e:
                self.logger.warning(f"Error closing session: {e}")

        # Clear caches
        if hasattr(self, '_logo_cache'):
            self._logo_cache.clear()

        self.logger.info(f"{self.__class__.__name__} cleanup completed")

    def _game_divisions(self, game: Dict) -> Optional[set]:
        """Divisions of BOTH sides, or None when they cannot be told.

        Both sides are collected, but the caller only needs ONE of them to sit
        in a checked division. Requiring every participant read as "FBS games
        only" and removed a ranked side hosting an FCS school -- which is still
        a game involving a team the viewer checked the box for, and on a real
        Week 2 slate it silently dropped five of the twenty ranked matchups.
        What the checkbox is for is keeping FCS-versus-FCS out of a board
        configured for FBS, and that still holds: a game with no checked
        division on either side is dropped.
        """
        divisions = self._load_division_team_ids()
        if not any(divisions.values()):
            return None
        try:
            ids = [int(game.get("home_id")), int(game.get("away_id"))]
        except (TypeError, ValueError):
            return None
        present = set()
        for team_id in ids:
            for name in ("fbs", "fcs"):
                if team_id in divisions.get(name, set()):
                    present.add(name)
                    break
            else:
                present.add("other")
        return present

    def _league_has_rankings(self) -> bool:
        """Only college leagues publish a poll; everyone else 404s.

        This gate matters more than it looks. _fetch_team_rankings only
        short-circuits when the cache is non-empty, so a failed fetch leaves it
        empty and the next update tries again -- at a 30s interval that is
        ~2,900 pointless requests a day, per league, all of them 404s.
        """
        league = (self.league or "").lower()
        return "college" in league or "ncaa" in league

    @staticmethod
    def _normalise_divisions(raw) -> List[str]:
        """Division names from config, in the shape the filter expects.

        A hand-edited config can hold "fbs" where the schema says ["fbs"], and
        list("fbs") is ['f', 'b', 's'] -- three names that match no division, so
        every non-favourite game is rejected by a setting the user believes says
        the opposite. An empty list is left empty: that means "no division
        filter" and is a legitimate choice, not a mistake to correct.
        """
        if isinstance(raw, str):
            raw = [raw]
        try:
            items = list(raw or [])
        except TypeError:
            return []
        return [str(d).strip().lower() for d in items if str(d).strip()]

    def _round_robin_favorites(self, games: List[Dict], limit: int) -> List[Dict]:
        """Each favourite team's next game before any team's second one.

        Taking the soonest N favourite games spends the slots on whoever plays
        most often. Walked across a real season with two favourites and a limit
        of 2, nine days of it showed Auburn twice and Georgia not at all --
        Auburn played either side of a Georgia bye, so both slots went to
        Auburn. The other-games pool already refuses to do this; favourites
        were still doing it.

        Depth is kept where there is room: one favourite with three slots still
        gets its next three games, because the round-robin only comes back for
        a team's second game once every team has had a first.

        A game between two favourites is picked once and counts for both.
        """
        if limit <= 0 or not games:
            return []
        wanted = [t for t in (self.favorite_teams or []) if t]
        if len(wanted) < 2:
            return games[:limit]        # nothing to share the slots between

        # Which side of a game belongs to which favourite is a per-lineage
        # question: NRL matches on ESPN team IDs because its abbreviations are
        # not unique ("NEW" is both Newcastle and New Zealand), while the rest
        # match on abbreviation. Ask for the lineage's own matcher rather than
        # assuming, or this silently groups nothing and every slot goes empty.
        team_in = getattr(self, "_team_in", None)
        if callable(team_in):
            def belongs(game, team):
                return bool(team_in(game.get("home_id"), [team])
                            or team_in(game.get("away_id"), [team]))
        else:
            def belongs(game, team):
                return team in (game.get("home_abbr"), game.get("away_abbr"))

        queues = {team: [] for team in wanted}
        for game in games:              # already in kickoff order
            for team in wanted:
                if belongs(game, team):
                    queues[team].append(game)

        picked, taken = [], set()
        while len(picked) < limit:
            progressed = False
            for team in wanted:
                queue = queues[team]
                while queue and queue[0].get("id") in taken:
                    queue.pop(0)
                if queue and len(picked) < limit:
                    game = queue.pop(0)
                    taken.add(game.get("id"))
                    picked.append(game)
                    progressed = True
            if not progressed:
                break                   # every queue is empty
        return picked

    def _normalise_quality(self, raw) -> str:
        """other_games_min_quality, as one of the values the code implements.

        An unusable value used to fall through every branch of
        _passes_other_filters and silently mean "any" -- a quality bar the
        board believes it has and does not.
        """
        value = str(raw or "").strip().lower()
        if value in self._QUALITY_CHOICES:
            return value
        if value == "broadcast":
            # Retired in football-scoreboard 3.0.0 and now here. Measured
            # against a real Week 1 and Week 2 college slate it passed 174 of
            # 175 games: ESPN publishes a broadcaster for nearly everything
            # now, ESPN+ included, so the tier read as a quality bar and
            # behaved as "any". Boards holding it get the bar they thought
            # they were getting.
            self.logger.warning(
                "%s: other_games_min_quality 'broadcast' has been retired -- "
                "it let through nearly every game -- using 'ranked'. Change "
                "the setting to clear this.", getattr(self, "sport_key", "?"),
            )
            return "ranked"
        self.logger.warning(
            "%s: ignoring unusable other_games_min_quality=%r, using 'ranked'",
            getattr(self, "sport_key", "?"), raw,
        )
        return "ranked"

    def _check_ranking_coverage(self, games: List[Dict]) -> None:
        """Say so when a loaded poll matches nothing on the schedule.

        The table is keyed by the abbreviation the RANKINGS endpoint returns and
        matched against the one the SCOREBOARD endpoint returns. Nothing
        guarantees the two agree, and if they ever stop agreeing the filter
        quietly removes every non-favourite game -- no exception, no log line,
        just a shorter board. That is the same shape as the bug where rankings
        were never loading at all, which survived until someone went looking.

        Throttled to once an hour: selection runs on every update.
        """
        if self.other_games_min_quality != "ranked":
            return
        rankings = getattr(self, "_team_rankings_cache", None) or {}
        if not rankings or not games:
            return
        if any(self._is_ranked_game(g) for g in games):
            return
        now = time.monotonic()
        # Zero means never logged, not "logged at the epoch". monotonic() counts
        # from an arbitrary origin -- on a freshly booted board it is a few
        # hundred seconds -- so comparing against 0 swallowed the first warning
        # for the first hour of uptime, which is exactly when a misconfigured
        # board is being watched. CI caught this; a machine with days of uptime
        # cannot.
        if (self._ranking_coverage_logged_at
                and now - self._ranking_coverage_logged_at < self._RANKING_COVERAGE_SECONDS):
            return
        self._ranking_coverage_logged_at = now
        self.logger.warning(
            "%s: %d ranked teams loaded, but none of the %d other games match "
            "one -- the quality filter is removing every non-favourite game. "
            "Ranked abbreviations look like: %s",
            self.league, len(rankings), len(games),
            ", ".join(sorted(rankings)[:8]),
        )

    def _favorites_first(
        self,
        processed_games: List[Dict],
        favorite_limit: int,
        other_limit: int,
        newest_first: bool = False,
    ) -> List[Dict]:
        """Favourite games first, then a bounded number of everything else.

        This is the middle setting the plugin was missing. `show_favorite_teams_only`
        used to be the whole story: on, and you saw nothing but your teams; off,
        and your teams were ignored entirely -- the selection just took the next
        N games league-wide, so a UGA fan with 946 upcoming college games in the
        window saw UGA about as often as chance allowed.

        Both counts are TOTALS here, not per-team. In favourites-only mode
        `upcoming_games_to_show` is a per-team budget, which is reasonable when
        the list is your own teams; applied to a dynamic group it is not. With
        AP_TOP_10 resolving to a dozen teams, three games each is 28 distinct
        cards before a single non-favourite is added. A total keeps the rotation
        the length the user asked for.
        """
        if newest_first:
            def key(g):
                return g.get("start_time_utc") or datetime.min.replace(tzinfo=timezone.utc)
            ordered = sorted(processed_games, key=key, reverse=True)
        else:
            def key(g):
                return g.get("start_time_utc") or datetime.max.replace(tzinfo=timezone.utc)
            ordered = sorted(processed_games, key=key)

        favorites, others, unfiltered = [], [], []
        for game in ordered:
            if self._is_favorite_game(game):
                favorites.append(game)          # never filtered: your team is your team
                continue
            unfiltered.append(game)
            if self._passes_other_filters(game):
                others.append(game)
        self._check_ranking_coverage(unfiltered)

        self._selection_pools = {
            "favorites": favorites,
            "others": self._by_importance(others, newest_first),
            "unfiltered": self._by_importance(unfiltered, newest_first),
            "favorite_limit": favorite_limit,
            "other_limit": other_limit,
            "newest_first": newest_first,
        }
        return self._compose_selection()

    def _compose_selection(self) -> List[Dict]:
        """Favourites plus the current slice of others, in schedule order.

        Split out of _favorites_first so the slice can be re-cut between
        fetches. The pools are settled -- which games exist, and which of them
        are worth a slot -- while WHICH of the others is on screen is a display
        decision, and gating it on the fetch made the rotation interval a lie:
        update() returns early until upcoming_update_interval has passed, so a
        four-minute rotation actually stepped fifteen windows once an hour.
        Same lesson as _advance_live_game_if_due further down this file.
        """
        pools = self._selection_pools
        favorites, others = pools["favorites"], pools["others"]
        favorite_limit, other_limit = pools["favorite_limit"], pools["other_limit"]
        newest_first = pools["newest_first"]
        if newest_first:
            def key(g):
                return g.get("start_time_utc") or datetime.min.replace(tzinfo=timezone.utc)
        else:
            def key(g):
                return g.get("start_time_utc") or datetime.max.replace(tzinfo=timezone.utc)

        selected = self._round_robin_favorites(favorites, max(0, favorite_limit))
        selected.extend(self._other_games_window(others, max(0, other_limit)))
        if not selected and other_limit > 0:
            # Nothing survived at all: your teams are not playing inside the
            # schedule window AND the filters removed every other game. Each
            # check fails open on missing data, but a filter working exactly as
            # asked can still match nothing on a given day, and with no
            # favourite game left there is nothing to carry the mode -- an empty
            # list is a blank panel, not a short one. Same whole-list fallback
            # `_filtered_or_all` makes for a board with no favourites at all.
            # `other_limit` of 0 is an explicit "favourites only", so that one
            # is left to go quiet as asked.
            selected = self._other_games_window(pools["unfiltered"], max(0, other_limit))
        # Re-sort so the card order still reads as a schedule. Selection decides
        # WHICH games; it should not reorder them into favourites-then-others,
        # which would show next week's UGA game before tonight's.
        selected.sort(key=key, reverse=newest_first)
        return selected


class SportsLiveSharedMixin:
    """The ``SportsLive`` bodies identical in all eight scoreboards."""

    def _detect_stale_games(self, games: List[Dict]) -> None:
        """Remove games that appear stale or haven't updated."""
        current_time = time.time()

        for game in games[:]:  # Copy list to iterate safely
            game_id = game.get("id")
            if not game_id:
                continue

            # Check if game data is stale
            timestamps = self.game_update_timestamps.get(game_id, {})
            last_seen = timestamps.get("last_seen", 0)

            if last_seen > 0 and current_time - last_seen > self.stale_game_timeout:
                self.logger.warning(
                    f"Removing stale game {game.get('away_abbr')}@{game.get('home_abbr')} "
                    f"(last seen {int(current_time - last_seen)}s ago)"
                )
                games.remove(game)
                if game_id in self.game_update_timestamps:
                    del self.game_update_timestamps[game_id]
                continue

            # Also check if game appears to be over
            if self._is_game_really_over(game):
                self.logger.debug(
                    f"Removing game that appears over: {game.get('away_abbr')}@{game.get('home_abbr')} "
                    f"(clock={game.get('clock')}, period={game.get('period')}, period_text={game.get('period_text')})"
                )
                games.remove(game)
                if game_id in self.game_update_timestamps:
                    del self.game_update_timestamps[game_id]

    def _idle_live_interval(self) -> int:
        """How long to wait before looking for live games again, when there are none.

        Escalates the longer nothing turns up, and any live game resets it, so
        an in-season gap between games costs at most one escalated wait while
        an out-of-season league stops polling on a live cadence entirely.

        Capped rather than unbounded: the cost of backing off is how late the
        first game after a quiet spell is noticed, and past the cap the saving
        stops being worth that.
        """
        streak = getattr(self, "_empty_live_streak", 0)
        base = self.no_data_interval
        ceiling = getattr(self, "live_idle_max_interval",
                          _DEFAULT_LIVE_IDLE_MAX_SECONDS)
        # The ceiling bounds the un-escalated interval too. The two settings are
        # independent integers with no cross-validation, so base > ceiling is a
        # reachable config -- and returning base unclamped there made the wait
        # *shrink* as the streak grew (3600s at streak 0, 900s at streak 24),
        # the opposite of what the setting named "maximum" promises.
        if streak >= _IDLE_LONG_STREAK:
            return min(int(base * _IDLE_LONG_FACTOR), ceiling)
        if streak >= _IDLE_SHORT_STREAK:
            return min(int(base * _IDLE_SHORT_FACTOR), ceiling)
        return min(base, ceiling)

    def _note_live_fetch(self, found_live: bool) -> None:
        """Record whether a look for live games found any."""
        if found_live:
            if getattr(self, "_empty_live_streak", 0):
                self.logger.info(
                    "Live games found after %d empty check(s); back to the "
                    "live update interval", self._empty_live_streak)
            self._empty_live_streak = 0
        else:
            self._empty_live_streak = getattr(self, "_empty_live_streak", 0) + 1


class SportsRecentSharedMixin:
    """The ``SportsRecent`` bodies identical in all eight scoreboards."""

    def __init__(
        self,
        config: Dict[str, Any],
        display_manager,
        cache_manager,
        logger: logging.Logger,
        sport_key: str,
    ):
        super().__init__(config, display_manager, cache_manager, logger, sport_key)
        self.games_list = []  # Filtered list for display (favorite teams)
        self.current_game_index = 0
        self.last_update = 0
        self.update_interval = self.mode_config.get(
            "recent_update_interval", 3600
        )  # Check for recent games every hour
        self.last_game_switch = 0
        self.game_display_duration = self.mode_config.get("recent_game_duration", 15)
        self._zero_clock_timestamps: Dict[str, float] = {}  # Track games at 0:00

    def _get_zero_clock_duration(self, game_id: str) -> float:
        """Track how long a game has been at 0:00 clock."""
        current_time = time.time()
        if game_id not in self._zero_clock_timestamps:
            self._zero_clock_timestamps[game_id] = current_time
            return 0.0
        return current_time - self._zero_clock_timestamps[game_id]

    def _clear_zero_clock_tracking(self, game_id: str) -> None:
        """Clear tracking when game clock moves away from 0:00 or game ends."""
        if game_id in self._zero_clock_timestamps:
            del self._zero_clock_timestamps[game_id]

