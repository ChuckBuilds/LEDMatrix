"""
Skin system: user-installable visual overlays for sports scoreboards.

A skin replaces only the rendering of a scoreboard (live / recent /
upcoming) while the host plugin keeps doing data fetching, scheduling,
caching, live priority, and vegas mode. See docs/SKIN_SYSTEM.md.
"""

# Skins are not offered to users yet. The only render hook is
# SportsCore._render_game in src/base_classes/sports/core.py, and none of the
# current scoreboard plugins (monorepo or third-party) build on
# src.base_classes, so a selected skin never draws. The web UI and store
# read these instead of offering install/selection; stored "skin" config
# values still load and save. See docs/SKIN_SYSTEM.md.
SKINS_RENDER_SUPPORTED = False
SKINS_UNSUPPORTED_MESSAGE = (
    "Skins aren't supported yet: the current scoreboard plugins don't render "
    "them. Installed skins and saved skin settings are kept but have no effect."
)

from src.skin_system.skin_base import (  # noqa: E402
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
