"""
Drift guard: api_v3 must use the canonical secret helpers.

Historically web_interface/blueprints/api_v3.py carried THREE inline
nested-function copies of ``find_secret_fields``/``separate_secrets`` (in the
main-config save, plugin-config save, and plugin-config reset endpoints).
They lacked the canonical module's array-item secret support and drifted from
each other. They have been migrated onto
``src/web_interface/secret_helpers`` — this file now guards against copies
REAPPEARING, and keeps the canonical array-item behavior executable.
"""

import re
from pathlib import Path

from src.web_interface.secret_helpers import find_secret_fields, separate_secrets

API_V3_PATH = (Path(__file__).resolve().parents[2]
               / "web_interface" / "blueprints" / "api_v3.py")

# The migration is complete: any inline reimplementation is a regression.
EXPECTED_INLINE_COPIES = 0


class TestNoInlineCopies:
    def _count(self, name: str) -> int:
        source = API_V3_PATH.read_text(encoding="utf-8")
        return len(re.findall(rf"^\s*def {name}\(", source, flags=re.MULTILINE))

    def test_no_inline_find_secret_fields(self):
        count = self._count("find_secret_fields")
        assert count == EXPECTED_INLINE_COPIES, (
            f"api_v3.py has {count} inline find_secret_fields definitions, "
            f"expected {EXPECTED_INLINE_COPIES}. Import it from "
            f"src/web_interface/secret_helpers instead of re-implementing it."
        )

    def test_no_inline_separate_secrets(self):
        count = self._count("separate_secrets")
        assert count == EXPECTED_INLINE_COPIES, (
            f"api_v3.py has {count} inline separate_secrets definitions, "
            f"expected {EXPECTED_INLINE_COPIES}. Import it from "
            f"src/web_interface/secret_helpers instead of re-implementing it."
        )

    def test_canonical_import_present(self):
        # Tripwire: the endpoints still need the helpers, so removing the
        # import means either dead secret handling or a new local copy.
        source = API_V3_PATH.read_text(encoding="utf-8")
        assert re.search(
            r"from src\.web_interface\.secret_helpers import .*find_secret_fields",
            source,
        ), "api_v3.py no longer imports the canonical secret helpers"


class TestCanonicalArrayItemBehavior:
    """Executable documentation of the array-item secret contract the
    endpoints now inherit from the canonical module."""

    SCHEMA = {
        "accounts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "token": {"type": "string", "x-secret": True},
                },
            },
        },
    }

    def test_canonical_module_routes_array_item_secrets(self):
        paths = find_secret_fields(self.SCHEMA)
        assert "accounts[].token" in paths

        config = {"accounts": [{"name": "a", "token": "s3cret"}]}
        regular, secrets = separate_secrets(config, paths)
        assert regular == {"accounts": [{"name": "a"}]}
        assert secrets == {"accounts": [{"token": "s3cret"}]}
