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
import os
import socket
import struct
import threading
import time
import logging
from enum import Enum
from typing import Optional
import numpy as np
from PIL import Image

# Raw-frame wire format: 8-byte magic + 4-byte header + raw RGB pixels
# Much faster than PNG: no encode/decode, negligible CPU, same UDP packet size
_RAW_MAGIC = b'SYNC_RAW'
_RAW_HEADER = struct.Struct('<HH')  # width, height (uint16 LE)


SYNC_PORT = 5765
HELLO_INTERVAL = 5.0       # follower broadcasts hello every 5 s
HEARTBEAT_INTERVAL = 2.0   # follower sends heartbeat every 2 s
PEER_TIMEOUT = 6.0         # leader: no heartbeat → follower gone
LEADER_TIMEOUT = 6.0       # follower: no frame → leader gone
STATUS_FILE = "/tmp/led_matrix_sync_status.json"


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
    Leader sends compressed PNG frames to the follower after each render cycle.
    Follower renders received frames; returns to own plugin stack when leader
    goes offline.
    """

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
        self._last_heartbeat_time: float = 0.0
        self._leader_width: int = 0  # set by display_controller after init

        # Follower state
        self._follower_state = FollowerState.STANDALONE
        self._latest_frame: Optional[Image.Image] = None  # pixel-frame fallback
        self._latest_scroll_x: Optional[float] = None    # Vegas scroll position
        self._last_leader_frame_time: float = 0.0
        self._frame_lock = threading.Lock()
        self._leader_ip: Optional[str] = None
        self._on_new_cycle: Optional[callable] = None       # called when leader starts new cycle
        self._on_scroll_image: Optional[callable] = None   # called with Image when received
        self._pending_scroll_image: Optional[Image.Image] = None  # image received before callback set
        self._img_server_sock = None                        # TCP server for scroll image transfer

        # Leader state additions
        self._on_follower_connected: Optional[callable] = None  # called when follower connects

        self._error_message: Optional[str] = None
        self._running = False
        self._recv_sock: Optional[socket.socket] = None
        self._send_sock: Optional[socket.socket] = None

        if self.role == SyncRole.STANDALONE:
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
        self._recv_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._recv_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._recv_sock.bind(("", self.port))
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
                        self._last_heartbeat_time = time.time()
            except socket.timeout:
                continue
            except Exception as exc:
                self.logger.debug("Sync leader recv error: %s", exc)

    def _handle_hello(self, msg: dict, sender_ip: str) -> None:
        hw = self._hw_config
        local_rows = hw.get("rows", 32)
        local_cols = hw.get("cols", 64)
        peer_rows = int(msg.get("rows", 0))
        peer_cols = int(msg.get("cols", 0))
        peer_chain = int(msg.get("chain", 1))

        compatible = peer_rows == local_rows and peer_cols == local_cols

        self._peer_ip = sender_ip
        self._peer_compatible = compatible
        self._peer_chain = peer_chain
        self._last_heartbeat_time = time.time()

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
                if time.time() - self._last_heartbeat_time > PEER_TIMEOUT:
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
                    data = b""
                    while len(data) < length:
                        chunk = conn.recv(min(65536, length - len(data)))
                        if not chunk:
                            break
                        data += chunk
                    img = Image.open(io.BytesIO(data))
                    img.load()
                    self.logger.info(
                        "Sync: received scroll image %dx%d (%d bytes compressed)",
                        img.width, img.height, length,
                    )
                    if self._on_scroll_image:
                        self._on_scroll_image(img)
                    else:
                        # Callback not registered yet (startup race) — cache it
                        self._pending_scroll_image = img
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
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5.0)
            sock.connect((self._peer_ip, self.port + 1))
            sock.sendall(len(data).to_bytes(4, "big") + data)
            sock.close()
            self.logger.info(
                "Sync: sent scroll image %dx%d (%d bytes compressed)",
                image.width, image.height, len(data),
            )
        except Exception as exc:
            self.logger.debug("Sync: image send error: %s", exc)

    def set_on_follower_connected(self, callback) -> None:
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

    def set_on_scroll_image(self, callback) -> None:
        """Follower: callback fired with the received Image when leader sends scroll image.
        If an image was received before this callback was registered (startup race),
        fires immediately with that cached image.
        """
        self._on_scroll_image = callback
        if self._pending_scroll_image is not None:
            callback(self._pending_scroll_image)
            self._pending_scroll_image = None

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
        try:
            arr = np.asarray(image.convert("RGB"), dtype=np.uint8)
            header = _RAW_MAGIC + _RAW_HEADER.pack(image.width, image.height)
            data = header + arr.tobytes()
            if len(data) <= 65000:
                self._send_sock.sendto(data, (self._peer_ip, self.port))
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
        self._recv_sock.bind(("", self.port))
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
        self._img_server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._img_server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._img_server_sock.bind(("", self.port + 1))
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

    def _follower_recv_loop(self) -> None:
        while self._running:
            try:
                data, addr = self._recv_sock.recvfrom(65535)
                sender_ip = addr[0]

                if len(data) > 512:
                    # Raw RGB frame: magic(8) + width/height(4) + pixels
                    try:
                        if data[:8] == _RAW_MAGIC:
                            w, h = _RAW_HEADER.unpack(data[8:12])
                            raw = data[12:]
                            img = Image.frombuffer(
                                "RGB", (w, h), raw, "raw", "RGB", 0, 1
                            )
                        else:
                            # Fallback: try legacy PNG
                            img = Image.open(io.BytesIO(data))
                            img.load()
                        with self._frame_lock:
                            self._latest_frame = img
                        self._last_leader_frame_time = time.time()
                        self._leader_ip = sender_ip

                        if self._follower_state == FollowerState.STANDALONE:
                            self._follower_state = FollowerState.FOLLOWER
                            self.logger.info(
                                "Sync: leader active at %s — switching to follower mode",
                                sender_ip,
                            )
                            self.write_status_file()
                    except Exception as exc:
                        self.logger.debug("Sync: frame decode error: %s", exc)
                else:
                    # Control message
                    try:
                        msg = json.loads(data.decode("utf-8"))
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
                            self._latest_scroll_x = float(msg["x"])
                            self._last_leader_frame_time = time.time()
                            self._leader_ip = sender_ip
                            if self._follower_state == FollowerState.STANDALONE:
                                self._follower_state = FollowerState.FOLLOWER
                                self.logger.info(
                                    "Sync: leader active at %s — switching to follower mode",
                                    sender_ip,
                                )
                                self.write_status_file()
                                if self._on_new_cycle:
                                    self._on_new_cycle()  # build initial scroll image
                        elif t == "nc":
                            # Leader started a new scroll cycle — rebuild local image
                            if self._on_new_cycle:
                                self._on_new_cycle()
                    except (json.JSONDecodeError, UnicodeDecodeError, KeyError):
                        pass

            except socket.timeout:
                continue
            except Exception as exc:
                self.logger.debug("Sync follower recv error: %s", exc)

    def _follower_announce_loop(self) -> None:
        hw = self._hw_config
        hello = json.dumps({
            "t": "hello",
            "rows": hw.get("rows", 32),
            "cols": hw.get("cols", 64),
            "chain": hw.get("chain_length", 1),
        }).encode("utf-8")
        heartbeat = json.dumps({"t": "hb"}).encode("utf-8")
        dest = ("<broadcast>", self.port)

        last_hello = 0.0
        last_hb = 0.0

        while self._running:
            now = time.time()
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
                if time.time() - self._last_leader_frame_time > LEADER_TIMEOUT:
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

    def set_on_new_cycle(self, callback) -> None:
        """Follower: register a callback fired when the leader starts a new scroll cycle.
        Used to trigger a local start_new_cycle() so both Pis rebuild from same fresh data.
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
            "local_rows": hw.get("rows", 32),
            "local_cols": hw.get("cols", 64),
            "local_chain": hw.get("chain_length", 1),
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
        try:
            status = self.get_status()
            status["ts"] = time.time()
            tmp = STATUS_FILE + ".tmp"
            with open(tmp, "w") as f:
                json.dump(status, f)
            os.replace(tmp, STATUS_FILE)
        except Exception as exc:
            self.logger.debug("Sync: status file write error: %s", exc)

    def stop(self) -> None:
        """Shut down threads and close sockets."""
        self._running = False
        for sock in (self._recv_sock, self._send_sock, self._img_server_sock):
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass
