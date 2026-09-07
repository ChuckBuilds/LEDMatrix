"""starlark-apps PixletRenderer: what reaches Pixlet, and what counts as a render.

Three things a Starlark app can do that the renderer got wrong:

  * put a "|" in a config value -- a shell metacharacter filter dropped the
    whole key, though the command is a list and no shell is involved;
  * render nothing -- Pixlet exits 0 and writes a 0-byte file, which was
    reported as a successful render;
  * compute its schema at runtime -- the source parser can only read option
    lists that are written out literally, so a dropdown fed by a live API call
    came back empty.
"""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

PLUGIN_DIR = Path(__file__).resolve().parent.parent / "plugin-repos" / "starlark-apps"


@pytest.fixture(scope="module")
def renderer_module():
    if not PLUGIN_DIR.exists():
        pytest.skip("starlark-apps plugin is not checked out")
    sys.path.insert(0, str(PLUGIN_DIR))
    try:
        spec = importlib.util.spec_from_file_location(
            "pixlet_renderer_under_test", PLUGIN_DIR / "pixlet_renderer.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except Exception as e:  # noqa: BLE001 - optional deps may be absent
        pytest.skip(f"pixlet_renderer is not importable here: {e}")
    finally:
        sys.path.remove(str(PLUGIN_DIR))


@pytest.fixture
def renderer(renderer_module):
    """A renderer with a known binary and __init__'s binary search bypassed."""
    r = renderer_module.PixletRenderer.__new__(renderer_module.PixletRenderer)
    r.timeout = 30
    r.pixlet_binary = "/usr/local/bin/pixlet"
    return r


def _completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=returncode,
                                       stdout=stdout, stderr=stderr)


class TestConfigValuesReachPixlet:
    """cmd is a list and there is no shell=True, so nothing here is ever
    interpreted by a shell -- the filter is defence in depth, not a boundary."""

    def _args_for(self, renderer, tmp_path, config):
        star = tmp_path / "app.star"
        star.write_text("# app", encoding="utf-8")
        out = tmp_path / "out.webp"

        def fake_run(cmd, **kw):
            out.write_bytes(b"webp-bytes")
            fake_run.cmd = cmd
            return _completed()

        with patch.object(subprocess, "run", side_effect=fake_run):
            renderer.render(str(tmp_path / "app.star"), str(out), config=config)
        return fake_run.cmd

    def test_a_pipe_in_a_value_is_passed_through(self, renderer, tmp_path):
        """Real apps use "|" as a separator inside one config value."""
        args = self._args_for(renderer, tmp_path, {"sign_id": "I-476 North|175659"})
        assert "sign_id=I-476 North|175659" in args

    def test_a_dropped_value_does_not_take_the_key_with_it(self, renderer, tmp_path):
        args = self._args_for(renderer, tmp_path, {"sign_id": "I-476 North|175659"})
        assert any(a.startswith("sign_id=") for a in args)

    @pytest.mark.parametrize("value", [
        "$(rm -rf /)",
        "`whoami`",
        "a;b",
        "a&b",
        "a>b",
        "a<b",
    ])
    def test_actual_shell_metacharacters_are_still_dropped(self, renderer, tmp_path, value):
        args = self._args_for(renderer, tmp_path, {"k": value})
        assert not any(a.startswith("k=") for a in args)

    def test_an_invalid_key_is_still_dropped(self, renderer, tmp_path):
        args = self._args_for(renderer, tmp_path, {"bad key!": "x"})
        assert not any("bad key!" in a for a in args)


class TestAnEmptyRenderIsNotASuccess:
    """Pixlet exits 0 and writes 0 bytes when an app renders no content."""

    def _render(self, renderer, tmp_path, write_bytes):
        star = tmp_path / "app.star"
        star.write_text("# app", encoding="utf-8")
        out = tmp_path / "out.webp"

        def fake_run(cmd, **kw):
            out.write_bytes(write_bytes)
            return _completed()

        with patch.object(subprocess, "run", side_effect=fake_run):
            return renderer.render(str(tmp_path / "app.star"), str(out))

    def test_a_zero_byte_output_is_reported_as_a_failure(self, renderer, tmp_path):
        ok, error = self._render(renderer, tmp_path, b"")
        assert ok is False
        assert "empty" in (error or "").lower()

    def test_a_real_output_still_succeeds(self, renderer, tmp_path):
        ok, error = self._render(renderer, tmp_path, b"RIFF....WEBP")
        assert (ok, error) == (True, None)

    def test_a_missing_output_still_fails(self, renderer, tmp_path):
        star = tmp_path / "app.star"
        star.write_text("# app", encoding="utf-8")
        out = tmp_path / "out.webp"
        with patch.object(subprocess, "run", return_value=_completed()):
            ok, error = renderer.render(str(tmp_path / "app.star"), str(out))
        assert ok is False
        assert "not found" in (error or "").lower()


class TestSchemaComesFromPixletWhenItCan:
    """`pixlet schema` runs get_schema(); the source parser only reads it.

    An app whose dropdown options come from a live API call has no option list
    anywhere in its source, so the parser reports an empty dropdown and the
    config form offers nothing to choose.
    """

    PIXLET_OUTPUT = json.dumps({
        "version": "1",
        "schema": [
            {"id": "roadway", "name": "Roadway", "type": "dropdown",
             "description": "Pick a road", "options": [{"display": "I-476", "value": "476"}]},
        ],
    })

    def test_the_pixlet_output_is_used(self, renderer, tmp_path):
        star = tmp_path / "app.star"
        star.write_text("# no schema literal anywhere\n", encoding="utf-8")
        with patch.object(subprocess, "run",
                          return_value=_completed(stdout=self.PIXLET_OUTPUT)):
            ok, schema, error = renderer.extract_schema(str(star))
        assert ok is True and error is None
        assert schema["schema"][0]["options"][0]["value"] == "476"

    def test_pixlet_field_keys_are_remapped_for_the_config_form(self, renderer, tmp_path):
        """The UI reads typeOf/desc; Pixlet emits type/description."""
        star = tmp_path / "app.star"
        star.write_text("#\n", encoding="utf-8")
        with patch.object(subprocess, "run",
                          return_value=_completed(stdout=self.PIXLET_OUTPUT)):
            _, schema, _ = renderer.extract_schema(str(star))
        field = schema["schema"][0]
        assert field["typeOf"] == "dropdown"
        assert field["desc"] == "Pick a road"
        assert "type" not in field and "description" not in field

    @pytest.mark.parametrize("outcome", [
        {"return_value": _completed(returncode=1, stderr="unknown command \"schema\"")},
        {"return_value": _completed(stdout="not json at all")},
        {"side_effect": subprocess.TimeoutExpired(cmd="pixlet", timeout=20)},
        {"side_effect": OSError("no such binary")},
    ])
    def test_it_falls_back_to_the_source_parser(self, renderer, tmp_path, outcome):
        """Older Pixlet has no `schema` subcommand, and an app can fail to run.
        Neither should cost the schema an app does state literally."""
        star = tmp_path / "app.star"
        star.write_text(
            'def get_schema():\n'
            '    return schema.Schema(version = "1", fields = [\n'
            '        schema.Text(id = "city", name = "City", desc = "Your city",'
            ' icon = "gear"),\n'
            '    ])\n',
            encoding="utf-8")
        with patch.object(subprocess, "run", **outcome):
            ok, parsed, error = renderer.extract_schema(str(star))
        assert ok is True and error is None
        assert [f["id"] for f in parsed["schema"]] == ["city"]

    def test_no_pixlet_binary_skips_straight_to_the_parser(self, renderer, tmp_path):
        renderer.pixlet_binary = None
        star = tmp_path / "app.star"
        star.write_text("#\n", encoding="utf-8")
        assert renderer.extract_schema_via_pixlet(str(star)) is None

    def test_a_missing_star_file_is_still_an_error(self, renderer, tmp_path):
        ok, schema, error = renderer.extract_schema(str(tmp_path / "gone.star"))
        assert ok is False and schema is None and "not found" in error

    def test_an_unexpected_pixlet_shape_is_not_trusted(self, renderer, tmp_path):
        """A bare list, or a dict with no schema array, must not reach the UI."""
        star = tmp_path / "app.star"
        star.write_text("#\n", encoding="utf-8")
        with patch.object(subprocess, "run",
                          return_value=_completed(stdout='["not", "a", "schema"]')):
            assert renderer.extract_schema_via_pixlet(str(star)) is None
