"""
Drift guard for the duplicated secret-separation logic.

src/web_interface/secret_helpers.py is the canonical implementation of
find_secret_fields / separate_secrets, but web_interface/blueprints/api_v3.py
still carries THREE inline nested-function copies of each (in the plugin
config GET, POST, and reset endpoints). The copies lack the canonical
module's array-item support (`accounts[].token`), so migrating an endpoint
onto the module is a behavior change that must be made deliberately.

This file guards two things:
1. The copy count can only go DOWN. A fourth copy appearing means someone
   re-implemented the logic again instead of importing secret_helpers.
2. The known behavioral gap is documented as an executable fact, so whoever
   migrates the endpoints knows exactly what changes.
"""

import re
from pathlib import Path

from src.web_interface.secret_helpers import find_secret_fields, separate_secrets

API_V3_PATH = (Path(__file__).resolve().parents[2]
               / "web_interface" / "blueprints" / "api_v3.py")

# Update DOWNWARD as endpoints migrate onto src/web_interface/secret_helpers.
EXPECTED_INLINE_COPIES = 3


class TestInlineCopyCount:
    def _count(self, name: str) -> int:
        source = API_V3_PATH.read_text(encoding="utf-8")
        return len(re.findall(rf"^\s*def {name}\(", source, flags=re.MULTILINE))

    def test_find_secret_fields_copy_count(self):
        count = self._count("find_secret_fields")
        assert count == EXPECTED_INLINE_COPIES, (
            f"api_v3.py has {count} inline find_secret_fields definitions, "
            f"expected {EXPECTED_INLINE_COPIES}. New code must import it from "
            f"src/web_interface/secret_helpers instead of re-implementing it; "
            f"if you migrated an endpoint, lower EXPECTED_INLINE_COPIES."
        )

    def test_separate_secrets_copy_count(self):
        count = self._count("separate_secrets")
        assert count == EXPECTED_INLINE_COPIES, (
            f"api_v3.py has {count} inline separate_secrets definitions, "
            f"expected {EXPECTED_INLINE_COPIES}. New code must import it from "
            f"src/web_interface/secret_helpers instead of re-implementing it; "
            f"if you migrated an endpoint, lower EXPECTED_INLINE_COPIES."
        )

    def test_inline_copies_lack_array_item_support(self):
        """The documented gap: no inline copy recurses into array `items`
        schemas, so array-item secrets (accounts[].token) are NOT routed to
        config_secrets.json by these endpoints. The canonical module handles
        them. When an endpoint migrates onto the module that behavior
        changes (a fix, but a deliberate one).

        If this fails, an inline copy has grown array support — duplicating
        the canonical module even harder. Migrate the endpoint onto
        src/web_interface/secret_helpers instead.
        """
        for body in self._inline_bodies("find_secret_fields"):
            # Array handling requires checking type == 'array'; no inline
            # copy does. (Can't grep bare "items" — properties.items() the
            # dict method appears legitimately.)
            assert "'array'" not in body and '"array"' not in body

    @staticmethod
    def _inline_bodies(name: str):
        """Extract each inline def's body from api_v3.py by indentation."""
        lines = API_V3_PATH.read_text(encoding="utf-8").splitlines()
        bodies = []
        i = 0
        while i < len(lines):
            match = re.match(rf"^(\s+)def {name}\(", lines[i])
            if not match:
                i += 1
                continue
            indent = len(match.group(1))
            body = [lines[i]]
            i += 1
            while i < len(lines):
                line = lines[i]
                if line.strip() and (len(line) - len(line.lstrip())) <= indent:
                    break
                body.append(line)
                i += 1
            bodies.append("\n".join(body))
        assert bodies, f"no inline {name} definitions found"
        return bodies


class TestCanonicalArrayItemBehavior:
    """Executable documentation of what migrating endpoints will change."""

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
