"""Removing a dev plugin linked into the plugins directory removes the link.

``scripts/dev/dev_plugin_setup.sh`` symlinks a checkout into the plugins
directory. ``PluginStoreManager._safe_remove_directory`` -- behind uninstall,
and behind discarding the set-aside copy after an install or update -- handed
the link to ``shutil.rmtree``, which refuses a symlink. Its fallback then
walked through the link and chmodded every directory and file of the linked
checkout to 0700, and the sudo stage refused a path outside the plugins
directory. So the uninstall failed, the link stayed, and the developer's
checkout lost its group/other permissions and gained execute bits.

Skipped where this process cannot create a symlink (Windows without the
privilege).
"""

import json
import os
from unittest.mock import MagicMock

import pytest

from src.plugin_system.store_manager import PluginStoreManager

PLUGIN_ID = "linked-demo"


def _symlink_or_skip(target, link):
    try:
        os.symlink(target, link, target_is_directory=True)
    except (OSError, NotImplementedError) as e:
        pytest.skip(f"cannot create a symlink here: {e}")


@pytest.fixture
def linked(tmp_path):
    checkout = tmp_path / "dev-plugins" / PLUGIN_ID
    checkout.mkdir(parents=True)
    (checkout / "manifest.json").write_text(
        json.dumps({"id": PLUGIN_ID, "name": "Linked", "class_name": "P",
                    "display_modes": ["linked"], "version": "1.0.0"}),
        encoding="utf-8")
    (checkout / "manager.py").write_text("X = 1\n", encoding="utf-8")
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    link = plugins_dir / PLUGIN_ID
    _symlink_or_skip(checkout, link)
    store = PluginStoreManager(plugins_dir=str(plugins_dir))
    store.logger = MagicMock()
    return store, link, checkout


def test_removing_a_linked_plugin_removes_only_the_link(linked):
    store, link, checkout = linked

    assert store._safe_remove_directory(link) is True

    assert not os.path.lexists(link)
    assert (checkout / "manager.py").read_text(encoding="utf-8") == "X = 1\n"


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits")
def test_removing_a_linked_plugin_leaves_the_checkouts_permissions(linked):
    store, link, checkout = linked
    os.chmod(checkout, 0o755)
    os.chmod(checkout / "manager.py", 0o644)

    store._safe_remove_directory(link)

    assert checkout.stat().st_mode & 0o777 == 0o755
    assert (checkout / "manager.py").stat().st_mode & 0o777 == 0o644


def test_uninstalling_a_linked_plugin_removes_the_link(linked):
    store, link, checkout = linked

    assert store.uninstall_plugin(PLUGIN_ID) is True

    assert not os.path.lexists(link)
    assert (checkout / "manifest.json").exists()


def test_a_dangling_link_is_removed_too(linked):
    store, link, checkout = linked
    for child in checkout.iterdir():
        child.unlink()
    checkout.rmdir()

    assert store._safe_remove_directory(link) is True

    assert not os.path.lexists(link)
