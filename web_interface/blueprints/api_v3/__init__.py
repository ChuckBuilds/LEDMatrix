"""The /api/v3 blueprint.

This was one 10,469-line module. It is now a package, but deliberately not a
set of *separate* blueprints: every route module beside this one decorates the
single `api_v3` object defined here, so endpoint names stay `api_v3.<function>`
and the URL map is unchanged. web_interface/app.py registers it as before.

The shared imports, constants and helpers stay in this file rather than moving
to a _common submodule, because tests monkeypatch some of them by module
attribute -- `monkeypatch.setattr(api_v3_module, "_BACKUP_EXPORT_DIR", ...)`.
Keeping them here means that keeps working exactly as it did. Route modules
read the mutable ones back through this module (see _pkg below) rather than
binding them by value, for the same reason.

The route modules are imported at the *bottom*: they import names from here, so
everything they need has to exist first.
"""
from flask import Blueprint, request, jsonify, Response
import contextlib
import json
import os
import re
import stat
import sys
import shutil
import subprocess
import tempfile
import time
import hashlib
import uuid
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, Type
from urllib.parse import urlparse, urlunparse
logger = logging.getLogger(__name__)
# Import new infrastructure
from src.web_interface.api_helpers import success_response, error_response, validate_request_json
from src.web_interface.errors import ErrorCode
from src.web_interface.secret_helpers import (find_secret_fields, mask_all_secret_values,
                                              merge_secrets, remove_empty_secrets,
                                              separate_secrets,
                                              strip_masked_values)
from src.web_interface.error_handler import describe_exception, redact_text
from src.plugin_system.operation_types import OperationType
from src.web_interface.validators import (
    validate_file_upload
)
from src.error_aggregator import get_error_aggregator
from src.common.permission_utils import install_requirements_file
_SUDO = shutil.which('sudo')
_JOURNALCTL = shutil.which('journalctl')
_GIT = shutil.which('git')
# Cap subprocess output returned to the browser — pip can produce MBs on build failures.
_MAX_OUTPUT_BYTES = 51_200  # 50 KB
def _truncate_output(stdout: str, stderr: str) -> str:
    """Combine stdout+stderr and truncate to _MAX_OUTPUT_BYTES (keeping the tail)."""
    combined = (stdout + stderr).strip()
    if len(combined) > _MAX_OUTPUT_BYTES:
        combined = '[...output truncated...]\n' + combined[-_MAX_OUTPUT_BYTES:]
    return combined
def _pip_install_requirements(req_file: Path, timeout: int) -> subprocess.CompletedProcess:
    """Install a requirements.txt file, preferring the vetted sudo wrapper so
    the packages are visible to root-run ledmatrix.service — not just to
    whichever non-root user runs this web process. Falls back to installing
    for the current process only if the wrapper isn't set up yet (i.e. the
    admin hasn't run scripts/install/configure_web_sudo.sh since upgrading),
    so the button still does *something* useful rather than hard-failing.

    Thin wrapper around the shared implementation in permission_utils so the
    Plugin Store's own dependency installation (store_manager.py) follows the
    exact same root-visible install path instead of a divergent one.
    """
    return install_requirements_file(req_file, timeout=timeout)
def _scrub_git_remote_url(url: str) -> str:
    """Strip embedded username/password from an HTTPS remote URL before returning it to the UI."""
    try:
        p = urlparse(url)
        if p.scheme in ('http', 'https') and (p.username or p.password):
            netloc = p.hostname or ''
            if p.port:
                netloc += f':{p.port}'
            return urlunparse(p._replace(netloc=netloc))
    except Exception:
        pass
    return url
# Will be initialized when blueprint is registered
# NOTE: the managers live on the blueprint object (app.py sets
# api_v3.config_manager / api_v3.plugin_manager). Deliberately not
# mirrored as module globals: a bare `config_manager` used to resolve to
# a None that was never assigned, which silently disabled the /health
# checks and made /display/current fall back to a hardcoded 128x64.
plugin_store_manager = None
saved_repositories_manager = None
cache_manager = None
schema_manager = None
operation_queue = None
plugin_state_manager = None
operation_history = None
sync_manager = None  # Optional DisplaySyncManager instance (set by app.py if available)
# Get project root directory (web_interface/../..)
# web_interface/blueprints/api_v3/_common.py -> up four to the project root.
# This was three levels when everything lived in web_interface/blueprints/api_v3.py;
# the split moved the file one directory deeper and silently pointed PROJECT_ROOT
# at web_interface/ instead. Nothing failed at import -- it surfaced as routes
# 404ing and "installation script not found", because every path built from it
# was wrong. Asserted in test_api_v3_url_map.py so the next move cannot repeat it.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
# System fonts that cannot be deleted (used by catalog API and delete endpoint)
SYSTEM_FONTS = frozenset([
    'pressstart2p-regular', 'pressstart2p',
    '4x6-font', '4x6',
    '5by7.regular', '5by7', '5x7',
    '5x8', '6x9', '6x10', '6x12', '6x13', '6x13b', '6x13o',
    '7x13', '7x13b', '7x13o', '7x14', '7x14b',
    '8x13', '8x13b', '8x13o',
    '9x15', '9x15b', '9x18', '9x18b',
    '10x20',
    'matrixchunky8', 'matrixlight6', 'tom-thumb',
    'clr6x12', 'helvr12', 'texgyre-27'
])
api_v3 = Blueprint('api_v3', __name__)
def _get_plugin_version(plugin_id: str) -> str:
    """Read the installed version from a plugin's manifest.json.

    Returns the version string on success, or '' if the manifest
    cannot be read (missing, corrupt, permission denied, etc.).
    """
    manifest_path = Path(api_v3.plugin_store_manager.plugins_dir) / plugin_id / "manifest.json"
    try:
        with open(manifest_path, 'r', encoding='utf-8') as f:
            manifest = json.load(f)
        return manifest.get('version', '')
    except (FileNotFoundError, PermissionError, OSError) as e:
        logger.warning("[PluginVersion] Could not read manifest for %s at %s: %s", plugin_id, manifest_path, e)
    except json.JSONDecodeError as e:
        logger.warning("[PluginVersion] Invalid JSON in manifest for %s at %s: %s", plugin_id, manifest_path, e)
    return ''
def _is_plugin_update_available(installed_version: str, latest_version: str) -> bool:
    """Return True when the registry's ``latest_version`` is strictly newer
    than the installed version.

    Thin alias for the shared comparator in
    `src.plugin_system.compatibility.is_update_available` — the store's
    `update_plugin` uses the same function, so the UI badge and the actual
    reinstall decision can never disagree.
    """
    from src.plugin_system.compatibility import is_update_available
    return is_update_available(installed_version, latest_version)
def _ensure_cache_manager():
    """Ensure cache manager is initialized."""
    global cache_manager
    if cache_manager is None:
        from src.cache_manager import CacheManager
        cache_manager = CacheManager()
    return cache_manager
def _save_config_atomic(config_manager, config_data, create_backup=True):
    """
    Save configuration using atomic save if available, fallback to regular save.

    Returns:
        tuple: (success: bool, error_message: str or None)
    """
    if hasattr(config_manager, 'save_config_atomic'):
        result = config_manager.save_config_atomic(config_data, create_backup=create_backup)
        if result.status.value != 'success':
            return False, result.message
        return True, None
    else:
        try:
            config_manager.save_config(config_data)
            return True, None
        except Exception as e:
            return False, str(e)
def _coerce_to_bool(value):
    """
    Coerce a form value to a proper Python boolean.

    HTML checkboxes send string values like "true", "on", "1" when checked.
    This ensures we store actual booleans in config JSON, not strings.

    Args:
        value: The form value (string, bool, int, or None)

    Returns:
        bool: True if value represents a truthy checkbox state, False otherwise
    """
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value == 1
    if isinstance(value, str):
        return value.lower() in ('true', 'on', '1', 'yes')
    return False
def _get_display_service_status():
    """Return status information about the ledmatrix service."""
    try:
        result = subprocess.run(
            ['systemctl', 'is-active', 'ledmatrix'],
            capture_output=True,
            text=True,
            timeout=3
        )
        return {
            'active': result.stdout.strip() == 'active',
            'returncode': result.returncode,
            'stdout': result.stdout.strip(),
            'stderr': result.stderr.strip()
        }
    except subprocess.TimeoutExpired:
        return {
            'active': False,
            'returncode': -1,
            'stdout': '',
            'stderr': 'timeout'
        }
    except Exception as err:
        return {
            'active': False,
            'returncode': -1,
            'stdout': '',
            'stderr': str(err)
        }
def _run_systemctl_command(args):
    """Run a systemctl command safely."""
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=15
        )
        return {
            'returncode': result.returncode,
            'stdout': result.stdout,
            'stderr': result.stderr
        }
    except subprocess.TimeoutExpired:
        return {
            'returncode': -1,
            'stdout': '',
            'stderr': 'timeout'
        }
    except Exception as err:
        return {
            'returncode': -1,
            'stdout': '',
            'stderr': str(err)
        }
def _ensure_display_service_running():
    """Ensure the ledmatrix display service is running."""
    status = _get_display_service_status()
    if status.get('active'):
        status['started'] = False
        return status
    result = _run_systemctl_command(['sudo', 'systemctl', 'start', 'ledmatrix.service'])
    service_status = _get_display_service_status()
    result['started'] = result.get('returncode') == 0
    result['active'] = service_status.get('active')
    result['status'] = service_status
    return result
def _stop_display_service():
    """Stop the ledmatrix display service."""
    result = _run_systemctl_command(['sudo', 'systemctl', 'stop', 'ledmatrix.service'])
    status = _get_display_service_status()
    result['active'] = status.get('active')
    result['status'] = status
    return result
#: Field names whose value is a credential. Matched by name because this
#: endpoint returns the whole config, core keys included, and core config has
#: no schema to carry x-secret markers.
_CREDENTIAL_NAME_PARTS = ("password", "passwd", "secret", "token", "api_key",
                          "apikey", "access_key", "private_key", "client_secret")
def _looks_like_a_credential(name: str) -> bool:
    lowered = name.lower()
    return any(part in lowered for part in _CREDENTIAL_NAME_PARTS)
def _redact_credentials(value):
    """A copy of `value` with credential-named fields blanked.

    /config/main returned the raw config to anyone who could reach the port,
    and this interface has no authentication. On one rig that meant a 40-char
    GitHub token, a 183-char Home Assistant token and five API keys were
    readable by anything on the LAN.

    The x-secret masking used by the plugin config endpoints does not help
    here: this endpoint never consults a schema, and core keys such as
    github.api_token have no schema to mark. Matching on the field name is
    blunt, but for a whole-config dump the right default is that anything
    named like a credential does not leave the process.

    Blanked rather than removed, and safe to blank: POST /config/main merges
    into the loaded config and only writes the keys it was given, so a client
    that round-trips this response cannot erase a secret it never saw.
    """
    if isinstance(value, dict):
        return {k: (_blank_credential_value(v) if _looks_like_a_credential(k)
                    else _redact_credentials(v))
                for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_credentials(item) for item in value]
    return value
def _blank_all_scalars(value):
    """Blank every scalar reached from `value`, at any depth.

    Unlike `_blank_credential_value`, this never delegates back to
    `_redact_credentials`'s name-based walk: an object reached through a
    credential-owned list (e.g. `tokens: [{"value": "secret"}]`) has no
    field name of its own to test, so every scalar inside it is blanked
    regardless of what its keys are called.
    """
    if isinstance(value, dict):
        return {k: _blank_all_scalars(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_blank_all_scalars(item) for item in value]
    return ""
def _blank_credential_value(value):
    """Blank a value that sits under a credential-shaped key.

    A dict is still walked -- `secrets: {api_key: ..., note: ...}` is a
    section name, not a value to blank in one go, so sub-fields that are not
    themselves credential-named survive (see
    test_a_credential_shaped_container_is_still_walked). A list has no field
    names to test for its items, though, so every scalar reached through it
    is blanked outright: a bare list of secrets (`tokens: ["a", "b"]`) and a
    list of credential-shaped objects (`tokens: [{"value": "secret"}]`) are
    both blanked at any depth via `_blank_all_scalars`.
    """
    if isinstance(value, dict):
        return _redact_credentials(value)
    if isinstance(value, list):
        return [_blank_all_scalars(item) for item in value]
    return ""
def _validate_time_format(time_str):
    """Validate time format is HH:MM"""
    try:
        datetime.strptime(time_str, '%H:%M')
        return True, None
    except (ValueError, TypeError):
        return False, f"Invalid time format: {time_str}. Expected HH:MM format."
def _git_current_branch(project_dir):
    """Current branch name, or '' when detached or git fails."""
    try:
        r = subprocess.run(['git', 'branch', '--show-current'],
                           capture_output=True, text=True, timeout=10, cwd=str(project_dir))
        return r.stdout.strip() if r.returncode == 0 else ''
    except (subprocess.TimeoutExpired, OSError):
        return ''
def _git_upstream(project_dir):
    """Configured upstream for the current branch (e.g. 'origin/main'), or ''."""
    try:
        r = subprocess.run(['git', 'rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{u}'],
                           capture_output=True, text=True, timeout=10, cwd=str(project_dir))
        return r.stdout.strip() if r.returncode == 0 else ''
    except (subprocess.TimeoutExpired, OSError):
        return ''
def _git_remote_branch_exists(project_dir, branch):
    """True when origin/<branch> exists locally as a remote-tracking ref."""
    if not branch:
        return False
    try:
        r = subprocess.run(
            ['git', 'show-ref', '--verify', '--quiet', f'refs/remotes/origin/{branch}'],
            capture_output=True, text=True, timeout=10, cwd=str(project_dir))
        return r.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False
def resolve_pull_command(project_dir):
    """Work out how to pull, for branches with and without an upstream.

    A plain ``git pull --rebase`` fails outright on a branch that has no
    upstream ("There is no tracking information for the current branch"),
    which is easy to end up on: checking out a branch by name, restoring a
    backup, or following an install guide that names one. The update button
    then reports a failure the user cannot act on.

    ``--autostash`` is passed for the same reason. Rebase refuses to start
    when any tracked file is modified, and on these installs something always
    is: first_time_install.sh chmods five scripts that git tracked as 644, so
    every machine that ran the installer carries five permanent mode changes
    and the update button reports "cannot pull with rebase: You have unstaged
    changes". Those modes are corrected in this commit, but a user cannot pull
    the correction while the pull is what is blocked, and any other local edit
    would reproduce it anyway. Autostash reapplies the changes afterwards.

    Returns ``(args, note, error)``. When ``origin/<branch>`` exists the pull
    is made explicit against it, so the update proceeds and the branch is
    given tracking information afterwards.
    """
    upstream = _git_upstream(project_dir)
    if upstream:
        return ['git', 'pull', '--rebase', '--autostash'], '', None

    branch = _git_current_branch(project_dir)
    if not branch:
        return None, '', (
            "This checkout is in a detached HEAD state, so there is no branch "
            "to update. Switch to a branch first (Tools -> Switch branch)."
        )
    if _git_remote_branch_exists(project_dir, branch):
        return (
            ['git', 'pull', '--rebase', '--autostash', 'origin', branch],
            f"Branch '{branch}' had no upstream; pulled from origin/{branch} and set it as the upstream.",
            None,
        )
    return None, '', (
        f"Branch '{branch}' has no upstream and there is no origin/{branch} to "
        f"pull from. Use Switch branch to move to a branch that exists on the "
        f"remote, or push this one first."
    )
_BRANCH_NAME_RE = re.compile(r'[A-Za-z0-9._/-]{1,200}')
def is_valid_branch_name(name):
    """Accept only plain branch names.

    This value becomes a subprocess argument, so anything exotic is refused
    rather than escaped. '..' is excluded because it is range syntax to git.
    """
    if not name or not _BRANCH_NAME_RE.fullmatch(name):
        return False
    return '..' not in name and not name.startswith('-')
def checkout_branch(project_dir, target, stash=False):
    """Switch the checkout to `target`, returning (payload, http_status).

    Split out of the route so it can be tested against real repositories.
    Attaches tracking when the branch exists on origin, so the next
    Pull Latest is a plain `git pull` rather than the no-upstream fallback.
    """
    target = (target or '').strip()
    if not target:
        return {'status': 'error', 'message': 'Branch name required'}, 400
    if not is_valid_branch_name(target):
        return {'status': 'error', 'message': 'Invalid branch name'}, 400

    try:
        subprocess.run(['git', 'fetch', 'origin', '--prune'],
                       capture_output=True, text=True, timeout=60, cwd=project_dir)

        local_exists = subprocess.run(
            ['git', 'show-ref', '--verify', '--quiet', f'refs/heads/{target}'],
            capture_output=True, text=True, timeout=10, cwd=project_dir).returncode == 0
        remote_exists = _git_remote_branch_exists(project_dir, target)
        if not local_exists and not remote_exists:
            return {'status': 'error',
                    'message': f"No branch '{target}' locally or on origin"}, 404

        # Local edits block a checkout. Pull Latest already stashes for the
        # same reason, so offer it here too -- but only when asked, never
        # silently: putting someone's edits away unasked is worse than
        # refusing the switch.
        stash_note = ''
        if stash:
            stashed = subprocess.run(['git', 'stash', 'push', '-m', f'switch to {target}'],
                                     capture_output=True, text=True, timeout=60, cwd=project_dir)
            if stashed.returncode == 0 and 'No local changes' not in stashed.stdout:
                stash_note = ' Local changes were stashed (recover them with git stash list).'

        if local_exists:
            co = subprocess.run(['git', 'checkout', target],
                                capture_output=True, text=True, timeout=60, cwd=project_dir)
        else:
            # -B so a stale local ref does not block the checkout.
            co = subprocess.run(['git', 'checkout', '-B', target, f'origin/{target}'],
                                capture_output=True, text=True, timeout=60, cwd=project_dir)

        if co.returncode != 0:
            logger.warning("git checkout %s failed: %s", target, co.stderr)
            return {
                'status': 'error',
                'message': f"Could not switch to '{target}'.",
                # Keep git's full list of blocking files: naming them is the
                # difference between an error the user can act on and one they
                # cannot.
                'detail': (co.stderr or '').strip(),
                'can_retry_with_stash': 'would be overwritten by checkout' in (co.stderr or ''),
            }, 200

        if remote_exists:
            subprocess.run(['git', 'branch', f'--set-upstream-to=origin/{target}', target],
                           capture_output=True, text=True, timeout=10, cwd=project_dir)

        logger.info("Switched checkout to branch %s", target)
        return {
            'status': 'success',
            'message': f"Now on '{target}'.{stash_note} Use Pull Latest to fetch its newest code.",
        }, 200
    except subprocess.TimeoutExpired:
        return {'status': 'error', 'message': 'Timed out talking to git'}, 504
    except OSError as exc:
        logger.error("checkout_branch failed: %s", exc, exc_info=True)
        return {'status': 'error', 'message': 'Could not switch branch'}, 500
def get_git_version(project_dir=None):
    """Get git version information from the repository"""
    if project_dir is None:
        project_dir = PROJECT_ROOT

    try:
        # Try to get tag description (e.g., v2.4-10-g123456)
        result = subprocess.run(
            ['git', 'describe', '--tags', '--dirty'],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=str(project_dir)
        )

        if result.returncode == 0:
            version_str = result.stdout.strip()
            if re.match(r'^[a-zA-Z0-9._\-]+$', version_str):
                return version_str

        # Fallback to short commit hash
        result = subprocess.run(
            ['git', 'rev-parse', '--short', 'HEAD'],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=str(project_dir)
        )

        if result.returncode == 0:
            version_str = result.stdout.strip()
            if re.match(r'^[a-zA-Z0-9._\-]+$', version_str):
                return version_str

        return 'Unknown'
    except Exception:
        return 'Unknown'
_update_check_cache: Dict[str, Any] = {'result': None, 'ts': 0.0}
_UPDATE_CHECK_TTL = 300  # 5 minutes — avoids a git fetch on every page load
def _update_check_failed(detail: str) -> Dict[str, Any]:
    """A check that could not run is not the same as being up to date.

    Reporting update_available=False on a git failure hides the banner, and
    the banner is the only route to the update button -- so a checkout git
    refuses to touch looks exactly like a current one, permanently. The most
    common cause is an install performed as root: git then reports "dubious
    ownership" and every command fails, including the fetch here.
    """
    return {'update_available': False, 'remote_sha': 'unknown',
            'commits_behind': 0, 'check_failed': True, 'error': detail}
def _describe_git_failure(stderr: str) -> str:
    """Turn git's stderr into something the user can act on."""
    text = (stderr or '').strip()
    if 'dubious ownership' in text or 'detected dubious ownership' in text:
        return ("This checkout is owned by a different user than the one "
                "running the web interface, so git refuses to use it. It is "
                "usually the result of installing as root. Fix the ownership "
                "and the update will work: sudo chown -R $USER:$USER "
                + str(PROJECT_ROOT))
    if 'could not resolve host' in text.lower() or 'network is unreachable' in text.lower():
        return "Could not reach GitHub to check for updates."
    return "Could not check for updates: " + (text.splitlines()[0] if text else "git failed")
def _installed_plugin_ids():
    """Best-effort list of installed plugin IDs for the web process.

    Health/metrics state is written by the separate display service to the
    shared on-disk cache, so the tracker's in-memory set is empty here. We
    enumerate the installed plugins and read each one's persisted summary by ID
    instead of relying on the tracker's in-memory `get_all_*` view.
    """
    pm = api_v3.plugin_manager
    manifests = getattr(pm, 'plugin_manifests', None)
    if not manifests:
        # Only pay for a discovery scan when we haven't discovered anything yet;
        # subsequent polls reuse the already-populated manifest map.
        try:
            pm.discover_plugins()
        except Exception:
            logger.debug('discover_plugins failed while listing plugin ids', exc_info=True)
        manifests = getattr(pm, 'plugin_manifests', None)
    try:
        return list(manifests.keys()) if manifests else []
    except Exception:
        logger.debug('listing plugin_manifests failed while building plugin ids', exc_info=True)
        return []
def _do_transactional_uninstall(plugin_id, preserve_config):
    """Execute an uninstall with snapshot-based rollback.

    Order of operations:
      1. Snapshot main config + secrets (abort on unexpected errors, proceed on expected I/O errors).
      2. Clean up plugin config (abort with 500 if this raises — avoids orphaned files).
      3. Unload plugin from runtime if loaded (rollback + 500 if this raises).
      4. Remove plugin files (rollback + 500 if this returns False or raises).
      5. Finish (remove state, invalidate caches).

    Rollback restores the config snapshot and, if the plugin had been
    loaded before unload, calls load_plugin to restore runtime state.

    Returns (True, None) on success or (False, error_message) on failure.
    """
    from src.exceptions import ConfigError

    # --- Step 1: snapshot main + secrets ---
    main_snapshot = None
    secrets_snapshot = None
    try:
        main_snapshot = api_v3.config_manager.get_raw_file_content('main')
    except (OSError, ConfigError):
        pass  # Proceed without snapshot; narrow catch preserves TypeError/AttributeError
    try:
        secrets_snapshot = api_v3.config_manager.get_raw_file_content('secrets')
    except (OSError, ConfigError):
        pass

    # --- Step 2: cleanup config first (abort before touching filesystem) ---
    if not preserve_config:
        api_v3.config_manager.cleanup_plugin_config(plugin_id, remove_secrets=True)

    # Record whether the plugin was running before we touch anything.
    was_loaded = (
        api_v3.plugin_manager is not None
        and plugin_id in api_v3.plugin_manager.plugins
    )

    def _rollback(reload_plugin):
        if main_snapshot is not None:
            try:
                api_v3.config_manager.save_raw_file_content('main', main_snapshot)
            except Exception as restore_err:
                logger.error("Failed to restore main config snapshot for %s: %s", plugin_id, restore_err)
        if secrets_snapshot is not None:
            try:
                api_v3.config_manager.save_raw_file_content('secrets', secrets_snapshot)
            except Exception as restore_err:
                logger.error("Failed to restore secrets snapshot for %s: %s", plugin_id, restore_err)
        if reload_plugin and api_v3.plugin_manager is not None:
            try:
                api_v3.plugin_manager.load_plugin(plugin_id)
            except Exception as reload_err:
                logger.error("Failed to reload plugin %s during rollback: %s", plugin_id, reload_err)

    # --- Step 3: unload ---
    if was_loaded:
        try:
            api_v3.plugin_manager.unload_plugin(plugin_id)
        except Exception as unload_err:
            _rollback(reload_plugin=False)  # unload failed — runtime state unchanged
            return False, f"Failed to unload plugin {plugin_id}: {unload_err}"

    # --- Step 4: remove files ---
    try:
        success = api_v3.plugin_store_manager.uninstall_plugin(plugin_id)
    except Exception as remove_err:
        _rollback(reload_plugin=was_loaded)
        return False, f"Failed to remove plugin {plugin_id}: {remove_err}"

    if not success:
        _rollback(reload_plugin=was_loaded)
        return False, f"Failed to uninstall plugin {plugin_id}"

    # --- Step 5: finish ---
    if api_v3.schema_manager:
        api_v3.schema_manager.invalidate_cache(plugin_id)
    if api_v3.plugin_state_manager:
        api_v3.plugin_state_manager.remove_plugin_state(plugin_id)
    # Persistently record the uninstall so a later core `git pull` update
    # cannot resurrect a built-in plugin (committed under plugin-repos/) that
    # the user removed. Best-effort: never fail the uninstall over this.
    try:
        api_v3.plugin_store_manager.record_uninstalled_plugin(plugin_id)
    except Exception as record_err:
        logger.warning("Could not record uninstall for %s: %s", plugin_id, record_err)
    return True, None
def deep_merge(base_dict, update_dict):
    """
    Deep merge update_dict into base_dict.
    For nested dicts, recursively merge. For other types, update_dict takes precedence.

    Lists are intentionally REPLACED wholesale, never index-merged: form posts
    carry complete arrays, and index-merging would resurrect items the user
    deleted. This also applies to the parallel secrets lists produced by
    separate_secrets — a newly saved secrets list is authoritative.
    """
    result = base_dict.copy()
    for key, value in update_dict.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            # Recursively merge nested dicts
            result[key] = deep_merge(result[key], value)
        else:
            # For non-dict values or new keys, use the update value
            result[key] = value
    return result
def _parse_form_value(value):
    """
    Parse a form value into the appropriate Python type.
    Handles booleans, numbers, JSON arrays/objects, and strings.
    """
    import json

    if value is None:
        return None

    # Handle string values
    if isinstance(value, str):
        stripped = value.strip()

        # Check for boolean strings
        if stripped.lower() == 'true':
            return True
        if stripped.lower() == 'false':
            return False
        if stripped.lower() in ('null', 'none') or stripped == '':
            return None

        # Try parsing as JSON (for arrays and objects) - do this BEFORE number parsing
        # This handles RGB arrays like "[255, 0, 0]" correctly
        if stripped.startswith('[') or stripped.startswith('{'):
            try:
                return json.loads(stripped)
            except json.JSONDecodeError:
                pass

        # Try parsing as number
        try:
            if '.' in stripped:
                return float(stripped)
            return int(stripped)
        except ValueError:
            pass

        # Return as string (original value, not stripped)
        return value

    return value
def _get_schema_property(schema, key_path):
    """
    Get the schema property for a given key path (supports dot notation).

    Args:
        schema: The JSON schema dict
        key_path: Dot-separated path like "customization.time_text.font"

    Returns:
        The property schema dict or None if not found
    """
    if not schema or 'properties' not in schema:
        return None

    parts = key_path.split('.')
    current = schema['properties']
    i = 0

    while i < len(parts):
        # Try progressively longer candidates, longest first, so schema keys that
        # themselves contain dots (e.g. league keys like "fifa.world") are matched
        # instead of being mistaken for nested "fifa" -> "world" objects.
        matched = False
        for j in range(len(parts), i, -1):
            candidate = '.'.join(parts[i:j])
            if isinstance(current, dict) and candidate in current:
                prop = current[candidate]
                # Consumed all remaining parts — this is the target property.
                if j == len(parts):
                    return prop
                # Navigate deeper through an object with properties.
                if isinstance(prop, dict) and 'properties' in prop:
                    current = prop['properties']
                    i = j
                    matched = True
                    break
                # Matched a non-object before consuming the path — can't go deeper.
                return None
        if not matched:
            return None

    return None
def _is_field_required(key_path, schema):
    """
    Check if a field is required according to the schema.
    
    Args:
        key_path: Dot-separated path like "mqtt.username"
        schema: The JSON schema dict
    
    Returns:
        True if field is required, False otherwise
    """
    if not schema or 'properties' not in schema:
        return False
    
    parts = key_path.split('.')
    if len(parts) == 1:
        # Top-level field
        required = schema.get('required', [])
        return parts[0] in required
    else:
        # Nested field - navigate to parent object
        parent_path = '.'.join(parts[:-1])
        field_name = parts[-1]
        
        # Get parent property
        parent_prop = _get_schema_property(schema, parent_path)
        if not parent_prop or 'properties' not in parent_prop:
            return False
        
        # Check if field is required in parent
        required = parent_prop.get('required', [])
        return field_name in required
# Sentinel object to indicate a field should be skipped (not set in config)
_SKIP_FIELD = object()
def _parse_form_value_with_schema(value, key_path, schema):
    """
    Parse a form value using schema information to determine correct type.
    Handles arrays (comma-separated strings), objects, and other types.

    Args:
        value: The form value (usually a string)
        key_path: Dot-separated path like "category_order" or "customization.time_text.font"
        schema: The plugin's JSON schema

    Returns:
        Parsed value with correct type, or _SKIP_FIELD to indicate the field should not be set
    """
    import json

    # Get the schema property for this field
    prop = _get_schema_property(schema, key_path)

    # Handle None/empty values
    if value is None or (isinstance(value, str) and value.strip() == ''):
        # If schema says it's an array, return empty array instead of None
        if prop and prop.get('type') == 'array':
            return []
        # If schema says it's an object, return empty dict instead of None
        if prop and prop.get('type') == 'object':
            return {}
        # If it's an optional string field, preserve empty string instead of None
        if prop and prop.get('type') == 'string':
            if not _is_field_required(key_path, schema):
                return ""  # Return empty string for optional string fields
        # For number/integer fields, check if they have defaults or are required
        if prop:
            prop_type = prop.get('type')
            if prop_type in ('number', 'integer'):
                # If field has a default, use it
                if 'default' in prop:
                    return prop['default']
                # If field is not required and has no default, skip setting it
                if not _is_field_required(key_path, schema):
                    return _SKIP_FIELD
                # If field is required but empty, return None (validation will fail, which is correct)
                return None
        return None

    # Handle string values
    if isinstance(value, str):
        stripped = value.strip()

        # Check for boolean strings
        if stripped.lower() == 'true':
            return True
        if stripped.lower() == 'false':
            return False
        # "on"/"off" come from HTML checkboxes — only coerce when schema says boolean
        if prop and prop.get('type') == 'boolean':
            if stripped.lower() == 'on':
                return True
            if stripped.lower() == 'off':
                return False

        # Handle arrays based on schema
        if prop and prop.get('type') == 'array':
            # Try parsing as JSON first (handles "[1,2,3]" format)
            if stripped.startswith('['):
                try:
                    return json.loads(stripped)
                except json.JSONDecodeError:
                    pass

            # Otherwise, treat as comma-separated string
            if stripped:
                # Split by comma and strip each item
                items = [item.strip() for item in stripped.split(',') if item.strip()]
                # Try to convert items to numbers if schema items are numbers
                items_schema = prop.get('items', {})
                if items_schema.get('type') in ('number', 'integer'):
                    try:
                        return [int(item) if '.' not in item else float(item) for item in items]
                    except ValueError:
                        pass
                return items
            return []

        # Handle objects based on schema
        if prop and prop.get('type') == 'object':
            # Try parsing as JSON
            if stripped.startswith('{'):
                try:
                    return json.loads(stripped)
                except json.JSONDecodeError:
                    pass
            # If it's not JSON, return empty dict (form shouldn't send objects as strings)
            return {}

        # Try parsing as JSON (for arrays and objects) - do this BEFORE number parsing
        if stripped.startswith('[') or stripped.startswith('{'):
            try:
                return json.loads(stripped)
            except json.JSONDecodeError:
                pass

        # Handle numbers based on schema
        if prop:
            prop_type = prop.get('type')
            if prop_type == 'integer':
                try:
                    return int(stripped)
                except ValueError:
                    return prop.get('default', 0)
            elif prop_type == 'number':
                try:
                    return float(stripped)
                except ValueError:
                    return prop.get('default', 0.0)

        # Try parsing as number (fallback) — skip when schema explicitly says string
        if not (prop and prop.get('type') == 'string'):
            try:
                if '.' in stripped:
                    return float(stripped)
                return int(stripped)
            except ValueError:
                pass

        # Return as string
        return value

    return value
def _resolve_key_segments(key_path, config):
    """Split a dot-notation path into segments, greedily preserving keys that
    themselves contain dots (e.g. league keys like "fifa.world").

    At each level the longest candidate that matches a key already present in the
    config wins; otherwise the path splits on the next dot (the normal
    nested-create case). Because dotted keys such as ``leagues."fifa.world"``
    always exist in the saved config being updated, this routes the value to the
    real league object instead of fabricating a ``leagues.fifa.world`` tree.
    """
    parts = key_path.split('.')
    segments = []
    node = config
    i = 0
    while i < len(parts):
        matched = False
        if isinstance(node, dict):
            for j in range(len(parts), i, -1):
                candidate = '.'.join(parts[i:j])
                if candidate in node:
                    segments.append(candidate)
                    node = node[candidate]
                    i = j
                    matched = True
                    break
        if not matched:
            part = parts[i]
            segments.append(part)
            node = node.get(part) if isinstance(node, dict) else None
            i += 1
    return segments
def _set_nested_value(config, key_path, value):
    """
    Set a value in a nested dict using dot notation path.
    Handles existing nested dicts correctly by merging instead of replacing.
    Keys containing dots (e.g. league keys like "fifa.world") are preserved when
    they already exist in the config rather than being split into nested objects.

    Args:
        config: The config dict to modify
        key_path: Dot-separated path (e.g., "customization.period_text.font")
        value: The value to set (or _SKIP_FIELD to skip setting)
    """
    # Skip setting if value is the sentinel
    if value is _SKIP_FIELD:
        return

    segments = _resolve_key_segments(key_path, config)
    current = config

    # Navigate/create intermediate dicts
    for seg in segments[:-1]:
        if seg not in current:
            current[seg] = {}
        elif not isinstance(current[seg], dict):
            # If the existing value is not a dict, replace it with a dict
            current[seg] = {}
        current = current[seg]

    # Set the final value (don't overwrite with empty dict if value is None and we want to preserve structure)
    if value is not None or segments[-1] not in current:
        current[segments[-1]] = value
def _set_missing_booleans_to_false(config, schema_props, form_keys, prefix='', config_node=None):
    """Walk schema and set missing boolean form fields to False.

    HTML checkboxes don't submit values when unchecked. When saving plugin config,
    the backend starts from existing config (to support partial form updates), which
    means an unchecked checkbox's old ``True`` value persists. This function detects
    boolean schema properties not present in the form submission and explicitly sets
    them to ``False``.

    The top-level ``enabled`` field is excluded because it has its own preservation
    logic in the save endpoint.

    Handles boolean fields inside nested objects and inside arrays of objects
    (e.g. ``feeds.custom_feeds.0.enabled``).

    Args:
        config: The root plugin config dict (used for pure-dict paths)
        schema_props: Schema ``properties`` dict at the current nesting level
        form_keys: Set of form field names that were submitted
        prefix: Dot-notation prefix for the current nesting level
        config_node: The current config subtree when inside an array item (avoids
                     using _set_nested_value which corrupts lists)
    """
    # Determine which config node to operate on
    node = config_node if config_node is not None else config

    for prop_name, prop_schema in schema_props.items():
        if not isinstance(prop_schema, dict):
            continue

        full_path = f"{prefix}.{prop_name}" if prefix else prop_name
        prop_type = prop_schema.get('type')

        if prop_type == 'boolean' and full_path != 'enabled':
            # If this boolean wasn't submitted in the form, it's an unchecked checkbox
            if full_path not in form_keys:
                if config_node is not None:
                    # Inside an array item — set directly on the item dict
                    node[prop_name] = False
                else:
                    # Pure dict path — use helper
                    _set_nested_value(config, full_path, False)

        elif prop_type == 'object' and 'properties' in prop_schema:
            # Recurse into nested objects
            if config_node is not None:
                # Inside an array item — ensure nested dict exists in item
                if prop_name not in node or not isinstance(node[prop_name], dict):
                    node[prop_name] = {}
                _set_missing_booleans_to_false(
                    config, prop_schema['properties'], form_keys, full_path,
                    config_node=node[prop_name]
                )
            else:
                _set_missing_booleans_to_false(
                    config, prop_schema['properties'], form_keys, full_path
                )

        elif prop_type == 'array':
            # Handle arrays of objects that may contain boolean fields
            # Form keys use indexed notation: "path.0.field", "path.1.field"
            items_schema = prop_schema.get('items', {})
            if isinstance(items_schema, dict) and items_schema.get('type') == 'object' and 'properties' in items_schema:
                array_prefix = f"{full_path}."
                # Collect unique item indices from submitted form keys
                indices = set()
                for k in form_keys:
                    if k.startswith(array_prefix):
                        # Extract index: "path.0.field" -> "0"
                        rest = k[len(array_prefix):]
                        idx = rest.split('.', 1)[0]
                        if idx.isdigit():
                            indices.add(int(idx))

                if not indices:
                    continue

                # Navigate to the array in the config (create if missing)
                if config_node is not None:
                    if prop_name not in node or not isinstance(node[prop_name], list):
                        node[prop_name] = []
                    array_list = node[prop_name]
                else:
                    # Navigate from root config through dict keys to get the list
                    parts = full_path.split('.')
                    current = config
                    for part in parts[:-1]:
                        if part not in current or not isinstance(current[part], dict):
                            current[part] = {}
                        current = current[part]
                    arr_key = parts[-1]
                    if arr_key not in current or not isinstance(current[arr_key], list):
                        current[arr_key] = []
                    array_list = current[arr_key]

                # Recurse into each array item so its missing booleans get set to False
                for idx in indices:
                    # Ensure list is long enough and item is a dict
                    while len(array_list) <= idx:
                        array_list.append({})
                    if not isinstance(array_list[idx], dict):
                        array_list[idx] = {}
                    item_prefix = f"{full_path}.{idx}"
                    _set_missing_booleans_to_false(
                        config, items_schema['properties'], form_keys, item_prefix,
                        config_node=array_list[idx]
                    )
def _enhance_schema_with_core_properties(schema):
    """
    Enhance schema with core plugin properties (enabled, display_duration, live_priority).
    These properties are system-managed and should always be allowed even if not in the plugin's schema.

    Args:
        schema: The original JSON schema dict

    Returns:
        Enhanced schema dict with core properties injected
    """
    import copy

    if not schema:
        return schema

    # Core plugin properties that should always be allowed
    # These match the definitions in SchemaManager.validate_config_against_schema()
    core_properties = {
        "enabled": {
            "type": "boolean",
            "default": True,
            "description": "Enable or disable this plugin"
        },
        "display_duration": {
            "type": "number",
            "default": 15,
            "minimum": 1,
            "maximum": 300,
            "description": "How long to display this plugin in seconds"
        },
        "live_priority": {
            "type": "boolean",
            "default": False,
            "description": "Enable live priority takeover when plugin has live content"
        }
    }

    # Create a deep copy of the schema to modify (to avoid mutating the original)
    enhanced_schema = copy.deepcopy(schema)
    if "properties" not in enhanced_schema:
        enhanced_schema["properties"] = {}

    # Inject core properties if they're not already defined in the schema
    for prop_name, prop_def in core_properties.items():
        if prop_name not in enhanced_schema["properties"]:
            enhanced_schema["properties"][prop_name] = copy.deepcopy(prop_def)

    return enhanced_schema
def _filter_config_by_schema(config, schema, prefix=''):
    """
    Filter config to only include fields defined in the schema.
    Removes fields not in schema, especially important when additionalProperties is false.

    Args:
        config: The config dict to filter
        schema: The JSON schema dict
        prefix: Prefix for nested paths (used recursively)

    Returns:
        Filtered config dict containing only schema-defined fields
    """
    if not schema or 'properties' not in schema:
        return config

    filtered = {}
    schema_props = schema.get('properties', {})

    for key, value in config.items():
        if key not in schema_props:
            # Field not in schema, skip it
            continue

        prop_schema = schema_props[key]

        # Handle nested objects recursively
        if isinstance(value, dict) and prop_schema.get('type') == 'object' and 'properties' in prop_schema:
            filtered[key] = _filter_config_by_schema(value, prop_schema, f"{prefix}.{key}" if prefix else key)
        else:
            # Keep the value as-is for non-object types
            filtered[key] = value

    return filtered
_MAX_CREDENTIAL_BACKUPS = 5
def _prune_credential_backups(plugin_dir: Path) -> None:
    """Keep only the newest _MAX_CREDENTIAL_BACKUPS credential backups.

    Every re-upload copies the previous credentials.json aside. Without
    pruning those accumulate for the life of the install — each one a
    complete set of OAuth client credentials sitting in the plugin
    directory.
    """
    backups = sorted(
        plugin_dir.glob('credentials.json.backup.*'),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for stale in backups[_MAX_CREDENTIAL_BACKUPS:]:
        try:
            stale.unlink()
        except OSError:
            logger.warning("Could not remove old credential backup %s", stale.name)
# calendarList.list pages at 250 entries maximum. Ten pages is far past any
# real account and exists only so a malformed nextPageToken cannot spin here.
_CALENDAR_LIST_MAX_PAGES = 10
def _calendar_plugin_dir() -> Optional[Path]:
    """Where the calendar plugin is installed, or None if it is not."""
    if api_v3.plugin_manager:
        plugin_dir = api_v3.plugin_manager.get_plugin_directory('calendar')
    else:
        plugin_dir = PROJECT_ROOT / 'plugins' / 'calendar'
    if not plugin_dir:
        return None
    plugin_dir = Path(plugin_dir)
    return plugin_dir if plugin_dir.exists() else None
def _run_calendar_registration(plugin_dir: Path, stdin_payload: str):
    """Run the plugin's OAuth script and return the JSON object it prints.

    The script decides between web and terminal mode by whether stdin is a
    tty, so it must be given a pipe. It emits one JSON object on stdout; the
    last parsable line is taken, because an import warning or a library's
    stderr redirection can land in front of it.

    Returns (payload, error_message). Exactly one is None.
    """
    script = plugin_dir / 'calendar_registration.py'
    if not script.exists():
        return None, 'Authentication script not found in the calendar plugin'

    try:
        result = subprocess.run(  # nosec B603 - fixed script path inside the plugin dir
            [sys.executable, str(script)],
            input=stdin_payload,
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(plugin_dir),
        )
    except subprocess.TimeoutExpired:
        return None, 'Authentication timed out after 120s'
    except OSError as e:
        logger.error('Could not run calendar_registration.py', exc_info=True)
        return None, 'Could not run the authentication script: %s' % describe_exception(e)

    for line in reversed((result.stdout or '').splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload, None

    raw = (result.stderr or result.stdout or '').strip()
    # Redacted in the log too: this is a script that handles OAuth client
    # secrets, and its stderr can quote them verbatim (CWE-532).
    if raw:
        logger.error('calendar_registration.py failed (exit %s): %s',
                     result.returncode, redact_text(raw))
    return None, 'Authentication script produced no result%s' % (
        ': %s' % redact_text(raw) if raw else '')
def _resolve_backup_export_dir() -> Path:
    """Where exported backups live: beside the install, not inside it.

    They used to be written to ``<project>/config/backups/exports``. That is
    inside the directory a reinstall deletes, so the documented recovery path
    -- export a backup, then reinstall -- destroyed the backup it had just
    told the user to make. Anyone who downloaded the ZIP was fine; anyone
    relying on the on-device copy was not.

    Falls back to the old location when the parent directory is not writable,
    so an unusual layout degrades to previous behaviour instead of failing to
    export at all.
    """
    preferred = PROJECT_ROOT.parent / "ledmatrix-backups"
    fallback = PROJECT_ROOT / "config" / "backups" / "exports"
    try:
        preferred.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=preferred, prefix=".writetest-"):
            pass
        return preferred
    except OSError as e:
        logger.warning(
            f"[Backup] Export dir {preferred} is not writable ({e}); "
            f"falling back to {fallback}, which a reinstall will delete"
        )
        return fallback
_BACKUP_EXPORT_DIR = _resolve_backup_export_dir()
def _safe_backup_path(filename: str) -> Path:
    """Resolve a filename to an absolute path inside the export dir,
    rejecting any traversal attempts. Returns None if unsafe."""
    # Use basename first (CodeQL-recognized sanitizer) then validate format
    filename = os.path.basename(filename or '')
    if not filename or not re.match(r'^[a-zA-Z0-9][a-zA-Z0-9._-]{0,200}\.zip$', filename):
        return None
    path = (_BACKUP_EXPORT_DIR / filename).resolve()
    try:
        path.relative_to(_BACKUP_EXPORT_DIR.resolve())
    except ValueError:
        return None
    return path
_STARLARK_APPS_DIR = PROJECT_ROOT / 'starlark-apps'
_STARLARK_MANIFEST_FILE = _STARLARK_APPS_DIR / 'manifest.json'
# A dedicated, never-replaced file to flock -- see _starlark_manifest_lock.
_STARLARK_MANIFEST_LOCK_FILE = _STARLARK_APPS_DIR / 'manifest.json.lock'
def _get_starlark_plugin() -> Optional[Any]:
    """Get the starlark-apps plugin instance, or None."""
    if not api_v3.plugin_manager:
        return None
    return api_v3.plugin_manager.get_plugin('starlark-apps')
def _find_pixlet_binary(explicit_path: Optional[str] = None) -> Optional[str]:
    """Find pixlet binary: explicit path → bundled binary → system PATH."""
    import platform
    if explicit_path and os.path.isfile(explicit_path) and os.access(explicit_path, os.X_OK):
        return explicit_path
    bin_dir = PROJECT_ROOT / "bin" / "pixlet"
    system = platform.system().lower()
    machine = platform.machine().lower()
    if system == "linux":
        if "aarch64" in machine or "arm64" in machine:
            name = "pixlet-linux-arm64"
        elif "x86_64" in machine or "amd64" in machine:
            name = "pixlet-linux-amd64"
        else:
            name = None
    elif system == "darwin":
        name = "pixlet-darwin-arm64" if "arm64" in machine else "pixlet-darwin-amd64"
    else:
        name = None
    if name:
        bundled = bin_dir / name
        if bundled.is_file():
            if os.access(str(bundled), os.X_OK):
                return str(bundled)
            try:
                bundled.chmod(0o755)
            except OSError:
                logger.warning("Could not make pixlet bundled binary executable (%s); falling back to PATH", bundled)
            else:
                if os.access(str(bundled), os.X_OK):
                    return str(bundled)
                logger.warning("Pixlet bundled binary still not executable after chmod (%s); falling back to PATH", bundled)
    return shutil.which("pixlet")
@contextlib.contextmanager
def _starlark_manifest_lock():
    """Hold an exclusive lock across a standalone starlark-manifest read-modify-write.

    StarlarkAppsPlugin._update_manifest_safe (plugin-repos/starlark-apps/manager.py)
    already does this -- fcntl.flock held for the whole read-modify-write cycle --
    when the plugin instance is loaded. These routes fall back to reading and
    writing manifest.json directly when it is not, and did so with no lock: each
    _write_starlark_manifest() call is atomic on its own (temp file + rename), but
    two concurrent requests can each read the manifest, mutate their own copy, and
    write it back, and the second write silently discards the first's change.

    Locks _STARLARK_MANIFEST_LOCK_FILE, a sidecar that is never written to or
    renamed over -- not manifest.json itself. manifest.json is replaced by an
    atomic rename on every write (here and in the plugin), which swaps in a
    fresh inode; a lock held on the old inode does not exclude a second locker
    that opens the path afresh right after the rename and gets the new inode,
    so two writers could still race each other despite both "holding a lock".
    A stable sidecar path always resolves to the same inode, so every locker
    -- standalone or plugin-owned -- contends for the same lock. The plugin
    must lock this same sidecar file for that guarantee to hold across both.

    Callers should do their read, mutation and _write_starlark_manifest() call
    entirely inside the `with` block, mirroring the plugin's lock scope.
    """
    # Imported here, not at module scope: fcntl is POSIX-only, and a top-level
    # import made the whole api_v3 package unimportable on Windows -- which the
    # monolithic blueprint never was, so the split would have broken local dev
    # and the test suite there. Deliberately NOT degraded to a no-op lock off
    # POSIX: the docstring above describes a real lost-update race, and silently
    # not locking would be worse than failing loudly on a platform that cannot
    # run the display anyway.
    import fcntl

    _STARLARK_APPS_DIR.mkdir(parents=True, exist_ok=True)
    lock_fd = os.open(str(_STARLARK_MANIFEST_LOCK_FILE), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
    finally:
        os.close(lock_fd)
def _read_starlark_manifest() -> Dict[str, Any]:
    """Read the starlark-apps manifest.json directly from disk."""
    try:
        if _STARLARK_MANIFEST_FILE.exists():
            with open(_STARLARK_MANIFEST_FILE, 'r') as f:
                return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.error(f"Error reading starlark manifest: {e}")
    return {'apps': {}}
def _starlark_github_token() -> Optional[str]:
    """The GitHub token the Starlark store should authenticate with.

    These routes used to read `github_token` off config.json, a key that is
    written nowhere and offered by no setting -- so the store always ran
    unauthenticated at 60 requests/hour, on the same per-IP budget every
    plugin update check spends, while the token the user had actually
    configured sat in config_secrets.json raising the same budget to 5000.
    The store going blank was that budget running out.

    Prefer the store manager's token, which is the one the settings UI
    writes and validates; keep the config.json key as a fallback so a
    hand-edited config still works.
    """
    token = getattr(api_v3.plugin_store_manager, 'github_token', None)
    if token:
        return token

    try:
        config = api_v3.config_manager.load_config() if api_v3.config_manager else {}
        return config.get('github_token')
    except Exception:
        logger.warning("[Starlark] Could not read config for a GitHub token", exc_info=True)
        return None
def _get_tronbyte_repository_class() -> Type[Any]:
    """Import TronbyteRepository from plugin-repos directory."""
    import importlib.util
    import importlib

    module_path = PROJECT_ROOT / 'plugin-repos' / 'starlark-apps' / 'tronbyte_repository.py'
    if not module_path.exists():
        raise ImportError(f"TronbyteRepository module not found at {module_path}")

    # If already imported, return cached class
    if "tronbyte_repository" in sys.modules:
        return sys.modules["tronbyte_repository"].TronbyteRepository

    spec = importlib.util.spec_from_file_location("tronbyte_repository", str(module_path))
    if spec is None:
        raise ImportError(f"Failed to create module spec for tronbyte_repository at {module_path}")

    module = importlib.util.module_from_spec(spec)
    if module is None:
        raise ImportError("Failed to create module from spec for tronbyte_repository")

    sys.modules["tronbyte_repository"] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        # A module that failed to execute must not stay in sys.modules: the
        # cache branch above would hand back the half-initialised object for
        # the rest of the process, so one transient failure would disable
        # this path permanently and surface as AttributeError, not ImportError.
        sys.modules.pop("tronbyte_repository", None)
        raise
    return module.TronbyteRepository
def _get_pixlet_renderer_class() -> Type[Any]:
    """Import PixletRenderer from plugin-repos directory."""
    import importlib.util
    import importlib

    module_path = PROJECT_ROOT / 'plugin-repos' / 'starlark-apps' / 'pixlet_renderer.py'
    if not module_path.exists():
        raise ImportError(f"PixletRenderer module not found at {module_path}")

    # If already imported, return cached class
    if "pixlet_renderer" in sys.modules:
        return sys.modules["pixlet_renderer"].PixletRenderer

    spec = importlib.util.spec_from_file_location("pixlet_renderer", str(module_path))
    if spec is None:
        raise ImportError(f"Failed to create module spec for pixlet_renderer at {module_path}")

    module = importlib.util.module_from_spec(spec)
    if module is None:
        raise ImportError("Failed to create module from spec for pixlet_renderer")

    sys.modules["pixlet_renderer"] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        # A module that failed to execute must not stay in sys.modules: the
        # cache branch above would hand back the half-initialised object for
        # the rest of the process, so one transient failure would disable
        # this path permanently and surface as AttributeError, not ImportError.
        sys.modules.pop("pixlet_renderer", None)
        raise
    return module.PixletRenderer
def _validate_and_sanitize_app_id(app_id: Optional[str], fallback_source: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]:
    """Validate and sanitize app_id to a safe slug."""
    if not app_id and fallback_source:
        app_id = fallback_source
    if not app_id:
        return None, "app_id is required"
    if '..' in app_id or '/' in app_id or '\\' in app_id:
        return None, "app_id contains invalid characters"

    sanitized = re.sub(r'[^a-z0-9_]', '_', app_id.lower()).strip('_')
    if not sanitized:
        sanitized = f"app_{hashlib.sha256(app_id.encode()).hexdigest()[:12]}"
    if sanitized[0].isdigit():
        sanitized = f"app_{sanitized}"
    return sanitized, None
def _validate_timing_value(value: Any, field_name: str, min_val: int = 1, max_val: int = 86400) -> Tuple[Optional[int], Optional[str]]:
    """Validate and coerce timing values."""
    if value is None:
        return None, None
    try:
        int_value = int(value)
    except (ValueError, TypeError):
        return None, f"{field_name} must be an integer"
    if int_value < min_val:
        return None, f"{field_name} must be at least {min_val}"
    if int_value > max_val:
        return None, f"{field_name} must be at most {max_val}"
    return int_value, None
def _validate_starlark_app_path(app_id: str) -> Tuple[Optional[Path], Optional[str]]:
    """The app's directory, or an error if app_id could escape the base dir.

    Returns the *resolved* path rather than a boolean, and every caller uses
    what it returns instead of re-joining ``_STARLARK_APPS_DIR / app_id``
    afterwards. The old shape validated in one place and rebuilt the path in
    another, which is two things that have to stay in step -- and is why
    CodeQL reported twenty-four path-injection alerts across these handlers
    even though the guard was effective: a boolean is not a sanitiser it can
    follow, and the value reaching the filesystem was the raw one.

    The name is unchanged so the call sites read the same.
    """
    if not isinstance(app_id, str) or not app_id:
        return None, "Invalid app_id"

    # Reject the traversal characters outright before touching the filesystem.
    if '..' in app_id or '/' in app_id or '\\' in app_id:
        return None, "Invalid app_id: contains path traversal characters"

    # os.path.basename strips any directory component, so what is joined below
    # cannot carry one. The equality check means this rejects rather than
    # silently truncates -- behaviour is identical to the character test above,
    # and it is the sanitiser CodeQL's path-injection query actually follows.
    # relative_to() alone is a check it cannot trace, which is why twenty-four
    # of these stayed flagged after the value was threaded through properly.
    safe_name = os.path.basename(app_id)
    if safe_name != app_id or safe_name in ('', '.', '..'):
        return None, "Invalid app_id: contains path traversal characters"

    try:
        base_path = _STARLARK_APPS_DIR.resolve()
        app_path = (base_path / safe_name).resolve()
        try:
            app_path.relative_to(base_path)
        except ValueError:
            return None, "Invalid app_id: path traversal attempt"
        return app_path, None
    except OSError as e:
        logger.warning("Path validation error for app_id %r: %s", app_id, e)
        return None, "Invalid app_id"
def _standalone_render_starlark_app(app_id: str) -> Tuple[bool, int, Optional[str]]:
    """Render a Starlark app via pixlet directly (no plugin required).

    Reads the .star file and config from starlark-apps/{app_id}/, runs pixlet,
    and saves the output to cached_render.webp in the same directory.
    This is the web-service fallback when starlark-apps plugin is not loaded.

    Returns (success, http_status_code, error_message).
    """
    manifest = _read_starlark_manifest()
    if not isinstance(manifest, dict):
        return False, 400, "Invalid manifest shape: expected object with 'apps' mapping"
    apps = manifest.get('apps', {})
    if not isinstance(apps, dict):
        return False, 400, "Invalid manifest shape: expected object with 'apps' mapping"
    app_data = apps.get(app_id)
    if not app_data:
        return False, 404, f"App not found: {app_id}"

    # Validated here as well as at the handler: this is reachable on its own,
    # and a path built from app_id should never be assembled without it.
    app_dir, path_error = _validate_starlark_app_path(app_id)
    if path_error:
        return False, 400, path_error
    star_file = app_dir / app_data.get('star_file', f'{app_id}.star')
    if not star_file.exists():
        return False, 404, f"Star file not found: {star_file}"

    full_config = api_v3.config_manager.load_config() if api_v3.config_manager else {}
    plugin_config = full_config.get('starlark-apps', {})

    pixlet_path = _find_pixlet_binary(plugin_config.get('pixlet_path'))
    if not pixlet_path:
        return False, 503, "Pixlet binary not found — install pixlet first"

    magnify = plugin_config.get('magnify')
    if magnify is None:
        hw = full_config.get('display', {}).get('hardware', {})
        cols = hw.get('cols', 64)
        chain = hw.get('chain_length', 1)
        rows = hw.get('rows', 32)
        magnify = max(1, min(8, int(min((cols * chain) / 64, rows / 32))))
    else:
        try:
            magnify = max(1, min(8, int(magnify)))
        except (ValueError, TypeError):
            magnify = 1

    config_file = app_dir / 'config.json'
    app_config: Dict[str, Any] = {}
    if config_file.exists():
        try:
            with open(config_file) as f:
                app_config = json.load(f)
        except json.JSONDecodeError as e:
            logger.warning("Invalid config.json for %r at %s: %s", app_id, config_file, e)
            return False, 400, f"Invalid config.json for {app_id}"
        except OSError as e:
            logger.warning("Cannot read config.json for %r at %s: %s", app_id, config_file, e)
            return False, 400, f"Cannot read config.json for {app_id}"
        if not isinstance(app_config, dict):
            return False, 400, (
                f"config.json for {app_id} must be a JSON object, "
                f"got {type(app_config).__name__}"
            )

    INTERNAL_KEYS = {'render_interval', 'display_duration'}
    pixlet_config = {k: v for k, v in app_config.items() if k not in INTERNAL_KEYS}

    output_path = str(app_dir / 'cached_render.webp')
    cmd = [pixlet_path, 'render', str(star_file)]
    for key, value in pixlet_config.items():
        if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', key):
            continue
        value_str = 'true' if value is True else 'false' if value is False else str(value)
        if re.search(r'[`$|<>&;\x00]|\$\(', value_str):
            continue
        cmd.append(f'{key}={value_str}')
    cmd.extend(['-o', output_path, '-m', str(magnify)])

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, cwd=str(app_dir))
        if result.returncode == 0 and os.path.isfile(output_path):
            return True, 200, None
        return False, 502, f"Pixlet failed (exit {result.returncode}): {result.stderr.strip()}"
    except subprocess.TimeoutExpired:
        return False, 504, "Render timed out after 30s"
    except Exception as e:
        logger.exception("Starlark render failed for %r", app_id)
        return False, 500, "Render error"
def _write_starlark_manifest(manifest: Dict[str, Any]) -> bool:
    """Write the starlark-apps manifest.json to disk with atomic write."""
    temp_file = None
    try:
        _STARLARK_APPS_DIR.mkdir(parents=True, exist_ok=True)

        # Atomic write: unique temp file in the target directory, then rename.
        # with_suffix('.tmp') gave every caller the same manifest.tmp, and
        # Flask serves concurrently -- upload, uninstall, config, toggle and
        # the plugin toggle all reach here. Two writers shared one file,
        # interleaved their json.dump output, and both renamed it, so the
        # rename was atomic over content that was a mix of two manifests.
        fd, temp_name = tempfile.mkstemp(
            dir=str(_STARLARK_APPS_DIR), prefix='.manifest.', suffix='.tmp')
        temp_file = Path(temp_name)
        with os.fdopen(fd, 'w') as f:
            json.dump(manifest, f, indent=2)
            f.flush()
            os.fsync(f.fileno())  # Ensure data is written to disk
        os.chmod(temp_name, 0o644)  # mkstemp creates 0600; match a normal write

        # Atomic rename (overwrites destination)
        temp_file.replace(_STARLARK_MANIFEST_FILE)
        return True
    except OSError as e:
        logger.error(f"Error writing starlark manifest: {e}")
        # Clean up temp file if it exists
        if temp_file and temp_file.exists():
            try:
                temp_file.unlink()
            except Exception:
                pass
        return False
def _install_star_file(app_id: str, star_file_path: str, metadata: Dict[str, Any], assets_dir: Optional[str] = None) -> bool:
    """Install a .star file and update the manifest (standalone, no plugin needed)."""
    import shutil
    import json
    app_dir, path_error = _validate_starlark_app_path(app_id)
    if path_error:
        logger.warning("Refusing to install %r: %s", app_id, path_error)
        return False
    app_dir.mkdir(parents=True, exist_ok=True)
    dest = app_dir / f"{app_id}.star"
    shutil.copy2(star_file_path, str(dest))

    # Copy asset directories if provided (images/, sources/, etc.)
    if assets_dir and Path(assets_dir).exists():
        assets_path = Path(assets_dir)
        for item in assets_path.iterdir():
            if item.is_dir():
                # Copy entire directory (e.g., images/, sources/)
                dest_dir = app_dir / item.name
                if dest_dir.exists():
                    shutil.rmtree(dest_dir)
                shutil.copytree(item, dest_dir)
                logger.debug(f"Copied assets directory: {item.name}")
        logger.info(f"Installed assets for {app_id}")

    # Try to extract schema using PixletRenderer
    schema = None
    try:
        PixletRenderer = _get_pixlet_renderer_class()
        pixlet = PixletRenderer()
        if pixlet.is_available():
            _, schema, _ = pixlet.extract_schema(str(dest))
            if schema:
                schema_path = app_dir / "schema.json"
                with open(schema_path, 'w') as f:
                    json.dump(schema, f, indent=2)
                logger.info(f"Extracted schema for {app_id}")
    except Exception as e:
        logger.warning(f"Failed to extract schema for {app_id}: {e}")

    # Create default config — pre-populate with schema defaults
    default_config = {}
    if schema:
        fields = schema.get('fields') or schema.get('schema') or []
        for field in fields:
            if isinstance(field, dict) and 'id' in field and 'default' in field:
                default_config[field['id']] = field['default']

    # Create config.json file
    config_path = app_dir / "config.json"
    with open(config_path, 'w') as f:
        json.dump(default_config, f, indent=2)

    with _starlark_manifest_lock():
        manifest = _read_starlark_manifest()
        manifest.setdefault('apps', {})[app_id] = {
            'name': metadata.get('name', app_id),
            'enabled': True,
            'render_interval': metadata.get('render_interval', 300),
            'display_duration': metadata.get('display_duration', 15),
            'config': metadata.get('config', {}),
            # The filename, not the full path. Readers join this to the app's own
            # directory and fall back to a bare '<app_id>.star', so an absolute
            # value gave the key two meanings -- and Path.__truediv__ discards the
            # left side when the right is absolute, which pinned the manifest to
            # whatever PROJECT_ROOT installed it. Moving or redeploying the install
            # then left the app unable to find its own file.
            'star_file': dest.name,
        }
        return _write_starlark_manifest(manifest)
def _starlark_virtual_plugins() -> list:
    """Installed Starlark apps, shaped like plugin entries.

    #253 surfaced these alongside real plugins so an installed .star app can
    be seen, enabled and disabled from the same list as everything else; #330
    dropped it with the rest of the Starlark code, which is why an app
    installs successfully and then appears nowhere.

    Reads the loaded plugin when there is one and the on-disk manifest
    otherwise, so the list is right before starlark-apps has been loaded too.
    """
    entries = []
    base = {
        'version': 'starlark', 'category': 'Starlark App', 'tags': ['starlark'],
        'verified': False, 'last_updated': None, 'last_commit': None,
        'last_commit_message': None, 'branch': None, 'web_ui_actions': [],
        'vegas_mode': 'fixed', 'vegas_content_type': 'multi',
        'is_starlark_app': True,
    }
    try:
        plugin = _get_starlark_plugin()
        if plugin is not None and hasattr(plugin, 'apps'):
            for app_id, app in plugin.apps.items():
                m = getattr(app, 'manifest', {}) or {}
                entries.append({**base,
                                'id': f'starlark:{app_id}',
                                'name': m.get('name', app_id),
                                'author': m.get('author', 'Tronbyte Community'),
                                'description': m.get('summary', 'Starlark app'),
                                'enabled': app.is_enabled(),
                                'loaded': True})
            return entries

        for app_id, data in (_read_starlark_manifest().get('apps', {}) or {}).items():
            entries.append({**base,
                            'id': f'starlark:{app_id}',
                            'name': data.get('name', app_id),
                            'author': data.get('author', 'Tronbyte Community'),
                            'description': data.get('summary', 'Starlark app'),
                            'enabled': data.get('enabled', True),
                            'loaded': False})
    except Exception:
        # Never let a Starlark problem empty the whole plugins list.
        logger.exception('Could not build Starlark virtual plugin entries')
    return entries
def _toggle_starlark_app(app_id: str, enabled: bool):
    """Enable or disable one Starlark app, loaded or not."""
    # Check for traversal, but toggle the key that was listed.
    # _starlark_virtual_plugins publishes the raw manifest key, while
    # _validate_and_sanitize_app_id lowercases it and rewrites every character
    # outside [a-z0-9_]: an app stored as 'My-App' was offered to the UI as
    # 'starlark:My-App' and looked up here as 'my_app', so toggling an app the
    # page had just drawn answered 404. Keys written by _install_star_file are
    # already sanitised; ones written by the plugin, or edited by hand, are
    # not. _validate_starlark_app_path rejects traversal without rewriting.
    _, err = _validate_starlark_app_path(app_id)
    if err:
        # err already names app_id; do not prefix it a second time.
        return jsonify({'status': 'error', 'message': err}), 400
    safe_id = app_id

    plugin = _get_starlark_plugin()
    if plugin is not None and safe_id in getattr(plugin, 'apps', {}):
        def _update(manifest):
            # setdefault rather than indexing: the app is loaded, but its
            # on-disk entry need not exist, and _update_manifest_safe does not
            # catch KeyError -- it would escape as a 500 rather than the error
            # this returns.
            manifest.setdefault('apps', {}).setdefault(safe_id, {})['enabled'] = enabled

        if plugin._update_manifest_safe(_update) is False:
            return jsonify({'status': 'error',
                            'message': 'Failed to save app state'}), 500
        # Only now is the in-memory copy allowed to disagree with disk.
        plugin.apps[safe_id].manifest['enabled'] = enabled
    else:
        with _starlark_manifest_lock():
            manifest = _read_starlark_manifest()
            app_data = manifest.get('apps', {}).get(safe_id)
            if not app_data:
                return jsonify({'status': 'error',
                                'message': f'Starlark app not found: {safe_id}'}), 404
            app_data['enabled'] = enabled
            if not _write_starlark_manifest(manifest):
                return jsonify({'status': 'error', 'message': 'Failed to save manifest'}), 500

    return jsonify({'status': 'success',
                    'message': f"Starlark app {'enabled' if enabled else 'disabled'}",
                    'enabled': enabled})


_PIXLET_EDITOR_SCRIPT = PROJECT_ROOT / 'scripts' / 'utils' / 'pixlet_config_editor.sh'

# Deliberately under /tmp: a session cannot survive a reboot, so neither should
# the record of one.
_PIXLET_EDITOR_STATE = Path(tempfile.gettempdir()) / 'ledmatrix_pixlet_editor.json'

_PIXLET_EDITOR_DEFAULT_PORT = 8080

_PIXLET_EDITOR_DEFAULT_TIMEOUT = 1800

_PIXLET_EDITOR_MAX_TIMEOUT = 14400

def _read_pixlet_editor_state() -> Optional[Dict[str, Any]]:
    try:
        if _PIXLET_EDITOR_STATE.is_file():
            with open(_PIXLET_EDITOR_STATE, encoding='utf-8') as handle:
                state = json.load(handle)
            return state if isinstance(state, dict) else None
    except (OSError, json.JSONDecodeError):
        logger.debug('Unreadable pixlet editor state; treating as no session', exc_info=True)
    return None

def _clear_pixlet_editor_state() -> None:
    with contextlib.suppress(OSError):
        _PIXLET_EDITOR_STATE.unlink()

def _pixlet_editor_alive(pid: Optional[int]) -> bool:
    """Is the recorded session still running?

    os.kill(pid, 0) is not enough on its own: the script is a child of this
    process, so once it exits it stays a zombie until reaped, and signal 0
    succeeds against a zombie. Left at that, a finished session would read as
    running forever and the UI would keep offering a Stop button for it.
    """
    if not pid:
        return False

    # Reap it if it is ours and already finished; harmless if it is not.
    with contextlib.suppress(ChildProcessError, OSError):
        reaped, _ = os.waitpid(pid, os.WNOHANG)
        if reaped == pid:
            return False

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Exists but is not ours to signal, which still counts as running.
        return True

    # Not our child (e.g. the web service restarted under a live session), so
    # waitpid told us nothing -- ask /proc whether it is merely a zombie.
    with contextlib.suppress(OSError, IndexError, ValueError):
        with open(f'/proc/{pid}/stat', encoding='utf-8') as handle:
            # The comm field can contain spaces and parens; everything after
            # the final ')' is positional, and state is the first of those.
            fields = handle.read().rsplit(')', 1)[1].split()
        if fields and fields[0] == 'Z':
            return False
    return True

def _pixlet_editor_status() -> Dict[str, Any]:
    """Current session, reconciled against reality.

    The state file records what we started; the process may have ended on its
    own (its timeout, a crash, a manual kill). Anything stale is cleared here so
    the UI never offers a Stop button for a session that is already over.
    """
    state = _read_pixlet_editor_state()
    if not state:
        return {'running': False}
    if not _pixlet_editor_alive(state.get('pid')):
        _clear_pixlet_editor_state()
        return {'running': False}

    remaining = None
    deadline = state.get('deadline')
    if isinstance(deadline, (int, float)):
        remaining = max(0, int(deadline - time.time()))
    return {
        'running': True,
        'app_id': state.get('app_id'),
        'port': state.get('port', _PIXLET_EDITOR_DEFAULT_PORT),
        'pid': state.get('pid'),
        'started_at': state.get('started_at'),
        'timeout': state.get('timeout'),
        'seconds_remaining': remaining,
        'host_bound': state.get('host', '0.0.0.0'),
    }

_MQTT_BRIDGE_DIR = PROJECT_ROOT / 'integrations' / 'mqtt_bridge'

_MQTT_BRIDGE_CONFIG = _MQTT_BRIDGE_DIR / 'bridge_config.json'

_MQTT_BRIDGE_EXAMPLE = _MQTT_BRIDGE_DIR / 'bridge_config.example.json'

_MQTT_BRIDGE_SERVICE = 'ledmatrix-mqtt-bridge.service'

# Mirrors DEFAULTS in ledmatrix_mqtt_bridge.py. Duplicated rather than imported
# because that module pulls in paho-mqtt, which the web process does not need
# installed just to render a settings form.
_MQTT_BRIDGE_DEFAULTS = {
    'mqtt_host': 'localhost',
    'mqtt_port': 1883,
    'mqtt_username': None,
    'mqtt_client_id': 'ledmatrix-mqtt-bridge',
    'mqtt_topic': 'ledmatrix/command',
    'mqtt_tls': False,
    'mqtt_tls_insecure': False,
    'ledmatrix_api_base': 'http://localhost:5000',
    'request_timeout': 15,
    'on_demand_duration': None,
    'log_level': 'INFO',
}

_MQTT_BRIDGE_LOG_LEVELS = ('DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL')

def _mqtt_bridge_service_state() -> Dict[str, Any]:
    """installed / active / enabled for the bridge unit."""
    state = {'installed': False, 'active': False, 'enabled': False}
    try:
        listed = _run_systemctl_command(
            ['systemctl', 'list-unit-files', _MQTT_BRIDGE_SERVICE, '--no-legend'])
        state['installed'] = bool((listed.get('stdout') or '').strip())
        state['active'] = _run_systemctl_command(
            ['systemctl', 'is-active', _MQTT_BRIDGE_SERVICE]).get('stdout', '').strip() == 'active'
        state['enabled'] = _run_systemctl_command(
            ['systemctl', 'is-enabled', _MQTT_BRIDGE_SERVICE]).get('stdout', '').strip() == 'enabled'
    except Exception:
        logger.debug('Could not read %s state', _MQTT_BRIDGE_SERVICE, exc_info=True)
    return state

def _read_mqtt_bridge_config() -> Dict[str, Any]:
    """Stored config overlaid on the defaults. Missing file is not an error.

    Named `settings`, not `config`: this module imports a submodule called
    `config` at the bottom for its route side effects, and a local of the same
    name shadows it.
    """
    settings = dict(_MQTT_BRIDGE_DEFAULTS)
    settings['mqtt_password'] = None
    try:
        if _MQTT_BRIDGE_CONFIG.is_file():
            with open(_MQTT_BRIDGE_CONFIG, encoding='utf-8') as handle:
                stored = json.load(handle)
            if isinstance(stored, dict):
                settings.update(stored)
    except (OSError, json.JSONDecodeError) as err:
        logger.warning('Could not read %s: %s', _MQTT_BRIDGE_CONFIG, err)
    return settings

def _coerce_mqtt_bridge_value(key: str, raw: Any) -> Tuple[Any, Optional[str]]:
    """Validate one submitted field. Returns (value, error)."""
    if key in ('mqtt_port',):
        try:
            port = int(raw)
        except (TypeError, ValueError):
            return None, 'Port must be a whole number'
        if not 1 <= port <= 65535:
            return None, 'Port must be between 1 and 65535'
        return port, None
    if key in ('request_timeout',):
        try:
            timeout = int(raw)
        except (TypeError, ValueError):
            return None, 'Request timeout must be a whole number'
        if not 1 <= timeout <= 300:
            return None, 'Request timeout must be between 1 and 300 seconds'
        return timeout, None
    if key == 'on_demand_duration':
        if raw in (None, ''):
            return None, None
        try:
            duration = int(raw)
        except (TypeError, ValueError):
            return None, 'On-demand duration must be a whole number of seconds'
        if not 1 <= duration <= 86400:
            return None, 'On-demand duration must be between 1 and 86400 seconds'
        return duration, None
    if key in ('mqtt_tls', 'mqtt_tls_insecure'):
        return bool(raw) if isinstance(raw, bool) else str(raw).lower() in ('1', 'true', 'yes', 'on'), None
    if key == 'log_level':
        level = str(raw or '').upper()
        if level not in _MQTT_BRIDGE_LOG_LEVELS:
            return None, f"Log level must be one of {', '.join(_MQTT_BRIDGE_LOG_LEVELS)}"
        return level, None
    if key == 'ledmatrix_api_base':
        base = str(raw or '').strip().rstrip('/')
        if not base.startswith(('http://', 'https://')):
            return None, 'API base must start with http:// or https://'
        if len(base) > 300:
            return None, 'API base is too long'
        return base, None
    # Remaining keys are free text; empty means "unset" for the optional ones.
    text = '' if raw is None else str(raw).strip()
    if len(text) > 300:
        return None, f'{key} is too long'
    if key == 'mqtt_username' and not text:
        return None, None
    if key in ('mqtt_host', 'mqtt_client_id', 'mqtt_topic') and not text:
        return None, f'{key.replace("_", " ")} cannot be empty'
    return text, None


# Imported last, and for their side effect: each registers its routes on
# api_v3. They import names from this module, so this module must be fully
# executed before they run.
from web_interface.blueprints.api_v3 import (  # noqa: E402,F401
    backup,
    config,
    display,
    fonts,
    misc,
    plugins,
    starlark,
    system,
    wifi,
)

__all__ = ["api_v3"]
