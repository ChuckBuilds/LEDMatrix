"""Request-supplied names must not be able to name a file outside their base.

Three of these were live. Each test that stands in for one says so, and
asserts on the *filesystem* -- that the file outside the base is still there,
or was never read -- rather than only on the status code, because a handler
that returns 403 and deletes the file anyway would pass the weaker check.

The rest pin the shared helper in src/common/path_safety.py that the handlers
now go through, so a future handler gets the same behaviour by using it.
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.common.path_safety import (  # noqa: E402
    resolve_under,
    safe_path_component,
    safe_relative_parts,
)
from test._api_v3_test_helpers import api_v3_client, api_v3_module  # noqa: F401,E402

BACKSLASH = chr(92)


class TestSafePathComponent:
    """One segment, or nothing."""

    @pytest.mark.parametrize("value", [
        "plugin", "ledmatrix-of-the-day", "weather_data", "manifest.json",
        "a.b.c", "UPPER", "with-dash", "with_underscore", "9lives",
    ])
    def test_ordinary_names_pass_through_unchanged(self, value):
        assert safe_path_component(value) == value

    @pytest.mark.parametrize("value", [
        "..", ".", "", "../etc", "a/b", "/abs", "etc/passwd",
        "a" + BACKSLASH + "b", "C:" + BACKSLASH + "x", "C:",
        "a\x00b", None, 123, b"bytes", ["list"],
    ])
    def test_anything_that_could_name_another_directory_is_refused(self, value):
        assert safe_path_component(value) is None

    def test_it_returns_the_value_rather_than_a_verdict(self):
        # A boolean lets a caller validate one string and then open a
        # different one. Returning the checked value is what stops that.
        assert safe_path_component("ok") == "ok"
        assert safe_path_component("../ok") is None


class TestSafeRelativeParts:
    def test_a_multi_segment_path_splits(self):
        assert safe_relative_parts("web_ui/index.html") == ["web_ui", "index.html"]

    def test_empty_segments_are_dropped_not_rejected(self):
        assert safe_relative_parts("a//b") == ["a", "b"]
        assert safe_relative_parts("x/") == ["x"]

    @pytest.mark.parametrize("value", [
        "../x", "a/../../etc", "/etc/passwd", BACKSLASH + "etc",
        "a" + BACKSLASH + ".." + BACKSLASH + "b", "", None,
    ])
    def test_traversal_and_absolute_paths_are_refused(self, value):
        assert safe_relative_parts(value) is None


class TestResolveUnder:
    def test_a_path_inside_the_base_resolves(self, tmp_path):
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "f.txt").write_text("x", encoding="utf-8")
        resolved = resolve_under(tmp_path, "sub", "f.txt")
        assert resolved == (tmp_path / "sub" / "f.txt").resolve()

    def test_a_path_that_would_escape_is_refused(self, tmp_path):
        assert resolve_under(tmp_path, "..") is None
        assert resolve_under(tmp_path, "..", "etc") is None
        assert resolve_under(tmp_path, "/etc/passwd") is None

    def test_a_sibling_sharing_a_prefix_is_refused(self, tmp_path):
        # The bug the old `str(x).startswith(str(base))` checks had:
        # "<root>/foo-evil" starts with "<root>/foo" as a string, but is not
        # inside it as a directory.
        (tmp_path / "foo").mkdir()
        (tmp_path / "foo-evil").mkdir()
        (tmp_path / "foo-evil" / "secret").write_text("s", encoding="utf-8")
        assert resolve_under(tmp_path / "foo", "..", "foo-evil", "secret") is None
        # And the string check it replaces would have said yes:
        naive = (tmp_path / "foo" / ".." / "foo-evil" / "secret").resolve()
        assert str(naive).startswith(str((tmp_path / "foo").resolve()))

    def test_it_returns_none_rather_than_raising_on_junk(self, tmp_path):
        assert resolve_under(tmp_path, None) is None
        assert resolve_under(tmp_path, 42) is None


@pytest.fixture
def plugins_tree(tmp_path):
    """A plugins directory with one plugin, plus a secret outside it."""
    plugins = tmp_path / "plugin-repos"
    (plugins / "demo" / "web_ui").mkdir(parents=True)
    (plugins / "demo" / "web_ui" / "index.html").write_text("<p>hi</p>", encoding="utf-8")
    (plugins / "demo-evil").mkdir()
    (plugins / "demo-evil" / "stolen.json").write_text('{"a":1}', encoding="utf-8")
    secrets = tmp_path / "config"
    secrets.mkdir()
    (secrets / "config_secrets.json").write_text('{"api_key":"hunter2"}', encoding="utf-8")
    return tmp_path


class TestServePluginStatic:
    """GET /api/v3/plugins/<plugin_id>/static/<path:file_path>

    This handler read any file whose resolved path *string-prefixed* the
    plugin directory. Two ways through:

      * plugin_id of "..", because Flask's default converter forbids a slash
        but not dots, and get_plugin_directory returned the parent of the
        plugins directory since it exists. Every file under the project then
        prefixed that directory, config/config_secrets.json included.
      * a sibling directory sharing a prefix, per the helper test above.
    """

    def _wire(self, api_v3_module, plugins_tree):
        plugins = plugins_tree / "plugin-repos"

        def get_plugin_directory(plugin_id):
            candidate = plugins / plugin_id
            return str(candidate) if candidate.exists() else None

        api_v3_module.api_v3.plugin_manager = MagicMock()
        api_v3_module.api_v3.plugin_manager.get_plugin_directory = get_plugin_directory
        return plugins

    def test_a_real_plugin_file_is_still_served(
        self, api_v3_client, api_v3_module, plugins_tree
    ):
        self._wire(api_v3_module, plugins_tree)
        response = api_v3_client.get("/api/v3/plugins/demo/static/web_ui/index.html")
        assert response.status_code == 200
        assert b"<p>hi</p>" in response.data

    def test_a_dotdot_plugin_id_cannot_reach_the_secrets_file(
        self, api_v3_client, api_v3_module, plugins_tree
    ):
        self._wire(api_v3_module, plugins_tree)
        response = api_v3_client.get(
            "/api/v3/plugins/../static/config/config_secrets.json"
        )
        assert response.status_code in (400, 403, 404)
        assert b"hunter2" not in response.data

    def test_a_percent_encoded_dotdot_plugin_id_is_refused_too(
        self, api_v3_client, api_v3_module, plugins_tree
    ):
        self._wire(api_v3_module, plugins_tree)
        response = api_v3_client.get(
            "/api/v3/plugins/%2e%2e/static/config/config_secrets.json"
        )
        assert response.status_code in (400, 403, 404)
        assert b"hunter2" not in response.data

    def test_a_sibling_directory_sharing_a_prefix_is_refused(
        self, api_v3_client, api_v3_module, plugins_tree
    ):
        self._wire(api_v3_module, plugins_tree)
        response = api_v3_client.get(
            "/api/v3/plugins/demo/static/../demo-evil/stolen.json"
        )
        assert response.status_code in (400, 403, 404)
        assert b'"a"' not in response.data

    def test_an_unknown_plugin_is_still_a_404(
        self, api_v3_client, api_v3_module, plugins_tree
    ):
        self._wire(api_v3_module, plugins_tree)
        response = api_v3_client.get("/api/v3/plugins/nope/static/x.json")
        assert response.status_code == 404


class TestDeleteOfTheDayJson:
    """POST /api/v3/plugins/of-the-day/json/delete

    file_id came from the request body and was interpolated into
    ``f"{file_id}.json"`` and then unlinked, with no validation at all. A
    file_id of "../../../../etc/something" deleted that file. This is the one
    finding in the batch that destroyed data rather than exposing it.
    """

    @pytest.fixture
    def plugin_tree(self, tmp_path, api_v3_module):
        plugin_dir = tmp_path / "plugin-repos" / "ledmatrix-of-the-day"
        (plugin_dir / "of_the_day").mkdir(parents=True)
        (plugin_dir / "of_the_day" / "quotes.json").write_text("{}", encoding="utf-8")
        outside = tmp_path / "victim.json"
        outside.write_text("important", encoding="utf-8")

        api_v3_module.api_v3.plugin_manager = MagicMock()
        api_v3_module.api_v3.plugin_manager.get_plugin_directory.return_value = str(plugin_dir)
        return plugin_dir, outside

    URL = "/api/v3/plugins/of-the-day/json/delete"

    def test_a_real_file_in_the_plugin_is_still_deleted(
        self, api_v3_client, plugin_tree
    ):
        plugin_dir, _ = plugin_tree
        target = plugin_dir / "of_the_day" / "quotes.json"
        response = api_v3_client.post(self.URL, json={"file_id": "quotes"})
        assert response.status_code == 200
        assert not target.exists()

    def test_a_traversing_file_id_deletes_nothing(self, api_v3_client, plugin_tree):
        _, outside = plugin_tree
        response = api_v3_client.post(
            self.URL, json={"file_id": "../../../victim"}
        )
        assert response.status_code == 400
        assert outside.exists(), "file outside the plugin directory was deleted"
        assert outside.read_text(encoding="utf-8") == "important"

    @pytest.mark.parametrize("file_id", ["..", "a/b", "/etc/x", "x" + BACKSLASH + "y"])
    def test_other_shapes_of_traversal_are_refused(
        self, api_v3_client, plugin_tree, file_id
    ):
        _, outside = plugin_tree
        response = api_v3_client.post(self.URL, json={"file_id": file_id})
        assert response.status_code == 400
        assert outside.exists()


class TestDiskCacheKeys:
    """The cache key becomes a filename, and POST /api/v3/cache/delete passes
    the request body's key straight through CacheManager.clear_cache to
    os.remove. A key of "../../../../etc/whatever" named a file well outside
    the cache directory.
    """

    @pytest.fixture
    def cache(self, tmp_path):
        from src.cache.disk_cache import DiskCache

        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        return DiskCache(str(cache_dir)), cache_dir

    def test_an_ordinary_key_still_round_trips(self, cache):
        disk_cache, cache_dir = cache
        disk_cache.set("espn_nfl_2024", {"ok": True})
        assert (cache_dir / "espn_nfl_2024.json").exists()
        assert disk_cache.get("espn_nfl_2024", max_age=None)["ok"] is True

    def test_a_traversing_key_has_no_path(self, cache):
        disk_cache, _ = cache
        assert disk_cache.get_cache_path("../../victim") is None
        assert disk_cache.get_cache_path("..") is None
        assert disk_cache.get_cache_path("a/b") is None

    def test_clearing_a_traversing_key_deletes_nothing(self, cache, tmp_path):
        disk_cache, _ = cache
        victim = tmp_path / "victim.json"
        victim.write_text("important", encoding="utf-8")
        disk_cache.clear("../victim")
        assert victim.exists(), "file outside the cache directory was deleted"

    def test_writing_a_traversing_key_creates_nothing(self, cache, tmp_path):
        disk_cache, _ = cache
        disk_cache.set("../escaped", {"x": 1})
        assert not (tmp_path / "escaped.json").exists()

    def test_the_delete_endpoint_says_no_rather_than_claiming_success(
        self, api_v3_client, api_v3_module
    ):
        api_v3_module.api_v3.cache_manager = MagicMock()
        response = api_v3_client.post(
            "/api/v3/cache/delete", json={"key": "../../etc/passwd"}
        )
        assert response.status_code == 400
        api_v3_module.api_v3.cache_manager.clear_cache.assert_not_called()

    def test_the_delete_endpoint_still_deletes_an_ordinary_key(
        self, api_v3_client, api_v3_module
    ):
        api_v3_module.api_v3.cache_manager = MagicMock()
        response = api_v3_client.post(
            "/api/v3/cache/delete", json={"key": "espn_nfl_2024"}
        )
        assert response.status_code == 200
        api_v3_module.api_v3.cache_manager.clear_cache.assert_called_once_with(
            "espn_nfl_2024"
        )


class TestPluginVersionLookup:
    """_get_plugin_version joins a request-supplied id onto the plugins
    directory and opens manifest.json under it."""

    def test_a_real_manifest_is_read(self, tmp_path):
        from web_interface.blueprints import api_v3 as module

        plugins = tmp_path / "plugin-repos"
        (plugins / "demo").mkdir(parents=True)
        (plugins / "demo" / "manifest.json").write_text(
            json.dumps({"version": "1.2.3"}), encoding="utf-8"
        )
        original = getattr(module.api_v3, "plugin_store_manager", None)
        module.api_v3.plugin_store_manager = MagicMock(plugins_dir=str(plugins))
        try:
            assert module._get_plugin_version("demo") == "1.2.3"
            assert module._get_plugin_version("../demo") == ""
            assert module._get_plugin_version("..") == ""
        finally:
            module.api_v3.plugin_store_manager = original
