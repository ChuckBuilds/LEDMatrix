"""/api/v3/system/status must report MemAvailable, not just used/total.

"Memory used %" cannot tell a healthy board from one about to fail. Page cache
counts as used and is reclaimable on demand, so a Pi can read 70% used and be
perfectly fine, or read the same and be minutes from trouble. MemAvailable is
the kernel's own estimate of what a new allocation can actually obtain, and it
is the number that tracked the failure on a 1GB Pi 3B+: healthy running sat at
500MB+, the crash happened at 73MB, and by then fork() was failing -- sshd
could not spawn a session and systemd could not respawn the display, while the
kernel carried on answering pings.

psutil.virtual_memory().available is MemAvailable on Linux. total - used is not
a substitute: they diverge exactly when unreclaimable memory (shmem, tmpfs) is
in play, which is when the distinction matters.
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask

sys.path.insert(0, str(Path(__file__).parent.parent))

MB = 1024 * 1024


@pytest.fixture
def client():
    pytest.importorskip("psutil")
    app = Flask(__name__)
    app.config["TESTING"] = True
    from web_interface.blueprints.api_v3 import api_v3
    for attr in ("config_manager", "plugin_manager", "cache_manager"):
        setattr(api_v3, attr, MagicMock())
    if "api_v3" not in app.blueprints:
        app.register_blueprint(api_v3, url_prefix="/api/v3")
    return app.test_client()


def _memory(total_mb, used_mb, available_mb):
    m = MagicMock()
    m.total = total_mb * MB
    m.used = used_mb * MB
    m.available = available_mb * MB
    m.percent = round(used_mb / total_mb * 100, 1)
    return m


def _get_status(client, memory):
    # The endpoint caches for 10s; bypass so each case is measured fresh.
    with patch("web_interface.cache.get_cached", return_value=None), \
         patch("psutil.virtual_memory", return_value=memory), \
         patch("psutil.cpu_percent", return_value=5.0), \
         patch("psutil.boot_time", return_value=0.0):
        resp = client.get("/api/v3/system/status")
    assert resp.status_code == 200, resp.data
    return json.loads(resp.data)["data"]


def test_available_memory_is_reported(client):
    data = _get_status(client, _memory(total_mb=905, used_mb=620, available_mb=284))
    assert "memory_available_mb" in data
    assert data["memory_available_mb"] == pytest.approx(284, abs=0.5)


def test_available_is_not_total_minus_used(client):
    # The case the readout exists for: 600MB is "not used", but only 300MB can
    # actually be allocated. Reporting used% alone would call this healthy.
    data = _get_status(client, _memory(total_mb=1000, used_mb=400, available_mb=300))

    derived = data["memory_total_mb"] - data["memory_used_mb"]
    assert derived == pytest.approx(600, abs=1)
    assert data["memory_available_mb"] == pytest.approx(300, abs=0.5)
    assert data["memory_available_mb"] != pytest.approx(derived, abs=1), \
        "available must come from MemAvailable, not be derived from used"


def test_existing_memory_fields_are_unchanged(client):
    data = _get_status(client, _memory(total_mb=905, used_mb=620, available_mb=284))
    assert data["memory_total_mb"] == pytest.approx(905, abs=0.5)
    assert data["memory_used_mb"] == pytest.approx(620, abs=0.5)
    assert "memory_used_percent" in data


def test_a_nearly_exhausted_board_reports_a_small_number(client):
    # 73MB available is what the board actually read when it stopped being able
    # to fork. The readout has to surface that rather than round it away.
    data = _get_status(client, _memory(total_mb=905, used_mb=800, available_mb=73))
    assert data["memory_available_mb"] == pytest.approx(73, abs=0.5)
