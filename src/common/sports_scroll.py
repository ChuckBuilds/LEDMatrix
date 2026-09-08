"""Shared scroll-display scaffolding for the sports scoreboards.

Ten plugins ship a `scroll_display.py`. A method-level comparison of the eight
that share a shape (f1 and ufc are genuine forks) found a sharp split, and this
module is drawn along it rather than around all of it:

* The **orchestration layer is converged** — ``get_all_vegas_content_items`` is
  byte-identical in all eight, and ``clear_all``, ``get_scroll_info``,
  ``get_dynamic_duration``, ``is_complete`` and ``display_frame`` are 96-100%
  similar. That is what lives here.
* The **content layer has genuinely diverged** — ``prepare_scroll_content`` has
  eight distinct bodies across eight plugins (145 lines, 53% similarity at
  worst) and ``_load_separator_icons`` seven (6% at worst). Those build each
  sport's game cards and icon strip; they are *not* drift to be merged but
  per-sport rendering. They stay override points here, permanently.

Promoting the content layer would be exactly the mistake
``docs/SPORTS_UNIFICATION.md`` warns against — merging on the intuition that
same-named methods are the same method. Same name, different job.

The one behavior this module adds over the plugin copies is native support for
``global_config['target_fps']``: the bundled copies hardcode ~100 FPS via
``scroll_delay=0.01`` and never consult the global smooth-scrolling target. A
plugin inheriting from here gets it for free.

Usage::

    class HockeyScrollDisplay(SportsScrollDisplay):
        SCROLL_LEAGUE_KEYS = ("nhl", "ncaa_mens", "ncaam_hockey")

        def prepare_scroll_content(self, games, game_type, leagues, rankings=None):
            ...                      # build this sport's cards

    class HockeyScrollDisplayManager(SportsScrollDisplayManager):
        display_class = HockeyScrollDisplay
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from PIL import Image

from src.common import scroll_config
from src.common.scroll_helper import ScrollHelper

logger = logging.getLogger(__name__)

#: Defaults every copy agreed on. A subclass overrides
#: :meth:`SportsScrollDisplay.scroll_settings_defaults` to change them —
#: the soccer lineage uses a 24px gap and min/max duration keys instead.
DEFAULT_SCROLL_SETTINGS: Dict[str, Any] = {
    "scroll_speed": 50.0,
    "scroll_delay": 0.01,
    "gap_between_games": 48,
    "show_league_separators": True,
    "dynamic_duration": True,
}

#: Pacing to assume when scroll_delay is 0, i.e. the plugin has not set one.
#: Only used to interpret this module's own px/frame config shape; the speed
#: bounds and the px/s -> px/frame conversion belong to scroll_config now.
ASSUMED_FPS_WHEN_UNPACED = 100.0


class SportsScrollDisplay:
    """One scrolling strip of game cards.

    Subclasses supply the content (:meth:`prepare_scroll_content`) and,
    optionally, the per-sport league ladder and separator icons. Everything
    else — helper configuration, frame pumping, completion, state — is here.
    """

    #: Config keys to walk when looking for per-league ``scroll_settings``,
    #: most-preferred first. A sport's own league names, which is the *only*
    #: reason the eight copies of ``_get_scroll_settings`` differ. Empty means
    #: the plugin has no per-league scroll settings.
    SCROLL_LEAGUE_KEYS: tuple = ()

    #: Config block holding scroll settings when the plugin keeps them in one
    #: place rather than per league (the afl/nrl/soccer shape).
    SCROLL_CONFIG_KEY: Optional[str] = None

    def __init__(
        self,
        display_manager,
        config: Dict[str, Any],
        custom_logger: Optional[logging.Logger] = None,
        global_config: Optional[Dict[str, Any]] = None,
    ):
        """
        :param display_manager: the core display manager
        :param config: the plugin's configuration
        :param custom_logger: the plugin's logger, so scroll lines are attributed
        :param global_config: the LEDMatrix global config — the source of
            ``target_fps``. Optional so an older caller that does not pass it
            keeps working at the config-derived pacing.
        """
        self.display_manager = display_manager
        self.config = config
        self.logger = custom_logger or logger
        self.global_config = global_config or {}

        if getattr(display_manager, "matrix", None) is not None:
            self.display_width = display_manager.matrix.width
            self.display_height = display_manager.matrix.height
        else:
            self.display_width = getattr(display_manager, "width", 128)
            self.display_height = getattr(display_manager, "height", 32)

        self.scroll_helper = ScrollHelper(
            self.display_width, self.display_height, self.logger
        )
        self._configure_scroll_helper()

        self._logo_cache: Dict[str, Image.Image] = {}
        self._separator_icons: Dict[str, Image.Image] = {}
        self._load_separator_icons()

        self._current_games: List[Dict] = []
        self._current_game_type: str = ""
        self._current_leagues: List[str] = []
        self._vegas_content_items: List[Image.Image] = []
        self._is_scrolling = False
        self._scroll_start_time: Optional[float] = None
        self._last_log_time: float = 0
        self._log_interval: float = 5.0
        self._frame_count: int = 0
        self._fps_sample_start: float = time.time()

    # ------------------------------------------------------------------
    # Override points
    # ------------------------------------------------------------------

    def prepare_scroll_content(
        self,
        games: List[Dict],
        game_type: str,
        leagues: List[str],
        rankings_cache: Optional[Dict[str, int]] = None,
    ) -> bool:
        """Render ``games`` into one wide image and hand it to the scroll helper.

        **Per-sport by nature, not by drift** — the eight plugin copies have
        eight different bodies because each draws its own card. Implementations
        build the strip, hand it over with
        ``self.scroll_helper.set_scrolling_image(...)`` (or
        ``create_scrolling_image(...)`` from a list of cards), and record
        ``self._current_games`` / ``_current_game_type`` / ``_current_leagues``.

        :returns: True when there is content to scroll.
        """
        raise NotImplementedError(
            f"{type(self).__name__} must implement prepare_scroll_content(); "
            "it builds this sport's game cards and is not shared code."
        )

    def _load_separator_icons(self) -> None:
        """Populate ``self._separator_icons``. Per-sport; no-op by default."""

    def scroll_settings_defaults(self) -> Dict[str, Any]:
        """The baseline scroll settings before any config is applied."""
        defaults = dict(DEFAULT_SCROLL_SETTINGS)
        defaults["game_card_width"] = self.display_width
        return defaults

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------

    def _get_scroll_settings(self, league: Optional[str] = None) -> Dict[str, Any]:
        """Resolve scroll settings: defaults, then the most specific override.

        Precedence: the named ``league``, then each entry of
        :attr:`SCROLL_LEAGUE_KEYS` in order, then :attr:`SCROLL_CONFIG_KEY`.
        The eight plugin copies implement exactly this and differ only in which
        league names they walk — which is why the ladder is data here rather
        than a body per sport.
        """
        settings = self.scroll_settings_defaults()

        candidates: List[str] = []
        if league:
            candidates.append(league)
        candidates.extend(self.SCROLL_LEAGUE_KEYS)
        for key in candidates:
            override = (self.config.get(key) or {}).get("scroll_settings")
            if override:
                return {**settings, **override}

        if self.SCROLL_CONFIG_KEY:
            override = self.config.get(self.SCROLL_CONFIG_KEY) or {}
            if override:
                return {**settings, **override}
        return settings

    def _resolve_target_fps(self) -> Optional[float]:
        """The global smooth-scrolling FPS target, or None to keep config pacing.

        Coerced before use: a malformed value in the global config must degrade
        to the existing ``scroll_delay`` pacing, never raise on a display path.
        """
        raw = self.global_config.get("target_fps") or self.global_config.get(
            "scroll_target_fps"
        )
        try:
            return float(raw) if raw is not None else None
        except (TypeError, ValueError):
            self.logger.debug("Ignoring unusable target_fps: %r", raw)
            return None

    def _coerce_float(self, value: Any, default: float) -> float:
        """A usable float from config, or ``default``.

        ``dict.get(key, default)`` only helps when the key is *absent*; a key
        present with ``null`` or a string returns that value verbatim and blows
        up in the arithmetic below — inside ``__init__``, so the whole display
        fails to construct.
        """
        if value is None:
            return default
        try:
            return float(value)
        except (TypeError, ValueError):
            self.logger.warning(
                "Ignoring unusable scroll setting %r; using %s", value, default
            )
            return default

    def _configure_scroll_helper(self) -> None:
        """Apply config to the scroll helper. Safe to call again after a change."""
        settings = self._get_scroll_settings()

        dynamic_duration = bool(settings.get("dynamic_duration", True))
        self.scroll_helper.set_dynamic_duration_settings(
            enabled=dynamic_duration,
            min_duration=settings.get("min_duration", 30),
            max_duration=settings.get("max_duration", 600),
            buffer=0.2,  # ensure the strip clears the panel completely
        )

        # Speed goes through scroll_config, which every other scrolling plugin
        # already uses. What must NOT happen is handing it this module's
        # settings dict: the two read the same key names with different
        # meanings, and the collision is a factor of 1/scroll_delay.
        #
        #   sports_scroll: scroll_speed is px/SECOND; scroll_delay is only the
        #                  frame period used to reach px/frame.
        #   scroll_config: scroll_speed is px per STEP, so px/s = speed/delay.
        #
        # Passing {"scroll_speed": 50.0, "scroll_delay": 0.01} straight through
        # resolves to 5000 px/s (clamped to 500) instead of 50. So this module
        # keeps ownership of reading its own config -- _get_scroll_settings
        # merges the league overrides -- and hands the resolver a plain px/s.
        pixels_per_second = self._resolve_pixels_per_second(settings)

        resolved = scroll_config.configure(
            self.scroll_helper,
            plugin_config=None,
            global_config=self.global_config,
            default_pixels_per_second=pixels_per_second,
            display_manager=self.display_manager,
            plugin_logger=self.logger,
            refresh_hz=self._resolve_refresh_hz(),
        )
        self._scroll_settings = resolved
        self.logger.info(
            "ScrollHelper configured: %s (requested %.1f px/s), "
            "dynamic_duration=%s",
            resolved.describe(),
            pixels_per_second, dynamic_duration,
        )

    def _resolve_pixels_per_second(self, settings: Dict[str, Any]) -> float:
        """This module's config shape, expressed as plain pixels per second.

        ``scroll_speed`` is already px/s here. ``scroll_delay`` only matters
        when a caller supplied px/frame instead, which the 0 case covers.
        """
        scroll_speed = self._coerce_float(settings.get("scroll_speed"), 50.0)
        scroll_delay = self._coerce_float(settings.get("scroll_delay"), 0.01)
        if scroll_delay <= 0:
            return scroll_speed * ASSUMED_FPS_WHEN_UNPACED
        return scroll_speed

    def _resolve_refresh_hz(self) -> Optional[float]:
        """The panel refresh the crisp ladder should be computed against.

        Prefers the configured hardware refresh. Falls back to the global
        ``target_fps``/``scroll_target_fps`` this module has always honoured:
        under the old model that key *was* the rate frames were presented at,
        so it is the faithful translation for anyone who set it. Returning
        None lets scroll_config apply its own default.
        """
        hardware = scroll_config.refresh_hz_from_config(self.global_config)
        if hardware and hardware != scroll_config.DEFAULT_REFRESH_HZ:
            return hardware
        return self._resolve_target_fps() or hardware or None

    def _scroll_frame_hold(self) -> int:
        """Refreshes to hold each frame for, from the resolved settings."""
        return getattr(getattr(self, "_scroll_settings", None), "frame_hold", 1)

    # ------------------------------------------------------------------
    # Frame pumping
    # ------------------------------------------------------------------

    def display_scroll_frame(self) -> bool:
        """Advance and render one frame.

        :returns: True if a frame was drawn; False when there is no content or
            the frame could not be rendered.
        """
        if not self.scroll_helper.cached_image:
            return False

        try:
            # Inside the try, not before it: advancing the position and cropping
            # the visible slice are as capable of raising as the display push,
            # and the promise below is that no frame failure reaches the
            # plugin's loop.
            self.scroll_helper.update_scroll_position()
            visible = self.scroll_helper.get_visible_portion()
            if not visible:
                return False

            # Tell the core the panel is scrolling, and for how many
            # refreshes to hold each frame. Without this the frame hold is
            # never applied -- so a speed the ladder made crisp still presents
            # a new frame every refresh and judders -- and, because deferred
            # updates only run while nothing is scrolling, core would run
            # blocking work in the middle of this scroll.
            if hasattr(self.display_manager, "set_scrolling_state"):
                self.display_manager.set_scrolling_state(
                    True, frame_hold=self._scroll_frame_hold())

            self.display_manager.image = visible
            self.display_manager.update_display()
            self._frame_count += 1
            self.scroll_helper.log_frame_rate()
            self._log_scroll_progress()
        except Exception:
            # A display failure must not propagate into the plugin's loop.
            self.logger.exception("Error displaying scroll frame")
            return False
        return True

    def _log_scroll_progress(self) -> None:
        """Emit a throttled progress line."""
        now = time.time()
        if now - self._last_log_time < self._log_interval:
            return
        self._last_log_time = now
        elapsed = now - self._fps_sample_start
        fps = self._frame_count / elapsed if elapsed > 0 else 0.0
        self.logger.debug(
            f"Scrolling {len(self._current_games)} {self._current_game_type} "
            f"game(s) at {fps:.1f} FPS"
        )

    def is_scroll_complete(self) -> bool:
        """True when the strip has scrolled fully past the panel."""
        complete = self.scroll_helper.is_scroll_complete()
        if complete:
            self._release_scrolling_state()
        return complete

    def _release_scrolling_state(self) -> None:
        """Tell the core this display is no longer scrolling.

        The scrolling flag and the frame hold are global to the display
        manager, so leaving them set holds every other plugin's frames too.
        """
        if hasattr(self.display_manager, "set_scrolling_state"):
            self.display_manager.set_scrolling_state(False)

    def reset_scroll(self) -> None:
        """Return the strip to its starting position, keeping the content."""
        self.scroll_helper.reset_scroll()
        self._frame_count = 0
        self._fps_sample_start = time.time()
        self.logger.debug("Scroll position reset")

    def clear(self) -> None:
        """Drop cached content and reset tracking state."""
        self.scroll_helper.clear_cache()
        self._current_games = []
        self._current_game_type = ""
        self._current_leagues = []
        self._vegas_content_items = []
        self._is_scrolling = False
        self._scroll_start_time = None
        self._release_scrolling_state()
        self.logger.debug("Scroll display cleared")

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def get_dynamic_duration(self) -> int:
        """How long this content needs to scroll fully, in seconds."""
        return self.scroll_helper.get_dynamic_duration()

    def has_cached_content(self) -> bool:
        """Whether content is prepared and ready to scroll."""
        return bool(self.scroll_helper.cached_image)

    def get_current_game_count(self) -> int:
        return len(self._current_games)

    def get_current_leagues(self) -> List[str]:
        return list(self._current_leagues)

    def get_scroll_info(self) -> Dict[str, Any]:
        """Helper state plus this display's tracking state, for logging/debug."""
        info = self.scroll_helper.get_scroll_info()
        info.update(
            {
                "game_count": len(self._current_games),
                "game_type": self._current_game_type,
                "leagues": self._current_leagues,
                "is_scrolling": self._is_scrolling,
            }
        )
        return info


class SportsScrollDisplayManager:
    """One :class:`SportsScrollDisplay` per game type ('live'/'recent'/'upcoming').

    Subclasses set :attr:`display_class`; everything else was near-identical
    across the eight plugin copies.
    """

    #: The SportsScrollDisplay subclass to instantiate per game type.
    display_class = SportsScrollDisplay

    def __init__(
        self,
        display_manager,
        config: Dict[str, Any],
        custom_logger: Optional[logging.Logger] = None,
        global_config: Optional[Dict[str, Any]] = None,
    ):
        self.display_manager = display_manager
        self.config = config
        self.logger = custom_logger or logger
        self.global_config = global_config or {}
        self._scroll_displays: Dict[str, SportsScrollDisplay] = {}
        # "" rather than None, matching SportsScrollDisplay's own empty value —
        # both are falsy, so `game_type or self._current_game_type` behaved
        # either way, but two spellings of "nothing active" across two classes
        # is a trap for anyone comparing state between them.
        self._current_game_type: str = ""

    def get_scroll_display(self, game_type: str) -> SportsScrollDisplay:
        """The display for ``game_type``, created on first use."""
        if game_type not in self._scroll_displays:
            self._scroll_displays[game_type] = self.display_class(
                self.display_manager,
                self.config,
                self.logger,
                global_config=self.global_config,
            )
        return self._scroll_displays[game_type]

    def prepare_and_display(
        self,
        games: List[Dict],
        game_type: str,
        leagues: List[str],
        rankings_cache: Optional[Dict[str, int]] = None,
    ) -> bool:
        """Build content for ``game_type`` and make it the active strip."""
        scroll_display = self.get_scroll_display(game_type)
        try:
            success = scroll_display.prepare_scroll_content(
                games, game_type, leagues, rankings_cache
            )
        except Exception:
            # prepare_scroll_content is subclass-implemented and builds cards
            # straight from feed data, which is exactly where this PR's other
            # crashes came from. One sport's bad payload must not take down the
            # shared orchestration for the others.
            self.logger.exception(
                "Error preparing scroll content for game_type=%s", game_type
            )
            return False
        if success:
            self._current_game_type = game_type
        return success

    def display_frame(self, game_type: Optional[str] = None) -> bool:
        """Advance the active strip (or a named one) by one frame."""
        game_type = game_type or self._current_game_type
        if not game_type:
            return False
        scroll_display = self._scroll_displays.get(game_type)
        if scroll_display is None:
            return False
        return scroll_display.display_scroll_frame()

    def is_complete(self, game_type: Optional[str] = None) -> bool:
        """True when the strip has finished — including when there isn't one,
        so a caller waiting on completion is never wedged."""
        game_type = game_type or self._current_game_type
        if not game_type:
            return True
        scroll_display = self._scroll_displays.get(game_type)
        if scroll_display is None:
            return True
        return scroll_display.is_scroll_complete()

    def clear_all(self) -> None:
        """Clear every display and forget which one was active."""
        for scroll_display in self._scroll_displays.values():
            scroll_display.clear()
        self._current_game_type = ""

    def get_all_vegas_content_items(self) -> List[Image.Image]:
        """Every display's Vegas items, for splicing into the marquee."""
        items: List[Image.Image] = []
        for scroll_display in self._scroll_displays.values():
            vegas_items = getattr(scroll_display, "_vegas_content_items", None)
            if vegas_items:
                items.extend(vegas_items)
        return items
