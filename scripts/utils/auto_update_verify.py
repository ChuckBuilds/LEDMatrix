#!/usr/bin/env python3
"""Check that an automatic LEDMatrix update left the device working; roll it back if not.

Started by the web interface's weekly updater (web_interface/auto_update.py)
through ledmatrix-update-verify.service, right after it pulls new code. It has
to run outside the web service: checking the update means restarting that
service, and a check running inside it would be killed by its own restart.

It must not be the code it is checking, either. The updater copies this file
to data/auto_update_verifier.py *before* pulling and the unit runs that copy,
so a broken update cannot break its own rollback. Standard library only for
the same reason: the rollback cannot depend on packages the update changed.

The updater leaves data/auto_update_pending.json:

    {"status": "pending", "old_head": ..., "new_head": ...,
     "display_was_active": bool, "dependency_failures": [...], "created_at": ...}

This moves its status to "verifying" and then to one of "success",
"rolled_back" or "rollback_failed", with "reason" and "detail" saying why.
The web interface reports that outcome and raises a banner for anything but
success.
"""
import json
import os
import subprocess  # nosec B404 - list-form argv only, no shell  # nosemgrep
import sys
from collections import namedtuple
import tempfile
import time
import traceback
import urllib.error
import urllib.request
from pathlib import Path

PENDING_NAME = 'auto_update_pending.json'
REQUIREMENT_FILES = ('requirements.txt', 'web_interface/requirements.txt')
WEB_HEALTH_URL = 'http://127.0.0.1:5000/api/v3/system/version'
#: How long the services get to come up after a restart...
HEALTH_TIMEOUT_SECONDS = 180
#: ...and how long they must then stay up. Restart=on-failure makes a crash
#: loop look healthy between attempts, so a single "is-active" proves nothing.
STABLE_SECONDS = 45
POLL_SECONDS = 5
WEB_CHECK_TIMEOUT_SECONDS = 5
SYSTEMCTL_QUERY_TIMEOUT_SECONDS = 10
RESTART_TIMEOUT_SECONDS = 90
GIT_TIMEOUT_SECONDS = 60
GIT_RESET_TIMEOUT_SECONDS = 120
PIP_TIMEOUT_SECONDS = 600
#: All of a rollback's dependency reinstalls together. A pip that times out
#: or fails is not retried: systemd stops this unit at TimeoutStartSec, and a
#: rollback killed half-way leaves the update reported as still verifying.
PIP_BUDGET_SECONDS = 600
#: sudoers matches the exact command line, so bash is named by path, the same
#: candidates src/common/permission_utils.install_requirements_file tries...
BASH_CANDIDATES = ('/usr/bin/bash', '/bin/bash')
#: ...and, like it, moves to the next one only when sudo refused the command
#: line (permission_utils.SUDO_REFUSAL_PHRASES), never after pip itself ran.
SUDO_REFUSAL_PHRASES = ('a password is required', 'is not allowed to run', 'no tty present')

#: The longest one health check can take: restart and wait, roll back
#: (diff, reset, reinstalls), restart and wait again. A wait's last poll can
#: start just before its deadline and run every query to its timeout.
_WAIT_WORST_SECONDS = (HEALTH_TIMEOUT_SECONDS + STABLE_SECONDS + WEB_CHECK_TIMEOUT_SECONDS
                       + 2 * SYSTEMCTL_QUERY_TIMEOUT_SECONDS + POLL_SECONDS)
WORST_CASE_SECONDS = (2 * (2 * RESTART_TIMEOUT_SECONDS + _WAIT_WORST_SECONDS)
                      + GIT_TIMEOUT_SECONDS + GIT_RESET_TIMEOUT_SECONDS + PIP_BUDGET_SECONDS)

#: What a command that could not run at all reports: its callers only read
#: these three fields, the same ones a completed subprocess has.
_Failed = namedtuple('_Failed', 'returncode stdout stderr')


def pending_path(project_root):
    return Path(project_root) / 'data' / PENDING_NAME


def read_pending(path):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def write_pending(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix='.auto_update_pending_')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _web_responds(url=WEB_HEALTH_URL):
    try:
        with urllib.request.urlopen(url, timeout=WEB_CHECK_TIMEOUT_SECONDS) as resp:  # nosec B310 - fixed loopback URL
            return resp.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _short(sha):
    return (sha or 'unknown')[:7]


class Verifier:
    def __init__(self, project_root, run=subprocess.run, sleep=time.sleep,
                 clock=time.monotonic, web_responds=_web_responds, log=None):
        self.project_root = Path(project_root)
        self.pending_file = pending_path(project_root)
        self.run = run
        self.sleep = sleep
        self.clock = clock
        self.web_responds = web_responds
        self.log = log or (lambda msg: print(f'[auto-update-verify] {msg}', flush=True))

    def _run(self, args, timeout=GIT_TIMEOUT_SECONDS):
        try:
            return self.run(args, cwd=str(self.project_root), capture_output=True,
                            text=True, timeout=timeout)
        except (subprocess.SubprocessError, OSError) as e:
            return _Failed(returncode=1, stdout='', stderr=str(e))

    # -- services ---------------------------------------------------------

    def service_active(self, unit):
        return self._run(['systemctl', 'is-active', unit],
                         timeout=SYSTEMCTL_QUERY_TIMEOUT_SECONDS).stdout.strip() == 'active'

    def restart_count(self, unit):
        out = self._run(['systemctl', 'show', '-p', 'NRestarts', '--value', unit],
                        timeout=SYSTEMCTL_QUERY_TIMEOUT_SECONDS).stdout.strip()
        return int(out) if out.isdigit() else None

    def restart(self, unit):
        result = self._run(['sudo', '-n', 'systemctl', 'restart', f'{unit}.service'],
                           timeout=RESTART_TIMEOUT_SECONDS)
        if result.returncode != 0:
            self.log(f'restarting {unit} failed: {(result.stderr or "").strip()}')
        return result.returncode == 0

    def restart_services(self, display):
        """Restart what should be running. False if any restart command failed."""
        ok = True
        # A display the user had stopped stays stopped.
        if display:
            ok = self.restart('ledmatrix') and ok
        return self.restart('ledmatrix-web') and ok

    def wait_healthy(self, display):
        """None once the services are up and stay up, else what went wrong."""
        deadline = self.clock() + HEALTH_TIMEOUT_SECONDS + STABLE_SECONDS
        healthy_since = baseline = None
        web = disp = False
        count_known = True
        while self.clock() < deadline:
            web = self.web_responds()
            disp = self.service_active('ledmatrix') if display else True
            restarts = self.restart_count('ledmatrix') if display else None
            # Without a restart count a crash loop looks healthy between
            # attempts, so an unreadable count never counts as stable.
            count_known = not display or restarts is not None
            if web and disp and count_known and (healthy_since is None or restarts == baseline):
                if healthy_since is None:
                    healthy_since, baseline = self.clock(), restarts
                elif self.clock() - healthy_since >= STABLE_SECONDS:
                    return None
            else:
                healthy_since = None
            self.sleep(POLL_SECONDS)
        problems = []
        if not web:
            problems.append('the web interface did not respond')
        if not disp:
            problems.append('the display service did not stay running')
        if web and disp and not count_known:
            problems.append("the display service's restart count could not be read")
        return '; '.join(problems) or 'the display service kept restarting'

    # -- rollback ---------------------------------------------------------

    def changed_requirements(self, old, new):
        result = self._run(['git', 'diff', '--name-only', old, new])
        # If the diff is unavailable, reinstall both rather than guess.
        changed = set(result.stdout.split()) if result.returncode == 0 else set(REQUIREMENT_FILES)
        return [rel for rel in REQUIREMENT_FILES if rel in changed]

    def install_requirements(self, rel, deadline=None):
        """Install one requirements file through the root wrapper, by ``deadline``."""
        wrapper = self.project_root / 'scripts' / 'fix_perms' / 'safe_pip_install.sh'
        req = self.project_root / rel
        if not req.exists():
            return True
        for bash in BASH_CANDIDATES:
            timeout = PIP_TIMEOUT_SECONDS
            if deadline is not None:
                timeout = min(timeout, deadline - self.clock())
                if timeout <= 0:
                    self.log(f'no time left to reinstall {rel}')
                    return False
            result = self._run(['sudo', '-n', bash, str(wrapper), str(req)], timeout=timeout)
            if result.returncode == 0:
                return True
            # Only a refused command line is worth the next candidate. A pip
            # that ran and failed, or timed out, would just do it again.
            if not any(phrase in (result.stderr or '') for phrase in SUDO_REFUSAL_PHRASES):
                # Not pip's output: it can echo an index URL's credentials.
                self.log(f'reinstalling {rel} failed (exit {result.returncode})')
                return False
        return False

    def rollback(self, pending):
        """Reset to the previous commit and its dependencies. Returns (ok, detail)."""
        old, new = pending.get('old_head'), pending.get('new_head')
        if not old:
            return False, 'the commit to roll back to is unknown'
        requirements = self.changed_requirements(old, new) if new else list(REQUIREMENT_FILES)
        # --hard: the updater refuses to run with local edits to tracked core
        # files (web_interface/auto_update.local_changes), so outside the
        # plugin folders the only thing this discards is the update. Edits
        # under plugins/ and plugin-repos/, which that check leaves to the
        # pull's --autostash, are reset along with it.
        result = self._run(['git', 'reset', '--hard', old], timeout=GIT_RESET_TIMEOUT_SECONDS)
        if result.returncode != 0:
            return False, (f'"git reset --hard {old}" failed: '
                           f'{(result.stderr or result.stdout or "").strip()}')
        deadline = self.clock() + PIP_BUDGET_SECONDS
        failed = [rel for rel in requirements if not self.install_requirements(rel, deadline)]
        if failed:
            return True, ('reinstalling the previous dependencies from ' + ', '.join(failed)
                          + ' failed; run Install Base Requirements from the Tools tab')
        return True, ''

    # -- the check itself -------------------------------------------------

    def _finish(self, pending, status, reason=None, detail=None):
        pending.update({'status': status, 'reason': reason, 'detail': detail or None,
                        'finished_at': time.time()})
        write_pending(self.pending_file, pending)
        self.log(' '.join(p for p in (status, reason or '', detail or '') if p))

    def verify(self):
        pending = read_pending(self.pending_file)
        if not pending or pending.get('status') != 'pending':
            self.log('no update is waiting to be verified')
            return 0
        pending['status'] = 'verifying'
        write_pending(self.pending_file, pending)

        display = bool(pending.get('display_was_active'))
        dependency_failures = pending.get('dependency_failures') or []
        if dependency_failures:
            # Never restart onto code whose packages did not install.
            reason = 'installing its dependencies failed (' + ', '.join(dependency_failures) + ')'
        elif not self.restart_services(display):
            # The old process may still be answering; checking it would pass
            # an update that never started.
            reason = 'restarting the services failed'
        else:
            reason = self.wait_healthy(display)
            if reason is None:
                self._finish(pending, 'success')
                return 0

        self.log(f'update to {_short(pending.get("new_head"))} is unhealthy ({reason}); '
                 f'rolling back to {_short(pending.get("old_head"))}')
        ok, detail = self.rollback(pending)
        if not ok:
            self._finish(pending, 'rollback_failed', reason, detail)
            return 1
        still = (self.wait_healthy(display) if self.restart_services(display)
                 else 'restarting the services failed')
        if still:
            self._finish(pending, 'rollback_failed', reason,
                         f'still unhealthy after rolling back: {still}'
                         + (f'; {detail}' if detail else ''))
            return 1
        self._finish(pending, 'rolled_back', reason, detail)
        return 0


def main(argv):
    if len(argv) != 2:
        print('usage: auto_update_verify.py PROJECT_ROOT', file=sys.stderr)
        return 2
    verifier = Verifier(Path(argv[1]))
    try:
        return verifier.verify()
    except Exception as e:
        traceback.print_exc()
        # Whatever happened, the web interface must not be left thinking the
        # check is still running.
        try:
            pending = read_pending(verifier.pending_file) or {}
            if pending.get('status') in ('pending', 'verifying'):
                pending.update({'status': 'rollback_failed', 'reason': 'the health check crashed',
                                'detail': str(e), 'finished_at': time.time()})
                write_pending(verifier.pending_file, pending)
        except OSError:
            pass
        return 1


if __name__ == '__main__':
    sys.exit(main(sys.argv))
