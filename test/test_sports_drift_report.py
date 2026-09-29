"""scripts/sports_drift_report.py counts what it says it counts.

The report is the progress measure for the reconcile-then-promote stages in
docs/SPORTS_UNIFICATION.md, so a wrong count is a wrong roadmap. These build a
tiny plugin tree by hand and check each definition: families across classes,
variants per class, what is ignored (docstrings, comments, annotations), what
folding does, and that a missing checkout is a report, not a failure.
"""

import importlib.util
import json
import textwrap
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "sports_drift_report.py"


@pytest.fixture(scope="module")
def drift():
    spec = importlib.util.spec_from_file_location("sports_drift_report", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write(root: Path, sport: str, fname: str, body: str) -> None:
    d = root / "plugins" / f"{sport}-scoreboard"
    d.mkdir(parents=True, exist_ok=True)
    (d / fname).write_text(textwrap.dedent(body), encoding="utf-8")


SAME = '''
    class SportsLive:
        def update(self):
            """Docstrings are not behaviour."""
            return self.x + 1  # nor are comments

        def over(self, game: dict) -> bool:
            return game["clock"] == "0:00"
'''

OTHER = '''
    class SportsLive:
        def update(self):
            return self.x + 2

        def over(self, game):
            """Annotations and docstrings differ; the body does not."""
            return game["clock"] == "0:00"
'''


@pytest.fixture
def tree(tmp_path):
    for sport in ("afl", "nrl", "soccer"):
        _write(tmp_path, sport, "sports.py", SAME)
    _write(tmp_path, "hockey", "sports.py", OTHER)
    # A second class role with its own update(): same family, counted apart.
    _write(tmp_path, "hockey", "sports.py", OTHER + '''
    class SportsRecent:
        def update(self):
            return None
''')
    # manager.py classes are named after the sport; they must line up.
    _write(tmp_path, "afl", "manager.py", '''
    class AFLScoreboardPlugin:
        def get_live_modes(self):
            return ["afl_live"]
''')
    _write(tmp_path, "nrl", "manager.py", '''
    class NRLScoreboardPlugin:
        def get_live_modes(self):
            return ["nrl_live"]
''')
    return tmp_path


def _row(rep, fname, family):
    return next(r for r in rep["rows"] if r["file"] == fname and r["family"] == family)


def test_counts_variants_per_class_and_ignores_docstrings_and_annotations(drift, tree):
    rep = drift.report(drift.build(tree / "plugins", drift.DEFAULT_FILES), 1, 2)
    over = _row(rep, "sports.py", "over")
    assert (over["plugins"], over["copies"], over["variants"]) == (4, 4, 1)

    update = _row(rep, "sports.py", "update")
    # SportsLive.update: afl/nrl/soccer agree, hockey differs -> 2; plus
    # hockey's SportsRecent.update -> 3 variants in the family.
    assert update["copies"] == 5
    assert update["variants"] == 3
    assert update["worst_class_variants"] == 2
    live = next(k for k in update["classes"] if k["class"] == "SportsLive")
    assert live["groups"] == [["afl", "nrl", "soccer"], ["hockey"]]


def test_sport_names_fold_classes_together_but_not_variants(drift, tree):
    rep = drift.report(drift.build(tree / "plugins", drift.DEFAULT_FILES), 1, 2)
    modes = _row(rep, "manager.py", "get_live_modes")
    assert [k["class"] for k in modes["classes"]] == ["SScoreboardPlugin"]
    # "afl_live" vs "nrl_live": two exact variants, one once sport names fold.
    assert (modes["variants"], modes["folded"]) == (2, 1)


def test_drifted_identical_and_one_outlier_lists(drift, tree):
    rep = drift.report(drift.build(tree / "plugins", drift.DEFAULT_FILES), 2, 3)
    assert [(r["file"], r["family"]) for r in rep["drifted"]] == [("sports.py", "update")]
    assert ("sports.py", "over") in [(r["file"], r["family"]) for r in rep["identical"]]
    assert ("sports.py", "update") in [(r["file"], r["family"])
                                       for r in rep["one_outlier"]]


def test_fold(drift):
    assert drift.fold("nfl_live") == "S_live"
    assert drift.fold("UFCScoreboardPlugin") == "SScoreboardPlugin"
    assert drift.fold("HockeyLive") == "SLive"
    assert drift.fold("display_mode") == "display_mode"


def test_no_checkout_is_a_report_not_a_failure(drift, tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("LEDMATRIX_PLUGINS", raising=False)
    assert drift.main([]) == 0
    assert drift.main(["--plugins", str(tmp_path), "--markdown"]) == 0
    assert "Nothing to report" in capsys.readouterr().out


def test_cli_markdown_json_and_family_diff(drift, tree, tmp_path, capsys):
    out = tmp_path / "report.json"
    assert drift.main(["--plugins", str(tree), "--markdown", "--json", str(out),
                       "--min-plugins", "2", "--family", "sports.py::update",
                       "--diff"]) == 0
    text = capsys.readouterr().out
    assert "| `sports.py::update` |" in text
    assert "+++ hockey-scoreboard/sports.py" in text
    data = json.loads(out.read_text(encoding="utf-8"))
    assert {"files", "drifted", "identical", "one_outlier", "rows"} <= set(data)
