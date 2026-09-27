"""src/common/README.md says it lists every module; keep it true.

frame_timing, json_body and render_gate landed without rows, so the page a
plugin author reads to see what core offers was missing three modules.
"""

from pathlib import Path

COMMON = Path(__file__).resolve().parent.parent / "src" / "common"


def _modules():
    return sorted(p.stem for p in COMMON.glob("*.py") if p.stem != "__init__")


def test_every_module_has_a_table_row_and_a_section():
    readme = (COMMON / "README.md").read_text(encoding="utf-8")
    missing_rows = [m for m in _modules() if f"| [`{m}`](#{m}) |" not in readme]
    missing_sections = [m for m in _modules() if f"\n### {m}\n" not in readme]
    assert not missing_rows, f"no summary-table row for: {missing_rows}"
    assert not missing_sections, f"no '### <module>' section for: {missing_sections}"
