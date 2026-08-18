"""Static Starlark schema extraction and installed-app migration tests."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


renderer_module = _load_module(
    "test_starlark_pixlet_renderer",
    ROOT / "plugin-repos/starlark-apps/pixlet_renderer.py",
)
rebuild_module = _load_module(
    "test_starlark_rebuild_schemas",
    ROOT / "plugin-repos/starlark-apps/tools/rebuild_schemas.py",
)


@pytest.fixture
def renderer():
    return renderer_module.PixletRenderer()


def _extract(renderer, tmp_path, source):
    star_file = tmp_path / "app.star"
    star_file.write_text(source, encoding="utf-8")
    success, schema, error = renderer.extract_schema(str(star_file))
    assert success, error
    return {field["id"]: field for field in schema["schema"]}


def test_resolves_defaults_and_all_supported_dropdown_option_forms(renderer, tmp_path):
    fields = _extract(renderer, tmp_path, '''
DEFAULT_NAME = "two"
DEFAULT_COUNT = 7
DEFAULT_RATIO = 1.5
DEFAULT_ENABLED = True
DEFAULT_COLOR = "#A1B2C3"
CHOICES = {"One": "1", "Two": "2"}
literal_options = [
    schema.Option(display = "One", value = "1"),
    schema.Option(display = "Two", value = "2"),
]
def get_schema():
    generated_options = [
        schema.Option(display = key, value = value)
        for key, value in CHOICES.items()
    ]
    return schema.Schema(version = "1", fields = [
        schema.Dropdown(id = "inline", default = "1", options = [
            schema.Option(display = "One", value = "1"),
            schema.Option(display = "Two", value = "2"),
        ]),
        schema.Dropdown(id = "variable", default = DEFAULT_NAME, options = literal_options),
        schema.Dropdown(id = "generated", default = DEFAULT_NAME, options = generated_options),
        schema.Text(id = "count", default = DEFAULT_COUNT),
        schema.Text(id = "ratio", default = DEFAULT_RATIO),
        schema.Toggle(id = "enabled", default = DEFAULT_ENABLED),
        schema.Color(id = "color", default = DEFAULT_COLOR),
    ])
''')

    expected_options = [
        {"display": "One", "value": "1"},
        {"display": "Two", "value": "2"},
    ]
    assert fields["inline"]["options"] == expected_options
    assert fields["variable"]["options"] == expected_options
    assert fields["generated"]["options"] == expected_options
    assert fields["variable"]["default"] == "two"
    assert fields["count"]["default"] == 7
    assert fields["ratio"]["default"] == 1.5
    assert fields["enabled"]["default"] is True
    assert fields["color"]["default"] == "#A1B2C3"


def test_unresolved_expressions_are_omitted_without_crashing(renderer, tmp_path, caplog):
    fields = _extract(renderer, tmp_path, '''
UNRELATED = [item + 1 for item in [1, 2]]
def dynamic_value():
    return "runtime"
def get_schema():
    return schema.Schema(version = "1", fields = [
        schema.Dropdown(id = "choice", default = dynamic_value(), options = make_options()),
    ])
''')

    assert "default" not in fields["choice"]
    assert "options" not in fields["choice"]
    assert "Could not statically resolve" in caplog.text


def test_rebuild_repairs_only_missing_and_symbolic_defaults(tmp_path):
    apps_dir = tmp_path / "starlark-apps"
    app_dir = apps_dir / "sample"
    app_dir.mkdir(parents=True)
    (apps_dir / "manifest.json").write_text(
        json.dumps({"apps": {"sample": {"star_file": "sample.star"}}}), encoding="utf-8")
    (app_dir / "sample.star").write_text('''
DEFAULT_SPEED = "2"
DEFAULT_ENABLED = True
def get_schema():
    return schema.Schema(version = "1", fields = [
        schema.Text(id = "speed", default = DEFAULT_SPEED),
        schema.Toggle(id = "enabled", default = DEFAULT_ENABLED),
        schema.Color(id = "color", default = "#FFFFFF"),
    ])
''', encoding="utf-8")
    (app_dir / "config.json").write_text(json.dumps({
        "speed": "DEFAULT_SPEED",
        "color": "#123456",
        "custom": "keep-me",
    }), encoding="utf-8")

    first = rebuild_module.rebuild_schemas(apps_dir)
    config = json.loads((app_dir / "config.json").read_text(encoding="utf-8"))
    schema = json.loads((app_dir / "schema.json").read_text(encoding="utf-8"))

    assert first.apps_scanned == 1
    assert first.schemas_regenerated == 1
    assert first.configs_repaired == 1
    assert first.unresolved_fields == 0
    assert first.errors == 0
    assert config == {
        "speed": "2",
        "enabled": True,
        "color": "#123456",
        "custom": "keep-me",
    }
    assert {field["id"]: field["default"] for field in schema["schema"]} == {
        "speed": "2", "enabled": True, "color": "#FFFFFF",
    }

    second = rebuild_module.rebuild_schemas(apps_dir)
    assert second.schemas_regenerated == 1
    assert second.configs_repaired == 0
    assert second.errors == 0
