"""Schedule windows: the display's on/off schedule and per-plugin schedules.

A schedule is the dict config.json keeps under ``schedule``::

    {"enabled": true, "mode": "global" | "per-day",
     "start_time": "07:00", "end_time": "23:00",
     "days": {"monday": {"enabled": true, "start_time": ..., "end_time": ...}, ...}}

``config['plugin_schedules']`` maps a plugin id to one of these, the window in
which that plugin may take a turn on the panel, plus ``hide_when_live``: hide
the plugin while any plugin reports a live game ("no news or stocks on
gameday"). A plugin without an entry always shows. Hiding is a skip at
display time, never the plugin's ``enabled`` flag: flipping that unloads and
reloads the plugin and throws away its data.
"""

import logging
from datetime import datetime, time
from typing import Any, FrozenSet, Mapping, Tuple

logger = logging.getLogger(__name__)


def in_window(start: time, end: time, now: time) -> bool:
    """Whether ``now`` is within [start, end), a window that may span midnight.

    Half-open: on from the start minute, off at exactly the end minute.
    A closed end made the end minute count as inside, and because ``now``
    carries seconds, only a check at hh:mm:00.000 saw it that way -- so
    whether the panel went off at the start or the end of that minute
    depended on when the minute's one check ran. ``start == end`` is an
    empty window, as it effectively was before.
    """
    if start <= end:
        return start <= now < end
    return now >= start or now < end


def schedule_state(schedule: Mapping[str, Any], now: datetime) -> Tuple[bool, str]:
    """Whether ``now`` is inside ``schedule``, and a phrase saying why.

    No schedule, or ``enabled: false``, is always inside; a schedule without
    an ``enabled`` key counts as enabled. ``mode`` "global" ignores ``days``;
    "per-day" (or "per_day") uses today's entry and falls back to the global
    times when today has none; with no ``mode`` (older configs) a ``days``
    entry for today wins. A disabled day is outside all day. A time that does
    not parse is logged and counts as inside, so a bad edit never blanks the
    panel.
    """
    if not schedule or not schedule.get('enabled', True):
        return True, 'no schedule'

    day = now.strftime('%A').lower()
    days = schedule.get('days') or {}
    mode = schedule.get('mode')
    mode = mode.replace('_', '-') if mode else None
    # An explicit Global mode beats a stray 'days' dict left from a past
    # per-day setup.
    per_day = mode != 'global' and day in days

    if per_day:
        day_config = days[day]
        if not day_config.get('enabled', True):
            return False, f'{day} is disabled'
        start_str = day_config.get('start_time', '07:00')
        end_str = day_config.get('end_time', '23:00')
        kind = f'per-day ({day})'
    else:
        start_str = schedule.get('start_time', '07:00')
        end_str = schedule.get('end_time', '23:00')
        kind = 'global'

    try:
        start = datetime.strptime(start_str, '%H:%M').time()
        end = datetime.strptime(end_str, '%H:%M').time()
    except (TypeError, ValueError) as e:
        logger.warning("Invalid %s schedule: %s (start: %s, end: %s). Treating as inside.",
                       kind, e, start_str, end_str)
        return True, f'invalid {kind} schedule'
    return in_window(start, end, now.time()), f'{kind} window {start_str} - {end_str}'


def wants_live_scan(plugin_schedules: Mapping[str, Any]) -> bool:
    """Whether any plugin hides during live games, so live content is worth asking about."""
    return any(isinstance(s, Mapping) and s.get('hide_when_live')
               for s in plugin_schedules.values())


def hidden_plugins(plugin_schedules: Mapping[str, Any], now: datetime,
                   game_live: bool) -> FrozenSet[str]:
    """The plugin ids that may not show at ``now``.

    A plugin is hidden outside its schedule window, or while ``game_live``
    when its entry sets ``hide_when_live``.
    """
    return frozenset(
        plugin_id for plugin_id, schedule in plugin_schedules.items()
        if isinstance(schedule, Mapping)
        and ((game_live and schedule.get('hide_when_live'))
             or not schedule_state(schedule, now)[0]))
