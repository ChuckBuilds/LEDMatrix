"""Sports helpers every scoreboard's ``sports.py`` carries an identical copy of.

Ten helpers are byte-identical (executable AST, docstrings stripped) in the
scoreboard plugins' ``sports.py`` and have no equivalent elsewhere in
``src/common``. The bodies below were copied from those plugin copies at
ledmatrix-plugins ``f09bff2`` (origin/main, 2026-09-14):

- In all nine (afl, baseball, basketball, football, hockey, lacrosse, nrl,
  soccer, ufc): ``_clamp_window``, ``_clamp_seconds``, ``_logo_needs_refresh``
  (with ``_MIN_WINDOW_DAYS`` / ``_MAX_WINDOW_DAYS``), and the ``SportsCore``
  methods ``_mode_customization``, ``_setting_int``,
  ``_reset_dwell_on_reentry`` (with ``_DWELL_REENTRY_GAP_SECONDS``),
  ``_next_switch_index`` and ``_spread_weighted_order``.
- In the eight that share a lineage (all but ufc, which has neither):
  ``_odds_color`` and ``_upcoming_date_and_time_text``.

The module-level helpers become public free functions here (``clamp_window``,
``clamp_seconds``, ``logo_needs_refresh``, ``spread_weighted_order``,
``MIN_WINDOW_DAYS``, ``MAX_WINDOW_DAYS``); the methods keep the plugins' exact
names and signatures on ``SportsHelpersMixin`` so adopting it is deleting the
copies and adding one base. ``test/test_sports_helpers.py`` compares every body
here against every plugin copy when a plugins checkout is available
(``LEDMATRIX_PLUGINS``), so drift in either direction fails a test.

WHY A NEW MODULE, NOT MORE METHODS ON sports_shared
---------------------------------------------------
A plugin that deletes its copy of a method and relies on an *existing* module
having grown it fails at runtime on an older core: ``AttributeError`` mid-render,
usually swallowed into a blank board. The plugin loader cannot see that, and
neither can the plugins' ``scripts/check_min_core_version.py``, which checks
modules. A plugin importing a module that does not exist yet fails at load,
where both can. Plugins import this and floor ``ledmatrix_min_version`` on the
first core release that ships it (see ``CHANGELOG.md``).

``_favorite_key`` is the one method not taken from the plugins: it is the
override point from the since-removed ``src/base_classes`` sports core,
carried here so later phases (shared celebrations and game selection) have a
hardware-free home for the seam. No plugin defines it today and nothing in this module calls it.

WHAT A HOST MUST PROVIDE
------------------------
Derived by walking every ``self.<attr>`` the mixin reads; the host-contract
test in ``test/test_sports_helpers.py`` fails if a read is added without being
listed here.

- ``config`` (dict) -- ``_mode_customization``.
- ``mode_config`` (dict) and ``logger`` -- ``_setting_int``. ``league`` is
  read with ``getattr`` for the warning text only.
- ``games_list`` and ``current_game_index`` -- ``_next_switch_index``; plus
  ``_is_favorite_game`` (called with a game), which stays per-plugin and is
  only called when
  ``favorite_rotation_boost`` is above 1. ``favorite_rotation_boost`` itself
  defaults to 1 on the mixin.
- ``last_game_switch`` -- ``_reset_dwell_on_reentry``, read with ``getattr``
  and written back.
- ``_card_option``, ``_format_game_date`` and ``_format_game_time`` --
  ``_upcoming_date_and_time_text``. All three are on
  ``SportsCoreSharedMixin`` (``src/common/sports_shared.py``).
- ``_element_color`` -- ``_odds_color``, looked up with ``getattr`` and falling
  back to green, so a host without it still works. Also on
  ``SportsCoreSharedMixin``.
- ``SKIN_MODE`` -- optional, read with ``getattr`` by ``_mode_customization``;
  without it the mode overrides are simply not merged.

State the mixin keeps on the host, created on first use (all read with
``getattr``, so nothing needs initialising): ``_last_display_call_monotonic``,
``_switch_order``, ``_switch_order_key``, ``_switch_position``.

Class-level defaults: ``_DWELL_REENTRY_GAP_SECONDS = 5.0`` and
``favorite_rotation_boost = 1``, matching the plugins. A host's own value
shadows them.

MRO
---
No ``__init__`` and no bare ``super()``, so base order does not change what
runs. By convention list it after ``SportsCoreSharedMixin``::

    class SportsCore(SportsCoreSharedMixin, SportsHelpersMixin, ABC):

HARDWARE-FREE
-------------
Nothing here may import ``src.display_manager``, ``src.plugin_system`` or
anything else that reaches ``rgbmatrix`` at module
level; ``test/test_common_is_hardware_free.py`` enforces that for all of
``src/common``. ``logo_needs_refresh`` imports ``src.logo_downloader`` lazily,
exactly as the plugin copy does.
"""

from __future__ import annotations

import time
from typing import Any, ClassVar, Dict, List, Optional, Tuple

__all__ = [
    "MIN_WINDOW_DAYS",
    "MAX_WINDOW_DAYS",
    "clamp_window",
    "clamp_seconds",
    "logo_needs_refresh",
    "spread_weighted_order",
    "SportsHelpersMixin",
]

MIN_WINDOW_DAYS = 1
MAX_WINDOW_DAYS = 60


def clamp_window(value: Any, fallback: int) -> int:
    """Days for one side of the schedule window, or the default if unusable."""
    try:
        days = int(value)
    except (TypeError, ValueError, OverflowError):
        # OverflowError: json parses a bare Infinity and int(inf) raises,
        # which crashed manager init from a hand-edited config.
        return fallback
    return max(MIN_WINDOW_DAYS, min(MAX_WINDOW_DAYS, days))


def clamp_seconds(value: Any, fallback: int, low: int = 5,
                  high: int = 86400) -> int:
    """An interval in seconds, or the fallback when the value is unusable."""
    try:
        seconds = int(value)
    except (TypeError, ValueError, OverflowError):
        # OverflowError: json parses bare Infinity by default and int(inf)
        # raises -- the same gap clamp_window above already covers.
        return fallback
    return max(low, min(high, seconds))


def logo_needs_refresh(logo_file) -> bool:
    """True if this file is a placeholder stale enough to retry the real logo.

    A failed logo download is cached as a placeholder wearing the real logo's
    filename, so "the file exists" is not proof the logo was ever fetched.
    Without this check one transient failure leaves a team a grey box forever.

    Returns False on a core that predates placeholder marking, which keeps the
    previous behaviour rather than breaking the load.
    """
    # Imported from the core by its full path, never as a bare name: a
    # deferred bare-name import can bind another plugin's vendored
    # logo_downloader once the core isolates top-level plugin modules.
    try:
        from src.logo_downloader import (
            PLACEHOLDER_RETRY_SECONDS,
            is_placeholder_logo,
            placeholder_age_seconds,
        )
    except ImportError:
        return False

    try:
        if not is_placeholder_logo(logo_file):
            return False
        age = placeholder_age_seconds(logo_file)
        return age is None or age >= PLACEHOLDER_RETRY_SECONDS
    except Exception:
        return False


def spread_weighted_order(weights: List[int]) -> List[int]:
    """Indices into ``weights``, each repeated by its weight and spread out.

    Each index keeps its own slot and places its extra turns at even
    fractions of the rotation after it, wrapping round. That keeps the
    list's schedule order for everything else and spaces a favourite's
    repeats evenly *around the loop* -- the live rotation's smooth
    weighted round-robin schedules a boosted game first and last, so a
    rotation that wraps shows it back to back. Equal weights come back in
    plain order, so a boost that applies to no card changes nothing.

    Repeats are kept apart only where the ratio leaves room: once one
    weight exceeds all the others combined, no cyclic order can separate
    its turns ([3, 1, 1] gives [0, 1, 0, 2, 0]). Each index still gets
    exactly its weight in turns -- the configured ratio wins over spacing.
    """
    count = len(weights)
    slots = []
    for index, weight in enumerate(weights):
        for turn in range(weight):
            slots.append(((index + turn * count / weight) % count, turn > 0, index))
    return [index for _, _, index in sorted(slots)]


class SportsHelpersMixin:
    """The ``SportsCore`` methods identical across the scoreboards' sports.py.

    Constructor-free; keeps lazy state on its host (see the module docstring
    for that state, the host contract and base-order guidance).
    """

    #: Longest gap between two display() calls that still counts as one
    #: on-screen stint. Frames arrive many times a second while a mode is on
    #: the panel; between mode blocks the gap is the length of every other
    #: mode's block -- a minute or more. Anything past a few seconds can only
    #: be a block boundary, or the very first frame after startup.
    _DWELL_REENTRY_GAP_SECONDS: ClassVar[float] = 5.0

    #: Turns a favourite's card gets per turn of any other card in switch
    #: mode. The plugins set it per instance from config; this default keeps
    #: a host that does not on the plain rotation.
    favorite_rotation_boost: int = 1

    def _favorite_key(self, game: Dict, side: str) -> Optional[str]:
        """Override point: which view-model field identifies a team when
        matching against ``favorite_teams``.

        ``side`` is ``"home"`` or ``"away"``. The default is the team
        abbreviation -- what eight of the nine scoreboards match on, and what
        users type into their favorites list.

        NRL needs the team **id** instead, because NRL abbreviations are not
        unique: "NEW" is both Newcastle Knights and New Zealand Warriors,
        "CAN" both Canberra Raiders and Canterbury Bulldogs. It is a seam
        rather than a branch so core never has to learn the string "nrl"::

            def _favorite_key(self, game, side):
                return str(game.get(f"{side}_id"))

        An override that stringifies should note that a missing id becomes the
        literal ``"None"``, which would spuriously match a favorites list
        containing that string. The default returns ``None`` for a missing
        abbreviation, which never matches.

        Carried from the since-removed ``src/base_classes`` sports core for
        later phases; nothing in this module calls it yet.
        """
        return game.get(f"{side}_abbr")

    def _mode_customization(self) -> dict:
        """``customization`` with this mode's overrides merged over it.

        SportsUpcoming / SportsRecent / SportsLive are separate instances
        with their own SKIN_MODE, so merging once here makes every
        per-element lookup mode-aware without changing one of them.

        ``None`` in a mode block means "inherit", which is what lets a user
        restyle one element on live cards and leave everything else
        following the settings above. It has to stay distinct from 0: a mode
        y_offset of 0 means "sit at the base position", not "no preference".
        """
        customization = self.config.get('customization', {})
        if not isinstance(customization, dict):
            return {}
        mode = getattr(self, 'SKIN_MODE', None)
        if not mode:
            return customization
        modes = customization.get('modes')
        block = modes.get(mode) if isinstance(modes, dict) else None
        if not isinstance(block, dict):
            return customization

        merged = dict(customization)
        for element, override in block.items():
            if element == 'layout' or not isinstance(override, dict):
                continue
            base = merged.get(element)
            base = dict(base) if isinstance(base, dict) else {}
            base.update({k: v for k, v in override.items() if v is not None})
            merged[element] = base

        mode_layout = block.get('layout')
        if isinstance(mode_layout, dict):
            base_layout = merged.get('layout')
            new_layout = dict(base_layout) if isinstance(base_layout, dict) else {}
            for element, axes in mode_layout.items():
                if not isinstance(axes, dict):
                    continue
                current = new_layout.get(element)
                current = dict(current) if isinstance(current, dict) else {}
                current.update({k: v for k, v in axes.items() if v is not None})
                new_layout[element] = current
            merged['layout'] = new_layout
        return merged

    def _setting_int(self, key: str, default: int, low: int, high: int) -> int:
        """A count from config, clamped to the range its schema declares.

        The schema constrains these, but config.json can be hand-edited or
        written by an older tool, and a string or a negative here does not
        raise where anyone would see it -- it raises inside update()'s own
        try/except, which shows up as a mode that silently renders nothing.
        Same shape as the favorite_live_boost clamp in the plugins.
        """
        try:
            return max(low, min(high, int(self.mode_config.get(key, default))))
        except (TypeError, ValueError, OverflowError):
            # OverflowError: json parses a bare Infinity, and int(inf) raises
            # -- from __init__, outside any try/except, so the manager would
            # fail to construct instead of falling back.
            self.logger.warning(
                "%s: ignoring unusable %s=%r, using %s",
                getattr(self, "league", "?"), key,
                self.mode_config.get(key), default,
            )
            return default

    def _reset_dwell_on_reentry(self) -> bool:
        """Give the current card a full turn when this mode (re)takes the panel.

        The dwell clock (last_game_switch) keeps running while the mode is off
        screen, so on re-entry it was always long expired and the first
        display() call advanced immediately: the card cut off by the end of
        the previous block was skipped instead of shown -- measured at one in
        five card transitions on a 30s block of 15s cards -- and after a
        service restart the clock started at manager construction, seconds
        before the first frame, shaving that much off the first card. Both are
        the same defect: the dwell clock counting time the viewer never saw.

        Returns True when the dwell was reset, so the caller forces a redraw.
        The one-frame card at the end of a block (the advance that races the
        controller's mode switch) still renders -- this reset is what turns it
        into the card that opens the next block with a full turn, instead of
        one the rotation skipped.
        """
        # getattr, and zero treated as "never displayed": the managers are
        # constructed in several places -- the plugin tests among them -- not
        # all of which set every attribute, and a freshly booted Pi can reach
        # the first frame while time.monotonic() itself is still under the
        # gap threshold, which would make `now - 0.0` look like one stint.
        last = getattr(self, "_last_display_call_monotonic", 0.0)
        now = time.monotonic()
        self._last_display_call_monotonic = now
        if last > 0.0 and now - last < self._DWELL_REENTRY_GAP_SECONDS:
            return False
        if getattr(self, "last_game_switch", 0) <= 0:
            # Zero is the live screen's "no game shown yet" sentinel with its
            # own handling; overwriting it here would hide the first game's
            # arrival from that logic.
            return False
        self.last_game_switch = time.time()
        return True

    _spread_weighted_order = staticmethod(spread_weighted_order)

    def _next_switch_index(self) -> int:
        """The games_list index switch mode shows next.

        favorite_rotation_boost gives a favourite's card that many turns for
        every one turn another card gets, spread through the rotation and kept
        apart wherever the other cards leave room (a boost above the number of
        other cards makes some repeats adjacent; the ratio is kept either way).
        games_list itself stays one entry per game -- the
        cycle-duration count, the scroll strip and the other-games re-cut all
        read it -- so the weighting is an order walked over it instead.

        The order is rebuilt whenever the list's games change, and the walk
        resyncs from current_game_index whenever the two disagree: update()
        and the other-games rotation both set the index directly when they
        swap a list in, and the card on screen is where the walk resumes.

        Called with _games_lock held and games_list non-empty.
        """
        count = len(self.games_list)
        boost = getattr(self, "favorite_rotation_boost", 1)
        if boost <= 1 or count < 2:
            return (self.current_game_index + 1) % count
        key = (boost, tuple(g.get("id") for g in self.games_list))
        if getattr(self, "_switch_order_key", None) != key:
            self._switch_order = self._spread_weighted_order(
                [boost if self._is_favorite_game(g) else 1 for g in self.games_list]
            )
            self._switch_order_key = key
            self._switch_position = -1
        order = self._switch_order
        position = getattr(self, "_switch_position", -1)
        if not 0 <= position < len(order) or order[position] != self.current_game_index:
            position = (order.index(self.current_game_index)
                        if self.current_game_index in order else -1)
        position = (position + 1) % len(order)
        self._switch_position = position
        return order[position]

    def _odds_color(self) -> Tuple[int, int, int]:
        """Colour for the odds text; the green it always drew unless configured.

        Guarded with getattr because not every class that reaches
        _draw_dynamic_odds carries the element-colour helper -- the plugins'
        own test harnesses build minimal manager objects, and a bare
        AttributeError here is swallowed by the surrounding except, which
        drops the odds off the card instead of failing loudly.
        """
        getter = getattr(self, "_element_color", None)
        if getter is None:
            return (0, 255, 0)
        try:
            return getter("odds_text", (0, 255, 0))  # pylint: disable=not-callable
        except Exception:
            return (0, 255, 0)

    def _upcoming_date_and_time_text(self, game_date: str, game_time: str,
                                     game: Optional[Dict] = None) -> Tuple[str, str]:
        """The formatted (date, time) pair, blanked by switch_show_date/_time.

        Deliberately not the shared show_date/show_time: those governed only
        the scroll and Vegas cards before this display read the block, so a
        config that had turned them off there would silently blank a scorebug
        that has always drawn both lines. The switch keys default to True for
        the same reason switch_upcoming_center defaults to "date_time" -- an
        untouched panel keeps rendering exactly what it rendered before.
        """
        date_text = (self._format_game_date(game_date, game)
                     if self._card_option("switch_show_date", True) else "")
        time_text = (self._format_game_time(game_time)
                     if self._card_option("switch_show_time", True) else "")
        return date_text, time_text
