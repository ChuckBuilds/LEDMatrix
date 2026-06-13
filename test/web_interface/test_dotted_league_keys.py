"""
Regression test for saving plugin config fields whose schema keys contain dots
(e.g. soccer league keys like "fifa.world", "eng.1", "usa.1").

Bug: the web config form posts form-data with dotted paths such as
"leagues.fifa.world.enabled". The helpers that resolve those paths split on every
dot, so the dotted league key "fifa.world" was mistaken for nested "fifa" ->
"world" objects. Per-league edits (enable, favorite_teams, nested booleans) were
written to a fabricated "leagues.fifa.world" branch while the real league object
was never updated, so the save silently dropped the change and the saved config
came out byte-identical.
"""

import unittest

from web_interface.blueprints.api_v3 import (
    _get_schema_property,
    _set_nested_value,
    _parse_form_value_with_schema,
)


SCHEMA = {
    "type": "object",
    "properties": {
        "leagues": {
            "type": "object",
            "properties": {
                "fifa.world": {
                    "type": "object",
                    "properties": {
                        "enabled": {"type": "boolean"},
                        "favorite_teams": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "display_modes": {
                            "type": "object",
                            "properties": {"live": {"type": "boolean"}},
                        },
                    },
                }
            },
        }
    },
}


class TestDottedLeagueKeys(unittest.TestCase):
    def test_schema_lookup_resolves_dotted_league_key(self):
        prop = _get_schema_property(SCHEMA, "leagues.fifa.world.favorite_teams")
        self.assertIsNotNone(prop, "dotted league key path should resolve")
        self.assertEqual(prop.get("type"), "array")

    def test_schema_lookup_resolves_nested_object_beneath_dotted_key(self):
        live = _get_schema_property(SCHEMA, "leagues.fifa.world.display_modes.live")
        self.assertIsNotNone(live)
        self.assertEqual(live.get("type"), "boolean")

    def test_parse_typed_value_for_dotted_key(self):
        # Comma-separated text input "USA" must become an array, not the raw string.
        parsed = _parse_form_value_with_schema(
            "USA", "leagues.fifa.world.favorite_teams", SCHEMA
        )
        self.assertEqual(parsed, ["USA"])

    def test_set_value_updates_real_league_not_fabricated_branch(self):
        config = {"leagues": {"fifa.world": {"enabled": False, "favorite_teams": []}}}
        _set_nested_value(config, "leagues.fifa.world.enabled", True)
        _set_nested_value(config, "leagues.fifa.world.favorite_teams", ["USA"])

        self.assertTrue(config["leagues"]["fifa.world"]["enabled"])
        self.assertEqual(config["leagues"]["fifa.world"]["favorite_teams"], ["USA"])
        # The real league must be updated and no fabricated "fifa" branch created.
        self.assertNotIn("fifa", config["leagues"])

    def test_set_value_into_missing_leaf_lands_in_real_league(self):
        # A leaf that does not exist yet still resolves into the real dotted league.
        config = {"leagues": {"fifa.world": {"enabled": False}}}
        _set_nested_value(config, "leagues.fifa.world.display_modes.live", True)
        self.assertTrue(
            config["leagues"]["fifa.world"]["display_modes"]["live"]
        )
        self.assertNotIn("fifa", config["leagues"])

    def test_plain_nested_paths_still_work(self):
        config = {}
        _set_nested_value(config, "customization.text.font", "small")
        self.assertEqual(config["customization"]["text"]["font"], "small")


if __name__ == "__main__":
    unittest.main()
