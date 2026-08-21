#!/usr/bin/env python3
"""A restore must not repoint this device at another machine's panel.

display.hardware describes the panel physically wired to this device -- cols,
rows, chain_length, hardware_mapping, panel_type, multiplexing, the refresh
cap. A backup carries the panel of the machine it was taken on. Restoring a
512x64 rig's backup onto a 128x32 one used to overwrite the smaller panel's
geometry with the larger one's, and nothing on screen explains why: the
display just stops being right.

That is not hypothetical. It happened, and the rig it happened to had to be
reflashed.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.backup_manager import _restore_config_preserving_hardware  # noqa: E402

BIG = {"display": {"hardware": {"cols": 128, "rows": 64, "chain_length": 4,
                                "hardware_mapping": "adafruit-hat-pwm"},
                   "runtime": {"gpio_slowdown": 4}},
       "timezone": "America/New_York", "some-plugin": {"enabled": True}}
SMALL = {"display": {"hardware": {"cols": 64, "rows": 32, "chain_length": 2,
                                  "hardware_mapping": "regular"},
                     "runtime": {"gpio_slowdown": 2}},
         "timezone": "UTC"}


def _run(tmp, keep, local_text=None, backup_text=None):
    src = tmp / "backup_config.json"
    src.write_text(backup_text if backup_text is not None else json.dumps(BIG))
    dst = tmp / "config.json"
    dst.write_text(local_text if local_text is not None else json.dumps(SMALL))
    _restore_config_preserving_hardware(src, dst, keep_hardware=keep)
    return json.loads(dst.read_text())


def test_local_panel_survives(tmp_path):
    out = _run(tmp_path, keep=True)
    hw = out["display"]["hardware"]
    assert (hw["cols"], hw["rows"], hw["chain_length"]) == (64, 32, 2), (
        "the restore repointed this device at the backup's panel")
    assert hw["hardware_mapping"] == "regular", "panel wiring came from the backup"


def test_everything_else_is_restored(tmp_path):
    out = _run(tmp_path, keep=True)
    assert out["timezone"] == "America/New_York", "config was not restored"
    assert out["some-plugin"] == {"enabled": True}, "plugin config was not restored"
    assert out["display"]["runtime"] == {"gpio_slowdown": 4}, (
        "only display.hardware should be held back")


def test_opting_in_takes_the_backups_panel(tmp_path):
    out = _run(tmp_path, keep=False)
    hw = out["display"]["hardware"]
    assert (hw["cols"], hw["rows"], hw["chain_length"]) == (128, 64, 4)


def test_a_device_with_no_local_hardware_takes_the_backups(tmp_path):
    src = tmp_path / "b.json"
    src.write_text(json.dumps(BIG))
    dst = tmp_path / "c.json"
    dst.write_text(json.dumps({"timezone": "UTC"}))
    _restore_config_preserving_hardware(src, dst, keep_hardware=True)
    out = json.loads(dst.read_text())
    assert out["display"]["hardware"]["cols"] == 128, (
        "nothing local to preserve, so the backup's panel should be used")


def test_unparseable_local_config_still_restores(tmp_path):
    src = tmp_path / "b.json"
    src.write_text(json.dumps(BIG))
    dst = tmp_path / "c.json"
    dst.write_text("{ not json")
    _restore_config_preserving_hardware(src, dst, keep_hardware=True)
    assert json.loads(dst.read_text())["timezone"] == "America/New_York", (
        "a restore must never fail because of this merge")


def test_the_destination_mode_is_preserved(tmp_path):
    """config.json is installed with a deliberate mode; a merge must not widen it.

    _copy_file preserves the destination's mode and owner on purpose -- these
    files are root-owned while the web interface running the restore is not.
    Writing the merged result directly would have replaced that with whatever
    the umask allowed.
    """
    import os
    import stat as statmod
    src = tmp_path / "b.json"
    src.write_text(json.dumps(BIG))
    dst = tmp_path / "c.json"
    dst.write_text(json.dumps(SMALL))
    os.chmod(dst, 0o600)

    _restore_config_preserving_hardware(src, dst, keep_hardware=True)

    mode = statmod.S_IMODE(os.stat(dst).st_mode)
    assert mode == 0o600, f"restore widened config.json from 0600 to {oct(mode)}"
    assert json.loads(dst.read_text())["display"]["hardware"]["cols"] == 64


def test_no_scratch_file_is_left_behind(tmp_path):
    src = tmp_path / "b.json"
    src.write_text(json.dumps(BIG))
    dst = tmp_path / "c.json"
    dst.write_text(json.dumps(SMALL))
    _restore_config_preserving_hardware(src, dst, keep_hardware=True)
    leftovers = [p.name for p in tmp_path.iterdir() if "tmp" in p.name]
    assert not leftovers, f"scratch files left in place: {leftovers}"


@pytest.mark.parametrize("shape", ["null", "[1, 2]", '"a string"', "42"])
def test_a_non_object_local_config_falls_back_to_a_plain_copy(tmp_path, shape):
    """json.loads accepts any JSON value, not just objects.

    A config.json holding null/[]/"text" parsed fine and then raised
    AttributeError on the first .get(). restore_backup() only catches OSError
    around this call, so that escaped unhandled and aborted the entire
    restore -- the opposite of the plain-copy fallback promised here.
    """
    out = _run(tmp_path, keep=True, local_text=shape)
    assert out == BIG, "the backup was not restored as-is"


@pytest.mark.parametrize("shape", ["null", "[1, 2]", '"a string"', "42"])
def test_a_non_object_backup_config_falls_back_to_a_plain_copy(tmp_path, shape):
    """Same hazard from the other side: the backup itself may not be an object."""
    out = _run(tmp_path, keep=True, backup_text=shape)
    assert out == json.loads(shape), "the backup was not restored as-is"


def test_a_missing_local_config_still_restores(tmp_path):
    """dst.exists() is False -> local defaults to {} and must not trip the guard."""
    src = tmp_path / "backup_config.json"
    src.write_text(json.dumps(BIG))
    dst = tmp_path / "config.json"
    _restore_config_preserving_hardware(src, dst, keep_hardware=True)
    assert json.loads(dst.read_text()) == BIG
