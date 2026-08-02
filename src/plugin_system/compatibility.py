"""One place that answers "can this plugin run on this core?".

Two callers ask that question and they must not drift apart:

- `PluginLoader._warn_if_incompatible` — at load time, **advisory**. A plugin
  already on disk keeps loading regardless, because the guarded-import pattern
  means most incompatibilities degrade rather than break.
- `PluginStoreManager.install_plugin` — at install/update time, **blocking**.
  This is the point where refusing costs the user nothing (they keep the
  version they already had) and allowing can cost them a plugin that fails to
  load with only a log line to explain it.

## The trustworthiness problem

The core's own `__version__` has not always been right. `v3.1.0` was tagged
2026-05-31 while `src/__init__.py` still said `"1.0.0"`; the bump landed
2026-07-12. Devices installed from that release report `1.0.0` — below the
floor that essentially every published plugin declares.

So a core reporting a version below `TRUSTWORTHY_FLOOR` is treated as
**unknown, not old**: it neither warns nor blocks. Blocking on it would be far
worse than the problem being solved — nearly every manifest in the ecosystem
floors at `2.0.0`, so a strict gate would stop those users installing *any*
plugin. They are unprotected until they update the core, which is also what
fixes their version string. See `docs/SPORTS_UNIFICATION.md`, phase B4.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

# Below this, the core's self-reported version is not evidence of anything.
# See the module docstring.
TRUSTWORTHY_FLOOR: Tuple[int, int, int] = (2, 0, 0)


def parse_semver(value: Any) -> Optional[Tuple[int, int, int]]:
    """Parse ``X.Y.Z`` (extra parts and suffixes ignored) into a comparable
    3-tuple, or ``None`` when unparseable. A leading ``v`` is tolerated."""
    if not isinstance(value, str):
        return None
    parts = value.strip().lstrip('v').split('.')
    try:
        nums = [int(''.join(ch for ch in p if ch.isdigit()) or 0) for p in parts[:3]]
    except ValueError:
        return None
    while len(nums) < 3:
        nums.append(0)
    return tuple(nums)  # type: ignore[return-value]


def declared_min_version(manifest: Dict[str, Any]) -> Optional[str]:
    """The core version this plugin says it needs, or ``None`` if it doesn't say.

    Checked in order of specificity. `ledmatrix_min` is the deprecated spelling
    of `ledmatrix_min_version` (`store_manager._validate_manifest_fields` flags
    it); both are read because a large share of published manifests still carry
    the old one.
    """
    declared = (
        manifest.get('min_ledmatrix_version')
        or (manifest.get('requires') or {}).get('min_ledmatrix_version')
    )
    if declared:
        return declared

    versions = manifest.get('versions') or []
    if versions and isinstance(versions[0], dict):
        return (versions[0].get('ledmatrix_min_version')
                or versions[0].get('ledmatrix_min'))
    return None


def check(manifest: Dict[str, Any], core_version: str) -> Tuple[bool, Optional[str]]:
    """Return ``(compatible, reason)``.

    ``compatible`` is False **only** when the plugin declares a parseable floor,
    the core reports a parseable and trustworthy version, and the floor is
    genuinely above it. Every uncertain case resolves to compatible: an
    undeclared floor, an unparseable version on either side, or a core whose
    version is below `TRUSTWORTHY_FLOOR`. Refusing on a guess would break
    working installs, which is the more expensive mistake here.

    ``reason`` is user-facing text, present only when incompatible.
    """
    declared = declared_min_version(manifest)
    needed = parse_semver(declared)
    if needed is None:
        return True, None

    current = parse_semver(core_version)
    if current is None or current < TRUSTWORTHY_FLOOR:
        return True, None

    if needed > current:
        name = manifest.get('name') or manifest.get('id') or 'This plugin'
        return False, (
            f"{name} requires LEDMatrix {declared} or newer, but this system is "
            f"running {core_version}. Update LEDMatrix first, then install it."
        )

    return True, None
