"""The core's own tuning keys must not make a plugin look broken.

`vegas_width_pct`, `vegas_overflow` and `vegas_max_width_screens` are read by
the *core* out of each plugin's config block — `vegas_mode/plugin_adapter.py`
and `base_plugin.py`. No plugin declares them, and 37 of the 42 published
config schemas set `"additionalProperties": false`, so schema validation
reported them as violations.

That is not just log noise: `_validate_config_schema_soft` sets `degraded` in
the health tracker, which the web UI surfaces. Measured on a real device, **9
of 27 installed plugins** were flagged degraded purely for using a documented
core feature — including `baseball-scoreboard` and `f1-scoreboard`.

The fix strips those keys before validating. It deliberately does *not* match
on a `vegas_` prefix: `vegas_mode` is plugin-owned and declared in schemas, and
a prefix rule would silently stop validating it.
"""

from unittest.mock import MagicMock

import pytest

from src.plugin_system.plugin_manager import PluginManager


STRICT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "enabled": {"type": "boolean"},
        "vegas_mode": {"type": "string"},  # plugin-owned, must stay validated
    },
}


@pytest.fixture
def manager():
    mgr = PluginManager.__new__(PluginManager)  # skip the heavy constructor
    mgr.logger = MagicMock()
    mgr.schema_manager = MagicMock()
    mgr._set_degraded_safe = MagicMock()
    return mgr


class TestStripCoreOwnedKeys:
    def test_removes_every_core_owned_key(self, manager):
        cfg = {"enabled": True, "vegas_width_pct": 50,
               "vegas_overflow": "wrap", "vegas_max_width_screens": 2}
        assert manager._strip_core_owned_keys(cfg) == {"enabled": True}

    def test_leaves_plugin_owned_vegas_mode_alone(self, manager):
        """A prefix rule would have eaten this one."""
        cfg = {"enabled": True, "vegas_mode": "scroll"}
        assert manager._strip_core_owned_keys(cfg) == cfg

    def test_returns_the_same_object_when_nothing_to_strip(self, manager):
        cfg = {"enabled": True}
        assert manager._strip_core_owned_keys(cfg) is cfg

    def test_does_not_mutate_the_caller_config(self, manager):
        cfg = {"enabled": True, "vegas_width_pct": 50}
        manager._strip_core_owned_keys(cfg)
        assert "vegas_width_pct" in cfg, "the live plugin config was mutated"

    def test_tolerates_a_non_dict(self, manager):
        assert manager._strip_core_owned_keys(None) is None


class TestSoftValidation:
    def _validate_with(self, manager, config, valid=True, errors=()):
        manager.schema_manager.load_schema.return_value = STRICT_SCHEMA
        manager.schema_manager.validate_config_against_schema.return_value = (
            valid, list(errors))
        manager._validate_config_schema_soft("baseball-scoreboard", config)
        return manager.schema_manager.validate_config_against_schema.call_args

    def test_core_keys_never_reach_the_validator(self, manager):
        """The regression: these keys reaching a strict schema is what flagged
        9 of 27 plugins degraded."""
        args = self._validate_with(
            manager, {"enabled": True, "vegas_width_pct": 50})
        validated = args[0][0]
        assert "vegas_width_pct" not in validated
        assert validated == {"enabled": True}

    def test_plugin_owned_keys_still_reach_the_validator(self, manager):
        args = self._validate_with(
            manager, {"enabled": True, "vegas_mode": "scroll"})
        assert args[0][0]["vegas_mode"] == "scroll"

    def test_a_genuine_violation_is_still_reported(self, manager):
        """Stripping core keys must not turn the check into a no-op."""
        self._validate_with(
            manager, {"enabled": True, "typo_key": 1},
            valid=False, errors=["Field root: 'typo_key' was unexpected"])
        manager._set_degraded_safe.assert_called()
        reason = manager._set_degraded_safe.call_args[0][1]
        assert reason and "typo_key" in reason
