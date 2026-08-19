"""A checkbox group must not post back options it cannot show.

The enum that lets the widget draw checkboxes is also what validates the
saved value. When a league retires a team code -- OAK for the Athletics, ARI
for the Coyotes -- or a schema drops an option, a config that still holds the
old value has nothing to render for it. The value stayed in the hidden
``_data`` input regardless, because that input is seeded from the stored array
and only rebuilt by ``updateCheckboxGroupData()`` on change. Editing any other
field on that plugin therefore posted the stale value back, the schema
rejected it, and the save endpoint returned 400
``CONFIG_VALIDATION_FAILED`` -- so the whole plugin became uneditable until
the user worked out which invisible entry was at fault.

Runtime was never affected: plugin loading treats schema violations as
warn/degrade, and the stale code already matched no team. Only the web UI
blocked.

These tests render the checkbox-group block lifted *out of the shipped
template*, following test_enum_option_labels.py, so they exercise the
production expression rather than a copy that could drift from it.
"""
import json
import re
from pathlib import Path

from jinja2 import DictLoader, Environment

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_FORM = (PROJECT_ROOT / 'web_interface' / 'templates' / 'v3' / 'partials'
               / 'plugin_config.html')

# The checkbox-group branch: from its `{% elif %}` guard through the sentinel
# hidden input that closes it. Anchored on the guard so the match cannot run on
# into a neighbouring widget branch.
BLOCK_RE = re.compile(
    r"\{%\s*elif x_widget == 'checkbox-group'\s*%\}(.*?)"
    r"<input type=\"hidden\" name=\"\{\{ full_key \}\}\[\]\" value=\"\">",
    re.S,
)


def _shipped_block() -> str:
    """Return the live checkbox-group block lifted from plugin_config.html."""
    source = CONFIG_FORM.read_text(encoding='utf-8')
    match = BLOCK_RE.search(source)
    assert match, (
        'could not find the checkbox-group block in plugin_config.html — the '
        'template changed shape and this guard needs updating'
    )
    block = match.group(1)
    assert 'data-option-value' in block, 'extracted the wrong branch'
    assert '{% elif' not in block, 'extraction ran past the checkbox-group branch'
    return block


def _render(prop: dict, value=None) -> str:
    env = Environment(loader=DictLoader({'f': _shipped_block()}), autoescape=True)
    return env.get_template('f').render(
        prop=prop, value=value, field_id='fid', full_key='k'
    )


def _submitted(html: str) -> list:
    """The array the form will actually post: the hidden _data input."""
    match = re.search(r'id="fid_data"[^>]*\svalue=\'([^\']*)\'', html)
    assert match, f'hidden _data input not found in:\n{html}'
    return json.loads(match.group(1).replace('&#39;', "'"))


def _checked(html: str) -> list:
    return re.findall(r'data-option-value="([^"]+)"[^>]*checked', html)


MLB = {'type': 'array', 'items': {'type': 'string', 'enum': ['NYY', 'BOS', 'ATH']},
       'x-widget': 'checkbox-group'}


def test_a_retired_code_is_not_posted_back() -> None:
    """The regression: OAK became ATH, and OAK used to ride along on save."""
    html = _render(MLB, ['NYY', 'OAK'])
    assert _submitted(html) == ['NYY'], 'stale value would still be submitted'


def test_the_dropped_value_is_named_rather_than_vanishing() -> None:
    html = _render(MLB, ['NYY', 'OAK'])
    assert 'OAK' in html
    assert 'data-stale-options' in html


def test_valid_values_are_untouched_and_still_checked() -> None:
    html = _render(MLB, ['NYY', 'ATH'])
    assert _submitted(html) == ['NYY', 'ATH']
    assert sorted(_checked(html)) == ['ATH', 'NYY']
    assert 'data-stale-options' not in html


def test_an_all_stale_selection_clears_rather_than_blocking() -> None:
    html = _render(MLB, ['OAK', 'SD'])
    assert _submitted(html) == []


def test_an_empty_enum_leaves_the_value_alone() -> None:
    """No options means nothing to validate against — filtering would wipe it."""
    prop = {'type': 'array', 'items': {'type': 'string'}, 'x-widget': 'checkbox-group'}
    html = _render(prop, ['ANYTHING', 'GOES'])
    assert _submitted(html) == ['ANYTHING', 'GOES']


def test_unset_value_falls_back_to_the_default() -> None:
    prop = dict(MLB, default=['BOS'])
    html = _render(prop, None)
    assert _submitted(html) == ['BOS']
    assert _checked(html) == ['BOS']
