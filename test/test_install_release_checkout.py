"""New installs run the newest release; re-running the installer never moves backwards.

#684 made devices update along a channel -- stable follows the newest vX.Y.Z
tag, beta follows main -- but a new install still cloned main's tip, so it ran
unreleased code until the next release caught up with it. The one-shot
installer (scripts/install/one-shot-install.sh, which is where the clone
happens) now checks out the newest release after cloning, unless
LEDMATRIX_CHANNEL=beta. Re-running it on an existing checkout moves a stable
device forward to the newest release only when that release contains its
commit, as update_channel.checkout_release() does, and leaves beta devices
(and stable ones newer than every release) on the fast-forward pull they
always had. first_time_install.sh writes an explicitly chosen channel
(--beta / LEDMATRIX_CHANNEL) into config.json.

These run the installer's own bash, under its strict mode, against real git
repositories.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
ONE_SHOT = ROOT / "scripts" / "install" / "one-shot-install.sh"
INSTALLER = ROOT / "first_time_install.sh"
BEGIN = "# --- release checkout helpers"
END = "# --- end release checkout helpers"

from web_interface import update_channel  # noqa: E402

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="runs the installer's bash under Linux"
)


def helper_block() -> str:
    text = ONE_SHOT.read_text(encoding="utf-8")
    assert text.count(BEGIN) == 1 and text.count(END) == 1, "helper block markers missing or duplicated"
    return text[text.index(BEGIN): text.index(END)]


def git(*args, cwd, env):
    result = subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True)
    assert result.returncode == 0, f"git {' '.join(args)} failed: {result.stderr}"
    return result.stdout.strip()


@pytest.fixture
def git_env(tmp_path):
    config = tmp_path / "gitconfig"
    config.write_text(
        "[user]\n\tname = t\n\temail = t@t\n"
        "[protocol \"file\"]\n\tallow = always\n"
        "[init]\n\tdefaultBranch = main\n"
        "[advice]\n\tdetachedHead = false\n",
        encoding="utf-8",
    )
    return {
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "HOME": str(tmp_path),
        "GIT_CONFIG_GLOBAL": str(config),
        "GIT_CONFIG_NOSYSTEM": "1",
    }


#: Tags and the commit (index into the history) each points at. The newest
#: release is v3.10.0: 10 > 8 numerically, the rc and the zero-padded tag are
#: not releases, and v3.12 and nightly are not vX.Y.Z at all.
TAGS = {
    "v3.7.0": 0, "v3.8.0": 1, "v3.10.0": 2,
    "v3.11.0-rc1": 3, "v03.12.0": 3, "v3.12": 3, "nightly": 3,
}
NEWEST = "v3.10.0"


@pytest.fixture
def origin(tmp_path, git_env):
    """A stand-in for GitHub: five commits on main (the last newer than any release)."""
    seed = tmp_path / "seed"
    seed.mkdir()
    git("init", "-q", ".", cwd=seed, env=git_env)
    commits = []
    for i in range(5):
        (seed / "version.txt").write_text(str(i), encoding="utf-8")
        git("add", ".", cwd=seed, env=git_env)
        git("commit", "-qm", f"c{i}", cwd=seed, env=git_env)
        commits.append(git("rev-parse", "HEAD", cwd=seed, env=git_env))
    for tag, index in TAGS.items():
        git("tag", tag, commits[index], cwd=seed, env=git_env)
    bare = tmp_path / "origin.git"
    git("clone", "-q", "--bare", str(seed), str(bare), cwd=tmp_path, env=git_env)
    return bare, commits, seed


def run_block(snippet, cwd, env, channel=None):
    env = dict(env)
    if channel is not None:
        env["LEDMATRIX_CHANNEL"] = channel
    script = (
        "set -Eeuo pipefail\n"
        "trap 'echo ERR_TRAP_FIRED >&2; exit 99' ERR\n"
        'print_success() { echo "OK: $*"; }\n'
        'print_warning() { echo "W: $*"; }\n'
        f"{helper_block()}\n"
        f"{snippet}\n"
    )
    result = subprocess.run(["bash", "-c", script], cwd=cwd, capture_output=True, text=True, env=env)
    assert "ERR_TRAP_FIRED" not in result.stderr, result.stdout + result.stderr
    return result


def clone(origin, tmp_path, env, name="LEDMatrix"):
    bare, _, _ = origin
    target = tmp_path / name
    git("clone", "-q", str(bare), str(target), cwd=tmp_path, env=env)
    return target


def head(repo, env):
    return git("rev-parse", "HEAD", cwd=repo, env=env)


def branch(repo, env):
    result = subprocess.run(["git", "symbolic-ref", "--quiet", "--short", "HEAD"], cwd=repo, env=env,
                            capture_output=True, text=True)
    return result.stdout.strip()


def set_channel(repo, channel):
    (repo / "config").mkdir(exist_ok=True)
    (repo / "config" / "config.json").write_text(
        json.dumps({"auto_update": {"enabled": False, "channel": channel}}), encoding="utf-8")


# -- a fresh install -------------------------------------------------------------

def test_a_fresh_clone_checks_out_the_newest_release(origin, tmp_path, git_env):
    _, commits, _ = origin
    repo = clone(origin, tmp_path, git_env)
    out = run_block("_lm_checkout_release_after_clone", repo, git_env)
    assert out.returncode == 0
    assert head(repo, git_env) == commits[TAGS[NEWEST]]
    assert branch(repo, git_env) == "", "a release is checked out detached, as Update Code does"
    assert f"Installing release {NEWEST}" in out.stdout


@pytest.mark.parametrize("channel", ["beta", "BETA", " beta "])
def test_a_fresh_beta_install_stays_on_main(origin, tmp_path, git_env, channel):
    _, commits, _ = origin
    repo = clone(origin, tmp_path, git_env)
    run_block("_lm_checkout_release_after_clone", repo, git_env, channel=channel)
    assert head(repo, git_env) == commits[-1] and branch(repo, git_env) == "main"


def test_an_unknown_channel_falls_back_to_stable_and_says_so(origin, tmp_path, git_env):
    _, commits, _ = origin
    repo = clone(origin, tmp_path, git_env)
    out = run_block("_lm_checkout_release_after_clone", repo, git_env, channel="nightly")
    assert head(repo, git_env) == commits[TAGS[NEWEST]]
    assert "not stable or beta" in out.stdout + out.stderr


def test_a_repository_without_releases_installs_main(tmp_path, git_env):
    seed = tmp_path / "seed"
    seed.mkdir()
    git("init", "-q", ".", cwd=seed, env=git_env)
    (seed / "f").write_text("x", encoding="utf-8")
    git("add", ".", cwd=seed, env=git_env)
    git("commit", "-qm", "only", cwd=seed, env=git_env)
    git("tag", "v3.0", cwd=seed, env=git_env)  # not a release tag
    repo = tmp_path / "LEDMatrix"
    git("clone", "-q", str(seed), str(repo), cwd=tmp_path, env=git_env)
    out = run_block("_lm_checkout_release_after_clone", repo, git_env)
    assert branch(repo, git_env) == "main" and "No release found" in out.stdout


# -- re-running on an existing checkout -----------------------------------------------

def existing(origin, tmp_path, env, at, detached):
    """An installed checkout, at commit index ``at``, on main or detached."""
    _, commits, _ = origin
    repo = clone(origin, tmp_path, env)
    if detached:
        git("checkout", "-q", "--detach", commits[at], cwd=repo, env=env)
    else:
        git("reset", "-q", "--hard", commits[at], cwd=repo, env=env)
    return repo


def update(repo, env, channel=None):
    out = run_block("if _lm_update_existing_checkout; then echo RESULT=handled; "
                    "else echo RESULT=pull; fi", repo, env, channel=channel)
    return re.search(r"RESULT=(\w+)", out.stdout).group(1), out


def test_a_device_on_an_older_release_moves_to_the_newest(origin, tmp_path, git_env):
    _, commits, _ = origin
    repo = existing(origin, tmp_path, git_env, at=TAGS["v3.7.0"], detached=True)
    result, out = update(repo, git_env)
    assert result == "handled"
    assert head(repo, git_env) == commits[TAGS[NEWEST]]
    assert f"Updated to release {NEWEST}" in out.stdout


def test_a_device_already_on_the_newest_release_stays(origin, tmp_path, git_env):
    _, commits, _ = origin
    repo = existing(origin, tmp_path, git_env, at=TAGS[NEWEST], detached=True)
    result, out = update(repo, git_env)
    assert result == "handled" and head(repo, git_env) == commits[TAGS[NEWEST]]
    assert "Already on the newest release" in out.stdout


def test_a_device_on_main_behind_the_newest_release_moves_to_it(origin, tmp_path, git_env):
    _, commits, _ = origin
    repo = existing(origin, tmp_path, git_env, at=TAGS["v3.8.0"], detached=False)
    result, _ = update(repo, git_env)
    assert result == "handled" and head(repo, git_env) == commits[TAGS[NEWEST]]


def test_a_device_on_main_newer_than_every_release_is_not_moved_back(origin, tmp_path, git_env):
    """It keeps the fast-forward pull it always had, and waits for a release to contain it."""
    _, commits, _ = origin
    repo = existing(origin, tmp_path, git_env, at=4, detached=False)
    result, _ = update(repo, git_env)
    assert result == "pull"
    assert head(repo, git_env) == commits[4] and branch(repo, git_env) == "main"


def test_a_detached_device_newer_than_every_release_is_left_alone(origin, tmp_path, git_env):
    _, commits, _ = origin
    repo = existing(origin, tmp_path, git_env, at=3, detached=True)
    result, out = update(repo, git_env)
    assert result == "handled" and head(repo, git_env) == commits[3]
    assert "newer than the newest release" in out.stdout


def test_a_higher_version_on_an_older_commit_is_not_a_downgrade(origin, tmp_path, git_env):
    """Newest by version is not newest by history: never move to a tag that does not contain HEAD."""
    bare, commits, seed = origin
    git("tag", "v9.0.0", commits[0], cwd=seed, env=git_env)
    git("push", "-q", str(bare), "v9.0.0", cwd=seed, env=git_env)
    repo = existing(origin, tmp_path, git_env, at=TAGS[NEWEST], detached=True)
    result, _ = update(repo, git_env)
    assert result == "handled" and head(repo, git_env) == commits[TAGS[NEWEST]]


def test_a_new_release_published_since_the_clone_is_fetched(origin, tmp_path, git_env):
    bare, commits, seed = origin
    repo = existing(origin, tmp_path, git_env, at=TAGS[NEWEST], detached=True)
    git("tag", "v3.11.0", commits[4], cwd=seed, env=git_env)
    git("push", "-q", str(bare), "v3.11.0", cwd=seed, env=git_env)
    result, _ = update(repo, git_env)
    assert result == "handled" and head(repo, git_env) == commits[4]


def test_a_beta_device_keeps_its_pull(origin, tmp_path, git_env):
    _, commits, _ = origin
    repo = existing(origin, tmp_path, git_env, at=TAGS["v3.8.0"], detached=False)
    set_channel(repo, "beta")
    result, _ = update(repo, git_env)
    assert result == "pull" and head(repo, git_env) == commits[TAGS["v3.8.0"]]


def test_the_environment_overrides_the_configured_channel(origin, tmp_path, git_env):
    _, commits, _ = origin
    repo = existing(origin, tmp_path, git_env, at=TAGS["v3.8.0"], detached=False)
    set_channel(repo, "stable")
    result, _ = update(repo, git_env, channel="beta")
    assert result == "pull" and head(repo, git_env) == commits[TAGS["v3.8.0"]]


def test_local_edits_that_block_the_move_keep_the_checkout(origin, tmp_path, git_env):
    _, commits, _ = origin
    repo = existing(origin, tmp_path, git_env, at=TAGS["v3.7.0"], detached=True)
    (repo / "version.txt").write_text("my edit", encoding="utf-8")
    result, out = update(repo, git_env)
    assert result == "handled" and head(repo, git_env) == commits[TAGS["v3.7.0"]]
    assert (repo / "version.txt").read_text(encoding="utf-8") == "my edit"
    assert "Could not move to release" in out.stdout


def test_an_unreachable_origin_keeps_the_checkout(origin, tmp_path, git_env):
    _, commits, _ = origin
    repo = existing(origin, tmp_path, git_env, at=TAGS["v3.7.0"], detached=True)
    git("remote", "set-url", "origin", str(tmp_path / "gone.git"), cwd=repo, env=git_env)
    result, out = update(repo, git_env)
    assert result == "handled" and head(repo, git_env) == commits[TAGS["v3.7.0"]]
    assert "Could not fetch" in out.stdout


# -- same rules as the web interface -------------------------------------------------

NAMES = ["v1.2.3", "v1.10.0", "v1.9.9", "v2.0.0-rc1", "v02.0.0", "v2.0", "v10.0.0", "v9.99.99",
         "release-11", "v10.0.0+build", "v0.0.0", "v10.0.1", "v1.2.03", "V11.0.0"]


@pytest.mark.parametrize("subset", [NAMES, NAMES[:4], ["v2.0", "nightly"], NAMES[::-1][:6]])
def test_the_newest_tag_matches_update_channel(tmp_path, git_env, subset):
    repo = tmp_path / "tags"
    repo.mkdir()
    git("init", "-q", ".", cwd=repo, env=git_env)
    git("commit", "-q", "--allow-empty", "-m", "x", cwd=repo, env=git_env)
    for name in subset:
        git("tag", name, cwd=repo, env=git_env)
    out = run_block("_lm_newest_release_tag", repo, git_env).stdout.strip()
    assert out == (update_channel.newest_release_tag(subset) or "")


# -- wiring --------------------------------------------------------------------------

def test_every_clone_is_followed_by_the_release_checkout():
    text = ONE_SHOT.read_text(encoding="utf-8")
    clones = [m.start() for m in re.finditer(r'retry git clone "\$REPO_URL" "\$REPO_DIR"\n', text)]
    assert clones
    for pos in clones:
        following = text[pos:].splitlines()[1]
        assert '_lm_checkout_release_after_clone' in following, following


def test_the_existing_checkout_is_handled_before_the_old_pull():
    text = ONE_SHOT.read_text(encoding="utf-8")
    assert re.search(r'if _lm_update_existing_checkout; then\n\s+PULL_SUCCESS=true\n'
                     r'\s+elif git pull --ff-only origin "\$CURRENT_BRANCH"', text)


def test_the_one_shot_passes_the_channel_to_the_installer():
    text = ONE_SHOT.read_text(encoding="utf-8")
    assert 'LEDMATRIX_CHANNEL="${LEDMATRIX_CHANNEL:-}"' in text


# -- first_time_install.sh records the chosen channel ----------------------------------

CHANNEL_BEGIN = 'case "$UPDATE_CHANNEL" in'
CHANNEL_END = 'set it from the General tab instead"\n    fi\nfi\n'


def channel_block():
    text = INSTALLER.read_text(encoding="utf-8")
    start = text.index(CHANNEL_BEGIN)
    return text[start: text.index(CHANNEL_END, start) + len(CHANNEL_END)]


@pytest.mark.parametrize("auto_update, channel, expected", [
    ("", "beta", {"enabled": False, "channel": "beta"}),
    ("", "stable", {"enabled": False, "channel": "stable"}),
    ("1", "", {"enabled": True, "channel": "stable"}),
    ("", "", {"enabled": False, "channel": "stable"}),       # nothing asked: untouched
    ("", "nightly", {"enabled": False, "channel": "stable"}),  # nonsense: untouched
])
def test_the_installer_writes_only_an_explicit_channel(tmp_path, auto_update, channel, expected):
    (tmp_path / "config").mkdir()
    config = tmp_path / "config" / "config.json"
    config.write_text(json.dumps({"auto_update": {"enabled": False, "channel": "stable"}, "x": 1}))
    script = (f'set -Eeuo pipefail\nPROJECT_ROOT_DIR="{tmp_path}"\nAUTO_UPDATE="{auto_update}"\n'
              f'UPDATE_CHANNEL="{channel}"\n{channel_block()}')
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    data = json.loads(config.read_text())
    assert data["auto_update"] == expected and data["x"] == 1


def test_the_installer_accepts_beta_as_a_flag_and_from_the_environment():
    text = INSTALLER.read_text(encoding="utf-8")
    assert re.search(r"^\s*--beta\) UPDATE_CHANNEL=beta ;;", text, re.M)
    assert 'UPDATE_CHANNEL=$(printf \'%s\' "${LEDMATRIX_CHANNEL:-}"' in text
    assert "LEDMATRIX_CHANNEL=stable|beta" in text, "documented in --help"
