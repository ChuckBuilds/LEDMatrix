"""Non-finite floats round-trip identically with and without orjson.

JSON has no NaN or Infinity. The stdlib emits them anyway as an extension;
orjson refuses to and writes null. Cache files outlive the decision of which
encoder is installed, so both halves of that gap are pinned here:

  * writing -- installing orjson must not silently change what gets cached,
    so the stdlib path writes null too;
  * reading -- records already on disk carrying NaN or Infinity must stay
    readable, or installing orjson turns each of them into a "corrupted cache
    file" that DiskCache.get logs as an error and deletes.
"""

import importlib
import json
import math
import sys
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.cache import disk_cache as disk_cache_module  # noqa: E402


@pytest.fixture
def stdlib_cache():
    """The module as it loads on a host with no orjson wheel."""
    # A None entry in sys.modules makes `import orjson` raise ImportError,
    # which is the branch we want, whether or not orjson is really installed.
    with mock.patch.dict(sys.modules, {"orjson": None}):
        module = importlib.reload(disk_cache_module)
        assert module.orjson is None
        yield module
    importlib.reload(disk_cache_module)


@pytest.fixture
def orjson_cache():
    """The module as it loads with orjson available."""
    module = importlib.reload(disk_cache_module)
    if module.orjson is None:
        pytest.skip("orjson is not installed on this host")
    return module


NON_FINITE = {"nan": float("nan"), "inf": float("inf"), "ninf": float("-inf")}


def _reject_constant(name):
    """Make json.loads as strict as orjson about NaN/Infinity tokens."""
    raise AssertionError(f"non-spec JSON constant in output: {name}")


class TestWritePolicy:
    """Non-finite floats become null on whichever encoder is in use."""

    def _assert_nulled(self, module):
        record = module._loads(module._dumps(dict(NON_FINITE, ok=1.5)))
        assert record["nan"] is None
        assert record["inf"] is None
        assert record["ninf"] is None
        # Finite values are untouched.
        assert record["ok"] == 1.5

    def test_stdlib_writes_null(self, stdlib_cache):
        self._assert_nulled(stdlib_cache)

    def test_orjson_writes_null(self, orjson_cache):
        self._assert_nulled(orjson_cache)

    def test_stdlib_emits_spec_compliant_json(self, stdlib_cache):
        # The point of the write half: bytes written without orjson must still
        # parse once orjson is installed later. json.loads accepts the
        # extension tokens, so it cannot show this -- assert on the bytes, and
        # on a strict reader when there is one.
        raw = stdlib_cache._dumps(dict(NON_FINITE))
        assert b"NaN" not in raw
        assert b"Infinity" not in raw
        assert json.loads(raw, parse_constant=_reject_constant) == {
            "nan": None, "inf": None, "ninf": None}

    def test_nested_non_finite_are_replaced(self, stdlib_cache):
        data = {"a": [1.0, float("nan"), {"b": float("inf")}], "c": (float("-inf"),)}
        record = stdlib_cache._loads(stdlib_cache._dumps(data))
        assert record["a"] == [1.0, None, {"b": None}]
        assert record["c"] == [None]

    def test_finite_payloads_are_byte_identical_to_the_old_encoder(self, stdlib_cache):
        # allow_nan=False must not change ordinary output.
        data = {"x": 1, "y": [1.5, "s", True, None], "z": {"k": 2.25}}
        assert stdlib_cache._dumps(data) == json.dumps(
            data, cls=stdlib_cache.DateTimeEncoder).encode("utf-8")


class TestReplaceNonFinite:
    def test_leaves_ordinary_values_alone(self):
        for value in (1, 1.5, "s", True, None, [], {}):
            assert disk_cache_module._replace_nonfinite(value) == value

    def test_replaces_every_non_finite_float(self):
        for value in NON_FINITE.values():
            assert disk_cache_module._replace_nonfinite(value) is None

    def test_walks_nested_containers(self):
        assert disk_cache_module._replace_nonfinite(
            {"a": [{"b": float("nan")}]}) == {"a": [{"b": None}]}


class TestLegacyRecordsStayReadable:
    """Files written before orjson arrived still load."""

    LEGACY = b'{"timestamp": 1000.0, "value": NaN, "other": Infinity}'

    def test_stdlib_reads_legacy_tokens(self, stdlib_cache):
        record = stdlib_cache._loads(self.LEGACY)
        assert math.isnan(record["value"])
        assert math.isinf(record["other"])

    def test_orjson_falls_back_for_legacy_tokens(self, orjson_cache):
        record = orjson_cache._loads(self.LEGACY)
        assert math.isnan(record["value"])
        assert math.isinf(record["other"])

    def test_genuinely_malformed_files_still_raise(self, stdlib_cache):
        with pytest.raises(json.JSONDecodeError):
            stdlib_cache._loads(b'{"a": ')

    def test_orjson_still_raises_for_malformed_files(self, orjson_cache):
        with pytest.raises(json.JSONDecodeError):
            orjson_cache._loads(b'{"a": ')


class TestDiskCacheEndToEnd:
    def _cache(self, module, tmp_path):
        return module.DiskCache(str(tmp_path))

    def test_legacy_file_is_not_deleted_as_corrupt(self, orjson_cache, tmp_path):
        cache = self._cache(orjson_cache, tmp_path)
        path = cache.get_cache_path("legacy")
        Path(path).write_bytes(
            b'{"timestamp": %d, "value": NaN}' % int(__import__("time").time()))

        record = cache.get("legacy", max_age=None)

        assert record is not None, "legacy NaN record was treated as corrupt"
        assert math.isnan(record["value"])
        assert Path(path).exists(), "legacy NaN record was deleted"

    def test_round_trip_through_set_and_get(self, stdlib_cache, tmp_path):
        cache = self._cache(stdlib_cache, tmp_path)
        cache.set("k", {"timestamp": __import__("time").time(),
                        "value": float("nan")})
        record = cache.get("k", max_age=None)
        assert record is not None
        assert record["value"] is None
