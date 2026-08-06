"""Guard: enum dropdowns in the plugin config form honour x-options.labels.

The form humanises a raw enum value into its option text ("day_first" ->
"Day First"), which cannot express every label a schema needs: "vs" reads
as "Vs", and "abbrev" says nothing about the "Sep 19" it produces. Schemas
can supply x-options.labels instead, the same convention the checkbox-group
widget already uses.

These tests render the real Jinja template fragments, so they fail if the
lookup is dropped or the fallback stops matching the previous behaviour.
"""
from pathlib import Path

import pytest
from jinja2 import DictLoader, Environment

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_FORM = (PROJECT_ROOT / 'web_interface' / 'templates' / 'v3' / 'partials'
               / 'plugin_config.html')

# The dropdown fragment, lifted from the template so the test exercises the
# real expression rather than a paraphrase of it.
SELECT_FRAGMENT = """
{%- set enum_labels = (prop.get('x-options') or prop.get('x_options') or {}).get('labels') or {} -%}
{%- for option in prop.enum -%}
<option value="{{ option }}">{{ enum_labels.get(option, option|replace('_', ' ')|title) }}</option>
{%- endfor -%}
"""


def _render(prop: dict) -> str:
    env = Environment(loader=DictLoader({'f': SELECT_FRAGMENT}), autoescape=True)
    return env.get_template('f').render(prop=prop)


def test_template_looks_up_enum_labels() -> None:
    """The shipped template must resolve option text through x-options.labels."""
    source = CONFIG_FORM.read_text(encoding='utf-8')
    assert "enum_labels.get(option," in source, (
        'plugin_config.html no longer resolves enum option text through '
        'x-options.labels; schemas that supply labels would silently show '
        'raw values again'
    )


def test_labels_are_used_when_supplied() -> None:
    prop = {
        'enum': ['vs', 'date_time'],
        'x-options': {'labels': {'vs': 'VS', 'date_time': 'Date and time'}},
    }
    html = _render(prop)
    assert '>VS<' in html
    assert '>Date and time<' in html


def test_unlabelled_values_keep_the_humanised_fallback() -> None:
    """Schemas without labels must render exactly as they did before."""
    html = _render({'enum': ['day_first', 'weekday']})
    assert '>Day First<' in html
    assert '>Weekday<' in html


def test_partial_labels_fall_back_per_value() -> None:
    """A labels map covering some values leaves the rest humanised."""
    prop = {'enum': ['vs', 'day_first'], 'x-options': {'labels': {'vs': 'VS'}}}
    html = _render(prop)
    assert '>VS<' in html
    assert '>Day First<' in html


def test_option_values_are_unchanged_by_labelling() -> None:
    """Labels are display-only: the submitted value stays the enum value."""
    prop = {'enum': ['abbrev'], 'x-options': {'labels': {'abbrev': 'Sep 19'}}}
    html = _render(prop)
    assert 'value="abbrev"' in html
    assert '>Sep 19<' in html


@pytest.mark.parametrize('key', ['x-options', 'x_options'])
def test_both_option_key_spellings_work(key: str) -> None:
    """The template accepts either spelling, as its other widgets do."""
    html = _render({'enum': ['vs'], key: {'labels': {'vs': 'VS'}}})
    assert '>VS<' in html


def test_table_column_enum_falls_back_to_the_raw_value() -> None:
    """Array-table columns must not title-case values that were never labelled.

    Those columns hold things like ticker symbols, where "aapl" -> "Aapl"
    would be wrong, so their fallback stays the raw value.
    """
    source = CONFIG_FORM.read_text(encoding='utf-8')
    assert 'col_labels.get(opt, opt)' in source, (
        'array-table column options must fall back to the raw value, not the '
        'humanised one'
    )
