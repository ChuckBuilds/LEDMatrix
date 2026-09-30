"""Tests for src.backup_manager."""

from __future__ import annotations

import json
import os
import stat
import sys
import zipfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src import backup_manager
from src.backup_manager import (
    BUNDLED_FONTS,
    SCHEMA_VERSION,
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
    # Device-local auth that lives in config/ like the three above. It was
    # omitted from backups, so a restore silently signed the user out of
    # YouTube Music and they had to re-authenticate by hand.
    (root / "config" / "ytm_auth.json").write_text(
        json.dumps({"token": "YTM-TOKEN"}),
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

    # A plugin_state.json left behind by an older release. Retired: the
    # listing must ignore it (see test_list_installed_plugins).
    (root / "data").mkdir()
    (root / "data" / "plugin_state.json").write_text(
        json.dumps(
            {
                "version": 1,
                "states": {
                    "my-plugin": {"version": "1.2.3", "enabled": True},
                    "other-plugin": {"version": "0.1.0", "enabled": False},
                },
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
    """Installed = a manifest on disk; enabled = config.json. The retired
    plugin_state.json is not read: its "other-plugin" is not installed and
    not configured, so a restore must not install it."""
    plugins = list_installed_plugins(project)
    assert plugins == [{"plugin_id": "my-plugin", "version": "1.2.3", "enabled": True}]


def test_list_installed_plugins_reads_enabled_from_config(project: Path) -> None:
    """The display's rule: only "enabled": true is enabled; a plugin with no
    config section, or no flag, is disabled."""
    for pid in ("quiet-plugin", "unconfigured-plugin"):
        d = project / "plugin-repos" / pid
        d.mkdir()
        (d / "manifest.json").write_text(json.dumps({"id": pid, "version": "2.0.0"}),
                                         encoding="utf-8")
    config_path = project / "config" / "config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["quiet-plugin"] = {"favorites": []}
    config_path.write_text(json.dumps(config), encoding="utf-8")

    by_id = {p["plugin_id"]: p for p in list_installed_plugins(project)}

    assert by_id["my-plugin"]["enabled"] is True
    assert by_id["quiet-plugin"]["enabled"] is False
    assert by_id["unconfigured-plugin"]["enabled"] is False
    assert by_id["quiet-plugin"]["version"] == "2.0.0"


def test_list_installed_plugins_without_a_state_file(project: Path) -> None:
    """Nothing depends on plugin_state.json being there."""
    (project / "data" / "plugin_state.json").unlink()
    assert [p["plugin_id"] for p in list_installed_plugins(project)] == ["my-plugin"]


def test_backup_restore_round_trip_ignores_the_retired_state_file(
        project: Path, empty_project: Path, tmp_path: Path) -> None:
    """A backup made on a device that still has plugin_state.json restores
    the installed plugins and their enabled state (from config.json), and
    carries no state file of its own."""
    zip_path = create_backup(project, output_dir=tmp_path / "exports")
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
        listed = json.loads(zf.read("plugins.json"))
    assert not any("plugin_state" in n for n in names)
    assert listed == [{"plugin_id": "my-plugin", "version": "1.2.3", "enabled": True}]

    result = restore_backup(zip_path, empty_project, RestoreOptions())

    assert result.success, result.errors
    assert result.plugins_to_install == [{"plugin_id": "my-plugin", "version": "1.2.3"}]
    restored = json.loads((empty_project / "config" / "config.json").read_text())
    assert restored["my-plugin"]["enabled"] is True
    assert not (empty_project / "data" / "plugin_state.json").exists()


def test_restore_of_a_backup_listing_a_state_file_only_plugin(
        project: Path, empty_project: Path, tmp_path: Path) -> None:
    """A backup written by an older release could list a plugin known only
    to plugin_state.json. Restore reads plugins.json as written, so such a
    backup still restores everything it lists."""
    zip_path = tmp_path / "old.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("manifest.json", json.dumps({
            "schema_version": 1, "created_at": "2026-01-01T00:00:00Z",
            "ledmatrix_version": "3.6.0", "hostname": "old",
            "contents": ["config", "plugins"]}))
        zf.writestr("config/config.json", json.dumps({"my-plugin": {"enabled": True}}))
        zf.writestr("plugins.json", json.dumps([
            {"plugin_id": "my-plugin", "version": "1.2.3", "enabled": True},
            {"plugin_id": "other-plugin", "version": "0.1.0", "enabled": False},
        ]))

    result = restore_backup(zip_path, empty_project, RestoreOptions())

    assert result.success, result.errors
    assert {p["plugin_id"] for p in result.plugins_to_install} == {"my-plugin", "other-plugin"}


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


def test_manifest_version_is_the_core_release(project: Path, tmp_path: Path) -> None:
    """Not a git sha or a truncated "ref: refs/he..." read from .git/HEAD."""
    from src import __version__
    git = project / ".git"
    git.mkdir()
    (git / "HEAD").write_text("ref: refs/heads/some-branch-that-is-not-there\n", encoding="utf-8")
    zip_path = create_backup(project, output_dir=tmp_path / "exports")
    with zipfile.ZipFile(zip_path) as zf:
        manifest = json.loads(zf.read("manifest.json"))
    assert manifest["ledmatrix_version"] == __version__


def test_installed_plugins_come_from_the_configured_directory(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    (root / "config").mkdir(parents=True)
    (root / "config" / "config.json").write_text(
        json.dumps({"plugin_system": {"plugins_directory": "plugins"}}), encoding="utf-8")
    plugin_dir = root / "plugins" / "dev-plugin"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "manifest.json").write_text(
        json.dumps({"id": "dev-plugin", "version": "0.3.0"}), encoding="utf-8")

    assert [p["plugin_id"] for p in list_installed_plugins(root)] == ["dev-plugin"]


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
        zf.writestr("manifest.json", json.dumps({"schema_version": SCHEMA_VERSION, "contents": []}))
        zf.writestr("../../etc/passwd", "x")
    ok, err, _ = validate_backup(zip_path)
    assert not ok
    assert "unsafe" in err.lower()


def test_validate_backup_not_a_zip(tmp_path: Path) -> None:
    p = tmp_path / "nope.zip"
    p.write_text("hello", encoding="utf-8")
    ok, _err, _ = validate_backup(p)
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

    assert "ytm_auth" in result.restored
    restored_ytm = json.loads((empty_project / "config" / "ytm_auth.json").read_text())
    assert restored_ytm["token"] == "YTM-TOKEN"

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
    # ytm_auth rides on restore_wifi rather than its own flag -- disabling
    # wifi restore must not leave a stale session token behind.
    assert "ytm_auth" in result.skipped
    assert not (empty_project / "config" / "ytm_auth.json").exists()


def test_restore_rejects_malicious_zip(empty_project: Path, tmp_path: Path) -> None:
    zip_path = tmp_path / "bad.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"schema_version": SCHEMA_VERSION, "contents": []}))
        zf.writestr("../escape.txt", "x")
    result = restore_backup(zip_path, empty_project, RestoreOptions())
    # validate_backup catches it before extraction.
    assert not result.success
    assert any("unsafe" in e.lower() for e in result.errors)


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="simulates root-owned POSIX files with chmod 0o444, which on Windows sets the "
    "read-only attribute, and Windows refuses to rename over a read-only file",
)
def test_restore_over_a_file_the_user_cannot_write(
    project: Path, empty_project: Path, tmp_path: Path
) -> None:
    """Restore must not need write permission on the destination *file*.

    Reproduces what a fresh install leaves behind: config files owned by root
    and only group-readable, while the web interface that performs the restore
    runs as a non-root user. shutil.copy2 opens the destination for writing and
    failed with EACCES; writing alongside and renaming needs only directory
    permission, which that account has.

    Simulated here by making the destination read-only — the owner cannot
    open it for writing either, but can still replace it within its directory.
    """
    zip_path = create_backup(project, output_dir=tmp_path / "exports")

    # Pre-existing, read-only destinations.
    (empty_project / "config").mkdir(parents=True, exist_ok=True)
    for name in ("config.json", "config_secrets.json", "wifi_config.json", "ytm_auth.json"):
        target = empty_project / "config" / name
        target.write_text("{}", encoding="utf-8")
        target.chmod(0o444)

    result = restore_backup(zip_path, empty_project, RestoreOptions())

    assert result.success, result.errors
    for section in ("config", "secrets", "wifi", "ytm_auth"):
        assert section in result.restored, f"{section} not restored: {result.errors}"

    restored = json.loads((empty_project / "config" / "config.json").read_text())
    assert restored["my-plugin"]["favorites"] == ["A", "B"]

    # The destination's mode is preserved rather than widened to the umask.
    assert stat.S_IMODE((empty_project / "config" / "config_secrets.json").stat().st_mode) == 0o444


def _existing_config(empty_project: Path) -> None:
    (empty_project / "config").mkdir(parents=True, exist_ok=True)
    for name in ("config.json", "config_secrets.json", "wifi_config.json", "ytm_auth.json"):
        (empty_project / "config" / name).write_text("{}", encoding="utf-8")


def test_restore_over_existing_files_without_os_chown(
    project: Path, empty_project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Restore must work where the OS has no file ownership API (Windows).

    Replacing a file tries to carry its previous owner across with os.chown.
    That name does not exist on Windows, and the AttributeError is not an
    OSError, so it escaped every per-section handler: restoring over any
    existing config aborted the whole restore and left the old files in place.
    """
    zip_path = create_backup(project, output_dir=tmp_path / "exports")
    _existing_config(empty_project)
    monkeypatch.delattr(os, "chown", raising=False)

    result = restore_backup(zip_path, empty_project, RestoreOptions())

    assert result.success, result.errors
    for section in ("config", "secrets", "wifi", "ytm_auth"):
        assert section in result.restored, f"{section} not restored: {result.errors}"
    restored = json.loads((empty_project / "config" / "config.json").read_text())
    assert restored["my-plugin"]["favorites"] == ["A", "B"]


def test_restore_still_carries_the_previous_owner_across(
    project: Path, empty_project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Where os.chown exists, the replaced file keeps the old file's owner."""
    zip_path = create_backup(project, output_dir=tmp_path / "exports")
    _existing_config(empty_project)
    target = empty_project / "config" / "config.json"
    old = target.stat()
    chown = MagicMock()
    monkeypatch.setattr(os, "chown", chown, raising=False)

    result = restore_backup(zip_path, empty_project, RestoreOptions(
        restore_secrets=False, restore_wifi=False,
        restore_fonts=False, restore_plugin_uploads=False, reinstall_plugins=False,
    ))

    assert result.success, result.errors
    owners = {(c.args[1], c.args[2]) for c in chown.call_args_list}
    assert owners == {(old.st_uid, old.st_gid)}


def test_restore_onto_a_fresh_device_keeps_secrets_private(
    project: Path, empty_project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no existing file to take a mode from, the restored file took the
    extracted temp file's umask mode -- 0o644, so config_secrets.json,
    wifi_config.json and ytm_auth.json came back world-readable.

    Recorded through os.chmod because Windows cannot represent 0o640.
    """
    zip_path = create_backup(project, output_dir=tmp_path / "exports")
    chmods = []
    real_chmod = os.chmod

    def recording_chmod(path, mode, *args, **kwargs):
        chmods.append((Path(path).name.lstrip("."), mode))
        return real_chmod(path, mode, *args, **kwargs)

    monkeypatch.setattr(os, "chmod", recording_chmod)

    result = restore_backup(zip_path, empty_project, RestoreOptions(
        restore_fonts=False, restore_plugin_uploads=False, reinstall_plugins=False,
    ))

    assert result.success, result.errors
    private = {name.split(".json")[0]: mode for name, mode in chmods
               if name.startswith(("config_secrets.json", "wifi_config.json", "ytm_auth.json"))}
    assert private == {"config_secrets": 0o640, "wifi_config": 0o640, "ytm_auth": 0o640}
    assert all(mode != 0o640 for name, mode in chmods if name.startswith("config.json"))


def test_a_manifest_that_is_not_an_object_is_skipped(project: Path) -> None:
    broken = project / "plugin-repos" / "broken"
    broken.mkdir()
    (broken / "manifest.json").write_text("[1, 2]", encoding="utf-8")

    ids = [p["plugin_id"] for p in list_installed_plugins(project)]

    assert "my-plugin" in ids and "broken" not in ids


def test_same_second_exports_do_not_overwrite_each_other(
    project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import datetime as real_datetime

    class FrozenDatetime:
        @staticmethod
        def now(*a, **k):
            return real_datetime(2026, 1, 2, 3, 4, 5)

    monkeypatch.setattr(backup_manager, "datetime", FrozenDatetime)
    out = tmp_path / "exports"

    first = create_backup(project, output_dir=out)
    second = create_backup(project, output_dir=out)

    assert first != second
    assert first.exists() and second.exists()
    assert second.name == first.name[:-len(".zip")] + "-2.zip"
    assert not list(out.glob("*.tmp"))


def test_export_name_is_claimed_atomically(
    project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Two exports racing both see "no such file" before either publishes.
    # Simulate that by making exists() always say no: the name must still be
    # claimed exclusively, so the second export gets -2 instead of replacing
    # the first archive.
    from datetime import datetime as real_datetime

    class FrozenDatetime:
        @staticmethod
        def now(*a, **k):
            return real_datetime(2026, 1, 2, 3, 4, 5)

    monkeypatch.setattr(backup_manager, "datetime", FrozenDatetime)
    out = tmp_path / "exports"
    first = create_backup(project, output_dir=out)
    first_bytes = first.read_bytes()

    monkeypatch.setattr(Path, "exists", lambda self: False)
    second = create_backup(project, output_dir=out)
    monkeypatch.undo()

    assert second != first
    assert first.read_bytes() == first_bytes
    assert zipfile.is_zipfile(second)
