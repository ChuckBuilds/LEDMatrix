"""
Multi-Display Sync Manager

Synchronizes scrolling content across two LED matrix display units over UDP.
Runs at the core framework level — works with any plugin automatically.

Roles:
  standalone  No sync (default behavior)
  leader      Drives scroll, sends rendered follower frames via UDP
  follower    Receives frames from leader; falls back to own plugins when
              the leader goes offline

Compatibility rule: rows and cols must match between leader and follower.
chain_length may differ — each display can have a different number of panels.

Port default: 5765 (UDP). Open this port on both Pis if ufw is active:
  sudo ufw allow 5765/udp
"""

import io
import json
import math
import os
import socket
import struct
import tempfile
import threading
import time
import logging
from enum import Enum
from typing import Callable, Optional
from PIL import Image

from src.config_manager_atomic import _replace
from src.display_geometry import DEFAULT_CHAIN_LENGTH, DEFAULT_COLS, DEFAULT_ROWS

# Raw-frame wire format: 8-byte magic + 4-byte header + raw RGB pixels
# Much faster than PNG: no encode/decode, negligible CPU, same UDP packet size
_RAW_MAGIC = b'SYNC_RAW'
_RAW_HEADER = struct.Struct('<HH')  # width, height (uint16 LE)


# Upper bound on a decoded frame/scroll image. Generous for any real scroll
# image (a leader's full cycle is long but only panel-height tall), and low
# enough that a crafted image from any host on the LAN cannot force a large
# allocation on the render thread. Applied on both receive paths — the TCP
# image server and the follower's legacy-PNG UDP fallback.
_MAX_FRAME_W, _MAX_FRAME_H = 100_000, 256

SYNC_PORT = 5765
HELLO_INTERVAL = 5.0       # follower broadcasts hello every 5 s
HEARTBEAT_INTERVAL = 2.0   # follower sends heartbeat every 2 s
PEER_TIMEOUT = 6.0         # leader: no heartbeat → follower gone
LEADER_TIMEOUT = 6.0       # follower: no frame → leader gone
STATUS_FILE = os.path.join(tempfile.gettempdir(), "led_matrix_sync_status.json")
# Serialises writes to STATUS_FILE (several threads report status) against
# its removal in stop(), so a write already under way cannot put the file back
# after the display process has shut down.
_STATUS_LOCK = threading.Lock()


def _remove_status_file() -> None:
    try:
        os.remove(STATUS_FILE)
    except FileNotFoundError:
        pass


class SyncRole(Enum):
    STANDALONE = "standalone"
    LEADER = "leader"
    FOLLOWER = "follower"


class LeaderState(Enum):
    NO_PEER = "no_peer"
    CONNECTED = "connected"
    INCOMPATIBLE = "incompatible"


class FollowerState(Enum):
    STANDALONE = "standalone"
    FOLLOWER = "follower"


class DisplaySyncManager:
    """
    Core sync manager.  Instantiated by DisplayController based on config['sync'].

    The leader sends each rendered frame to the follower over UDP as raw RGB
    bytes (send_frame), and for Vegas scrolling sends the whole scroll image
    once per cycle as a PNG over TCP on port + 1 (send_scroll_image), then
    only the scroll position. The follower draws what it receives and goes
    back to its own plugins when the leader stops sending.
    """

    # Set by stop(); status writes after that are dropped. Class-level so
    # instances built without __init__ (tests) have it too.
    _status_closed = False

    def __init__(
        self,
        role_str: str,
        cfg: dict,
        hw_config: dict,
        logger: logging.Logger,
    ) -> None:
        """
        Args:
            role_str:   "standalone" | "leader" | "follower"
            cfg:        config['sync'] dict
            hw_config:  config['display']['hardware'] dict (this Pi's own config)
            logger:     framework logger
        """
        try:
            self.role = SyncRole(role_str)
        except ValueError:
            logger.warning("Invalid sync role '%s', defaulting to standalone", role_str)
            self.role = SyncRole.STANDALONE

        self.logger = logger
        self.port = int(cfg.get("port", SYNC_PORT))
        self._hw_config = hw_config

        # Leader state
        self._leader_state = LeaderState.NO_PEER
        self._peer_ip: Optional[str] = None
        self._peer_compatible: bool = False
        self._peer_chain: int = 0
        # time.monotonic() readings, like _last_leader_frame_time: these only
        # feed the timeout watchdogs, and a wall-clock step (NTP correcting a
        # Pi with no RTC) would otherwise fake or mask a timeout.
        self._last_heartbeat_time: float = 0.0
        self._leader_width: int = 0  # set by display_controller after init
        self._oversized_frame_warned: bool = False

        # Follower state
        self._follower_state = FollowerState.STANDALONE
        self._latest_frame: Optional[Image.Image] = None  # pixel-frame fallback
        self._latest_scroll_x: Optional[float] = None    # Vegas scroll position
        self._last_leader_frame_time: float = 0.0
        self._frame_lock = threading.Lock()
        self._leader_ip: Optional[str] = None
        self._on_new_cycle: Optional[Callable[[], None]] = None       # called when leader starts new cycle
        self._on_scroll_image: Optional[Callable[[Image.Image], None]] = None   # called with Image when received
        self._pending_scroll_image: Optional[Image.Image] = None  # image received before callback set
        self._scroll_image_lock = threading.Lock()         # guards _on_scroll_image / _pending_scroll_image
        self._img_server_sock = None                        # TCP server for scroll image transfer

        # Leader state additions
        self._on_follower_connected: Optional[Callable[[], None]] = None  # called when follower connects

        self._error_message: Optional[str] = None
        self._running = False
        self._recv_sock: Optional[socket.socket] = None
        self._send_sock: Optional[socket.socket] = None

        if self.role == SyncRole.STANDALONE:
            # Standalone never writes a status file, so one still here is
            # from an earlier run as leader or follower. The web UI would
            # keep reporting that run's peer as if it were live.
            try:
                with _STATUS_LOCK:
                    _remove_status_file()
            except OSError as exc:
                logger.debug("Sync: could not remove stale status file: %s", exc)
            return

        if self.role == SyncRole.LEADER:
            self._start_leader()
        elif self.role == SyncRole.FOLLOWER:
            self._start_follower()

    # ------------------------------------------------------------------ #
    # Leader setup                                                         #
    # ------------------------------------------------------------------ #

    def _start_leader(self) -> None:
        # Receive socket: listens for hello + heartbeat from follower
        self._recv_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)  # nosec B104
        self._recv_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._recv_sock.bind(("", self.port))  # nosec B104 — intentional: must receive UDP broadcast on all interfaces
        self._recv_sock.settimeout(1.0)

        # Send socket: unicast frames + hello_ack to follower
        self._send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        self._running = True
        threading.Thread(
            target=self._leader_recv_loop, daemon=True, name="sync-leader-recv"
        ).start()
        threading.Thread(
            target=self._leader_watchdog, daemon=True, name="sync-leader-watchdog"
        ).start()
        self.logger.info("Sync: leader started on UDP port %d", self.port)
        self.write_status_file()

    def _leader_recv_loop(self) -> None:
        while self._running:
            try:
                data, addr = self._recv_sock.recvfrom(1024)
                sender_ip = addr[0]
                try:
                    msg = json.loads(data.decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                t = msg.get("t")
                if t == "hello":
                    self._handle_hello(msg, sender_ip)
                elif t == "hb":
                    if self._peer_ip == sender_ip:
                        self._last_heartbeat_time = time.monotonic()
            except socket.timeout:
                continue
            except Exception as exc:
                self.logger.debug("Sync leader recv error: %s", exc)
                # Brief backoff: a socket left in a bad state raises
                # immediately, which would otherwise spin this thread at
                # 100% CPU logging the same error.
                time.sleep(0.1)

    def _handle_hello(self, msg: dict, sender_ip: str) -> None:
        hw = self._hw_config
        local_rows = hw.get("rows", DEFAULT_ROWS)
        local_cols = hw.get("cols", DEFAULT_COLS)
        peer_rows = int(msg.get("rows", 0))
        peer_cols = int(msg.get("cols", 0))
        peer_chain = int(msg.get("chain", DEFAULT_CHAIN_LENGTH))

        compatible = peer_rows == local_rows and peer_cols == local_cols

        self._peer_ip = sender_ip
        self._peer_compatible = compatible
        self._peer_chain = peer_chain
        self._last_heartbeat_time = time.monotonic()

        prev_state = self._leader_state
        if compatible:
            if prev_state != LeaderState.CONNECTED:
                self.logger.info(
                    "Sync: follower connected at %s (chain=%d)", sender_ip, peer_chain
                )
            self._leader_state = LeaderState.CONNECTED
            self._error_message = None
            # Send scroll image immediately on new connection so follower has identical content
            if prev_state != LeaderState.CONNECTED and self._on_follower_connected:
                threading.Thread(
                    target=self._on_follower_connected,
                    daemon=True, name="sync-leader-img-push"
                ).start()
        else:
            self._leader_state = LeaderState.INCOMPATIBLE
            self._error_message = (
                f"Incompatible panels: follower is {peer_cols}x{peer_rows}, "
                f"leader is {local_cols}x{local_rows}. "
                f"rows and cols must match between displays."
            )
            if prev_state != LeaderState.INCOMPATIBLE:
                self.logger.error("Sync: %s", self._error_message)

        if self._leader_state != prev_state:
            self.write_status_file()

        ack = json.dumps({
            "t": "hello_ack",
            "compatible": compatible,
            "leader_width": self._leader_width,
            "error": self._error_message,
        }).encode("utf-8")
        try:
            self._send_sock.sendto(ack, (sender_ip, self.port))
        except Exception as exc:
            self.logger.debug("Sync: hello_ack send failed: %s", exc)

    def _leader_watchdog(self) -> None:
        while self._running:
            time.sleep(1.0)
            if self._leader_state == LeaderState.CONNECTED:
                if time.monotonic() - self._last_heartbeat_time > PEER_TIMEOUT:
                    self.logger.info(
                        "Sync: follower heartbeat timeout — peer disconnected"
                    )
                    self._leader_state = LeaderState.NO_PEER
                    self._peer_ip = None
                    self._peer_compatible = False
                    self.write_status_file()

    def _image_server_loop(self) -> None:
        """Follower: TCP server that receives the leader's scroll image at each new cycle."""
        while self._running:
            try:
                conn, addr = self._img_server_sock.accept()
                conn.settimeout(10.0)
                try:
                    # 4-byte big-endian length prefix
                    hdr = b""
                    while len(hdr) < 4:
                        chunk = conn.recv(4 - len(hdr))
                        if not chunk:
                            break
                        hdr += chunk
                    if len(hdr) < 4:
                        continue
                    length = int.from_bytes(hdr, "big")
                    _MAX_IMAGE_BYTES = 10 * 1024 * 1024  # 10 MB — well above any real scroll image
                    if length <= 0 or length > _MAX_IMAGE_BYTES:
                        self.logger.warning(
                            "Sync: rejected TCP image with invalid length %d (max %d) from %s",
                            length, _MAX_IMAGE_BYTES, addr,
                        )
                        conn.close()
                        continue
                    data = bytearray()
                    while len(data) < length:
                        chunk = conn.recv(min(65536, length - len(data)))
                        if not chunk:
                            break
                        data.extend(chunk)
                    img = Image.open(io.BytesIO(data))
                    if img.width > _MAX_FRAME_W or img.height > _MAX_FRAME_H:
                        self.logger.warning(
                            "Sync: rejected oversized scroll image %dx%d (max %dx%d) from %s",
                            img.width, img.height, _MAX_FRAME_W, _MAX_FRAME_H, addr,
                        )
                        continue
                    try:
                        img.load()
                    except (Image.DecompressionBombError, ValueError) as exc:
                        self.logger.warning("Sync: rejected decompression bomb from %s: %s", addr, exc)
                        continue
                    self.logger.info(
                        "Sync: received scroll image %dx%d (%d bytes compressed)",
                        img.width, img.height, length,
                    )
                    with self._scroll_image_lock:
                        if self._on_scroll_image:
                            cb = self._on_scroll_image
                        else:
                            # Callback not registered yet (startup race) — cache it
                            self._pending_scroll_image = img
                            cb = None
                    if cb:
                        cb(img)
                finally:
                    conn.close()
            except socket.timeout:
                continue
            except Exception as exc:
                self.logger.debug("Sync: image server error: %s", exc)

    def send_scroll_image(self, image: Image.Image) -> None:
        """Leader: send the full scroll image to the follower via TCP.
        PNG compression typically reduces a 5000×32 image to ~20–50KB,
        transferring in <20ms on local WiFi. Called at new_cycle and on
        first connection so both Pis always have identical cached_arrays.
        """
        if self.role != SyncRole.LEADER:
            return
        if self._leader_state != LeaderState.CONNECTED or not self._peer_ip:
            return
        try:
            buf = io.BytesIO()
            image.save(buf, format="PNG", optimize=True)
            data = buf.getvalue()
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(5.0)
                sock.connect((self._peer_ip, self.port + 1))
                sock.sendall(len(data).to_bytes(4, "big") + data)
            self.logger.info(
                "Sync: sent scroll image %dx%d (%d bytes compressed)",
                image.width, image.height, len(data),
            )
        except Exception as exc:
            self.logger.debug("Sync: image send error: %s", exc)

    def set_on_follower_connected(self, callback: Callable[[], None]) -> None:
        """Leader: callback fired (in a thread) when a compatible follower first connects.
        Use this to push the current scroll image immediately.
        If a follower is already connected when this is called, fires right away
        (handles the race where follower connects during leader startup).
        """
        self._on_follower_connected = callback
        if self._leader_state == LeaderState.CONNECTED:
            threading.Thread(
                target=callback, daemon=True, name="sync-leader-img-push-late"
            ).start()

    def set_on_scroll_image(self, callback: Callable[[Image.Image], None]) -> None:
        """Follower: callback fired with the received Image when leader sends scroll image.
        If an image was received before this callback was registered (startup race),
        fires immediately with that cached image.
        """
        with self._scroll_image_lock:
            self._on_scroll_image = callback
            pending = self._pending_scroll_image
            self._pending_scroll_image = None
        if pending is not None:
            callback(pending)

    def send_scroll_x(self, scroll_x: float) -> None:
        """Leader (Vegas mode): broadcast scroll position instead of a pixel frame.
        The follower renders from its own local pipeline at scroll_x - display_width.
        ~20 bytes vs ~18KB for raw frames — eliminates all content-change artifacts.
        """
        if self.role != SyncRole.LEADER:
            return
        if self._leader_state != LeaderState.CONNECTED or not self._peer_ip:
            return
        try:
            msg = json.dumps({"t": "sx", "x": round(scroll_x, 2)}).encode("utf-8")
            self._send_sock.sendto(msg, (self._peer_ip, self.port))
        except Exception as exc:
            self.logger.debug("Sync: scroll_x send error: %s", exc)

    def send_new_cycle(self) -> None:
        """Leader: signal that a new scroll cycle has started so follower rebuilds its image."""
        if self.role != SyncRole.LEADER:
            return
        if self._leader_state != LeaderState.CONNECTED or not self._peer_ip:
            return
        try:
            self._send_sock.sendto(b'{"t":"nc"}', (self._peer_ip, self.port))
        except Exception as exc:
            self.logger.debug("Sync: new_cycle send error: %s", exc)

    def send_frame(self, image: Image.Image) -> None:
        """Leader: send a rendered frame to the follower as raw RGB bytes.
        Raw format is orders of magnitude faster than PNG on Pi hardware —
        no encode on sender, no decode on receiver.
        Packet: 8-byte magic + 4-byte (width, height) header + raw RGB bytes.
        """
        if self.role != SyncRole.LEADER:
            return
        if self._leader_state != LeaderState.CONNECTED or not self._peer_ip:
            return
        # numpy is imported here, not at module level: the web interface
        # imports this module for its constants (STATUS_FILE, SYNC_PORT) and
        # would otherwise load numpy for nothing. Only a connected leader
        # gets this far, and after the first frame the import is a
        # sys.modules lookup.
        import numpy as np
        try:
            arr = np.asarray(image.convert("RGB"), dtype=np.uint8)
            header = _RAW_MAGIC + _RAW_HEADER.pack(image.width, image.height)
            data = header + arr.tobytes()
            if len(data) <= 65000:
                self._send_sock.sendto(data, (self._peer_ip, self.port))
            elif not self._oversized_frame_warned:
                self._oversized_frame_warned = True
                self.logger.warning(
                    "Sync: frame too large for UDP (%d bytes, max 65000) — "
                    "image %dx%d will not be sent; use TCP image sync instead",
                    len(data), image.width, image.height,
                )
        except Exception as exc:
            self.logger.debug("Sync: frame send error: %s", exc)

    def set_leader_width(self, width: int) -> None:
        """Called by DisplayController once display_manager.width is known."""
        self._leader_width = width

    # ------------------------------------------------------------------ #
    # Follower setup                                                       #
    # ------------------------------------------------------------------ #

    def _start_follower(self) -> None:
        # Receive socket: listens for frames + hello_ack from leader
        self._recv_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._recv_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._recv_sock.bind(("", self.port))  # nosec B104 — intentional: must receive UDP broadcast on all interfaces
        self._recv_sock.settimeout(0.1)

        # Send socket: broadcasts hello + heartbeat
        self._send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._send_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

        self._running = True
        threading.Thread(
            target=self._follower_recv_loop, daemon=True, name="sync-follower-recv"
        ).start()
        threading.Thread(
            target=self._follower_announce_loop, daemon=True, name="sync-follower-announce"
        ).start()
        threading.Thread(
            target=self._follower_watchdog, daemon=True, name="sync-follower-watchdog"
        ).start()
        # TCP server: receives scroll images from leader (port + 1)
        self._img_server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)  # nosec B104
        self._img_server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._img_server_sock.bind(("", self.port + 1))  # nosec B104 — intentional: TCP server must accept connections on all interfaces
        self._img_server_sock.listen(1)
        self._img_server_sock.settimeout(1.0)
        threading.Thread(
            target=self._image_server_loop, daemon=True, name="sync-image-server"
        ).start()

        self.logger.info(
            "Sync: follower started on UDP port %d, image server on TCP %d",
            self.port, self.port + 1,
        )
        self.write_status_file()

    def _handle_received_frame(self, img: Image.Image, sender_ip: str) -> None:
        """Record a decoded leader frame and enter follower mode if needed."""
        with self._frame_lock:
            self._latest_frame = img
        self._enter_follower_mode(sender_ip)

    def _enter_follower_mode(self, sender_ip: str) -> bool:
        """Note that the leader at ``sender_ip`` just sent something, and
        switch from standalone to follower mode if not already following.
        Returns True if this call made the switch."""
        self._last_leader_frame_time = time.monotonic()
        self._leader_ip = sender_ip
        if self._follower_state != FollowerState.STANDALONE:
            return False
        self._follower_state = FollowerState.FOLLOWER
        self.logger.info(
            "Sync: leader active at %s — switching to follower mode",
            sender_ip,
        )
        self.write_status_file()
        return True

    def _follower_recv_loop(self) -> None:
        while self._running:
            try:
                data, addr = self._recv_sock.recvfrom(65535)
                sender_ip = addr[0]

                if data[:8] == _RAW_MAGIC:
                    # Magic-tagged raw RGB frame — self-describing, no guessing.
                    try:
                        w, h = _RAW_HEADER.unpack(data[8:12])
                        raw = data[12:]
                        img = Image.frombuffer(
                            "RGB", (w, h), raw, "raw", "RGB", 0, 1
                        )
                        self._handle_received_frame(img, sender_ip)
                    except Exception as exc:
                        self.logger.debug("Sync: frame decode error: %s", exc)
                else:
                    # No magic prefix. Whether the payload parses as JSON
                    # decides between a control message and a legacy
                    # (pre-magic) PNG frame — both wire formats are
                    # self-describing, so no size heuristic is needed. A
                    # >512-byte control message used to be misrouted into
                    # image decode and silently dropped.
                    try:
                        msg = json.loads(data.decode("utf-8"))
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        # Not JSON — try a legacy PNG frame.
                        try:
                            img = Image.open(io.BytesIO(data))
                            if img.width > _MAX_FRAME_W or img.height > _MAX_FRAME_H:
                                # Same cap the TCP image path applies: decode
                                # is deferred until load(), so check first.
                                self.logger.debug(
                                    "Sync: rejected oversized legacy frame %dx%d from %s",
                                    img.width, img.height, sender_ip,
                                )
                                continue
                            img.load()
                            self._handle_received_frame(img, sender_ip)
                        except Exception as exc:
                            self.logger.debug("Sync: frame decode error: %s", exc)
                        continue

                    # It parsed, so it is a control message and never a
                    # frame. Read and validate its fields under a guard —
                    # a UDP payload is attacker-shaped, so a non-object
                    # body makes .get() raise AttributeError and an "sx"
                    # carrying a non-numeric x raises ValueError/TypeError
                    # — but dispatch the callback *outside* it. Running
                    # the callback in here would let a fault in someone
                    # else's code read as a malformed packet and be
                    # logged as one.
                    fire_new_cycle = False
                    try:
                        t = msg.get("t")
                        if t == "hello_ack":
                            self._leader_ip = sender_ip
                            self._peer_compatible = msg.get("compatible", False)
                            self._error_message = msg.get("error")
                            if not self._peer_compatible and self._error_message:
                                self.logger.error(
                                    "Sync: leader rejected handshake — %s",
                                    self._error_message,
                                )
                            self.write_status_file()
                        elif t == "sx":
                            # Vegas scroll-position sync — tiny message, renders locally
                            scroll_x = float(msg["x"])
                            if not math.isfinite(scroll_x):
                                # json.loads accepts the NaN/Infinity literals,
                                # and float("nan") accepts the strings, so a
                                # non-finite x reaches here intact. Left alone
                                # it poisons every offset computed from it —
                                # NaN comparisons are all false, so the
                                # follower renders a frame it can never scroll
                                # back from. Treat it as malformed.
                                raise ValueError(f"non-finite scroll x: {msg['x']!r}")
                            self._latest_scroll_x = scroll_x
                            if self._enter_follower_mode(sender_ip):
                                fire_new_cycle = True  # build initial scroll image
                        elif t == "nc":
                            # Leader started a new scroll cycle — rebuild local image
                            fire_new_cycle = True
                    except (KeyError, AttributeError, TypeError, ValueError) as exc:
                        self.logger.debug("Sync: malformed control message: %s", exc)
                        continue

                    if fire_new_cycle and self._on_new_cycle:
                        self._on_new_cycle()

            except socket.timeout:
                continue
            except Exception as exc:
                self.logger.debug("Sync follower recv error: %s", exc)
                time.sleep(0.1)

    def _follower_announce_loop(self) -> None:
        hw = self._hw_config
        hello = json.dumps({
            "t": "hello",
            "rows": hw.get("rows", DEFAULT_ROWS),
            "cols": hw.get("cols", DEFAULT_COLS),
            "chain": hw.get("chain_length", DEFAULT_CHAIN_LENGTH),
        }).encode("utf-8")
        heartbeat = json.dumps({"t": "hb"}).encode("utf-8")
        dest = ("<broadcast>", self.port)

        # -inf, not 0.0: monotonic time starts near boot, so "now - 0.0" can
        # be under the interval and would delay the first announcement.
        last_hello = float("-inf")
        last_hb = float("-inf")

        while self._running:
            now = time.monotonic()
            if now - last_hello >= HELLO_INTERVAL:
                try:
                    self._send_sock.sendto(hello, dest)
                    last_hello = now
                except Exception as exc:
                    self.logger.debug("Sync: hello broadcast error: %s", exc)
            if now - last_hb >= HEARTBEAT_INTERVAL:
                try:
                    self._send_sock.sendto(heartbeat, dest)
                    last_hb = now
                except Exception as exc:
                    self.logger.debug("Sync: heartbeat error: %s", exc)
            time.sleep(0.5)

    def _follower_watchdog(self) -> None:
        while self._running:
            time.sleep(1.0)
            if self._follower_state == FollowerState.FOLLOWER:
                if time.monotonic() - self._last_leader_frame_time > LEADER_TIMEOUT:
                    self.logger.info(
                        "Sync: leader frame timeout — returning to standalone mode"
                    )
                    self._follower_state = FollowerState.STANDALONE
                    with self._frame_lock:
                        self._latest_frame = None
                    self.write_status_file()

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def is_follower_active(self) -> bool:
        """True when this Pi is in active follower mode (receiving frames)."""
        return (
            self.role == SyncRole.FOLLOWER
            and self._follower_state == FollowerState.FOLLOWER
        )

    def get_latest_scroll_x(self) -> Optional[float]:
        """Follower: return the most recently received Vegas scroll position, or None."""
        return self._latest_scroll_x

    def set_on_new_cycle(self, callback: Callable[[], None]) -> None:
        """Follower: register a callback fired when the leader starts a new scroll cycle.

        Nothing in core registers one: display_controller follows the leader
        through set_on_scroll_image() and the scroll position instead of
        rebuilding locally. The hook stays for callers that want the signal.
        """
        self._on_new_cycle = callback

    def get_latest_frame(self) -> Optional[Image.Image]:
        """Follower: return the most recently received pixel frame (non-Vegas fallback)."""
        with self._frame_lock:
            return self._latest_frame

    def get_status(self) -> dict:
        """Return sync state dict for the web API status endpoint."""
        hw = self._hw_config
        base = {
            "role": self.role.value,
            "port": self.port,
            "local_rows": hw.get("rows", DEFAULT_ROWS),
            "local_cols": hw.get("cols", DEFAULT_COLS),
            "local_chain": hw.get("chain_length", DEFAULT_CHAIN_LENGTH),
        }

        if self.role == SyncRole.STANDALONE:
            return {**base, "state": "standalone"}

        if self.role == SyncRole.LEADER:
            return {
                **base,
                "state": self._leader_state.value,
                "peer_ip": self._peer_ip,
                "peer_compatible": self._peer_compatible,
                "peer_chain": self._peer_chain,
                "leader_width": self._leader_width,
                "error": self._error_message,
            }

        # Follower
        return {
            **base,
            "state": self._follower_state.value,
            "leader_ip": self._leader_ip,
            "peer_compatible": self._peer_compatible,
            "error": self._error_message,
        }

    def write_status_file(self) -> None:
        """Write current sync status to STATUS_FILE for the web UI to read."""
        tmp = None
        try:
            status = self.get_status()
            status["ts"] = time.time()
            with _STATUS_LOCK:
                if self._status_closed:
                    return
                # A unique temp name per write, like frame_timing's stats
                # file: the receive loop, watchdog and hello handler all
                # write, and with one fixed ".tmp" name one thread's
                # os.replace() could move the other's half-written file.
                fd, tmp = tempfile.mkstemp(
                    dir=os.path.dirname(STATUS_FILE) or ".",
                    prefix=".led_matrix_sync_status.", suffix=".tmp")
                with os.fdopen(fd, "w") as f:
                    json.dump(status, f)
                # mkstemp makes it owner-only; the web UI may run as a
                # different user from the display service.
                os.chmod(tmp, 0o644)
                # _replace: on Windows a rename can briefly fail with
                # "Access is denied" while a scanner holds the target open.
                _replace(tmp, STATUS_FILE)
                tmp = None
        except Exception as exc:
            self.logger.debug("Sync: status file write error: %s", exc)
        finally:
            if tmp is not None:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass

    def stop(self) -> None:
        """Shut down threads, close sockets and withdraw the status file."""
        self._running = False
        for sock in (self._recv_sock, self._send_sock, self._img_server_sock):
            if sock:
                try:
                    sock.close()
                except Exception as exc:
                    self.logger.debug("Sync: error closing socket: %s", exc)
        # The web UI reads this file as live status. Left behind, it went on
        # reporting a connected peer after the display service had stopped.
        try:
            with _STATUS_LOCK:
                self._status_closed = True
                _remove_status_file()
        except OSError as exc:
            self.logger.debug("Sync: could not remove status file: %s", exc)
