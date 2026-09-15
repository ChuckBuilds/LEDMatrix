"""Which matrix settings the pinned rgbmatrix library can drive on a Raspberry Pi 5.

On a Pi 5 the library drives the panel through the RP1 chip, and at the pinned
rpi-rgb-led-matrix-master commit (1ee4f76) that path supports only some
settings -- ``Rp1PioConfigSupported()`` in ``lib/rp1/rp1_pio_backend.cc``. For
anything else ``RGBMatrix::CreateFromOptions()`` returns NULL. The Python
binding does not check for that, so instead of raising, the display process
crashes on its first call into the matrix, and systemd restarts it into the
same crash every 10 seconds.

``DisplayManager`` checks this rule before creating the matrix, turning that
crash into a logged error and the usual fallback mode. The config API and the
Display form use the same rule to refuse the settings up front.

**Re-check this when the submodule is bumped.** Upstream is extending Pi 5
support (e.g. 4e326c1b, "Pi5 - Improvements, additional led-row-addr-type
support"); a stale rule here would block settings the new library drives.
"""

from typing import Any, Mapping, Optional

#: Read the way the library's ``Rp1PioPlatformDetected()`` reads it.
MODEL_PATH = "/proc/device-tree/model"
PI5_MODEL_MARKERS = ("Raspberry Pi 5", "Compute Module 5")

PI5_ROW_ADDRESS_TYPES = (0, 2)
PI5_MAX_PARALLEL = 3
PI5_HARDWARE_MAPPINGS = ("regular", "regular-pi1", "classic", "adafruit-hat", "adafruit-hat-pwm")


def is_raspberry_pi_5() -> bool:
    """True on a Pi 5-family board: Pi 5, Pi 500 or Compute Module 5."""
    try:
        with open(MODEL_PATH, "rb") as f:
            model = f.read(256).decode("utf-8", "replace")
    except OSError:
        return False
    return any(marker in model for marker in PI5_MODEL_MARKERS)


def _as_int(value: Any, default: int) -> Optional[int]:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def pi5_unsupported_settings(hardware: Mapping[str, Any]) -> Optional[str]:
    """Why a Pi 5 can't drive this ``display.hardware`` config, or None if it can.

    Missing keys take DisplayManager's defaults. A value that isn't a number is
    left to the caller's own validation rather than reported here.
    """
    problems = []
    row_address_type = _as_int(hardware.get("row_address_type"), 0)
    if row_address_type is not None and row_address_type not in PI5_ROW_ADDRESS_TYPES:
        problems.append(f"row address type {row_address_type} (only 0 and 2 are supported)")
    parallel = _as_int(hardware.get("parallel"), 1)
    if parallel is not None and not 1 <= parallel <= PI5_MAX_PARALLEL:
        problems.append(f"parallel {parallel} (1 to {PI5_MAX_PARALLEL} are supported)")
    mapping = hardware.get("hardware_mapping", "adafruit-hat-pwm")
    # The library treats an empty mapping as "regular".
    if isinstance(mapping, str) and (mapping or "regular") not in PI5_HARDWARE_MAPPINGS:
        problems.append(f'hardware mapping "{mapping}"')
    if not problems:
        return None
    return ("Not supported on a Raspberry Pi 5 by the installed rgbmatrix library: "
            + "; ".join(problems) + ".")
