"""
User configuration backup and restore.

Packages the user's LEDMatrix configuration, secrets, WiFi settings,
user-uploaded fonts, plugin image uploads, and installed-plugin manifest
into a single ``.zip`` that can be exported from one installation and
imported on a fresh install.

This module is intentionally Flask-free so it can be unit-tested and
used from scripts.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import socket
import tempfile
import zipfile
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

# Filenames shipped with the LEDMatrix repository under ``assets/fonts/``.
# Anything present on disk but NOT in this set is treated as a user upload
# and included in backups. Keep this snapshot in sync with the repo — regenerate
# with::
#
#     ls assets/fonts/
#
# Tests assert the set matches the checked-in fonts.
BUNDLED_FONTS: frozenset[str] = frozenset({
    "10x20.bdf",
    "4x6.bdf",
    "4x6-font.ttf",
    "5by7.regular.ttf",
    "5x7.bdf",
    "5x8.bdf",
    "6x9.bdf",
    "6x10.bdf",
    "6x12.bdf",
    "6x13.bdf",
    "6x13B.bdf",
    "6x13O.bdf",
    "7x13.bdf",
    "7x13B.bdf",
    "7x13O.bdf",
    "7x14.bdf",
    "7x14B.bdf",
    "8x13.bdf",
    "8x13B.bdf",
    "8x13O.bdf",
    "9x15.bdf",
    "9x15B.bdf",
    "9x18.bdf",
    "9x18B.bdf",
    "AUTHORS",
    "bdf_font_guide",
    "clR6x12.bdf",
    "helvR12.bdf",
    "ic8x8u.bdf",
    "MatrixChunky8.bdf",
    "MatrixChunky8X.bdf",
    "MatrixLight6.bdf",
    "MatrixLight6X.bdf",
    "MatrixLight8X.bdf",
    "PressStart2P-Regular.ttf",
    "README",
    "README.md",
    "texgyre-27.bdf",
    "tom-thumb.bdf",
})

# Relative paths inside the project that the backup knows how to round-trip.
_CONFIG_REL = Path("config/config.json")
_SECRETS_REL = Path("config/config_secrets.json")
_WIFI_REL = Path("config/wifi_config.json")
_FONTS_REL = Path("assets/fonts")
_PLUGIN_UPLOADS_REL = Path("assets/plugins")
_STATE_REL = Path("data/plugin_state.json")

MANIFEST_NAME = "manifest.json"
PLUGINS_MANIFEST_NAME = "plugins.json"

# Hard cap on the size of a single file we'll accept inside an uploaded ZIP
# to limit zip-bomb risk. 50 MB matches the existing plugin-image upload cap.
_MAX_MEMBER_BYTES = 50 * 1024 * 1024
# Hard cap on the total uncompressed size of an uploaded ZIP.
_MAX_TOTAL_BYTES = 200 * 1024 * 1024


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class RestoreOptions:
    """Which sections of a backup should be restored."""

    restore_config: bool = True
    restore_secrets: bool = True
    restore_wifi: bool = True
    restore_fonts: bool = True
    restore_plugin_uploads: bool = True
    reinstall_plugins: bool = True


@dataclass
class RestoreResult:
    """Outcome of a restore operation."""

    success: bool = False
    restored: List[str] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)
    plugins_to_install: List[Dict[str, Any]] = field(default_factory=list)
    plugins_installed: List[str] = field(default_factory=list)
    plugins_failed: List[Dict[str, str]] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    manifest: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Manifest helpers
# ---------------------------------------------------------------------------


def _ledmatrix_version(project_root: Path) -> str:
    """Best-effort version string for the current install."""
    version_file = project_root / "VERSION"
    if version_file.exists():
        try:
            return version_file.read_text(encoding="utf-8").strip() or "unknown"
        except OSError:
            pass
    head_file = project_root / ".git" / "HEAD"
    if head_file.exists():
        try:
            head = head_file.read_text(encoding="utf-8").strip()
            if head.startswith("ref: "):
                ref = head[5:]
                ref_path = project_root / ".git" / ref
                if ref_path.exists():
                    return ref_path.read_text(encoding="utf-8").strip()[:12] or "unknown"
            return head[:12] or "unknown"
        except OSError:
            pass
    return "unknown"


def _build_manifest(contents: List[str], project_root: Path) -> Dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "ledmatrix_version": _ledmatrix_version(project_root),
        "hostname": socket.gethostname(),
        "contents": contents,
    }


# ---------------------------------------------------------------------------
# Installed-plugin enumeration
# ---------------------------------------------------------------------------


def list_installed_plugins(project_root: Path) -> List[Dict[str, Any]]:
    """
    Return a list of currently-installed plugins suitable for the backup
    manifest. Each entry has ``plugin_id`` and ``version``.

    Reads ``data/plugin_state.json`` if present; otherwise walks the plugin
    directory and reads each ``manifest.json``.
    """
    plugins: Dict[str, Dict[str, Any]] = {}

    state_file = project_root / _STATE_REL
    if state_file.exists():
        try:
            with state_file.open("r", encoding="utf-8") as f:
                state = json.load(f)
            raw_plugins = state.get("states", {}) if isinstance(state, dict) else {}
            if isinstance(raw_plugins, dict):
                for plugin_id, info in raw_plugins.items():
                    if not isinstance(info, dict):
                        continue
                    plugins[plugin_id] = {
                        "plugin_id": plugin_id,
                        "version": info.get("version") or "",
                        "enabled": bool(info.get("enabled", True)),
                    }
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("Could not read plugin_state.json: %s", e)

    # Fall back to scanning plugin-repos/ for manifests.
    plugins_root = project_root / "plugin-repos"
    if plugins_root.exists():
        for entry in sorted(plugins_root.iterdir()):
            if not entry.is_dir():
                continue
            manifest = entry / "manifest.json"
            if not manifest.exists():
                continue
            try:
                with manifest.open("r", encoding="utf-8") as f:
                    data = json.load(f)
            except (OSError, json.JSONDecodeError):
                continue
            plugin_id = data.get("id") or entry.name
            if plugin_id not in plugins:
                plugins[plugin_id] = {
                    "plugin_id": plugin_id,
                    "version": data.get("version", ""),
                    "enabled": True,
                }

    return sorted(plugins.values(), key=lambda p: p["plugin_id"])


# ---------------------------------------------------------------------------
# Font filtering
# ---------------------------------------------------------------------------


def iter_user_fonts(project_root: Path) -> List[Path]:
    """Return absolute paths to user-uploaded fonts (anything in
    ``assets/fonts/`` not listed in :data:`BUNDLED_FONTS`)."""
    fonts_dir = project_root / _FONTS_REL
    if not fonts_dir.exists():
        return []
    user_fonts: List[Path] = []
    for entry in sorted(fonts_dir.iterdir()):
        if entry.is_file() and entry.name not in BUNDLED_FONTS:
            user_fonts.append(entry)
    return user_fonts


def iter_plugin_uploads(project_root: Path) -> List[Path]:
    """Return every file under ``assets/plugins/*/uploads/`` (recursive)."""
    plugin_root = project_root / _PLUGIN_UPLOADS_REL
    if not plugin_root.exists():
        return []
    out: List[Path] = []
    for plugin_dir in sorted(plugin_root.iterdir()):
        if not plugin_dir.is_dir():
            continue
        uploads = plugin_dir / "uploads"
        if not uploads.exists():
            continue
        for root, _dirs, files in os.walk(uploads):
            for name in sorted(files):
                out.append(Path(root) / name)
    return out


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


def create_backup(
    project_root: Path,
    output_dir: Optional[Path] = None,
) -> Path:
    """
    Build a backup ZIP and write it into ``output_dir`` (defaults to
    ``<project_root>/config/backups/exports/``). Returns the path to the
    created file.
    """
    project_root = Path(project_root).resolve()
    if output_dir is None:
        output_dir = project_root / "config" / "backups" / "exports"
    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    hostname = socket.gethostname() or "ledmatrix"
    safe_host = "".join(c for c in hostname if c.isalnum() or c in "-_") or "ledmatrix"
    zip_name = f"ledmatrix-backup-{safe_host}-{timestamp}.zip"
    zip_path = output_dir / zip_name

    contents: List[str] = []

    # Stream directly to a temp file so we never hold the whole ZIP in memory.
    tmp_path = zip_path.with_suffix(".zip.tmp")
    with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        # Config files.
        if (project_root / _CONFIG_REL).exists():
            zf.write(project_root / _CONFIG_REL, _CONFIG_REL.as_posix())
            contents.append("config")
        if (project_root / _SECRETS_REL).exists():
            zf.write(project_root / _SECRETS_REL, _SECRETS_REL.as_posix())
            contents.append("secrets")
        if (project_root / _WIFI_REL).exists():
            zf.write(project_root / _WIFI_REL, _WIFI_REL.as_posix())
            contents.append("wifi")

        # User-uploaded fonts.
        user_fonts = iter_user_fonts(project_root)
        if user_fonts:
            for font in user_fonts:
                arcname = font.relative_to(project_root).as_posix()
                zf.write(font, arcname)
            contents.append("fonts")

        # Plugin uploads.
        plugin_uploads = iter_plugin_uploads(project_root)
        if plugin_uploads:
            for upload in plugin_uploads:
                arcname = upload.relative_to(project_root).as_posix()
                zf.write(upload, arcname)
            contents.append("plugin_uploads")

        # Installed plugins manifest.
        plugins = list_installed_plugins(project_root)
        if plugins:
            zf.writestr(
                PLUGINS_MANIFEST_NAME,
                json.dumps(plugins, indent=2),
            )
            contents.append("plugins")

        # Manifest goes last so that `contents` reflects what we actually wrote.
        manifest = _build_manifest(contents, project_root)
        zf.writestr(MANIFEST_NAME, json.dumps(manifest, indent=2))

    os.replace(tmp_path, zip_path)
    logger.info("Created backup %s (%d bytes)", zip_path, zip_path.stat().st_size)
    return zip_path


def preview_backup_contents(project_root: Path) -> Dict[str, Any]:
    """Return a summary of what ``create_backup`` would include."""
    project_root = Path(project_root).resolve()
    return {
        "has_config": (project_root / _CONFIG_REL).exists(),
        "has_secrets": (project_root / _SECRETS_REL).exists(),
        "has_wifi": (project_root / _WIFI_REL).exists(),
        "user_fonts": [p.name for p in iter_user_fonts(project_root)],
        "plugin_uploads": len(iter_plugin_uploads(project_root)),
        "plugins": list_installed_plugins(project_root),
    }


# ---------------------------------------------------------------------------
# Validate
# ---------------------------------------------------------------------------


def _safe_extract_path(base_dir: Path, member_name: str) -> Optional[Path]:
    """Resolve a ZIP member name against ``base_dir`` and reject anything
    that escapes it. Returns the resolved absolute path, or ``None`` if the
    name is unsafe."""
    # Reject absolute paths and Windows-style drives outright.
    if member_name.startswith(("/", "\\")) or (len(member_name) >= 2 and member_name[1] == ":"):
        return None
    target = (base_dir / member_name).resolve()
    try:
        target.relative_to(base_dir.resolve())
    except ValueError:
        return None
    return target


def validate_backup(zip_path: Path) -> Tuple[bool, str, Dict[str, Any]]:
    """
    Inspect a backup ZIP without extracting to disk.

    Returns ``(ok, error_message, manifest_dict)``. ``manifest_dict`` contains
    the parsed manifest plus diagnostic fields:
      - ``detected_contents``: list of section names present in the archive
      - ``plugins``: parsed plugins.json if present
      - ``total_uncompressed``: sum of uncompressed sizes
    """
    zip_path = Path(zip_path)
    if not zip_path.exists():
        return False, f"Backup file not found: {zip_path}", {}

    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = zf.namelist()
            if MANIFEST_NAME not in names:
                return False, "Backup is missing manifest.json", {}

            total = 0
            for info in zf.infolist():
                if info.file_size > _MAX_MEMBER_BYTES:
                    return False, f"Member {info.filename} is too large", {}
                total += info.file_size
                if total > _MAX_TOTAL_BYTES:
                    return False, "Backup exceeds maximum allowed size", {}
                # Safety: reject members with unsafe paths up front.
                if _safe_extract_path(Path("/tmp/_zip_check"), info.filename) is None:
                    return False, f"Unsafe path in backup: {info.filename}", {}

            try:
                manifest_raw = zf.read(MANIFEST_NAME).decode("utf-8")
                manifest = json.loads(manifest_raw)
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
                return False, f"Invalid manifest.json: {e}", {}

            if not isinstance(manifest, dict) or "schema_version" not in manifest:
                return False, "Invalid manifest structure", {}
            if manifest.get("schema_version") != SCHEMA_VERSION:
                return (
                    False,
                    f"Unsupported backup schema version: {manifest.get('schema_version')}",
                    {},
                )

            detected: List[str] = []
            if _CONFIG_REL.as_posix() in names:
                detected.append("config")
            if _SECRETS_REL.as_posix() in names:
                detected.append("secrets")
            if _WIFI_REL.as_posix() in names:
                detected.append("wifi")
            if any(n.startswith(_FONTS_REL.as_posix() + "/") for n in names):
                detected.append("fonts")
            if any(
                n.startswith(_PLUGIN_UPLOADS_REL.as_posix() + "/") and "/uploads/" in n
                for n in names
            ):
                detected.append("plugin_uploads")

            plugins: List[Dict[str, Any]] = []
            if PLUGINS_MANIFEST_NAME in names:
                try:
                    plugins = json.loads(zf.read(PLUGINS_MANIFEST_NAME).decode("utf-8"))
                    if not isinstance(plugins, list):
                        plugins = []
                    else:
                        detected.append("plugins")
                except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                    plugins = []

            result_manifest = dict(manifest)
            result_manifest["detected_contents"] = detected
            result_manifest["plugins"] = plugins
            result_manifest["total_uncompressed"] = total
            result_manifest["file_count"] = len(names)
            return True, "", result_manifest
    except zipfile.BadZipFile:
        return False, "File is not a valid ZIP archive", {}
    except OSError as e:
        return False, f"Could not read backup: {e}", {}


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------


def _extract_zip_safe(zip_path: Path, dest_dir: Path) -> None:
    """Extract ``zip_path`` into ``dest_dir`` rejecting any unsafe members."""
    with zipfile.ZipFile(zip_path, "r") as zf:
        for info in zf.infolist():
            target = _safe_extract_path(dest_dir, info.filename)
            if target is None:
                raise ValueError(f"Unsafe path in backup: {info.filename}")
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info, "r") as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst, length=64 * 1024)


def _copy_file(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def restore_backup(
    zip_path: Path,
    project_root: Path,
    options: Optional[RestoreOptions] = None,
) -> RestoreResult:
    """
    Restore ``zip_path`` into ``project_root`` according to ``options``.

    Plugin reinstalls are NOT performed here — the caller is responsible for
    walking ``result.plugins_to_install`` and calling the store manager. This
    keeps this module Flask-free and side-effect free beyond the filesystem.
    """
    if options is None:
        options = RestoreOptions()
    project_root = Path(project_root).resolve()
    result = RestoreResult()

    ok, err, manifest = validate_backup(zip_path)
    if not ok:
        result.errors.append(err)
        return result
    result.manifest = manifest

    with tempfile.TemporaryDirectory(prefix="ledmatrix_restore_") as tmp:
        tmp_dir = Path(tmp)
        try:
            _extract_zip_safe(Path(zip_path), tmp_dir)
        except (ValueError, zipfile.BadZipFile, OSError) as e:
            result.errors.append(f"Failed to extract backup: {e}")
            return result

        # Main config.
        if options.restore_config and (tmp_dir / _CONFIG_REL).exists():
            try:
                _copy_file(tmp_dir / _CONFIG_REL, project_root / _CONFIG_REL)
                result.restored.append("config")
            except OSError as e:
                result.errors.append(f"Failed to restore config.json: {e}")
        elif (tmp_dir / _CONFIG_REL).exists():
            result.skipped.append("config")

        # Secrets.
        if options.restore_secrets and (tmp_dir / _SECRETS_REL).exists():
            try:
                _copy_file(tmp_dir / _SECRETS_REL, project_root / _SECRETS_REL)
                result.restored.append("secrets")
            except OSError as e:
                result.errors.append(f"Failed to restore config_secrets.json: {e}")
        elif (tmp_dir / _SECRETS_REL).exists():
            result.skipped.append("secrets")

        # WiFi.
        if options.restore_wifi and (tmp_dir / _WIFI_REL).exists():
            try:
                _copy_file(tmp_dir / _WIFI_REL, project_root / _WIFI_REL)
                result.restored.append("wifi")
            except OSError as e:
                result.errors.append(f"Failed to restore wifi_config.json: {e}")
        elif (tmp_dir / _WIFI_REL).exists():
            result.skipped.append("wifi")

        # User fonts — skip anything that collides with a bundled font.
        tmp_fonts = tmp_dir / _FONTS_REL
        if options.restore_fonts and tmp_fonts.exists():
            restored_count = 0
            for font in sorted(tmp_fonts.iterdir()):
                if not font.is_file():
                    continue
                if font.name in BUNDLED_FONTS:
                    result.skipped.append(f"font:{font.name} (bundled)")
                    continue
                try:
                    _copy_file(font, project_root / _FONTS_REL / font.name)
                    restored_count += 1
                except OSError as e:
                    result.errors.append(f"Failed to restore font {font.name}: {e}")
            if restored_count:
                result.restored.append(f"fonts ({restored_count})")
        elif tmp_fonts.exists():
            result.skipped.append("fonts")

        # Plugin uploads.
        tmp_uploads = tmp_dir / _PLUGIN_UPLOADS_REL
        if options.restore_plugin_uploads and tmp_uploads.exists():
            count = 0
            for root, _dirs, files in os.walk(tmp_uploads):
                for name in files:
                    src = Path(root) / name
                    rel = src.relative_to(tmp_dir)
                    if "/uploads/" not in rel.as_posix():
                        result.errors.append(f"Rejected unexpected plugin path: {rel}")
                        continue
                    try:
                        _copy_file(src, project_root / rel)
                        count += 1
                    except OSError as e:
                        result.errors.append(f"Failed to restore {rel}: {e}")
            if count:
                result.restored.append(f"plugin_uploads ({count})")
        elif tmp_uploads.exists():
            result.skipped.append("plugin_uploads")

        # Plugins list (for caller to reinstall).
        if options.reinstall_plugins and (tmp_dir / PLUGINS_MANIFEST_NAME).exists():
            try:
                with (tmp_dir / PLUGINS_MANIFEST_NAME).open("r", encoding="utf-8") as f:
                    plugins = json.load(f)
                if isinstance(plugins, list):
                    result.plugins_to_install = [
                        {"plugin_id": p.get("plugin_id"), "version": p.get("version", "")}
                        for p in plugins
                        if isinstance(p, dict) and p.get("plugin_id")
                    ]
            except (OSError, json.JSONDecodeError) as e:
                result.errors.append(f"Could not read plugins.json: {e}")

    result.success = not result.errors
    return result
