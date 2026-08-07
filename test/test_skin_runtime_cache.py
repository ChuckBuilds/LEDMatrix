"""Gap tests for src/skin_system/skin_runtime.py: the discovery cache,
module namespacing internals, API gating edge cases, and targeting.

test/test_skin_system.py already covers discovery validation, load_skin
basics, and build_context — nothing here duplicates those.

NOTE: every test uses a UNIQUE skin id. load_skin caches the entry
module in sys.modules per skin id and never re-executes it, so reusing
an id across tests would silently serve another test's module.
"""

import builtins
import json
import os
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# skin_runtime -> skin_base can transitively reach hardware modules via
# sports imports in sibling tests' processes; stub the matrix driver
# before importing, matching test_skin_system.py.
sys.modules.setdefault("rgbmatrix", MagicMock())

from src.skin_system import skin_runtime
from src.skin_system.skin_base import SKIN_API_VERSION, ScoreboardSkin


DEFAULT_BODY = (
    "from src.skin_system.skin_base import ScoreboardSkin\n"
    "class {cls}(ScoreboardSkin):\n"
    "    def render_live(self, ctx, game):\n"
    "        return True\n"
)


@pytest.fixture(autouse=True)
def _clean_runtime_state():
    """Clear the discovery cache and any skin modules this test creates."""
    skin_runtime._discovery_cache.clear()
    before = {k for k in sys.modules if k.startswith("_skin_")}
    yield
    skin_runtime._discovery_cache.clear()
    created = [k for k in sys.modules
               if k.startswith("_skin_") and k not in before]
    for k in created:
        sys.modules.pop(k, None)


def make_skin(skins_dir: Path, skin_id: str, *,
              api_version: str = SKIN_API_VERSION,
              class_name: str = "TestSkin",
              body: str = None,
              extra_files: dict = None,
              entry_point: str = None,
              manifest_id: str = None,
              manifest_extra: dict = None,
              write_entry: bool = True) -> Path:
    """Write a skin package directory and return its path."""
    skin_dir = skins_dir / skin_id
    skin_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "id": manifest_id or skin_id,
        "name": skin_id,
        "version": "1.0.0",
        "skin_api_version": api_version,
        "class_name": class_name,
    }
    if entry_point:
        manifest["entry_point"] = entry_point
    manifest.update(manifest_extra or {})
    (skin_dir / "skin.json").write_text(json.dumps(manifest))
    if write_entry:
        entry_name = entry_point or "skin.py"
        (skin_dir / entry_name).write_text(
            body if body is not None else DEFAULT_BODY.format(cls=class_name))
    for name, content in (extra_files or {}).items():
        (skin_dir / name).write_text(content)
    return skin_dir


def counting_read_manifest(monkeypatch):
    """Wrap skin_runtime._read_manifest with a call counter."""
    original = skin_runtime._read_manifest
    counter = {"count": 0}

    def wrapper(skin_dir):
        counter["count"] += 1
        return original(skin_dir)

    monkeypatch.setattr(skin_runtime, "_read_manifest", wrapper)
    return counter


def bump_mtime(path: Path, offset: float = 100.0):
    """Set a distinct, strictly later mtime so the fingerprint changes."""
    t = time.time() + offset
    os.utime(path, (t, t))


# ---------------------------------------------------------------------------
# A. Discovery cache
# ---------------------------------------------------------------------------

class TestDiscoveryCache:
    def test_second_call_serves_cache(self, tmp_path, monkeypatch):
        make_skin(tmp_path, "t01-cache-hit")
        counter = counting_read_manifest(monkeypatch)
        first = skin_runtime.discover_skins(tmp_path)
        count_after_first = counter["count"]
        assert count_after_first >= 1
        second = skin_runtime.discover_skins(tmp_path)
        assert counter["count"] == count_after_first  # no re-read
        assert second == first
        assert "t01-cache-hit" in second

    def test_manifest_edit_invalidates_without_force_refresh(self, tmp_path):
        skin_dir = make_skin(tmp_path, "t02-edit")
        skins = skin_runtime.discover_skins(tmp_path)
        assert skins["t02-edit"]["name"] == "t02-edit"

        manifest_path = skin_dir / "skin.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["name"] = "renamed"
        manifest_path.write_text(json.dumps(manifest))
        bump_mtime(manifest_path)

        skins = skin_runtime.discover_skins(tmp_path)  # no force_refresh
        assert skins["t02-edit"]["name"] == "renamed"

    def test_new_skin_dir_invalidates(self, tmp_path):
        make_skin(tmp_path, "t03-first")
        assert set(skin_runtime.discover_skins(tmp_path)) == {"t03-first"}

        new_dir = make_skin(tmp_path, "t03-second")
        bump_mtime(new_dir / "skin.json")
        bump_mtime(tmp_path)

        skins = skin_runtime.discover_skins(tmp_path)  # no force_refresh
        assert set(skins) == {"t03-first", "t03-second"}

    def test_py_file_change_does_not_invalidate(self, tmp_path, monkeypatch):
        # PIN: the fingerprint only globs */skin.json — editing a skin's
        # .py file alone does NOT invalidate the cache; the cached
        # manifests are still served (a code change needs a restart).
        skin_dir = make_skin(tmp_path, "t04-pyedit")
        counter = counting_read_manifest(monkeypatch)
        skin_runtime.discover_skins(tmp_path)
        count_after_first = counter["count"]

        (skin_dir / "skin.py").write_text("# rewritten\n" +
                                          DEFAULT_BODY.format(cls="TestSkin"))
        bump_mtime(skin_dir / "skin.py")

        skins = skin_runtime.discover_skins(tmp_path)
        assert counter["count"] == count_after_first  # cache still served
        assert "t04-pyedit" in skins

    def test_force_refresh_rereads_with_unchanged_fingerprint(self, tmp_path,
                                                              monkeypatch):
        make_skin(tmp_path, "t05-force")
        counter = counting_read_manifest(monkeypatch)
        skin_runtime.discover_skins(tmp_path)
        count_after_first = counter["count"]
        skin_runtime.discover_skins(tmp_path, force_refresh=True)
        assert counter["count"] > count_after_first

    def test_result_mapping_is_copy_but_manifests_shared(self, tmp_path):
        make_skin(tmp_path, "t06-copy")
        result = skin_runtime.discover_skins(tmp_path)

        # Mutating the returned mapping does not poison the cache...
        del result["t06-copy"]
        again = skin_runtime.discover_skins(tmp_path)  # cache hit
        assert "t06-copy" in again

        # ...but the inner manifest dicts ARE shared with the cache (pin).
        again["t06-copy"]["name"] = "mutated-inner"
        third = skin_runtime.discover_skins(tmp_path)  # cache hit
        assert third["t06-copy"]["name"] == "mutated-inner"

    def test_missing_directory_returns_empty_and_caches_nothing(self, tmp_path):
        missing = tmp_path / "not-yet"
        assert skin_runtime.discover_skins(missing) == {}
        assert str(missing) not in skin_runtime._discovery_cache

        # Creating the directory later is picked up without force_refresh.
        make_skin(missing, "t07-late")
        skins = skin_runtime.discover_skins(missing)
        assert "t07-late" in skins

    def test_hidden_underscore_and_plain_file_entries_skipped(self, tmp_path):
        make_skin(tmp_path, ".hidden-skin")
        make_skin(tmp_path, "_private-skin")
        (tmp_path / "stray-file").write_text("not a directory")
        make_skin(tmp_path, "t08-good")
        skins = skin_runtime.discover_skins(tmp_path, force_refresh=True)
        assert set(skins) == {"t08-good"}

    def test_manifest_id_mismatch_keys_by_manifest_id(self, tmp_path):
        make_skin(tmp_path, "t09-dirname", manifest_id="t09-manifest-id")
        skins = skin_runtime.discover_skins(tmp_path, force_refresh=True)
        assert "t09-manifest-id" in skins
        assert "t09-dirname" not in skins
        assert skins["t09-manifest-id"]["_skin_dir"].endswith("t09-dirname")

    def test_falsy_required_field_drops_skin(self, tmp_path):
        make_skin(tmp_path, "t10-empty-class", class_name="")
        skins = skin_runtime.discover_skins(tmp_path, force_refresh=True)
        assert skins == {}


# ---------------------------------------------------------------------------
# B. Module namespacing (_load_skin_module via load_skin)
# ---------------------------------------------------------------------------

BODY_WITH_HELPERS = (
    "import helpers\n"
    "from src.skin_system.skin_base import ScoreboardSkin\n"
    "class TestSkin(ScoreboardSkin):\n"
    "    pass\n"
)


class TestModuleNamespacing:
    def test_namespaced_sys_modules_keys(self, tmp_path):
        make_skin(tmp_path, "t11-ns", body=BODY_WITH_HELPERS,
                  extra_files={"helpers.py": "VALUE = 11\n"})
        skin = skin_runtime.load_skin("t11-ns", skins_dir=tmp_path)
        assert skin is not None
        assert "_skin_t11-ns_skin" in sys.modules
        assert "_skin_t11-ns_helpers" in sys.modules

    def test_preseeded_bare_name_restored(self, tmp_path, monkeypatch):
        sentinel = object()
        monkeypatch.setitem(sys.modules, "helpers", sentinel)
        make_skin(tmp_path, "t12a-restore", body=BODY_WITH_HELPERS,
                  extra_files={"helpers.py": "VALUE = 'a'\n"})
        skin = skin_runtime.load_skin("t12a-restore", skins_dir=tmp_path)
        assert skin is not None
        assert sys.modules["helpers"] is sentinel

    def test_absent_bare_name_stays_absent(self, tmp_path):
        saved = sys.modules.pop("helpers", None)
        try:
            assert "helpers" not in sys.modules
            make_skin(tmp_path, "t12b-absent", body=BODY_WITH_HELPERS,
                      extra_files={"helpers.py": "VALUE = 'b'\n"})
            skin = skin_runtime.load_skin("t12b-absent", skins_dir=tmp_path)
            assert skin is not None
            assert "helpers" not in sys.modules
        finally:
            if saved is not None:
                sys.modules["helpers"] = saved

    def test_stdlib_shadowing_sibling_leaves_real_module_intact(self, tmp_path):
        real_json = sys.modules["json"]
        make_skin(tmp_path, "t12c-json",
                  extra_files={"json.py": "SKIN_LOCAL = True\n"})
        skin = skin_runtime.load_skin("t12c-json", skins_dir=tmp_path)
        assert skin is not None
        assert sys.modules["json"] is real_json
        assert not hasattr(sys.modules["json"], "SKIN_LOCAL")
        assert json.loads('{"ok": 1}') == {"ok": 1}  # stdlib still works
        # The skin's copy lives only under its namespaced alias.
        assert getattr(sys.modules["_skin_t12c-json_json"], "SKIN_LOCAL") is True

    def test_entry_module_executed_once_across_loads(self, tmp_path,
                                                     monkeypatch):
        executions = []
        monkeypatch.setattr(builtins, "_t13_skin_executions", executions,
                            raising=False)
        body = (
            "import builtins\n"
            "builtins._t13_skin_executions.append(1)\n"
            "from src.skin_system.skin_base import ScoreboardSkin\n"
            "class TestSkin(ScoreboardSkin):\n"
            "    pass\n"
        )
        make_skin(tmp_path, "t13-cached", body=body)
        for _ in range(3):
            skin = skin_runtime.load_skin("t13-cached", skins_dir=tmp_path)
            assert skin is not None
        assert len(executions) == 1  # module executed exactly once

    def test_sibling_import_failure_returns_none_and_restores_bare(
            self, tmp_path, monkeypatch):
        sentinel = object()
        monkeypatch.setitem(sys.modules, "helpers", sentinel)
        make_skin(tmp_path, "t14-sibfail", body=BODY_WITH_HELPERS,
                  extra_files={"helpers.py": "raise RuntimeError('sibling boom')\n"})
        assert skin_runtime.load_skin("t14-sibfail", skins_dir=tmp_path) is None
        assert sys.modules["helpers"] is sentinel

    def test_missing_entry_point_file(self, tmp_path):
        make_skin(tmp_path, "t15-noentry", write_entry=False)
        assert skin_runtime.load_skin("t15-noentry", skins_dir=tmp_path) is None

    def test_custom_entry_point(self, tmp_path):
        make_skin(tmp_path, "t16-custom", entry_point="render.py")
        skin = skin_runtime.load_skin("t16-custom", skins_dir=tmp_path)
        assert isinstance(skin, ScoreboardSkin)
        assert "_skin_t16-custom_render" in sys.modules
        assert "_skin_t16-custom_skin" not in sys.modules

    def test_class_name_pointing_at_unrelated_class(self, tmp_path):
        body = "class NotASkin:\n    pass\n"
        make_skin(tmp_path, "t17a-wrongclass", body=body,
                  class_name="NotASkin")
        assert skin_runtime.load_skin("t17a-wrongclass",
                                      skins_dir=tmp_path) is None

    def test_class_name_pointing_at_instance(self, tmp_path):
        body = (
            "from src.skin_system.skin_base import ScoreboardSkin\n"
            "class MySkin(ScoreboardSkin):\n"
            "    pass\n"
            "obj = MySkin({}, {})\n"
        )
        make_skin(tmp_path, "t17b-instance", body=body, class_name="obj")
        assert skin_runtime.load_skin("t17b-instance",
                                      skins_dir=tmp_path) is None

    def test_constructor_raising_returns_none(self, tmp_path):
        body = (
            "from src.skin_system.skin_base import ScoreboardSkin\n"
            "class TestSkin(ScoreboardSkin):\n"
            "    def __init__(self, manifest, options):\n"
            "        raise ValueError('ctor boom')\n"
        )
        make_skin(tmp_path, "t18-ctor", body=body)
        assert skin_runtime.load_skin("t18-ctor", skins_dir=tmp_path) is None


# ---------------------------------------------------------------------------
# C. API gate + targeting
# ---------------------------------------------------------------------------

class TestApiGateAndTargeting:
    def test_same_major_higher_minor_loads(self, tmp_path):
        make_skin(tmp_path, "t19-minor", api_version="1.9.0")
        skin = skin_runtime.load_skin("t19-minor", skins_dir=tmp_path)
        assert isinstance(skin, ScoreboardSkin)

    def test_malformed_api_version_refused(self, tmp_path):
        make_skin(tmp_path, "t20-malformed", api_version="abc")
        assert skin_runtime.load_skin("t20-malformed",
                                      skins_dir=tmp_path) is None

    @pytest.mark.parametrize("manifest,sport,sport_key,expected", [
        # No targets key at all -> matches everything
        ({"id": "x"}, "baseball", "mlb", True),
        ({"id": "x"}, None, None, True),
        # Empty targets dict -> matches everything
        ({"id": "x", "targets": {}}, "hockey", None, True),
        # sports family match
        ({"id": "x", "targets": {"sports": ["baseball"]}},
         "baseball", None, True),
        # sport_keys exact match
        ({"id": "x", "targets": {"sport_keys": ["milb"]}},
         None, "milb", True),
        # OR semantics: sport_keys matches even though sports excludes it
        ({"id": "x", "targets": {"sports": ["hockey"],
                                 "sport_keys": ["milb"]}},
         "baseball", "milb", True),
        # Neither matches
        ({"id": "x", "targets": {"sports": ["hockey"]}},
         "baseball", None, False),
        ({"id": "x", "targets": {"sports": ["hockey"],
                                 "sport_keys": ["nhl"]}},
         "baseball", "milb", False),
    ])
    def test_skin_matches_target(self, manifest, sport, sport_key, expected):
        assert skin_runtime.skin_matches_target(
            manifest, sport, sport_key) is expected
