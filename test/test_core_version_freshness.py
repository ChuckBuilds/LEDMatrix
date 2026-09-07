"""The gate must read the core version from disk, not from its own import.

`from src import __version__` binds whatever the process loaded at start. The
web UI is a long-lived service of its own (ledmatrix-web.service), and updating
the core replaces files on disk without restarting it -- the update route says
so and asks the user to restart, but its prompt named only the *display*
service, so a user who followed it left the web process holding the old number.

The plugin store's gate lives in that web process, so being stale by exactly
one release is the case that bites: every plugin flooring on the release you
just installed is refused, with a message blaming a core version that is
already correct on disk.

Observed on hardware: after updating a rig to 3.3.0 and restarting only the
display service, all eight sports scoreboards were refused with
"supports LEDMatrix >=3.3.0, but this system is running 3.2.0" while
src/__init__.py on that machine read 3.3.0.
"""

import importlib
import sys

import pytest

from src.plugin_system import compatibility


class TestCurrentCoreVersion:
    def test_it_reads_the_file_rather_than_the_imported_value(self, monkeypatch, tmp_path):
        # Simulate a process whose import predates the update: the module
        # object says 3.2.0 while the file on disk says 3.3.0.
        import src
        monkeypatch.setattr(src, "__version__", "3.2.0")
        fake = tmp_path / "__init__.py"
        fake.write_text('__version__ = "3.3.0"\n', encoding="utf-8")
        monkeypatch.setattr(compatibility, "_VERSION_FILE", fake)
        assert compatibility.current_core_version() == "3.3.0"

    def test_it_matches_the_real_file_by_default(self):
        import src
        assert compatibility.current_core_version() == src.__version__

    @pytest.mark.parametrize("body", [
        "__version__ = '3.4.1'\n",
        '__version__="3.4.1"\n',
        '"""doc"""\n\n__version__ = "3.4.1"  # trailing comment\n',
    ])
    def test_it_tolerates_the_ways_that_line_gets_written(self, monkeypatch, tmp_path, body):
        fake = tmp_path / "__init__.py"
        fake.write_text(body, encoding="utf-8")
        monkeypatch.setattr(compatibility, "_VERSION_FILE", fake)
        assert compatibility.current_core_version() == "3.4.1"

    def test_a_missing_file_falls_back_to_the_import(self, monkeypatch, tmp_path):
        # Never worse than before: an unreadable file returns what the old
        # code would have returned.
        import src
        monkeypatch.setattr(src, "__version__", "3.2.0")
        monkeypatch.setattr(compatibility, "_VERSION_FILE", tmp_path / "gone.py")
        assert compatibility.current_core_version() == "3.2.0"

    def test_a_file_without_the_line_falls_back(self, monkeypatch, tmp_path):
        import src
        monkeypatch.setattr(src, "__version__", "3.2.0")
        fake = tmp_path / "__init__.py"
        fake.write_text("# no version here\n", encoding="utf-8")
        monkeypatch.setattr(compatibility, "_VERSION_FILE", fake)
        assert compatibility.current_core_version() == "3.2.0"

    def test_it_never_raises(self, monkeypatch, tmp_path):
        # This runs on the install path; an exception here would surface as a
        # failed update rather than a version mismatch.
        bad = tmp_path / "__init__.py"
        bad.write_bytes(b"\xff\xfe\x00 not utf-8 \xff")
        monkeypatch.setattr(compatibility, "_VERSION_FILE", bad)
        assert isinstance(compatibility.current_core_version(), str)


class TestTheBugItFixes:
    def test_a_stale_import_no_longer_refuses_a_compatible_plugin(self, monkeypatch, tmp_path):
        """The exact hardware failure, as a test."""
        import src
        monkeypatch.setattr(src, "__version__", "3.2.0")     # what the process holds
        fake = tmp_path / "__init__.py"
        fake.write_text('__version__ = "3.3.0"\n', encoding="utf-8")   # what is on disk
        monkeypatch.setattr(compatibility, "_VERSION_FILE", fake)

        manifest = {"name": "Hockey Scoreboard", "min_ledmatrix_version": "3.3.0"}

        stale_ok, _ = compatibility.check(manifest, src.__version__)
        assert stale_ok is False, "precondition: the stale value is what refused it"

        fresh_ok, reason = compatibility.check(
            manifest, compatibility.current_core_version())
        assert fresh_ok is True, f"the disk version must allow it, got: {reason}"

    def test_it_still_refuses_when_the_core_really_is_too_old(self, monkeypatch, tmp_path):
        # The gate must not become permissive: a genuinely old core still says no.
        fake = tmp_path / "__init__.py"
        fake.write_text('__version__ = "3.2.0"\n', encoding="utf-8")
        monkeypatch.setattr(compatibility, "_VERSION_FILE", fake)
        ok, reason = compatibility.check(
            {"name": "Hockey", "min_ledmatrix_version": "3.3.0"},
            compatibility.current_core_version())
        assert ok is False
        assert "3.2.0" in (reason or "")


class TestCallSites:
    @pytest.mark.parametrize("module", [
        "src.plugin_system.store_manager",
        "src.plugin_system.plugin_loader",
    ])
    def test_no_gate_binds_the_version_at_import(self, module):
        """Catch a future call site reintroducing the stale read."""
        import inspect
        mod = importlib.import_module(module)
        source = inspect.getsource(mod)
        assert "from src import __version__ as core_version" not in source, (
            f"{module} binds __version__ at import; use "
            f"compatibility.current_core_version() so a long-lived process "
            f"sees a core update.")
