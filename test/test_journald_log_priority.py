"""Log lines must reach the journal with their real severity.

Everything this process writes to stdout lands in the journal as PRIORITY=6,
whatever the Python level was, because journald has no other signal. Measured
on a live rig over 24 hours: 55 lines containing " - ERROR - " and 13
containing " - WARNING - ", every one of them recorded as informational. So

    journalctl -p err -u ledmatrix

returned nothing while errors were being logged, and anyone triaging has to
grep the message text instead. That is slower and it is wrong: a search for
"oom" also matches the radar logging "zoom=9", which is exactly the false
positive it produced during this audit.

systemd reads a leading "<N>" on each stdout line and uses it as the priority
(sd-daemon(3)), so this needs no extra dependency -- and it must only be
applied when systemd is actually reading, or the prefixes become literal noise
in a terminal, the emulator, and test output.
"""
import logging
import os
import sys
from unittest.mock import patch

import pytest

from src.logging_config import JournalPriorityFormatter, _SYSLOG_PRIORITY, _under_systemd


class _Plain(logging.Formatter):
    def format(self, record):
        return record.getMessage()


def _record(level, msg="hello"):
    return logging.LogRecord("t", level, "f.py", 1, msg, None, None)


@pytest.mark.parametrize("level,expected", [
    (logging.CRITICAL, 2),
    (logging.ERROR, 3),
    (logging.WARNING, 4),
    (logging.INFO, 6),
    (logging.DEBUG, 7),
])
def test_each_level_maps_to_its_syslog_priority(level, expected):
    out = JournalPriorityFormatter(_Plain()).format(_record(level))
    assert out.startswith(f"<{expected}>"), out
    assert _SYSLOG_PRIORITY[level] == expected


def test_error_and_info_are_distinguishable():
    """The whole point: journalctl -p err must be able to tell them apart."""
    fmt = JournalPriorityFormatter(_Plain())
    assert fmt.format(_record(logging.ERROR))[:3] != fmt.format(_record(logging.INFO))[:3]


def test_every_line_of_a_multiline_record_is_tagged():
    """The journal splits them, and an untagged continuation loses its level.

    A traceback is the case that matters -- it is the most important thing in
    the log and the longest.
    """
    out = JournalPriorityFormatter(_Plain()).format(
        _record(logging.ERROR, "Traceback:\nline one\nline two"))
    lines = out.split("\n")
    assert len(lines) == 3
    assert all(line.startswith("<3>") for line in lines), lines


def test_the_message_survives_intact():
    out = JournalPriorityFormatter(_Plain()).format(_record(logging.WARNING, "disk full"))
    assert out == "<4>disk full"


def test_an_unknown_level_falls_back_to_info():
    out = JournalPriorityFormatter(_Plain()).format(_record(25))
    assert out.startswith("<6>")


def _stdout_ids():
    """The dev:ino systemd would publish for this process's stdout."""
    st = os.fstat(sys.stdout.fileno())
    return f"{st.st_dev}:{st.st_ino}"


def test_prefixing_is_off_outside_systemd():
    """Otherwise a terminal run, the emulator and pytest all show `<6>`."""
    with patch.dict(os.environ, {}, clear=True):
        assert not _under_systemd()
    with patch.dict(os.environ, {"JOURNAL_STREAM": _stdout_ids()}):
        assert _under_systemd()


def test_an_inherited_journal_stream_does_not_count():
    """The variable outlives the descriptor it describes.

    systemd sets JOURNAL_STREAM for the service, and every child inherits it
    -- including one whose stdout has been redirected to a pipe or a file.
    Trusting the variable alone put literal "<6>" prefixes into that captured
    output. Only a descriptor whose dev:ino actually matches is the journal.
    """
    with patch.dict(os.environ, {"JOURNAL_STREAM": "8:12345"}):
        assert not _under_systemd(), \
            "a stale inherited JOURNAL_STREAM was treated as the journal"


@pytest.mark.parametrize("value", ["", "not-a-pair", "8", "8:", ":12345",
                                   "eight:12345", "8:12345:9"])
def test_a_malformed_journal_stream_is_not_the_journal(value):
    with patch.dict(os.environ, {"JOURNAL_STREAM": value}):
        assert not _under_systemd()


def test_a_closed_stdout_is_not_the_journal():
    """os.fstat raises rather than answers; that must not propagate."""
    with patch.dict(os.environ, {"JOURNAL_STREAM": "8:12345"}), \
            patch("src.logging_config.sys.stdout") as fake_stdout:
        fake_stdout.fileno.side_effect = ValueError("I/O operation on closed file")
        assert not _under_systemd()


def test_setup_uses_the_wrapper_only_under_systemd():
    from src.logging_config import setup_logging

    for env, expect_wrapped in (({}, False),
                                ({"JOURNAL_STREAM": _stdout_ids()}, True)):
        with patch.dict(os.environ, env, clear=True):
            setup_logging()
            handlers = [h for h in logging.getLogger().handlers
                        if isinstance(h, logging.StreamHandler)]
            assert handlers, "no stream handler installed"
            wrapped = any(isinstance(h.formatter, JournalPriorityFormatter)
                          for h in handlers)
            assert wrapped is expect_wrapped, (
                f"JOURNAL_STREAM={env}: wrapped={wrapped}, expected {expect_wrapped}")
    logging.getLogger().handlers.clear()
