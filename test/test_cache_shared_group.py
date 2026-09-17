"""Tests that the web interface can read what the display service caches.

The display service runs as root and the web interface as the installing user;
cache files are 0660, so the web interface reads them only through their group.
The installers made that the directory's group via setgid, but the web unit's
CacheDirectory= let systemd re-own /var/cache/ledmatrix to the web user and its
primary group on first start, erasing it. Every file root wrote afterwards was
root:root 0660:

    WARNING - Permission denied loading cache for display_current_state from
    /var/cache/ledmatrix/display_current_state.json: [Errno 13] Permission denied

Measured on one rig: 365 unreadable files, and the web UI's display status,
on-demand state and plugin health all silently empty.

Most of these need POSIX ownership calls, so they skip on Windows. The ones
that only make sense as root (another user reading, another user's files) skip
without it; run them with `sudo python3 -m pytest test/test_cache_shared_group.py`.
"""

import os
import shutil
import stat
import tempfile
import time
from pathlib import Path

import pytest

from src.cache import disk_cache as disk_cache_module
from src.cache.disk_cache import DiskCache

posix_only = pytest.mark.skipif(
    not hasattr(os, 'fchown') or not hasattr(os, 'geteuid'),
    reason="needs POSIX file ownership")

IS_ROOT = hasattr(os, 'geteuid') and os.geteuid() == 0
root_only = pytest.mark.skipif(not IS_ROOT, reason="needs root")

# Ids nothing on a test host is likely to use, for root-only scenarios.
OTHER_GID = 48213
OTHER_UID = 48214


def _another_group():
    """A group this process may chown its files to, other than its own."""
    if IS_ROOT:
        return OTHER_GID
    for gid in os.getgroups():
        if gid != os.getegid():
            return gid
    pytest.skip("process belongs to no supplementary group")


def _shared_dir(path, gid, mode=0o775):
    """The rig's broken layout: group-writable, no setgid, a foreign group."""
    os.chown(path, -1, gid)
    os.chmod(path, mode)
    assert not os.stat(path).st_mode & stat.S_ISGID
    return path


@posix_only
class TestNewFilesTakeTheDirectoryGroup:
    def test_without_setgid(self, tmp_path):
        gid = _another_group()
        cache = DiskCache(str(_shared_dir(tmp_path, gid)))

        cache.set('display_current_state', {'mode': 'clock'})

        st = os.stat(tmp_path / 'display_current_state.json')
        assert st.st_gid == gid
        assert stat.S_IMODE(st.st_mode) == 0o660

    def test_the_file_is_never_visible_with_a_narrower_mode(self, tmp_path, monkeypatch):
        """mkstemp creates 0600; the rename used to publish it before the chmod."""
        gid = _another_group()
        cache = DiskCache(str(_shared_dir(tmp_path, gid)))
        seen = []
        real_replace = os.replace

        def spy(src, dst):
            st = os.stat(src)
            seen.append((stat.S_IMODE(st.st_mode), st.st_gid))
            return real_replace(src, dst)

        monkeypatch.setattr(disk_cache_module.os, 'replace', spy)
        cache.set('k', {'v': 1})

        assert seen == [(0o660, gid)]

    def test_a_rewrite_fixes_a_file_left_with_the_wrong_group(self, tmp_path):
        gid = _another_group()
        cache = DiskCache(str(_shared_dir(tmp_path, gid)))
        path = tmp_path / 'k.json'
        path.write_text('{}')
        os.chown(path, -1, os.getegid())

        cache.set('k', {'v': 2})

        assert os.stat(path).st_gid == gid

    def test_the_direct_write_fallback_shares_too(self, tmp_path, monkeypatch):
        gid = _another_group()
        cache = DiskCache(str(_shared_dir(tmp_path, gid)))

        def no_temp(*args, **kwargs):
            raise OSError("no temp files")

        monkeypatch.setattr(disk_cache_module.tempfile, 'mkstemp', no_temp)
        cache.set('k', {'v': 3})

        st = os.stat(tmp_path / 'k.json')
        assert (stat.S_IMODE(st.st_mode), st.st_gid) == (0o660, gid)

    def test_a_directory_not_shared_with_its_group_is_left_alone(self, tmp_path):
        gid = _another_group()
        cache = DiskCache(str(_shared_dir(tmp_path, gid, mode=0o755)))

        cache.set('k', {'v': 4})

        assert os.stat(tmp_path / 'k.json').st_gid == os.getegid()


@posix_only
class TestExistingFilesAreRepaired:
    def test_a_root_style_file_becomes_readable(self, tmp_path):
        gid = _another_group()
        cache = DiskCache(str(_shared_dir(tmp_path, gid)))
        path = tmp_path / 'display_on_demand_state.json'
        path.write_text('{}')
        os.chown(path, -1, os.getegid())
        os.chmod(path, 0o600)

        assert cache.share_existing_files() == 1

        st = os.stat(path)
        assert (stat.S_IMODE(st.st_mode), st.st_gid) == (0o660, gid)

    def test_files_already_shared_are_not_counted(self, tmp_path):
        gid = _another_group()
        cache = DiskCache(str(_shared_dir(tmp_path, gid)))
        cache.set('k', {'v': 1})

        assert cache.share_existing_files() == 0

    def test_only_json_files(self, tmp_path):
        gid = _another_group()
        cache = DiskCache(str(_shared_dir(tmp_path, gid)))
        other = tmp_path / 'tile.png'
        other.write_bytes(b'x')
        os.chown(other, -1, os.getegid())

        cache.share_existing_files()

        assert os.stat(other).st_gid == os.getegid()

    def test_a_symlink_is_not_followed(self, tmp_path):
        """The directory is writable by the web user; root must not be aimed elsewhere."""
        gid = _another_group()
        cache_dir = tmp_path / 'cache'
        cache_dir.mkdir()
        _shared_dir(cache_dir, gid)
        outside = tmp_path / 'outside'
        outside.write_text('secret')
        os.chown(outside, -1, os.getegid())
        os.chmod(outside, 0o600)
        (cache_dir / 'evil.json').symlink_to(outside)

        assert DiskCache(str(cache_dir)).share_existing_files() == 0

        st = os.stat(outside)
        assert (stat.S_IMODE(st.st_mode), st.st_gid) == (0o600, os.getegid())

    def test_a_hard_linked_file_is_skipped(self, tmp_path):
        gid = _another_group()
        cache_dir = tmp_path / 'cache'
        cache_dir.mkdir()
        _shared_dir(cache_dir, gid)
        outside = tmp_path / 'outside'
        outside.write_text('secret')
        os.chown(outside, -1, os.getegid())
        os.chmod(outside, 0o600)
        os.link(outside, cache_dir / 'linked.json')

        assert DiskCache(str(cache_dir)).share_existing_files() == 0
        assert stat.S_IMODE(os.stat(outside).st_mode) == 0o600

    @root_only
    def test_another_users_file_is_skipped(self, tmp_path):
        cache = DiskCache(str(_shared_dir(tmp_path, OTHER_GID)))
        path = tmp_path / 'theirs.json'
        path.write_text('{}')
        os.chown(path, OTHER_UID, 0)
        os.chmod(path, 0o600)

        assert cache.share_existing_files() == 0
        assert os.stat(path).st_gid == 0


@posix_only
@root_only
def test_a_non_root_member_of_the_group_can_read_what_root_wrote():
    """The rig, end to end: root writes, the web user reads."""
    # Not tmp_path: pytest's root-owned base directory is 0700, which the
    # reader could not traverse no matter what the cache file's group was.
    base = tempfile.mkdtemp()
    try:
        _root_writes_web_user_reads(Path(base))
    finally:
        shutil.rmtree(base, ignore_errors=True)


def _root_writes_web_user_reads(base):
    os.chmod(base, 0o755)
    cache_dir = base / 'cache'
    cache_dir.mkdir()
    # What systemd's CacheDirectory= left behind: the web user's own group.
    os.chown(cache_dir, OTHER_UID, OTHER_GID)
    os.chmod(cache_dir, 0o775)
    DiskCache(str(cache_dir)).set('display_current_state', {'mode': 'clock'})

    pid = os.fork()
    if pid == 0:  # pragma: no cover - child
        code = 1
        try:
            os.setgroups([])
            os.setgid(OTHER_GID)
            os.setuid(OTHER_UID)
            with open(cache_dir / 'display_current_state.json', 'rb') as f:
                code = 0 if b'clock' in f.read() else 2
        except PermissionError:
            code = 13
        finally:
            os._exit(code)
    _, status = os.waitpid(pid, 0)
    assert os.WEXITSTATUS(status) == 0, "web user could not read the display's cache file"


def test_the_web_unit_does_not_let_systemd_re_own_the_cache():
    template = Path(__file__).resolve().parent.parent / 'systemd' / 'ledmatrix-web.service'
    directives = [line.strip() for line in template.read_text(encoding='utf-8').splitlines()
                  if line.strip() and not line.strip().startswith('#')]
    offending = [d for d in directives if d.startswith(('CacheDirectory', 'StateDirectory'))]
    assert not offending, (
        f"{offending}: systemd re-owns that directory to the web user, and the "
        "display service (root) shares it")


def test_the_cleanup_thread_repairs_existing_files(tmp_path, monkeypatch):
    from src.cache_manager import CacheManager

    calls = []
    monkeypatch.setattr(CacheManager, '_get_writable_cache_dir', lambda self: str(tmp_path))
    monkeypatch.setattr(DiskCache, 'share_existing_files',
                        lambda self: calls.append(self.cache_dir) or 0)
    CacheManager._cleanup_owners.clear()
    manager = CacheManager()
    try:
        deadline = time.monotonic() + 5
        while not calls and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        manager.stop_cleanup_thread()
        CacheManager._cleanup_owners.clear()

    assert calls == [str(tmp_path)]
