"""Tests for the plugin loader's advisory version-compatibility warning."""

import logging

import pytest

from src.plugin_system.plugin_loader import PluginLoader


@pytest.fixture
def loader():
    return PluginLoader(logger=logging.getLogger("test-loader"))


def _warnings(caplog):
    return [r for r in caplog.records if r.levelno == logging.WARNING]


class TestParseSemver:
    def test_basic(self, loader):
        assert loader._parse_semver("3.1.0") == (3, 1, 0)
        assert loader._parse_semver("v2.0") == (2, 0, 0)
        assert loader._parse_semver("2.0.0-beta.1") == (2, 0, 0)

    def test_unparseable(self, loader):
        assert loader._parse_semver(None) is None
        assert loader._parse_semver(123) is None


class TestWarnIfIncompatible:
    def test_warns_when_plugin_needs_newer_core(self, loader, caplog, monkeypatch):
        import src
        monkeypatch.setattr(src, "__version__", "3.1.0")
        with caplog.at_level(logging.WARNING, logger="test-loader"):
            loader._warn_if_incompatible("p", {"min_ledmatrix_version": "9.0.0"})
        assert len(_warnings(caplog)) == 1
        assert "9.0.0" in _warnings(caplog)[0].message

    def test_silent_when_compatible(self, loader, caplog, monkeypatch):
        import src
        monkeypatch.setattr(src, "__version__", "3.1.0")
        with caplog.at_level(logging.WARNING, logger="test-loader"):
            loader._warn_if_incompatible("p", {"min_ledmatrix_version": "2.0.0"})
        assert not _warnings(caplog)

    def test_silent_when_field_absent(self, loader, caplog):
        with caplog.at_level(logging.WARNING, logger="test-loader"):
            loader._warn_if_incompatible("p", {"name": "no version fields"})
        assert not _warnings(caplog)

    def test_reads_requires_and_versions_spellings(self, loader, caplog, monkeypatch):
        import src
        monkeypatch.setattr(src, "__version__", "3.1.0")
        with caplog.at_level(logging.WARNING, logger="test-loader"):
            loader._warn_if_incompatible(
                "a", {"requires": {"min_ledmatrix_version": "9.0.0"}})
            loader._warn_if_incompatible(
                "b", {"versions": [{"ledmatrix_min_version": "9.0.0"}]})
            loader._warn_if_incompatible(
                "c", {"versions": [{"ledmatrix_min": "9.0.0"}]})
        assert len(_warnings(caplog)) == 3

    def test_stale_core_version_skips_comparison(self, loader, caplog, monkeypatch):
        # Anti-spam guard: a core whose __version__ is below the ecosystem
        # floor must not warn about every plugin.
        import src
        monkeypatch.setattr(src, "__version__", "1.0.0")
        with caplog.at_level(logging.WARNING, logger="test-loader"):
            loader._warn_if_incompatible("p", {"min_ledmatrix_version": "2.0.0"})
        assert not _warnings(caplog)
