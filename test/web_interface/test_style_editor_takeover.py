"""When the style editor takes over a customization block, it takes over
exactly its own part of it -- and it actually gets to take over at all.

Two defects this pins, both found by rendering the real partial in a browser
rather than by reading the code:

1. The hand-off guard asked "do any fallback controls differ from their
   server-rendered defaults?" as a proxy for "is someone editing this?". The
   fallback contains this block's own font fields, and the font-selector
   widget populates them on the same 50ms timer -- so a plain page load with
   nobody touching anything raced into "dirty" (seven customization.*.font
   selects, measured ~60ms after injection) and the style editor removed
   itself, leaving the accordion form it exists to replace. The question is
   whether a *person* typed, and isTrusted answers exactly that.

2. Taking over used to remove the whole fallback section. A customization
   block can hold more than styling -- football keeps favorite_result_colors
   there -- so that removed the only UI those fields had.

These assert on the template source because the behaviour lives in an inline
script that no Python test executes; the browser check that found both is not
something CI runs.
"""

import re
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

TEMPLATE = (PROJECT_ROOT / "web_interface" / "templates" / "v3" / "partials"
            / "plugin_config.html")


@pytest.fixture(scope="module")
def source():
    return TEMPLATE.read_text(encoding="utf-8")


class TestTheHandOffGuard:
    def test_it_asks_whether_a_person_typed(self, source):
        assert "isTrusted" in source, (
            "a programmatic change by a sibling widget must not read as a "
            "user edit -- that is what removed the editor on a plain load")
        assert "userEdited" in source

    def test_it_no_longer_diffs_values_against_defaults(self, source):
        assert "fallbackIsDirty" not in source, (
            "value-diffing cannot distinguish a user's edit from another "
            "widget populating this block's own fields")
        assert "defaultSelected" not in source

    def test_the_listeners_are_attached_synchronously(self, source):
        """The edit worth protecting can happen before initWidget runs, so
        the listeners cannot wait for it.

        Scoped to this branch: the template defines an initWidget for every
        widget it can render, and the first one in the file belongs to a
        different branch entirely.
        """
        start = source.index("obj_widget == 'style-editor'")
        branch = source[start:source.index("{% elif prop.properties %}", start)]
        assert "watchForRealEdits" in branch
        assert branch.index("watchForRealEdits") < branch.index("function initWidget"), (
            "the watcher must be installed above this branch's initWidget, "
            "and invoked immediately rather than from inside it")

    def test_the_guard_still_backs_off_for_a_real_edit(self, source):
        assert re.search(r"if \(userEdited\) \{ container\.remove\(\); return; \}",
                         source), "a genuine in-progress edit must still win"


class TestItTakesOverOnlyItsOwnBlocks:
    def test_children_are_individually_addressable(self, source):
        assert 'data-child-key="{{ nested_key }}"' in source, (
            "without a handle per child the only options are removing the "
            "whole section or none of it")

    def test_removal_is_driven_by_what_the_widget_reported(self, source):
        assert "ownedKeys" in source
        assert "fallback.querySelector(" in source

    def test_an_unowned_child_survives(self, source):
        """The empty-section case still removes the lot, so a block that is
        entirely styling looks exactly as it did before."""
        assert "!fallback.querySelector('[data-child-key]')" in source


class TestTheSchemaSaysWhichBlocksAreStyling:
    def test_adopted_blocks_are_marked_and_others_are_not(self):
        from src.element_style import expand_style_elements

        schema = {
            "type": "object",
            "properties": {
                "customization": {
                    "type": "object",
                    "properties": {
                        "score_text": {
                            "type": "object",
                            "properties": {
                                "font": {"type": "string",
                                         "default": "PressStart2P-Regular.ttf"},
                                "font_size": {"type": "integer", "default": 10},
                                "text_color": {"type": "array",
                                               "default": [255, 255, 255]},
                            },
                        },
                        # Not styling: a feature that happens to live here.
                        "favorite_result_colors": {
                            "type": "object",
                            "properties": {
                                "enabled": {"type": "boolean", "default": False},
                                "win_color": {"type": "array",
                                              "default": [0, 255, 0]},
                            },
                        },
                    },
                },
            },
        }
        props = expand_style_elements(schema)["properties"]["customization"]["properties"]
        assert props["score_text"]["x-style-managed"] is True
        assert "x-style-managed" not in props["favorite_result_colors"]
