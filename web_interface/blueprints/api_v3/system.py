"""Service control, updates, versions and system status.

Routes decorate the shared `api_v3` Blueprint from ._common, so their
endpoint names are unchanged by living here.
"""
from web_interface.blueprints.api_v3 import (
    Any, Dict, PROJECT_ROOT, Path, _GIT, _UPDATE_CHECK_TTL,
    _describe_git_failure, _get_display_service_status, _git_current_branch,
    _git_remote_branch_exists, _git_upstream, _pip_install_requirements,
    _scrub_git_remote_url, _truncate_output, _update_check_cache,
    _update_check_failed, api_v3, checkout_branch, describe_exception,
    get_git_version, jsonify, logger, os, request, resolve_pull_command,
    shutil, subprocess,
)
import web_interface.blueprints.api_v3 as _pkg
# Read through the module rather than bound by value: tests patch these
# as module attributes, and a value binding would not see the patch.
# Several are also called from helpers that live in __init__, so the
# package is the only patch point that covers every caller.


@api_v3.route('/system/status', methods=['GET'])
def get_system_status():
    """Get system status"""
    try:
        # Check cache first (10 second TTL for system status)
        try:
            from web_interface.cache import get_cached, set_cached
            cached_result = get_cached('system_status', ttl_seconds=10)
            if cached_result is not None:
                return jsonify({'status': 'success', 'data': cached_result})
        except ImportError:
            # Cache not available, continue without caching
            get_cached = None
            set_cached = None

        # Import psutil for system monitoring
        try:
            import psutil
        except ImportError:
            # Fallback if psutil not available
            return jsonify({
                'status': 'error',
                'message': 'psutil not available for system monitoring'
            }), 503

        # Get system metrics using psutil
        cpu_percent = psutil.cpu_percent(interval=0.1)  # Short interval for responsiveness
        memory = psutil.virtual_memory()
        memory_percent = memory.percent
        disk = psutil.disk_usage('/')
        disk_percent = disk.percent

        # Calculate uptime
        boot_time = psutil.boot_time()
        uptime_seconds = _pkg.time.time() - boot_time
        uptime_hours = uptime_seconds / 3600
        uptime_days = uptime_hours / 24

        # Format uptime string
        if uptime_days >= 1:
            uptime_str = f"{int(uptime_days)}d {int(uptime_hours % 24)}h"
        elif uptime_hours >= 1:
            uptime_str = f"{int(uptime_hours)}h {int((uptime_seconds % 3600) / 60)}m"
        else:
            uptime_str = f"{int(uptime_seconds / 60)}m"

        # Get CPU temperature (Raspberry Pi)
        cpu_temp = None
        try:
            temp_file = '/sys/class/thermal/thermal_zone0/temp'
            if os.path.exists(temp_file):
                with open(temp_file, 'r') as f:
                    temp_millidegrees = int(f.read().strip())
                    cpu_temp = temp_millidegrees / 1000.0  # Convert to Celsius
        except (IOError, ValueError, OSError):
            # Temperature sensor not available or error reading
            cpu_temp = None

        # Get display service status
        service_status = _get_display_service_status()

        status = {
            'timestamp': _pkg.time.time(),
            'uptime': uptime_str,
            'uptime_seconds': int(uptime_seconds),
            'service_active': service_status.get('active', False),
            'cpu_percent': round(cpu_percent, 1),
            'memory_used_percent': round(memory_percent, 1),
            'memory_total_mb': round(memory.total / (1024 * 1024), 1),
            'memory_used_mb': round(memory.used / (1024 * 1024), 1),
            # MemAvailable, not total-minus-used: it accounts for reclaimable
            # page cache, so it is what actually predicts memory trouble. A
            # board can read 70% "used" and be fine, or read the same and be
            # about to fail fork(), and only this number tells them apart.
            'memory_available_mb': round(memory.available / (1024 * 1024), 1),
            'cpu_temp': round(cpu_temp, 1) if cpu_temp is not None else None,
            'disk_used_percent': round(disk_percent, 1),
            'disk_total_gb': round(disk.total / (1024 * 1024 * 1024), 1),
            'disk_used_gb': round(disk.used / (1024 * 1024 * 1024), 1)
        }

        # Cache the result if available
        if set_cached:
            try:
                set_cached('system_status', status, ttl_seconds=10)
            except Exception:
                pass  # Cache write failed, but continue

        return jsonify({'status': 'success', 'data': status})
    except Exception as e:
        logger.error('Unhandled exception', exc_info=True)
        return jsonify({'status': 'error', 'message': 'An error occurred; see logs for details', 'details': describe_exception(e)}), 500
@api_v3.route('/system/version', methods=['GET'])
def get_system_version():
    """Get LEDMatrix repository version"""
    try:
        version = get_git_version()
        return jsonify({'status': 'success', 'data': {'version': version}})
    except Exception as e:
        logger.error("get_system_version failed: %s", e, exc_info=True)
        return jsonify({'status': 'error', 'message': 'Unable to retrieve version'}), 500
@api_v3.route('/system/check-update', methods=['GET'])
def check_for_update():
    """Check whether a newer LEDMatrix commit is available on origin/main."""
    now = _pkg.time.time()
    if _update_check_cache['result'] and now - _update_check_cache['ts'] < _UPDATE_CHECK_TTL:
        return jsonify(_update_check_cache['result'])

    _safe: Dict[str, Any] = {'update_available': False, 'remote_sha': 'unknown', 'commits_behind': 0}
    try:
        cwd = str(PROJECT_ROOT)
        fetch_result = subprocess.run(
            ['git', 'fetch', 'origin', 'main', '--quiet'],
            capture_output=True, timeout=10, cwd=cwd,
        )
        if fetch_result.returncode != 0:
            stderr = fetch_result.stderr.decode(errors='replace').strip()
            logger.warning("check-update: git fetch failed (rc=%d): %s",
                           fetch_result.returncode, stderr)
            failed = _update_check_failed(_describe_git_failure(stderr))
            _update_check_cache['result'] = failed
            _update_check_cache['ts'] = now
            return jsonify(failed)
        local = subprocess.run(
            ['git', 'rev-parse', 'HEAD'],
            capture_output=True, text=True, timeout=5, cwd=cwd,
        ).stdout.strip()
        remote = subprocess.run(
            ['git', 'rev-parse', 'origin/main'],
            capture_output=True, text=True, timeout=5, cwd=cwd,
        ).stdout.strip()

        if not local or not remote:
            return jsonify(_safe)

        if local == remote:
            result: Dict[str, Any] = {'update_available': False, 'remote_sha': remote, 'commits_behind': 0}
        else:
            count_str = subprocess.run(
                ['git', 'rev-list', 'HEAD..origin/main', '--count'],
                capture_output=True, text=True, timeout=5, cwd=cwd,
            ).stdout.strip()
            count = int(count_str) if count_str.isdigit() else 0
            result = {'update_available': count > 0, 'remote_sha': remote, 'commits_behind': count}

        _update_check_cache['result'] = result
        _update_check_cache['ts'] = now
        return jsonify(result)
    except Exception as e:
        logger.warning("check-update failed: %s", e)
        return jsonify(_update_check_failed(
            "Could not check for updates; see logs for details."))
#: sudo's own wording when it needs a password it cannot ask for. The web
#: interface runs unprivileged, so its systemctl/reboot/journalctl calls only
#: work once scripts/install/configure_web_sudo.sh has granted NOPASSWD --
#: which first_time_install.sh does not do. That makes this the common case on
#: a fresh device, and "Action failed; see logs for details" named none of it,
#: while the log viewer was broken for the very same reason.
_SUDO_NEEDS_PASSWORD = (
    'a password is required',
    'no tty present',
    'a terminal is required',
)

_SUDO_HINT = (
    'Passwordless sudo is not configured for the web interface user, so this '
    'action cannot run. Run scripts/install/configure_web_sudo.sh as that user, '
    'then retry.'
)


def _sudo_hint_for(text):
    """An actionable hint when `text` is sudo refusing to prompt, else None."""
    lowered = (text or '').lower()
    if any(marker in lowered for marker in _SUDO_NEEDS_PASSWORD):
        return _SUDO_HINT
    return None


@api_v3.route('/system/action', methods=['POST'])
def execute_system_action():
    """Execute system actions (start/stop/reboot/etc)"""
    try:
        # HTMX sends data as form data, not JSON
        data = request.get_json(silent=True) or {}
        if not data:
            # Try to get from form data if JSON fails
            data = {
                'action': request.form.get('action'),
                'mode': request.form.get('mode')
            }

        if not data or 'action' not in data:
            return jsonify({'status': 'error', 'message': 'Action required'}), 400

        action = data['action']
        mode = data.get('mode')  # For on-demand modes

        # Map actions to subprocess calls (similar to original implementation)
        if action == 'start_display':
            if mode:
                # For on-demand modes, we would need to integrate with the display controller
                # For now, just start the display service
                try:
                    result = subprocess.run(['sudo', 'systemctl', 'start', 'ledmatrix.service'],
                                         capture_output=True, text=True, timeout=10)
                except subprocess.TimeoutExpired as e:
                    logger.error("start_display (%s) timed out: %s", mode, e)
                    return jsonify({'status': 'error', 'message': 'Command timed out', 'returncode': -1, 'stderr': 'timeout'})
                logger.info("start_display (%s) returned code %d", mode, result.returncode)
                if result.returncode != 0 and result.stderr:
                    logger.error("start_display (%s) stderr: %s", mode, result.stderr.strip())
                resp = {
                    'status': 'success' if result.returncode == 0 else 'error',
                    # This branch returns before the shared nonzero-result
                    # response below, so it needs the hint of its own or an
                    # on-demand start reports "Failed to start display" and
                    # says nothing about the sudo that actually refused it.
                    'message': (
                        'Display started' if result.returncode == 0
                        else _sudo_hint_for(result.stderr) or 'Failed to start display'
                    ),
                }
                if result.returncode != 0:
                    resp['returncode'] = result.returncode
                    resp['stderr'] = result.stderr.strip()
                return jsonify(resp)
            else:
                result = subprocess.run(['sudo', 'systemctl', 'start', 'ledmatrix.service'],
                                     capture_output=True, text=True, timeout=10)
        elif action == 'stop_display':
            result = subprocess.run(['sudo', 'systemctl', 'stop', 'ledmatrix.service'],
                                 capture_output=True, text=True, timeout=10)
        elif action == 'enable_autostart':
            result = subprocess.run(['sudo', 'systemctl', 'enable', 'ledmatrix.service'],
                                 capture_output=True, text=True, timeout=10)
        elif action == 'disable_autostart':
            result = subprocess.run(['sudo', 'systemctl', 'disable', 'ledmatrix.service'],
                                 capture_output=True, text=True, timeout=10)
        elif action == 'reboot_system':
            result = subprocess.run(['sudo', 'reboot'],
                                 capture_output=True, text=True, timeout=10)
        elif action == 'shutdown_system':
            result = subprocess.run(['sudo', 'poweroff'],
                                 capture_output=True, text=True, timeout=10)
        elif action == 'git_pull':
            # Use PROJECT_ROOT instead of hardcoded path
            project_dir = str(PROJECT_ROOT)

            # Decide how to pull BEFORE stashing. If this checkout cannot be
            # updated at all, stashing first would put the user's local changes
            # away for an update that was never going to run.
            pull_args, upstream_note, pull_error = resolve_pull_command(project_dir)
            if pull_error:
                logger.warning("git pull not attempted: %s", pull_error)
                return jsonify({'status': 'error', 'message': pull_error})

            # Check if there are local changes that need to be stashed
            # Exclude plugins directory - plugins are separate repos and shouldn't be stashed with base project
            # Use --untracked-files=no to skip untracked files check (much faster with symlinked plugins)
            try:
                status_result = subprocess.run(
                    ['git', 'status', '--porcelain', '--untracked-files=no'],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    cwd=project_dir
                )
                # Filter out any changes in plugins directory - plugins are separate repositories
                # Git status format: XY filename (where X is status of index, Y is status of work tree)
                status_lines = [line for line in status_result.stdout.strip().split('\n')
                               if line.strip() and 'plugins/' not in line]
                has_changes = bool('\n'.join(status_lines).strip())
            except subprocess.TimeoutExpired:
                # If status check times out, assume there might be changes and proceed
                # This is safer than failing the update
                has_changes = True
                status_result = type('obj', (object,), {'stdout': '', 'stderr': 'Status check timed out'})()

            stash_info = ""

            # Stash local changes if they exist (excluding plugins)
            # Plugins are separate repositories and shouldn't be stashed with base project updates
            if has_changes:
                try:
                    # Use pathspec to exclude plugins directory from stash
                    stash_result = subprocess.run(
                        ['git', 'stash', 'push', '-m', 'LEDMatrix auto-stash before update', '--', ':!plugins'],
                        capture_output=True,
                        text=True,
                        timeout=30,
                        cwd=project_dir
                    )
                    if stash_result.returncode == 0:
                        logger.debug("git stash: stashed local changes before pull")
                        stash_info = " Local changes were stashed."
                    else:
                        logger.warning("git stash failed before pull (returncode=%d)", stash_result.returncode)
                except subprocess.TimeoutExpired:
                    logger.warning("git stash timed out, proceeding with pull")

            # Record HEAD before the pull so dependency changes can be detected
            old_head = None
            try:
                _pre = subprocess.run(['git', 'rev-parse', 'HEAD'],
                                      capture_output=True, text=True, timeout=10, cwd=project_dir)
                if _pre.returncode == 0:
                    old_head = _pre.stdout.strip()
            except subprocess.TimeoutExpired:
                logger.warning("git rev-parse timed out before pull")

            # Whether the pull actually brought new code in. "Already up to
            # date" is a success too, and prompting for a restart then would
            # train users to ignore the prompt.
            code_changed = False

            # Perform the git pull. Branches without an upstream were given
            # an explicit "origin <branch>" above so the update still works.
            result = subprocess.run(
                pull_args,
                capture_output=True,
                text=True,
                timeout=60,
                cwd=project_dir
            )

            # Give the branch tracking information so the next pull is a plain
            # `git pull` — otherwise every update repeats the fallback.
            if result.returncode == 0 and upstream_note:
                branch = _git_current_branch(project_dir)
                if branch:
                    try:
                        subprocess.run(
                            ['git', 'branch', f'--set-upstream-to=origin/{branch}', branch],
                            capture_output=True, text=True, timeout=10, cwd=project_dir)
                    except (subprocess.TimeoutExpired, OSError) as exc:
                        logger.debug("could not set upstream for %s: %s", branch, exc)

            # Return custom response for git_pull
            if result.returncode == 0:
                pull_message = "Code updated successfully."
                if has_changes:
                    pull_message = f"Code updated successfully. Local changes were automatically stashed.{stash_info}"
                if result.stdout and "Already up to date" not in result.stdout:
                    pull_message = f"Code updated successfully.{stash_info}"
                if upstream_note:
                    pull_message = f"{pull_message} {upstream_note}"

                # Keep Python dependencies in sync automatically: if the pull
                # changed a requirements file, install it now — users updating
                # from the web UI (most of them) never SSH in to pip install.
                # Installs go through the same root-visible path as the
                # Tools-tab buttons (_pip_install_requirements).
                dep_notes = []
                try:
                    _post = subprocess.run(['git', 'rev-parse', 'HEAD'],
                                           capture_output=True, text=True, timeout=10, cwd=project_dir)
                    new_head = _post.stdout.strip() if _post.returncode == 0 else None
                    if old_head and new_head and old_head != new_head:
                        code_changed = True
                        diff = subprocess.run(
                            ['git', 'diff', '--name-only', f'{old_head}..{new_head}'],
                            capture_output=True, text=True, timeout=15, cwd=project_dir)
                        changed = set(diff.stdout.split()) if diff.returncode == 0 else set()
                        for rel in ('requirements.txt', 'web_interface/requirements.txt'):
                            req_path = PROJECT_ROOT / rel
                            if rel not in changed or not req_path.exists():
                                continue
                            # Each file's install is isolated: a timeout or
                            # OSError (e.g. the sudo wrapper/interpreter
                            # missing) on one file must not abort the other.
                            try:
                                r = _pip_install_requirements(req_path, timeout=180)
                                if r.returncode == 0:
                                    dep_notes.append(f"Dependencies from {rel} updated.")
                                else:
                                    dep_notes.append(
                                        f"Dependency install from {rel} failed — "
                                        "run Install Base Requirements from the Tools tab.")
                                    logger.warning("post-update pip install failed for %s: %s",
                                                   rel, _truncate_output(r.stdout, r.stderr))
                            except subprocess.TimeoutExpired:
                                dep_notes.append(
                                    f"Dependency install from {rel} timed out — "
                                    "run Install Base Requirements from the Tools tab.")
                                logger.warning("post-update pip install timed out for %s", rel)
                            except OSError as install_err:
                                dep_notes.append(
                                    f"Dependency install from {rel} failed — "
                                    "run Install Base Requirements from the Tools tab.")
                                logger.warning("post-update pip install errored for %s: %s",
                                               rel, install_err)
                except subprocess.TimeoutExpired:
                    logger.warning("post-update dependency sync timed out")
                if dep_notes:
                    pull_message += " " + " ".join(dep_notes)
                # A `git pull` restores built-in plugins (committed under
                # plugin-repos/) even if the user uninstalled them. Re-remove
                # any the user previously uninstalled so the update doesn't
                # resurrect them.
                if api_v3.plugin_store_manager:
                    try:
                        purged = api_v3.plugin_store_manager.purge_uninstalled_plugins()
                        if purged:
                            logger.info(
                                "Re-removed %d uninstalled plugin(s) restored by update: %s",
                                len(purged), ", ".join(purged),
                            )
                    except (OSError, RuntimeError) as purge_err:
                        logger.warning("Post-update plugin purge failed: %s", purge_err)
            else:
                logger.warning("git pull failed (returncode=%d): %s", result.returncode, result.stderr)
                # Show git's own first line: "check logs" leaves the user with
                # nothing to act on, and these failures are usually actionable
                # (conflicting local commits, no upstream, network).
                detail = next((ln.strip() for ln in (result.stderr or '').splitlines()
                               if ln.strip()), '')
                pull_message = f"Update failed: {detail}" if detail else "Update failed; check logs for details"

            # Nothing here restarts anything: the pull replaces files on
            # disk while the display and web services keep running the code
            # they loaded at boot. Without this the user is told the update
            # succeeded and sees no change until they happen to reboot.
            return jsonify({
                'status': 'success' if result.returncode == 0 else 'error',
                'message': pull_message,
                'restart_required': bool(result.returncode == 0 and code_changed),
            })
        elif action == 'checkout_branch':
            # Switch branches from the Tools tab. Needed because a checkout
            # that predates tracking (or a restored backup) can leave the pi
            # on a branch the update button cannot pull.
            result_payload, http_status = checkout_branch(
                str(PROJECT_ROOT), data.get('branch') or '', stash=bool(data.get('stash')))
            return jsonify(result_payload), http_status

        elif action == 'restart_display_service':
            result = subprocess.run(['sudo', 'systemctl', 'restart', 'ledmatrix.service'],
                                 capture_output=True, text=True, timeout=10)
        elif action == 'restart_web_service':
            # Try to restart the web service (assuming it's ledmatrix-web.service)
            result = subprocess.run(['sudo', 'systemctl', 'restart', 'ledmatrix-web.service'],
                                 capture_output=True, text=True, timeout=10)
        elif action == 'install_base_requirements':
            # Base + web interface requirements: flask-compress and friends
            # live in web_interface/requirements.txt, not the root file.
            req_files = [f for f in (PROJECT_ROOT / 'requirements.txt',
                                     PROJECT_ROOT / 'web_interface' / 'requirements.txt')
                         if f.exists()]
            if not req_files:
                return jsonify({'status': 'error', 'message': 'No requirements.txt found at project root'})
            outputs = []
            all_ok = True
            for req_file in req_files:
                label = req_file.relative_to(PROJECT_ROOT)
                # Isolate each file's install: a timeout or OSError on one
                # (e.g. requirements.txt) must not abort the rest of the
                # loop (e.g. web_interface/requirements.txt never attempted).
                try:
                    result = _pip_install_requirements(req_file, timeout=120)
                    all_ok = all_ok and result.returncode == 0
                    outputs.append(f"== {label} ==\n" + _truncate_output(result.stdout, result.stderr))
                except subprocess.TimeoutExpired:
                    all_ok = False
                    outputs.append(f"== {label} ==\nTimed out after 120s")
                    logger.warning("install_base_requirements timed out for %s", label)
                except OSError as install_err:
                    all_ok = False
                    outputs.append(f"== {label} ==\nFailed: {install_err}")
                    logger.warning("install_base_requirements errored for %s: %s", label, install_err)
            return jsonify({
                'status': 'success' if all_ok else 'error',
                'message': 'Base requirements installed successfully' if all_ok else 'pip install failed',
                'output': "\n".join(outputs)
            })
        elif action == 'install_plugin_requirements':
            active_pm = getattr(api_v3, 'plugin_manager', None)
            if active_pm:
                plugins_dir = Path(active_pm.plugins_dir)
            else:
                _cm = getattr(api_v3, 'config_manager', None)
                _cfg = _cm.load_config() if _cm else {}
                _dir_name = _cfg.get('plugin_system', {}).get('plugins_directory', 'plugin-repos')
                plugins_dir = Path(_dir_name) if os.path.isabs(_dir_name) else PROJECT_ROOT / _dir_name
            results = []
            if plugins_dir.exists():
                for p in sorted(plugins_dir.iterdir()):
                    req = p / 'requirements.txt'
                    if p.is_dir() and req.exists():
                        try:
                            r = _pip_install_requirements(req, timeout=60)
                            results.append({
                                'plugin': p.name,
                                'ok': r.returncode == 0,
                                'output': _truncate_output(r.stdout, r.stderr)
                            })
                        except subprocess.TimeoutExpired:
                            results.append({'plugin': p.name, 'ok': False, 'output': 'pip install timed out'})
                        except OSError as exc:
                            results.append({'plugin': p.name, 'ok': False, 'output': exc.strerror or 'OS error'})
            ok_count = sum(1 for r in results if r['ok'])
            all_ok = all(r['ok'] for r in results) if results else True
            return jsonify({
                'status': 'success' if all_ok else 'error',
                'message': f'Processed {len(results)} plugin(s) — {ok_count} succeeded' if results else 'No plugin requirements.txt files found',
                'details': results
            })
        elif action == 'force_git_reset':
            if not _GIT:
                return jsonify({'status': 'error', 'message': 'git not found on this system'}), 503
            project_dir = str(PROJECT_ROOT)
            fetch = subprocess.run(
                [_GIT, 'fetch', 'origin'],
                capture_output=True, text=True, timeout=30, cwd=project_dir
            )
            if fetch.returncode != 0:
                return jsonify({'status': 'error', 'message': 'git fetch failed', 'output': fetch.stderr.strip()})
            reset = subprocess.run(
                [_GIT, 'reset', '--hard', 'origin/main'],
                capture_output=True, text=True, timeout=30, cwd=project_dir
            )
            return jsonify({
                'status': 'success' if reset.returncode == 0 else 'error',
                'message': 'Reset to origin/main successfully' if reset.returncode == 0 else 'git reset failed',
                'output': (reset.stdout + reset.stderr).strip()
            })
        elif action == 'clear_pycache':
            cleared = 0
            failed = 0
            for d in PROJECT_ROOT.rglob('__pycache__'):
                if d.is_dir():
                    try:
                        shutil.rmtree(d)
                        cleared += 1
                    except OSError:
                        failed += 1
            msg = f'Cleared {cleared} __pycache__ directories'
            if failed:
                msg += f' ({failed} could not be removed)'
            return jsonify({'status': 'success', 'message': msg})
        else:
            return jsonify({'status': 'error', 'message': 'Unknown action'}), 400

        logger.info("system action '%s' returncode=%d", action, result.returncode)
        if result.returncode != 0 and result.stderr:
            logger.error("system action '%s' stderr: %s", action, result.stderr.strip())
        resp = {
            'status': 'success' if result.returncode == 0 else 'error',
            'message': 'Action completed' if result.returncode == 0 else 'Action failed; check logs for details',
        }
        if result.returncode != 0:
            resp['returncode'] = result.returncode
            resp['stderr'] = result.stderr.strip()
            hint = _sudo_hint_for(result.stderr)
            if hint:
                resp['message'] = hint
        return jsonify(resp)

    except subprocess.TimeoutExpired as e:
        logger.error("system action '%s' timed out: %s", action, e)
        return jsonify({'status': 'error', 'message': 'Command timed out', 'returncode': -1, 'stderr': 'timeout'})
    except Exception as e:
        logger.error("execute_system_action failed: %s", e, exc_info=True)
        detail = describe_exception(e)
        resp = {
            'status': 'error',
            'message': _sudo_hint_for(detail) or 'Action failed; see logs for details',
            'details': detail,
        }
        return jsonify(resp), 500
@api_v3.route('/system/git-info', methods=['GET'])
def get_git_info():
    """Return branch, dirty state, recent commits and remote URL for the Tools tab."""
    if not _GIT:
        return jsonify({'status': 'error', 'message': 'git not found on this system'}), 503
    d = str(PROJECT_ROOT)
    try:
        branch = subprocess.run([_GIT, 'branch', '--show-current'], capture_output=True, text=True, timeout=10, cwd=d)
        if branch.returncode != 0:
            return jsonify({'status': 'error', 'message': f'git branch failed: {branch.stderr.strip()}'}), 500

        status = subprocess.run([_GIT, 'status', '--short', '--untracked-files=no'], capture_output=True, text=True, timeout=15, cwd=d)
        if status.returncode != 0:
            return jsonify({'status': 'error', 'message': f'git status failed: {status.stderr.strip()}'}), 500

        log    = subprocess.run([_GIT, 'log', '--oneline', '-5'], capture_output=True, text=True, timeout=10, cwd=d)
        remote = subprocess.run([_GIT, 'remote', 'get-url', 'origin'], capture_output=True, text=True, timeout=10, cwd=d)
        branch_name = branch.stdout.strip()
        upstream = _git_upstream(d)
        return jsonify({
            'branch': branch_name,
            'dirty': bool(status.stdout.strip()),
            'status': status.stdout.strip(),
            'recent_commits': log.stdout.strip() if log.returncode == 0 else '',
            'remote_url': _scrub_git_remote_url(remote.stdout.strip()) if remote.returncode == 0 else '',
            # Surfaced so the Tools tab can warn before the user clicks Pull
            # Latest, rather than after it fails.
            'upstream': upstream,
            'can_pull': bool(upstream) or _git_remote_branch_exists(d, branch_name),
        })
    except Exception as e:
        logger.error("get_git_info failed: %s", e, exc_info=True)
        return jsonify({'status': 'error', 'message': 'Failed to get git info'}), 500
@api_v3.route('/system/git-branches', methods=['GET'])
def get_git_branches():
    """List branches available to switch to, for the Tools tab picker."""
    if not _GIT:
        return jsonify({'status': 'error', 'message': 'git not found on this system'}), 503
    d = str(PROJECT_ROOT)
    try:
        # Refresh remote refs so a branch created since the last fetch shows up.
        subprocess.run([_GIT, 'fetch', 'origin', '--prune'],
                       capture_output=True, text=True, timeout=60, cwd=d)

        local = subprocess.run([_GIT, 'for-each-ref', '--format=%(refname:short)', 'refs/heads'],
                               capture_output=True, text=True, timeout=15, cwd=d)
        remote = subprocess.run([_GIT, 'for-each-ref', '--format=%(refname:short)', 'refs/remotes/origin'],
                                capture_output=True, text=True, timeout=15, cwd=d)
        if local.returncode != 0:
            return jsonify({'status': 'error', 'message': 'Could not list branches'}), 500

        local_names = [b for b in local.stdout.split() if b]
        remote_names = []
        for ref in remote.stdout.split() if remote.returncode == 0 else []:
            name = ref.split('origin/', 1)[-1]
            # origin/HEAD is a symbolic alias, not a branch a user can pick.
            if name and name != 'HEAD' and name not in local_names:
                remote_names.append(name)

        return jsonify({
            'status': 'success',
            'current': _git_current_branch(d),
            'upstream': _git_upstream(d),
            'local': sorted(local_names),
            'remote_only': sorted(remote_names),
        })
    except subprocess.TimeoutExpired:
        return jsonify({'status': 'error', 'message': 'Timed out talking to the remote'}), 504
    except OSError as e:
        logger.error("get_git_branches failed: %s", e, exc_info=True)
        return jsonify({'status': 'error', 'message': 'Failed to list branches'}), 500
