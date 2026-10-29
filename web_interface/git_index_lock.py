"""Clear a stale ``.git/index.lock`` before Update Code touches the checkout.

git creates ``.git/index.lock`` while it rewrites the index and removes it when
it finishes, or when it is stopped with SIGTERM/SIGINT. A git that dies any
other way leaves the file behind: ``subprocess.run(..., timeout=...)`` SIGKILLs
its child when the timeout expires (a slow pull or checkout on a Pi), and a
power cut or an OOM kill does the same. From then on every git command that
writes the index fails with::

    fatal: Unable to create '.../.git/index.lock': File exists.

and Update Code can never succeed again until someone removes it over SSH.

``clear_stale_index_lock`` removes the file only when it is provably stale: no
git process is running in this checkout, and the file is old enough that no
git can have just created it. When it cannot tell (no ``/proc``), it leaves
the file alone. Standard library only.
"""
import logging
import os
import signal
import subprocess
import time
from pathlib import Path

logger = logging.getLogger(__name__)

#: A lock younger than this is left alone even when no git process is seen,
#: so a git that started between the process scan and the removal keeps it.
MIN_STALE_AGE_SECONDS = 5

#: ``clear_stale_index_lock`` outcomes.
ABSENT = 'absent'      # no lock file: nothing to do
REMOVED = 'removed'    # it was stale and has been removed
IN_USE = 'in_use'      # a git process is running here (or the lock is brand new)
UNKNOWN = 'unknown'    # could not tell whether git is running; left alone

#: How long a timed-out git gets to exit after SIGTERM (which lets it remove
#: its own lock) before the whole process group is SIGKILLed.
TERM_GRACE_SECONDS = 5

#: Timeout for commands that rewrite the checkout (pull, checkout, reset,
#: stash). A Pi on an SD card can take minutes; killing git is what leaves the
#: lock behind, so this is generous.
MUTATING_TIMEOUT_SECONDS = 600

_PROC = Path('/proc')


def index_lock_path(project_dir):
    """``<project_dir>/.git/index.lock``, or None when ``.git`` is not a directory."""
    git_dir = Path(project_dir) / '.git'
    return git_dir / 'index.lock' if git_dir.is_dir() else None


def _is_within(path, root):
    try:
        Path(path).relative_to(root)
        return True
    except ValueError:
        return False


def git_running_in(project_dir, proc_root=_PROC):
    """True when a git process may be working in ``project_dir``.

    Scans ``/proc`` for processes named ``git`` or ``git-*`` (``git-remote-https``
    and friends) whose working directory is inside the checkout, or whose
    arguments name a path in it (``git -C ...``, ``--git-dir=...``). A git process
    whose working directory cannot be read (another user's) counts as running
    here: it might be. Returns None when ``/proc`` is unavailable.
    """
    proc_root = Path(proc_root)
    if not proc_root.is_dir():
        return None
    root = Path(project_dir).resolve()
    try:
        entries = list(proc_root.iterdir())
    except OSError:
        return None
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            comm = (entry / 'comm').read_text().strip()
        except OSError:
            continue  # exited meanwhile
        if comm != 'git' and not comm.startswith('git-'):
            continue
        try:
            cwd = os.readlink(entry / 'cwd')
        except FileNotFoundError:
            continue  # exited meanwhile
        except OSError:
            return True
        if _is_within(cwd, root) or _names_checkout(entry, cwd, root):
            return True
    return False


def _names_checkout(entry, cwd, root):
    """True when the process's arguments name a path in the checkout
    (``-C``, ``--git-dir``, ``--work-tree``, absolute or relative to its
    working directory): git can work on it from anywhere. Unreadable
    arguments count as naming it."""
    try:
        args = os.fsdecode((entry / 'cmdline').read_bytes()).split('\0')
    except FileNotFoundError:
        return False  # exited meanwhile
    except OSError:
        return True
    for arg in args[1:]:
        value = arg.split('=', 1)[1] if arg.startswith('--') and '=' in arg else arg
        if value and _is_within(os.path.normpath(os.path.join(cwd, value)), root):
            return True
    return False


def clear_stale_index_lock(project_dir, proc_root=_PROC, now=None):
    """Remove ``.git/index.lock`` when no git process can still own it.

    Returns one of ABSENT, REMOVED, IN_USE or UNKNOWN.
    """
    lock = index_lock_path(project_dir)
    if lock is None:
        return ABSENT
    try:
        mtime = lock.stat().st_mtime
    except FileNotFoundError:
        return ABSENT
    except OSError:
        return UNKNOWN
    running = git_running_in(project_dir, proc_root)
    if running is None:
        return UNKNOWN
    age = (time.time() if now is None else now) - mtime
    if running or age < MIN_STALE_AGE_SECONDS:
        return IN_USE
    try:
        lock.unlink()
    except FileNotFoundError:
        return ABSENT  # its owner finished after all
    except OSError as exc:
        logger.warning("Could not remove stale git lock %s: %s", lock, exc)
        return UNKNOWN
    logger.warning("Removed stale git lock %s (%.0fs old, no git process running); "
                   "a previous git command was killed before it finished", lock, age)
    return REMOVED


def _stop_group(proc):
    """SIGTERM the process group, then SIGKILL whatever is left of it."""
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        proc.wait(timeout=TERM_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        pass
    # Also reaps git's children (git-remote-https, rebase helpers) that
    # outlive a killed parent.
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def run_git(cmd, *, cwd=None, timeout=None, capture_output=True, text=True, **kwargs):
    """``subprocess.run`` for git, without leaving ``.git/index.lock`` behind.

    ``subprocess.run`` SIGKILLs only the direct child when ``timeout`` expires,
    which skips git's cleanup and orphans its helpers. This starts git in its
    own session, stops the whole group with SIGTERM before SIGKILL, removes a
    lock nothing owns any more, then raises ``TimeoutExpired`` as
    ``subprocess.run`` would.
    """
    if capture_output:
        kwargs['stdout'] = kwargs['stderr'] = subprocess.PIPE
    with subprocess.Popen(cmd, cwd=cwd, text=text, start_new_session=True, **kwargs) as proc:
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            _stop_group(proc)
            exc.stdout, exc.stderr = proc.communicate()
            if cwd is not None:
                clear_stale_index_lock(cwd)
            raise
        except BaseException:
            _stop_group(proc)
            raise
    return subprocess.CompletedProcess(proc.args, proc.returncode, stdout, stderr)


def read_only_env():
    """Environment for git calls that only read: ``GIT_OPTIONAL_LOCKS=0``
    stops ``status`` and ``describe --dirty`` refreshing (and so locking) the
    index, so a poll that times out cannot leave a lock."""
    return {**os.environ, 'GIT_OPTIONAL_LOCKS': '0'}


def manual_fix_hint(project_dir):
    """The sentence shown when a lock blocks the update and was not removed."""
    lock = Path(project_dir) / '.git' / 'index.lock'
    return (f"If it keeps coming back, stop the LEDMatrix services, kill any running git "
            f"(ps aux | grep git), then remove it with rm -f {lock} and try again.")
