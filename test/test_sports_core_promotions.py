"""Tests for the methods promoted onto SportsCore from the nine bundled
plugin copies of ``sports.py`` (phase B1 of docs/SPORTS_UNIFICATION.md).

Three methods and their seams land here:

- ``cleanup()`` — byte-identical in all nine copies. The tests pin the
  ordering (session close, then caches, then the completion log) and the
  deliberate *omission*: the process-wide background service must never be
  shut down by one unloading plugin.
- ``_get_layout_offset()`` — football's resolver-backed variant, with the
  classic inline config read as the fallback used by every plugin that
  doesn't hand core a ``_config_schema_path()``.
- ``_load_custom_font_from_element_config()`` — baseball's body (the only
  copy that handles BDF strikes correctly) under hockey's wider signature,
  resolving font files through the ``_font_root()`` seam instead of the
  process cwd.
"""

import ast
import json
import logging
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PIL import Image, ImageFont

# src.base_classes.sports transitively imports the hardware matrix driver;
# stub it so these tests can import the sports base classes off-device.
sys.modules.setdefault("rgbmatrix", MagicMock())

from src.base_classes.sports import SportsCore

LOGGER = logging.getLogger("test_sports_core_promotions")

CORE_ROOT = Path(__file__).resolve().parents[1]
FONTS_DIR = CORE_ROOT / "assets" / "fonts"
TTF_NAME = "PressStart2P-Regular.ttf"
BDF_NAME = "5x7.bdf"          # a BDF whose only valid strike is 7px
BDF_NATIVE_SIZE = 7


class _StubSports(SportsCore):
    """Minimal concrete SportsCore — the abstract methods are never called
    by anything under test here."""

    def _fetch_data(self):
        return None

    def _extract_game_details(self, game_event):
        return None


@pytest.fixture
def build(monkeypatch, tmp_path):
    """Factory for real SportsCore instances: logo dir redirected to tmp and
    the process-wide background service replaced with a MagicMock so the
    tests can assert nothing ever calls it."""
    monkeypatch.setattr(
        SportsCore, "_initialize_logo_dir", lambda self, configured: tmp_path)
    monkeypatch.setattr(
        "src.base_classes.sports.core.get_background_service",
        lambda *args, **kwargs: MagicMock())

    def _build(config=None, cls=_StubSports):
        display_manager = MagicMock()
        display_manager.matrix.width = 128
        display_manager.matrix.height = 32
        display_manager.width = 128
        display_manager.height = 32
        display_manager.image = Image.new("RGB", (128, 32))
        cache_manager = MagicMock()
        cache_manager.cache_dir = str(tmp_path)
        return cls(config if config is not None else {"timezone": "UTC"},
                   display_manager, cache_manager, LOGGER, "nhl")

    return _build


def probe(config=None):
    """Unbound-call stand-in for hosts we don't need a full instance for
    (same pattern as make_probe in test_sports_base_characterization)."""
    host = MagicMock()
    host.logger = LOGGER
    host.config = config if config is not None else {}
    host._font_cache = {}
    host._bdf_native_size_cache = {}
    host._config_schema_path.return_value = None
    host._font_root.side_effect = lambda: SportsCore._font_root(host)
    host._resolve_font_path.side_effect = (
        lambda name: SportsCore._resolve_font_path(host, name))
    return host


def offset(host, element, axis, default=0):
    return SportsCore._get_layout_offset(host, element, axis, default)


def load_font(host, *args, **kwargs):
    return SportsCore._load_custom_font_from_element_config(host, *args, **kwargs)


# ---------------------------------------------------------------------------
# 1. cleanup()
# ---------------------------------------------------------------------------

class TestCleanup:
    def test_closes_session_and_clears_all_caches(self, build):
        manager = build()
        session = MagicMock()
        manager.session = session
        manager._logo_cache["TB"] = object()
        manager._font_cache[("PressStart2P-Regular.ttf", 8)] = object()
        manager._bdf_native_size_cache["assets/fonts/5x7.bdf"] = 7

        manager.cleanup()

        session.close.assert_called_once_with()
        assert manager._logo_cache == {}
        # Promoted alongside the font loader: these hold PIL faces and are
        # an unbounded leak across enable/disable cycles if never released.
        assert manager._font_cache == {}
        assert manager._bdf_native_size_cache == {}

    def test_second_cleanup_is_a_noop(self, build):
        manager = build()
        manager.session = MagicMock()
        manager._logo_cache["TB"] = object()
        manager._font_cache[("x", 8)] = object()

        manager.cleanup()
        manager.cleanup()  # must not raise on already-released state

        assert manager._logo_cache == {}
        assert manager._font_cache == {}
        assert manager._bdf_native_size_cache == {}
        assert manager.session.close.call_count == 2

    def test_does_not_shut_down_the_shared_background_service(self, build):
        # get_background_service() hands out a PROCESS-WIDE singleton shared
        # by every scoreboard. One plugin unloading must not stop background
        # fetching for the other eight — cleanup() touches it not at all.
        manager = build()
        service = manager.background_service
        manager.session = MagicMock()

        manager.cleanup()

        assert service.shutdown.called is False
        assert service.stop.called is False
        assert service.method_calls == [], (
            "cleanup() called into the shared background service: "
            f"{service.method_calls}")

    def test_completion_is_logged_even_when_session_close_raises(self, build, caplog):
        manager = build()
        manager.session = MagicMock()
        manager.session.close.side_effect = RuntimeError("socket already gone")
        manager._logo_cache["TB"] = object()

        with caplog.at_level(logging.DEBUG, logger=LOGGER.name):
            manager.cleanup()

        messages = [r.message for r in caplog.records]
        assert any("Error closing session" in m for m in messages)
        # Ordering is load-bearing: the caches still get cleared and the
        # completion log still fires after a failed close.
        assert manager._logo_cache == {}
        assert any("cleanup completed" in m for m in messages)

    def test_tolerates_missing_attributes(self):
        # The hasattr guards exist so a partially constructed instance (an
        # __init__ that raised) can still be cleaned up.
        host = MagicMock(spec=["logger"])
        host.logger = LOGGER
        SportsCore.cleanup(host)


# ---------------------------------------------------------------------------
# 2. _get_layout_offset() + the _config_schema_path() seam
# ---------------------------------------------------------------------------

def layout_config(element, axis, value):
    return {"customization": {"layout": {element: {axis: value}}}}


class TestLayoutOffsetClassicPath:
    """The default path: _config_schema_path() returns None, so offsets come
    from the inline customization.layout read every plugin ships today."""

    def test_config_schema_path_defaults_to_none(self, build):
        manager = build()
        assert manager._config_schema_path() is None

    def test_reads_configured_int(self):
        host = probe(layout_config("home_logo", "x_offset", 5))
        assert offset(host, "home_logo", "x_offset") == 5

    def test_float_is_truncated_to_int(self):
        host = probe(layout_config("score", "y_offset", 2.9))
        result = offset(host, "score", "y_offset")
        assert result == 2 and isinstance(result, int)

    def test_numeric_string_is_coerced(self):
        host = probe(layout_config("score", "x_offset", "-3.5"))
        assert offset(host, "score", "x_offset") == -3

    def test_unconfigured_element_and_axis_use_default(self):
        host = probe(layout_config("score", "x_offset", 5))
        assert offset(host, "status_text", "x_offset", 7) == 7
        assert offset(host, "score", "y_offset", -1) == -1
        assert offset(probe(), "score", "x_offset", 4) == 4

    def test_non_numeric_string_degrades_to_default(self):
        host = probe(layout_config("score", "x_offset", "left"))
        assert offset(host, "score", "x_offset", 3) == 3

    def test_unsupported_type_degrades_to_default(self):
        host = probe(layout_config("score", "x_offset", {"nested": 1}))
        assert offset(host, "score", "x_offset", 2) == 2
        host = probe(layout_config("score", "x_offset", None))
        assert offset(host, "score", "x_offset", 2) == 2

    def test_broken_config_object_degrades_to_default(self):
        host = probe()
        host.config = "not a dict"
        assert offset(host, "score", "x_offset", 6) == 6

    def test_boolean_counts_as_one(self):
        # PINNED AS-IS: the classic read predates the resolver and treats a
        # bool as its int value (True -> 1). See the resolver test below for
        # the stricter, more correct handling.
        host = probe(layout_config("score", "x_offset", True))
        assert offset(host, "score", "x_offset", 4) == 1


class TestLayoutOffsetResolverPath:
    """When a plugin supplies its config_schema.json, offsets resolve through
    src.element_style instead."""

    @pytest.fixture
    def schema_path(self, tmp_path):
        path = tmp_path / "config_schema.json"
        path.write_text(json.dumps({
            "type": "object",
            "properties": {
                "customization": {
                    "type": "object",
                    "properties": {
                        "layout": {"type": "object", "properties": {}},
                    },
                },
            },
        }))
        return str(path)

    def host(self, schema_path, config):
        host = probe(config)
        host._config_schema_path.return_value = schema_path
        del host._style_resolver_cached  # MagicMock auto-attrs otherwise
        host._style_resolver_cached = None
        return host

    def test_reads_configured_offsets(self, schema_path):
        host = self.host(schema_path, layout_config("home_logo", "x_offset", 5))
        assert offset(host, "home_logo", "x_offset") == 5

    def test_numeric_string_is_coerced(self, schema_path):
        host = self.host(schema_path, layout_config("score", "x_offset", "-3.5"))
        assert offset(host, "score", "x_offset") == -3

    def test_missing_value_uses_default(self, schema_path):
        host = self.host(schema_path, layout_config("score", "x_offset", 5))
        assert offset(host, "score", "y_offset", 9) == 9

    def test_bad_input_degrades_to_default(self, schema_path):
        host = self.host(schema_path, layout_config("score", "x_offset", "left"))
        assert offset(host, "score", "x_offset", 3) == 3
        host = self.host(schema_path, {"customization": {"layout": "nope"}})
        assert offset(host, "score", "x_offset", 3) == 3

    def test_boolean_is_rejected_unlike_the_classic_path(self, schema_path):
        # The intended behavior difference: a bool is not a pixel offset, so
        # the resolver returns the default where the classic read returns 1.
        host = self.host(schema_path, layout_config("score", "x_offset", True))
        assert offset(host, "score", "x_offset", 4) == 4

    def test_resolver_is_cached_and_rebuilt_when_config_is_swapped(self, schema_path):
        host = self.host(schema_path, layout_config("score", "x_offset", 5))
        assert offset(host, "score", "x_offset") == 5
        first = host._style_resolver_cached
        assert offset(host, "score", "x_offset") == 5
        assert host._style_resolver_cached is first

        # on_config_change swaps the dict object; the resolver must follow.
        host.config = layout_config("score", "x_offset", 11)
        assert offset(host, "score", "x_offset") == 11
        assert host._style_resolver_cached is not first

    def test_missing_schema_file_still_resolves_offsets(self, tmp_path):
        # Offsets don't depend on schema defaults, so an unreadable schema
        # must not cost the plugin its layout customization.
        host = self.host(str(tmp_path / "absent.json"),
                         layout_config("score", "x_offset", 5))
        assert offset(host, "score", "x_offset") == 5


# ---------------------------------------------------------------------------
# 3. _load_custom_font_from_element_config() + the _font_root() seam
# ---------------------------------------------------------------------------

class TestFontRootSeam:
    def test_default_font_root_is_the_core_install_root(self, build):
        manager = build()
        assert Path(manager._font_root()) == CORE_ROOT
        assert (Path(manager._font_root()) / "assets" / "fonts").is_dir()

    def test_resolve_font_path_honors_an_overridden_root(self, tmp_path):
        fonts = tmp_path / "assets" / "fonts"
        fonts.mkdir(parents=True)
        (fonts / "Bundled.ttf").write_bytes(b"not really a font")
        host = probe()
        host._font_root.side_effect = lambda: str(tmp_path)
        assert SportsCore._resolve_font_path(host, "Bundled.ttf") == str(
            fonts / "Bundled.ttf")

    def test_unknown_font_returns_the_familiar_relative_path(self):
        host = probe()
        assert SportsCore._resolve_font_path(host, "Nope.ttf") == os.path.join(
            "assets", "fonts", "Nope.ttf")


class TestFontLoaderSignature:
    """Hockey's signature is the only safe superset: basketball's positional
    ``default_font: str`` blows up on an explicit None."""

    def test_two_arg_call(self):
        font = load_font(probe(), {"font": TTF_NAME, "font_size": 10})
        assert isinstance(font, ImageFont.FreeTypeFont)
        assert font.size == 10

    def test_default_size_is_used_when_config_omits_it(self):
        assert load_font(probe(), {}, 12).size == 12

    def test_three_positional_args(self):
        font = load_font(probe(), {}, 6, "4x6-font.ttf")
        assert isinstance(font, ImageFont.FreeTypeFont)
        assert font.size == 6
        assert font.path.endswith("4x6-font.ttf")

    def test_explicit_default_font_none(self):
        # The regression this signature guards: os.path.join(..., None).
        font = load_font(probe(), {"font_size": 9}, default_font=None)
        assert isinstance(font, ImageFont.FreeTypeFont)
        assert font.path.endswith(TTF_NAME)

    def test_config_font_wins_over_default_font(self):
        font = load_font(probe(), {"font": TTF_NAME}, 8, "4x6-font.ttf")
        assert font.path.endswith(TTF_NAME)

    def test_string_font_size_is_coerced(self):
        assert load_font(probe(), {"font": TTF_NAME, "font_size": "11"}).size == 11


class TestFontLoaderBehavior:
    def test_family_alias_resolves_through_the_font_manager_catalog(self):
        # "press_start" is a FontManager catalog family, not a filename; the
        # promoted loader must not carry its own duplicate alias table.
        host = probe()
        font = load_font(host, {"font": "press_start", "font_size": 8})
        assert font.path.endswith(TTF_NAME)
        assert ("PressStart2P-Regular.ttf", 8) in host._font_cache

    def test_memo_cache_returns_the_same_face(self):
        host = probe()
        first = load_font(host, {"font": TTF_NAME, "font_size": 8})
        second = load_font(host, {"font": TTF_NAME, "font_size": 8})
        assert first is second
        assert len(host._font_cache) == 1
        # A different size is a different face.
        assert load_font(host, {"font": TTF_NAME, "font_size": 9}) is not first
        assert len(host._font_cache) == 2

    def test_bdf_loads_at_its_native_strike_when_the_request_misses(self):
        # BDF is a fixed-size bitmap format: FreeType raises "invalid pixel
        # size" for anything but the file's own strike. Baseball's retry is
        # the only copy that gets this right.
        host = probe()
        font = load_font(host, {"font": BDF_NAME, "font_size": 8})
        assert isinstance(font, ImageFont.FreeTypeFont)
        assert font.size == BDF_NATIVE_SIZE
        assert font.path.endswith(BDF_NAME)
        assert set(host._bdf_native_size_cache.values()) == {BDF_NATIVE_SIZE}
        # The retried face is memoized under the REQUESTED size.
        assert host._font_cache[(BDF_NAME, 8)] is font

    def test_bdf_at_its_native_size_needs_no_retry(self):
        host = probe()
        font = load_font(host, {"font": BDF_NAME, "font_size": BDF_NATIVE_SIZE})
        assert font.size == BDF_NATIVE_SIZE
        assert host._bdf_native_size_cache == {}

    def test_bdf_strike_lookup_is_memoized(self, monkeypatch):
        calls = []
        real = SportsCore.__module__

        def counting(path):
            calls.append(path)
            from src.font_manager import FontManager
            return FontManager._read_bdf_native_size(path)

        monkeypatch.setattr(f"{real}._read_bdf_native_size", counting)
        host = probe()
        load_font(host, {"font": BDF_NAME, "font_size": 8})
        host._font_cache.clear()  # force the load path again
        load_font(host, {"font": BDF_NAME, "font_size": 8})
        assert len(calls) == 1

    def test_missing_font_falls_back_and_caches_the_fallback(self, caplog):
        host = probe()
        with caplog.at_level(logging.WARNING, logger=LOGGER.name):
            font = load_font(host, {"font": "DoesNotExist.ttf", "font_size": 8})
        assert isinstance(font, ImageFont.FreeTypeFont)
        assert font.path.endswith(TTF_NAME)
        assert any("Font file not found" in r.message for r in caplog.records)
        # Cached under the requested name so a misconfiguration costs one
        # disk probe, not one per frame.
        assert host._font_cache[("DoesNotExist.ttf", 8)] is font

    def test_unknown_extension_falls_back(self):
        host = probe()
        font = load_font(host, {"font": "AUTHORS", "font_size": 8})
        assert font.path.endswith(TTF_NAME)

    def test_fallback_honors_the_supplied_default_font(self):
        host = probe()
        font = load_font(host, {"font": "DoesNotExist.ttf"}, 6, "4x6-font.ttf")
        assert font.path.endswith("4x6-font.ttf")


class TestFontLoaderCwdIndependence:
    """The bug the _font_root() seam exists to prevent: every plugin copy
    joins 'assets/fonts' onto the process cwd, so a process started anywhere
    else silently degrades to PIL's default bitmap face (the same defect
    already fixed in FontManager — see CHANGELOG Unreleased/Fixed)."""

    @pytest.mark.parametrize("font_name,expected_size",
                             [(TTF_NAME, 8), (BDF_NAME, BDF_NATIVE_SIZE)])
    def test_fonts_load_from_an_unrelated_cwd(self, monkeypatch, font_name,
                                              expected_size):
        monkeypatch.chdir("/")
        host = probe()
        font = load_font(host, {"font": font_name, "font_size": 8})
        assert isinstance(font, ImageFont.FreeTypeFont), (
            f"{font_name} degraded to PIL's default face when the process "
            "runs outside the install root")
        assert font.size == expected_size
        assert Path(font.path) == FONTS_DIR / font_name

    def test_fallback_font_also_survives_an_unrelated_cwd(self, monkeypatch):
        monkeypatch.chdir("/")
        font = load_font(probe(), {"font": "DoesNotExist.ttf", "font_size": 8})
        assert isinstance(font, ImageFont.FreeTypeFont)
        assert Path(font.path) == FONTS_DIR / TTF_NAME

    @pytest.mark.parametrize("key", ["score", "time", "team", "status",
                                     "detail", "rank"])
    def test_load_fonts_survives_an_unrelated_cwd(self, monkeypatch, key):
        """`_load_fonts` had the same cwd-relative literals the seam exists to
        remove; every scoreboard font silently became PIL's default bitmap face
        when the process started outside the install root."""
        monkeypatch.chdir("/")
        fonts = SportsCore._load_fonts(probe())
        assert isinstance(fonts[key], ImageFont.FreeTypeFont), (
            f"fonts['{key}'] degraded to PIL's default face outside the "
            "install root")


class TestShouldLogCooldown:
    """`_should_log` reads `self._last_warning_time` unguarded, so it must be
    initialized in __init__ — otherwise the first warning of a run raises
    AttributeError instead of logging."""

    def test_cooldown_clock_is_initialized(self, build):
        assert build()._last_warning_time == 0

    def test_first_call_logs_then_cools_down(self, build):
        manager = build()
        assert manager._should_log("api", cooldown=60) is True
        assert manager._should_log("api", cooldown=60) is False

    def test_cooldown_expires(self, build):
        manager = build()
        assert manager._should_log("api", cooldown=60) is True
        manager._warning_cooldowns["api"] -= 61
        assert manager._should_log("api", cooldown=60) is True

    def test_cooldowns_are_tracked_per_warning_type(self, build):
        """The parameter was accepted and ignored: one shared timestamp meant an
        API warning silenced an unrelated cache warning for the next minute."""
        manager = build()
        assert manager._should_log("api", cooldown=60) is True
        assert manager._should_log("cache", cooldown=60) is True
        assert manager._should_log("api", cooldown=60) is False
        assert manager._should_log("cache", cooldown=60) is False

    def test_one_type_expiring_does_not_free_another(self, build):
        manager = build()
        manager._should_log("api")
        manager._should_log("cache")
        manager._warning_cooldowns["api"] -= 61
        assert manager._should_log("api") is True
        assert manager._should_log("cache") is False

    def test_legacy_single_clock_field_is_kept_in_step(self, build):
        """Subclasses in the plugin copies read _last_warning_time directly."""
        manager = build()
        manager._should_log("api")
        assert manager._last_warning_time == manager._warning_cooldowns["api"]


# ---------------------------------------------------------------------------
# 4. Seam guard rails
# ---------------------------------------------------------------------------

class TestPromotedSeamsExist:
    @pytest.mark.parametrize("name", [
        "cleanup", "_get_layout_offset", "_load_custom_font_from_element_config",
        "_config_schema_path", "_font_root", "_resolve_font_path",
    ])
    def test_method_is_callable_on_the_base_class(self, name):
        assert callable(getattr(SportsCore, name, None)), (
            f"SportsCore.{name} is part of the promoted plugin-facing seam "
            "(docs/SPORTS_UNIFICATION.md) — plugins probe for it with "
            "hasattr before delegating.")

    def test_no_sport_names_leaked_into_core(self):
        """core.py must never branch on which sport it is (prose and skin-id
        examples in docstrings are fine — executable code is not)."""
        tree = ast.parse((CORE_ROOT / "src" / "base_classes" / "sports"
                          / "core.py").read_text())
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
                first = node.body[0] if node.body else None
                if (isinstance(first, ast.Expr)
                        and isinstance(first.value, ast.Constant)
                        and isinstance(first.value.value, str)):
                    docstrings.add(id(first.value))

        tokens = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if id(node) not in docstrings:
                    tokens.append(node.value)
            elif isinstance(node, ast.Name):
                tokens.append(node.id)
            elif isinstance(node, ast.Attribute):
                tokens.append(node.attr)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                   ast.ClassDef)):
                tokens.append(node.name)

        haystack = " ".join(tokens).lower()
        for sport in ("afl", "nrl", "hockey", "baseball", "basketball",
                      "football", "lacrosse", "soccer", "ufc"):
            assert sport not in haystack, (
                f"core.py code mentions '{sport}' — core must never learn "
                "sport names; add an override point instead.")


class TestInstallRootResolution:
    """Guards the depth bug the sports.py -> package move introduced.

    The move was byte-identical in every class body, but `__file__` gained a
    directory, so `Path(__file__).resolve().parents[2]` silently changed from
    the repo root to `<root>/src`. Textual identity is not semantic identity
    when code measures its own location: these tests assert the resolved
    values, not the index.
    """

    def test_install_root_is_the_repo_root(self):
        from src.base_classes.sports.core import _INSTALL_ROOT

        # The repo root is the directory that actually holds src/ and assets/.
        assert (_INSTALL_ROOT / "src").is_dir()
        assert (_INSTALL_ROOT / "src" / "base_classes" / "sports").is_dir()
        assert _INSTALL_ROOT.name != "src", (
            "_INSTALL_ROOT resolved to src/ — the parents[] depth is off by "
            "one, which is exactly the regression the package move caused.")

    def test_resolve_project_path_roots_at_repo_not_src(self):
        from src.base_classes.sports.core import SportsCore, _INSTALL_ROOT

        resolved = SportsCore._resolve_project_path(None, Path("assets/fonts"))
        assert resolved == _INSTALL_ROOT / "assets" / "fonts"
        assert "src" not in resolved.relative_to(_INSTALL_ROOT).parts

    def test_absolute_paths_pass_through_unchanged(self):
        from src.base_classes.sports.core import SportsCore

        absolute = Path("/tmp/some/logo/dir")
        assert SportsCore._resolve_project_path(None, absolute) == absolute

    def test_font_root_and_project_path_share_one_anchor(self):
        """Both consumers must derive from the same constant, so a future
        move needs exactly one line changed rather than two."""
        from src.base_classes.sports.core import SportsCore, _INSTALL_ROOT

        assert SportsCore._font_root(None) == str(_INSTALL_ROOT)
