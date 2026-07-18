"""
Skin system: user-installable visual overlays for sports scoreboards.

A skin replaces only the rendering of a scoreboard (live / recent /
upcoming) while the host plugin keeps doing data fetching, scheduling,
caching, live priority, and vegas mode. See docs/SKIN_SYSTEM.md.
"""

from src.skin_system.skin_base import (
    SKIN_API_VERSION,
    VIEW_MODEL_VERSION,
    ScoreboardSkin,
    SkinContext,
)
from src.skin_system.skin_runtime import (
    build_context,
    discover_skins,
    get_skins_directory,
    load_skin,
)

__all__ = [
    "SKIN_API_VERSION",
    "VIEW_MODEL_VERSION",
    "ScoreboardSkin",
    "SkinContext",
    "build_context",
    "discover_skins",
    "get_skins_directory",
    "load_skin",
]
