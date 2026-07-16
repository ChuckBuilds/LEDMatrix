"""
Skin runtime: discovery, validation, loading, and context building.

Deliberately generic — this module knows nothing about sports beyond
passing a `sport` label through; the sports flavor lives in skin_base
(ScoreboardSkin) and in the hosts that call build_context.

Every failure path here logs and returns None: a broken or missing skin
must never take down the plugin that references it — the host falls
back to its built-in renderer.
"""

import importlib.util
import json
import sys
import threading
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from PIL import Image, ImageDraw

from src.adaptive_layout import LayoutContext
from src.logging_config import get_logger
from src.skin_system.skin_base import (
    SKIN_API_VERSION,
    ScoreboardSkin,
    SkinContext,
)

logger = get_logger(__name__)

_REQUIRED_MANIFEST_FIELDS = ("id", "name", "version", "skin_api_version", "class_name")
_DEFAULT_ENTRY_POINT = "skin.py"

_lock = threading.RLock()
# skins_dir -> (fingerprint, {skin_id: manifest+path})
_discovery_cache: Dict[str, Tuple[Tuple, Dict[str, Dict[str, Any]]]] = {}

_shared_layout_font_manager: Optional[Any] = None


def _get_font_manager() -> Any:
    """Shared FontManager for skin LayoutContexts. SportsCore hosts don't
    carry a plugin_manager, so skins share one module-level FontManager —
    the same shape as base_plugin._fallback_font_manager, constructed
    directly so rendering never has to import the whole plugin system."""
    global _shared_layout_font_manager
    if _shared_layout_font_manager is None:
        from src.font_manager import FontManager
        _shared_layout_font_manager = FontManager({})
    return _shared_layout_font_manager


def get_skins_directory() -> Path:
    """Central skins directory: <project_root>/skins. Lives outside the
    plugin directories on purpose — plugin reinstall/update deletes the
    whole plugin directory, and a skin must survive that."""
    return Path(__file__).resolve().parents[2] / "skins"


def _major(version: str) -> Optional[int]:
    try:
        return int(str(version).split(".")[0])
    except (ValueError, AttributeError, IndexError):
        return None


def _read_manifest(skin_dir: Path) -> Optional[Dict[str, Any]]:
    manifest_path = skin_dir / "skin.json"
    if not manifest_path.is_file():
        return None
    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.error("Skin manifest %s is unreadable: %s", manifest_path, e)
        return None
    missing = [k for k in _REQUIRED_MANIFEST_FIELDS if not manifest.get(k)]
    if missing:
        logger.error("Skin manifest %s missing required fields: %s",
                     manifest_path, ", ".join(missing))
        return None
    if manifest["id"] != skin_dir.name:
        logger.warning("Skin manifest id %r does not match directory name %r",
                       manifest["id"], skin_dir.name)
    manifest["_skin_dir"] = str(skin_dir)
    return manifest


def _discovery_fingerprint(skins_dir: Path) -> Optional[Tuple]:
    """Cache key for a skins directory: its mtime plus every skin.json's
    (path, mtime). The directory mtime alone misses in-place manifest edits
    (a skin updated without adding/removing entries)."""
    try:
        parts = [skins_dir.stat().st_mtime]
        for manifest_path in sorted(skins_dir.glob("*/skin.json")):
            parts.append((str(manifest_path), manifest_path.stat().st_mtime))
        return tuple(parts)
    except OSError:
        return None


def discover_skins(skins_dir: Optional[Path] = None,
                   force_refresh: bool = False) -> Dict[str, Dict[str, Any]]:
    """Return {skin_id: manifest} for every valid skin package installed.

    Cached per directory and invalidated when the directory or any
    skin.json changes; pass force_refresh to bypass.
    """
    skins_dir = Path(skins_dir) if skins_dir else get_skins_directory()
    cache_key = str(skins_dir)
    fingerprint = _discovery_fingerprint(skins_dir)
    if fingerprint is None:
        return {}

    with _lock:
        cached = _discovery_cache.get(cache_key)
        if cached and not force_refresh and cached[0] == fingerprint:
            return dict(cached[1])

        skins: Dict[str, Dict[str, Any]] = {}
        for entry in sorted(skins_dir.iterdir()):
            if not entry.is_dir() or entry.name.startswith((".", "_")):
                continue
            manifest = _read_manifest(entry)
            if manifest:
                skins[manifest["id"]] = manifest
        _discovery_cache[cache_key] = (fingerprint, skins)
        return dict(skins)


def skin_targets(manifest: Dict[str, Any]) -> Tuple[list, list]:
    """(sports, sport_keys) a skin declares it supports."""
    targets = manifest.get("targets") or {}
    return (list(targets.get("sports") or []),
            list(targets.get("sport_keys") or []))


def skin_matches_target(manifest: Dict[str, Any], sport: Optional[str],
                        sport_key: Optional[str]) -> bool:
    """True when the skin declares support for this sport family or exact
    sport key. A skin with no targets at all matches everything."""
    sports, sport_keys = skin_targets(manifest)
    if not sports and not sport_keys:
        return True
    if sport and sport in sports:
        return True
    if sport_key and sport_key in sport_keys:
        return True
    return False


def skins_for_plugin(plugin_id: str,
                     skins: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, Dict[str, Any]]:
    """Installed skins that plausibly apply to a plugin, for UI dropdowns.

    A skin matches when the plugin id is listed in targets.plugins, or any
    declared sport / sport_key appears as a token of the plugin id (so a
    skin targeting sports=["baseball"] matches "baseball-scoreboard", and
    sport_keys=["milb"] matches "milb-scoreboard")."""
    if skins is None:
        skins = discover_skins()
    tokens = set(str(plugin_id).lower().replace("-", "_").split("_"))
    matched = {}
    for skin_id, manifest in skins.items():
        targets = manifest.get("targets") or {}
        if plugin_id in (targets.get("plugins") or []):
            matched[skin_id] = manifest
            continue
        sports, sport_keys = skin_targets(manifest)
        if any(str(t).lower() in tokens for t in sports + sport_keys):
            matched[skin_id] = manifest
    return matched


def _load_skin_module(skin_id: str, skin_dir: Path, entry_point: str) -> Optional[Any]:
    """Import the skin's entry module under a namespaced sys.modules key,
    namespacing its sibling .py files the same way — the collision-
    avoidance scheme plugins use (plugin_loader._namespace_plugin_modules),
    so two skins can both ship a helpers.py.

    The entry module is cached: the live/recent/upcoming hosts all load
    the same skin, and only the first load executes any code. (A skin
    whose *code* changed on disk needs a service restart to take effect —
    Python modules can't be safely hot-swapped.)
    """
    entry_path = skin_dir / entry_point
    if not entry_path.is_file():
        logger.error("Skin '%s' entry point not found: %s", skin_id, entry_path)
        return None

    module_name = f"_skin_{skin_id}_{Path(entry_point).stem}"
    with _lock:
        cached_entry = sys.modules.get(module_name)
        if cached_entry is not None:
            return cached_entry

        # Import siblings under their namespaced alias, and *bind* the bare
        # name (cached or fresh) so `import helpers` inside the entry module
        # resolves to this skin's copy. The bare bindings are transient —
        # restored below so another skin's identically-named sibling can't
        # be shadowed by ours.
        replaced_bare: Dict[str, Any] = {}
        try:
            for sibling in skin_dir.glob("*.py"):
                if sibling.name == entry_point:
                    continue
                alias = f"_skin_{skin_id}_{sibling.stem}"
                module = sys.modules.get(alias)
                if module is None:
                    spec = importlib.util.spec_from_file_location(alias, sibling)
                    if not spec or not spec.loader:
                        continue
                    module = importlib.util.module_from_spec(spec)
                    sys.modules[alias] = module
                    replaced_bare.setdefault(sibling.stem, sys.modules.get(sibling.stem))
                    sys.modules[sibling.stem] = module
                    try:
                        spec.loader.exec_module(module)
                    except Exception as e:
                        logger.error("Skin '%s' sibling module %s failed to import: %s",
                                     skin_id, sibling.name, e, exc_info=True)
                        sys.modules.pop(alias, None)
                        return None
                else:
                    replaced_bare.setdefault(sibling.stem, sys.modules.get(sibling.stem))
                    sys.modules[sibling.stem] = module

            try:
                spec = importlib.util.spec_from_file_location(module_name, entry_path)
                if not spec or not spec.loader:
                    logger.error("Skin '%s': could not create import spec for %s",
                                 skin_id, entry_path)
                    return None
                module = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = module
                spec.loader.exec_module(module)
                return module
            except Exception as e:
                sys.modules.pop(module_name, None)
                logger.error("Skin '%s' failed to import: %s", skin_id, e, exc_info=True)
                return None
        finally:
            for bare_name, previous in replaced_bare.items():
                if previous is None:
                    sys.modules.pop(bare_name, None)
                else:
                    sys.modules[bare_name] = previous


def load_skin(skin_id: str, sport: Optional[str] = None,
              sport_key: Optional[str] = None,
              options: Optional[Dict[str, Any]] = None,
              skins_dir: Optional[Path] = None) -> Optional[ScoreboardSkin]:
    """Load and instantiate a skin. Returns None (after logging why) on
    any failure — callers treat None as 'use the built-in renderer'."""
    skins = discover_skins(skins_dir)
    manifest = skins.get(skin_id)
    if manifest is None:
        logger.warning("Skin '%s' is configured but not installed under %s; "
                       "using built-in renderer",
                       skin_id, skins_dir or get_skins_directory())
        return None

    manifest_major = _major(manifest.get("skin_api_version"))
    api_major = _major(SKIN_API_VERSION)
    if manifest_major != api_major:
        logger.error("Skin '%s' targets skin API %s but this LEDMatrix "
                     "provides %s — the skin needs an update; using "
                     "built-in renderer",
                     skin_id, manifest.get("skin_api_version"), SKIN_API_VERSION)
        return None

    if not skin_matches_target(manifest, sport, sport_key):
        # Soft: the user explicitly configured it, so warn but load anyway
        # (a baseball skin may render an acceptable generic scoreboard).
        logger.warning("Skin '%s' does not declare support for sport=%r / "
                       "sport_key=%r; loading anyway", skin_id, sport, sport_key)

    skin_dir = Path(manifest["_skin_dir"])
    module = _load_skin_module(skin_id, skin_dir,
                               manifest.get("entry_point", _DEFAULT_ENTRY_POINT))
    if module is None:
        return None

    class_name = manifest["class_name"]
    skin_class = getattr(module, class_name, None)
    if skin_class is None or not isinstance(skin_class, type) or \
            not issubclass(skin_class, ScoreboardSkin):
        logger.error("Skin '%s': %s is missing or not a ScoreboardSkin subclass",
                     skin_id, class_name)
        return None

    try:
        return skin_class(manifest, options or {})
    except Exception as e:
        logger.error("Skin '%s' failed to instantiate: %s", skin_id, e, exc_info=True)
        return None


def build_context(host: Any, game: Dict[str, Any],
                  size: Optional[Tuple[int, int]] = None) -> SkinContext:
    """Build a SkinContext for one render call.

    `host` is a SportsCore-style object: display_manager, fonts, logger,
    sport, skin_options, _load_and_resize_logo, _draw_text_with_outline.
    `size` overrides the canvas size (vegas cards); default is the
    current display size read live from the display manager.
    """
    if size is not None:
        width, height = int(size[0]), int(size[1])
    else:
        dm = host.display_manager
        width = getattr(dm, "width", None) or dm.matrix.width
        height = getattr(dm, "height", None) or dm.matrix.height

    canvas = Image.new("RGB", (width, height), (0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    layout = LayoutContext(width, height, _get_font_manager())

    def load_logo(side: str) -> Optional[Image.Image]:
        if side not in ("home", "away"):
            return None
        try:
            logo_path = game.get(f"{side}_logo_path")
            if logo_path is not None and not isinstance(logo_path, Path):
                logo_path = Path(logo_path)
            return host._load_and_resize_logo(
                game.get(f"{side}_id"), game.get(f"{side}_abbr"),
                logo_path, game.get(f"{side}_logo_url"))
        except Exception as e:
            host.logger.warning("Skin logo load failed for %s: %s", side, e)
            return None

    def draw_text_outlined(text, position, font, fill=(255, 255, 255),
                           outline_color=(0, 0, 0)):
        host._draw_text_with_outline(draw, text, position, font,
                                     fill=fill, outline_color=outline_color)

    return SkinContext(
        canvas=canvas,
        draw=draw,
        layout=layout,
        width=width,
        height=height,
        fonts=dict(host.fonts),
        options=dict(getattr(host, "skin_options", {}) or {}),
        logger=host.logger,
        sport=getattr(host, "sport", None),
        load_logo=load_logo,
        draw_text_outlined=draw_text_outlined,
    )
