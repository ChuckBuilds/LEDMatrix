"""
Tests for scripts/install/lib_lowmem.sh, the installer's low-memory helpers.

Background: the rgbmatrix build compiles ~45 C++ translation units, two of them
Cython-generated. Upstream's pyproject.toml sets no [tool.scikit-build] options,
so scikit-build-core drives Ninja at its default of nproc+2 jobs -- six
concurrent cc1plus on a 4-core Pi. On 512MB and 1GB models the OOM killer reaps
the compiler and pip reports only "Failed building wheel for rgbmatrix", which
the installer used to misreport as a missing-build-tools problem.

These cover the pure sizing/detection functions. The swap-management functions
need root and mutate the system, so they are exercised manually instead.
"""

import subprocess
import tempfile
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parent.parent / "scripts" / "install" / "lib_lowmem.sh"


def run_lib(snippet: str, env: dict | None = None) -> subprocess.CompletedProcess:
    """Source the helper library and run a snippet against it."""
    script = f". {LIB}\n{snippet}"
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", **(env or {})},
    )


def _fstype_of(path: object) -> str:
    """Filesystem type backing ``path``, via the same tool the helper uses."""
    result = subprocess.run(
        ["findmnt", "-no", "FSTYPE", "--target", str(path)],
        capture_output=True, text=True,
        env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin"},
    )
    return result.stdout.strip()


def call(fn: str, *args: object, env: dict | None = None) -> str:
    joined = " ".join(str(a) for a in args)
    result = run_lib(f"{fn} {joined}", env=env)
    assert result.returncode == 0, f"{fn} failed: {result.stderr}"
    return result.stdout.strip()


class TestLibraryLoads:
    def test_library_exists_and_is_syntactically_valid(self):
        assert LIB.is_file(), f"{LIB} is missing"
        result = subprocess.run(["bash", "-n", str(LIB)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr

    def test_sourcing_is_safe_under_strict_mode(self):
        # first_time_install.sh runs under `set -Eeuo pipefail` with an ERR
        # trap, so sourcing must not trip either.
        result = run_lib("set -Eeuo pipefail\ntrap 'exit 99' ERR\necho ok")
        assert result.returncode == 0, result.stderr
        assert "ok" in result.stdout


class TestBuildJobs:
    @pytest.mark.parametrize(
        "ram_mb,cores,expected",
        [
            (512, 4, 1),  # Pi Zero 2 W - must serialize
            (1024, 4, 1),  # Pi 3B/3B+ - the device from the bug report
            (2048, 4, 2),
            (4096, 4, 4),  # core-capped
            (8192, 4, 4),  # core-capped
            (2048, 1, 1),  # single-core machine
        ],
    )
    def test_jobs_scale_with_ram_and_cap_at_cores(self, ram_mb, cores, expected):
        assert call("lm_build_jobs", ram_mb, cores) == str(expected)

    def test_never_returns_zero_jobs(self):
        assert call("lm_build_jobs", 0, 4) == "1"

    def test_treats_zero_cores_as_one(self):
        assert call("lm_build_jobs", 8192, 0) == "1"


class TestSwapSizing:
    @pytest.mark.parametrize(
        "ram_mb,existing_swap_mb,expected",
        [
            (512, 0, 2048),  # capped at LM_SWAP_MAX_MB
            (1024, 0, 2048),  # matches the workaround the reporter found
            (2048, 0, 1024),
            (2048, 1024, 0),  # existing swap already covers it
            (4096, 0, 0),  # untouched on machines that already work
            (8192, 0, 0),
        ],
    )
    def test_swap_target_scales_with_ram(self, ram_mb, existing_swap_mb, expected):
        assert call("lm_swap_needed_mb", ram_mb, existing_swap_mb) == str(expected)

    def test_result_is_a_multiple_of_256mb(self):
        # 3072 - 900 = 2172, which must round up rather than produce an odd size.
        assert int(call("lm_swap_needed_mb", 900, 0)) % 256 == 0


class TestSwapDetection:
    def test_zram_swap_is_excluded(self, tmp_path):
        # zram swap is compressed RAM: counting it would let a zram-enabled
        # image skip provisioning and then OOM exactly as before.
        swaps = tmp_path / "swaps"
        swaps.write_text(
            "Filename\t\t\t\tType\t\tSize\t\tUsed\t\tPriority\n"
            "/dev/zram0                              partition\t1048572\t\t0\t\t100\n"
            "/var/swap                               file\t\t524284\t\t0\t\t-2\n"
        )
        assert call("lm_total_swap_mb", env={"LM_SWAPS": str(swaps)}) == "511"

    def test_zram_only_system_reports_no_usable_swap(self, tmp_path):
        swaps = tmp_path / "swaps"
        swaps.write_text(
            "Filename\t\t\t\tType\t\tSize\t\tUsed\t\tPriority\n"
            "/dev/zram0                              partition\t1048572\t\t0\t\t100\n"
        )
        assert call("lm_total_swap_mb", env={"LM_SWAPS": str(swaps)}) == "0"

    def test_ram_is_read_from_meminfo(self, tmp_path):
        meminfo = tmp_path / "meminfo"
        # A real Pi 3B+ reports this; 948204/1024 truncates to 925.
        meminfo.write_text("MemTotal:         948204 kB\nMemFree:  123456 kB\n")
        assert call("lm_total_ram_mb", env={"LM_MEMINFO": str(meminfo)}) == "925"

    def test_missing_files_report_zero_rather_than_failing(self, tmp_path):
        missing = str(tmp_path / "nope")
        assert call("lm_total_ram_mb", env={"LM_MEMINFO": missing}) == "0"
        assert call("lm_total_swap_mb", env={"LM_SWAPS": missing}) == "0"


class TestOomDetection:
    """The regression tests for the misdiagnosis in the bug report."""

    def _check(self, tmp_path, build_log: str, kernel_log: str = "") -> bool:
        build_file = tmp_path / "build.log"
        build_file.write_text(build_log)
        kernel_file = tmp_path / "kernel.log"
        kernel_file.write_text(kernel_log)
        result = run_lib(
            f"lm_build_failed_on_oom {build_file}",
            env={"LM_KERNEL_LOG_FILE": str(kernel_file)},
        )
        return result.returncode == 0

    @pytest.mark.parametrize(
        "line",
        [
            "c++: fatal error: Killed signal terminated program cc1plus",
            "cc1plus: out of memory allocating 65536 bytes",
            "virtual memory exhausted: Cannot allocate memory",
            "error: command '/usr/bin/c++' died with signal 9",
        ],
    )
    def test_detects_compiler_reported_memory_failures(self, tmp_path, line):
        log = f"[15/45] Building CXX object core.cpp.o\n{line}\nninja: build stopped.\n"
        assert self._check(tmp_path, log) is True

    def test_detects_oom_visible_only_in_the_kernel_log(self, tmp_path):
        # The OOM killer writes nothing to the build's stdout. This silence is
        # precisely why the old handler blamed missing build tools.
        build_log = (
            "[15/45] Building CXX object core.cpp.o\n"
            "ninja: build stopped: subcommand failed.\n"
            "ERROR: Failed building wheel for rgbmatrix\n"
        )
        kernel_log = (
            "[12345.6] Out of memory: Killed process 4242 (cc1plus) "
            "total-vm:812345kB, anon-rss:764000kB\n"
        )
        assert self._check(tmp_path, build_log, kernel_log) is True

    def test_does_not_flag_a_genuine_missing_build_tool(self, tmp_path):
        build_log = (
            "CMake Error at CMakeLists.txt:12 (find_package):\n"
            "  Could NOT find Python (missing: Development.Module)\n"
            "fatal error: Python.h: No such file or directory\n"
            "ERROR: Failed building wheel for rgbmatrix\n"
        )
        assert self._check(tmp_path, build_log) is False

    def test_does_not_flag_a_network_failure(self, tmp_path):
        build_log = (
            "WARNING: Retrying after connection broken by 'NewConnectionError'\n"
            "ERROR: Could not install packages due to an OSError\n"
        )
        assert self._check(tmp_path, build_log) is False

    def test_handles_a_missing_build_log(self, tmp_path):
        kernel_file = tmp_path / "kernel.log"
        kernel_file.write_text("")
        result = run_lib(
            f"lm_build_failed_on_oom {tmp_path / 'absent.log'}",
            env={"LM_KERNEL_LOG_FILE": str(kernel_file)},
        )
        assert result.returncode == 1


class TestDiskBackedTmpdir:
    def test_returns_nothing_when_tmpdir_is_already_disk_backed(self, tmp_path):
        # Do not assume tmp_path is disk-backed. Debian 13 -- the platform this
        # helper exists for -- mounts /tmp as tmpfs, and pytest puts tmp_path
        # under /tmp, so this asserted against a *memory*-backed directory and
        # failed on the target platform while the helper behaved exactly as
        # designed. Search for a directory whose backing store is really disk.
        scratch = None
        disk_backed = None
        for candidate in (tmp_path, Path("/var/tmp"), LIB.parent):
            if _fstype_of(candidate) not in ("tmpfs", "ramfs", ""):
                if candidate is tmp_path:
                    disk_backed = candidate
                else:
                    scratch = Path(tempfile.mkdtemp(dir=str(candidate)))
                    disk_backed = scratch
                break
        if disk_backed is None:
            pytest.skip("no disk-backed directory available to test against")
        try:
            assert call("lm_disk_backed_tmpdir",
                        env={"TMPDIR": str(disk_backed)}) == ""
        finally:
            if scratch is not None:
                scratch.rmdir()

    def test_redirects_away_from_a_memory_backed_tmpdir(self):
        # Debian 13 mounts /tmp as tmpfs, which would otherwise hold the whole
        # C++ build tree in RAM alongside the compiler.
        shm = Path("/dev/shm")
        if not shm.is_dir():
            pytest.skip("/dev/shm not available")
        result = run_lib("lm_disk_backed_tmpdir", env={"TMPDIR": str(shm)})
        assert result.returncode == 0
        assert result.stdout.strip() in ("", "/var/tmp")
