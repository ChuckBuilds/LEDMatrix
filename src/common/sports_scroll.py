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

What this module adds over the plugin copies is pacing through
:mod:`src.common.scroll_config`, the resolver every other scroller uses: the
configured px/s is snapped to a whole-pixel speed for the panel's refresh, the
helper steps a fixed number of pixels per presented frame, and each drawn frame
publishes the frame hold to the display manager. Speed depends only on
``scroll_speed`` and the panel refresh
(``display.hardware.limit_refresh_rate_hz``) -- not on the global
``target_fps``, and not on ``scroll_delay``, which is kept in the settings for
compatibility only.

Usage::

    class HockeyScrollDisplay(SportsScrollDisplay):
        SCROLL_LEAGUE_KEYS = ("nhl", "ncaa_mens", "ncaam_hockey")

        def prepare_scroll_content(self, games, game_type, leagues, rankings=None):
            ...                      # build this sport's cards

    class HockeyScrollDisplayManager(SportsScrollDisplayManager):
        display_class = HockeyScrollDisplay
"""

from __future__ import annotations

import functools
import logging
import threading
import time
import weakref
from collections import OrderedDict
from typing import Any, Callable, Dict, List, Optional, Tuple

from PIL import Image

from src.common import scroll_config, sports_vegas
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
        :param global_config: the LEDMatrix global config, consulted for the
            panel refresh when the display manager cannot report one.
            Optional so an older caller that does not pass it keeps working.
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
        #   sports_scroll: scroll_speed is px/SECOND; scroll_delay is ignored
        #                  for pacing (see _resolve_pixels_per_second).
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

        ``scroll_speed`` is already px/s here, and it is the whole answer.
        ``scroll_delay`` is kept in the settings for compatibility but is
        ignored for pacing: the frame rate is the panel refresh divided by the
        frame hold scroll_config picks, so no positive delay changes the speed.
        The one exception is a delay of exactly 0 (below every scoreboard
        schema's minimum), which is read as ``scroll_speed`` being px per
        frame at ``ASSUMED_FPS_WHEN_UNPACED``.
        """
        scroll_speed = self._coerce_float(settings.get("scroll_speed"), 50.0)
        scroll_delay = self._coerce_float(settings.get("scroll_delay"), 0.01)
        if scroll_delay <= 0:
            return scroll_speed * ASSUMED_FPS_WHEN_UNPACED
        return scroll_speed

    def _resolve_refresh_hz(self) -> float:
        """The panel refresh the crisp ladder should be computed against.

        This has to be the rate frames are actually presented at, because the
        helper advances a fixed number of whole pixels per presented frame and
        the display manager holds each frame for ``frame_hold`` refreshes. A
        ladder built for any other rate plays back at the wrong speed: built
        for 60 Hz and shown on a 100 Hz panel, it runs 100/60 too fast.

        So it is the display manager's ``refresh_hz`` (what the panel is
        driven at), then ``display.hardware.limit_refresh_rate_hz`` from the
        global config, then scroll_config's default. The global ``target_fps``
        is deliberately NOT consulted: before frame-locked presentation it was
        the rate frames were shown at, but now honouring it only turned the
        General tab's "Scroll Frame Rate" into a scoreboard speed multiplier.
        """
        reported = getattr(self.display_manager, "refresh_hz", None)
        # A real number only: a stub or a mock answering for anything must not
        # become the refresh rate.
        if (isinstance(reported, (int, float)) and not isinstance(reported, bool)
                and reported > 0):
            return float(reported)
        return scroll_config.refresh_hz_from_config(self.global_config)

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
            # refreshes to hold each frame. The helper advances a fixed number
            # of whole pixels per presented frame, so without the hold a new
            # frame is presented every refresh and the scroll runs frame_hold
            # times too fast. And because deferred updates only run while
            # nothing is scrolling, core would otherwise run blocking work in
            # the middle of this scroll.
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

    # ------------------------------------------------------------------
    # Live Vegas cards
    # ------------------------------------------------------------------
    #
    # One live element per game (src/plugin_system/vegas_elements.py): the
    # ticker swaps a card in place when its game changes. A sport opts in by
    # implementing make_vegas_renderer(); everything else is here.

    def make_vegas_renderer(self, card_width: int,
                            rankings_cache: Optional[Dict[str, int]] = None) -> Any:
        """The renderer this sport draws one game card with, at ``card_width``.

        **Override point.** Return the object whose ``render_game_card(game,
        game_type)`` draws one card exactly ``card_width`` wide at the display's
        height -- the one prepare_scroll_content already builds -- without the
        black padding prepare_scroll_content adds around each card (the ticker
        adds its own). Raising NotImplementedError, the default, keeps the
        plugin on its ordinary Vegas content.
        """
        raise NotImplementedError(
            f"{type(self).__name__} has no live Vegas cards (make_vegas_renderer)")

    def _determine_game_type(self, game: Dict[str, Any]) -> str:
        """The card a game is drawn as: 'live', 'recent' or 'upcoming'.

        From the game's state; a sport whose scroll display decides it
        differently (most define their own) overrides this.
        """
        return {'in': 'live', 'post': 'recent'}.get(sports_vegas._state(game), 'upcoming')

    def render_vegas_card(self, renderer: Any, game: Dict[str, Any]) -> Image.Image:
        """Draw one game's card. Override only if the renderer is called differently."""
        card: Image.Image = renderer.render_game_card(game, self._determine_game_type(game))
        return card

    def vegas_separator(self, league: str) -> Optional[Image.Image]:
        """The league separator shown before a league's cards, if there is an icon."""
        icon = self._separator_icons.get(league)
        if icon is None:
            return None
        gap = self._vegas_settings(league).get("gap_between_games", 48)
        pad = max(4, int(gap) // 2)
        image = Image.new('RGB', (icon.width + pad * 2, self.display_height), (0, 0, 0))
        mask = icon if icon.mode == 'RGBA' else None
        image.paste(icon, (pad, (self.display_height - icon.height) // 2), mask)
        return image

    def _vegas_memo(self) -> Dict[Any, Any]:
        """Per-size, per-config memo for the live path; emptied when either changes."""
        stamp = (self.display_width, self.display_height, id(self.config))
        memo: Optional[Tuple[Any, Dict[Any, Any]]] = getattr(self, '_vegas_memo_store', None)
        if memo is None or memo[0] != stamp:
            memo = (stamp, {})
            self._vegas_memo_store = memo
        store: Dict[Any, Any] = memo[1]
        return store

    def _vegas_settings(self, league: Optional[str]) -> Dict[str, Any]:
        """A league's scroll settings, looked up once per size and config.

        The live path asks after every update; a sport's settings lookup can
        be expensive (sizing the default card width builds probe renderers).
        """
        memo = self._vegas_memo()
        key = ('settings', league)
        if key not in memo:
            memo[key] = dict(self._get_scroll_settings(league))
        settings: Dict[str, Any] = memo[key]
        return settings

    def _vegas_renderer(self, card_width: int,
                        rankings_cache: Optional[Dict[str, int]]) -> Any:
        """The sport's renderer for one card width, built once rather than per slate.

        Building one loads fonts and, for the default card width, probes the
        layout; the scroll path pays that on every prepare, which the live
        path would repeat on every update.
        """
        memo = self._vegas_memo()
        key = ('renderer', card_width)
        if key not in memo:
            memo[key] = self.make_vegas_renderer(card_width, rankings_cache)
        renderer = memo[key]
        if hasattr(renderer, 'set_rankings_cache'):
            # Every time, empty included: the renderer is reused across
            # slates, and ranks cleared since must not stay drawn.
            renderer.set_rankings_cache(rankings_cache or {})
        return renderer

    def build_vegas_elements(
        self,
        games: List[Dict[str, Any]],
        leagues: List[str],
        rankings_cache: Optional[Dict[str, int]] = None,
        fingerprint: Optional[Callable[[Dict[str, Any]], Any]] = None,
        now: Optional[float] = None,
    ) -> Optional[List[Any]]:
        """The slate as live Vegas elements: one card per game, separators between leagues.

        Only cards whose fingerprint changed are drawn; the rest come from the
        cache. ``fingerprint(game)`` should return what the card draws (the
        plugin's own signature fields, the clock included for live games); by
        default the whole game dict is used, which redraws on any change. The
        teams' ranks from ``rankings_cache`` count too: the renderer draws
        them from there, not from the game.

        Raises NotImplementedError when the sport has no make_vegas_renderer.
        """
        from src.plugin_system.vegas_elements import VegasElement

        games = sports_vegas.dedupe_games(games)
        if not games:
            return None
        # Settings follow each game's own league, not the slate's first one:
        # a card's width must not change because another league has no games
        # today (the ticker refuses a redraw of another width).
        first = self._vegas_settings(leagues[0] if leagues else None)

        cards = getattr(self, '_vegas_cards', None)
        if cards is None:
            cards = self._vegas_cards = sports_vegas.VegasCardCache()
        odds = getattr(self, '_vegas_odds', None)
        if odds is None:
            odds = self._vegas_odds = sports_vegas.StickyOdds()
        fingerprint = fingerprint or sports_vegas.game_fingerprint

        elements: List[Any] = []
        keys: List[str] = []
        current_league = None
        separators = 0
        for game in games:
            league = game.get("league")
            settings = self._vegas_settings(league) if league else first
            card_width = int(settings.get("game_card_width", self.display_width))
            if settings.get("show_league_separators", True) and league != current_league:
                separator = self.vegas_separator(league) if league else None
                if separator is not None:
                    elements.append(VegasElement(
                        key=f"sep:{separators}:{league}", image=separator, live=False))
                    separators += 1
            current_league = league
            key = sports_vegas.game_key(game)
            drawn = odds.apply(key, game, now)
            ranks = (rankings_cache.get(str(drawn.get("home_abbr"))),
                     rankings_cache.get(str(drawn.get("away_abbr")))) if rankings_cache else None
            renderer = self._vegas_renderer(card_width, rankings_cache)
            elements.append(cards.element(
                key, (fingerprint(drawn), ranks, card_width, self.display_height),
                functools.partial(self.render_vegas_card, renderer, drawn)))
            keys.append(key)
        cards.retain(keys)
        odds.retain(keys)
        return elements

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


class _StripSlot:
    """One slate's display in a game type's pool, and what its strip shows.

    ``key`` is None when the strip must not be reused: never built, built
    from something that could not be fingerprinted, a build that failed, or
    released to stay inside the memory budget.
    """

    __slots__ = ("display", "key", "built_at", "strip", "last_used", "epoch")

    def __init__(self, display: SportsScrollDisplay) -> None:
        self.display = display
        self.key: Optional[Tuple[Any, ...]] = None
        self.built_at = 0.0
        #: The helper's strip array when it was built, held weakly: a strip
        #: replaced or cleared by anything since (a Vegas build on the same
        #: display, a plugin calling clear()) no longer matches it.
        self.strip: Optional[Callable[[], Any]] = None
        self.last_used = 0.0
        #: Bumped by every forget(). A build records its key only if this is
        #: what it was when the build started: anything that forgot the slot
        #: meanwhile (another build on this display, which may finish first
        #: and leave its strip in the helper) means the strip in the helper
        #: is not known to be this build's.
        self.epoch = 0

    def forget(self) -> None:
        self.key = None
        self.strip = None
        self.epoch += 1


class SportsScrollDisplayManager:
    """One :class:`SportsScrollDisplay` per game type ('live'/'recent'/'upcoming').

    Subclasses set :attr:`display_class`; everything else was near-identical
    across the eight plugin copies.

    A recent or upcoming strip that has not changed is reused rather than
    redrawn when its turn comes round again -- see :meth:`prepare_and_display`.
    """

    #: The SportsScrollDisplay subclass to instantiate per game type.
    display_class = SportsScrollDisplay

    #: Game types whose strip is reused while nothing it is drawn from has
    #: changed. Live is left out: its games change every poll, so the check
    #: would never pay, and sports_live_scroll rebuilds a live strip in place
    #: mid-cycle around get_scroll_display('live'), which has to stay the one
    #: display it always was. Vegas's 'mixed' never comes through
    #: prepare_and_display.
    STRIP_MEMO_GAME_TYPES = frozenset({"recent", "upcoming"})

    #: Oldest a reused strip may be. Not everything a card draws is in the
    #: game dicts -- a team logo that was missing at the first build appears
    #: only when the card is drawn again -- so an unchanged slate is still
    #: redrawn this often.
    STRIP_MEMO_MAX_AGE_S = 600.0

    #: Displays kept per game type, one per slate (its leagues): the one on
    #: screen plus the most recently shown others. When all are taken, the
    #: least recently shown one draws the new slate, as the one shared display
    #: always did, so a rotation with more slates than this costs no more
    #: than before.
    STRIP_MEMO_SLATES_PER_TYPE = 4

    #: Ceiling, per plugin, on the strips kept for displays not on screen, in
    #: bytes (the strip's array and image, and its Vegas items). Seven
    #: football games at 192x48 come to ~0.65MB, thirty at 512x64 to ~4MB,
    #: so a low-memory board holds no more than this beyond what one display
    #: per game type already held.
    STRIP_MEMO_MAX_PARKED_BYTES = 6 * 1024 * 1024

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
        # Per game type, its displays by slate, least recently shown first.
        # _scroll_displays[game_type] is always the one on screen, so every
        # reader of it (display_frame, is_complete, the plugins' own
        # get_dynamic_duration and has_cached_content) sees what it did when
        # there was only one display per game type.
        self._strip_pools: Dict[str, "OrderedDict[Tuple[str, Tuple[Any, ...]], _StripSlot]"] = {}
        # Only the bookkeeping is under it, never a build: a display() call
        # that outlived its timeout can still be building when the next one
        # starts.
        self._strip_lock = threading.RLock()

    def _new_scroll_display(self) -> SportsScrollDisplay:
        return self.display_class(
            self.display_manager,
            self.config,
            self.logger,
            global_config=self.global_config,
        )

    def get_scroll_display(self, game_type: str) -> SportsScrollDisplay:
        """The display for ``game_type``, created on first use.

        For a recent or upcoming game type, the display of the slate prepared
        last -- the strip display_frame() draws.
        """
        if game_type not in self._scroll_displays:
            self._scroll_displays[game_type] = self._new_scroll_display()
        return self._scroll_displays[game_type]

    def prepare_and_display(
        self,
        games: List[Dict],
        game_type: str,
        leagues: List[str],
        rankings_cache: Optional[Dict[str, int]] = None,
    ) -> bool:
        """Build content for ``game_type`` and make it the active strip.

        Building a strip draws every card while the render thread waits for
        it, with the panel frozen on its last frame: ~1.4s for seven football
        cards at 192x48 on a Pi 4, at the start of every turn and again each
        time the cycle completes. A recent or upcoming turn usually draws
        exactly the strip its slate drew last time. So when nothing the strip
        is drawn from has changed -- the games, the rankings, the config, the
        panel size and the date -- that strip is rewound and shown again
        instead, which leaves the plugin's prepare_scroll_content() uncalled.

        Two leagues usually take turns on one game type (nfl_recent, then
        ncaa_fb_recent), so each slate keeps its own display rather than
        sharing one; see STRIP_MEMO_SLATES_PER_TYPE. Anything in doubt is
        drawn again: a live strip, a turn with no games, inputs that cannot
        be fingerprinted, a strip older than STRIP_MEMO_MAX_AGE_S, or one
        changed since it was built.
        """
        keyed = self._strip_memo_key(games, game_type, leagues, rankings_cache)
        restore: Optional[SportsScrollDisplay] = None
        epoch = 0
        with self._strip_lock:
            if keyed is None:
                self._forget_shown_strip(game_type)
                scroll_display = self.get_scroll_display(game_type)
            else:
                reused = self._reuse_strip(game_type, *keyed)
                if reused is not None:
                    # What a fresh build leaves: the strip at its start, a new
                    # cycle not yet complete, this game type active.
                    reused.reset_scroll()
                    self._current_game_type = game_type
                    self.logger.debug(
                        "Reusing the unchanged %s strip for %s",
                        game_type, ", ".join(map(str, keyed[0][1])))
                    return True
                scroll_display, restore, epoch = self._display_to_build(
                    game_type, keyed[0])
            # Before the build, so data that changes during it reads as changed.
            started = time.monotonic()
        success = self._prepare_on(
            scroll_display, games, game_type, leagues, rankings_cache)
        if keyed is not None:
            with self._strip_lock:
                self._note_strip_built(
                    game_type, keyed, scroll_display, restore, success, started,
                    epoch)
        return success

    def _prepare_on(
        self,
        scroll_display: SportsScrollDisplay,
        games: List[Dict],
        game_type: str,
        leagues: List[str],
        rankings_cache: Optional[Dict[str, int]],
    ) -> bool:
        try:
            success = scroll_display.prepare_scroll_content(
                games, game_type, leagues, rankings_cache
            )
        except Exception:
            # prepare_scroll_content is subclass-implemented and builds cards
            # straight from feed data, so it can raise on a malformed payload.
            # One sport's bad payload must not take down the shared
            # orchestration for the others.
            self.logger.exception(
                "Error preparing scroll content for game_type=%s", game_type
            )
            return False
        if success:
            self._current_game_type = game_type
        return success

    # ------------------------------------------------------------------
    # Reusing an unchanged strip
    # ------------------------------------------------------------------

    def _strip_memo_key(
        self,
        games: Any,
        game_type: str,
        leagues: Any,
        rankings_cache: Any,
    ) -> Optional[Tuple[Tuple[str, Tuple[Any, ...]], Tuple[Any, ...]]]:
        """``(slate, key)``: which display, and everything its strip is drawn
        from. None when this strip must be drawn regardless.

        The games go through sports_vegas.game_fingerprint, the same "has
        this card changed" the live Vegas cards are redrawn on: the whole
        game dict, so no field a card draws can be missed. The config is
        fingerprinted by value, not by identity, so a config edited in place
        counts as changed.
        """
        if game_type not in self.STRIP_MEMO_GAME_TYPES or not games:
            return None
        # Iterated twice (here and by the build), so a one-shot iterable
        # would reach the build empty.
        if not isinstance(games, (list, tuple)) or not isinstance(leagues, (list, tuple)):
            return None
        try:
            slate = (game_type, tuple(leagues))
            hash(slate)
            key = (
                tuple(sports_vegas.game_fingerprint(game) for game in games),
                sports_vegas._freeze(rankings_cache),
                sports_vegas._freeze(self.config),
                (getattr(self.display_manager, "width", None),
                 getattr(self.display_manager, "height", None)),
                # A backstop: no card reads the clock today (game dates come
                # in the game dicts), but a strip must not outlive its day.
                time.localtime()[:3],
            )
        except Exception:
            # A game dict changing size under a background update, say. The
            # build reads it anyway; only the reuse is given up.
            self.logger.debug("Strip for %s not reusable this turn", game_type,
                              exc_info=True)
            return None
        return slate, key

    def _strip_reusable(self, slot: _StripSlot, key: Tuple[Any, ...]) -> bool:
        if slot.key is None or slot.strip is None:
            return False
        if time.monotonic() - slot.built_at >= self.STRIP_MEMO_MAX_AGE_S:
            return False
        helper = getattr(slot.display, "scroll_helper", None)
        array = getattr(helper, "cached_array", None)
        if array is None or slot.strip() is not array:
            return False
        has_strip = getattr(helper, "has_strip", None)
        if callable(has_strip) and not has_strip():
            return False
        return bool(slot.key == key)

    def _reuse_strip(
        self, game_type: str, slate: Tuple[str, Tuple[Any, ...]], key: Tuple[Any, ...],
    ) -> Optional[SportsScrollDisplay]:
        """The display already showing this exact strip, made the active one."""
        pool = self._strip_pools.get(game_type)
        slot = pool.get(slate) if pool else None
        if pool is None or slot is None or not self._strip_reusable(slot, key):
            return None
        pool.move_to_end(slate)
        slot.last_used = time.monotonic()
        # The display this replaces keeps its slot and strip for its own next
        # turn, within the parked-strip budget.
        self._scroll_displays[game_type] = slot.display
        self._trim_parked_strips()
        return slot.display

    def _display_to_build(
        self, game_type: str, slate: Tuple[str, Tuple[Any, ...]],
    ) -> Tuple[SportsScrollDisplay, Optional[SportsScrollDisplay], int]:
        """The display to draw ``slate`` on, made the active one.

        Returns it; when it replaced another as the active display, that
        one, to put back if the build fails -- a failed build left the
        previous strip showing when the game type had one display; and the
        slot's epoch the build must still find to record its key.
        """
        pool = self._strip_pools.setdefault(game_type, OrderedDict())
        active = self._scroll_displays.get(game_type)
        slot = pool.get(slate)
        if slot is None:
            if active is not None and not any(s.display is active for s in pool.values()):
                # The game type's display, holding nothing reusable: this
                # slate is drawn on it, exactly as before slates had their own.
                slot = _StripSlot(active)
            elif len(pool) < max(1, self.STRIP_MEMO_SLATES_PER_TYPE):
                slot = _StripSlot(self._new_scroll_display())
            else:
                # The least recently shown slate's display draws this one.
                _, slot = pool.popitem(last=False)
            pool[slate] = slot
        # Whatever was reusable on it is about to be drawn over.
        slot.forget()
        pool.move_to_end(slate)
        slot.last_used = time.monotonic()
        self._scroll_displays[game_type] = slot.display
        replaced = active if active is not None and active is not slot.display else None
        return slot.display, replaced, slot.epoch

    def _note_strip_built(
        self,
        game_type: str,
        keyed: Tuple[Tuple[str, Tuple[Any, ...]], Tuple[Any, ...]],
        scroll_display: SportsScrollDisplay,
        restore: Optional[SportsScrollDisplay],
        success: bool,
        started: float,
        epoch: int,
    ) -> None:
        slate, key = keyed
        pool = self._strip_pools.get(game_type)
        slot = pool.get(slate) if pool else None
        if (slot is None or slot.display is not scroll_display or slot.epoch != epoch
                or self._scroll_displays.get(game_type) is not scroll_display):
            # Another prepare moved on while this one built, or drew on this
            # display too. Record nothing; the strip is drawn again next time.
            return
        array = getattr(scroll_display.scroll_helper, "cached_array", None)
        if success and array is not None:
            slot.key = key
            slot.built_at = started
            slot.strip = weakref.ref(array)
        elif not success and restore is not None:
            self._scroll_displays[game_type] = restore
        self._trim_parked_strips()

    def _forget_shown_strip(self, game_type: str) -> None:
        """A build this memo cannot key is about to draw on the active display."""
        active = self._scroll_displays.get(game_type)
        for slot in (self._strip_pools.get(game_type) or {}).values():
            if slot.display is active:
                slot.forget()

    @staticmethod
    def _strip_bytes(scroll_display: SportsScrollDisplay) -> int:
        """What keeping this display's strip costs: the strip's array, the
        image beside it, and its Vegas items."""
        array = getattr(scroll_display.scroll_helper, "cached_array", None)
        total = int(array.nbytes) * 2 if array is not None else 0
        for item in getattr(scroll_display, "_vegas_content_items", None) or ():
            total += item.width * item.height * len(item.getbands())
        return total

    def _trim_parked_strips(self) -> None:
        """Release the strips of displays not on screen until they fit
        STRIP_MEMO_MAX_PARKED_BYTES: those that can never be reused first (a
        failed build left an older slate's strip behind), then the least
        recently shown. The display itself is kept, so its slate's next turn
        draws on it again."""
        # list() first: get_scroll_display() can add a game type from another
        # thread (Vegas's 'mixed'), and a dict must not grow mid-iteration.
        on_screen = {id(display) for display in list(self._scroll_displays.values())}
        parked = []
        total = 0
        for pool in self._strip_pools.values():
            for slot in pool.values():
                if id(slot.display) in on_screen:
                    continue
                try:
                    size = self._strip_bytes(slot.display)
                except Exception:
                    # Unmeasurable: assume it does not fit.
                    size = self.STRIP_MEMO_MAX_PARKED_BYTES + 1
                if size:
                    parked.append((slot.last_used, size, slot))
                    total += size
        parked.sort(key=lambda entry: (entry[2].key is not None, entry[0]))
        for _, size, slot in parked:
            if total <= self.STRIP_MEMO_MAX_PARKED_BYTES:
                break
            slot.forget()
            self._release_strip(slot.display)
            total -= size

    def _release_strip(self, scroll_display: SportsScrollDisplay) -> None:
        """Drop a display's strip without SportsScrollDisplay.clear(), which
        also tells the display manager nothing is scrolling -- not this
        display's to say while another one is on screen."""
        try:
            scroll_display.scroll_helper.clear_cache()
            scroll_display._vegas_content_items = []
        except Exception:
            self.logger.debug("Could not release a parked strip", exc_info=True)

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
        """Clear every display and forget which one was active.

        The displays of slates not on screen too, and nothing cleared is
        reused: the next prepare draws its strip again.
        """
        displays = list(self._scroll_displays.values())
        with self._strip_lock:
            for pool in self._strip_pools.values():
                for slot in pool.values():
                    slot.forget()
                    if not any(slot.display is shown for shown in displays):
                        displays.append(slot.display)
        for scroll_display in displays:
            scroll_display.clear()
        self._current_game_type = ""

    def get_vegas_elements_for(
        self,
        game_type: str,
        games: List[Dict[str, Any]],
        leagues: List[str],
        rankings_cache: Optional[Dict[str, int]] = None,
        fingerprint: Optional[Callable[[Dict[str, Any]], Any]] = None,
    ) -> Optional[List[Any]]:
        """Live Vegas cards for a slate, built on the ``game_type`` display.

        None when the sport has no live cards (it does not implement
        make_vegas_renderer) or building them failed, so the plugin's
        get_vegas_elements() can return it and the ticker falls back to the
        plugin's ordinary Vegas content.
        """
        scroll_display = self.get_scroll_display(game_type)
        try:
            return scroll_display.build_vegas_elements(
                games, leagues, rankings_cache, fingerprint)
        except NotImplementedError:
            return None
        except Exception:
            # Built straight from feed data, like prepare_scroll_content.
            self.logger.exception("Error building live Vegas cards")
            return None

    def get_all_vegas_content_items(self) -> List[Image.Image]:
        """Every display's Vegas items, for splicing into the marquee."""
        items: List[Image.Image] = []
        for scroll_display in self._scroll_displays.values():
            vegas_items = getattr(scroll_display, "_vegas_content_items", None)
            if vegas_items:
                items.extend(vegas_items)
        return items
