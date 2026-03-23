"""Tests for dedup_unique_arrays used by save_plugin_config.

Validates that arrays with uniqueItems: true in the JSON schema have
duplicates removed before validation, preventing spurious validation
failures when form merging introduces duplicate entries.

Tests import the production function from src.web_interface.validators
to ensure they exercise the real code path.
"""

import pytest

from src.web_interface.validators import dedup_unique_arrays as _dedup_unique_arrays


class TestDedupUniqueArrays:
    """Test suite for uniqueItems array deduplication."""

    def test_flat_array_with_duplicates(self) -> None:
        """Duplicates in a top-level uniqueItems array are removed."""
        cfg = {"stock_symbols": ["AAPL", "GOOGL", "FNMA", "TSLA", "FNMA"]}
        schema = {
            "properties": {
                "stock_symbols": {
                    "type": "array",
                    "uniqueItems": True,
                    "items": {"type": "string"},
                }
            }
        }
        _dedup_unique_arrays(cfg, schema)
        assert cfg["stock_symbols"] == ["AAPL", "GOOGL", "FNMA", "TSLA"]

    def test_flat_array_preserves_order(self) -> None:
        """First occurrence of each item is kept, order preserved."""
        cfg = {"tags": ["b", "a", "c", "a", "b"]}
        schema = {
            "properties": {
                "tags": {
                    "type": "array",
                    "uniqueItems": True,
                    "items": {"type": "string"},
                }
            }
        }
        _dedup_unique_arrays(cfg, schema)
        assert cfg["tags"] == ["b", "a", "c"]

    def test_no_duplicates_unchanged(self) -> None:
        """Array without duplicates is not modified."""
        cfg = {"items": ["a", "b", "c"]}
        schema = {
            "properties": {
                "items": {
                    "type": "array",
                    "uniqueItems": True,
                    "items": {"type": "string"},
                }
            }
        }
        _dedup_unique_arrays(cfg, schema)
        assert cfg["items"] == ["a", "b", "c"]

    def test_array_without_unique_items_not_deduped(self) -> None:
        """Arrays without uniqueItems constraint keep duplicates."""
        cfg = {"items": ["a", "a", "b"]}
        schema = {
            "properties": {
                "items": {
                    "type": "array",
                    "items": {"type": "string"},
                }
            }
        }
        _dedup_unique_arrays(cfg, schema)
        assert cfg["items"] == ["a", "a", "b"]

    def test_nested_object_with_unique_array(self) -> None:
        """Dedup works for uniqueItems arrays inside nested objects."""
        cfg = {
            "feeds": {
                "stock_symbols": ["AAPL", "FNMA", "NVDA", "FNMA"]
            }
        }
        schema = {
            "properties": {
                "feeds": {
                    "type": "object",
                    "properties": {
                        "stock_symbols": {
                            "type": "array",
                            "uniqueItems": True,
                            "items": {"type": "string"},
                        }
                    },
                }
            }
        }
        _dedup_unique_arrays(cfg, schema)
        assert cfg["feeds"]["stock_symbols"] == ["AAPL", "FNMA", "NVDA"]

    def test_array_of_objects_with_nested_unique_arrays(self) -> None:
        """Dedup recurses into array elements that are objects."""
        cfg = {
            "servers": [
                {"tags": ["web", "prod", "web"]},
                {"tags": ["db", "staging"]},
            ]
        }
        schema = {
            "properties": {
                "servers": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "tags": {
                                "type": "array",
                                "uniqueItems": True,
                                "items": {"type": "string"},
                            }
                        },
                    },
                }
            }
        }
        _dedup_unique_arrays(cfg, schema)
        assert cfg["servers"][0]["tags"] == ["web", "prod"]
        assert cfg["servers"][1]["tags"] == ["db", "staging"]

    def test_missing_key_in_config_skipped(self) -> None:
        """Schema properties not present in config are silently skipped."""
        cfg = {"other": "value"}
        schema = {
            "properties": {
                "items": {
                    "type": "array",
                    "uniqueItems": True,
                    "items": {"type": "string"},
                }
            }
        }
        _dedup_unique_arrays(cfg, schema)
        assert cfg == {"other": "value"}

    def test_empty_array(self) -> None:
        """Empty arrays are handled without error."""
        cfg = {"items": []}
        schema = {
            "properties": {
                "items": {
                    "type": "array",
                    "uniqueItems": True,
                    "items": {"type": "string"},
                }
            }
        }
        _dedup_unique_arrays(cfg, schema)
        assert cfg["items"] == []

    def test_integer_duplicates(self) -> None:
        """Dedup works for non-string types (integers)."""
        cfg = {"ports": [80, 443, 80, 8080, 443]}
        schema = {
            "properties": {
                "ports": {
                    "type": "array",
                    "uniqueItems": True,
                    "items": {"type": "integer"},
                }
            }
        }
        _dedup_unique_arrays(cfg, schema)
        assert cfg["ports"] == [80, 443, 8080]

    def test_deeply_nested_objects(self) -> None:
        """Dedup works through multiple levels of nesting."""
        cfg = {
            "level1": {
                "level2": {
                    "values": ["x", "y", "x"]
                }
            }
        }
        schema = {
            "properties": {
                "level1": {
                    "type": "object",
                    "properties": {
                        "level2": {
                            "type": "object",
                            "properties": {
                                "values": {
                                    "type": "array",
                                    "uniqueItems": True,
                                    "items": {"type": "string"},
                                }
                            },
                        }
                    },
                }
            }
        }
        _dedup_unique_arrays(cfg, schema)
        assert cfg["level1"]["level2"]["values"] == ["x", "y"]

    def test_stock_news_real_world_schema(self) -> None:
        """End-to-end test matching the actual stock-news plugin config shape."""
        cfg = {
            "enabled": True,
            "global": {
                "display_duration": 30.0,
                "scroll_speed": 1.0,
            },
            "feeds": {
                "news_source": "google_news",
                "stock_symbols": [
                    "AAPL", "GOOGL", "MSFT", "FNMA",
                    "NVDA", "TSLA", "META", "AMD", "FNMA",
                ],
                "text_color": [0, 255, 0],
            },
            "display_duration": 15,
        }
        schema = {
            "properties": {
                "enabled": {"type": "boolean"},
                "global": {
                    "type": "object",
                    "properties": {
                        "display_duration": {"type": "number"},
                        "scroll_speed": {"type": "number"},
                    },
                },
                "feeds": {
                    "type": "object",
                    "properties": {
                        "news_source": {"type": "string"},
                        "stock_symbols": {
                            "type": "array",
                            "uniqueItems": True,
                            "items": {"type": "string"},
                        },
                        "text_color": {
                            "type": "array",
                            "items": {"type": "integer"},
                        },
                    },
                },
                "display_duration": {"type": "number"},
            }
        }
        _dedup_unique_arrays(cfg, schema)
        assert cfg["feeds"]["stock_symbols"] == [
            "AAPL", "GOOGL", "MSFT", "FNMA", "NVDA", "TSLA", "META", "AMD"
        ]
        # text_color has no uniqueItems, should be untouched
        assert cfg["feeds"]["text_color"] == [0, 255, 0]
