"""src.common.sports_timezone: which zone a scoreboard renders start times in.

Ported from the scoreboard plugins' test_timezone_resolution.py, which test
their bundled ``<sport>_timezone.py`` copies. The copies differ only in the
two values this module takes as keyword arguments, so every test runs once per
plugin with that plugin's values (``PLUGINS``).

Regression the resolution order guards: a plugin used to read the global
timezone only from ``cache_manager.config_manager``. On cores that hang
``config_manager`` off the plugin manager instead, that lookup found nothing
and every start time was drawn in UTC.
"""

import logging
from datetime import datetime

import pytest
import pytz

from src.common import sports_timezone
from src.common.sports_timezone import resolve_timezone, resolve_timezone_name

#: (plugin_label, writeback_fixed_in) as the plugins' copies carried them.
#: Only baseball and football ever wrote "UTC" back into the saved config.
PLUGINS = [
    ("AFL scoreboard", None),
    ("baseball scoreboard", "1.20.0"),
    ("basketball scoreboard", None),
    ("F1 scoreboard", None),
    ("football scoreboard", "2.9.0"),
    ("hockey scoreboard", None),
    ("lacrosse scoreboard", None),
    ("NRL scoreboard", None),
    ("soccer scoreboard", None),
    ("UFC scoreboard", None),
]


@pytest.fixture(params=PLUGINS, ids=[label for label, _ in PLUGINS])
def plugin(request):
    label, fixed_in = request.param
    return {"plugin_label": label, "writeback_fixed_in": fixed_in}


@pytest.fixture
def system_zone(monkeypatch):
    """Stub system-zone detection so results don't depend on this machine."""
    def stub(value):
        monkeypatch.setattr(sports_timezone, "system_timezone_name", lambda: value)
    stub(None)
    return stub


class _ConfigManager:
    """Core ConfigManager stand-in exposing get_timezone()."""

    def __init__(self, timezone=None):
        self._timezone = timezone

    def get_timezone(self):
        return self._timezone


class _LegacyConfigManager:
    """Older core: no get_timezone(), only load_config()."""

    def __init__(self, timezone=None):
        self._timezone = timezone

    def load_config(self):
        return {"timezone": self._timezone}


class _CountingConfigManager(_ConfigManager):
    """Records how many times the core was asked for the timezone."""

    calls = 0

    def get_timezone(self):
        self.calls += 1
        return self._timezone


class _BrokenConfigManager:
    """Core whose get_timezone() blows up -- must not take the plugin down."""

    def get_timezone(self):
        raise RuntimeError("config not loaded")


class _RealCoreConfigManager:
    """Faithful stand-in for the shipping core ConfigManager.

    The real get_timezone() is ``self.config.get('timezone', 'UTC')`` -- it
    substitutes its own "UTC" when the global config has no timezone key.
    """

    def __init__(self, config):
        self._config = config

    def get_config(self):
        return self._config

    def load_config(self):
        return self._config

    def get_timezone(self):
        return self._config.get("timezone", "UTC")


class _Holder:
    """Stands in for a plugin_manager / cache_manager."""

    def __init__(self, config_manager=None):
        if config_manager is not None:
            self.config_manager = config_manager


def test_plugin_config_override_wins(plugin):
    assert resolve_timezone_name(
        config={"timezone": "America/Denver"},
        plugin_manager=_Holder(_ConfigManager("America/New_York")),
        cache_manager=_Holder(_ConfigManager("Europe/London")),
        **plugin,
    ) == "America/Denver"


def test_lower_priority_sources_are_not_evaluated(plugin, monkeypatch):
    """SportsCore._get_timezone() runs per game; once a candidate resolves, the
    remaining sources must not be touched."""
    plugin_cm = _CountingConfigManager("America/New_York")
    cache_cm = _CountingConfigManager("Europe/London")
    system_calls = []
    monkeypatch.setattr(sports_timezone, "system_timezone_name",
                        lambda: system_calls.append(1) or None)
    name = resolve_timezone_name(
        config={"timezone": "America/Chicago"},
        plugin_manager=_Holder(plugin_cm),
        cache_manager=_Holder(cache_cm),
        **plugin,
    )
    assert name == "America/Chicago"
    assert (plugin_cm.calls, cache_cm.calls, system_calls) == (0, 0, [])


def test_plugin_manager_config_manager_is_consulted(plugin, system_zone):
    """The regression: cache_manager has no config_manager at all."""
    assert resolve_timezone_name(
        config={},
        plugin_manager=_Holder(_ConfigManager("America/Chicago")),
        cache_manager=_Holder(),
        **plugin,
    ) == "America/Chicago"


def test_cache_manager_config_manager_fallback(plugin, system_zone):
    assert resolve_timezone_name(
        config={},
        plugin_manager=_Holder(),
        cache_manager=_Holder(_ConfigManager("America/Chicago")),
        **plugin,
    ) == "America/Chicago"


def test_legacy_load_config_fallback(plugin, system_zone):
    assert resolve_timezone_name(
        config={},
        plugin_manager=_Holder(_LegacyConfigManager("America/Chicago")),
        cache_manager=_Holder(),
        **plugin,
    ) == "America/Chicago"


def test_raising_config_manager_falls_through(plugin, system_zone):
    assert resolve_timezone_name(
        config={},
        plugin_manager=_Holder(_BrokenConfigManager()),
        cache_manager=_Holder(_ConfigManager("America/Chicago")),
        **plugin,
    ) == "America/Chicago"


def test_blank_and_invalid_values_are_skipped(plugin, system_zone, caplog):
    with caplog.at_level(logging.WARNING, logger=sports_timezone.__name__):
        name = resolve_timezone_name(
            config={"timezone": "   "},
            plugin_manager=_Holder(_ConfigManager("Not/AZone")),
            cache_manager=_Holder(_ConfigManager("America/Chicago")),
            **plugin,
        )
    assert name == "America/Chicago"
    assert caplog.messages == [
        "Ignoring invalid timezone 'Not/AZone' from plugin_manager.config_manager"]


def test_system_timezone_backstop(plugin, system_zone):
    system_zone("America/Chicago")
    assert resolve_timezone_name(
        config={}, plugin_manager=_Holder(), cache_manager=_Holder(), **plugin,
    ) == "America/Chicago"


def test_utc_last_resort_names_the_plugin(plugin, system_zone, caplog):
    with caplog.at_level(logging.WARNING, logger=sports_timezone.__name__):
        name = resolve_timezone_name(config={}, **plugin)
    assert name == "UTC"
    # Word for word what the plugins' copies logged, with their own name in it.
    assert caplog.messages == [
        "Could not determine a timezone from the plugin config, the LEDMatrix "
        "config or the system; game times will be shown in UTC. Set a timezone "
        f"in the {plugin['plugin_label']}'s Advanced Settings to override."]


def test_log_defaults_to_this_modules_logger_and_honours_a_given_one(plugin, system_zone, caplog):
    own = logging.getLogger("test.sports_timezone.own")
    with caplog.at_level(logging.WARNING):
        resolve_timezone_name(config={}, **plugin)
        resolve_timezone_name(config={}, log=own, **plugin)
    assert [r.name for r in caplog.records] == [sports_timezone.__name__, own.name]


def test_resolve_timezone_returns_tzinfo_and_converts(plugin):
    tz = resolve_timezone(config={"timezone": "America/Chicago"}, **plugin)
    # 2026-07-28 23:45Z is a 6:45pm CDT first pitch -- the exact symptom that
    # started this: a Chicago game rendering as 11:45PM.
    local = datetime(2026, 7, 28, 23, 45, tzinfo=pytz.UTC).astimezone(tz)
    assert local.strftime("%I:%M%p").lstrip("0") == "6:45PM"


def test_plugin_level_utc_when_the_global_config_disagrees(plugin, system_zone, caplog):
    """With the write-back bug, a bare "UTC" is its artifact and is ignored;
    without it, "UTC" can only be the user's own choice and is honored."""
    with caplog.at_level(logging.WARNING, logger=sports_timezone.__name__):
        name = resolve_timezone_name(
            config={"timezone": "UTC"},
            plugin_manager=_Holder(_RealCoreConfigManager({"timezone": "America/Chicago"})),
            cache_manager=_Holder(),
            **plugin,
        )
    fixed_in = plugin["writeback_fixed_in"]
    if fixed_in is None:
        assert name == "UTC"
        assert caplog.messages == []
    else:
        assert name == "America/Chicago"
        assert caplog.messages == [
            "Ignoring the plugin-level timezone 'UTC': it is almost certainly "
            f"left over from the write-back bug fixed in {fixed_in}, and "
            "plugin_manager.config_manager says America/Chicago. Using "
            "America/Chicago. If you really do want UTC here, set this plugin's "
            "timezone to 'Etc/UTC' instead."]


def test_plugin_level_utc_against_the_system_zone(plugin, system_zone):
    system_zone("America/Chicago")
    name = resolve_timezone_name(
        config={"timezone": "UTC"}, plugin_manager=_Holder(), cache_manager=_Holder(),
        **plugin,
    )
    assert name == ("UTC" if plugin["writeback_fixed_in"] is None else "America/Chicago")


def test_utc_is_kept_when_nothing_disagrees(plugin, system_zone):
    """A genuinely-UTC device must not be dragged off UTC."""
    system_zone("UTC")
    assert resolve_timezone_name(
        config={"timezone": "UTC"},
        plugin_manager=_Holder(_RealCoreConfigManager({"timezone": "UTC"})),
        cache_manager=_Holder(),
        **plugin,
    ) == "UTC"


def test_etc_utc_is_always_honored(plugin):
    """The unambiguous opt-in the write-back bug could never have produced."""
    assert resolve_timezone_name(
        config={"timezone": "Etc/UTC"},
        plugin_manager=_Holder(_RealCoreConfigManager({"timezone": "America/Chicago"})),
        cache_manager=_Holder(),
        **plugin,
    ) == "Etc/UTC"


def test_absent_global_key_falls_through_to_system_zone(plugin, system_zone):
    """The core's get_timezone() returns its own 'UTC' default for a config
    with no timezone key; that must not mask the system zone."""
    system_zone("America/Chicago")
    assert resolve_timezone_name(
        config={},
        plugin_manager=_Holder(_RealCoreConfigManager({"display": {}})),
        cache_manager=_Holder(),
        **plugin,
    ) == "America/Chicago"


def test_present_global_key_still_wins_over_system_zone(plugin, system_zone):
    system_zone("America/Denver")
    assert resolve_timezone_name(
        config={},
        plugin_manager=_Holder(_RealCoreConfigManager({"timezone": "America/Chicago"})),
        cache_manager=_Holder(),
        **plugin,
    ) == "America/Chicago"


def test_plugin_label_is_required():
    with pytest.raises(TypeError):
        resolve_timezone_name(config={})  # type: ignore[call-arg]


def test_system_timezone_name_is_a_string_or_none():
    value = sports_timezone.system_timezone_name()
    assert value is None or isinstance(value, str)
