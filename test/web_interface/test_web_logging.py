"""The web process logs the way the display process does.

web_interface/app.py used to call its own setup (web_interface/logging_config.py)
which replaced the root handlers with a plain stdout formatter. Under systemd
every line then reached the journal as PRIORITY=6, so

    journalctl -p err -u ledmatrix-web

showed nothing while the web interface was logging errors. It also logged
every request at INFO, including what the UI polls: the journal on a Pi showed
``GET /api/v3/errors/summary - 200`` every minute per open tab.
"""
import logging
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from flask import Flask

from web_interface import request_logging

PROJECT_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# The real app, imported the way systemd runs it
# ---------------------------------------------------------------------------

_CHILD = textwrap.dedent("""
    import logging
    import web_interface.app as web_app

    # Startup reconciliation may try to reinstall plugins; not this test's job.
    web_app._reconciliation_started = True
    client = web_app.app.test_client()
    client.get('/api/v3/errors/summary')
    client.get('/favicon.ico')
    client.get('/api/v3/no-such-endpoint')
    logging.getLogger('web_interface.probe').error('probe error line')
    logging.getLogger('web_interface.probe').info('probe info line')
""")


@pytest.fixture(scope="module")
def journal_output(tmp_path_factory):
    """Run the child with stdout as a file systemd would call the journal.

    systemd sets JOURNAL_STREAM to the dev:ino of the stream it captures;
    src.logging_config only adds priorities when stdout really is that stream,
    so hand the child a file and name that file's dev:ino.
    """
    out_path = tmp_path_factory.mktemp("journal") / "stdout.txt"
    with open(out_path, "wb") as out:
        st = os.fstat(out.fileno())
        env = dict(os.environ)
        env.update({
            "JOURNAL_STREAM": f"{st.st_dev}:{st.st_ino}",
            "PYTHONUTF8": "1",
            "EMULATOR": "true",
            "PYTHONPATH": str(PROJECT_ROOT),
        })
        env.pop("LEDMATRIX_DEBUG", None)
        env.pop("LEDMATRIX_JSON_LOGGING", None)
        proc = subprocess.run(
            [sys.executable, "-c", _CHILD], cwd=str(PROJECT_ROOT), env=env,
            stdout=out, stderr=subprocess.PIPE, timeout=180,
        )
    text = out_path.read_text(encoding="utf-8", errors="replace")
    assert proc.returncode == 0, proc.stderr.decode(errors="replace")[-4000:]
    return text.splitlines()


def test_error_reaches_the_journal_as_err(journal_output):
    lines = [l for l in journal_output if "probe error line" in l]
    assert lines, "\n".join(journal_output[-40:])
    assert lines[0].startswith("<3>"), lines[0]
    # Same readable shape as the display service (and what the log viewer strips).
    assert " - ERROR - web_interface.probe - probe error line" in lines[0]


def test_info_reaches_the_journal_as_info(journal_output):
    lines = [l for l in journal_output if "probe info line" in l]
    assert lines and lines[0].startswith("<6>"), journal_output[-40:]


def test_polling_gets_are_not_logged_at_info(journal_output):
    for path in ("/api/v3/errors/summary", "/favicon.ico"):
        assert not [l for l in journal_output if f"GET {path} " in l], (
            f"a successful GET {path} was logged by default")


def test_failed_request_is_still_logged(journal_output):
    lines = [l for l in journal_output if "GET /api/v3/no-such-endpoint - 404" in l]
    assert lines and lines[0].startswith("<4>"), journal_output[-40:]


# ---------------------------------------------------------------------------
# The level policy
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("method,status,level", [
    ("GET", 200, logging.DEBUG),
    ("GET", 304, logging.DEBUG),
    ("HEAD", 200, logging.DEBUG),
    ("OPTIONS", 204, logging.DEBUG),
    ("get", 200, logging.DEBUG),
    ("POST", 200, logging.INFO),
    ("PUT", 204, logging.INFO),
    ("DELETE", 200, logging.INFO),
    ("PATCH", 302, logging.INFO),
    ("GET", 404, logging.WARNING),
    ("POST", 400, logging.WARNING),
    ("GET", 500, logging.ERROR),
    ("POST", 503, logging.ERROR),
])
def test_request_log_level(method, status, level):
    assert request_logging.request_log_level(method, status) == level


@pytest.fixture
def tiny_app():
    app = Flask(__name__)
    request_logging.init_app(app)

    @app.route("/poll")
    def poll():
        return "ok"

    @app.route("/save", methods=["POST"])
    def save():
        return "saved"

    @app.route("/boom")
    def boom():
        return "no", 500

    return app.test_client()


def test_hooks_log_each_request_once_at_its_level(tiny_app, caplog):
    caplog.set_level(logging.DEBUG, logger="web_interface.api")
    tiny_app.get("/poll")
    tiny_app.post("/save")
    tiny_app.get("/boom")
    tiny_app.get("/missing")
    got = [(r.levelno, r.getMessage().split(" (")[0]) for r in caplog.records
           if r.name == "web_interface.api"]
    assert got == [
        (logging.DEBUG, "GET /poll - 200"),
        (logging.INFO, "POST /save - 200"),
        (logging.ERROR, "GET /boom - 500"),
        (logging.WARNING, "GET /missing - 404"),
    ]


def test_duration_is_rounded(tiny_app, caplog):
    caplog.set_level(logging.DEBUG, logger="web_interface.api")
    tiny_app.post("/save")
    msg = caplog.records[-1].getMessage()
    duration = msg.rsplit("(", 1)[1]
    assert duration.endswith("ms)") and len(duration.split(".")[1]) == len("0ms)"), msg
