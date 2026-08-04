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

import re
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


# `parse_semver` is deliberately lenient — it strips non-digits and yields
# (0, 0, 0) for a string with no numbers at all, which is fine for a floor
# (a floor of 0.0.0 never blocks anything) but wrong for a range, where the
# same leniency would turn an unreadable spec into a *refusal*. Range specs
# are therefore validated against this first, so garbage reads as "no
# evidence" rather than "incompatible".
_VERSION_TOKEN = re.compile(r"^v?\d+(\.\d+){0,2}(-[\w.-]+)?(\+[\w.-]+)?$")


def _parse_strict(value: str) -> Optional[Tuple[int, int, int]]:
    """`parse_semver`, but ``None`` unless the string really looks like one."""
    if not isinstance(value, str) or not _VERSION_TOKEN.match(value.strip()):
        return None
    return parse_semver(value)


def _satisfies_range(core: Tuple[int, int, int], spec: str) -> Optional[bool]:
    """Does ``core`` satisfy one `compatible_versions` entry?

    Returns ``None`` when the spec cannot be parsed — the caller treats that as
    "no evidence" rather than as a refusal, so an unrecognised spelling never
    costs a user a working install.

    Supports the forms `schema/manifest_schema.json` permits: `>=`, `<=`, `>`,
    `<`, `~`, `^`, a bare exact version, and an inclusive `A - B` range.
    Prerelease/build suffixes are tolerated and ignored, matching `parse_semver`.
    """
    spec = spec.strip()
    if not spec:
        return None

    if " - " in spec:  # inclusive range, e.g. "2.0.0 - 3.1.0"
        low_raw, _, high_raw = spec.partition(" - ")
        low, high = _parse_strict(low_raw), _parse_strict(high_raw)
        if low is None or high is None:
            return None
        return low <= core <= high

    for op in (">=", "<=", ">", "<", "~", "^"):
        if spec.startswith(op):
            target = _parse_strict(spec[len(op):])
            if target is None:
                return None
            if op == ">=":
                return core >= target
            if op == "<=":
                return core <= target
            if op == ">":
                return core > target
            if op == "<":
                return core < target
            if op == "~":
                # Patch-level changes only: >=X.Y.Z, <X.(Y+1).0
                return target <= core < (target[0], target[1] + 1, 0)
            # "^": minor and patch changes: >=X.Y.Z, <(X+1).0.0
            return target <= core < (target[0] + 1, 0, 0)

    exact = _parse_strict(spec)
    return None if exact is None else core == exact


def satisfies_compatible_versions(
    manifest: Dict[str, Any], core: Tuple[int, int, int]
) -> Optional[bool]:
    """Evaluate the manifest's `compatible_versions` array against ``core``.

    The array is a set of *alternatives*: satisfying any one entry means the
    plugin declares itself compatible. Returns ``None`` when the field is
    absent or no entry could be parsed, so callers can distinguish "declared
    incompatible" from "did not say".

    This is the field `schema/manifest_schema.json` marks **required**, and it
    is the only one that can express an upper bound — `ledmatrix_min_version`
    is a floor and cannot say "not compatible with 4.x".
    """
    specs = manifest.get('compatible_versions')
    if not isinstance(specs, list) or not specs:
        return None

    verdicts = [_satisfies_range(core, s) for s in specs if isinstance(s, str)]
    parsed = [v for v in verdicts if v is not None]
    if not parsed:
        return None
    return any(parsed)


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

    Two fields can say a plugin is incompatible and **the more restrictive
    wins**:

    - `compatible_versions` — the schema-required array of semver ranges, and
      the only one that can express an upper bound.
    - `ledmatrix_min_version` (or the deprecated `ledmatrix_min`) — the
      per-release floor inside `versions[]`.

    They agree across every published manifest today except `7-segment-clock`,
    but they *can* disagree, and a plugin that says `["2.0.0 - 2.9.9"]` means
    "not compatible with 3.x" no matter what its floor says.

    ``compatible`` is False **only** on evidence: the core reports a parseable,
    trustworthy version and a field genuinely excludes it. Every uncertain case
    resolves to compatible — nothing declared, an unparseable version on either
    side, or a core below `TRUSTWORTHY_FLOOR`. Refusing on a guess breaks a
    working install, which is the more expensive mistake here.

    ``reason`` is user-facing text, present only when incompatible.
    """
    current = parse_semver(core_version)
    name = manifest.get('name') or manifest.get('id') or 'This plugin'

    if current is None or current < TRUSTWORTHY_FLOOR:
        # The version is not evidence of what this core HAS. But a floor above
        # the ecosystem baseline says the plugin needs modules that arrived
        # *after* 2.0.0 — and a core reporting below that either is the v3.1.0
        # release (which ships __version__ = "1.0.0" and has none of the 3.2.0
        # modules) or is genuinely ancient. Either way it will not have them.
        #
        # This is the only protection available to that population: they cannot
        # be told apart from a real 1.0.0 install, so the gate cannot reason
        # about them, and the *plugin's* guarded-import fallback disappears at
        # the B6 sunset. Refusing the install leaves them on the version they
        # already run instead of handing them one that fails to load.
        #
        # Floors at or below 2.0.0 are still allowed, which is every manifest
        # published today — so this does not lock anyone out of the store.
        declared = declared_min_version(manifest)
        needed = parse_semver(declared)
        if needed is not None and needed > TRUSTWORTHY_FLOOR:
            return False, (
                f"{name} requires LEDMatrix {declared} or newer. This system "
                f"reports {core_version}, which is too old to identify "
                f"reliably — update LEDMatrix, then install it."
            )
        return True, None

    # Ranges first: they are the canonical field and can rule out a core that
    # clears the floor.
    if satisfies_compatible_versions(manifest, current) is False:
        specs = ", ".join(
            s for s in manifest.get('compatible_versions', []) if isinstance(s, str))
        return False, (
            f"{name} supports LEDMatrix {specs}, but this system is running "
            f"{core_version}. Install a build in that range, or a plugin "
            f"version that supports {core_version}."
        )

    declared = declared_min_version(manifest)
    needed = parse_semver(declared)
    if needed is not None and needed > current:
        return False, (
            f"{name} requires LEDMatrix {declared} or newer, but this system is "
            f"running {core_version}. Update LEDMatrix first, then install it."
        )

    return True, None
