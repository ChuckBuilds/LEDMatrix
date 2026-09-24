"""StreamManager logs one INFO line per plugin-list refresh.

The refresh runs at each cycle start and every 30 seconds. It used to write
decorative "=" * 60 banners plus a line per plugin ("INCLUDED", "SKIPPED"),
and each fetch added its own banners and "FETCHING CONTENT" / "SEGMENT
CREATED" lines, all at INFO: dozens of journal lines a minute on a Pi, burying
anything that mattered. Per-plugin detail belongs at DEBUG.
"""

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

from PIL import Image

from src.plugin_system.base_plugin import VegasDisplayMode
from src.vegas_mode.config import VegasModeConfig
from src.vegas_mode.stream_manager import StreamManager

LOGGER = "src.vegas_mode.stream_manager"


class _Plugin:
    def __init__(self, enabled=True, mode=VegasDisplayMode.SCROLL):
        self.enabled = enabled
        self._mode = mode

    def get_vegas_display_mode(self):
        return self._mode


def _stream(plugins):
    adapter = MagicMock()
    adapter.get_content_type.return_value = 'multi'
    adapter.get_content.return_value = [Image.new('RGB', (20, 8))]
    return StreamManager(VegasModeConfig(), SimpleNamespace(plugins=plugins), adapter)


def _info(caplog):
    return [r for r in caplog.records
            if r.name == LOGGER and r.levelno == logging.INFO]


def test_a_refresh_logs_a_single_info_summary(caplog):
    stream = _stream({
        'clock': _Plugin(),
        'weather': _Plugin(),
        'off': _Plugin(enabled=False),
    })
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        stream._refresh_plugin_list()

    info = _info(caplog)
    assert len(info) == 1, [r.getMessage() for r in info]
    summary = info[0].getMessage()
    assert 'clock' in summary and 'weather' in summary
    assert 'off' not in summary.split(':', 1)[1]
    # The per-plugin decisions are still there for anyone at DEBUG.
    debug = [r.getMessage() for r in caplog.records if r.levelno == logging.DEBUG]
    assert any('off' in m and 'not enabled' in m for m in debug)


def test_fetching_content_logs_nothing_at_info(caplog):
    stream = _stream({'clock': _Plugin()})
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        segment = stream._fetch_plugin_content('clock')

    assert segment is not None
    assert _info(caplog) == []


def test_no_decorative_banners(caplog):
    stream = _stream({'clock': _Plugin(), 'static': _Plugin(mode=VegasDisplayMode.STATIC)})
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        stream._refresh_plugin_list()
        stream._fetch_plugin_content('clock')
        stream._fetch_plugin_content('static')

    assert not any(set(r.getMessage()) == {'='} for r in caplog.records)
