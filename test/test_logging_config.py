"""
Tests for src/logging_config.py — the formatters, adapter, and setup used
by every logger in the system (BasePlugin uses get_logger, not stdlib
logging.getLogger).

Includes regression guards for two fixed bugs: ContextualFormatter used to
mutate record.msg in place (double-prefixing with two handlers), and
log_error hardcoded exc_info=True so passing it explicitly raised
TypeError.
"""

import json
import logging
import sys

import pytest

from src.logging_config import (
    ContextualFormatter,
    PluginLoggerAdapter,
    StructuredFormatter,
    get_logger,
    log_debug,
    log_error,
    log_info,
    log_warning,
    log_with_context,
    setup_logging,
)


def make_record(msg="hello", level=logging.INFO, **extra):
    record = logging.LogRecord(
        name="test.logger", level=level, pathname=__file__, lineno=42,
        msg=msg, args=(), exc_info=None)
    for key, value in extra.items():
        setattr(record, key, value)
    return record


class TestStructuredFormatter:
    def test_emits_valid_json_with_base_keys(self):
        out = json.loads(StructuredFormatter().format(make_record()))
        assert set(out) == {
            "timestamp", "level", "logger", "message",
            "module", "function", "line",
        }
        assert out["level"] == "INFO"
        assert out["message"] == "hello"
        assert out["logger"] == "test.logger"

    def test_optional_keys_only_when_present(self):
        record = make_record(context={"k": "v"}, plugin_id="clock",
                             operation_id="op-1")
        out = json.loads(StructuredFormatter().format(record))
        assert out["context"] == {"k": "v"}
        assert out["plugin_id"] == "clock"
        assert out["operation_id"] == "op-1"

    def test_exception_key_when_exc_info_present(self):
        try:
            raise ValueError("kaboom")
        except ValueError:
            record = logging.LogRecord(
                name="t", level=logging.ERROR, pathname=__file__, lineno=1,
                msg="failed", args=(), exc_info=sys.exc_info())
        out = json.loads(StructuredFormatter().format(record))
        assert "kaboom" in out["exception"]

    def test_percent_args_formatted_into_message(self):
        record = logging.LogRecord(
            name="t", level=logging.INFO, pathname=__file__, lineno=1,
            msg="count=%d", args=(7,), exc_info=None)
        out = json.loads(StructuredFormatter().format(record))
        assert out["message"] == "count=7"


class TestContextualFormatter:
    def test_context_prefix_prepended(self):
        record = make_record(plugin_id="clock", operation_id="op-1",
                             context={"k": "v"})
        out = ContextualFormatter().format(record)
        assert "[Plugin: clock] [Op: op-1] [k: v] hello" in out

    def test_include_context_false_leaves_message_bare(self):
        record = make_record(plugin_id="clock")
        out = ContextualFormatter(include_context=False).format(record)
        assert "[Plugin:" not in out
        assert "hello" in out

    def test_location_toggle(self):
        record = make_record()
        with_loc = ContextualFormatter(include_location=True).format(record)
        without = ContextualFormatter(include_location=False).format(record)
        assert f":{record.lineno}" in with_loc
        assert f":{record.lineno}" not in without

    def test_record_not_mutated_no_double_prefix(self):
        # Regression: a record is formatted once PER HANDLER. The formatter
        # must not mutate record.msg, or the second handler's format call
        # prepends the prefix again.
        record = make_record(plugin_id="clock")
        formatter = ContextualFormatter()
        first = formatter.format(record)
        second = formatter.format(record)
        assert record.msg == "hello"  # untouched
        assert first.count("[Plugin: clock]") == 1
        assert second.count("[Plugin: clock]") == 1

    def test_percent_args_still_format_after_copy(self):
        record = logging.LogRecord(
            name="t", level=logging.INFO, pathname=__file__, lineno=1,
            msg="count=%d", args=(7,), exc_info=None)
        record.plugin_id = "clock"
        out = ContextualFormatter().format(record)
        assert "[Plugin: clock] count=7" in out

    def test_exception_renders_through_two_handlers(self):
        try:
            raise ValueError("kaboom")
        except ValueError:
            record = logging.LogRecord(
                name="t", level=logging.ERROR, pathname=__file__, lineno=1,
                msg="failed", args=(), exc_info=sys.exc_info())
        record.plugin_id = "clock"
        formatter = ContextualFormatter()
        assert "kaboom" in formatter.format(record)
        assert "kaboom" in formatter.format(record)  # second handler's pass


class TestPluginLoggerAdapter:
    def _capture(self, adapter):
        records = []
        handler = logging.Handler()
        handler.emit = records.append
        adapter.logger.addHandler(handler)
        adapter.logger.setLevel(logging.DEBUG)
        return records

    def test_stamps_plugin_id_on_every_record(self):
        adapter = get_logger("test.adapter1", plugin_id="clock")
        records = self._capture(adapter)
        adapter.info("x")
        assert records[0].plugin_id == "clock"

    def test_explicit_extra_plugin_id_wins(self):
        adapter = get_logger("test.adapter2", plugin_id="clock")
        records = self._capture(adapter)
        adapter.info("x", extra={"plugin_id": "other"})
        assert records[0].plugin_id == "other"

    def test_unrelated_extra_keys_preserved(self):
        adapter = get_logger("test.adapter3", plugin_id="clock")
        records = self._capture(adapter)
        adapter.info("x", extra={"custom": 1})
        assert records[0].plugin_id == "clock"
        assert records[0].custom == 1


class TestGetLogger:
    def test_plain_logger_without_plugin_id(self):
        logger = get_logger("test.plain")
        assert isinstance(logger, logging.Logger)
        assert logger.name == "test.plain"

    def test_adapter_with_plugin_id(self):
        adapter = get_logger("test.wrapped", plugin_id="clock")
        assert isinstance(adapter, PluginLoggerAdapter)
        assert adapter.logger.name == "test.wrapped"


class TestSetupLogging:
    # conftest's autouse reset_logging restores root handlers after each test.

    def test_installs_single_stdout_handler(self):
        setup_logging()
        root = logging.getLogger()
        assert len(root.handlers) == 1
        assert isinstance(root.handlers[0], logging.StreamHandler)

    def test_repeat_calls_do_not_accumulate_handlers(self):
        setup_logging()
        setup_logging()
        assert len(logging.getLogger().handlers) == 1

    def test_json_format_selects_structured_formatter(self):
        setup_logging(format_type="json")
        assert isinstance(
            logging.getLogger().handlers[0].formatter, StructuredFormatter)

    def test_readable_format_selects_contextual_formatter(self):
        setup_logging(format_type="readable")
        assert isinstance(
            logging.getLogger().handlers[0].formatter, ContextualFormatter)

    def test_log_file_adds_file_handler(self, tmp_path):
        log_file = tmp_path / "test.log"
        setup_logging(log_file=str(log_file))
        root = logging.getLogger()
        file_handlers = [h for h in root.handlers
                         if isinstance(h, logging.FileHandler)]
        assert len(file_handlers) == 1
        for h in file_handlers:
            h.close()

    def test_unwritable_log_file_warns_and_keeps_console(self, tmp_path, capsys):
        bad_path = tmp_path / "no-such-dir" / "test.log"
        setup_logging(log_file=str(bad_path))  # must not raise
        assert len(logging.getLogger().handlers) == 1  # console only
        assert "Could not set up file logging" in capsys.readouterr().err

    def test_debug_env_true_enables_debug(self, monkeypatch):
        monkeypatch.setenv("LEDMATRIX_DEBUG", "TRUE")
        setup_logging()
        assert logging.getLogger().level == logging.DEBUG

    def test_debug_env_other_values_stay_info(self, monkeypatch):
        # Pinned: only the literal (case-insensitive) "true" enables debug;
        # "1" does not.
        monkeypatch.setenv("LEDMATRIX_DEBUG", "1")
        setup_logging()
        assert logging.getLogger().level == logging.INFO

    def test_explicit_level_wins_over_env(self, monkeypatch):
        monkeypatch.setenv("LEDMATRIX_DEBUG", "true")
        setup_logging(level=logging.WARNING)
        assert logging.getLogger().level == logging.WARNING


class TestLogWithContext:
    def _capture(self, name):
        logger = logging.getLogger(name)
        records = []
        handler = logging.Handler()
        handler.emit = records.append
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
        return logger, records

    def test_context_attrs_stamped(self):
        logger, records = self._capture("test.lwc1")
        log_with_context(logger, logging.INFO, "msg",
                         context={"k": "v"}, plugin_id="clock",
                         operation_id="op-1")
        record = records[0]
        assert record.context == {"k": "v"}
        assert record.plugin_id == "clock"
        assert record.operation_id == "op-1"

    def test_wrappers_use_their_levels(self):
        logger, records = self._capture("test.lwc2")
        log_debug(logger, "d")
        log_info(logger, "i")
        log_warning(logger, "w")
        assert [r.levelno for r in records] == [
            logging.DEBUG, logging.INFO, logging.WARNING]

    def test_log_error_defaults_exc_info_true(self):
        logger, records = self._capture("test.lwc3")
        try:
            raise ValueError("kaboom")
        except ValueError:
            log_error(logger, "failed")
        assert records[0].levelno == logging.ERROR
        assert records[0].exc_info is not None

    def test_log_error_accepts_explicit_exc_info(self):
        # Regression: the old hardcoded exc_info=True raised
        # "got multiple values for keyword argument 'exc_info'".
        logger, records = self._capture("test.lwc4")
        log_error(logger, "failed", exc_info=False)
        # Falsy exc_info is stored verbatim on the record; the contract is
        # simply "no traceback attached".
        assert not records[0].exc_info
