"""Display settings the pinned rgbmatrix library refuses, on every board.

The library does not raise on a setting it can't use. For most it returns no
matrix -- ``RGBMatrix::CreateFromOptions()`` gives back NULL when
``Options::Validate()`` (``lib/options-initialize.cc``) or its GPIO slowdown
check (``lib/led-matrix.cc``) fails -- and the Python binding stores that
pointer without checking, so the display process crashes on its first call
into the matrix. For two it calls ``abort()``: an unknown hardware mapping
name, and more parallel chains than the mapping has outputs
(``Framebuffer::InitHardwareMapping()`` and the ``Framebuffer`` constructor in
``lib/framebuffer.cc``). Either way systemd restarts the service into the same
crash, and no fallback or hardware status is ever reported.

``DisplayManager`` runs :func:`library_refusals` before creating the matrix,
turning those crashes into a logged error and the usual fallback mode. The
config API uses the same rules to refuse the settings up front, and
:data:`INT_SETTING_LIMITS` is the one place the numeric ranges live.

Pi 5-only limits come from :mod:`src.pi5_matrix_support` and are added when
``pi5=True``.

**Re-check this when the submodule is bumped** (pinned at 1ee4f76): the
ranges in ``Options::Validate()``, the mapping table in
``lib/hardware-mapping.c`` and the setter types in
``bindings/python/rgbmatrix/core.pyx``. A stale rule here blocks settings a
new library accepts; a missing one lets the display service crash-loop.
"""

from typing import Any, Dict, List, Mapping, NamedTuple, Optional, Tuple

from src.pi5_matrix_support import pi5_unsupported_settings

#: The binding's setters for rows, chain_length, parallel and the other small
#: integers are declared ``uint8_t`` (``core.pyx``), cols ``uint32_t``, and
#: limit_refresh_rate_hz lands in a C ``int``; a larger value raises
#: ``OverflowError`` before the library sees it.
UINT8_MAX = 255
UINT32_MAX = 2 ** 32 - 1
INT32_MAX = 2 ** 31 - 1

#: field -> (config section, lowest, highest, must be even). The library's own
#: ranges where it has one, otherwise the binding's integer type.
INT_SETTING_LIMITS: Dict[str, Tuple[str, int, int, bool]] = {
    'rows': ('hardware', 8, 64, True),
    'cols': ('hardware', 16, UINT32_MAX, False),
    'chain_length': ('hardware', 1, UINT8_MAX, False),
    # 3 is the most any mapping in the default build has (see MAPPING_OUTPUTS).
    'parallel': ('hardware', 1, 3, False),
    'brightness': ('hardware', 1, 100, False),
    'scan_mode': ('hardware', 0, 1, False),
    'pwm_bits': ('hardware', 1, 11, False),
    'pwm_dither_bits': ('hardware', 0, 2, False),
    'pwm_lsb_nanoseconds': ('hardware', 50, 3000, False),
    # 0 = no cap; the library has no upper bound.
    'limit_refresh_rate_hz': ('hardware', 0, INT32_MAX, False),
    'row_address_type': ('hardware', 0, 5, False),
    # 22 registered multiplexers (lib/multiplex-mappers.cc).
    'multiplexing': ('hardware', 0, 22, False),
    'gpio_slowdown': ('runtime', 0, 10, False),
    'rp1_rio': ('runtime', 0, 1, False),
}

#: Hardware mapping name -> parallel chains it has outputs for, as
#: ``lib/hardware-mapping.c`` defines them. ``compute-module`` exists only when
#: the library is built with ENABLE_WIDE_GPIO_COMPUTE_MODULE, which the pinned
#: Makefile leaves commented out and the installer does not set, so the library
#: LEDMatrix installs aborts on it like any other unknown name.
MAPPING_OUTPUTS: Dict[str, int] = {
    'regular': 3,
    'adafruit-hat': 1,
    'adafruit-hat-pwm': 1,
    'regular-pi1': 1,
    'classic': 3,
    'classic-pi1': 1,
}

#: What DisplayManager passes when a key is missing from display.hardware /
#: display.runtime. Config migration normally fills these from
#: config/config.template.json first, so they rarely apply.
DISPLAY_MANAGER_DEFAULTS: Dict[str, Any] = {
    'rows': 32, 'cols': 64, 'chain_length': 2, 'parallel': 1,
    'hardware_mapping': 'adafruit-hat-pwm', 'brightness': 90, 'pwm_bits': 10,
    'pwm_lsb_nanoseconds': 150, 'led_rgb_sequence': 'RGB',
    'row_address_type': 0, 'multiplexing': 0, 'limit_refresh_rate_hz': 90,
    'gpio_slowdown': 3,
}


class Refusal(NamedTuple):
    """One reason the library can't start with a config.

    ``fields`` are the settings involved; the config API reports a refusal
    only when the request sets one of them, so a problem already stored
    doesn't block an unrelated save.
    """
    fields: Tuple[str, ...]
    message: str
    #: A Raspberry Pi 5-only limit, whose message is already a full sentence.
    pi5: bool = False


class MatrixSettingsRefused(RuntimeError):
    """Raised by DisplayManager instead of handing the library such a config."""


def _as_int(value: Any) -> Optional[int]:
    """The integer the binding would receive, or None if it isn't one.

    A value that isn't a number is left to the binding, which raises a
    ``TypeError`` DisplayManager already turns into fallback mode.
    """
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _setting(hardware: Mapping[str, Any], runtime: Mapping[str, Any], field: str) -> Any:
    section = runtime if INT_SETTING_LIMITS.get(field, ('hardware',))[0] == 'runtime' else hardware
    if field in section:
        return section[field]
    return DISPLAY_MANAGER_DEFAULTS.get(field)


def describe_range(low: int, high: int, even: bool = False) -> str:
    kind = "an even integer" if even else "an integer"
    if high >= INT32_MAX:
        return f"{kind} of at least {low}"
    return f"{kind} from {low} to {high}"


def library_refusals(hardware: Optional[Mapping[str, Any]],
                     runtime: Optional[Mapping[str, Any]] = None,
                     pi5: bool = False) -> List[Refusal]:
    """Every reason the pinned library would refuse (NULL, abort or overflow)
    to start with this ``display.hardware`` / ``display.runtime`` config.

    Missing keys take DisplayManager's defaults. ``pi5`` adds the Raspberry
    Pi 5 limits from :func:`pi5_unsupported_settings`.
    """
    hardware = hardware if isinstance(hardware, Mapping) else {}
    runtime = runtime if isinstance(runtime, Mapping) else {}
    refusals: List[Refusal] = []

    for field, (section, low, high, even) in INT_SETTING_LIMITS.items():
        # DisplayManager sets these only when present, and scan_mode /
        # pwm_dither_bits / rp1_rio have no default of its own.
        source = runtime if section == 'runtime' else hardware
        if field not in source and field not in DISPLAY_MANAGER_DEFAULTS:
            continue
        value = _as_int(_setting(hardware, runtime, field))
        if value is None:
            continue
        if not low <= value <= high or (even and value % 2):
            refusals.append(Refusal(
                (field,), f"{field} {value} (must be {describe_range(low, high, even)})"))

    mapping = _setting(hardware, runtime, 'hardware_mapping')
    outputs = None
    if not isinstance(mapping, str):
        refusals.append(Refusal(
            ('hardware_mapping',), f"hardware mapping {mapping!r} (must be a mapping name)"))
    else:
        # The library matches names case-insensitively and reads an empty
        # name as "regular".
        outputs = MAPPING_OUTPUTS.get((mapping or 'regular').lower())
        if outputs is None:
            refusals.append(Refusal(
                ('hardware_mapping',),
                f'hardware mapping "{mapping}" (the installed library has '
                + ", ".join(MAPPING_OUTPUTS) + ")"))

    parallel = _as_int(_setting(hardware, runtime, 'parallel'))
    if outputs is not None and parallel is not None and 1 <= parallel <= 3 and parallel > outputs:
        refusals.append(Refusal(
            ('parallel', 'hardware_mapping'),
            f'parallel {parallel} with hardware mapping "{mapping}", which has '
            f'{outputs} output{"s" if outputs != 1 else ""}'))

    sequence = _setting(hardware, runtime, 'led_rgb_sequence')
    if isinstance(sequence, str) and not (
            len(sequence) == 3 and set(sequence.upper()) == {'R', 'G', 'B'}):
        refusals.append(Refusal(
            ('led_rgb_sequence',),
            f'LED RGB sequence "{sequence}" (must be R, G and B in some order)'))

    if pi5:
        unsupported = pi5_unsupported_settings(hardware)
        if unsupported:
            refusals.append(Refusal(
                ('row_address_type', 'parallel', 'hardware_mapping'), unsupported, pi5=True))

    return refusals


def refusal_message(refusals: List[Refusal]) -> Optional[str]:
    """One sentence naming every refusal, or None when there are none."""
    general = [r.message for r in refusals if not r.pi5]
    pi5 = [r.message for r in refusals if r.pi5]
    parts = []
    if general:
        parts.append("The installed rgbmatrix library can't start with these "
                     "display settings: " + "; ".join(general) + ".")
    parts.extend(pi5)
    return " ".join(parts) or None
