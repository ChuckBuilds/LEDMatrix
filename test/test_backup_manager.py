"""Tests for src.backup_manager."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest

from src import backup_manager
from src.backup_manager import (
    BUNDLED_FONTS,
    RestoreOptions,
    create_backup,
    list_installed_plugins,
    preview_backup_contents,
    restore_backup,
    validate_backup,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_project(root: Path) -> Path:
    """Build a minimal fake project tree under ``root``."""
    (root / "config").mkdir(parents=True)
    (root / "config" / "config.json").write_text(
        json.dumps({"web_ui": {"port": 8080}, "my-plugin": {"enabled": True, "favorites": ["A", "B"]}}),
        encoding="utf-8",
    )
    (root / "config" / "config_secrets.json").write_text(
        json.dumps({"ledmatrix-weather": {"api_key": "SECRET"}}),
        encoding="utf-8",
    )
    (root / "config" / "wifi_config.json").write_text(
        json.dumps({"ap_mode": {"ssid": "LEDMatrix"}}),
        encoding="utf-8",
    )

    fonts = root / "assets" / "fonts"
    fonts.mkdir(parents=True)
    # One bundled font (should be excluded) and one user-uploaded font.
    (fonts / "5x7.bdf").write_text("BUNDLED", encoding="utf-8")
    (fonts / "my-custom-font.ttf").write_bytes(b"\x00\x01USER")

    uploads = root / "assets" / "plugins" / "static-image" / "uploads"
    uploads.mkdir(parents=True)
    (uploads / "image_1.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
    (uploads / ".metadata.json").write_text(json.dumps({"a": 1}), encoding="utf-8")

    # plugin-repos for installed-plugin enumeration.
    plugin_dir = root / "plugin-repos" / "my-plugin"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "manifest.json").write_text(
        json.dumps({"id": "my-plugin", "version": "1.2.3"}),
        encoding="utf-8",
    )

    # plugin_state.json
    (root / "data").mkdir()
    (root / "data" / "plugin_state.json").write_text(
        json.dumps(
            {
                "plugins": {
                    "my-plugin": {"version": "1.2.3", "enabled": True},
                    "other-plugin": {"version": "0.1.0", "enabled": False},
                }
            }
        ),
        encoding="utf-8",
    )
    return root


@pytest.fixture
def project(tmp_path: Path) -> Path:
    return _make_project(tmp_path / "src_project")


@pytest.fixture
def empty_project(tmp_path: Path) -> Path:
    root = tmp_path / "dst_project"
    root.mkdir()
    # Pre-seed only the bundled font to simulate a fresh install.
    (root / "assets" / "fonts").mkdir(parents=True)
    (root / "assets" / "fonts" / "5x7.bdf").write_text("BUNDLED", encoding="utf-8")
    return root


# ---------------------------------------------------------------------------
# BUNDLED_FONTS sanity
# ---------------------------------------------------------------------------


def test_bundled_fonts_matches_repo() -> None:
    """Every entry in BUNDLED_FONTS must exist on disk in assets/fonts/.

    The reverse direction is intentionally not checked: real installations
    have user-uploaded fonts in the same directory, and they should be
    treated as user data (not bundled).
    """
    repo_fonts = Path(__file__).resolve().parent.parent / "assets" / "fonts"
    if not repo_fonts.exists():
        pytest.skip("assets/fonts not present in test env")
    on_disk = {p.name for p in repo_fonts.iterdir() if p.is_file()}
    missing = set(BUNDLED_FONTS) - on_disk
    assert not missing, f"BUNDLED_FONTS references files not in assets/fonts/: {missing}"


# ---------------------------------------------------------------------------
# Preview / enumeration
# ---------------------------------------------------------------------------


def test_list_installed_plugins(project: Path) -> None:
    plugins = list_installed_plugins(project)
    ids = [p["plugin_id"] for p in plugins]
    assert "my-plugin" in ids
    assert "other-plugin" in ids
    my = next(p for p in plugins if p["plugin_id"] == "my-plugin")
    assert my["version"] == "1.2.3"


def test_preview_backup_contents(project: Path) -> None:
    preview = preview_backup_contents(project)
    assert preview["has_config"] is True
    assert preview["has_secrets"] is True
    assert preview["has_wifi"] is True
    assert preview["user_fonts"] == ["my-custom-font.ttf"]
    assert preview["plugin_uploads"] >= 2
    assert any(p["plugin_id"] == "my-plugin" for p in preview["plugins"])


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


def test_create_backup_contents(project: Path, tmp_path: Path) -> None:
    out_dir = tmp_path / "exports"
    zip_path = create_backup(project, output_dir=out_dir)
    assert zip_path.exists()
    assert zip_path.parent == out_dir
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
    assert "manifest.json" in names
    assert "config/config.json" in names
    assert "config/config_secrets.json" in names
    assert "config/wifi_config.json" in names
    assert "assets/fonts/my-custom-font.ttf" in names
    # Bundled font must NOT be included.
    assert "assets/fonts/5x7.bdf" not in names
    assert "assets/plugins/static-image/uploads/image_1.png" in names
    assert "plugins.json" in names


def test_create_backup_manifest(project: Path, tmp_path: Path) -> None:
    zip_path = create_backup(project, output_dir=tmp_path / "exports")
    with zipfile.ZipFile(zip_path) as zf:
        manifest = json.loads(zf.read("manifest.json"))
    assert manifest["schema_version"] == backup_manager.SCHEMA_VERSION
    assert "created_at" in manifest
    assert set(manifest["contents"]) >= {"config", "secrets", "wifi", "fonts", "plugin_uploads", "plugins"}


# ---------------------------------------------------------------------------
# Validate
# ---------------------------------------------------------------------------


def test_validate_backup_ok(project: Path, tmp_path: Path) -> None:
    zip_path = create_backup(project, output_dir=tmp_path / "exports")
    ok, err, manifest = validate_backup(zip_path)
    assert ok, err
    assert err == ""
    assert "config" in manifest["detected_contents"]
    assert "secrets" in manifest["detected_contents"]
    assert any(p["plugin_id"] == "my-plugin" for p in manifest["plugins"])


def test_validate_backup_missing_manifest(tmp_path: Path) -> None:
    zip_path = tmp_path / "bad.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("config/config.json", "{}")
    ok, err, _ = validate_backup(zip_path)
    assert not ok
    assert "manifest" in err.lower()


def test_validate_backup_bad_schema_version(tmp_path: Path) -> None:
    zip_path = tmp_path / "bad.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"schema_version": 999}))
    ok, err, _ = validate_backup(zip_path)
    assert not ok
    assert "schema" in err.lower()


def test_validate_backup_rejects_zip_traversal(tmp_path: Path) -> None:
    zip_path = tmp_path / "malicious.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"schema_version": 1, "contents": []}))
        zf.writestr("../../etc/passwd", "x")
    ok, err, _ = validate_backup(zip_path)
    assert not ok
    assert "unsafe" in err.lower()


def test_validate_backup_not_a_zip(tmp_path: Path) -> None:
    p = tmp_path / "nope.zip"
    p.write_text("hello", encoding="utf-8")
    ok, err, _ = validate_backup(p)
    assert not ok


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------


def test_restore_roundtrip(project: Path, empty_project: Path, tmp_path: Path) -> None:
    zip_path = create_backup(project, output_dir=tmp_path / "exports")
    result = restore_backup(zip_path, empty_project, RestoreOptions())

    assert result.success, result.errors
    assert "config" in result.restored
    assert "secrets" in result.restored
    assert "wifi" in result.restored

    # Files exist with correct contents.
    restored_config = json.loads((empty_project / "config" / "config.json").read_text())
    assert restored_config["my-plugin"]["favorites"] == ["A", "B"]

    restored_secrets = json.loads((empty_project / "config" / "config_secrets.json").read_text())
    assert restored_secrets["ledmatrix-weather"]["api_key"] == "SECRET"

    # User font restored, bundled font untouched.
    assert (empty_project / "assets" / "fonts" / "my-custom-font.ttf").read_bytes() == b"\x00\x01USER"
    assert (empty_project / "assets" / "fonts" / "5x7.bdf").read_text() == "BUNDLED"

    # Plugin uploads restored.
    assert (empty_project / "assets" / "plugins" / "static-image" / "uploads" / "image_1.png").exists()

    # Plugins to install surfaced for the caller.
    plugin_ids = {p["plugin_id"] for p in result.plugins_to_install}
    assert "my-plugin" in plugin_ids


def test_restore_honors_options(project: Path, empty_project: Path, tmp_path: Path) -> None:
    zip_path = create_backup(project, output_dir=tmp_path / "exports")
    opts = RestoreOptions(
        restore_config=True,
        restore_secrets=False,
        restore_wifi=False,
        restore_fonts=False,
        restore_plugin_uploads=False,
        reinstall_plugins=False,
    )
    result = restore_backup(zip_path, empty_project, opts)
    assert result.success, result.errors
    assert (empty_project / "config" / "config.json").exists()
    assert not (empty_project / "config" / "config_secrets.json").exists()
    assert not (empty_project / "config" / "wifi_config.json").exists()
    assert not (empty_project / "assets" / "fonts" / "my-custom-font.ttf").exists()
    assert result.plugins_to_install == []
    assert "secrets" in result.skipped
    assert "wifi" in result.skipped


def test_restore_rejects_malicious_zip(empty_project: Path, tmp_path: Path) -> None:
    zip_path = tmp_path / "bad.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"schema_version": 1, "contents": []}))
        zf.writestr("../escape.txt", "x")
    result = restore_backup(zip_path, empty_project, RestoreOptions())
    # validate_backup catches it before extraction.
    assert not result.success
    assert any("unsafe" in e.lower() for e in result.errors)
