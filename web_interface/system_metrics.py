"""System metrics for the status stream.

Deliberately free of Flask and app imports: importing web_interface.app
constructs the Flask application and a CacheManager, and the latter claims the
cache directory with a cleanup thread. Reading a CPU percentage should not do
that, and neither should testing it.

Every value is best-effort. A number that cannot be taken comes back as None so
the UI can render '--', because a confident wrong number is worse than a blank:
``disk_used_percent`` used to be hardcoded to 0, which read as "plenty of room"
on a card that was filling up.
"""

from typing import Any, Dict, Optional

_THERMAL_ZONE = '/sys/class/thermal/thermal_zone0/temp'


def _cpu_temp_c() -> Optional[float]:
    """CPU temperature in degrees C, or None off a Raspberry Pi."""
    try:
        with open(_THERMAL_ZONE, 'r') as f:
            return round(float(f.read()) / 1000.0, 1)
    except (OSError, ValueError):
        return None


def collect_system_metrics() -> Dict[str, Any]:
    """Collect the numbers that predict trouble on a small board.

    ``memory_available_mb`` is MemAvailable rather than a used percentage: it
    accounts for reclaimable page cache, so it is what separates a board at 70%
    "used" that is fine from one at 70% that is about to fail fork(). A 1GB Pi
    can sit at either and only this number tells them apart.
    """
    try:
        import psutil
    except ImportError:
        return {
            'cpu_percent': 0,
            'memory_used_percent': 0,
            'memory_available_mb': None,
            'disk_used_percent': None,
            'cpu_temp': _cpu_temp_c() or 0,
        }

    # interval=None is non-blocking; app startup primes psutil's internal state.
    cpu_percent = round(psutil.cpu_percent(interval=None), 1)
    memory = psutil.virtual_memory()

    try:
        disk_used_percent: Optional[float] = round(psutil.disk_usage('/').percent, 1)
    except OSError:
        disk_used_percent = None

    return {
        'cpu_percent': cpu_percent,
        'memory_used_percent': round(memory.percent, 1),
        'memory_available_mb': round(memory.available / (1024 * 1024), 1),
        'disk_used_percent': disk_used_percent,
        'cpu_temp': _cpu_temp_c() or 0,
    }
