"""src/malloc_tuning.py: glibc arena cap and periodic malloc_trim, ctypes mocked."""
import ctypes
from pathlib import Path
from unittest import mock

import pytest

from src import malloc_tuning as mt


class FakeLibc:
    """Stands in for ctypes.CDLL(None) on glibc: records calls."""

    def __init__(self, trim_result=1, glibc=True):
        self.trims = []
        self.mallopts = []
        self._trim_result = trim_result
        if glibc:
            self.gnu_get_libc_version = lambda: b'2.41'
        self.malloc_trim = mock.Mock(side_effect=self._trim)
        self.mallopt = mock.Mock(side_effect=self._mallopt)

    def _trim(self, pad):
        self.trims.append(pad)
        if isinstance(self._trim_result, Exception):
            raise self._trim_result
        return self._trim_result

    def _mallopt(self, param, value):
        self.mallopts.append((param, value))
        return 1


@pytest.fixture(autouse=True)
def fresh_libc(monkeypatch):
    """Each test loads the C library itself; nothing real is called."""
    monkeypatch.setattr(mt, '_libc', mt._UNLOADED)
    monkeypatch.delenv('MALLOC_ARENA_MAX', raising=False)
    yield


def _on_glibc(monkeypatch, libc):
    monkeypatch.setattr(mt.sys, 'platform', 'linux')
    cdll = mock.Mock(return_value=libc)
    monkeypatch.setattr(mt.ctypes, 'CDLL', cdll)
    return cdll


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


# -- loading ----------------------------------------------------------------

@pytest.mark.parametrize('platform', ['win32', 'darwin', 'freebsd14'])
def test_not_linux_loads_nothing(monkeypatch, platform):
    monkeypatch.setattr(mt.sys, 'platform', platform)
    cdll = mock.Mock(side_effect=AssertionError('must not load'))
    monkeypatch.setattr(mt.ctypes, 'CDLL', cdll)
    assert mt._load_libc() is None
    assert mt.cap_arenas() is False
    trimmer = mt.MallocTrimmer(interval=0)
    assert not trimmer.available
    assert trimmer.maybe_trim() is False
    cdll.assert_not_called()


def test_linux_without_glibc_is_a_noop(monkeypatch):
    """musl: no gnu_get_libc_version (and no malloc_trim) -- nothing is called."""
    libc = FakeLibc(glibc=False)
    del libc.malloc_trim
    _on_glibc(monkeypatch, libc)
    assert mt._load_libc() is None
    assert mt.cap_arenas() is False
    assert mt.MallocTrimmer(interval=0).maybe_trim() is False
    assert libc.mallopts == []


def test_cdll_failure_is_a_noop(monkeypatch):
    monkeypatch.setattr(mt.sys, 'platform', 'linux')
    monkeypatch.setattr(mt.ctypes, 'CDLL', mock.Mock(side_effect=OSError('no libc')))
    assert mt._load_libc() is None
    assert mt.cap_arenas() is False


def test_loads_once(monkeypatch):
    cdll = _on_glibc(monkeypatch, FakeLibc())
    mt._load_libc()
    mt._load_libc()
    mt.MallocTrimmer()
    assert cdll.call_count == 1


def test_declares_c_signatures(monkeypatch):
    libc = FakeLibc()
    _on_glibc(monkeypatch, libc)
    mt._load_libc()
    assert libc.malloc_trim.argtypes == [ctypes.c_size_t]
    assert libc.mallopt.argtypes == [ctypes.c_int, ctypes.c_int]


# -- cap_arenas ---------------------------------------------------------------

def test_cap_arenas_calls_mallopt(monkeypatch):
    libc = FakeLibc()
    _on_glibc(monkeypatch, libc)
    assert mt.cap_arenas() is True
    assert libc.mallopts == [(mt.M_ARENA_MAX, 2)]
    assert mt.M_ARENA_MAX == -8  # glibc's malloc.h


def test_cap_arenas_defers_to_the_environment(monkeypatch):
    libc = FakeLibc()
    _on_glibc(monkeypatch, libc)
    monkeypatch.setenv('MALLOC_ARENA_MAX', '4')
    assert mt.cap_arenas() is False
    assert libc.mallopts == []


def test_cap_arenas_swallows_errors(monkeypatch):
    libc = FakeLibc()
    libc.mallopt = mock.Mock(side_effect=RuntimeError('boom'))
    _on_glibc(monkeypatch, libc)
    assert mt.cap_arenas() is False


def test_cap_arenas_matches_the_unit():
    """The in-process default is the value the unit's MALLOC_ARENA_MAX carries."""
    unit = (Path(__file__).resolve().parent.parent / 'systemd' / 'ledmatrix.service').read_text()
    assert f'Environment=MALLOC_ARENA_MAX={mt.DEFAULT_ARENA_MAX}\n' in unit


# -- MallocTrimmer ------------------------------------------------------------

def test_trim_waits_one_interval_then_rate_limits(monkeypatch):
    libc = FakeLibc()
    _on_glibc(monkeypatch, libc)
    clock = Clock()
    trimmer = mt.MallocTrimmer(interval=300, clock=clock)
    assert trimmer.available
    assert trimmer.maybe_trim() is False          # start-up: not yet
    clock.t += 299.9
    assert trimmer.maybe_trim() is False
    clock.t += 0.1
    assert trimmer.maybe_trim() is True
    assert libc.trims == [0]
    clock.t += 100
    assert trimmer.maybe_trim() is False          # rate-limited
    clock.t += 200
    assert trimmer.maybe_trim() is True
    assert libc.trims == [0, 0]


def test_trim_reports_nothing_released(monkeypatch):
    libc = FakeLibc(trim_result=0)
    _on_glibc(monkeypatch, libc)
    clock = Clock()
    trimmer = mt.MallocTrimmer(interval=10, clock=clock)
    clock.t += 10
    assert trimmer.maybe_trim() is False
    assert libc.trims == [0]


def test_trim_failure_disables_trimming(monkeypatch):
    libc = FakeLibc(trim_result=RuntimeError('boom'))
    _on_glibc(monkeypatch, libc)
    clock = Clock()
    trimmer = mt.MallocTrimmer(interval=10, clock=clock)
    clock.t += 10
    assert trimmer.maybe_trim() is False
    clock.t += 10
    assert trimmer.maybe_trim() is False
    assert libc.trims == [0]                      # not retried
    assert not trimmer.available


# -- wiring -------------------------------------------------------------------

def test_run_py_caps_arenas_before_threads():
    """run.py applies the cap before the watchdog or the controller import."""
    src = (Path(__file__).resolve().parent.parent / 'run.py').read_text()
    cap = src.index('malloc_tuning.cap_arenas()')
    assert cap < src.index('display_watchdog.watchdog.begin_startup()')
    assert cap < src.index('from src.display_controller import main')


def test_render_loop_trims_between_screens():
    src = (Path(__file__).resolve().parent.parent / 'src' / 'display_controller.py').read_text()
    loop = src.index('display_watchdog.watchdog.loop_pass()')
    trim = src.index('trimmer.maybe_trim()')
    assert loop < trim < src.index('outcome = runner.run(plan, manager_to_display)')


@pytest.mark.skipif(not mt.sys.platform.startswith('linux'), reason='glibc only')
def test_real_libc_on_linux():
    """On a real Linux C library the calls go through without raising."""
    if mt._load_libc() is None:
        pytest.skip('not glibc')
    trimmer = mt.MallocTrimmer(interval=0)
    assert trimmer.available
    assert trimmer.maybe_trim() in (True, False)
    assert trimmer.available  # did not fail and disable itself
