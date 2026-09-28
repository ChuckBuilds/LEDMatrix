"""BasePlugin.get_vegas_render_width reads display_manager.width first.

CLAUDE.md asks plugins to size themselves from ``display_manager.width`` --
not ``display_manager.matrix.width`` -- because ``matrix`` is None when
hardware init fails and the property falls back to the canvas size. The base
class's own helper read ``matrix.width`` first. For the real DisplayManager
the two agree whenever there is a matrix, so this pins the documented order
with a display manager where they differ.
"""

from types import SimpleNamespace

from src.plugin_system.base_plugin import BasePlugin


class _Plugin(BasePlugin):
    def update(self):
        pass

    def display(self, force_clear=False):
        pass


def _plugin(display_manager):
    plugin = object.__new__(_Plugin)
    plugin.display_manager = display_manager
    return plugin


def test_prefers_display_manager_width_over_matrix_width():
    dm = SimpleNamespace(width=64, matrix=SimpleNamespace(width=128))
    assert _plugin(dm).get_vegas_render_width() == 64


def test_uses_display_manager_width_when_matrix_is_none():
    dm = SimpleNamespace(width=96, matrix=None)
    assert _plugin(dm).get_vegas_render_width() == 96


def test_falls_back_to_matrix_width_without_a_width():
    dm = SimpleNamespace(matrix=SimpleNamespace(width=80))
    assert _plugin(dm).get_vegas_render_width() == 80


def test_vegas_request_still_wins():
    plugin = _plugin(SimpleNamespace(width=192, matrix=None))
    plugin._vegas_render_width = 100
    assert plugin.get_vegas_render_width() == 100
