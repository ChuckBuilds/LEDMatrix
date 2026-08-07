"""Guard: enum dropdowns in the plugin config form honour x-options.labels.

The form derives an option's visible text from its value — underscores
replaced, title case applied ("day_first" -> "Day First"). That cannot
express every label a schema needs: "vs" reads as "Vs", and "abbrev" says
nothing about the "Sep 19" it produces. Schemas can supply x-options.labels
instead, the same convention the checkbox-group widget already uses.

These tests extract the enum <select> block *out of the shipped template*
and render that, so they exercise the production expression rather than a
copy of it. If the fallback or the lookup changes, these tests render the
changed code and fail — a duplicated fragment here would silently keep
passing.
"""
import re
from pathlib import Path

import pytest
from jinja2 import DictLoader, Environment

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_FORM = (PROJECT_ROOT / 'web_interface' / 'templates' / 'v3' / 'partials'
               / 'plugin_config.html')
ARRAY_TABLE_JS = (PROJECT_ROOT / 'web_interface' / 'static' / 'v3' / 'js'
                  / 'widgets' / 'array-table.js')

# The enum branch: from the `{% set enum_labels %}` line through `</select>`.
ENUM_BLOCK_RE = re.compile(
    r"(\{%\s*set enum_labels\s*=.*?</select>)", re.S
)


def _shipped_enum_block() -> str:
    """Return the live enum <select> block lifted from plugin_config.html."""
    source = CONFIG_FORM.read_text(encoding='utf-8')
    match = ENUM_BLOCK_RE.search(source)
    assert match, (
        'could not find the enum <select> block in plugin_config.html — the '
        'template changed shape and this guard needs updating'
    )
    return match.group(1)


def _render(prop: dict, value=None) -> str:
    """Render the shipped enum block with a minimal fixture."""
    env = Environment(loader=DictLoader({'f': _shipped_enum_block()}),
                      autoescape=True)
    return env.get_template('f').render(
        prop=prop, value=value, field_id='fid', full_key='k'
    )


def _option_labels(html: str) -> dict:
    """Map each rendered option's value to its visible text."""
    return {
        value: text.strip()
        for value, text in re.findall(
            r'<option value="([^"]*)"[^>]*>(.*?)</option>', html, re.S
        )
    }


def test_labels_are_used_when_supplied() -> None:
    html = _render({
        'enum': ['vs', 'date_time'],
        'x-options': {'labels': {'vs': 'VS', 'date_time': 'Date and time'}},
    })
    assert _option_labels(html) == {'vs': 'VS', 'date_time': 'Date and time'}


def test_unlabelled_values_keep_the_humanised_fallback() -> None:
    """Schemas without labels must render exactly as they did before."""
    html = _render({'enum': ['day_first', 'weekday']})
    assert _option_labels(html) == {'day_first': 'Day First', 'weekday': 'Weekday'}


def test_partial_labels_fall_back_per_value() -> None:
    """A labels map covering some values leaves the rest humanised."""
    html = _render({'enum': ['vs', 'day_first'],
                    'x-options': {'labels': {'vs': 'VS'}}})
    assert _option_labels(html) == {'vs': 'VS', 'day_first': 'Day First'}


def test_option_values_are_unchanged_by_labelling() -> None:
    """Labels are display-only: the submitted value stays the enum value."""
    html = _render({'enum': ['abbrev'],
                    'x-options': {'labels': {'abbrev': 'Sep 19'}}})
    assert _option_labels(html) == {'abbrev': 'Sep 19'}


def test_selected_option_still_tracks_the_current_value() -> None:
    """Labelling must not disturb which option is marked selected."""
    html = _render({'enum': ['abbrev', 'numeric'],
                    'x-options': {'labels': {'abbrev': 'Sep 19'}}},
                   value='numeric')
    selected = re.search(r'<option value="([^"]+)"[^>]*selected', html)
    assert selected and selected.group(1) == 'numeric'


@pytest.mark.parametrize('key', ['x-options', 'x_options'])
def test_both_option_key_spellings_work(key: str) -> None:
    """The template accepts either spelling, as its other widgets do."""
    html = _render({'enum': ['vs'], key: {'labels': {'vs': 'VS'}}})
    assert _option_labels(html) == {'vs': 'VS'}


def test_table_column_enum_falls_back_to_the_raw_value() -> None:
    """Array-table columns must not title-case values that were never labelled.

    Those columns hold values such as ticker symbols, where "aapl" -> "Aapl"
    would be wrong, so their fallback stays the raw value.
    """
    source = CONFIG_FORM.read_text(encoding='utf-8')
    assert 'col_labels.get(opt, opt)' in source, (
        'array-table column options must fall back to the raw value, not the '
        'humanised one'
    )


def test_dynamically_added_table_rows_use_the_same_labels() -> None:
    """Rows added client-side must label options like the server-rendered ones.

    array-table.js builds new rows in the browser; if it printed the raw value
    a column would read differently before and after a page reload.
    """
    js = ARRAY_TABLE_JS.read_text(encoding='utf-8')
    assert 'function enumOptionLabel' in js, (
        'array-table.js lost its enum label helper'
    )
    raw_option_text = re.findall(r'o\.textContent\s*=\s*opt\s*;', js)
    assert not raw_option_text, (
        'array-table.js renders an enum option as its raw value; it must go '
        'through enumOptionLabel() so dynamic rows match server-rendered ones'
    )
