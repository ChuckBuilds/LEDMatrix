"""The composer generates Python that the plugin loader imports and executes.

/api/install writes the generated manager.py into plugins_dir and the loader
imports it, so anything the payload can splice into that source runs on the
device. The ast.parse check in _generate_plugin_files rejects only *invalid*
syntax -- an injected `import os` is perfectly valid and passed it.

Two ways in, both confirmed against the code before it was fixed:

  metadata.name = a name containing a triple-quote, a newline, then
                  `import os; PWNED = os.getuid()`, then another triple-quote
      -> closes the module docstring; the rest became module-level statements
         (spelled out rather than shown literally -- writing the payload into
         this docstring closes *this* file's docstring, which is the bug)

  element x     = '0 or __import__("os").system("id")'
      -> f-string interpolated it verbatim: x=0 or __import__("os").system("id")
"""
import ast
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from web_interface.blueprints import composer as C  # noqa: E402

BASE_META = {"id": "test-plugin", "name": "Clock", "author": "a",
             "version": "1.0.0", "description": "d"}

#: Values that terminate a Python expression and start a new statement.
EXPR_PAYLOADS = [
    '0 or __import__("os").system("id")',
    '0);import os;os.system("id");(',
    '__import__("subprocess").run(["id"])',
    "0 if False else exec('x=1')",
    "1e999", "nan", "0x41", "0__0",
]

#: Values that close a string literal in the generated source.
LITERAL_PAYLOADS = [
    'Clock"""\nimport os; PWNED = os.getuid()\n"""',
    "Clock'''\nimport os\n'''",
    'Clock" + __import__("os").system("id") + "',
    "Clock\\", "Clock\nimport os",
]


def _payload(**over):
    p = {"metadata": dict(BASE_META), "elements": [], "config_vars": []}
    p["metadata"].update(over.pop("metadata", {}))
    p.update(over)
    return p


def _generated(payload):
    return C._generate_plugin_files(payload)["manager.py"]


def _module_level_code(src):
    """Statements at module level that are not the docstring/imports/classes."""
    tree = ast.parse(src)
    out = []
    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.ImportFrom)):
            continue
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            continue  # the docstring
        out.append(ast.unparse(node))
    return out


@pytest.mark.parametrize("payload", LITERAL_PAYLOADS)
def test_a_name_that_breaks_out_of_a_literal_is_refused(payload):
    with pytest.raises(C.ComposerInputError):
        _generated(_payload(metadata={"name": payload}))


@pytest.mark.parametrize("evil", EXPR_PAYLOADS)
@pytest.mark.parametrize("field", ["x", "y", "x0", "y0", "x1", "y1", "lineWidth"])
def test_a_non_numeric_geometry_value_cannot_reach_the_source(evil, field):
    el = {"type": "line", "id": "l1", "x0": 0, "y0": 0, "x1": 10, "y1": 10,
          "anchor_x": "right", "anchor_y": "bottom"}
    el[field] = evil
    src = _generated(_payload(elements=[el]))
    assert "__import__" not in src, f"{field}={evil!r} reached the generated source"
    assert "os.system" not in src
    assert not _module_level_code(src), \
        f"{field}={evil!r} produced module-level statements: {_module_level_code(src)}"


@pytest.mark.parametrize("evil", EXPR_PAYLOADS)
@pytest.mark.parametrize("channel", ["r", "g", "b"])
def test_a_non_numeric_colour_channel_cannot_reach_the_source(evil, channel):
    el = {"type": "text", "id": "t1", "x": 0, "y": 0, "text": "hi",
          "font": "press_start", "r": 255, "g": 255, "b": 255}
    el[channel] = evil
    src = _generated(_payload(elements=[el]))
    assert "__import__" not in src and "os.system" not in src
    assert not _module_level_code(src)


def test_colour_channels_are_clamped_to_a_byte():
    el = {"type": "text", "id": "t1", "x": 0, "y": 0, "text": "hi",
          "font": "press_start", "r": 99999, "g": -5, "b": 128}
    src = _generated(_payload(elements=[el]))
    assert "(255, 0, 128)" in src, "channels were not clamped to 0-255"


def test_the_generated_module_still_has_no_top_level_statements():
    """The clean case: a normal payload produces only imports and a class."""
    el = {"type": "text", "id": "t1", "x": 4, "y": 4, "text": "hi",
          "font": "press_start", "r": 1, "g": 2, "b": 3}
    src = _generated(_payload(elements=[el]))
    assert not _module_level_code(src)
    assert "(1, 2, 3)" in src


# --- config variable keys ---------------------------------------------------

def _with_key(key):
    return {"metadata": dict(BASE_META), "elements": [],
            "dataModel": {"configVars": [{"key": key, "type": "string",
                                          "default": "x", "label": "L"}]}}


@pytest.mark.parametrize("key", ["class", "def", "import", "None", "True",
                                 "lambda", "pass", "match", "case"])
def test_a_keyword_config_key_is_named_in_the_error(key):
    """ast.parse already rejected these, but as an unhelpful line number.

    "Generated code has a syntax error: invalid syntax (line 17)" tells the
    user nothing about which field to fix.
    """
    with pytest.raises(C.ComposerInputError) as exc:
        _generated(_with_key(key))
    assert key in str(exc.value) and "keyword" in str(exc.value).lower()


@pytest.mark.parametrize("key", ["config", "logger", "display_manager",
                                 "cache_manager", "plugin_id", "enabled",
                                 "self", "update", "display"])
def test_a_reserved_attribute_config_key_is_refused(key):
    """These generate *valid* Python that silently clobbers plugin state.

    The worst is `config`: the assignment lands right after super().__init__(),
    so `self.config = config.get("config", "x")` replaces the plugin's config
    dict with a string and every later self.config.get(...) fails at runtime.
    """
    with pytest.raises(C.ComposerInputError) as exc:
        _generated(_with_key(key))
    assert key in str(exc.value) and "reserved" in str(exc.value).lower()


@pytest.mark.parametrize("key", ["brightness", "my_var", "_private", "x1",
                                 "update_interval_seconds"])
def test_ordinary_config_keys_are_still_accepted(key):
    src = _generated(_with_key(key))
    assert f"self.{key} = config.get(" in src


def test_the_generated_config_assignment_does_not_precede_super_init():
    """Guards the reasoning behind the reserved list, not just the list."""
    src = _generated(_with_key("brightness"))
    body = src.splitlines()
    super_at = next(i for i, l in enumerate(body) if "super().__init__(" in l)
    assign_at = next(i for i, l in enumerate(body) if "self.brightness = config.get(" in l)
    assert assign_at > super_at, (
        "config vars are assigned before super().__init__(); the reserved-name "
        "list assumes they land after it")
