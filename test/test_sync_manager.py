"""
Tests for src/common/sync_manager.py — the UDP leader/follower protocol
that synchronizes scrolling content across two LED matrix displays.

This module had zero coverage: it only ever appeared in the suite as a
MagicMock() stand-in (test_vegas_continuous_refresh.py,
test_display_controller_vegas_tick.py), so none of its real framing,
handshake, or socket logic was exercised.

Most tests build the manager via object.__new__() + manual attribute
assignment (the test_display_controller_vegas_tick.py bare-stub pattern)
so no real sockets open and no background threads start. Receive loops are
driven synchronously by once_then_stop(): the mocked socket call returns
one crafted packet, then flips _running False and raises socket.timeout,
so `while self._running:` exits after exactly one real iteration.

Regression coverage for three fixed bugs:
- Both recv loops' generic `except Exception` retried with no delay, so a
  socket stuck raising a non-timeout error spun the thread at 100% CPU.
- _follower_recv_loop dispatched on `data[:8] == _RAW_MAGIC or
  len(data) > 512`, which routed any control message over 512 bytes into
  the image decoder (dropping it) and any raw frame under 512 bytes into
  the JSON parser.
- _oversized_frame_warned was read via getattr(self, ..., False) instead of
  being initialized in __init__.
"""

import io
import json
import socket
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from PIL import Image

from src.common import sync_manager
from src.common.sync_manager import (
    DisplaySyncManager,
    FollowerState,
    LeaderState,
    SyncRole,
)


@pytest.fixture(autouse=True)
def _isolated_status_file(tmp_path, monkeypatch):
    # STATUS_FILE is a module-level fixed path under tempfile.gettempdir() —
    # genuinely shared state between tests and even between processes.
    monkeypatch.setattr(
        sync_manager, "STATUS_FILE", str(tmp_path / "led_matrix_sync_status.json"))


def make_manager(role=SyncRole.STANDALONE, hw_config=None):
    """Bare stub bypassing __init__'s socket/thread setup."""
    mgr = object.__new__(DisplaySyncManager)
    mgr.role = role
    mgr.logger = MagicMock()
    mgr.port = sync_manager.SYNC_PORT
    mgr._hw_config = hw_config or {"rows": 32, "cols": 64, "chain_length": 1}

    mgr._leader_state = LeaderState.NO_PEER
    mgr._peer_ip = None
    mgr._peer_compatible = False
    mgr._peer_chain = 0
    mgr._last_heartbeat_time = 0.0
    mgr._leader_width = 0
    mgr._oversized_frame_warned = False

    mgr._follower_state = FollowerState.STANDALONE
    mgr._latest_frame = None
    mgr._latest_scroll_x = None
    mgr._last_leader_frame_time = 0.0
    mgr._frame_lock = threading.Lock()
    mgr._leader_ip = None
    mgr._on_new_cycle = None
    mgr._on_scroll_image = None
    mgr._pending_scroll_image = None
    mgr._scroll_image_lock = threading.Lock()
    mgr._img_server_sock = None

    mgr._on_follower_connected = None
    mgr._error_message = None
    mgr._running = False
    mgr._recv_sock = None
    mgr._send_sock = None
    return mgr


def once_then_stop(mgr, value):
    """side_effect returning `value` once, then stopping the enclosing loop."""
    state = {"served": False}

    def _side_effect(*args, **kwargs):
        if not state["served"]:
            state["served"] = True
            return value
        mgr._running = False
        raise socket.timeout()

    return _side_effect


def raise_n_then_stop(mgr, exc, count):
    """side_effect raising `exc` `count` times, then stopping the loop."""
    state = {"n": 0}

    def _side_effect(*args, **kwargs):
        state["n"] += 1
        if state["n"] <= count:
            raise exc
        mgr._running = False
        raise socket.timeout()

    return _side_effect


def run_watchdog_once(monkeypatch, mgr, watchdog, now):
    """Run exactly one watchdog iteration at a frozen wall-clock time."""
    monkeypatch.setattr(sync_manager.time, "time", lambda: now)
    monkeypatch.setattr(
        sync_manager.time, "sleep", lambda _: setattr(mgr, "_running", False))
    mgr._running = True
    watchdog()


class FakeConn:
    """Minimal TCP connection stand-in whose recv() drains a byte buffer."""

    def __init__(self, payload: bytes):
        self._buf = payload
        self.closed = False

    def settimeout(self, _):
        pass

    def recv(self, n):
        chunk, self._buf = self._buf[:n], self._buf[n:]
        return chunk

    def close(self):
        self.closed = True


def png_bytes(size=(10, 10), color=(1, 2, 3)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def raw_frame_packet(width, height, color=(10, 20, 30)) -> bytes:
    arr = np.asarray(Image.new("RGB", (width, height), color), dtype=np.uint8)
    return _magic_header(width, height) + arr.tobytes()


def _magic_header(width, height) -> bytes:
    return sync_manager._RAW_MAGIC + sync_manager._RAW_HEADER.pack(width, height)


def length_prefixed(payload: bytes) -> bytes:
    return len(payload).to_bytes(4, "big") + payload


class TestRoleParsing:
    def test_leader_role(self, monkeypatch):
        monkeypatch.setattr(DisplaySyncManager, "_start_leader", lambda self: None)
        assert DisplaySyncManager("leader", {}, {}, MagicMock()).role is SyncRole.LEADER

    def test_follower_role(self, monkeypatch):
        monkeypatch.setattr(DisplaySyncManager, "_start_follower", lambda self: None)
        assert DisplaySyncManager("follower", {}, {}, MagicMock()).role is SyncRole.FOLLOWER

    def test_standalone_starts_nothing(self):
        mgr = DisplaySyncManager("standalone", {}, {}, MagicMock())
        assert mgr.role is SyncRole.STANDALONE
        assert mgr._running is False
        assert mgr._recv_sock is None

    def test_invalid_role_warns_and_falls_back(self):
        logger = MagicMock()
        assert DisplaySyncManager("bogus", {}, {}, logger).role is SyncRole.STANDALONE
        assert logger.warning.called

    def test_role_matching_is_case_sensitive(self):
        # Pinned: SyncRole's values are lowercase, so "LEADER" is not
        # normalized — it is simply invalid and falls back to standalone.
        logger = MagicMock()
        assert DisplaySyncManager("LEADER", {}, {}, logger).role is SyncRole.STANDALONE
        assert logger.warning.called

    def test_port_defaults_to_module_constant(self):
        assert DisplaySyncManager("standalone", {}, {}, MagicMock()).port == sync_manager.SYNC_PORT

    def test_port_read_from_config(self):
        assert DisplaySyncManager("standalone", {"port": 9999}, {}, MagicMock()).port == 9999

    def test_oversized_frame_warned_initialized_in_init(self, monkeypatch):
        # Regression: this attribute was only ever created on first use via
        # getattr(self, '_oversized_frame_warned', False).
        monkeypatch.setattr(DisplaySyncManager, "_start_leader", lambda self: None)
        mgr = DisplaySyncManager("leader", {}, {}, MagicMock())
        assert mgr._oversized_frame_warned is False


class TestHandleHello:
    def test_matching_panels_connect(self):
        mgr = make_manager(role=SyncRole.LEADER)
        mgr._send_sock = MagicMock()
        mgr._handle_hello({"t": "hello", "rows": 32, "cols": 64, "chain": 3}, "10.0.0.5")
        assert mgr._leader_state is LeaderState.CONNECTED
        assert mgr._peer_ip == "10.0.0.5"
        assert mgr._peer_compatible is True
        assert mgr._peer_chain == 3
        assert mgr._error_message is None

    def test_ack_reports_compatibility(self):
        mgr = make_manager(role=SyncRole.LEADER)
        mgr._send_sock = MagicMock()
        mgr._leader_width = 128
        mgr._handle_hello({"t": "hello", "rows": 32, "cols": 64, "chain": 1}, "10.0.0.5")
        payload, dest = mgr._send_sock.sendto.call_args[0]
        ack = json.loads(payload.decode("utf-8"))
        assert ack["compatible"] is True
        assert ack["leader_width"] == 128
        assert dest == ("10.0.0.5", mgr.port)

    def test_mismatched_panels_are_incompatible(self):
        mgr = make_manager(role=SyncRole.LEADER)
        mgr._send_sock = MagicMock()
        mgr._handle_hello({"t": "hello", "rows": 16, "cols": 32, "chain": 1}, "10.0.0.5")
        assert mgr._leader_state is LeaderState.INCOMPATIBLE
        assert "Incompatible panels" in mgr._error_message
        ack = json.loads(mgr._send_sock.sendto.call_args[0][0].decode("utf-8"))
        assert ack["compatible"] is False
        assert ack["error"] == mgr._error_message

    def test_chain_length_may_differ(self):
        # Documented rule: rows/cols must match, chain_length need not.
        mgr = make_manager(role=SyncRole.LEADER, hw_config={"rows": 32, "cols": 64, "chain_length": 1})
        mgr._send_sock = MagicMock()
        mgr._handle_hello({"t": "hello", "rows": 32, "cols": 64, "chain": 4}, "10.0.0.5")
        assert mgr._leader_state is LeaderState.CONNECTED

    def test_connect_callback_fires_only_on_first_transition(self):
        mgr = make_manager(role=SyncRole.LEADER)
        mgr._send_sock = MagicMock()
        fired = threading.Event()
        calls = []
        mgr._on_follower_connected = lambda: (calls.append(1), fired.set())

        hello = {"t": "hello", "rows": 32, "cols": 64, "chain": 1}
        mgr._handle_hello(hello, "10.0.0.5")
        assert fired.wait(timeout=1)
        assert len(calls) == 1

        fired.clear()
        mgr._handle_hello(hello, "10.0.0.5")  # already CONNECTED
        assert not fired.wait(timeout=0.2)
        assert len(calls) == 1

    def test_ack_send_failure_is_swallowed(self):
        mgr = make_manager(role=SyncRole.LEADER)
        mgr._send_sock = MagicMock()
        mgr._send_sock.sendto.side_effect = OSError("network unreachable")
        mgr._handle_hello({"t": "hello", "rows": 32, "cols": 64, "chain": 1}, "10.0.0.5")
        assert mgr._leader_state is LeaderState.CONNECTED  # state still updated
        assert mgr.logger.debug.called


class TestWatchdogs:
    def test_leader_drops_peer_after_heartbeat_timeout(self, monkeypatch):
        mgr = make_manager(role=SyncRole.LEADER)
        mgr._leader_state = LeaderState.CONNECTED
        mgr._peer_ip = "10.0.0.1"
        mgr._peer_compatible = True
        mgr._last_heartbeat_time = 0.0
        run_watchdog_once(monkeypatch, mgr, mgr._leader_watchdog,
                          now=sync_manager.PEER_TIMEOUT + 1)
        assert mgr._leader_state is LeaderState.NO_PEER
        assert mgr._peer_ip is None
        assert mgr._peer_compatible is False

    def test_leader_keeps_peer_within_timeout(self, monkeypatch):
        mgr = make_manager(role=SyncRole.LEADER)
        mgr._leader_state = LeaderState.CONNECTED
        mgr._peer_ip = "10.0.0.1"
        mgr._last_heartbeat_time = 100.0
        run_watchdog_once(monkeypatch, mgr, mgr._leader_watchdog, now=101.0)
        assert mgr._leader_state is LeaderState.CONNECTED
        assert mgr._peer_ip == "10.0.0.1"

    def test_leader_watchdog_ignores_disconnected_state(self, monkeypatch):
        mgr = make_manager(role=SyncRole.LEADER)
        mgr._leader_state = LeaderState.INCOMPATIBLE
        mgr._last_heartbeat_time = 0.0
        run_watchdog_once(monkeypatch, mgr, mgr._leader_watchdog, now=10_000)
        assert mgr._leader_state is LeaderState.INCOMPATIBLE

    def test_follower_returns_to_standalone_after_frame_timeout(self, monkeypatch):
        mgr = make_manager(role=SyncRole.FOLLOWER)
        mgr._follower_state = FollowerState.FOLLOWER
        mgr._last_leader_frame_time = 0.0
        mgr._latest_frame = Image.new("RGB", (2, 2))
        run_watchdog_once(monkeypatch, mgr, mgr._follower_watchdog,
                          now=sync_manager.LEADER_TIMEOUT + 1)
        assert mgr._follower_state is FollowerState.STANDALONE
        assert mgr.get_latest_frame() is None

    def test_follower_keeps_frames_within_timeout(self, monkeypatch):
        mgr = make_manager(role=SyncRole.FOLLOWER)
        mgr._follower_state = FollowerState.FOLLOWER
        mgr._last_leader_frame_time = 100.0
        mgr._latest_frame = Image.new("RGB", (2, 2))
        run_watchdog_once(monkeypatch, mgr, mgr._follower_watchdog, now=101.0)
        assert mgr._follower_state is FollowerState.FOLLOWER
        assert mgr.get_latest_frame() is not None


class TestLeaderRecvLoop:
    def _drive(self, mgr, payload, sender="10.0.0.8"):
        mgr._recv_sock = MagicMock()
        mgr._recv_sock.recvfrom.side_effect = once_then_stop(mgr, (payload, (sender, 1)))
        mgr._running = True
        mgr._leader_recv_loop()

    def test_hello_is_dispatched(self):
        mgr = make_manager(role=SyncRole.LEADER)
        mgr._send_sock = MagicMock()
        self._drive(mgr, json.dumps(
            {"t": "hello", "rows": 32, "cols": 64, "chain": 1}).encode())
        assert mgr._leader_state is LeaderState.CONNECTED
        assert mgr._peer_ip == "10.0.0.8"

    def test_heartbeat_from_known_peer_refreshes_timer(self):
        mgr = make_manager(role=SyncRole.LEADER)
        mgr._peer_ip = "10.0.0.8"
        with patch.object(sync_manager.time, "time", return_value=12345.0):
            self._drive(mgr, json.dumps({"t": "hb"}).encode())
        assert mgr._last_heartbeat_time == 12345.0

    def test_heartbeat_from_stranger_is_ignored(self):
        mgr = make_manager(role=SyncRole.LEADER)
        mgr._peer_ip = "10.0.0.8"
        mgr._last_heartbeat_time = 5.0
        self._drive(mgr, json.dumps({"t": "hb"}).encode(), sender="10.0.0.99")
        assert mgr._last_heartbeat_time == 5.0

    def test_unknown_message_type_ignored(self):
        mgr = make_manager(role=SyncRole.LEADER)
        self._drive(mgr, json.dumps({"t": "who-knows"}).encode())
        assert mgr._leader_state is LeaderState.NO_PEER

    def test_malformed_json_is_swallowed(self):
        mgr = make_manager(role=SyncRole.LEADER)
        self._drive(mgr, b"{not json")
        assert mgr._leader_state is LeaderState.NO_PEER

    def test_undecodable_bytes_are_swallowed(self):
        mgr = make_manager(role=SyncRole.LEADER)
        self._drive(mgr, b"\xff\xfe\x00bad")
        assert mgr._leader_state is LeaderState.NO_PEER

    def test_backs_off_between_repeated_errors(self, monkeypatch):
        # Regression: without a sleep this loop spun at 100% CPU whenever
        # the socket raised a non-timeout error on every call.
        mgr = make_manager(role=SyncRole.LEADER)
        mgr._recv_sock = MagicMock()
        mgr._recv_sock.recvfrom.side_effect = raise_n_then_stop(mgr, OSError("boom"), 3)
        sleeps = MagicMock()
        monkeypatch.setattr(sync_manager.time, "sleep", sleeps)
        mgr._running = True
        mgr._leader_recv_loop()
        assert sleeps.call_count == 3
        sleeps.assert_called_with(0.1)


class TestFollowerRecvLoop:
    def _drive(self, mgr, payload, sender="10.0.0.2"):
        mgr._recv_sock = MagicMock()
        mgr._recv_sock.recvfrom.side_effect = once_then_stop(mgr, (payload, (sender, 1)))
        mgr._running = True
        mgr._follower_recv_loop()

    def test_small_raw_frame_is_decoded(self):
        # Regression: a raw frame under the old 512-byte threshold was sent
        # to the JSON parser and dropped.
        mgr = make_manager(role=SyncRole.FOLLOWER)
        packet = raw_frame_packet(4, 3)
        assert len(packet) <= 512
        self._drive(mgr, packet)
        frame = mgr.get_latest_frame()
        assert frame is not None and frame.size == (4, 3)
        assert mgr._follower_state is FollowerState.FOLLOWER

    def test_large_raw_frame_is_decoded(self):
        mgr = make_manager(role=SyncRole.FOLLOWER)
        packet = raw_frame_packet(64, 32)
        assert len(packet) > 512
        self._drive(mgr, packet)
        assert mgr.get_latest_frame().size == (64, 32)

    def test_large_control_message_is_not_routed_to_image_decode(self):
        # Regression: the old `len(data) > 512` branch treated any large
        # control message as frame data and silently discarded it.
        mgr = make_manager(role=SyncRole.FOLLOWER)
        long_error = "x" * 600
        payload = json.dumps(
            {"t": "hello_ack", "compatible": False, "error": long_error}).encode()
        assert len(payload) > 512
        self._drive(mgr, payload, sender="10.0.0.9")
        assert mgr._leader_ip == "10.0.0.9"
        assert mgr._peer_compatible is False
        assert mgr._error_message == long_error
        assert mgr.get_latest_frame() is None
        assert mgr.logger.error.called

    def test_legacy_png_frame_without_magic_is_decoded(self):
        mgr = make_manager(role=SyncRole.FOLLOWER)
        self._drive(mgr, png_bytes(size=(5, 5)))
        frame = mgr.get_latest_frame()
        assert frame is not None and frame.size == (5, 5)
        assert mgr._follower_state is FollowerState.FOLLOWER

    def test_truncated_raw_frame_is_swallowed(self):
        mgr = make_manager(role=SyncRole.FOLLOWER)
        self._drive(mgr, _magic_header(64, 32) + b"\x00" * 10)  # far too short
        assert mgr.get_latest_frame() is None
        assert mgr.logger.debug.called

    def test_garbage_payload_is_swallowed(self):
        mgr = make_manager(role=SyncRole.FOLLOWER)
        self._drive(mgr, b"neither json nor a png, just bytes 1234567890")
        assert mgr.get_latest_frame() is None

    def test_hello_ack_updates_peer_state(self):
        mgr = make_manager(role=SyncRole.FOLLOWER)
        self._drive(mgr, json.dumps(
            {"t": "hello_ack", "compatible": True, "error": None}).encode(),
            sender="10.0.0.6")
        assert mgr._leader_ip == "10.0.0.6"
        assert mgr._peer_compatible is True
        assert mgr.logger.error.called is False

    def test_scroll_x_switches_to_follower_and_builds_cycle(self):
        mgr = make_manager(role=SyncRole.FOLLOWER)
        calls = []
        mgr._on_new_cycle = lambda: calls.append(1)
        self._drive(mgr, json.dumps({"t": "sx", "x": 12.34}).encode())
        assert mgr._follower_state is FollowerState.FOLLOWER
        assert mgr.get_latest_scroll_x() == 12.34
        assert calls == [1]

    def test_scroll_x_while_already_following_does_not_rebuild(self):
        mgr = make_manager(role=SyncRole.FOLLOWER)
        mgr._follower_state = FollowerState.FOLLOWER
        calls = []
        mgr._on_new_cycle = lambda: calls.append(1)
        self._drive(mgr, json.dumps({"t": "sx", "x": 5.0}).encode())
        assert mgr.get_latest_scroll_x() == 5.0
        assert calls == []

    def test_new_cycle_message_triggers_callback(self):
        mgr = make_manager(role=SyncRole.FOLLOWER)
        mgr._follower_state = FollowerState.FOLLOWER
        calls = []
        mgr._on_new_cycle = lambda: calls.append(1)
        self._drive(mgr, json.dumps({"t": "nc"}).encode())
        assert calls == [1]

    def test_scroll_x_missing_key_is_swallowed(self):
        mgr = make_manager(role=SyncRole.FOLLOWER)
        self._drive(mgr, json.dumps({"t": "sx"}).encode())  # no "x"
        assert mgr.get_latest_scroll_x() is None

    def test_backs_off_between_repeated_errors(self, monkeypatch):
        mgr = make_manager(role=SyncRole.FOLLOWER)
        mgr._recv_sock = MagicMock()
        mgr._recv_sock.recvfrom.side_effect = raise_n_then_stop(mgr, OSError("boom"), 3)
        sleeps = MagicMock()
        monkeypatch.setattr(sync_manager.time, "sleep", sleeps)
        mgr._running = True
        mgr._follower_recv_loop()
        assert sleeps.call_count == 3
        sleeps.assert_called_with(0.1)


class TestSendFrame:
    def _connected_leader(self):
        mgr = make_manager(role=SyncRole.LEADER)
        mgr._leader_state = LeaderState.CONNECTED
        mgr._peer_ip = "10.0.0.1"
        mgr._send_sock = MagicMock()
        return mgr

    def test_frame_sent_with_magic_header(self):
        mgr = self._connected_leader()
        mgr.send_frame(Image.new("RGB", (8, 8)))
        packet = mgr._send_sock.sendto.call_args[0][0]
        assert packet[:8] == sync_manager._RAW_MAGIC
        assert sync_manager._RAW_HEADER.unpack(packet[8:12]) == (8, 8)

    def test_oversized_frame_warns_once_and_is_dropped(self):
        mgr = self._connected_leader()
        big = Image.new("RGB", (300, 300))  # 270000 bytes > 65000 UDP cap

        mgr.send_frame(big)
        assert mgr._oversized_frame_warned is True
        assert mgr.logger.warning.call_count == 1
        assert not mgr._send_sock.sendto.called

        mgr.send_frame(big)
        assert mgr.logger.warning.call_count == 1  # still warned only once

    def test_not_sent_when_no_peer(self):
        mgr = self._connected_leader()
        mgr._leader_state = LeaderState.NO_PEER
        mgr.send_frame(Image.new("RGB", (8, 8)))
        assert not mgr._send_sock.sendto.called

    def test_follower_never_sends(self):
        mgr = make_manager(role=SyncRole.FOLLOWER)
        mgr._send_sock = MagicMock()
        mgr.send_frame(Image.new("RGB", (8, 8)))
        assert not mgr._send_sock.sendto.called

    def test_send_error_is_swallowed(self):
        mgr = self._connected_leader()
        mgr._send_sock.sendto.side_effect = OSError("no route")
        mgr.send_frame(Image.new("RGB", (8, 8)))  # must not raise
        assert mgr.logger.debug.called


class TestSendControlMessages:
    def _connected_leader(self):
        mgr = make_manager(role=SyncRole.LEADER)
        mgr._leader_state = LeaderState.CONNECTED
        mgr._peer_ip = "10.0.0.1"
        mgr._send_sock = MagicMock()
        return mgr

    def test_send_scroll_x_rounds_to_two_places(self):
        mgr = self._connected_leader()
        mgr.send_scroll_x(3.14159)
        msg = json.loads(mgr._send_sock.sendto.call_args[0][0].decode())
        assert msg == {"t": "sx", "x": 3.14}

    def test_send_new_cycle(self):
        mgr = self._connected_leader()
        mgr.send_new_cycle()
        msg = json.loads(mgr._send_sock.sendto.call_args[0][0].decode())
        assert msg == {"t": "nc"}

    def test_control_messages_noop_when_disconnected(self):
        mgr = self._connected_leader()
        mgr._leader_state = LeaderState.NO_PEER
        mgr.send_scroll_x(1.0)
        mgr.send_new_cycle()
        assert not mgr._send_sock.sendto.called

    def test_set_leader_width(self):
        mgr = make_manager(role=SyncRole.LEADER)
        mgr.set_leader_width(256)
        assert mgr._leader_width == 256


class TestImageServerLoop:
    def _drive(self, mgr, conn):
        mgr._img_server_sock = MagicMock()
        mgr._img_server_sock.accept.side_effect = once_then_stop(
            mgr, (conn, ("10.0.0.1", 1)))
        mgr._running = True
        mgr._image_server_loop()

    def test_rejects_non_positive_length(self):
        mgr = make_manager(role=SyncRole.FOLLOWER)
        mgr._on_scroll_image = MagicMock()
        self._drive(mgr, FakeConn((0).to_bytes(4, "big")))
        assert mgr.logger.warning.called
        mgr._on_scroll_image.assert_not_called()

    def test_rejects_oversized_length(self):
        mgr = make_manager(role=SyncRole.FOLLOWER)
        mgr._on_scroll_image = MagicMock()
        self._drive(mgr, FakeConn((11 * 1024 * 1024).to_bytes(4, "big")))
        assert mgr.logger.warning.called
        mgr._on_scroll_image.assert_not_called()

    def test_rejects_oversized_dimensions(self):
        mgr = make_manager(role=SyncRole.FOLLOWER)
        mgr._on_scroll_image = MagicMock()
        self._drive(mgr, FakeConn(length_prefixed(png_bytes(size=(300, 300)))))
        assert mgr.logger.warning.called
        mgr._on_scroll_image.assert_not_called()

    def test_rejects_decompression_bomb(self, monkeypatch):
        mgr = make_manager(role=SyncRole.FOLLOWER)
        mgr._on_scroll_image = MagicMock()

        class BombImage:
            width = height = 10

            def load(self):
                raise Image.DecompressionBombError("too many pixels")

        monkeypatch.setattr(sync_manager.Image, "open", lambda *a, **kw: BombImage())
        self._drive(mgr, FakeConn(length_prefixed(png_bytes())))
        assert mgr.logger.warning.called
        mgr._on_scroll_image.assert_not_called()

    def test_valid_image_invokes_callback(self):
        mgr = make_manager(role=SyncRole.FOLLOWER)
        received = []
        mgr._on_scroll_image = received.append
        self._drive(mgr, FakeConn(length_prefixed(png_bytes(size=(10, 10)))))
        assert len(received) == 1
        assert received[0].size == (10, 10)

    def test_image_cached_when_callback_not_yet_registered(self):
        mgr = make_manager(role=SyncRole.FOLLOWER)
        mgr._on_scroll_image = None
        self._drive(mgr, FakeConn(length_prefixed(png_bytes(size=(6, 6)))))
        assert mgr._pending_scroll_image is not None
        assert mgr._pending_scroll_image.size == (6, 6)

    def test_short_header_is_skipped(self):
        mgr = make_manager(role=SyncRole.FOLLOWER)
        mgr._on_scroll_image = MagicMock()
        self._drive(mgr, FakeConn(b"\x00\x01"))  # under the 4-byte prefix
        mgr._on_scroll_image.assert_not_called()

    def test_connection_always_closed(self):
        mgr = make_manager(role=SyncRole.FOLLOWER)
        conn = FakeConn(length_prefixed(png_bytes()))
        self._drive(mgr, conn)
        assert conn.closed is True


class TestScrollImageCallback:
    def test_pending_image_delivered_on_late_registration(self):
        mgr = make_manager(role=SyncRole.FOLLOWER)
        img = Image.new("RGB", (3, 3))
        mgr._pending_scroll_image = img
        received = []
        mgr.set_on_scroll_image(received.append)
        assert received == [img]
        assert mgr._pending_scroll_image is None

    def test_no_pending_image_means_no_immediate_call(self):
        mgr = make_manager(role=SyncRole.FOLLOWER)
        received = []
        mgr.set_on_scroll_image(received.append)
        assert received == []


class TestFollowerConnectedCallback:
    def test_fires_immediately_when_already_connected(self):
        mgr = make_manager(role=SyncRole.LEADER)
        mgr._leader_state = LeaderState.CONNECTED
        fired = threading.Event()
        mgr.set_on_follower_connected(fired.set)
        assert fired.wait(timeout=1)

    def test_does_not_fire_when_no_peer(self):
        mgr = make_manager(role=SyncRole.LEADER)
        fired = threading.Event()
        mgr.set_on_follower_connected(fired.set)
        assert not fired.wait(timeout=0.2)


class TestSendScrollImage:
    def test_noop_when_not_connected(self):
        mgr = make_manager(role=SyncRole.LEADER)
        mgr._leader_state = LeaderState.NO_PEER
        with patch.object(sync_manager.socket, "socket") as sock:
            mgr.send_scroll_image(Image.new("RGB", (4, 4)))
            sock.assert_not_called()

    def test_noop_for_follower_role(self):
        mgr = make_manager(role=SyncRole.FOLLOWER)
        with patch.object(sync_manager.socket, "socket") as sock:
            mgr.send_scroll_image(Image.new("RGB", (4, 4)))
            sock.assert_not_called()

    def test_sends_length_prefixed_png(self):
        mgr = make_manager(role=SyncRole.LEADER)
        mgr._leader_state = LeaderState.CONNECTED
        mgr._peer_ip = "10.0.0.1"
        fake_sock = MagicMock()
        fake_sock.__enter__ = lambda s: s
        fake_sock.__exit__ = lambda s, *a: False
        with patch.object(sync_manager.socket, "socket", return_value=fake_sock):
            mgr.send_scroll_image(Image.new("RGB", (4, 4)))
        payload = fake_sock.sendall.call_args[0][0]
        assert int.from_bytes(payload[:4], "big") == len(payload) - 4
        assert payload[4:8] == b"\x89PNG"

    def test_connection_error_is_swallowed(self):
        mgr = make_manager(role=SyncRole.LEADER)
        mgr._leader_state = LeaderState.CONNECTED
        mgr._peer_ip = "10.0.0.1"
        with patch.object(sync_manager.socket, "socket", side_effect=OSError("refused")):
            mgr.send_scroll_image(Image.new("RGB", (4, 4)))  # must not raise
        assert mgr.logger.debug.called


class TestGetStatus:
    def test_standalone_shape(self):
        status = make_manager(role=SyncRole.STANDALONE).get_status()
        assert status["role"] == "standalone"
        assert status["state"] == "standalone"
        assert status["local_rows"] == 32 and status["local_cols"] == 64

    def test_leader_shape(self):
        mgr = make_manager(role=SyncRole.LEADER)
        mgr._leader_state = LeaderState.CONNECTED
        mgr._peer_ip = "10.0.0.1"
        mgr._peer_compatible = True
        mgr._peer_chain = 2
        mgr._leader_width = 128
        status = mgr.get_status()
        assert status["role"] == "leader"
        assert status["state"] == "connected"
        assert status["peer_ip"] == "10.0.0.1"
        assert status["peer_chain"] == 2
        assert status["leader_width"] == 128

    def test_follower_shape(self):
        mgr = make_manager(role=SyncRole.FOLLOWER)
        mgr._follower_state = FollowerState.FOLLOWER
        mgr._leader_ip = "10.0.0.2"
        status = mgr.get_status()
        assert status["role"] == "follower"
        assert status["state"] == "follower"
        assert status["leader_ip"] == "10.0.0.2"
        assert "peer_chain" not in status

    def test_is_follower_active(self):
        mgr = make_manager(role=SyncRole.FOLLOWER)
        assert mgr.is_follower_active() is False
        mgr._follower_state = FollowerState.FOLLOWER
        assert mgr.is_follower_active() is True

    def test_leader_is_never_follower_active(self):
        mgr = make_manager(role=SyncRole.LEADER)
        mgr._follower_state = FollowerState.FOLLOWER
        assert mgr.is_follower_active() is False


class TestWriteStatusFile:
    def test_writes_status_and_cleans_up_temp(self):
        mgr = make_manager(role=SyncRole.STANDALONE)
        mgr.write_status_file()
        data = json.loads(Path(sync_manager.STATUS_FILE).read_text())
        assert data["role"] == "standalone"
        assert "ts" in data
        assert not Path(sync_manager.STATUS_FILE + ".tmp").exists()

    def test_write_failure_is_swallowed(self, monkeypatch):
        mgr = make_manager(role=SyncRole.STANDALONE)
        monkeypatch.setattr("builtins.open", MagicMock(side_effect=OSError("disk full")))
        mgr.write_status_file()  # must not raise
        assert mgr.logger.debug.called


class TestStop:
    def _stub_with_sockets(self):
        mgr = make_manager(role=SyncRole.LEADER)
        mgr._recv_sock = MagicMock()
        mgr._send_sock = MagicMock()
        mgr._img_server_sock = MagicMock()
        return mgr

    def test_closes_every_socket(self):
        mgr = self._stub_with_sockets()
        mgr.stop()
        assert mgr._running is False
        mgr._recv_sock.close.assert_called_once()
        mgr._send_sock.close.assert_called_once()
        mgr._img_server_sock.close.assert_called_once()

    def test_is_idempotent(self):
        mgr = self._stub_with_sockets()
        mgr.stop()
        mgr.stop()  # must not raise

    def test_close_failure_is_swallowed(self):
        mgr = make_manager(role=SyncRole.LEADER)
        mgr._recv_sock = MagicMock()
        mgr._recv_sock.close.side_effect = OSError("already closed")
        mgr.stop()  # must not raise
        assert mgr.logger.debug.called

    def test_handles_unset_sockets(self):
        make_manager(role=SyncRole.STANDALONE).stop()  # all sockets None


class TestLoopbackHandshake:
    def test_leader_and_follower_negotiate_over_real_sockets(self, monkeypatch):
        # One end-to-end check that the wire format actually round-trips:
        # every other test drives the loops with mocked sockets.
        monkeypatch.setattr(sync_manager, "HELLO_INTERVAL", 0.02)
        monkeypatch.setattr(sync_manager, "HEARTBEAT_INTERVAL", 0.02)

        # Pick a free port by binding one on loopback and releasing it.
        # Loopback, not "", so this test never opens a port to the network.
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()

        hw = {"rows": 32, "cols": 64, "chain_length": 1}
        leader = DisplaySyncManager("leader", {"port": port}, hw, MagicMock())
        follower = DisplaySyncManager("follower", {"port": port}, hw, MagicMock())
        try:
            deadline = time.time() + 5.0
            while time.time() < deadline:
                if (leader._leader_state is LeaderState.CONNECTED
                        and follower._peer_compatible):
                    break
                time.sleep(0.02)
            assert leader._leader_state is LeaderState.CONNECTED
            assert follower._peer_compatible is True
            assert follower._leader_ip is not None
        finally:
            leader.stop()
            follower.stop()
