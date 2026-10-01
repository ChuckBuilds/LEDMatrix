"""Where a scoreboard's bundled font file is, whatever the working directory.

Every scoreboard's ``sports.py`` (nine) and ``game_renderer.py`` (eight)
carries the same module-level ``_resolve_font_path``. It predates
:func:`src.common.font_layout.resolve_asset_path`, and probes the core for
it: the path as given when it exists (relative to the cwd), else the core's
resolver (``FontManager._resolve_asset_path``, which delegates to
``resolve_asset_path``), else the path joined to the install root, else the
path unchanged so the caller's ``ImageFont.truetype`` raises and falls back
as before.

On every core this module ships in, the probe always finds the resolver, and
the install-root join repeats what the resolver already tried. What is left
is two steps, and :func:`resolve_font_path` is exactly those: the cwd first,
then ``resolve_asset_path``. ``test/test_sports_font_path.py`` checks that
against the plugins' own copies, path for path. It is the same rule as
``sports_shared._resolve_font_path``, made public so a plugin can import it.

Why not ``resolve_asset_path`` alone: it never consults the cwd, so a
process started from another checkout would switch to the install root's
fonts. Keeping the cwd first keeps that behaviour exactly.
"""

import os

from src.common.font_layout import resolve_asset_path


def resolve_font_path(path: str) -> str:
    """``path`` if it exists, else :func:`resolve_asset_path` of it.

    Absolute paths that exist come back untouched; a relative path is tried
    against the cwd, then the install root; a path found nowhere comes back
    unchanged, so the caller still raises and falls back.
    """
    if os.path.exists(path):
        return path
    return resolve_asset_path(path)


__all__ = ["resolve_font_path"]
