"""live_in_ticker's new default reaches existing installs, once (src/config_manager.py).

3.8.0 makes display.vegas_scroll.live_in_ticker true. Every existing config
holds an explicit false copied from the template, which the template merge
never touches (it only adds missing keys), so ConfigManager turns that false
on once and marks the config. A false chosen after that -- the new checkbox,
or by hand -- must stay false.
"""

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.config_manager import ConfigManager  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
MARKER = ConfigManager.LIVE_IN_TICKER_MARKER


@pytest.fixture
def files(tmp_path):
    template = {"display": {"brightness": 90,
                            "vegas_scroll": {"enabled": False, "live_in_ticker": True}}}
    paths = {name: tmp_path / f"{name}.json" for name in ("config", "secrets", "template")}
    paths["template"].write_text(json.dumps(template))
    paths["secrets"].write_text("{}")
    return paths


def _load(files, config):
    files["config"].write_text(json.dumps(config))
    manager = ConfigManager(config_path=str(files["config"]),
                            secrets_path=str(files["secrets"]))
    manager.template_path = str(files["template"])
    manager.load_config()
    return manager, json.loads(files["config"].read_text())


def _vegas(saved):
    return saved["display"]["vegas_scroll"]


def test_the_old_default_is_turned_on_once_and_marked(files):
    old = {"display": {"brightness": 90,
                       "vegas_scroll": {"enabled": True, "live_in_ticker": False}}}
    manager, saved = _load(files, old)
    assert _vegas(saved)["live_in_ticker"] is True
    assert _vegas(saved)[MARKER] is True
    assert _vegas(manager.config)["live_in_ticker"] is True
    # The config as it was is kept beside it.
    backup = json.loads(Path(f"{files['config']}.backup").read_text())
    assert _vegas(backup)["live_in_ticker"] is False


def test_false_chosen_after_the_migration_stays_false(files):
    chosen = {"display": {"brightness": 90,
                          "vegas_scroll": {"enabled": True, "live_in_ticker": False,
                                           MARKER: True}}}
    _manager, saved = _load(files, chosen)
    assert _vegas(saved)["live_in_ticker"] is False


def test_a_marked_config_is_not_written_again(files):
    marked = {"display": {"brightness": 90,
                          "vegas_scroll": {"enabled": True, "live_in_ticker": True,
                                           MARKER: True}}}
    files["config"].write_text(json.dumps(marked))
    before = files["config"].stat().st_mtime_ns
    manager = ConfigManager(config_path=str(files["config"]),
                            secrets_path=str(files["secrets"]))
    manager.template_path = str(files["template"])
    manager.load_config()
    assert files["config"].stat().st_mtime_ns == before
    assert not Path(f"{files['config']}.backup").exists()


def test_a_config_without_vegas_settings_gets_the_new_default(files):
    _manager, saved = _load(files, {"display": {"brightness": 90}})
    assert _vegas(saved)["live_in_ticker"] is True
    assert _vegas(saved)[MARKER] is True


def test_true_already_is_only_marked(files):
    on = {"display": {"brightness": 90,
                      "vegas_scroll": {"enabled": True, "live_in_ticker": True}}}
    _manager, saved = _load(files, on)
    assert _vegas(saved)["live_in_ticker"] is True and _vegas(saved)[MARKER] is True


def test_a_second_load_keeps_a_false_set_in_between(files):
    old = {"display": {"brightness": 90,
                       "vegas_scroll": {"enabled": True, "live_in_ticker": False}}}
    _manager, saved = _load(files, old)
    _vegas(saved)["live_in_ticker"] = False             # the checkbox, unticked
    _manager, saved = _load(files, saved)
    assert _vegas(saved)["live_in_ticker"] is False


def test_the_shipped_template_has_the_new_default_and_never_the_marker():
    template = json.loads((REPO / "config" / "config.template.json").read_text(encoding="utf-8"))
    vegas = template["display"]["vegas_scroll"]
    assert vegas["live_in_ticker"] is True
    assert MARKER not in vegas
