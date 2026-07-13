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
import tempfile
if os.getenv("EMULATOR", "false") == "true":
    from RGBMatrixEmulator import RGBMatrix, RGBMatrixOptions
else:
    from rgbmatrix import RGBMatrix, RGBMatrixOptions
from contextlib import contextmanager
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import threading
import time
from collections import OrderedDict
from typing import Dict, Any, List, Optional, Tuple
import logging
import math
import zlib
import freetype

from src.common import snapshot_policy
from src.common.permission_utils import (
    ensure_directory_permissions,
    ensure_file_permissions,
    get_assets_dir_mode,
    get_assets_file_mode,
)

# Get logger without configuring
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)  # Set to INFO level


class _LogicalMatrix:
    """Proxy that reports a logical (per-screen) size for a physical matrix.

    In double-sided mode the physical panel chain shows N identical copies of a
    smaller logical screen. Plugins size themselves from ``matrix.width`` /
    ``matrix.height`` (the documented convention, used at 30+ call sites), so
    this proxy reports the logical dimensions while delegating every real
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


def _resolve_double_sided(physical_width: int, physical_height: int,
                          ds_config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Validate the ``display.double_sided`` config against the physical size.

    Returns a dict ``{copies, axis, logical_width, logical_height}`` when the
    feature is enabled and the physical panel divides evenly into ``copies``
    along the chosen axis, otherwise ``None`` (single-screen behaviour). Bad
    config is logged and disabled rather than raised — a misconfigured panel
    should still light up.
    """
    if not isinstance(ds_config, dict) or not ds_config.get('enabled', False):
        return None

    copies = ds_config.get('copies', 2)
    if not isinstance(copies, int) or copies < 2:
        logger.warning(
            "double_sided: 'copies' must be an integer >= 2 (got %r); "
            "disabling double-sided mode", copies)
        return None

    axis = ds_config.get('axis', 'horizontal')
    if axis not in ('horizontal', 'vertical'):
        logger.warning(
            "double_sided: 'axis' must be 'horizontal' or 'vertical' "
            "(got %r); defaulting to 'horizontal'", axis)
        axis = 'horizontal'

    # Horizontal splits the chain (panels side by side); vertical splits the
    # parallel outputs (panels stacked). The split axis must divide evenly.
    if axis == 'horizontal':
        if physical_width % copies != 0:
            logger.warning(
                "double_sided: physical width %d is not divisible by copies "
                "%d; disabling double-sided mode", physical_width, copies)
            return None
        logical_width = physical_width // copies
        logical_height = physical_height
    else:
        if physical_height % copies != 0:
            logger.warning(
                "double_sided: physical height %d is not divisible by copies "
                "%d; disabling double-sided mode", physical_height, copies)
            return None
        logical_width = physical_width
        logical_height = physical_height // copies

    logger.info(
        "double_sided enabled: %d copies on %s axis — logical screen %dx%d "
        "tiled across physical %dx%d", copies, axis, logical_width,
        logical_height, physical_width, physical_height)
    return {
        'copies': copies,
        'axis': axis,
        'logical_width': logical_width,
        'logical_height': logical_height,
    }


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
    _initialized = False

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super(DisplayManager, cls).__new__(cls)
        return cls._instance

    def __init__(self, config: Dict[str, Any] = None, force_fallback: bool = False, suppress_test_pattern: bool = False):
        start_time = time.time()
        self.config = config or {}
        self._force_fallback = force_fallback
        self._suppress_test_pattern = suppress_test_pattern
        # When True, update_display() and clear() skip hardware writes (used during off-screen content capture)
        self._capture_mode_active = False
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
        self._snapshot_dir_prepared = False
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
        
        # Initialize managers
        # Calendar manager is now initialized by DisplayController
        
    def _setup_matrix(self):
        """Initialize the RGB matrix with configuration settings."""
        _init_error_str = None
        try:
            # Allow callers (e.g., web UI) to force non-hardware fallback mode
            if getattr(self, '_force_fallback', False):
                raise RuntimeError('Forced fallback mode requested')
            options = RGBMatrixOptions()
            
            # Hardware configuration
            hardware_config = self.config.get('display', {}).get('hardware', {})
            runtime_config = self.config.get('display', {}).get('runtime', {})
            
            # Basic hardware settings
            options.rows = hardware_config.get('rows', 32)
            options.cols = hardware_config.get('cols', 64)
            options.chain_length = hardware_config.get('chain_length', 2)
            options.parallel = hardware_config.get('parallel', 1)
            options.hardware_mapping = hardware_config.get('hardware_mapping', 'adafruit-hat-pwm')
            
            # Performance and stability settings
            options.brightness = hardware_config.get('brightness', 90)
            options.pwm_bits = hardware_config.get('pwm_bits', 10)
            options.pwm_lsb_nanoseconds = hardware_config.get('pwm_lsb_nanoseconds', 150)
            options.led_rgb_sequence = hardware_config.get('led_rgb_sequence', 'RGB')
            options.pixel_mapper_config = hardware_config.get('pixel_mapper_config', '')
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
            ds = _resolve_double_sided(self.matrix.width, self.matrix.height, ds_config)
            self._double_sided = ds
            if ds is not None:
                self._physical_image = Image.new(
                    'RGB', (self.matrix.width, self.matrix.height))
                self.matrix = _LogicalMatrix(
                    self.matrix, ds['logical_width'], ds['logical_height'])

            # Create image with the (logical) display dimensions
            self.image = Image.new('RGB', (self.matrix.width, self.matrix.height))
            self.draw = ImageDraw.Draw(self.image)
            logger.info(f"Image canvas created with dimensions: {self.matrix.width}x{self.matrix.height}")
            
            # Initialize font with Press Start 2P
            try:
                self.font = ImageFont.truetype("assets/fonts/PressStart2P-Regular.ttf", 8)
                logger.info("Initial Press Start 2P font loaded successfully")
            except Exception as e:
                logger.error(f"Failed to load initial font: {e}")
                self.font = ImageFont.load_default()
            
            # Draw a test pattern unless caller suppressed it (e.g., web on-demand)
            if not getattr(self, '_suppress_test_pattern', False):
                self._draw_test_pattern()
            
        except Exception as e:
            _init_error_str = str(e)
            logger.error(f"Failed to initialize RGB Matrix: {e}", exc_info=True)
            # Create a fallback image for web preview using configured dimensions when available
            self.matrix = None
            try:
                hardware_config = self.config.get('display', {}).get('hardware', {}) if self.config else {}
                rows = int(hardware_config.get('rows', 32))
                cols = int(hardware_config.get('cols', 64))
                chain_length = int(hardware_config.get('chain_length', 2))
                parallel = int(hardware_config.get('parallel', 1))
                fallback_width = max(1, cols * chain_length)
                fallback_height = max(1, rows * parallel)
                # Mirror double-sided in fallback so the preview shows one screen.
                ds_config = self.config.get('display', {}).get('double_sided', {}) if self.config else {}
                ds = _resolve_double_sided(fallback_width, fallback_height, ds_config)
                self._double_sided = ds
                if ds is not None:
                    fallback_width = ds['logical_width']
                    fallback_height = ds['logical_height']
            except Exception:
                fallback_width, fallback_height = 128, 32

            self.image = Image.new('RGB', (fallback_width, fallback_height))
            self.draw = ImageDraw.Draw(self.image)
            # Simple fallback visualization so web UI shows a realistic canvas
            try:
                self.draw.rectangle([0, 0, fallback_width - 1, fallback_height - 1], outline=(255, 0, 0))
                self.draw.line([0, 0, fallback_width - 1, fallback_height - 1], fill=(0, 255, 0))
                self.draw.text((2, max(0, (fallback_height // 2) - 4)), "Simulation", fill=(0, 128, 255))
            except Exception:  # nosec B110 - best-effort fallback visualization; drawing errors must not crash startup
                # Best-effort; ignore drawing errors in fallback
                pass
            logger.error(
                f"Matrix initialization failed — running in fallback/simulation mode "
                f"(size {fallback_width}x{fallback_height}). Error: {e}. "
                "On Raspberry Pi 5: ensure rpi-rgb-led-matrix was built from the latest "
                "submodule (re-run first_time_install.sh). gpio_slowdown of 2–3 is typical for Pi 5 PIO mode."
            )
            # Do not raise here; allow fallback mode so web preview and non-hardware environments work

        # Write hardware status file so the web UI can surface init failures
        _hw_status = {"ok": self.matrix is not None, "error": _init_error_str}
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

    def _draw_test_pattern(self):
        """Draw a test pattern to verify the display is working."""
        try:
            self.clear()
            
            if self.matrix is None:
                # Fallback mode - just draw on the image
                self.draw.rectangle([0, 0, self.image.width-1, self.image.height-1], outline=(255, 0, 0))
                self.draw.line([0, 0, self.image.width-1, self.image.height-1], fill=(0, 255, 0))
                self.draw.text((10, 10), "Simulation", font=self.font, fill=(0, 0, 255))
                logger.info("Drew test pattern in fallback mode")
                return
            
            # Draw a red rectangle border
            self.draw.rectangle([0, 0, self.matrix.width-1, self.matrix.height-1], outline=(255, 0, 0))
            
            # Draw a diagonal line
            self.draw.line([0, 0, self.matrix.width-1, self.matrix.height-1], fill=(0, 255, 0))
            
            # Draw some text - changed from "TEST" to "Initializing" with smaller font
            self.draw.text((10, 10), "Initializing", font=self.font, fill=(0, 0, 255))
            
            # Update the display once after everything is drawn
            self.update_display()
            time.sleep(0.5)  # Reduced from 1 second to 0.5 seconds for faster animation
            
        except Exception as e:
            logger.error(f"Error drawing test pattern: {e}", exc_info=True)

    @contextmanager
    def capture_mode(self):
        """Suppress hardware output during off-screen content capture.

        Plugins call update_display() as part of their normal display() flow.
        When fetching content for Vegas mode the render loop is still running,
        so any incidental hardware write causes a visible flash on the matrix.
        Entering this context prevents those writes without affecting the PIL
        image buffer, which the adapter reads to extract content.
        """
        self._capture_mode_active = True
        try:
            yield
        finally:
            self._capture_mode_active = False

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
            with self._update_lock:
                if self.matrix is None:
                    # Fallback mode - no actual hardware to update
                    logger.debug("Update display called in fallback mode (no hardware)")
                    # Still write a snapshot so the web UI can preview
                    self._write_snapshot_if_due()
                    return

                if self._capture_mode_active:
                    return  # Skip hardware write — content is being captured off-screen

                digest = None
                if self._dirty_tracking_enabled:
                    try:
                        brightness = getattr(self.matrix, 'brightness', None)
                    except AttributeError:
                        brightness = None
                    digest = (zlib.adler32(self.image.tobytes()), brightness)
                    if digest == self._last_pushed_digest:
                        # Nothing changed since the last push — the panel is
                        # already showing exactly this frame.
                        self._write_snapshot_if_due()
                        return

                # Copy the current image to the offscreen canvas. In double-sided
                # mode the logical screen is first tiled across the full chain.
                if self._double_sided is not None:
                    self.offscreen_canvas.SetImage(self._composite_double_sided())
                else:
                    self.offscreen_canvas.SetImage(self.image)

                # Swap buffers immediately
                self.matrix.SwapOnVSync(self.offscreen_canvas)

                # Swap our canvas references
                self.offscreen_canvas, self.current_canvas = self.current_canvas, self.offscreen_canvas

                self._last_pushed_digest = digest

                # Write a snapshot for the web preview (throttled)
                self._write_snapshot_if_due()
        except Exception as e:
            logger.error(f"Error updating display: {e}")

    def clear(self):
        """Clear the display completely."""
        try:
            if self.matrix is None:
                # Fallback mode - just clear the image
                # Explicitly clear old image reference to help garbage collection
                old_image = getattr(self, 'image', None)
                width = old_image.width if old_image else 64
                height = old_image.height if old_image else 64
                if old_image is not None:
                    del old_image
                
                self.image = Image.new('RGB', (width, height))
                self.draw = ImageDraw.Draw(self.image)
                logger.debug("Cleared display in fallback mode")
                return
                
            # Explicitly clear old image reference to help garbage collection
            old_image = getattr(self, 'image', None)
            if old_image is not None:
                del old_image
                
            # Create a new black image
            self.image = Image.new('RGB', (self.matrix.width, self.matrix.height))
            self.draw = ImageDraw.Draw(self.image)

            if not self._capture_mode_active:
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
        """Draw text using BDF font with proper bitmap handling."""
        try:
            # Use the passed font or fall back to calendar_font
            face = font if font else self.calendar_font
            
            # Compute baseline from font ascender so caller can pass top-left y
            try:
                ascender_px = face.size.ascender >> 6
            except Exception:
                ascender_px = 0
            baseline_y = y + ascender_px
            
            for char in text:
                face.load_char(char)
                bitmap = face.glyph.bitmap
                
                # Get glyph metrics
                glyph_left = face.glyph.bitmap_left
                glyph_top = face.glyph.bitmap_top
                
                # Draw the character
                for i in range(bitmap.rows):
                    for j in range(bitmap.width):
                        byte_index = i * bitmap.pitch + (j // 8)
                        if byte_index < len(bitmap.buffer):
                            byte = bitmap.buffer[byte_index]
                            if byte & (1 << (7 - (j % 8))):
                                # Calculate actual pixel position
                                pixel_x = x + glyph_left + j
                                pixel_y = baseline_y - glyph_top + i
                                # Only draw if within bounds
                                if (0 <= pixel_x < self.width and 0 <= pixel_y < self.height):
                                    self.draw.point((pixel_x, pixel_y), fill=color)
                
                # Move to next character
                x += face.glyph.advance.x >> 6
                
        except Exception as e:
            logger.error(f"Error drawing BDF text: {e}", exc_info=True)

    def _load_fonts(self):
        """Load fonts with proper error handling."""
        # Font objects get new id()s after reload, so the text-width cache would
        # return stale measurements keyed on the old ids.  Clear it here.
        self._text_width_cache.clear()
        try:
            # Load Press Start 2P font
            self.regular_font = ImageFont.truetype("assets/fonts/PressStart2P-Regular.ttf", 8)
            logger.info("Press Start 2P font loaded successfully")
            
            # Use the same font for small text (currently same size; adjust size here if needed)
            self.small_font = ImageFont.truetype("assets/fonts/PressStart2P-Regular.ttf", 8)
            logger.info("Press Start 2P small font loaded successfully")

            # Load 5x7 BDF font for calendar events
            try:
                self.calendar_font_path = "assets/fonts/5x7.bdf"
                logger.info(f"Attempting to load 5x7 font from: {self.calendar_font_path}")
                
                if not os.path.exists(self.calendar_font_path):
                    raise FileNotFoundError(f"Font file not found at {self.calendar_font_path}")
                
                # Load with freetype for proper BDF handling
                face = freetype.Face(self.calendar_font_path)
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

            # Load 4x6 font as extra_small_font
            try:
                font_path = "assets/fonts/4x6-font.ttf"
                logger.info(f"Attempting to load 4x6 TTF font from: {font_path} at size 6")
                self.extra_small_font = ImageFont.truetype(font_path, 6)
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
                return font.size.height >> 6
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

    def draw_cloud(self, x: int, y: int, size: int = 16, color=(200, 200, 200)):
        """Draw a cloud icon."""
        # Draw multiple circles to form a cloud shape
        self.draw.ellipse([x+size//4, y+size//3, x+size//4+size//2, y+size//3+size//2], fill=color)
        self.draw.ellipse([x+size//2, y+size//3, x+size//2+size//2, y+size//3+size//2], fill=color)
        self.draw.ellipse([x+size//3, y+size//6, x+size//3+size//2, y+size//6+size//2], fill=color)

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
        if hasattr(self, 'matrix') and self.matrix is not None:
            try:
                self.matrix.Clear()
            except Exception as e:
                logger.warning(f"Error clearing matrix during cleanup: {e}")
        # Ensure image/draw are reset to a blank state
        if hasattr(self, 'image') and hasattr(self, 'draw'):
            try:
                self.image = Image.new('RGB', (self.width, self.height))
                self.draw = ImageDraw.Draw(self.image)
            except (OSError, RuntimeError, ValueError, MemoryError):
                logger.debug("Canvas reset during cleanup failed", exc_info=True)
        # Reset the singleton state when cleaning up
        DisplayManager._instance = None
        DisplayManager._initialized = False

    def format_date_with_ordinal(self, dt):
        """Formats a datetime object into 'Mon Aug 30th' style."""
        day = dt.day
        if 11 <= day <= 13:
            suffix = 'th'
        else:
            suffix = {1: 'st', 2: 'nd', 3: 'rd'}.get(day % 10, 'th')
        
        return dt.strftime(f"%b %-d{suffix}") 

    def set_scrolling_state(self, is_scrolling: bool):
        """Set the current scrolling state. Call this when a display starts/stops scrolling."""
        current_time = time.time()
        self._scrolling_state['is_scrolling'] = is_scrolling
        if is_scrolling:
            self._scrolling_state['last_scroll_activity'] = current_time
        logger.debug(f"Scrolling state set to: {is_scrolling}")

    def is_currently_scrolling(self) -> bool:
        """Check if the display is currently in a scrolling state."""
        current_time = time.time()
        
        # If explicitly not scrolling, return False
        if not self._scrolling_state['is_scrolling']:
            return False
            
        # If we've been inactive for the threshold period, consider it not scrolling
        if current_time - self._scrolling_state['last_scroll_activity'] > self._scrolling_state['scroll_inactivity_threshold']:
            self._scrolling_state['is_scrolling'] = False
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

    def _write_snapshot_if_due(self) -> None:
        """Mirror the current frame to the preview snapshot when the policy
        says it's worth it — see src/common/snapshot_policy.py. Unchanged
        frames are never re-encoded; without viewers the cadence drops to
        the idle keepalive."""
        try:
            now = time.time()
            viewer_fresh = self._viewer_is_fresh(now)
            if viewer_fresh and not self._viewer_was_fresh:
                # A preview just opened: let the next changed frame through
                # immediately instead of waiting out the idle interval.
                self._last_snapshot_ts = 0.0
            self._viewer_was_fresh = viewer_fresh

            digest = zlib.adler32(self.image.tobytes())
            action = snapshot_policy.decide(
                now, self._last_snapshot_ts, self._last_snapshot_touch_ts,
                viewer_fresh, digest != self._last_snapshot_digest)
            if action is snapshot_policy.SnapshotAction.SKIP:
                return
            if action is snapshot_policy.SnapshotAction.TOUCH:
                # mtime bump only: keeps the health check (snapshot age)
                # green without paying for a PNG encode of an unchanged frame
                os.utime(self._snapshot_path, None)
                self._last_snapshot_touch_ts = now
                return

            # WRITE: ensure directory permissions once, not per frame
            snapshot_path_obj = Path(self._snapshot_path)
            if not self._snapshot_dir_prepared:
                # Never modify /tmp permissions - it has special system
                # permissions (1777) that must not be changed or it breaks
                # apt and other system tools
                parent_dir = snapshot_path_obj.parent
                if parent_dir and str(parent_dir) != '/tmp':  # nosec B108 - guard to skip /tmp for permission ops
                    ensure_directory_permissions(parent_dir, get_assets_dir_mode())
                self._snapshot_dir_prepared = True
            # Write atomically: temp then replace
            tmp_path = f"{self._snapshot_path}.tmp"
            self.image.save(tmp_path, format='PNG')
            try:
                os.replace(tmp_path, self._snapshot_path)
            except Exception:
                # Fallback to direct save if replace not supported
                self.image.save(self._snapshot_path, format='PNG')
            # Set proper file permissions after saving
            try:
                ensure_file_permissions(snapshot_path_obj, get_assets_file_mode())
            except Exception:
                pass
            self._last_snapshot_ts = now
            self._last_snapshot_touch_ts = now
            self._last_snapshot_digest = digest
        except Exception as e:
            # Snapshot failures must never break display — but they must not
            # be silent either: the snapshot's mtime is the web UI's display
            # mirror AND its hardware-liveness proxy, so a quietly failing
            # write freezes the mirror and makes health checks lie (seen in
            # the field: a stale root-owned /tmp file froze it for a day).
            # Warn at most once per 5 minutes to avoid log spam.
            if (now - self._snapshot_fail_log_ts) > 300:
                self._snapshot_fail_log_ts = now
                logger.warning("Snapshot write failing (web preview/health "
                               "mirror is stale): %s", e)
            else:
                logger.debug(f"Snapshot write skipped: {e}")