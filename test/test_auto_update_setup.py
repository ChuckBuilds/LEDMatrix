"""Installing the automatic-update health check from the display service.

src/auto_update_setup.py is how a user who never opens a terminal gets updates
with a safety net: it runs as root inside the display service and writes
systemd units. So it has to install exactly the right thing when asked, do
nothing when not asked, refuse templates that would run as anyone but the web
user, and say why whenever it could not finish.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import auto_update_setup as aus  # noqa: E402

ON = {'auto_update': {'enabled': True}}


class FakeSystemctl:
    def __init__(self, fail=(), activates=True):
        self.calls = []
        self.fail = set(fail)
        self.activates = activates
        self.active = False

    def __call__(self, args, **kwargs):
        assert args[0] == 'systemctl', args
        self.calls.append(args[1:])
        verb = args[1]
        if verb == 'is-active':
            return subprocess.CompletedProcess(args, 0 if self.active else 3,
                                               stdout='active\n' if self.active else 'inactive\n', stderr='')
        if verb in self.fail:
            return subprocess.CompletedProcess(args, 1, stdout='', stderr=f'{verb} refused')
        if verb in ('restart', 'enable') and self.activates and (verb == 'restart' or '--now' in args):
            self.active = True
        return subprocess.CompletedProcess(args, 0, stdout='', stderr='')


def project(tmp_path, user='hdpi', workdir=None):
    root = tmp_path / 'LEDMatrix'
    (root / 'systemd').mkdir(parents=True)
    for name in aus.UNITS:
        shutil.copy(ROOT / 'systemd' / name, root / 'systemd' / name)
    etc = tmp_path / 'etc'
    etc.mkdir()
    (etc / aus.WEB_UNIT).write_text(
        f'[Service]\nUser={user}\nWorkingDirectory={workdir or root}\n', encoding='utf-8')
    return root, etc


def setup(root, etc, systemctl, root_user=True):
    return aus.UpdateHelperSetup(project_root=root, systemd_dir=etc, run=systemctl,
                                 is_root=lambda: root_user,
                                 lookup_ids=lambda user: None if user == 'ghost' else (1000, 1000),
                                 clock=lambda: 1234.0)


def result(root):
    return json.loads((root / aus.RESULT_REL).read_text())


def test_does_nothing_while_updates_are_off(tmp_path):
    root, etc = project(tmp_path)
    systemctl = FakeSystemctl()
    assert setup(root, etc, systemctl).ensure({'auto_update': {'enabled': False}}) is None
    assert systemctl.calls == []
    assert not (etc / aus.SERVICE_UNIT).exists()
    assert not (root / aus.RESULT_REL).exists()


def test_installs_both_units_for_the_web_user(tmp_path):
    root, etc = project(tmp_path)
    systemctl = FakeSystemctl()
    out = setup(root, etc, systemctl).ensure(ON)

    assert out['status'] == 'installed' and result(root)['status'] == 'installed'
    service = (etc / aus.SERVICE_UNIT).read_text()
    assert 'User=hdpi' in service
    assert f'ExecStart=/usr/bin/python3 {root}/data/auto_update_verifier.py {root}' in service
    assert f'PathExists={root}/data/auto_update_verify.request' in (etc / aus.PATH_UNIT).read_text()
    assert '__' not in service
    assert ['daemon-reload'] in systemctl.calls
    assert ['enable', aus.PATH_UNIT] in systemctl.calls
    assert systemctl.active


def test_second_start_changes_nothing(tmp_path):
    root, etc = project(tmp_path)
    systemctl = FakeSystemctl()
    setup(root, etc, systemctl).ensure(ON)
    first = result(root)
    systemctl.calls.clear()
    setup(root, etc, systemctl).ensure(ON)
    assert systemctl.calls == [['is-active', aus.PATH_UNIT], ['is-active', aus.PATH_UNIT]]
    assert result(root) == first, "an unchanged setup must not rewrite its result every boot"


def test_a_changed_template_is_reinstalled(tmp_path):
    root, etc = project(tmp_path)
    systemctl = FakeSystemctl()
    setup(root, etc, systemctl).ensure(ON)
    template = root / 'systemd' / aus.SERVICE_UNIT
    template.write_text(template.read_text() + '\n# newer\n')
    systemctl.calls.clear()
    assert setup(root, etc, systemctl).ensure(ON)['message'] == 'Installed the update health check.'
    assert (etc / aus.SERVICE_UNIT).read_text().endswith('# newer\n')
    assert ['daemon-reload'] in systemctl.calls


def test_a_template_that_would_not_run_as_the_web_user_is_refused(tmp_path):
    root, etc = project(tmp_path)
    template = root / 'systemd' / aus.SERVICE_UNIT
    template.write_text(template.read_text().replace('User=__USER__', 'User=root'))
    systemctl = FakeSystemctl()
    out = setup(root, etc, systemctl).ensure(ON)
    assert out['status'] == 'failed' and 'refusing' in out['message']
    assert not (etc / aus.SERVICE_UNIT).exists()
    assert systemctl.calls == []


def test_a_path_unit_that_starts_something_else_is_refused(tmp_path):
    root, etc = project(tmp_path)
    template = root / 'systemd' / aus.PATH_UNIT
    template.write_text(template.read_text().replace('Unit=ledmatrix-update-verify.service', 'Unit=other.service'))
    out = setup(root, etc, FakeSystemctl()).ensure(ON)
    assert out['status'] == 'failed' and not (etc / aus.PATH_UNIT).exists()


@pytest.mark.parametrize('case, expected', [
    ('not_root', 'not running as root'),
    ('no_web_unit', 'not installed'),
    ('other_folder', 'runs from'),
    ('bad_user', 'not a usable account'),
])
def test_reports_why_it_could_not_install(tmp_path, case, expected):
    root, etc = project(tmp_path, user='ghost' if case == 'bad_user' else 'hdpi',
                        workdir=tmp_path / 'elsewhere' if case == 'other_folder' else None)
    if case == 'no_web_unit':
        (etc / aus.WEB_UNIT).unlink()
    out = setup(root, etc, FakeSystemctl(), root_user=case != 'not_root').ensure(ON)
    assert out['status'] == 'failed' and expected in out['message']
    assert result(root)['message'] == out['message']
    assert not (etc / aus.SERVICE_UNIT).exists()


def test_a_path_unit_that_will_not_start_is_a_failure(tmp_path):
    root, etc = project(tmp_path)
    out = setup(root, etc, FakeSystemctl(activates=False)).ensure(ON)
    assert out['status'] == 'failed' and 'did not start' in out['message']


def test_systemctl_errors_are_reported(tmp_path):
    root, etc = project(tmp_path)
    out = setup(root, etc, FakeSystemctl(fail={'daemon-reload'})).ensure(ON)
    assert out['status'] == 'failed' and 'daemon-reload refused' in out['message']


def test_not_a_systemd_host_is_left_alone(tmp_path):
    root, _ = project(tmp_path)
    systemctl = FakeSystemctl()
    assert setup(root, tmp_path / 'no-etc', systemctl).ensure(ON) is None
    assert systemctl.calls == []


def test_the_result_is_kept_when_it_cannot_be_given_to_the_web_user(tmp_path, monkeypatch):
    """Caught by CI, which runs as a non-root Linux user: chown needs root, and
    a failed chown used to discard the result, so the General tab never
    learned whether setup worked."""
    def refuse(*args):
        raise PermissionError('Operation not permitted')
    monkeypatch.setattr(aus.os, 'chown', refuse, raising=False)
    root, etc = project(tmp_path)
    out = setup(root, etc, FakeSystemctl()).ensure(ON)
    assert out['status'] == 'installed'
    assert result(root) == out
