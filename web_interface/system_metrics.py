"""System metrics for the status stream and GET /api/v3/system/status.

Deliberately free of Flask and app imports: importing web_interface.app
constructs the Flask application and a CacheManager, and the latter claims the
cache directory with a cleanup thread. Reading a CPU percentage should not do
that, and neither should testing it.

Every value is best-effort. A number that cannot be taken comes back as None so
the UI can render '--', because a confident wrong number is worse than a blank:
``disk_used_percent`` used to be hardcoded to 0, which read as "plenty of room"
on a card that was filling up.
"""

import time
from typing import Any, Dict, Optional

_THERMAL_ZONE = '/sys/class/thermal/thermal_zone0/temp'
_MB = 1024 * 1024
_GB = 1024 * 1024 * 1024

#: Every key collect_system_metrics() returns.
METRIC_KEYS = (
    'cpu_percent', 'cpu_temp',
    'memory_used_percent', 'memory_total_mb', 'memory_used_mb', 'memory_available_mb',
    'disk_used_percent', 'disk_total_gb', 'disk_used_gb',
    'uptime_seconds',
)


def _cpu_temp_c() -> Optional[float]:
    """CPU temperature in degrees C, or None off a Raspberry Pi."""
    try:
        with open(_THERMAL_ZONE, 'r') as f:
            return round(float(f.read()) / 1000.0, 1)
    except (OSError, ValueError):
        return None


def collect_system_metrics(cpu_interval: Optional[float] = None) -> Dict[str, Any]:
    """Collect the numbers that predict trouble on a small board.

    ``cpu_interval`` is passed to ``psutil.cpu_percent``. None does not block
    and measures since the previous call (app startup primes it); a request
    that may be the first call in its process passes a short interval instead.

    ``memory_available_mb`` is MemAvailable rather than a used percentage: it
    accounts for reclaimable page cache, so it is what separates a board at 70%
    "used" that is fine from one at 70% that is about to fail fork(). A 1GB Pi
    can sit at either and only this number tells them apart.
    """
    metrics: Dict[str, Any] = dict.fromkeys(METRIC_KEYS)
    metrics['cpu_temp'] = _cpu_temp_c()
    try:
        import psutil
    except ImportError:
        return metrics

    metrics['cpu_percent'] = round(psutil.cpu_percent(interval=cpu_interval), 1)

    memory = psutil.virtual_memory()
    metrics['memory_used_percent'] = round(memory.percent, 1)
    metrics['memory_total_mb'] = round(memory.total / _MB, 1)
    metrics['memory_used_mb'] = round(memory.used / _MB, 1)
    metrics['memory_available_mb'] = round(memory.available / _MB, 1)

    try:
        disk = psutil.disk_usage('/')
    except OSError:
        pass
    else:
        metrics['disk_used_percent'] = round(disk.percent, 1)
        metrics['disk_total_gb'] = round(disk.total / _GB, 1)
        metrics['disk_used_gb'] = round(disk.used / _GB, 1)

    metrics['uptime_seconds'] = int(time.time() - psutil.boot_time())
    return metrics


def format_uptime(uptime_seconds: Optional[float]) -> Optional[str]:
    """'3d 4h', '5h 12m' or '42m'; None when the uptime is unknown."""
    if uptime_seconds is None:
        return None
    hours = uptime_seconds / 3600
    if hours >= 24:
        return f"{int(hours / 24)}d {int(hours % 24)}h"
    if hours >= 1:
        return f"{int(hours)}h {int((uptime_seconds % 3600) / 60)}m"
    return f"{int(uptime_seconds / 60)}m"
