"""A dev plugin linked in under a name its checkout does not share still loads.

``scripts/dev/dev_plugin_setup.sh`` links a checkout into the plugins
directory under the plugin's id: ``link-github foo <url>`` clones
``ledmatrix-foo`` (the repository naming convention) and links it as
``plugins/foo``. ``contained_plugin_dir`` resolved the link and looked for the
*target's* folder name, ``ledmatrix-foo``, among the plugins directory's
entries. There is none, so ``install_dependencies`` refused the plugin as
outside the plugins directory and the load failed with "Dependency
installation failed" -- even with no requirements.txt at all.

The containment it exists for still holds: the answer is always rebuilt from
an entry enumerated under the plugins directory.

Skipped where this process cannot create a symlink (Windows without the
privilege).
"""

import os
from unittest.mock import MagicMock, patch

import pytest

from src.plugin_system.plugin_loader import PluginLoader, contained_plugin_dir


def _symlink_or_skip(target, link):
    try:
        os.symlink(target, link, target_is_directory=True)
    except (OSError, NotImplementedError) as e:
        pytest.skip(f"cannot create a symlink here: {e}")


@pytest.fixture
def linked(tmp_path):
    checkout = tmp_path / "dev-plugins" / "ledmatrix-foo"
    checkout.mkdir(parents=True)
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    link = plugins_dir / "foo"
    _symlink_or_skip(checkout, link)
    return plugins_dir, link, checkout


def test_a_link_resolves_to_its_own_entry_in_the_plugins_dir(linked):
    plugins_dir, link, _checkout = linked

    assert contained_plugin_dir(link, plugins_dir) == os.path.join(
        os.path.realpath(plugins_dir), "foo")


def test_a_linked_plugin_without_requirements_needs_no_install(linked):
    plugins_dir, link, _checkout = linked

    with patch("subprocess.run") as pip:
        assert PluginLoader().install_dependencies(link, "foo", plugins_dir=plugins_dir) is True
    pip.assert_not_called()


@patch("src.plugin_system.plugin_loader.requirements_are_satisfied", return_value=False)
def test_a_linked_plugins_requirements_are_installed_through_the_link(_satisfied, linked):
    plugins_dir, link, checkout = linked
    (checkout / "requirements.txt").write_text("package1==1.0.0\n", encoding="utf-8")

    with patch("subprocess.run", return_value=MagicMock(returncode=0, stderr="")) as pip:
        assert PluginLoader().install_dependencies(link, "foo", plugins_dir=plugins_dir) is True

    argv = pip.call_args[0][0]
    assert argv[argv.index("-r") + 1] == os.path.join(
        os.path.realpath(plugins_dir), "foo", "requirements.txt")


def test_a_link_outside_the_plugins_dir_is_still_refused(linked, tmp_path):
    plugins_dir, _link, checkout = linked
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    stray = elsewhere / "bar"
    _symlink_or_skip(checkout, stray)

    assert contained_plugin_dir(stray, plugins_dir) is None
    assert contained_plugin_dir(plugins_dir / ".." / "elsewhere" / "bar", plugins_dir) is None
