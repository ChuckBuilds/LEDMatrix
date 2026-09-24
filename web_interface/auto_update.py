"""Weekly automatic updates: LEDMatrix code, then installed plugins.

Turned on by ``auto_update.enabled`` in config.json (General tab, or the
installer's --enable-auto-update). Off by default: an update pulls new code and
restarts the display, which nobody should get without asking for it.

Nothing here is allowed to leave a device broken without saying so:

* **Checks first.** The code update is skipped, and the reason reported, when
  the checkout has local edits or commits, a rebase or merge is in progress,
  the branch has no upstream, disk is low, the newest commit was already
  rolled back once, or the health check is not set up.
* **Verified, and rolled back.** The pull itself is the Overview "Update Code"
  path (``perform_core_update``). Restarting and checking the result is handed
  to ledmatrix-update-verify.service (scripts/utils/auto_update_verify.py),
  started through ledmatrix-update-verify.path by a request file, which
  survives the web service restart, confirms both services come up and stay
  up, and otherwise resets to the previous commit and its dependencies.
* **Set up without SSH.** Those units are installed by the installer, or by the
  display service when the toggle is switched on (src/auto_update_setup.py).
* **Plugins after the code.** Plugins use the Plugin Store's own update, which
  refuses versions this core cannot run and restores the old copy when an
  install fails. When the code changed they wait until it passed its check,
  so they are never gated against code that is about to be rolled back.
* **Loud failures.** Anything other than success is written to the state file,
  shown under the toggle and raised as a banner on the Overview tab.

The schedule survives restarts through data/auto_update_state.json (gitignored).
"""
import importlib.util
import json
import logging
import os
import shutil
import subprocess  # nosec B404 - list-form argv only, no shell  # nosemgrep
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATE_REL = Path('data') / 'auto_update_state.json'
PENDING_REL = Path('data') / 'auto_update_pending.json'
REQUEST_REL = Path('data') / 'auto_update_verify.request'
SETUP_RESULT_REL = Path('data') / 'auto_update_setup.json'
VERIFIER_COPY_REL = Path('data') / 'auto_update_verifier.py'
VERIFIER_SOURCE = PROJECT_ROOT / 'scripts' / 'utils' / 'auto_update_verify.py'
STATE_FILE = PROJECT_ROOT / STATE_REL
PENDING_FILE = PROJECT_ROOT / PENDING_REL
SETUP_RESULT_FILE = PROJECT_ROOT / SETUP_RESULT_REL

VERIFY_UNIT = 'ledmatrix-update-verify'
PATH_UNIT = f'{VERIFY_UNIT}.path'

UPDATE_INTERVAL_SECONDS = 7 * 24 * 3600
#: A failure that is probably transient (usually no network) is retried the
#: next day rather than a week later.
RETRY_AFTER_FAILURE_SECONDS = 24 * 3600
#: Local hours an update prefers, since it restarts the display...
QUIET_HOURS = range(2, 5)
#: ...but a device that is never powered at night must still get updates.
QUIET_HOURS_GRACE_SECONDS = 24 * 3600
CHECK_INTERVAL_SECONDS = 30 * 60
#: Let the web service settle after boot (and never pull in a crash loop).
STARTUP_DELAY_SECONDS = 10 * 60
MIN_FREE_BYTES = 300 * 1024 * 1024
#: How long the health check gets to pick up a request before the update is undone.
HANDOFF_TIMEOUT_SECONDS = 90
#: The health check takes a few minutes at most; far past that, it is lost.
VERIFY_LOST_SECONDS = 45 * 60

#: Core outcomes that need the user's attention.
ERROR_OUTCOMES = frozenset({'error', 'blocked', 'rolled_back', 'rollback_failed', 'lost'})

HELPER_MISSING_MESSAGE = (
    "LEDMatrix code updates are paused until the update health check is set up. "
    "The display service sets it up when it starts while automatic updates are on, "
    "so restart the display from the Overview tab. If this keeps happening, the "
    "setup result under Automatic Updates on the General tab says why. Plugin "
    "updates still run."
)

_STATE_LOCK = threading.RLock()


def is_enabled(config):
    return bool((config.get('auto_update') or {}).get('enabled', False))


def _read_json(path):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _write_json(path, data):
    path = Path(path)
    tmp = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f'.{path.stem}_')
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
        tmp = None
    finally:
        if tmp and os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass


def _unlink(path):
    try:
        Path(path).unlink()
    except OSError:
        pass


def load_state(state_file=STATE_FILE):
    return _read_json(state_file) or {}


def save_state(state, state_file=STATE_FILE):
    with _STATE_LOCK:
        try:
            _write_json(state_file, state)
        except OSError as e:
            logger.warning("Could not save auto-update state: %s", e)


def dismiss_alert(alert_id, state_file=STATE_FILE):
    with _STATE_LOCK:
        state = load_state(state_file)
        state['alert_dismissed'] = str(alert_id)
        save_state(state, state_file)


def _zone(tz_name):
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(tz_name) if tz_name else None
    except Exception:
        return None


def _local_datetime(ts, tz_name):
    return datetime.fromtimestamp(ts, _zone(tz_name))


def _short(sha):
    return (sha or 'unknown')[:7]


def is_due(now, next_due, local_hour):
    """Due once ``next_due`` has passed, in quiet hours or after the grace period."""
    if next_due is None or now < next_due:
        return False
    return local_hour in QUIET_HOURS or now >= next_due + QUIET_HOURS_GRACE_SECONDS


def _plugin_fingerprint(store_manager, plugin_dir):
    """(manifest version, git sha): what changing tells us an update landed."""
    version = None
    try:
        with open(plugin_dir / 'manifest.json', 'r', encoding='utf-8') as f:
            manifest = json.load(f)
        if isinstance(manifest, dict):  # valid JSON need not be an object
            if manifest.get('local_only'):
                return None
            version = manifest.get('version')
    except (OSError, ValueError):
        pass
    sha = None
    try:
        git_info = store_manager._get_local_git_info(plugin_dir)
        sha = git_info.get('sha') if git_info else None
    except Exception:
        logger.debug("git info unavailable for %s", plugin_dir, exc_info=True)
    return (version, sha)


def update_plugins(store_manager, operation_history=None):
    """Update every installed plugin that has an update. Returns (updated, failed)."""
    updated, failed = [], []
    plugins_dir = Path(store_manager.plugins_dir)
    # list_installed_plugins() reports manifest ids, and a plugin's directory
    # may be named differently (ledmatrix-stocks/ holding id "stocks"), so
    # the directory comes from the store's own lookup, not a join.
    find_dir = getattr(store_manager, '_find_plugin_path', None)
    for plugin_id in sorted(store_manager.list_installed_plugins()):
        plugin_dir = (find_dir(plugin_id) if find_dir else None) or plugins_dir / plugin_id
        before = _plugin_fingerprint(store_manager, plugin_dir)
        if before is None:
            continue  # local_only: managed by hand, never from the registry
        try:
            ok = store_manager.update_plugin(plugin_id)
        except Exception:
            logger.exception("Automatic update of plugin %s raised", plugin_id)
            ok = False
        if not ok:
            failed.append(plugin_id)
            status = 'failed'
        elif _plugin_fingerprint(store_manager, plugin_dir) != before:
            updated.append(plugin_id)
            status = 'success'
        else:
            continue  # already current; not worth a history entry every week
        if operation_history:
            try:
                operation_history.record_operation(
                    'update', plugin_id=plugin_id, status=status,
                    details={'automatic': True})
            except Exception:
                logger.debug("Could not record auto-update history", exc_info=True)
    return updated, failed


def _service_active(unit):
    try:
        result = subprocess.run(['systemctl', 'is-active', unit],
                                capture_output=True, text=True, timeout=5)
        return result.stdout.strip() == 'active'
    except (subprocess.SubprocessError, OSError):
        return False


def helper_ready():
    """True when the health check can be triggered (its path unit is watching)."""
    return _service_active(PATH_UNIT)


def restart_service(unit):
    """Restart a unit only if it is running, via the sudoers-allowed command."""
    if not _service_active(unit):
        return False
    try:
        result = subprocess.run(['sudo', '-n', 'systemctl', 'restart', f'{unit}.service'],
                                capture_output=True, text=True, timeout=30)
    except (subprocess.SubprocessError, OSError) as e:
        logger.warning("Auto-update could not restart %s: %s", unit, e)
        return False
    if result.returncode != 0:
        logger.warning("Auto-update restart of %s failed: %s", unit, result.stderr.strip())
    return result.returncode == 0


def start_setup_if_needed(was_enabled, config):
    """After settings are saved: switching updates on finishes setting them up.

    The health check units are installed by the display service at startup
    (src/auto_update_setup.py), so switching the toggle on restarts it.
    Returns a note for the save message, or None.
    """
    if was_enabled or not is_enabled(config) or helper_ready():
        return None
    if not _service_active('ledmatrix'):
        return 'Automatic update setup finishes the next time the display service starts.'
    if restart_service('ledmatrix'):
        return 'Finishing automatic update setup: the display is restarting.'
    return 'Could not restart the display to finish automatic update setup; restart it from the Overview tab.'


#: Top-level folders of separately installed plugins. Edits there are not the
#: core's local changes: the Plugin Store updates plugins in place, including
#: the bundled ones committed under plugin-repos/, and the pull's --autostash
#: carries those edits across and reapplies them.
SEPARATE_INSTALL_DIRS = ('plugins', 'plugin-repos')


def local_changes(project_dir, run=None, timeout=30):
    """Tracked files edited in the checkout, as both code updates count them.

    The one definition shared by the automatic update's preflight and
    ``perform_core_update`` (Update Code), so the preflight's promise that
    nothing will be stashed holds for the pull that follows it.

    * Mode-only changes do not count (``core.fileMode=false``): installers
      chmod tracked scripts, and --autostash carries modes across the pull.
    * Paths under ``SEPARATE_INSTALL_DIRS`` do not count. The match is on
      the leading folder, not a substring, so
      ``web_interface/static/v3/js/plugins/x.js`` is still a core edit.

    Returns the changed paths, or None when git could not tell. Raises what
    ``run`` raises (e.g. ``subprocess.TimeoutExpired``).
    """
    run = run or subprocess.run
    result = run(['git', '-c', 'core.fileMode=false', 'status', '--porcelain',
                  '--untracked-files=no', '-z'],
                 cwd=str(project_dir), capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        return None
    changed = []
    entries = iter((result.stdout or '').split('\0'))
    for entry in entries:
        if len(entry) < 4:
            continue
        paths = [entry[3:]]
        if entry[0] in 'RC' or entry[1] in 'RC':
            paths.append(next(entries, ''))  # -z puts a rename's source next
        if any(p and p.split('/', 1)[0] not in SEPARATE_INSTALL_DIRS for p in paths):
            changed.append(paths[0])
    return changed


def describe_local_changes(changed):
    """Why an automatic update refused to touch a checkout with local edits."""
    example = f' (for example {changed[0]})' if changed else ''
    return (f'{len(changed or ()) or "Some"} tracked file(s) in the LEDMatrix folder were edited '
            f'locally{example}. Automatic updates will not stash your changes; '
            'commit or revert them, or update manually with Update Code.')


def _load_verifier(path):
    spec = importlib.util.spec_from_file_location('ledmatrix_auto_update_verifier', str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AutoUpdater:
    """Decides when an automatic update is due and runs it."""

    def __init__(self, config_manager, core_update, store_manager=None,
                 plugin_manager=None, schema_manager=None, operation_history=None,
                 project_root=PROJECT_ROOT, state_file=None, clock=time.time,
                 restart=restart_service, run=subprocess.run,
                 service_active=_service_active, helper_ready=helper_ready,
                 verifier_source=VERIFIER_SOURCE, disk_free=None, sleep=time.sleep):
        self.config_manager = config_manager
        self.core_update = core_update
        self.store_manager = store_manager
        self.plugin_manager = plugin_manager
        self.schema_manager = schema_manager
        self.operation_history = operation_history
        self.project_root = Path(project_root)
        self.state_file = Path(state_file) if state_file else self.project_root / STATE_REL
        self.pending_file = self.project_root / PENDING_REL
        self.request_file = self.project_root / REQUEST_REL
        self.verifier_copy = self.project_root / VERIFIER_COPY_REL
        self.verifier_source = Path(verifier_source)
        self.clock = clock
        self.restart = restart
        self.run_command = run
        self.service_active = service_active
        self.helper_ready = helper_ready
        self.disk_free = disk_free or (lambda path: shutil.disk_usage(path).free)
        self.sleep = sleep
        self._stop = threading.Event()
        self._thread = None

    # -- scheduling ---------------------------------------------------------

    def tick(self):
        """Do whatever is due. Returns True if an update ran."""
        now = self.clock()
        state = load_state(self.state_file)
        # Before the enabled check: a verification started before the user
        # switched updates off still has to be reported.
        if self._finalize_verification(state, now) == 'waiting':
            return False

        try:
            config = self.config_manager.load_config()
        except Exception:
            logger.debug("Auto-update could not load config", exc_info=True)
            return False
        if not is_enabled(config):
            return False

        if state.get('plugins_pending'):
            self._run_deferred_plugins(state, now)
            return True

        if state.get('next_due') is None:
            # Just switched on: due now, which in practice means the next
            # quiet-hours window rather than the moment Save was clicked.
            state['next_due'] = now
            save_state(state, self.state_file)

        local_hour = _local_datetime(now, config.get('timezone')).hour
        if not is_due(now, state['next_due'], local_hour):
            return False
        self.run(state)
        return True

    def run(self, state=None):
        state = load_state(self.state_file) if state is None else state
        logger.info("Automatic update starting")
        try:
            core = self.update_core(state)
        except Exception as e:
            logger.exception("Automatic core update raised")
            core = {'outcome': 'error', 'message': f'The LEDMatrix update failed unexpectedly: {e}'}

        deferred = core['outcome'] == 'verifying'
        # A core whose rollback failed is in an unknown state: as when the
        # health check reports rollback_failed, plugins are left alone and
        # nothing is restarted onto it.
        stranded = core['outcome'] == 'rollback_failed'
        updated, failed = ([], []) if deferred or stranded else self._update_plugins()
        self._store_run(state, core, updated, failed)
        logger.info("Automatic update: core %s (%s); plugins updated=%s failed=%s%s",
                    core['outcome'], core['message'], updated, failed,
                    '; plugins wait for the health check' if deferred
                    else '; plugins left alone' if stranded else '')

        # Code restarts belong to the health check; this only covers plugins.
        if updated:
            self.restart('ledmatrix')
        return state['last_result']

    def _store_run(self, state, core, updated, failed):
        now = self.clock()
        deferred = core['outcome'] == 'verifying'
        state.update({
            'last_run': now,
            'next_due': now + (RETRY_AFTER_FAILURE_SECONDS if core['outcome'] == 'error'
                               else UPDATE_INTERVAL_SECONDS),
            'plugins_pending': deferred,
            'last_result': {
                'core_outcome': core['outcome'],
                'core_message': core['message'],
                'plugins_updated': updated,
                'plugins_failed': failed,
                'plugins_deferred': deferred,
            },
        })
        self._apply_result(state, now)
        save_state(state, self.state_file)

    # -- core update --------------------------------------------------------

    def _git(self, *args, timeout=60):
        return self.run_command(['git', *args], cwd=str(self.project_root),
                                capture_output=True, text=True, timeout=timeout)

    def _count(self, rev_range):
        out = self._git('rev-list', '--count', rev_range).stdout.strip()
        return int(out) if out.isdigit() else 0

    def preflight(self, state):
        """Decide whether the code update may run.

        Returns ``(outcome, message, info)`` with outcome ``ready``,
        ``up_to_date``, ``blocked`` (needs the user) or ``error`` (retry soon).
        """
        if not self.helper_ready():
            return 'blocked', HELPER_MISSING_MESSAGE, {}

        git_dir = self._git('rev-parse', '--git-dir')
        if git_dir.returncode != 0:
            return 'blocked', 'LEDMatrix is not installed as a git checkout, so it cannot update itself.', {}
        git_dir = Path(git_dir.stdout.strip())
        if not git_dir.is_absolute():
            git_dir = self.project_root / git_dir
        rebase_dirs = [git_dir / name for name in ('rebase-merge', 'rebase-apply') if (git_dir / name).exists()]
        if rebase_dirs:
            # A rebase really in progress leaves HEAD detached. With HEAD back
            # on a branch it was abandoned -- typically a pull that stopped on
            # a conflict, then a checkout -- and it makes every later pull
            # fail. It holds no work (local edits are checked separately
            # below), so clear it rather than strand a device nobody SSHes into.
            if self._git('symbolic-ref', '-q', 'HEAD').returncode != 0:
                return 'blocked', ('A git rebase is in progress in the LEDMatrix folder. '
                                   'Finish or abort it; automatic updates will not touch it.'), {}
            cleared = self._git('rebase', '--quit')
            if cleared.returncode != 0 or any(path.exists() for path in rebase_dirs):
                return 'blocked', ('An abandoned git rebase in the LEDMatrix folder could not be cleared: '
                                   f'{(cleared.stderr or "").strip() or "unknown error"}.'), {}
            logger.warning("Cleared an abandoned git rebase from %s", ', '.join(str(p) for p in rebase_dirs))
        if (git_dir / 'MERGE_HEAD').exists():
            return 'blocked', ('A git merge is in progress in the LEDMatrix folder. '
                               'Finish or abort it; automatic updates will not touch it.'), {}

        if self._git('rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{u}').returncode != 0:
            return 'blocked', ('The current branch has no upstream to update from (or HEAD is detached). '
                               'Use Update Code once, or Tools -> Switch branch.'), {}

        # The same predicate perform_core_update refuses on when called from
        # here, so what passes this check is never stashed by the pull.
        changed = local_changes(self.project_root, run=self.run_command)
        if changed is None or changed:
            return 'blocked', describe_local_changes(changed), {}

        free = self.disk_free(str(self.project_root))
        if free < MIN_FREE_BYTES:
            return 'blocked', (f'Only {free // (1024 * 1024)} MB of disk space is free; an update '
                               f'needs at least {MIN_FREE_BYTES // (1024 * 1024)} MB.'), {}

        fetch = self._git('fetch', '--quiet', timeout=120)
        if fetch.returncode != 0:
            detail = next((ln.strip() for ln in (fetch.stderr or '').splitlines() if ln.strip()), '')
            return 'error', f'Could not check for LEDMatrix updates: {detail or "git fetch failed"}.', {}

        ahead = self._count('@{u}..HEAD')
        if ahead:
            return 'blocked', (f'This checkout has {ahead} local commit(s) that are not upstream. '
                               'Automatic updates will not rebase them; update manually with Update Code.'), {}
        if not self._count('HEAD..@{u}'):
            return 'up_to_date', 'LEDMatrix is already up to date.', {}

        upstream = self._git('rev-parse', '@{u}').stdout.strip()
        if upstream and upstream == state.get('rolled_back_head'):
            return 'up_to_date', (f'The newest LEDMatrix version ({_short(upstream)}) failed its health '
                                  'check and was rolled back before; waiting for a newer one.'), {}
        head = self._git('rev-parse', 'HEAD').stdout.strip()
        return 'ready', '', {'old_head': head, 'upstream_head': upstream}

    def update_core(self, state):
        try:
            outcome, message, info = self.preflight(state)
        except (subprocess.SubprocessError, OSError) as e:
            return {'outcome': 'error', 'message': f'Pre-update checks failed: {e}.'}
        if outcome != 'ready':
            return {'outcome': outcome, 'message': message}
        old_head = info['old_head']

        display_was_active = self.service_active('ledmatrix')
        try:
            # Copied before pulling, so the checker is the known-good version.
            self.verifier_copy.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.verifier_source, self.verifier_copy)
            verifier = _load_verifier(self.verifier_copy)
        except Exception as e:
            return {'outcome': 'error', 'message': f'Could not prepare the update health check: {e}.'}

        try:
            # Refuse rather than stash: edits made since the preflight are the
            # user's, and nothing would ever restore a stash taken here.
            core = self.core_update(stash_local_changes=False)
        except Exception as e:
            logger.exception("perform_core_update raised")
            core = {'status': 'error', 'message': f'Update failed: {e}'}
        new_head = self._git('rev-parse', 'HEAD').stdout.strip()
        if core.get('local_changes') is not None and new_head == old_head:
            return {'outcome': 'blocked', 'message': describe_local_changes(core['local_changes'])}
        pending = {
            'status': 'pending',
            'old_head': old_head,
            'new_head': new_head,
            'display_was_active': display_was_active,
            'dependency_failures': list(core.get('dependency_failures') or []),
            'created_at': self.clock(),
        }

        def rollback():
            return verifier.Verifier(self.project_root, run=self.run_command).rollback(pending)

        if core.get('status') != 'success':
            message = core.get('message') or 'The LEDMatrix update failed.'
            if new_head and new_head != old_head:
                ok, detail = rollback()
                message += (' The partial update was rolled back.' if ok
                            else f' Rolling back the partial update also failed: {detail}.')
                if not ok:
                    return {'outcome': 'rollback_failed', 'message': message}
            return {'outcome': 'error', 'message': message}
        if new_head == old_head:
            return {'outcome': 'up_to_date', 'message': core.get('message') or 'LEDMatrix is already up to date.'}

        handoff = {'outcome': 'verifying',
                   'message': (f'Updated LEDMatrix from {_short(old_head)} to {_short(new_head)}; '
                               'restarting and checking the services.')}
        # Recorded before the handoff: the health check restarts this process.
        self._store_run(state, handoff, [], [])
        try:
            _write_json(self.pending_file, pending)
            self.request_file.write_text(new_head, encoding='utf-8')
            picked_up = self._wait_for_pickup()
        except OSError as e:
            logger.error("Could not request the update health check: %s", e)
            picked_up = False
        if picked_up:
            return handoff

        # No health check means no update: undo it while the old code is
        # still the code that is running.
        logger.error("%s did not start the health check; rolling the update back", PATH_UNIT)
        _unlink(self.request_file)
        _unlink(self.pending_file)
        ok, detail = rollback()
        if not ok:
            return {'outcome': 'rollback_failed',
                    'message': (f'The update health check did not start, and rolling back to '
                                f'{_short(old_head)} failed: {detail}.')}
        return {'outcome': 'blocked',
                'message': ('The update health check did not start, so the update was undone. '
                            f'Check "systemctl status {PATH_UNIT}".'
                            + (f' Note: {detail}.' if detail else ''))}

    def _wait_for_pickup(self):
        for _ in range(HANDOFF_TIMEOUT_SECONDS):
            pending = _read_json(self.pending_file)
            if pending and pending.get('status') != 'pending':
                return True
            self.sleep(1)
        pending = _read_json(self.pending_file)
        return bool(pending and pending.get('status') != 'pending')

    def _finalize_verification(self, state, now):
        """Fold the health check's outcome into the state. None, 'waiting' or 'done'."""
        pending = _read_json(self.pending_file)
        if pending is None:
            return None
        status = pending.get('status')
        old, new = pending.get('old_head'), pending.get('new_head')
        reason, detail = pending.get('reason'), pending.get('detail')

        if status in ('pending', 'verifying'):
            if now - float(pending.get('created_at') or now) < VERIFY_LOST_SECONDS:
                return 'waiting'
            outcome = 'lost'
            message = (f'LEDMatrix was updated to {_short(new)}, but its health check never reported back, '
                       'so it is not known whether the device is healthy. '
                       f'See "journalctl -u {VERIFY_UNIT}".')
        elif status == 'success':
            outcome = 'updated'
            message = (f'Updated LEDMatrix from {_short(old)} to {_short(new)}; '
                       'the services restarted and stayed healthy.')
        elif status == 'rolled_back':
            outcome = 'rolled_back'
            message = (f'The LEDMatrix update to {_short(new)} was rolled back to {_short(old)} because '
                       f'{reason or "it failed its health check"}.' + (f' Note: {detail}.' if detail else ''))
            state['rolled_back_head'] = new
        else:
            outcome = 'rollback_failed'
            message = (f'The LEDMatrix update to {_short(new)} failed ({reason or "unknown reason"}) and '  # nosec B608 - user-facing message, not SQL  # nosemgrep
                       f'could not be rolled back: {detail or "unknown error"}. The device may need '
                       f'attention: run "git reset --hard {old}" in the LEDMatrix folder, then restart '
                       'the display and web services.')

        result = state.setdefault('last_result', {})
        result.update({'core_outcome': outcome, 'core_message': message})
        if outcome not in ('updated', 'rolled_back'):
            # The device is in an unknown state; don't pile plugin changes on it.
            state['plugins_pending'] = False
            result['plugins_deferred'] = False
        self._apply_result(state, now)
        save_state(state, self.state_file)
        _unlink(self.pending_file)
        (logger.info if outcome == 'updated' else logger.error)("Automatic update: %s", message)
        return 'done'

    # -- plugins and reporting ----------------------------------------------

    def _update_plugins(self):
        if not self.store_manager:
            return [], []
        updated, failed = update_plugins(self.store_manager, self.operation_history)
        for plugin_id in updated:
            if self.schema_manager:
                self.schema_manager.invalidate_cache(plugin_id)
        if updated and self.plugin_manager:
            try:
                self.plugin_manager.discover_plugins()
            except Exception:
                logger.debug("discover_plugins after auto-update failed", exc_info=True)
        return updated, failed

    def _run_deferred_plugins(self, state, now):
        state['plugins_pending'] = False
        updated, failed = self._update_plugins()
        result = state.setdefault('last_result', {})
        result.update({'plugins_updated': updated, 'plugins_failed': failed, 'plugins_deferred': False})
        self._apply_result(state, now)
        save_state(state, self.state_file)
        if updated:
            self.restart('ledmatrix')

    @staticmethod
    def _apply_result(state, now):
        result = state.get('last_result') or {}
        outcome = result.get('core_outcome')
        failed = result.get('plugins_failed') or []
        if outcome in ERROR_OUTCOMES or failed:
            result['status'] = 'error'
        elif outcome == 'verifying':
            result['status'] = 'pending'
        else:
            result['status'] = 'success'

        if result['status'] == 'error':
            parts = []
            if outcome in ERROR_OUTCOMES:
                parts.append(result.get('core_message') or 'The LEDMatrix update failed.')
            if failed:
                parts.append('These plugins failed to update: ' + ', '.join(failed) + '.')
            state['alert'] = {'id': str(int(now * 1000)), 'message': ' '.join(parts)}
        elif result['status'] == 'success':
            state.pop('alert', None)

    # -- thread -------------------------------------------------------------

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, name='auto-update', daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _loop(self):
        if self._stop.wait(STARTUP_DELAY_SECONDS):
            return
        while True:
            try:
                self.tick()
            except Exception:
                logger.exception("Automatic update check failed")
            if self._stop.wait(CHECK_INTERVAL_SECONDS):
                return


def describe_status(config, state=None, state_file=STATE_FILE, pending_file=PENDING_FILE,
                    setup_file=SETUP_RESULT_FILE, helper=None):
    """What the General tab and the Overview banner show."""
    state = load_state(state_file) if state is None else state
    tz = config.get('timezone')
    fmt = '%b %d, %Y %I:%M %p'
    pending = _read_json(pending_file) or {}
    setup = _read_json(setup_file) or {}
    enabled = is_enabled(config)
    out = {
        'last_run': None, 'summary': None, 'status': None, 'next_due': None,
        'alert': None, 'alert_id': None,
        # Only asked while enabled: it runs systemctl, on every page load.
        'verifier_installed': (helper or helper_ready)() if enabled else None,
        'setup_status': setup.get('status'),
        'setup_message': setup.get('message'),
        'verifying': pending.get('status') in ('pending', 'verifying'),
    }
    if state.get('last_run'):
        out['last_run'] = _local_datetime(state['last_run'], tz).strftime(fmt)
        result = state.get('last_result') or {}
        out['status'] = result.get('status')
        parts = [result.get('core_message') or 'No result recorded.']
        if result.get('plugins_updated'):
            parts.append('Plugins updated: ' + ', '.join(result['plugins_updated']) + '.')
        if result.get('plugins_failed'):
            parts.append('Plugins that failed to update: ' + ', '.join(result['plugins_failed']) + '.')
        if result.get('plugins_deferred') and state.get('plugins_pending'):
            parts.append('Plugin updates run once the code update passes its health check.')
        out['summary'] = ' '.join(parts)
    if enabled and state.get('next_due'):
        out['next_due'] = _local_datetime(state['next_due'], tz).strftime(fmt)
    alert = state.get('alert')
    if alert and alert.get('id') != state.get('alert_dismissed'):
        out['alert'], out['alert_id'] = alert.get('message'), alert.get('id')
    return out
