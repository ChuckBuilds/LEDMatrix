"""Timezone resolution for the scoreboard plugins.

Game start times arrive from ESPN in UTC and have to be converted to the
user's local zone before they are drawn. This module owns the "which zone?"
decision so every part of a scoreboard (its scorebug, its scroll-mode game
card and its plugin manager) agrees.

The scoreboards each carried a copy of this module as ``<sport>_timezone.py``.
The copies were identical apart from two per-plugin values, which are
keyword-only arguments here: ``plugin_label``, the name the final warning
tells the user to open, and ``writeback_fixed_in`` (see below).

Resolution order, first valid wins:

1. ``timezone`` in the plugin's own config (explicit per-plugin override)
2. The LEDMatrix global timezone via ``plugin_manager.config_manager``
3. The LEDMatrix global timezone via ``cache_manager.config_manager``
4. The host system's zone (``TZ``, ``/etc/timezone``, ``/etc/localtime``)
5. UTC

Steps 2 and 3 matter because the core does not consistently hang
``config_manager`` off both objects -- reading only one of them is what made
a scoreboard fall through to UTC while the clock plugin (which checks
``plugin_manager`` first) showed the right time on the same device. Step 4 is
the backstop for cores that expose no ``config_manager`` at all: a Pi with its
system clock set correctly should never end up rendering UTC.

Two traps this module exists to avoid, both of which render every start time
in UTC on a correctly-configured device:

* **The stale ``"UTC"`` artifact.** Some plugins once wrote
  ``"timezone": "UTC"`` into the *saved* config whenever resolution failed, and
  that write-back stuck -- thereafter shadowing the real global timezone. Such
  a plugin passes ``writeback_fixed_in`` (the release that fixed it), and step 1
  then treats a bare ``"UTC"`` as suspect: it is honored only when nothing
  downstream disagrees. ``Etc/UTC`` is the unambiguous spelling for "I really
  do want UTC"; the bug never produced it, so it is always honored. A plugin
  that never had the bug leaves ``writeback_fixed_in`` as ``None``, and its
  plugin-level ``"UTC"`` is honored verbatim.
* **``get_timezone()``'s own default.** The core's
  ``ConfigManager.get_timezone()`` is ``self.config.get('timezone', 'UTC')``, so
  it hands back ``"UTC"`` for a config that simply has no ``timezone`` key.
  Steps 2 and 3 read the raw config dict instead, so an absent key falls
  through to the system zone rather than latching onto that default.
"""

import logging
import os
from typing import Any, Dict, Iterable, Optional, Tuple

import pytz

logger = logging.getLogger(__name__)


def _from_config_manager(config_manager: Any, log: logging.Logger) -> Optional[str]:
    """Pull the global timezone out of a core ConfigManager, if it has one.

    Reads the raw config dict in preference to ``get_timezone()``. The core's
    ``ConfigManager.get_timezone()`` is ``self.config.get('timezone', 'UTC')`` --
    it substitutes its own ``"UTC"`` when the key is absent, which is
    indistinguishable from the user deliberately choosing UTC. Taking that at
    face value would mask a missing global setting and stop resolution ever
    reaching the host system zone. So: if the raw config is readable and has no
    ``timezone`` key, report "nothing here" and let the caller fall through.
    ``get_timezone()`` is only consulted for cores that expose no raw config.
    """
    if config_manager is None:
        return None

    raw_readable = False
    for loader_name in ("get_config", "load_config"):
        loader = getattr(config_manager, loader_name, None)
        if not callable(loader):
            continue
        try:
            main_config = loader()
        except Exception:
            log.debug("config_manager.%s() failed", loader_name, exc_info=True)
            continue
        if isinstance(main_config, dict):
            raw_readable = True
            name: Optional[str] = main_config.get("timezone")
            if name:
                return name

    if raw_readable:
        return None

    getter = getattr(config_manager, "get_timezone", None)
    if callable(getter):
        try:
            name = getter()
            if name:
                return name
        except Exception:
            log.debug("config_manager.get_timezone() failed", exc_info=True)

    return None


def system_timezone_name() -> Optional[str]:
    """Best-effort IANA name for the host's configured timezone."""
    name = os.environ.get("TZ")
    if name:
        return name

    # Debian / Raspberry Pi OS record the zone name here.
    try:
        with open("/etc/timezone", "r", encoding="utf-8") as handle:
            name = handle.read().strip()
        if name:
            return name
    except OSError:
        pass

    # Otherwise /etc/localtime is a symlink into the zoneinfo tree.
    try:
        path = os.path.realpath("/etc/localtime")
        marker = "zoneinfo" + os.sep
        if marker in path:
            return path.split(marker, 1)[1]
    except OSError:
        pass

    return None


def _validated(name: object, source: str, log: logging.Logger) -> Optional[str]:
    """Return a usable IANA name, or None if blank/absent/not a real zone."""
    if not isinstance(name, str):
        return None
    name = name.strip()
    if not name:
        return None
    try:
        pytz.timezone(name)
    except pytz.UnknownTimeZoneError:
        log.warning("Ignoring invalid timezone %r from %s", name, source)
        return None
    except Exception:
        # Not an unknown-zone error, so something else went wrong inside pytz.
        # Log it loudly rather than silently reclassifying it as "invalid" --
        # but still don't propagate: this runs in the render path, and a
        # mislabelled zone beats taking the whole display down.
        log.warning(
            "Unexpected error validating timezone %r from %s; ignoring it",
            name, source, exc_info=True,
        )
        return None
    return name


def resolve_timezone_name(
    config: Optional[Dict[str, Any]] = None,
    plugin_manager: Any = None,
    cache_manager: Any = None,
    log: Optional[logging.Logger] = None,
    *,
    plugin_label: str,
    writeback_fixed_in: Optional[str] = None,
) -> str:
    """Return the IANA timezone name to render game times in.

    Never raises and never returns an empty string; falls back to ``"UTC"``
    only when every source is missing or invalid.

    ``plugin_label`` names the plugin in the warning logged when nothing
    resolves (e.g. ``"hockey scoreboard"``). ``writeback_fixed_in`` is the
    plugin release that stopped writing ``"UTC"`` back into the saved config,
    for a plugin that ever did; ``None`` (the default) otherwise.
    """
    log = log or logger

    def downstream():
        """Yield (source, name) for everything except the plugin's own config.

        Lazy: ``SportsCore._get_timezone()`` runs this once per game and the
        answer is almost always already in the plugin config, so evaluating on
        demand keeps the common case from calling into both config managers and
        stat-ing the host timezone files every time.
        """
        yield (
            "plugin_manager.config_manager",
            _from_config_manager(getattr(plugin_manager, "config_manager", None), log),
        )
        yield (
            "cache_manager.config_manager",
            _from_config_manager(getattr(cache_manager, "config_manager", None), log),
        )
        yield "system timezone", system_timezone_name()

    def first_valid(
        sources: Iterable[Tuple[str, Any]],
    ) -> Tuple[Optional[str], Optional[str]]:
        for source, name in sources:
            name = _validated(name, source, log)
            if name:
                return source, name
        return None, None

    plugin_value = _validated((config or {}).get("timezone"), "plugin config", log)

    if writeback_fixed_in is not None and plugin_value and plugin_value.lower() == "utc":
        # Before writeback_fixed_in this plugin wrote "timezone": "UTC" into
        # the saved config whenever it failed to resolve a global timezone, and
        # that write-back persisted. A bare "UTC" is therefore far more likely
        # to be that artifact than a deliberate choice -- it only ever appeared
        # on failure. Honor it only when nothing downstream disagrees; a user
        # who genuinely wants UTC writes the unambiguous "Etc/UTC", which the
        # bug never produced and which falls through to the normal path below.
        source, downstream_name = first_valid(downstream())
        if downstream_name and downstream_name.lower() not in ("utc", "etc/utc"):
            log.warning(
                "Ignoring the plugin-level timezone 'UTC': it is almost "
                "certainly left over from the write-back bug fixed in %s, and "
                "%s says %s. Using %s. If you really do want UTC here, set "
                "this plugin's timezone to 'Etc/UTC' instead.",
                writeback_fixed_in, source, downstream_name, downstream_name,
            )
            return downstream_name
        log.debug("Plugin-level timezone 'UTC' agrees with %s; using UTC", source or "no other source")
        return "UTC"

    if plugin_value:
        log.debug("Resolved timezone %s from plugin config", plugin_value)
        return plugin_value

    source, name = first_valid(downstream())
    if name:
        log.debug("Resolved timezone %s from %s", name, source)
        return name

    log.warning(
        "Could not determine a timezone from the plugin config, the LEDMatrix "
        "config or the system; game times will be shown in UTC. Set a timezone "
        "in the %s's Advanced Settings to override.",
        plugin_label,
    )
    return "UTC"


def resolve_timezone(
    config: Optional[Dict[str, Any]] = None,
    plugin_manager: Any = None,
    cache_manager: Any = None,
    log: Optional[logging.Logger] = None,
    *,
    plugin_label: str,
    writeback_fixed_in: Optional[str] = None,
):
    """``resolve_timezone_name`` as a ready-to-use tzinfo object."""
    return pytz.timezone(
        resolve_timezone_name(
            config=config,
            plugin_manager=plugin_manager,
            cache_manager=cache_manager,
            log=log,
            plugin_label=plugin_label,
            writeback_fixed_in=writeback_fixed_in,
        )
    )
