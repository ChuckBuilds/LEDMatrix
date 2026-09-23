"""starlark-apps: the root display service must not lock the web UI out.

Reported by a user after a fresh install: installing an app from the Starlark
tab failed with "install failed: Failed to install from repository", and so did
uploading a .star file and installing from a GitHub directory. The cause was
ownership, which the error named nowhere -- they found it only by reading the
service logs, and fixed it with

    sudo chown -R ledpi:ledpi /home/ledpi/LEDMatrix/starlark-apps

The starlark-apps directory is not in the repository, so it is created lazily
by whichever process reaches it first. Those processes run as different users:
systemd/ledmatrix.service is `User=root` and constructs this plugin at startup
(which is what calls _get_apps_directory), while systemd/ledmatrix-web.service
runs as the login user and is what actually installs apps. The documented
first step is to install pixlet and reboot, so on a fresh machine the display
service usually wins the race and the directory lands root-owned.

The web user cannot repair that -- chown needs root. So root hands the
directory over itself, every startup, which also heals machines already broken
by this.
"""

import importlib.util
import os
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

PLUGIN_DIR = Path(__file__).resolve().parent.parent / "plugin-repos" / "starlark-apps"


@pytest.fixture(scope="module")
def manager_module():
    if not PLUGIN_DIR.exists():
        pytest.skip("starlark-apps plugin is not checked out")
    sys.path.insert(0, str(PLUGIN_DIR))
    injected_fcntl = "fcntl" not in sys.modules
    if injected_fcntl:
        stub = types.ModuleType("fcntl")
        stub.LOCK_EX, stub.LOCK_UN = 2, 8
        stub.flock = lambda *a, **kw: None
        sys.modules["fcntl"] = stub
    try:
        spec = importlib.util.spec_from_file_location(
            "starlark_manager_ownership", PLUGIN_DIR / "manager.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except ImportError as e:
        # Only a genuinely absent dependency is a skip. A syntax error or a
        # NameError in the plugin is a regression these tests exist to catch,
        # and swallowing it here would turn a red suite green.
        if any(dep in str(e) for dep in ("PIL", "Pillow", "pixlet", "frame_extractor")):
            pytest.skip(f"starlark-apps optional dependency missing: {e}")
        raise
    finally:
        sys.path.remove(str(PLUGIN_DIR))
        if injected_fcntl:
            sys.modules.pop("fcntl", None)


def _plugin(manager_module):
    """A manager with __init__ bypassed -- only ownership paths are tested."""
    cls = manager_module.StarlarkAppsPlugin
    inst = cls.__new__(cls)
    inst.logger = MagicMock()
    return inst


class _Stat:
    """A real stat_result with only the ownership fields overridden.

    Everything else is delegated to the genuine result. A stub carrying just
    st_uid/st_gid passed locally but broke in CI, because pathlib itself reads
    st_mode while walking the tree on some Python versions -- and the fields
    it needs are an implementation detail, not something this test should be
    asserting about.
    """

    def __init__(self, real, uid, gid):
        self._real = real
        self.st_uid = uid
        self.st_gid = gid

    def __getattr__(self, name):
        return getattr(self._real, name)


@pytest.fixture
def owned(monkeypatch):
    """Let a test declare a fake uid/gid for specific paths.

    Real ownership cannot be faked without root, and these tests must run as
    an ordinary user in CI.
    """
    fake = {}
    real_stat = Path.stat
    real_lstat = os.lstat

    def patched_stat(self, *args, **kwargs):
        st = real_stat(self, *args, **kwargs)
        key = str(self)
        return _Stat(st, *fake[key]) if key in fake else st

    def patched_lstat(path, *args, **kwargs):
        st = real_lstat(path, *args, **kwargs)
        key = str(path)
        return _Stat(st, *fake[key]) if key in fake else st

    # Both, because the code reads the checkout owner through Path.stat and
    # each entry it repairs through os.lstat -- lstat so a symlink reports
    # itself rather than its target.
    monkeypatch.setattr(Path, "stat", patched_stat)
    monkeypatch.setattr(os, "lstat", patched_lstat)
    return fake


@pytest.fixture
def as_root(monkeypatch):
    """Run the handover as root, recording chowns instead of performing them."""
    calls = []
    monkeypatch.setattr("os.geteuid", lambda: 0)
    monkeypatch.setattr(
        "os.chown",
        lambda p, uid, gid, **kw: calls.append((str(p), uid, gid)))
    return calls


def _tree(tmp_path):
    """A project root with an apps directory holding one installed app."""
    apps = tmp_path / "starlark-apps"
    (apps / "analogclock").mkdir(parents=True)
    (apps / "analogclock" / "analog_clock.star").write_text("# app")
    (apps / "manifest.json").write_text("{}")
    return apps


class TestRootHandsTheDirectoryOver:
    def test_root_created_directory_is_given_to_the_checkout_owner(
            self, manager_module, tmp_path, owned, as_root):
        apps = _tree(tmp_path)
        owned[str(tmp_path)] = (1000, 1000)      # checkout belongs to the login user
        owned[str(apps)] = (0, 0)                # but root got there first

        _plugin(manager_module)._hand_apps_dir_to_checkout_owner(apps, tmp_path)

        assert (str(apps), 1000, 1000) in as_root

    def test_contents_are_repaired_not_just_the_directory(
            self, manager_module, tmp_path, owned, as_root):
        """An install broken by this leaves root-owned files inside it too."""
        apps = _tree(tmp_path)
        owned[str(tmp_path)] = (1000, 1000)
        for p in (apps, apps / "analogclock",
                  apps / "analogclock" / "analog_clock.star",
                  apps / "manifest.json"):
            owned[str(p)] = (0, 0)

        _plugin(manager_module)._hand_apps_dir_to_checkout_owner(apps, tmp_path)

        chowned = {c[0] for c in as_root}
        assert str(apps / "analogclock" / "analog_clock.star") in chowned
        assert str(apps / "manifest.json") in chowned

    def test_nothing_is_touched_when_ownership_is_already_right(
            self, manager_module, tmp_path, owned, as_root):
        apps = _tree(tmp_path)
        owned[str(tmp_path)] = (1000, 1000)
        for p in (apps, apps / "analogclock",
                  apps / "analogclock" / "analog_clock.star",
                  apps / "manifest.json"):
            owned[str(p)] = (1000, 1000)

        _plugin(manager_module)._hand_apps_dir_to_checkout_owner(apps, tmp_path)

        assert as_root == []


class TestItDoesNotOverreach:
    def test_a_non_root_process_changes_nothing(
            self, manager_module, tmp_path, owned, monkeypatch):
        """The web service also calls this. It has no right to chown anything."""
        apps = _tree(tmp_path)
        owned[str(tmp_path)] = (1000, 1000)
        owned[str(apps)] = (0, 0)
        calls = []
        monkeypatch.setattr("os.geteuid", lambda: 1000)
        monkeypatch.setattr("os.chown", lambda p, u, g, **kw: calls.append(p))

        _plugin(manager_module)._hand_apps_dir_to_checkout_owner(apps, tmp_path)

        assert calls == []

    def test_a_genuinely_root_owned_checkout_is_left_alone(
            self, manager_module, tmp_path, owned, as_root):
        """Installed as root on purpose: there is nobody to hand it to."""
        apps = _tree(tmp_path)
        owned[str(tmp_path)] = (0, 0)
        owned[str(apps)] = (0, 0)

        _plugin(manager_module)._hand_apps_dir_to_checkout_owner(apps, tmp_path)

        assert as_root == []

    def test_a_failed_chown_warns_and_does_not_raise(
            self, manager_module, tmp_path, owned, monkeypatch):
        """Startup must not die because one file could not be handed over."""
        apps = _tree(tmp_path)
        owned[str(tmp_path)] = (1000, 1000)
        owned[str(apps)] = (0, 0)
        monkeypatch.setattr("os.geteuid", lambda: 0)

        def boom(*_a, **_kw):
            raise OSError("read-only file system")

        monkeypatch.setattr("os.chown", boom)
        plugin = _plugin(manager_module)

        plugin._hand_apps_dir_to_checkout_owner(apps, tmp_path)   # must not raise

        assert plugin.logger.warning.called


class TestTheDirectoryGetterUsesIt:
    def test_get_apps_directory_performs_the_handover(
            self, manager_module, monkeypatch):
        """Pins the wiring: creating the directory without handing it over is
        exactly the bug.

        This calls the real getter, which resolves to the checkout's own
        starlark-apps directory, so it removes the directory again when the
        test was what created it.
        """
        plugin = _plugin(manager_module)
        seen = []
        monkeypatch.setattr(
            type(plugin), "_hand_apps_dir_to_checkout_owner",
            lambda self, apps, root: seen.append((apps, root)))
        expected = PLUGIN_DIR.parent.parent / "starlark-apps"
        pre_existing = expected.exists()

        try:
            result = plugin._get_apps_directory()

            assert result == expected and result.exists()
            assert seen and seen[0] == (expected, expected.parent)
        finally:
            if not pre_existing and expected.exists():
                try:
                    expected.rmdir()
                except OSError:
                    pass


class TestTheErrorNamesTheCause:
    """The reporter saw only "Failed to install from repository".

    That message names no path and no cause, so the only way to the answer was
    reading the service logs. The handover above should stop the failure
    happening at all; this makes the failure legible if it ever does.
    """

    @pytest.fixture(scope="class")
    def hint(self):
        try:
            from web_interface.blueprints.api_v3.starlark import _ownership_hint
        except ImportError as e:
            # Same rule as above: absent Flask is a skip, a broken module is not.
            if "flask" in str(e).lower():
                pytest.skip(f"Flask is not installed here: {e}")
            raise
        return _ownership_hint

    def test_a_permission_error_explains_itself(self, hint):
        message = hint(PermissionError(13, "Permission denied"))
        assert message
        assert "chown" in message
        assert "starlark-apps" in message

    def test_it_points_at_the_automatic_repair_first(self, hint):
        """Restarting the display service is the fix that needs no root user
        to understand it -- the manual chown is the fallback."""
        message = hint(PermissionError(13, "Permission denied"))
        assert "systemctl restart ledmatrix" in message
        assert message.index("systemctl") < message.index("chown")

    def test_other_failures_are_not_mislabelled(self, hint):
        """A network error must not be reported as an ownership problem."""
        for err in (ValueError("bad json"), OSError(28, "No space left on device"),
                    TimeoutError("github timed out")):
            assert hint(err) is None


class TestItWillNotBeTrickedIntoGivingAwayAFile:
    """Root chowning a tree is a privilege-escalation primitive if it follows
    links: anyone who can write in the directory could point one at a
    root-owned file and have this hand it over."""

    def test_a_symlink_is_never_followed(self, manager_module, tmp_path, owned, as_root):
        apps = _tree(tmp_path)
        target = tmp_path / "precious"
        target.write_text("root-owned secret")
        (apps / "evil").symlink_to(target)
        owned[str(tmp_path)] = (1000, 1000)
        owned[str(apps)] = (0, 0)
        # The link must look like it NEEDS handing over, or it would be
        # skipped for already having the right owner and this test would pass
        # without ever exercising the symlink check.
        owned[str(apps / "evil")] = (0, 0)
        owned[str(target)] = (0, 0)

        _plugin(manager_module)._hand_apps_dir_to_checkout_owner(apps, tmp_path)

        chowned = {c[0] for c in as_root}
        assert str(target) not in chowned
        assert str(apps / "evil") not in chowned

    def test_the_directory_is_handed_over_last(self, manager_module, tmp_path, owned, as_root):
        """Its contents must be settled before the container changes hands."""
        apps = _tree(tmp_path)
        owned[str(tmp_path)] = (1000, 1000)
        for p in (apps, apps / "analogclock",
                  apps / "analogclock" / "analog_clock.star",
                  apps / "manifest.json"):
            owned[str(p)] = (0, 0)

        _plugin(manager_module)._hand_apps_dir_to_checkout_owner(apps, tmp_path)

        order = [c[0] for c in as_root]
        assert order[-1] == str(apps)


class TestAPermissionFailureReachesTheCaller:
    def test_install_app_does_not_swallow_permission_errors(
            self, manager_module, tmp_path, monkeypatch):
        """A False here reads as "this app is broken" and routes to a generic
        message -- which is how the ownership bug stayed invisible."""
        plugin = _plugin(manager_module)
        plugin.apps_dir = tmp_path
        plugin.apps = {}

        def denied(self, *a, **kw):
            raise PermissionError(13, "Permission denied")

        monkeypatch.setattr(Path, "mkdir", denied)

        with pytest.raises(PermissionError):
            plugin.install_app("analogclock", str(tmp_path / "x.star"), {})

    def test_other_install_failures_still_return_false(
            self, manager_module, tmp_path, monkeypatch):
        """Only permission errors are promoted; the bool contract is intact."""
        plugin = _plugin(manager_module)
        plugin.apps_dir = tmp_path
        plugin.apps = {}

        def broken(self, *a, **kw):
            raise OSError(28, "No space left on device")

        monkeypatch.setattr(Path, "mkdir", broken)

        assert plugin.install_app("analogclock", str(tmp_path / "x.star"), {}) is False
