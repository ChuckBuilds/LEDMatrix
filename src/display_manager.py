"""
Display Manager — hardware abstraction layer for the RGB LED matrix.

This module provides :class:`DisplayManager`, the single interface between
application code and the physical (or emulated) LED panel.

Key responsibilities
--------------------
* Initialise the ``RGBMatrix`` (hardware) or ``RGBMatrixEmulator`` depending
  on the ``EMULATOR`` environment variable.
* Expose a PIL ``Image``/``ImageDraw`` canvas that plugins draw into, then
  flush it to the matrix via double-buffering (:meth:`DisplayManager.update_display`).
* Load and cache TTF/BDF fonts; expose ``draw_text`` for consistent text rendering.
* Provide ``width`` / ``height`` properties — always use these instead of
  hard-coding display dimensions.
* Write periodic PNG snapshots to ``/tmp/led_matrix_preview.png`` for the
  web-interface live preview.
* Track scrolling state and gate deferred updates so plugins don't race with
  an in-progress scroll.

Singleton: only one ``DisplayManager`` instance exists per process.  The
first call to ``DisplayManager(config)`` creates it; subsequent calls return
the same object.
"""

import json
import os
import socket
import tempfile
if os.getenv("EMULATOR", "false") == "true":
    from RGBMatrixEmulator import RGBMatrix, RGBMatrixOptions
else:
    from rgbmatrix import RGBMatrix, RGBMatrixOptions
from contextlib import contextmanager
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from src.common.bdf_font import draw_bdf_text, load_bdf_face
from src.common.font_layout import crisp_size, load_truetype, resolve_asset_path
from src.display_geometry import (
    DEFAULT_CHAIN_LENGTH, DEFAULT_COLS, DEFAULT_PARALLEL, DEFAULT_ROWS,
    compose_pixel_mapper_config, physical_size, resolve_double_sided,
)
from src.matrix_support import MatrixSettingsRefused, library_refusals, refusal_message
from src.pi5_matrix_support import is_raspberry_pi_5
import threading
import time
from collections import OrderedDict
from typing import Dict, Any, List, Optional, Tuple
import math
import zlib
import freetype

from src.common import snapshot_policy
from src.common.frame_timing import FrameTimingRecorder
from src.deprecation import deprecated
from src.logging_config import get_logger
from src.common.permission_utils import (
    ensure_directory_permissions,
    ensure_file_permissions,
    get_assets_dir_mode,
    get_assets_file_mode,
)

logger = get_logger(__name__)

#: The strike 5x7.bdf is drawn at. FreeType renders a BDF at its own fixed
#: size regardless, but a Face needs an active size before its metrics --
#: and therefore get_font_height() -- report anything but 0.
_CALENDAR_FONT_PX = 7


def _bdf_native_size(face) -> int:
    """The pixel height a BDF Face declares, or 0 if it does not say.

    Used only to rescue a Face that was built without ``set_char_size``, so a
    zero line height never reaches layout code.
    """
    try:
        sizes = getattr(face, "available_sizes", None) or []
        if sizes:
            return int(getattr(sizes[0], "height", 0) or 0)
    except (AttributeError, IndexError, TypeError, ValueError) as exc:
        # This runs on the measurement path for a face the caller already
        # holds, so a malformed strike table must degrade to "unknown" rather
        # than take the display down. Say which face, so a font that is
        # actually broken is diagnosable rather than silently 8px.
        logger.debug("Could not read BDF strike size from %r: %s", face, exc)
    return 0



class _LogicalMatrix:
    """Proxy that reports a logical (per-screen) size for a physical matrix.

    In double-sided mode the physical panel chain shows N identical copies of a
    smaller logical screen. Plugins size themselves from
    ``display_manager.width`` / ``height`` (the documented convention), which
    defer to ``matrix.width`` / ``matrix.height`` -- and many older plugins read
    ``matrix.width`` directly -- so this proxy reports the logical dimensions
    while delegating every real
    operation — ``CreateFrameCanvas``, ``SwapOnVSync``, ``brightness``,
    ``Clear`` and so on — to the underlying physical matrix. The duplication
    itself happens once per frame in :meth:`DisplayManager.update_display`.
    """

    __slots__ = ("_logical_height", "_logical_width", "_matrix")

    def __init__(self, matrix: RGBMatrix, logical_width: int, logical_height: int) -> None:
        object.__setattr__(self, "_matrix", matrix)
        object.__setattr__(self, "_logical_width", logical_width)
        object.__setattr__(self, "_logical_height", logical_height)

    @property
    def width(self) -> int:
        """Logical (per-screen) width reported to plugins."""
        return self._logical_width

    @property
    def height(self) -> int:
        """Logical (per-screen) height reported to plugins."""
        return self._logical_height

    def __getattr__(self, name: str) -> Any:
        """Forward any non-overridden attribute access to the physical matrix.

        Reached only when normal lookup fails (i.e. not width/height/_*).
        """
        return getattr(object.__getattribute__(self, "_matrix"), name)

    def __setattr__(self, name: str, value: Any) -> None:
        """Forward attribute writes (e.g. ``matrix.brightness = 80``) to it."""
        setattr(object.__getattribute__(self, "_matrix"), name, value)


class _OffscreenMatrix(_LogicalMatrix):
    """``display_manager.matrix`` as a thread drawing off-screen sees it.

    Reports the surface's size, so plugins that lay out from ``matrix.width``
    follow it, and swallows every write that would reach the hardware. Nothing
    drawn off-screen may touch the panel the render loop is driving. Method
    names mirror the rgbmatrix API they stand in for.
    """

    # pylint: disable=invalid-name
    __slots__ = ()

    def SetImage(self, *_args: Any, **_kwargs: Any) -> None:
        """Inert: off-screen drawing never reaches the panel."""

    def SetPixel(self, *_args: Any, **_kwargs: Any) -> None:
        """Inert: off-screen drawing never reaches the panel."""

    def Clear(self) -> None:
        """Inert: off-screen drawing never reaches the panel."""

    def Fill(self, *_args: Any, **_kwargs: Any) -> None:
        """Inert: off-screen drawing never reaches the panel."""

    def SwapOnVSync(self, canvas: Any, *_args: Any, **_kwargs: Any) -> Any:
        """Inert: hands the canvas straight back without waiting on the panel."""
        return canvas

    def __setattr__(self, name: str, value: Any) -> None:
        """Inert: brightness and other writes stay off the real matrix."""


class _OffscreenSurface:
    """One thread's private canvas while it renders off-screen.

    See :meth:`DisplayManager.offscreen`.
    """

    __slots__ = ("draw", "image", "matrix")

    def __init__(self, width: int, height: int, real_matrix: Any) -> None:
        self.image = Image.new('RGB', (width, height))
        self.draw = ImageDraw.Draw(self.image)
        # 1-bit text: the panel has no partial brightness, so AA only smears glyphs.
        self.draw.fontmode = "1"
        self.matrix = (_OffscreenMatrix(real_matrix, width, height)
                       if real_matrix is not None else None)


def _per_thread_canvas_attr(name: str) -> property:
    """A DisplayManager attribute that resolves per thread.

    A thread inside :meth:`DisplayManager.offscreen` reads and writes its own
    surface's ``name``; every other thread reads and writes the shared value,
    exactly as when this was a plain attribute. Existing ``self.image = ...``
    assignments therefore keep working and become thread-correct as they are.
    """
    shared = "_shared_" + name

    def fget(self: "DisplayManager") -> Any:
        surface = self._current_surface()  # pylint: disable=protected-access
        if surface is not None:
            return getattr(surface, name)
        try:
            return self.__dict__[shared]
        except KeyError:
            raise AttributeError(name) from None

    def fset(self: "DisplayManager", value: Any) -> None:
        surface = self._current_surface()  # pylint: disable=protected-access
        if surface is not None:
            setattr(surface, name, value)
        else:
            self.__dict__[shared] = value

    return property(fget, fset, doc=f"The plugin-facing ``{name}``, per thread.")



class DisplayManager:
    """
    Singleton hardware abstraction layer for the RGB LED matrix.

    Plugins should never interact with ``RGBMatrix`` directly; they use this
    class to draw content and call :meth:`update_display` to push frames to
    the panel.

    Typical plugin usage::

        canvas = Image.new('RGB', (self.display_manager.width,
                                   self.display_manager.height), (0, 0, 0))
        draw = ImageDraw.Draw(canvas)
        # ... draw content ...
        self.display_manager.image = canvas
        self.display_manager.draw = ImageDraw.Draw(self.display_manager.image)
        self.display_manager.update_display()
    """

    _instance = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super(DisplayManager, cls).__new__(cls)
        return cls._instance

    # The plugin-facing canvas. Per thread: see offscreen().
    image = _per_thread_canvas_attr("image")
    draw = _per_thread_canvas_attr("draw")
    matrix = _per_thread_canvas_attr("matrix")

    def __init__(self, config: Dict[str, Any] = None, force_fallback: bool = False, suppress_test_pattern: bool = False):
        start_time = time.time()
        self.config = config or {}
        self._force_fallback = force_fallback
        self._suppress_test_pattern = suppress_test_pattern
        # Per-thread capture state. update_display() and clear() skip hardware
        # writes while the *calling* thread is capturing content off-screen.
        #
        # Thread-local rather than a plain flag because Vegas mode prepares
        # upcoming content on a background thread: a shared flag set there would
        # suppress the render loop's own frame pushes for the duration, freezing
        # the panel exactly when the point was to avoid a freeze.
        self._capture_state = threading.local()
        # Per-thread off-screen surface. While a thread is inside offscreen(),
        # image, draw and matrix resolve to its own canvas; see offscreen().
        self._surface_state = threading.local()
        # Double-sided mode state (resolved in _setup_matrix). When disabled,
        # the logical image is blitted to the matrix unchanged.
        self._double_sided = None  # dict {copies, axis, logical_width, logical_height} or None
        self._physical_image = None  # full-chain buffer reused each frame when tiling
        # Text-width measurement cache: (text, id(font)) -> (width, font_ref)
        # Avoids re-measuring the same string+font on every display() call.
        # LRU-bounded: keys embed the TEXT, so changing strings (a clock, a
        # live score) would otherwise grow it forever on a 24/7 service.
        # Entries hold a strong reference to the font so its id() can't be
        # recycled by a different font object — an id-keyed cache without
        # the reference can return the WRONG width after garbage collection.
        # Cleared on _load_fonts() so stale entries don't survive a font reload.
        self._text_width_cache: "OrderedDict[tuple, Tuple[int, Any]]" = OrderedDict()
        self._TEXT_WIDTH_CACHE_MAX = 1024
        # Snapshot mirror for web preview + health check (service writes, web
        # reads). Cadence/skip decisions live in src/common/snapshot_policy.py:
        # full rate only while the web SSE broadcaster keeps the viewer marker
        # fresh; unchanged frames are never re-encoded, only mtime-touched.
        self._snapshot_path = "/tmp/led_matrix_preview.png"  # nosec B108 - fixed path intentional; web UI reads same path
        self._viewer_marker_path = "/tmp/led_matrix_preview_viewer"  # nosec B108 - touched by web SSE broadcaster
        self._last_snapshot_ts = 0.0
        self._last_snapshot_touch_ts = 0.0
        self._last_snapshot_digest: Optional[int] = None
        # The frame actually on disk. _last_snapshot_digest moves when a frame
        # is handed to the writer; this only once it has been saved, so an
        # mtime touch never vouches for a frame still waiting to be written.
        self._saved_snapshot_digest: Optional[int] = None
        self._snapshot_dir_prepared = False
        # Background writer used mid-scroll; see _write_snapshot_if_due.
        self._snapshot_cond = threading.Condition()
        self._snapshot_pending: Optional[Tuple[Image.Image, Optional[int]]] = None
        self._snapshot_thread: Optional[threading.Thread] = None
        self._snapshot_stop = False
        # Held for the whole of each PNG write, by the writer thread and by
        # the inline static path, so the two land on disk in order.
        self._snapshot_write_lock = threading.Lock()
        self._viewer_check_ts = 0.0
        self._viewer_fresh = False
        self._viewer_was_fresh = False
        # Snapshot failures are logged as warnings, rate-limited so a
        # persistent failure (e.g. an unwritable file) can't spam the log —
        # but is never silent: the snapshot's mtime doubles as the web UI's
        # hardware-liveness signal, so a quiet failure makes health checks lie.
        self._snapshot_fail_log_ts = 0.0
        # Dirty tracking: (image digest, brightness) of the last frame pushed
        # to the panel; update_display() skips identical pushes. Kill switch:
        # display.dirty_tracking: false.
        self._dirty_tracking_enabled = bool(
            self.config.get('display', {}).get('dirty_tracking', True))
        self._last_pushed_digest = None
        # Serializes update_display(): plugins can call it directly from
        # background threads (see docstring on update_display), not just the
        # render loop. RLock in case a caller within the critical section
        # ever re-enters (e.g. via a nested draw callback).
        self._update_lock = threading.RLock()
        
        # Scrolling state tracking for graceful updates
        # How many panel refreshes each pushed frame is held for. 1 means a new
        # frame every refresh. Higher values are how a scroll runs slower than
        # one pixel per refresh WITHOUT fractional pixel positions: the panel
        # keeps refreshing at full rate (so flicker is unchanged) but motion
        # advances a whole pixel every Nth refresh instead of every one.
        # See src/common/scroll_config.py and scripts/scroll_speeds.py.
        self._frame_hold = 1

        # A src.common.render_gate.RenderGate while Vegas runs with
        # vegas_scroll.prefetch_gate on: opened around each swap so the
        # prefetch thread only runs Python while this thread waits on vsync.
        self.render_gate = None

        # Timing of every presented frame, whoever drew it, for
        # scripts/frame_soak.py. See src/common/frame_timing.py.
        self.frame_timing = FrameTimingRecorder(info=self._frame_timing_info())
        self.frame_timing.scrolling_now = self._scrolling_now

        self._scrolling_state = {
            'is_scrolling': False,
            'last_scroll_activity': 0,
            'scroll_inactivity_threshold': 2.0,  # seconds of inactivity before considering "not scrolling"
            'deferred_updates': [],
            'max_deferred_updates': 50,  # Limit queue size to prevent memory issues
            'deferred_update_ttl': 300.0  # 5 minutes TTL for deferred updates
        }
        
        self._setup_matrix()
        logger.info("Matrix setup completed in %.3f seconds", time.time() - start_time)
        
        font_time = time.time()
        self._load_fonts()
        logger.info("Font loading completed in %.3f seconds", time.time() - font_time)

    def _new_canvas(self, width: int, height: int) -> None:
        """Replace ``image``/``draw`` with a black canvas of the given size.

        Text is drawn 1-bit (``fontmode = "1"``): the panel has no partial
        brightness, so anti-aliasing only smears glyphs.
        """
        self.image = Image.new('RGB', (width, height))
        self.draw = ImageDraw.Draw(self.image)
        self.draw.fontmode = "1"

    @staticmethod
    def _fallback_advice(cause: str, error: Exception) -> str:
        """What to do about a failed matrix init, for the log.

        Only a library failure gets the rebuild hint: advice about the build
        or GPIO timing sends someone whose settings were refused the wrong way.
        """
        if cause == "settings":
            return (f"{error} Change these in the web interface's Display tab "
                    "(or display.hardware / display.runtime in config.json) "
                    "and restart the display service.")
        if cause == "forced":
            return f"Error: {error}."
        advice = (f"Error: {error}. If the rgbmatrix library printed a message "
                  "just before this, it names the problem.")
        if is_raspberry_pi_5():
            advice += (" On a Raspberry Pi 5, an mmap error means the library was "
                       "built without Pi 5 support: sudo RPI_RGB_FORCE_REBUILD=1 "
                       "./first_time_install.sh")
        return advice

    def _setup_matrix(self):
        """Initialize the RGB matrix with configuration settings."""
        _init_error_str = None
        _init_cause = None
        try:
            # Allow callers (e.g., web UI) to force non-hardware fallback mode
            if getattr(self, '_force_fallback', False):
                raise RuntimeError('Forced fallback mode requested')
            options = RGBMatrixOptions()
            
            # Hardware configuration
            hardware_config = self.config.get('display', {}).get('hardware', {})
            runtime_config = self.config.get('display', {}).get('runtime', {})

            # The library has no error path for many settings it can't use:
            # it returns no matrix (which the binding doesn't check, so the
            # process crashes on its next call) or calls abort(), and systemd
            # restarts the service into the same crash. Refuse those first so
            # they become a logged, reported fallback (src/matrix_support.py).
            refused = refusal_message(library_refusals(
                hardware_config, runtime_config, pi5=is_raspberry_pi_5()))
            if refused:
                if os.getenv("EMULATOR", "false") != "true":
                    raise MatrixSettingsRefused(refused)
                logger.warning("Emulator mode: continuing, but on a real panel the display would not start. %s", refused)
            
            # Every option comes from display.hardware / display.runtime, in
            # one place that scripts/scroll_speeds.py shares.
            self.apply_matrix_options(options, self.config)
            
            logger.info(f"Initializing RGB Matrix with settings: rows={options.rows}, cols={options.cols}, chain_length={options.chain_length}, parallel={options.parallel}, hardware_mapping={options.hardware_mapping}")
            
            # Initialize the matrix
            self.matrix = RGBMatrix(options=options)
            logger.info("RGB Matrix initialized successfully")

            # Create double buffer for smooth updates. The canvases are always
            # full physical size — they back the real chain regardless of mode.
            self.offscreen_canvas = self.matrix.CreateFrameCanvas()
            self.current_canvas = self.matrix.CreateFrameCanvas()
            logger.info("Frame canvases created successfully")

            # Double-sided mode: wrap the physical matrix so plugins see the
            # logical (per-screen) size, and keep a full-chain buffer to tile
            # the rendered screen into once per frame.
            ds_config = self.config.get('display', {}).get('double_sided', {})
            ds = resolve_double_sided(self.matrix.width, self.matrix.height, ds_config)
            self._double_sided = ds
            if ds is not None:
                self._physical_image = Image.new(
                    'RGB', (self.matrix.width, self.matrix.height))
                self.matrix = _LogicalMatrix(
                    self.matrix, ds['logical_width'], ds['logical_height'])

            # Create image with the (logical) display dimensions
            self._new_canvas(self.matrix.width, self.matrix.height)
            logger.info(f"Image canvas created with dimensions: {self.matrix.width}x{self.matrix.height}")
            
            # Initialize font with Press Start 2P
            try:
                self.font = load_truetype(
                    self._font_asset(self._PRESS_START),
                    crisp_size(self._PRESS_START, 8))
                logger.info("Initial Press Start 2P font loaded successfully")
            except Exception as e:
                logger.error(f"Failed to load initial font: {e}")
                self.font = ImageFont.load_default()
            
            # Draw a test pattern unless caller suppressed it (e.g., web on-demand)
            if not getattr(self, '_suppress_test_pattern', False):
                self._draw_test_pattern()
            
        except Exception as e:
            _init_error_str = str(e)
            if isinstance(e, MatrixSettingsRefused):
                _init_cause = "settings"
                logger.error("Failed to initialize RGB Matrix: %s", e)
            else:
                _init_cause = "forced" if getattr(self, '_force_fallback', False) else "library"
                logger.error(f"Failed to initialize RGB Matrix: {e}", exc_info=True)
            # Create a fallback image for web preview using configured dimensions when available
            self.matrix = None
            try:
                fallback_width, fallback_height = physical_size(self.config)
                # Mirror double-sided in fallback so the preview shows one screen.
                ds_config = self.config.get('display', {}).get('double_sided', {}) if self.config else {}
                ds = resolve_double_sided(fallback_width, fallback_height, ds_config)
                self._double_sided = ds
                if ds is not None:
                    fallback_width = ds['logical_width']
                    fallback_height = ds['logical_height']
            except Exception:
                fallback_width, fallback_height = 128, 32

            self._new_canvas(fallback_width, fallback_height)
            # Simple fallback visualization so web UI shows a realistic canvas
            try:
                self.draw.rectangle([0, 0, fallback_width - 1, fallback_height - 1], outline=(255, 0, 0))
                self.draw.line([0, 0, fallback_width - 1, fallback_height - 1], fill=(0, 255, 0))
                self.draw.text((2, max(0, (fallback_height // 2) - 4)), "Simulation", fill=(0, 128, 255))
            except Exception:  # nosec B110 - best-effort fallback visualization; drawing errors must not crash startup
                # Best-effort; ignore drawing errors in fallback
                pass
            logger.error(
                "Matrix initialization failed — running in fallback/simulation mode "
                "(size %dx%d). %s",
                fallback_width, fallback_height, self._fallback_advice(_init_cause, e))
            # Do not raise here; allow fallback mode so web preview and non-hardware environments work

        # Write hardware status file so the web UI can surface init failures
        # cause: None when ok; "settings" when LEDMatrix refused the config
        # (fix the named settings), "library" when the library itself failed,
        # "forced" for a caller-requested fallback. The Display tab keys its
        # advice on it.
        _hw_status = {"ok": self.matrix is not None, "error": _init_error_str,
                      "cause": None if self.matrix is not None else _init_cause}
        _status_path = "/tmp/led_matrix_hw_status.json"  # nosec B108
        try:
            if os.path.islink(_status_path):
                logger.warning("Skipping hardware status write: %s is a symlink", _status_path)
            else:
                _fd, _tmp_path = tempfile.mkstemp(dir="/tmp", prefix=".led_hw_")  # nosec B108
                try:
                    with os.fdopen(_fd, "w") as _f:
                        json.dump(_hw_status, _f)
                        _f.flush()
                        os.fsync(_f.fileno())
                    os.chmod(_tmp_path, 0o644)
                    os.replace(_tmp_path, _status_path)
                except Exception:
                    try:
                        os.unlink(_tmp_path)
                    except OSError:
                        pass
                    raise
        except Exception:
            logger.error("Failed to write hardware status file", exc_info=True)

    @property
    def width(self):
        """Get the display width."""
        if hasattr(self, 'matrix') and self.matrix is not None:
            return self.matrix.width
        elif hasattr(self, 'image'):
            return self.image.width
        else:
            return 128  # Default fallback width

    @property
    def height(self):
        """Get the display height."""
        if hasattr(self, 'matrix') and self.matrix is not None:
            return self.matrix.height
        elif hasattr(self, 'image'):
            return self.image.height
        else:
            return 32  # Default fallback height

    def set_brightness(self, brightness: int) -> bool:
        """
        Set display brightness at runtime.

        Args:
            brightness: Brightness level (0-100)

        Returns:
            True if brightness was set successfully, False otherwise
        """
        # Fail fast: validate input type
        if not isinstance(brightness, (int, float)):
            logger.error(f"[BRIGHTNESS] Invalid brightness type: {type(brightness).__name__}, expected int")
            return False

        if self.matrix is None:
            logger.warning("[BRIGHTNESS] Cannot set brightness in fallback mode")
            return False

        # Clamp to valid range
        brightness = max(0, min(100, int(brightness)))

        try:
            # RGBMatrix accepts brightness as a property
            self.matrix.brightness = brightness
            # Brightness applies on the next swap — force a re-push even if
            # the image itself is unchanged (belt-and-braces: brightness is
            # also part of the dirty-tracking digest when readable).
            self._last_pushed_digest = None
            logger.info(f"[BRIGHTNESS] Display brightness set to {brightness}%")
            return True
        except AttributeError as e:
            logger.error(f"[BRIGHTNESS] Matrix does not support brightness property: {e}", exc_info=True)
            return False
        except (TypeError, ValueError) as e:
            logger.error(f"[BRIGHTNESS] Invalid brightness value rejected by hardware: {e}", exc_info=True)
            return False

    def get_brightness(self) -> int:
        """
        Get current display brightness.

        Returns:
            Current brightness level (0-100), or -1 if unavailable
        """
        if self.matrix is None:
            logger.debug("[BRIGHTNESS] Cannot get brightness in fallback mode")
            return -1

        try:
            return self.matrix.brightness
        except AttributeError as e:
            logger.warning(f"[BRIGHTNESS] Matrix does not support brightness property: {e}", exc_info=True)
            return -1

    @staticmethod
    def _local_ip() -> Optional[str]:
        """This device's address on the network it routes through, or None.

        Deliberately not `hostname -I` or a systemctl probe for AP mode, which
        is how the web launcher does it: both spawn processes with multi-second
        timeouts, and this runs on the startup path the rest of this change
        exists to shorten. Connecting a UDP socket sends no packets -- it only
        asks the kernel which source address it would use -- so it costs
        microseconds and works with the network down, as long as a route
        exists.
        """
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(0.2)
            sock.connect(("8.8.8.8", 80))  # nosec B104 - no traffic; selects a route
            ip = sock.getsockname()[0]
            return ip if ip and not ip.startswith("127.") else None
        except OSError:
            return None
        finally:
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass

    def _fitting_font(self, lines, width):
        """The largest font from the usual ladder that fits every line.

        The ladder ends at 4x6 at 5px because a full dotted-quad address --
        "255.255.255.255", the widest this screen ever shows -- is 66px at
        6px and a 64px panel has 62 to give it. That used to squeak in only
        because the measurement depended on which text layout engine the host
        Pillow had; with the engine pinned it does not, so the rung the
        worst case actually needs is here rather than implied.
        """
        # The middle rung is on the 7px grid; the bottom one is deliberately
        # not. 4x6 advances the same whether it is asked for 6 or 7 -- the
        # dotted quad is 66px at both -- so the middle rung costs no width and
        # gains the fourth column in every glyph, which is the difference
        # between reading an address off a wall and guessing at it. The 5 rung
        # is the exception this screen needs: it drops the advance to 4px and
        # the quad to 51px, the only rung that fits a 64px panel, and no
        # on-grid size does that. It is the one place in the core that draws
        # 4x6 off-grid on purpose.
        candidates = [self.font,
                      (self._font_asset(self._FOUR_BY_SIX),
                       crisp_size(self._FOUR_BY_SIX, 6)),
                      (self._font_asset(self._FOUR_BY_SIX), 5)]
        narrowest = None
        for candidate in candidates:
            try:
                font = candidate
                if isinstance(candidate, tuple):
                    font = load_truetype(candidate[0], candidate[1])
                narrowest = font
                if all(self.draw.textlength(t, font=font) <= width for t in lines):
                    return font
            except (OSError, ValueError, AttributeError):
                continue
        # Nothing fit. Return the smallest face that loaded, not self.font --
        # falling back to the widest option is how "Initializing" ran off the
        # side of a 64px panel in the first place.
        return narrowest or self.font

    def _draw_startup_banner(self, lines, width: int, height: int) -> None:
        """Centre `lines` over whatever the test pattern already drew.

        This screen stays on the panel for the whole initial plugin update, and
        on a headless Pi it is the only place the device's address appears
        without going looking for it -- so it has to be readable off a wall,
        not merely present.

        The font is chosen to fit rather than fixed at 8px: "Initializing" is
        96px in PressStart2P, which ran off the side of a 64px panel even
        before an address was added. And the pattern is punched out behind the
        text, because the diagonal runs through the middle of the panel, which
        is exactly where this sits.

        The text stays blue. It is not decoration: the pattern draws one pure
        channel per element -- red border, green diagonal, blue text -- so that
        a glance at the panel says whether led_rgb_sequence is right. Swap the
        wiring to BGR and the border comes up blue and this text red. Drawing
        it white would light all three channels and destroy the only blue
        reference on the screen, which is why it is worth a comment rather
        than a quiet preference.
        """
        if not lines:
            return
        font = self._fitting_font(lines, width - 2)
        line_height = self.draw.textbbox((0, 0), "Ag", font=font)[3] + 1
        block_height = line_height * len(lines)
        block_top = max(1, (height - block_height) // 2)
        block_width = max(self.draw.textlength(t, font=font) for t in lines)
        block_left = max(0, (width - block_width) // 2)

        self.draw.rectangle(
            [block_left - 2, block_top - 1,
             block_left + block_width + 1, block_top + block_height],
            fill=(0, 0, 0))

        for row, line in enumerate(lines):
            line_width = self.draw.textlength(line, font=font)
            self.draw.text(
                (max(0, (width - line_width) // 2), block_top + row * line_height),
                line, font=font, fill=(0, 0, 255))

    def _draw_test_pattern(self):
        """Draw a test pattern to verify the display is working.

        Only called from _setup_matrix once the matrix exists; fallback mode
        draws its own "Simulation" canvas there.
        """
        try:
            self.clear()

            # Draw a red rectangle border
            self.draw.rectangle([0, 0, self.matrix.width-1, self.matrix.height-1], outline=(255, 0, 0))
            
            # Draw a diagonal line
            self.draw.line([0, 0, self.matrix.width-1, self.matrix.height-1], fill=(0, 255, 0))
            
            lines = ["Initializing"]
            ip = self._local_ip()
            if ip:
                lines.append(ip)
            self._draw_startup_banner(lines, self.matrix.width, self.matrix.height)
            
            # Update the display once after everything is drawn
            self.update_display()
            time.sleep(0.5)
            
        except Exception as e:
            logger.error(f"Error drawing test pattern: {e}", exc_info=True)

    @property
    def _capture_mode_active(self) -> bool:
        """True while the calling thread is capturing content off-screen."""
        # Read like _current_surface(): a DisplayManager built without
        # __init__ (tests do) has no per-thread state, and captures nothing.
        state = self.__dict__.get('_capture_state')
        return getattr(state, 'active', False) if state is not None else False

    @_capture_mode_active.setter
    def _capture_mode_active(self, value: bool) -> None:
        self._capture_state.active = bool(value)

    @contextmanager
    def capture_mode(self):
        """Suppress hardware output during off-screen content capture.

        Plugins call update_display() as part of their normal display() flow.
        When fetching content for Vegas mode the render loop is still running,
        so any incidental hardware write causes a visible flash on the matrix.
        Entering this context prevents those writes without affecting the PIL
        image buffer, which the adapter reads to extract content.
        """
        # Restore rather than clear: capture_mode() inside offscreen() must not
        # switch suppression off for the rest of the off-screen block.
        was_active = self._capture_mode_active
        self._capture_mode_active = True
        try:
            yield
        finally:
            self._capture_mode_active = was_active

    def _current_surface(self) -> Optional[_OffscreenSurface]:
        """The calling thread's off-screen surface, or None."""
        state = self.__dict__.get('_surface_state')
        return getattr(state, 'surface', None) if state is not None else None

    def _writes_suppressed(self) -> bool:
        """True when the calling thread must not touch the panel or its pacing."""
        return self._capture_mode_active or self._current_surface() is not None

    @contextmanager
    def offscreen(self, width: Optional[int] = None, height: Optional[int] = None):
        """Give the calling thread its own canvas to draw on.

        Inside the block, for the calling thread only, ``image``, ``draw`` and
        ``matrix`` (and so ``width``/``height``) are a fresh black canvas of the
        requested size, and nothing reaches the hardware: ``update_display()``
        and the hardware half of ``clear()`` are skipped, and
        ``set_scrolling_state()``/``set_frame_hold()`` cannot re-pace the live
        scroll. Every other thread, the render loop above all, keeps seeing the
        real canvas.

        That is what lets Vegas mode render a plugin on its background prefetch
        thread. The shared canvas used to be the only one, so any plugin that
        drew on it (display capture, scroll-content generation, narrowed
        rendering) had to be fetched on the render thread, stalling the scroll
        for 40-600ms each. See docs/OFFSCREEN_RENDERING.md.

        Blocks nest; each restores the one outside it, also on an exception.

        Args:
            width: Width of the surface, clamped to the size this thread sees
                now. Defaults to that size.
            height: Height, likewise.

        Yields:
            The surface. ``surface.image`` is what the plugin drew.
        """
        state = self.__dict__.get('_surface_state')
        if state is None:
            state = self._surface_state = threading.local()

        current_w, current_h = self.width, self.height
        target_w = max(1, min(int(width), current_w)) if width else current_w
        target_h = max(1, min(int(height), current_h)) if height else current_h

        surface = _OffscreenSurface(target_w, target_h, self.matrix)
        previous = getattr(state, 'surface', None)
        state.surface = surface
        try:
            yield surface
        finally:
            state.surface = previous

    @contextmanager
    def render_size(self, width: int, height: Optional[int] = None):
        """Temporarily present a smaller logical canvas to plugins.

        Plugins lay out against the ``display_manager.width``/``height``
        properties (which defer to ``matrix.width`` when hardware is present,
        and to the canvas when it is not; some older plugins read
        ``matrix.width`` directly), so the only way to
        get a *narrower layout* rather than a cropped one is to tell the plugin
        the screen is narrower while it renders. Trimming after the fact cannot
        fix a forecast spread across five columns or a progress bar drawn at
        100% width — those need the plugin to make different layout decisions.

        Vegas mode uses this so a plugin can occupy a fraction of a wide panel
        and still look deliberately composed. Reuses the same _LogicalMatrix
        indirection that double-sided mode relies on, so plugins see a
        consistent size from every accessor.

        Built on :meth:`offscreen`, so the narrower canvas belongs to the
        calling thread alone; the render loop keeps drawing on the real one.

        Args:
            width: Logical width to report, clamped to at least 1 and to the
                real panel width (a larger canvas would overflow the hardware).
            height: Logical height, defaulting to the current height.
        """
        current_w = self.width
        current_h = self.height
        target_w = max(1, min(int(width), current_w))
        target_h = max(1, min(int(height) if height else current_h, current_h))

        if target_w == current_w and target_h == current_h:
            # Nothing to do; avoid pointless wrapping and buffer churn.
            yield
            return

        with self.offscreen(target_w, target_h):
            yield

    def _composite_double_sided(self):
        """Tile the logical screen across the full physical chain.

        Renders once into ``self._physical_image`` by pasting the rendered
        logical image ``copies`` times along the configured axis. The paste is
        a single memcpy per copy, so the per-frame cost is negligible and the
        plugin render path is untouched.
        """
        ds = self._double_sided
        phys = self._physical_image
        lw = ds['logical_width']
        lh = ds['logical_height']
        for i in range(ds['copies']):
            if ds['axis'] == 'vertical':
                phys.paste(self.image, (0, i * lh))
            else:
                phys.paste(self.image, (i * lw, 0))
        return phys

    def update_display(self):
        """Update the display using double buffering with proper sync.

        Skips the panel push entirely when the frame is byte-identical to
        the last pushed one (same image digest AND same brightness) — static
        content re-rendered every second, and 125 fps loops between actual
        scroll steps, otherwise re-walk the full framebuffer for nothing.
        The panel keeps refreshing the current frame from its own thread,
        so skipping a swap never blanks or freezes the hardware.

        Correctness hinges on invalidation: clear() resets the digest (it
        writes to the matrix directly), and brightness is PART of the digest
        so a dim-schedule change is never skipped. Disable via config
        ``display.dirty_tracking: false`` if a redraw issue is ever suspected.

        Serialized via ``_update_lock``: plugins can call this directly from
        background threads (e.g. sports base classes push an immediate
        "live" refresh from inside update()), so without a lock two callers
        could both pass the digest check before either writes it back,
        double-pushing a frame, or interleave the offscreen/current canvas
        swap below. The lock is scoped to this method, so callers never
        need to know about it.
        """
        try:
            if self._writes_suppressed():
                # This thread is drawing off-screen. Checked before the lock,
                # so it never contends with the render loop's swap, and before
                # the fallback branch, so captured content never reaches the
                # web preview either.
                return
            with self._update_lock:
                if self.matrix is None:
                    # Fallback mode - no actual hardware to update
                    logger.debug("Update display called in fallback mode (no hardware)")
                    # Still write a snapshot so the web UI can preview
                    self._write_snapshot_if_due()
                    return

                digest = None
                frame_checksum = None
                if self._dirty_tracking_enabled:
                    try:
                        brightness = getattr(self.matrix, 'brightness', None)
                    except AttributeError:
                        brightness = None
                    frame_checksum = zlib.adler32(self.image.tobytes())
                    digest = (frame_checksum, brightness)
                    if digest == self._last_pushed_digest and not self.is_currently_scrolling():
                        # Nothing changed since the last push — the panel is
                        # already showing exactly this frame.
                        #
                        # Never taken mid-scroll, and that exception is the
                        # point. SwapOnVSync is what paces the render loop, so
                        # skipping it also skips the wait: a duplicate frame
                        # returns in ~8ms instead of ~10ms on a 100Hz panel,
                        # advances only 0.8px instead of 1.0px, and so makes
                        # the *next* frame more likely to repeat as well. That
                        # is self-sustaining -- measured at ~20% duplicate
                        # frames mid-scroll on the odds ticker, against
                        # essentially zero on a lighter plugin with identical
                        # scroll settings. Swapping an identical frame costs
                        # one canvas copy and keeps the loop locked to the
                        # panel; falling out of that lock costs smooth motion.
                        # Static content is unaffected: is_currently_scrolling()
                        # expires on its own inactivity threshold.
                        self._write_snapshot_if_due(frame_checksum)
                        return

                # Copy the current image to the offscreen canvas. In double-sided
                # mode the logical screen is first tiled across the full chain.
                blit_started = time.perf_counter()
                if self._double_sided is not None:
                    self.offscreen_canvas.SetImage(self._composite_double_sided())
                else:
                    self.offscreen_canvas.SetImage(self.image)
                blit_done = time.perf_counter()

                # Swap buffers immediately. framerate_fraction holds the frame
                # for N refreshes; SwapOnVSync blocks for all of them, which is
                # what paces the render loop to the chosen frame rate.
                gate = self.render_gate
                if gate is not None:
                    gate.before_swap(self._frame_hold)
                self.matrix.SwapOnVSync(self.offscreen_canvas, self._frame_hold)
                if gate is not None:
                    gate.after_swap(self._frame_hold)
                presented_at = time.perf_counter()
                self.frame_timing.record(
                    blit_done - blit_started, presented_at - blit_done,
                    self._frame_hold, self.is_currently_scrolling(), presented_at)

                # Swap our canvas references
                self.offscreen_canvas, self.current_canvas = self.current_canvas, self.offscreen_canvas

                self._last_pushed_digest = digest

                # Write a snapshot for the web preview (throttled)
                self._write_snapshot_if_due(frame_checksum)
        except Exception as e:
            logger.error(f"Error updating display: {e}")

    def clear(self):
        """Clear the display completely."""
        try:
            if self.matrix is None:
                # Fallback mode - just clear the image
                old_image = getattr(self, 'image', None)
                width = old_image.width if old_image else 64
                height = old_image.height if old_image else 64
                self._new_canvas(width, height)
                logger.debug("Cleared display in fallback mode")
                return

            self._new_canvas(self.matrix.width, self.matrix.height)

            if not self._writes_suppressed():
                # Clear both canvases and the underlying matrix to ensure no artifacts.
                # Failures are non-fatal — the image buffer is already black above, so
                # the next update_display() call will push clean content regardless.
                # The matrix content no longer matches the last pushed digest,
                # so dirty tracking must not skip the next push.
                self._last_pushed_digest = None
                try:
                    self.offscreen_canvas.Clear()
                except (RuntimeError, OSError) as e:
                    logger.error("Failed to clear offscreen canvas: %s", e)
                try:
                    self.current_canvas.Clear()
                except (RuntimeError, OSError) as e:
                    logger.error("Failed to clear current canvas: %s", e)
                try:
                    self.matrix.Clear()
                except (RuntimeError, OSError) as e:
                    logger.error("Failed to clear matrix front buffer: %s", e)
            
            # Note: We do NOT call update_display() here to avoid black flashes.
            # The caller should call update_display() after drawing new content.
            # If an immediate clear is needed, the caller can explicitly call
            # clear() followed by update_display().
        except Exception as e:
            logger.error(f"Error clearing display: {e}")

    def _draw_bdf_text(self, text, x, y, color=(255, 255, 255), font=None):
        """Draw text in a BDF ``freetype.Face`` with (x, y) as its top-left.

        Delegates to :func:`src.common.bdf_font.draw_bdf_text`, which the
        plugin test harness uses too, so previews and golden images show the
        pixels the panel does. Clipped to the logical display size.
        """
        try:
            face = font if font else self.calendar_font
            draw_bdf_text(self.draw, text, x, y, face, color,
                          clip=(self.width, self.height))
        except Exception as e:
            logger.error(f"Error drawing BDF text: {e}", exc_info=True)

    #: The bundled faces, and the size each is *asked* for. Every size here is
    #: run through `crisp_size`, so a number that drifts off the face's pixel
    #: grid is snapped rather than rendered anti-aliased -- see the note on
    #: `extra_small_font` below.
    _FONT_DIR = "assets/fonts"
    _PRESS_START = "PressStart2P-Regular.ttf"
    _FOUR_BY_SIX = "4x6-font.ttf"

    @classmethod
    def _font_asset(cls, filename: str) -> str:
        """Install-root-relative path to a bundled face.

        `_load_fonts` named these relative to the process cwd, which holds
        under the packaged systemd unit (WorkingDirectory is the install root)
        and nowhere else: the plugin safety harness, `python run.py` from
        $HOME, or a unit file written without WorkingDirectory all loaded
        nothing and fell through to `ImageFont.load_default()`. That failure is
        silent -- the panel just renders in PIL's default face at whatever size
        the layout was computed for.
        """
        return resolve_asset_path(f"{cls._FONT_DIR}/{filename}")

    def _load_fonts(self):
        """Load fonts with proper error handling."""
        # Font objects get new id()s after reload, so the text-width cache would
        # return stale measurements keyed on the old ids.  Clear it here.
        self._text_width_cache.clear()
        try:
            # Load Press Start 2P font
            press_start = self._font_asset(self._PRESS_START)
            self.regular_font = load_truetype(press_start, crisp_size(self._PRESS_START, 8))
            logger.info("Press Start 2P font loaded successfully")
            
            # Use the same font for small text (currently same size; adjust size here if needed)
            self.small_font = load_truetype(press_start, crisp_size(self._PRESS_START, 8))
            logger.info("Press Start 2P small font loaded successfully")

            # Load 5x7 BDF font for calendar events
            try:
                self.calendar_font_path = self._font_asset("5x7.bdf")
                logger.info(f"Attempting to load 5x7 font from: {self.calendar_font_path}")
                
                if not os.path.exists(self.calendar_font_path):
                    raise FileNotFoundError(f"Font file not found at {self.calendar_font_path}")
                
                # load_bdf_face sets the size: a Face built without
                # set_char_size reports face.size.height 0, and every caller
                # measuring the 5x7 face with get_font_height() got 0 and
                # stacked rows on top of one another. 5x7.bdf is a fixed
                # strike, so FreeType renders 7px whatever is asked for --
                # the size sets the metrics, not the raster.
                face, _ = load_bdf_face(self.calendar_font_path, _CALENDAR_FONT_PX)
                logger.info(f"5x7 calendar font loaded successfully from {self.calendar_font_path}")
                logger.info(f"Calendar font size: {face.size.height >> 6} pixels")
                
                # Store the face for later use
                self.calendar_font = face
                    
            except Exception as font_err:
                logger.error(f"Failed to load 5x7 font: {str(font_err)}", exc_info=True)
                logger.error("Falling back to small font")
                self.calendar_font = self.small_font

            # Assign the loaded calendar_font (which should be 5x7 BDF or its fallback) 
            # to a new attribute for specific use, e.g., in MusicManager.
            self.bdf_5x7_font = self.calendar_font 
            logger.info(f"Assigned calendar_font (type: {type(self.bdf_5x7_font).__name__}) to bdf_5x7_font.")

            # Load 4x6 font as extra_small_font.
            #
            # Asked for 6 -- the size the face's name suggests -- for years,
            # and 6 is off its 7px pixel grid. Plugins draw this face with
            # `draw.fontmode = "1"`, and the mono rasteriser thresholds each
            # glyph at 50% coverage, so off-grid every glyph came out 3px wide
            # instead of 4. The lost column deforms the letterforms rather than
            # merely thinning them: christmas-countdown rendered "UNTIL" as
            # "VM1JL" and "CHRISTMAS" as "CHAJS1MAS", and zero loses the left
            # half of its bowl. Those renders were committed as golden images.
            #
            # `crisp_size` snaps it to 7. The advance is unchanged -- 5px per
            # glyph at either size -- so nothing reflows and no layout gets
            # tighter; a string is at most a pixel or two wider because the
            # last glyph finally occupies the width it was always given.
            try:
                font_path = self._font_asset(self._FOUR_BY_SIX)
                size = crisp_size(self._FOUR_BY_SIX, 6)
                logger.info(f"Attempting to load 4x6 TTF font from: {font_path} at size {size}")
                self.extra_small_font = load_truetype(font_path, size)
                logger.info(f"4x6 TTF extra small font loaded successfully from {font_path}")
            except Exception as font_err:
                logger.error(f"Failed to load 4x6 TTF font: {font_err}. Falling back.")
                self.extra_small_font = self.small_font



        except Exception as e:
            logger.error(f"Error in font loading: {e}", exc_info=True)
            # Fallback to default font
            self.regular_font = ImageFont.load_default()
            self.small_font = self.regular_font
            self.calendar_font = self.regular_font
            if not hasattr(self, 'extra_small_font'): 
                self.extra_small_font = self.regular_font
            if not hasattr(self, 'bdf_5x7_font'): # Ensure bdf_5x7_font also gets a fallback
                self.bdf_5x7_font = self.regular_font


    def get_text_width(self, text, font):
        """Get the width of text when rendered with the given font.

        Results are cached by (text, font identity) so plugins that measure
        the same string every frame (e.g. to centre a score) pay only one
        measurement per unique (text, font) pair. The entry keeps the font
        alive so its id() can't be recycled, and the cache is LRU-bounded so
        ever-changing text (clocks, tickers) can't grow it without limit.
        """
        cache_key = (text, id(font))
        cached = self._text_width_cache.get(cache_key)
        if cached is not None:
            self._text_width_cache.move_to_end(cache_key)
            return cached[0]

        try:
            if isinstance(font, freetype.Face):
                width = 0
                for char in text:
                    font.load_char(char)
                    width += font.glyph.advance.x >> 6
            else:
                bbox = self.draw.textbbox((0, 0), text, font=font)
                width = bbox[2] - bbox[0]
        except (AttributeError, TypeError, ValueError, OSError) as e:
            logger.error("Error getting text width: %s", e)
            return 0

        self._text_width_cache[cache_key] = (width, font)
        while len(self._text_width_cache) > self._TEXT_WIDTH_CACHE_MAX:
            self._text_width_cache.popitem(last=False)
        return width

    def get_font_height(self, font):
        """Get the height of the given font for line spacing purposes."""
        try:
            if isinstance(font, freetype.Face):
                # For FreeType faces (BDF), the 'height' metric gives the recommended line spacing.
                height = font.size.height >> 6
                if height:
                    return height
                # A Face constructed without set_char_size reports 0, and a
                # zero line height collapses every stacked row onto one line.
                # Fall back to the strike the file declares.
                return _bdf_native_size(font) or 8
            else:
                # For PIL TTF fonts, getmetrics() provides ascent and descent.
                # The line height is the sum of ascent and descent.
                ascent, descent = font.getmetrics()
                return ascent + descent
        except Exception as e:
            logger.error(f"Error getting font height for font type {type(font).__name__}: {e}")
            # Fallback for TTF font if getmetrics() fails, or for other font types.
            if hasattr(font, 'size'):
                return font.size
            return 8 # A reasonable default for an 8px font.

    def draw_text(self, text: str, x: int = None, y: int = None, color: tuple = (255, 255, 255), 
                 small_font: bool = False, font: ImageFont = None, centered: bool = False):
        """Draw text on the canvas with optional font selection.
        
        Args:
            text: Text to display
            x: X position (None to auto-center, or used as center point if centered=True)
            y: Y position (None defaults to 0)
            color: RGB color tuple
            small_font: Use small font if True
            font: Custom font object (overrides small_font)
            centered: If True, x is treated as center point; if False, x is left edge
        """
        try:
            # Select font based on parameters
            if font:
                current_font = font
            else:
                current_font = self.small_font if small_font else self.regular_font
            
            # Calculate x position
            if x is None:
                # No x provided - center text
                text_width = self.get_text_width(text, current_font)
                x = (self.width - text_width) // 2
            elif centered:
                # x is provided as center point - adjust to left edge
                text_width = self.get_text_width(text, current_font)
                x = x - (text_width // 2)
            
            # Set default y position if not provided
            if y is None:
                y = 0  # Default to top of display
            
            # Draw the text
            if isinstance(current_font, freetype.Face):
                # For BDF fonts, _draw_bdf_text will compute the baseline from the
                # provided top-left y using the font ascender. Do not adjust here.
                self._draw_bdf_text(text, x, y, color, current_font)
            else:
                # For TTF fonts, use PIL's text drawing which expects top-left.
                self.draw.text((x, y), text, font=current_font, fill=color)
            
        except Exception as e:
            logger.error(f"Error drawing text: {e}", exc_info=True)

    @deprecated("3.7.0")
    def draw_sun(self, x: int, y: int, size: int = 16):
        """Draw a sun icon using yellow circles and lines."""
        center = (x + size//2, y + size//2)
        radius = size//3
        
        # Draw the center circle
        self.draw.ellipse([center[0]-radius, center[1]-radius, 
                          center[0]+radius, center[1]+radius], 
                         fill=(255, 255, 0))  # Yellow
        
        # Draw the rays
        ray_length = size//4
        for angle in range(0, 360, 45):
            rad = math.radians(angle)
            start_x = center[0] + (radius * math.cos(rad))
            start_y = center[1] + (radius * math.sin(rad))
            end_x = center[0] + ((radius + ray_length) * math.cos(rad))
            end_y = center[1] + ((radius + ray_length) * math.sin(rad))
            self.draw.line([start_x, start_y, end_x, end_y], fill=(255, 255, 0), width=2)

    @deprecated("3.7.0")
    def draw_cloud(self, x: int, y: int, size: int = 16, color=(200, 200, 200)):
        """Draw a cloud icon."""
        # Draw multiple circles to form a cloud shape
        self.draw.ellipse([x+size//4, y+size//3, x+size//4+size//2, y+size//3+size//2], fill=color)
        self.draw.ellipse([x+size//2, y+size//3, x+size//2+size//2, y+size//3+size//2], fill=color)
        self.draw.ellipse([x+size//3, y+size//6, x+size//3+size//2, y+size//6+size//2], fill=color)

    @deprecated("3.7.0")
    def draw_rain(self, x: int, y: int, size: int = 16):
        """Draw rain icon with cloud and droplets."""
        # Draw cloud
        self.draw_cloud(x, y, size)
        
        # Draw rain drops
        drop_color = (0, 0, 255)  # Blue
        drop_size = size//6
        for i in range(3):
            drop_x = x + size//4 + (i * size//3)
            drop_y = y + size//2
            self.draw.line([drop_x, drop_y, drop_x, drop_y+drop_size], 
                          fill=drop_color, width=2)

    @deprecated("3.7.0")
    def draw_snow(self, x: int, y: int, size: int = 16):
        """Draw snow icon with cloud and snowflakes."""
        # Draw cloud
        self.draw_cloud(x, y, size)
        
        # Draw snowflakes
        snow_color = (200, 200, 255)  # Light blue
        for i in range(3):
            center_x = x + size//4 + (i * size//3)
            center_y = y + size//2 + size//4
            # Draw a small star shape
            for angle in range(0, 360, 60):
                rad = math.radians(angle)
                end_x = center_x + (size//8 * math.cos(rad))
                end_y = center_y + (size//8 * math.sin(rad))
                self.draw.line([center_x, center_y, end_x, end_y], 
                             fill=snow_color, width=1)

    # Weather icon color constants
    WEATHER_COLORS = {
        'sun': (255, 200, 0),    # Bright yellow
        'cloud': (200, 200, 200), # Light gray
        'rain': (0, 100, 255),    # Light blue
        'snow': (220, 220, 255),  # Ice blue
        'storm': (255, 255, 0)    # Lightning yellow
    }

    def _draw_sun(self, x: int, y: int, size: int) -> None:
        """Draw a sun icon with rays."""
        center_x, center_y = x + size//2, y + size//2
        radius = size//4
        ray_length = size//3
        
        # Draw the main sun circle
        self.draw.ellipse([center_x - radius, center_y - radius, 
                          center_x + radius, center_y + radius], 
                         fill=self.WEATHER_COLORS['sun'])
        
        # Draw sun rays
        for angle in range(0, 360, 45):
            rad = math.radians(angle)
            start_x = center_x + int((radius + 2) * math.cos(rad))
            start_y = center_y + int((radius + 2) * math.sin(rad))
            end_x = center_x + int((radius + ray_length) * math.cos(rad))
            end_y = center_y + int((radius + ray_length) * math.sin(rad))
            self.draw.line([start_x, start_y, end_x, end_y], 
                         fill=self.WEATHER_COLORS['sun'], width=2)

    def _draw_cloud(self, x: int, y: int, size: int) -> None:
        """Draw a cloud using multiple circles."""
        cloud_color = self.WEATHER_COLORS['cloud']
        base_y = y + size//2
        
        # Draw main cloud body (3 overlapping circles)
        circle_radius = size//4
        positions = [
            (x + size//3, base_y),           # Left circle
            (x + size//2, base_y - size//6), # Top circle
            (x + 2*size//3, base_y)          # Right circle
        ]
        
        for cx, cy in positions:
            self.draw.ellipse([cx - circle_radius, cy - circle_radius,
                             cx + circle_radius, cy + circle_radius],
                            fill=cloud_color)

    def _draw_rain(self, x: int, y: int, size: int) -> None:
        """Draw rain drops falling from a cloud."""
        self._draw_cloud(x, y, size)
        rain_color = self.WEATHER_COLORS['rain']
        
        # Draw rain drops at an angle
        drop_size = size//8
        drops = [
            (x + size//4, y + 2*size//3),
            (x + size//2, y + 3*size//4),
            (x + 3*size//4, y + 2*size//3)
        ]
        
        for dx, dy in drops:
            # Draw angled rain drops
            self.draw.line([dx, dy, dx - drop_size//2, dy + drop_size],
                         fill=rain_color, width=2)

    def _draw_snow(self, x: int, y: int, size: int) -> None:
        """Draw snowflakes falling from a cloud."""
        self._draw_cloud(x, y, size)
        snow_color = self.WEATHER_COLORS['snow']
        
        # Draw snowflakes
        flake_size = size//6
        flakes = [
            (x + size//4, y + 2*size//3),
            (x + size//2, y + 3*size//4),
            (x + 3*size//4, y + 2*size//3)
        ]
        
        for fx, fy in flakes:
            # Draw a snowflake (six-pointed star)
            for angle in range(0, 360, 60):
                rad = math.radians(angle)
                end_x = fx + int(flake_size * math.cos(rad))
                end_y = fy + int(flake_size * math.sin(rad))
                self.draw.line([fx, fy, end_x, end_y],
                             fill=snow_color, width=1)

    def _draw_storm(self, x: int, y: int, size: int) -> None:
        """Draw a storm cloud with lightning bolt."""
        self._draw_cloud(x, y, size)
        
        # Draw lightning bolt
        bolt_color = self.WEATHER_COLORS['storm']
        bolt_points = [
            (x + size//2, y + size//2),          # Top
            (x + 3*size//5, y + 2*size//3),      # Middle right
            (x + 2*size//5, y + 2*size//3),      # Middle left
            (x + size//2, y + 5*size//6)         # Bottom
        ]
        self.draw.polygon(bolt_points, fill=bolt_color)

    @deprecated("3.7.0")
    def draw_weather_icon(self, condition: str, x: int, y: int, size: int = 16) -> None:
        """Draw a weather icon based on the condition."""
        if condition.lower() in ['clear', 'sunny']:
            self._draw_sun(x, y, size)
        elif condition.lower() in ['clouds', 'cloudy', 'partly cloudy']:
            self._draw_cloud(x, y, size)
        elif condition.lower() in ['rain', 'drizzle', 'shower']:
            self._draw_rain(x, y, size)
        elif condition.lower() in ['snow', 'sleet', 'hail']:
            self._draw_snow(x, y, size)
        elif condition.lower() in ['thunderstorm', 'storm']:
            self._draw_storm(x, y, size)
        else:
            self._draw_sun(x, y, size)
        # Note: No update_display() here - let the caller handle the update

    @deprecated("3.7.0")
    def draw_text_with_icons(self, text: str, icons: List[tuple] = None, x: int = None, y: int = None, 
                            color: tuple = (255, 255, 255)):
        """Draw text with weather icons at specified positions."""
        # Draw the text
        self.draw_text(text, x, y, color)
        
        # Draw any icons
        if icons:
            for icon_type, icon_x, icon_y in icons:
                self.draw_weather_icon(icon_type, icon_x, icon_y)
        
        # Update the display once after everything is drawn
        self.update_display()

    def cleanup(self):
        """Clean up resources."""
        if hasattr(self, '_snapshot_cond'):
            self._stop_snapshot_writer()
        if getattr(self, 'frame_timing', None) is not None:
            self.frame_timing.close()
        if hasattr(self, 'matrix') and self.matrix is not None:
            try:
                self.matrix.Clear()
            except Exception as e:
                logger.warning(f"Error clearing matrix during cleanup: {e}")
        # Ensure image/draw are reset to a blank state
        if hasattr(self, 'image') and hasattr(self, 'draw'):
            try:
                self._new_canvas(self.width, self.height)
            except (OSError, RuntimeError, ValueError, MemoryError):
                logger.debug("Canvas reset during cleanup failed", exc_info=True)
        # Reset the singleton state when cleaning up
        DisplayManager._instance = None

    def format_date_with_ordinal(self, dt):
        """Formats a datetime object into 'Mon Aug 30th' style."""
        day = dt.day
        if 11 <= day <= 13:
            suffix = 'th'
        else:
            suffix = {1: 'st', 2: 'nd', 3: 'rd'}.get(day % 10, 'th')
        
        return dt.strftime(f"%b %-d{suffix}") 

    @classmethod
    def apply_matrix_options(cls, options, config: Dict[str, Any]):
        """Fill ``options`` (an ``RGBMatrixOptions``) from the LEDMatrix config.

        This is exactly what the display service drives the panel with, so a
        tool that opens the matrix itself (``scripts/scroll_speeds.py``) gets
        the same panel -- same runtime ``gpio_slowdown``, ``rp1_rio``,
        ``panel_type``, orientation and defaults -- rather than a private copy
        that drifts. Does not open the matrix. Returns ``options``.
        """
        display = config.get('display', {}) if isinstance(config, dict) else {}
        hardware_config = display.get('hardware', {})
        runtime_config = display.get('runtime', {})

        # Basic hardware settings
        options.rows = hardware_config.get('rows', DEFAULT_ROWS)
        options.cols = hardware_config.get('cols', DEFAULT_COLS)
        options.chain_length = hardware_config.get('chain_length', DEFAULT_CHAIN_LENGTH)
        options.parallel = hardware_config.get('parallel', DEFAULT_PARALLEL)
        options.hardware_mapping = hardware_config.get('hardware_mapping', 'adafruit-hat-pwm')

        # Performance and stability settings
        options.brightness = hardware_config.get('brightness', 90)
        options.pwm_bits = hardware_config.get('pwm_bits', 10)
        options.pwm_lsb_nanoseconds = hardware_config.get('pwm_lsb_nanoseconds', 150)
        options.led_rgb_sequence = hardware_config.get('led_rgb_sequence', 'RGB')
        # Orientation becomes a "Rotate:<deg>" pixel mapper; the web preview
        # composes it the same way so it sizes the canvas identically.
        options.pixel_mapper_config = compose_pixel_mapper_config(hardware_config)
        options.row_address_type = hardware_config.get('row_address_type', 0)
        options.multiplexing = hardware_config.get('multiplexing', 0)
        options.panel_type = hardware_config.get('panel_type', '')
        options.disable_hardware_pulsing = hardware_config.get('disable_hardware_pulsing', False)
        options.show_refresh_rate = hardware_config.get('show_refresh_rate', False)
        options.limit_refresh_rate_hz = hardware_config.get('limit_refresh_rate_hz', 90)
        options.gpio_slowdown = runtime_config.get('gpio_slowdown', 3)

        # Disable internal privilege dropping - we manage this via systemd or remain root
        # This prevents the library from dropping to 'daemon' user which breaks file permissions
        options.drop_privileges = False

        # Additional settings from config
        if 'scan_mode' in hardware_config:
            options.scan_mode = hardware_config.get('scan_mode')
        if 'pwm_dither_bits' in hardware_config:
            options.pwm_dither_bits = hardware_config.get('pwm_dither_bits')
        if 'inverse_colors' in hardware_config:
            options.inverse_colors = hardware_config.get('inverse_colors')
        # Pi 5 only: 0=PIO/RP1 coprocessor (default, less CPU),
        # 1=RIO/Registered IO (faster; gpio_slowdown effect is inverted in this mode)
        if 'rp1_rio' in runtime_config:
            if hasattr(options, 'rp1_rio'):
                options.rp1_rio = runtime_config.get('rp1_rio')
            else:
                logger.warning(
                    "rp1_rio is set in config but the installed rgbmatrix library does "
                    "not support it — the library was likely built without Pi 5 RP1 "
                    "support (mmap to 0x3f000000 instead of RP1 chip). "
                    "Fix: sudo RPI_RGB_FORCE_REBUILD=1 ./first_time_install.sh"
                )
        return options

    @property
    def refresh_hz(self) -> float:
        """The panel's refresh rate in Hz, from the hardware config.

        The authoritative place to ask, because a plugin only receives its own
        config section and cannot see display.hardware. Scroll pacing needs
        this: the speeds a panel can show in whole pixels are refresh_hz
        divided by the frame hold, so getting it wrong silently produces
        fractional-pixel motion. See src/common/scroll_config.py.

        Note this is the configured *cap*, not necessarily what the panel
        achieves -- scripts/scroll_speeds.py --measure reports the real rate.
        """
        hardware = (self.config.get('display') or {}).get('hardware') or {}
        try:
            value = float(hardware.get('limit_refresh_rate_hz') or 0)
        except (TypeError, ValueError):
            value = 0.0
        return value if value > 0 else 100.0

    def _scrolling_now(self) -> bool:
        """Whether a scroll is running, without is_currently_scrolling()'s
        side effect of expiring the state -- safe from the stall watchdog's
        thread."""
        state = self._scrolling_state
        return bool(state['is_scrolling']) and (
            time.time() - state['last_scroll_activity']
            <= state['scroll_inactivity_threshold'])

    def _frame_timing_info(self) -> Dict[str, Any]:
        """What the frame-timing stats were measured on, for the soak report."""
        display = self.config.get('display') or {}
        hardware = display.get('hardware') or {}
        runtime = display.get('runtime') or {}
        info = {key: hardware.get(key) for key in (
            'rows', 'cols', 'chain_length', 'parallel', 'pwm_bits',
            'hardware_mapping', 'limit_refresh_rate_hz', 'pixel_mapper_config')}
        info['gpio_slowdown'] = runtime.get('gpio_slowdown')
        info['emulator'] = os.environ.get('EMULATOR', 'false') == 'true'
        return info

    def set_frame_hold(self, refreshes: int) -> None:
        """Hold each pushed frame for this many panel refreshes (>=1).

        Set by the scroll configuration so a plugin can run at, say, 50px/s on
        a 100Hz panel as one whole pixel every second refresh, rather than half
        a pixel every refresh (which has to be blended or repeated unevenly).

        Reset to 1 whenever scrolling stops, so one plugin's pacing cannot
        leak into the next thing on screen.
        """
        if self._writes_suppressed():
            return  # a plugin drawing off-screen cannot re-pace the live scroll
        try:
            value = int(refreshes)
        except (TypeError, ValueError):
            logger.warning("Ignoring unusable frame hold: %r", refreshes)
            return
        self._frame_hold = max(1, min(255, value))

    def set_scrolling_state(self, is_scrolling: bool, frame_hold: int = 1):
        """Set the current scrolling state, and this scroll's frame pacing.

        Call this when a display starts or stops scrolling. ``frame_hold`` is
        how many panel refreshes each frame is held for -- 2 gives one whole
        pixel every second refresh, which is how a scroll runs at half the
        refresh rate without fractional pixel positions.

        The hold is part of the scroll's speed. A ScrollHelper configured by
        ``scroll_config.configure()`` advances a fixed whole-pixel step per
        presented frame and reads no clock, so pass the returned
        ``settings.frame_hold`` here: a scroll that leaves it at 1 is
        presented every refresh and runs ``frame_hold`` times too fast.

        The hold is set here rather than once at plugin construction because
        it must not outlive the scroll that asked for it: plugins share one
        display manager, so a hold left set by whoever scrolled last would
        silently re-pace the next plugin. Passing it alongside the state makes
        the lifetime exactly the scroll, and the default of 1 means any caller
        that does not care gets a new frame every refresh.
        """
        if self._writes_suppressed():
            # A plugin captured for Vegas calls this from its own display();
            # it must not change the live scroll's state or frame hold.
            return
        current_time = time.time()
        # Scrolling callers set this every frame; log transitions only.
        changed = self._scrolling_state['is_scrolling'] != is_scrolling
        self._scrolling_state['is_scrolling'] = is_scrolling
        if is_scrolling:
            self._scrolling_state['last_scroll_activity'] = current_time
            self.set_frame_hold(frame_hold)
        else:
            self._frame_hold = 1
        if changed:
            logger.debug("Scrolling state set to: %s", is_scrolling)

    def is_currently_scrolling(self) -> bool:
        """Check if the display is currently in a scrolling state."""
        current_time = time.time()
        
        # If explicitly not scrolling, return False
        if not self._scrolling_state['is_scrolling']:
            return False
            
        # If we've been inactive for the threshold period, consider it not scrolling
        if current_time - self._scrolling_state['last_scroll_activity'] > self._scrolling_state['scroll_inactivity_threshold']:
            self._scrolling_state['is_scrolling'] = False
            # Drop the hold with the state, exactly as set_scrolling_state(False)
            # does. This path is the one a scroll takes when it ends without
            # saying so -- the rotation moves on mid-scroll, or the plugin is
            # torn down -- and leaving the hold set there means every later
            # plugin, scrolling or static, is presented at refresh/N until
            # somebody calls set_scrolling_state(False). The hold must not
            # outlive the scroll that asked for it, however that scroll ends.
            self._frame_hold = 1
            return False
            
        return True

    def defer_update(self, update_func, priority: int = 0):
        """Defer an update function to be called when not scrolling.
        
        Args:
            update_func: Function to call when not scrolling
            priority: Priority level (lower numbers = higher priority)
        """
        current_time = time.time()
        
        # Clean up expired updates before adding new ones
        self._cleanup_expired_deferred_updates(current_time)
        
        # Limit queue size to prevent memory issues
        if len(self._scrolling_state['deferred_updates']) >= self._scrolling_state['max_deferred_updates']:
            # Remove oldest update to make room
            self._scrolling_state['deferred_updates'].pop(0)
            logger.debug("Removed oldest deferred update due to queue size limit")
        
        self._scrolling_state['deferred_updates'].append({
            'func': update_func,
            'priority': priority,
            'timestamp': current_time
        })
        
        # Only sort if we have a reasonable number of updates to avoid excessive sorting
        if len(self._scrolling_state['deferred_updates']) <= 20:
            self._scrolling_state['deferred_updates'].sort(key=lambda x: x['priority'])
        
        logger.debug(f"Deferred update added. Total deferred: {len(self._scrolling_state['deferred_updates'])}")

    def process_deferred_updates(self):
        """Process any deferred updates if not currently scrolling."""
        current_time = time.time()
        
        # Always clean up expired updates, even if scrolling
        # This prevents memory leaks from accumulated expired updates
        self._cleanup_expired_deferred_updates(current_time)
        
        if self.is_currently_scrolling():
            return
            
        if not self._scrolling_state['deferred_updates']:
            return
            
        # Process only a limited number of updates per call to avoid blocking
        max_updates_per_call = min(5, len(self._scrolling_state['deferred_updates']))
        updates_to_process = self._scrolling_state['deferred_updates'][:max_updates_per_call]
        self._scrolling_state['deferred_updates'] = self._scrolling_state['deferred_updates'][max_updates_per_call:]
        
        logger.debug(f"Processing {len(updates_to_process)} deferred updates (queue size: {len(self._scrolling_state['deferred_updates'])})")
        
        failed_updates = []
        for update_info in updates_to_process:
            try:
                # Check if update is still valid (not too old)
                if current_time - update_info['timestamp'] > self._scrolling_state['deferred_update_ttl']:
                    logger.debug("Skipping expired deferred update")
                    continue
                    
                update_info['func']()
                logger.debug("Deferred update executed successfully")
            except Exception as e:
                logger.error(f"Error executing deferred update: {e}")
                # Only retry recent failures, and limit retries
                if current_time - update_info['timestamp'] < 60.0:  # Only retry for 1 minute
                    failed_updates.append(update_info)
        
        # Re-add failed updates to the end of the queue (not the beginning)
        if failed_updates:
            self._scrolling_state['deferred_updates'].extend(failed_updates)

    def _cleanup_expired_deferred_updates(self, current_time: float):
        """Remove expired deferred updates to prevent memory leaks."""
        ttl = self._scrolling_state['deferred_update_ttl']
        initial_count = len(self._scrolling_state['deferred_updates'])
        
        # Filter out expired updates
        self._scrolling_state['deferred_updates'] = [
            update for update in self._scrolling_state['deferred_updates']
            if current_time - update['timestamp'] <= ttl
        ]
        
        removed_count = initial_count - len(self._scrolling_state['deferred_updates'])
        if removed_count > 0:
            logger.debug(f"Cleaned up {removed_count} expired deferred updates")

    @deprecated("3.7.0")
    def get_scrolling_stats(self) -> dict:
        """Get current scrolling statistics for debugging."""
        return {
            'is_scrolling': self._scrolling_state['is_scrolling'],
            'last_activity': self._scrolling_state['last_scroll_activity'],
            'deferred_count': len(self._scrolling_state['deferred_updates']),
            'inactivity_threshold': self._scrolling_state['scroll_inactivity_threshold'],
            'max_deferred_updates': self._scrolling_state['max_deferred_updates'],
            'deferred_update_ttl': self._scrolling_state['deferred_update_ttl']
        }

    def _viewer_is_fresh(self, now: float) -> bool:
        """True when a browser preview is watching (marker file touched by
        the web SSE broadcaster). The marker is stat'd at most once per
        second — at 125 fps loops a per-call stat would be pure overhead."""
        if (now - self._viewer_check_ts) >= 1.0:
            self._viewer_check_ts = now
            try:
                marker_age = now - os.stat(self._viewer_marker_path).st_mtime
                self._viewer_fresh = marker_age < snapshot_policy.VIEWER_MARKER_FRESH_SEC
            except OSError:
                self._viewer_fresh = False
        return self._viewer_fresh

    def _write_snapshot_if_due(self, frame_checksum: Optional[int] = None) -> None:
        """Mirror the current frame to the preview snapshot when the policy
        says it's worth it — see src/common/snapshot_policy.py. Unchanged
        frames are never re-encoded; without viewers the cadence drops to
        the idle keepalive.

        Args:
            frame_checksum: adler32 of the current frame, when the caller has
                already computed one. Dirty tracking checksums every frame a
                few lines above the call site, and re-deriving it here meant a
                second tobytes() plus a second pass over the whole framebuffer
                on every single frame — ~0.17ms per frame of the two combined
                at 256x64, paid 100 times a second to reach the same number.
        """
        try:
            now = time.time()
            viewer_fresh = self._viewer_is_fresh(now)
            if viewer_fresh and not self._viewer_was_fresh:
                # A preview just opened: let the next changed frame through
                # immediately instead of waiting out the idle interval.
                self._last_snapshot_ts = 0.0
            self._viewer_was_fresh = viewer_fresh

            digest = (frame_checksum if frame_checksum is not None
                      else zlib.adler32(self.image.tobytes()))
            action = snapshot_policy.decide(
                now, self._last_snapshot_ts, self._last_snapshot_touch_ts,
                viewer_fresh, digest != self._last_snapshot_digest)
            if action is snapshot_policy.SnapshotAction.SKIP:
                return
            if (action is snapshot_policy.SnapshotAction.TOUCH
                    and self._saved_snapshot_digest == digest):
                # mtime bump only: keeps the health check (snapshot age)
                # green without paying for a PNG encode of an unchanged frame
                os.utime(self._snapshot_path, None)
                self._last_snapshot_touch_ts = now
                return
            # (A TOUCH for a frame that isn't on disk yet -- still queued, or
            # its write failed -- is written instead: touching would make the
            # older file on disk look current.)

            # WRITE. Mid-scroll the PNG encode goes to a background thread: at
            # 512x64 it takes 12-14ms on a Pi 4, longer than a 95Hz refresh,
            # so on the render thread every preview write made the next swap
            # miss its vsync -- five visible hitches a second, but only while
            # someone had the web preview open. Pillow releases the GIL while
            # it compresses, so the encode no longer holds the loop up. Static
            # frames still write inline: nothing is moving to disturb.
            if self.is_currently_scrolling():
                self._queue_snapshot(self.image.copy(), digest)
            else:
                # A scroll that just ended can leave its last frame queued or
                # mid-write; it must not land on top of this newer one.
                with self._snapshot_write_lock:
                    with self._snapshot_cond:
                        self._snapshot_pending = None
                    self._save_snapshot(self.image)
                    self._saved_snapshot_digest = digest
            self._last_snapshot_ts = now
            self._last_snapshot_touch_ts = now
            self._last_snapshot_digest = digest
        except Exception as e:
            self._log_snapshot_failure(e)

    def _log_snapshot_failure(self, error: Exception) -> None:
        # Snapshot failures must never break display — but they must not
        # be silent either: the snapshot's mtime is the web UI's display
        # mirror AND its hardware-liveness proxy, so a quietly failing
        # write freezes the mirror and makes health checks lie (seen in
        # the field: a stale root-owned /tmp file froze it for a day).
        # Warn at most once per 5 minutes to avoid log spam.
        now = time.time()
        if (now - self._snapshot_fail_log_ts) > 300:
            self._snapshot_fail_log_ts = now
            logger.warning("Snapshot write failing (web preview/health "
                           "mirror is stale): %s", error)
        else:
            logger.debug(f"Snapshot write skipped: {error}")

    def _save_snapshot(self, image: Image.Image) -> None:
        """Encode ``image`` to the snapshot path atomically. Raises on failure."""
        # Ensure directory permissions once, not per frame
        snapshot_path_obj = Path(self._snapshot_path)
        if not self._snapshot_dir_prepared:
            # Never modify /tmp permissions - it has special system
            # permissions (1777) that must not be changed or it breaks
            # apt and other system tools
            parent_dir = snapshot_path_obj.parent
            if parent_dir and str(parent_dir) != '/tmp':  # nosec B108 - guard to skip /tmp for permission ops
                ensure_directory_permissions(parent_dir, get_assets_dir_mode())
            self._snapshot_dir_prepared = True
        # Write atomically: temp then replace. The temp name must be
        # unique, not "<snapshot>.tmp": /tmp is world-writable and sticky,
        # and this file is written by whichever user the display service
        # runs as while tests and tooling run as someone else. A leftover
        # fixed-name temp owned by another user is then unopenable even by
        # root (fs.protected_regular refuses O_CREAT on a foreign file in a
        # sticky dir), which froze the preview and the health check's
        # liveness proxy until somebody deleted it by hand. Same pattern as
        # the hardware-status write above.
        _fd, tmp_path = tempfile.mkstemp(
            dir=str(snapshot_path_obj.parent),
            prefix=f".{snapshot_path_obj.name}.", suffix=".tmp")
        try:
            with os.fdopen(_fd, "wb") as _f:
                image.save(_f, format='PNG')
            os.chmod(tmp_path, 0o644)
            os.replace(tmp_path, self._snapshot_path)
        except Exception:
            # Never leave the temp behind -- that is what made the failure
            # permanent rather than transient.
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            # Fallback to direct save if replace not supported
            image.save(self._snapshot_path, format='PNG')
        # Set proper file permissions after saving
        try:
            ensure_file_permissions(snapshot_path_obj, get_assets_file_mode())
        except Exception:
            pass

    def _queue_snapshot(self, image: Image.Image, digest: Optional[int] = None) -> None:
        """Hand a frame to the snapshot writer thread; the newest frame wins.

        One slot, not a queue: if the writer is still encoding when the next
        frame is due, the waiting frame is simply replaced. The preview wants
        the latest frame, and a backlog would only cost memory and CPU.
        """
        with self._snapshot_cond:
            self._snapshot_pending = (image, digest)
            if self._snapshot_thread is None or not self._snapshot_thread.is_alive():
                self._snapshot_thread = threading.Thread(
                    target=self._snapshot_writer, daemon=True,
                    name="snapshot-writer")
                self._snapshot_thread.start()
            self._snapshot_cond.notify()

    def _snapshot_writer(self) -> None:
        while True:
            with self._snapshot_cond:
                while self._snapshot_pending is None and not self._snapshot_stop:
                    self._snapshot_cond.wait()
                if self._snapshot_stop:
                    return          # shutting down: a pending frame is dropped
            # The write lock before the frame: whichever of this and an inline
            # static save gets it first also writes first, and a static save
            # clears the slot, so an older frame never lands on a newer one.
            with self._snapshot_write_lock:
                with self._snapshot_cond:
                    pending, self._snapshot_pending = self._snapshot_pending, None
                if pending is None:
                    continue
                image, digest = pending
                try:
                    self._save_snapshot(image)
                    self._saved_snapshot_digest = digest
                except Exception as e:
                    # The frame was recorded as written when it was queued.
                    # Forget that, so an unchanged frame is written again
                    # rather than only mtime-touching a stale file into
                    # looking healthy.
                    self._last_snapshot_digest = None
                    self._log_snapshot_failure(e)

    def _stop_snapshot_writer(self, timeout: float = 1.0) -> None:
        """Stop the writer thread, dropping any frame it has not started."""
        with self._snapshot_cond:
            self._snapshot_stop = True
            self._snapshot_pending = None
            self._snapshot_cond.notify_all()
        thread = self._snapshot_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)
        self._snapshot_thread = None