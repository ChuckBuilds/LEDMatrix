"""Tests for live plugin enable/disable hot-reload in DisplayController.

Enabling or disabling a plugin in config used to require a full display
restart because the plugin list and available_modes were built once at init.
These tests cover the reconcile path that loads/unloads plugins and rebuilds
the dispatch maps on the main thread when the enabled set changes.
"""

from unittest.mock import MagicMock


def _make_plugin(modes):
    plugin = MagicMock()
    plugin.modes = list(modes)
    return plugin


def _wire_plugin_manager(controller, plugins, discovered=None):
    """Point the controller's mock plugin_manager at a set of fake plugins.

    `plugins` maps plugin_id -> mock instance (with a .modes list).
    """
    pm = controller.plugin_manager
    pm.discover_plugins.return_value = list(discovered if discovered is not None else plugins.keys())
    pm.load_plugin.return_value = True
    pm.unload_plugin.return_value = True
    pm.plugin_manifests = {}
    pm.get_plugin.side_effect = lambda pid: plugins.get(pid)
    return pm


def _set_config(controller, cfg):
    controller.config_service.get_config = lambda: cfg


class TestPluginEnableDisableHotReload:
    def test_enable_plugin_live(self, test_display_controller):
        controller = test_display_controller
        assert controller.available_modes == []

        plugin = _make_plugin(["foo"])
        _wire_plugin_manager(controller, {"foo": plugin})
        _set_config(controller, {"foo": {"enabled": True}})

        controller._reconcile_enabled_plugins()

        assert "foo" in controller.plugin_display_modes
        assert "foo" in controller.available_modes
        assert controller.plugin_modes["foo"] is plugin
        assert controller.mode_to_plugin_id["foo"] == "foo"
        controller.plugin_manager.load_plugin.assert_any_call("foo")

    def test_disable_plugin_live(self, test_display_controller):
        controller = test_display_controller
        plugin = _make_plugin(["live", "recent"])
        _wire_plugin_manager(controller, {"sports": plugin}, discovered=["sports"])

        # Enable, then disable.
        _set_config(controller, {"sports": {"enabled": True}})
        controller._reconcile_enabled_plugins()
        assert "sports" in controller.plugin_display_modes
        assert "live" in controller.available_modes and "recent" in controller.available_modes
        assert "sports" in controller._plugin_config_callbacks

        _set_config(controller, {"sports": {"enabled": False}})
        controller._reconcile_enabled_plugins()

        assert "sports" not in controller.plugin_display_modes
        assert "live" not in controller.available_modes
        assert "recent" not in controller.available_modes
        assert "live" not in controller.plugin_modes
        assert "recent" not in controller.mode_to_plugin_id
        controller.plugin_manager.unload_plugin.assert_any_call("sports")
        assert "sports" not in controller._plugin_config_callbacks

    def test_disable_clamps_current_mode_index(self, test_display_controller):
        controller = test_display_controller
        p1 = _make_plugin(["a"])
        p2 = _make_plugin(["b"])
        _wire_plugin_manager(controller, {"p1": p1, "p2": p2}, discovered=["p1", "p2"])

        _set_config(controller, {"p1": {"enabled": True}, "p2": {"enabled": True}})
        controller._reconcile_enabled_plugins()
        # Add order across multiple plugins is set-driven (as at init), so
        # compare membership, not order.
        assert set(controller.available_modes) == {"a", "b"}

        # Pretend we're currently showing p2's mode.
        controller.current_mode_index = controller.available_modes.index("b")
        controller.current_display_mode = "b"

        _set_config(controller, {"p1": {"enabled": True}, "p2": {"enabled": False}})
        controller._reconcile_enabled_plugins()

        assert controller.available_modes == ["a"]
        # Index must be back in range and the display mode no longer the removed one.
        assert 0 <= controller.current_mode_index < len(controller.available_modes)
        assert controller.current_display_mode == "a"

    def test_enable_keeps_current_mode(self, test_display_controller):
        controller = test_display_controller
        p1 = _make_plugin(["a"])
        p2 = _make_plugin(["b"])
        _wire_plugin_manager(controller, {"p1": p1, "p2": p2}, discovered=["p1", "p2"])

        _set_config(controller, {"p1": {"enabled": True}})
        controller._reconcile_enabled_plugins()
        controller.current_mode_index = 0
        controller.current_display_mode = "a"

        # Enabling p2 should not disturb the currently-showing mode.
        _set_config(controller, {"p1": {"enabled": True}, "p2": {"enabled": True}})
        controller._reconcile_enabled_plugins()

        assert "b" in controller.available_modes
        assert controller.current_display_mode == "a"
        assert controller.available_modes[controller.current_mode_index] == "a"

    def test_noop_when_enabled_set_unchanged(self, test_display_controller):
        controller = test_display_controller
        plugin = _make_plugin(["foo"])
        _wire_plugin_manager(controller, {"foo": plugin}, discovered=["foo"])
        _set_config(controller, {"foo": {"enabled": True}})
        controller._reconcile_enabled_plugins()

        load_calls = controller.plugin_manager.load_plugin.call_count
        unload_calls = controller.plugin_manager.unload_plugin.call_count

        # Reconcile again with no change — must not load/unload anything.
        controller._reconcile_enabled_plugins()
        assert controller.plugin_manager.load_plugin.call_count == load_calls
        assert controller.plugin_manager.unload_plugin.call_count == unload_calls

    def test_reconcile_ignores_non_dict_config_value(self, test_display_controller):
        """A malformed config value (e.g. a stray string where a plugin's
        section should be a dict) must be treated as disabled, not crash
        the reconcile with AttributeError."""
        controller = test_display_controller
        plugin = _make_plugin(["foo"])
        _wire_plugin_manager(controller, {"foo": plugin}, discovered=["foo"])
        _set_config(controller, {"foo": "not-a-dict"})

        controller._reconcile_enabled_plugins()  # must not raise

        assert "foo" not in controller.plugin_display_modes
        assert "foo" not in controller.available_modes


class TestRunWithNoModesEnabled:
    """Before hot-reload, an empty available_modes at startup was permanent
    -- the display never came back without a restart. Now that a plugin can
    be enabled live from the web UI, run() must idle rather than exit."""

    def test_idles_instead_of_exiting(self, test_display_controller):
        controller = test_display_controller
        assert controller.available_modes == []

        sleep_calls = []

        def fake_sleep(duration, tick_interval=1.0):
            sleep_calls.append(duration)
            if len(sleep_calls) >= 3:
                # Stand in for the process being torn down; run() catches
                # this via its broad except + finally, same as any other
                # unexpected error during the loop.
                raise RuntimeError("stop-test-loop")

        controller._sleep_with_plugin_updates = fake_sleep

        controller.run()

        # Old behavior returned before ever reaching the loop body, so
        # _sleep_with_plugin_updates would never have been called.
        assert sleep_calls == [30, 30, 30]


class TestEnabledSetChanged:
    def test_detects_toggle(self, test_display_controller):
        c = test_display_controller
        assert c._enabled_set_changed({"a": {"enabled": True}}, {"a": {"enabled": False}}) is True

    def test_no_change(self, test_display_controller):
        c = test_display_controller
        cfg = {"a": {"enabled": True}, "b": {"enabled": False}}
        assert c._enabled_set_changed(cfg, dict(cfg)) is False

    def test_new_enabled_section(self, test_display_controller):
        c = test_display_controller
        assert c._enabled_set_changed(
            {"a": {"enabled": True}},
            {"a": {"enabled": True}, "b": {"enabled": True}},
        ) is True

    def test_ignores_non_enabled_value_edits(self, test_display_controller):
        c = test_display_controller
        assert c._enabled_set_changed(
            {"a": {"enabled": True, "duration": 30}},
            {"a": {"enabled": True, "duration": 45}},
        ) is False
