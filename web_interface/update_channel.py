"""Update channels: which LEDMatrix code "Update Code" and the weekly updater move to.

* **stable** follows releases: the newest ``vX.Y.Z`` tag, by semantic version.
  Pre-release tags (``v3.8.0-rc1``) and anything else that is not exactly
  ``vX.Y.Z`` are ignored. The checkout sits on the tag with a detached HEAD.
* **beta** is how every device updated before channels existed: it follows
  ``main`` (``git pull --rebase --autostash`` on the current branch).

The setting is ``auto_update.channel`` in config.json. New installs get
``stable`` from config/config.template.json (and the installer).

**Nobody is moved backwards.** stable only ever checks out a release tag that
contains the current commit, so a device running code newer than the newest
release -- anything that pulled main since that release, or a fresh install
of main -- keeps following main ("waiting") until a release that contains its
commit exists, and moves to it at the next update. A config written before
channels existed (no key) behaves the same way, and the key is written as
``stable`` when that move happens.

Standard library only, and every git call goes through ``run`` (default:
``subprocess.run`` looked up at call time, so tests that patch it are seen).
"""
import re
import subprocess  # nosec B404 - list-form argv only, no shell  # nosemgrep
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CHANNELS = ('stable', 'beta')
DEFAULT_CHANNEL = 'stable'
#: The branch beta follows, and the one a device on a release tag moves to.
BETA_BRANCH = 'main'
REMOTE = 'origin'

#: Exactly vMAJOR.MINOR.PATCH, no leading zeros, nothing after it.
_RELEASE_TAG_RE = re.compile(r'v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)')

#: Actions an update can take (``ChannelStatus.action``).
ACTION_NONE = 'none'              # nothing to do: on the newest release, or waiting
ACTION_CHECKOUT_TAG = 'checkout_tag'  # stable: move forward to the newest release tag
ACTION_PULL = 'pull'              # beta (or waiting for a release): pull the current branch
ACTION_SWITCH_TO_BETA = 'switch_to_beta'  # beta on a detached release tag: go back to main

AUTOSTASH_MESSAGE = 'LEDMatrix autostash before update'


def parse_release_tag(name):
    """``(major, minor, patch)`` for a release tag such as ``v3.7.0``, else None."""
    match = _RELEASE_TAG_RE.fullmatch((name or '').strip())
    return tuple(int(part) for part in match.groups()) if match else None


def newest_release_tag(names):
    """The highest release tag among ``names`` by semver, or None."""
    releases = [(parse_release_tag(n), n.strip()) for n in names or ()]
    releases = [pair for pair in releases if pair[0] is not None]
    return max(releases)[1] if releases else None


def normalize_channel(value):
    """'stable' or 'beta' from user input, else None."""
    if not isinstance(value, str):
        return None
    value = value.strip().lower()
    return value if value in CHANNELS else None


def configured_channel(config):
    """The channel config.json names, or None when it names none (or nonsense)."""
    section = (config or {}).get('auto_update')
    if not isinstance(section, dict):
        return None
    return normalize_channel(section.get('channel'))


def set_channel(config_manager, channel):
    """Write ``auto_update.channel`` to config.json. Returns the saved config."""
    channel = normalize_channel(channel)
    if channel is None:
        raise ValueError(f"channel must be one of {', '.join(CHANNELS)}")
    config = config_manager.load_config()
    if not isinstance(config.get('auto_update'), dict):
        config['auto_update'] = {}
    config['auto_update']['channel'] = channel
    config_manager.save_config(config)
    return config


class ChannelStatus(dict):
    """What the channel means for this checkout right now (a JSON-able dict).

    Keys: ``configured`` ('stable', 'beta' or None), ``channel`` (the one in
    effect: 'beta' while stable is waiting), ``waiting``, ``migrate`` (write
    'stable' to a config that names no channel), ``action``, ``head``,
    ``branch`` ('' when detached), ``newest_release`` and its commit
    ``newest_release_sha``, ``current_release`` (the release tag HEAD is
    exactly on, if any), ``target_sha`` for ACTION_CHECKOUT_TAG, and
    ``message`` for people.
    """

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as e:
            raise AttributeError(name) from e


def _git(project_dir, run, *args, timeout=30):
    run = run or subprocess.run
    return run(['git', *args], cwd=str(project_dir), capture_output=True, text=True, timeout=timeout)


def _out(result):
    out = result.stdout
    if isinstance(out, bytes):
        out = out.decode(errors='replace')
    return (out or '').strip() if result.returncode == 0 else ''


def fetch(project_dir, run=None, timeout=120):
    """Fetch origin's branches and tags. ``--force`` so a moved tag is updated, not an error."""
    return _git(project_dir, run, 'fetch', '--quiet', '--tags', '--force', REMOTE, timeout=timeout)


def release_tags(project_dir, run=None):
    return [line for line in _out(_git(project_dir, run, 'tag', '--list', 'v*')).splitlines() if line.strip()]


def current_branch(project_dir, run=None):
    """The checked-out branch, or '' when HEAD is detached."""
    return _out(_git(project_dir, run, 'symbolic-ref', '--quiet', '--short', 'HEAD'))


def is_ancestor(project_dir, older, newer, run=None):
    """True when ``older`` is ``newer`` or one of its ancestors."""
    return _git(project_dir, run, 'merge-base', '--is-ancestor', older, newer).returncode == 0


def resolve(project_dir, config, run=None):
    """Decide what an update on this checkout should do. Reads local refs only:
    fetch first (``fetch``) for an answer about what origin has."""
    configured = configured_channel(config)
    status = ChannelStatus(
        configured=configured, channel='beta', waiting=False, migrate=False,
        action=ACTION_PULL, head='', branch=None, newest_release=None,
        newest_release_sha='', current_release=None, target_sha='', message='')

    if configured == 'beta':
        status['branch'] = current_branch(project_dir, run)
        if status.branch:
            status['message'] = f'Beta: following {BETA_BRANCH} (the newest code, before it is released).'
        else:
            status['action'] = ACTION_SWITCH_TO_BETA
            status['message'] = (f'Beta: this device is on a release; the next update moves it to '
                                 f'{BETA_BRANCH}, the newest code.')
        return status

    newest = newest_release_tag(release_tags(project_dir, run))
    if newest is None:
        # No release to follow (never fetched, or a fork without tags):
        # update exactly as before channels existed.
        status['waiting'] = True
        status['message'] = (f'Stable: no release has been published yet, so updates follow '
                             f'{BETA_BRANCH} until one is.')
        return status

    head = _out(_git(project_dir, run, 'rev-parse', 'HEAD'))
    tag_sha = _out(_git(project_dir, run, 'rev-parse', f'{newest}^{{commit}}'))
    status.update(head=head, newest_release=newest, newest_release_sha=tag_sha,
                  branch=current_branch(project_dir, run))
    if head and head == tag_sha:
        status['current_release'] = newest

    if head and tag_sha and is_ancestor(project_dir, head, newest, run):
        status['channel'] = 'stable'
        status['migrate'] = configured is None
        if head == tag_sha:
            status['action'] = ACTION_NONE
            status['message'] = f'Stable: on the newest release, {newest}.'
        else:
            status['action'] = ACTION_CHECKOUT_TAG
            status['target_sha'] = tag_sha
            status['message'] = f'Stable: release {newest} is available.'
        return status

    # The newest release does not contain this commit: moving to it would go
    # backwards. Keep following the branch until a release that does exists.
    status['waiting'] = True
    if status.branch:
        status['message'] = (f'Stable: this device runs code newer than the newest release ({newest}), '
                             f'so it keeps following {BETA_BRANCH} and moves to the first release '
                             'that includes its current version.')
    else:
        # Detached and newer than the release: there is no branch to follow,
        # so stay put until a release catches up.
        status['action'] = ACTION_NONE
        status['message'] = (f'Stable: this device runs code newer than the newest release ({newest}); '
                             'it stays on it and moves to the first release that includes it.')
    return status


def checkout(project_dir, args, run=None, timeout=120):
    """``git checkout <args>`` that carries uncommitted edits across, like ``pull --autostash``.

    The edits are saved as a stash commit (``git stash create``, which
    leaves the stash list alone), the tree is cleaned, the checkout runs,
    and the edits are reapplied. If they no longer apply they are kept in
    the stash list rather than left half-merged, which is what git's own
    autostash does. Returns ``(result, note)``: ``result`` is the checkout's
    CompletedProcess, ``note`` a sentence for the user or ''.
    """
    stash_sha = _out(_git(project_dir, run, 'stash', 'create', AUTOSTASH_MESSAGE))
    if stash_sha:
        cleaned = _git(project_dir, run, 'reset', '--hard', '--quiet', timeout=timeout)
        if cleaned.returncode != 0:
            return cleaned, ''
    result = _git(project_dir, run, 'checkout', '--quiet', *args, timeout=timeout)
    note = ''
    if stash_sha:
        applied = _git(project_dir, run, 'stash', 'apply', '--quiet', stash_sha, timeout=timeout)
        if applied.returncode != 0:
            _git(project_dir, run, 'reset', '--hard', '--quiet', timeout=timeout)
            _git(project_dir, run, 'stash', 'store', '-m', f'{AUTOSTASH_MESSAGE} (did not reapply)', stash_sha)
            note = ('Local changes could not be reapplied to the new version and were kept '
                    'in the git stash (git stash list).')
    return result, note


def checkout_release(project_dir, tag, run=None):
    """Move to release ``tag`` (detached HEAD). Refuses a tag that would go backwards."""
    if parse_release_tag(tag) is None:
        raise ValueError(f'not a release tag: {tag!r}')
    head = _out(_git(project_dir, run, 'rev-parse', 'HEAD'))
    if not head or not is_ancestor(project_dir, head, tag, run):
        failed = subprocess.CompletedProcess(
            ['git', 'checkout', tag], 1, stdout='',
            stderr=f'release {tag} does not contain the current commit; refusing to move backwards')
        return failed, ''
    return checkout(project_dir, ['--detach', f'{tag}^{{commit}}'], run)


def checkout_beta_branch(project_dir, run=None):
    """Leave a detached release for ``main``, tracking origin/main. The caller then pulls.

    Straight to origin/main when the local branch has nothing of its own
    (the usual case: it is wherever the device last left main, often older
    than the release it is on). Going through that older commit would make
    the carried edits apply to the wrong version, and a plugin file that did
    not exist yet would drop them into the stash. A local branch with
    commits of its own is checked out as it is and rebased by the pull.
    """
    local_ref = f'refs/heads/{BETA_BRANCH}'
    remote_ref = f'{REMOTE}/{BETA_BRANCH}'
    local = _git(project_dir, run, 'show-ref', '--verify', '--quiet', local_ref).returncode == 0
    if local and not is_ancestor(project_dir, local_ref, remote_ref, run):
        result, note = checkout(project_dir, [BETA_BRANCH], run)
    else:
        # -B: create it, or fast-forward it (it is an ancestor, so nothing is lost).
        result, note = checkout(project_dir, ['-B', BETA_BRANCH, remote_ref], run)
    if result.returncode == 0:
        # A branch left without tracking would make the pull that follows
        # take the no-upstream fallback; set it while we are here.
        _git(project_dir, run, 'branch', f'--set-upstream-to={REMOTE}/{BETA_BRANCH}', BETA_BRANCH)
    return result, note
