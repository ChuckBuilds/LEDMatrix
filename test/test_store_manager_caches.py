"""
Tests for the caching and tombstone behaviors added to PluginStoreManager
to fix the plugin-list slowness and the uninstall-resurrection bugs.

Coverage targets:
- ``mark_recently_uninstalled`` / ``was_recently_uninstalled`` lifecycle and
  TTL expiry.
- ``_get_local_git_info`` mtime-gated cache: ``git`` subprocesses only run
  when ``.git/HEAD`` mtime changes.
- ``fetch_registry`` stale-cache fallback on network failure.
"""

import os
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch, MagicMock

from src.plugin_system.store_manager import PluginStoreManager


class TestUninstallTombstone(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.sm = PluginStoreManager(plugins_dir=self._tmp.name)

    def test_unmarked_plugin_is_not_recent(self):
        self.assertFalse(self.sm.was_recently_uninstalled("foo"))

    def test_marking_makes_it_recent(self):
        self.sm.mark_recently_uninstalled("foo")
        self.assertTrue(self.sm.was_recently_uninstalled("foo"))

    def test_tombstone_expires_after_ttl(self):
        self.sm._uninstall_tombstone_ttl = 0.05
        self.sm.mark_recently_uninstalled("foo")
        self.assertTrue(self.sm.was_recently_uninstalled("foo"))
        time.sleep(0.1)
        self.assertFalse(self.sm.was_recently_uninstalled("foo"))
        # Expired entry should also be pruned from the dict.
        self.assertNotIn("foo", self.sm._uninstall_tombstones)


class TestGitInfoCache(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.plugins_dir = Path(self._tmp.name)
        self.sm = PluginStoreManager(plugins_dir=str(self.plugins_dir))

        # Minimal fake git checkout: .git/HEAD needs to exist so the cache
        # key (its mtime) is stable, but we mock subprocess so no actual git
        # is required.
        self.plugin_path = self.plugins_dir / "plg"
        (self.plugin_path / ".git").mkdir(parents=True)
        (self.plugin_path / ".git" / "HEAD").write_text("ref: refs/heads/main\n")

    def _fake_subprocess_run(self, *args, **kwargs):
        # Return different dummy values depending on which git subcommand
        # was invoked so the code paths that parse output all succeed.
        cmd = args[0]
        result = MagicMock()
        result.returncode = 0
        if "rev-parse" in cmd and "HEAD" in cmd and "--abbrev-ref" not in cmd:
            result.stdout = "abcdef1234567890\n"
        elif "--abbrev-ref" in cmd:
            result.stdout = "main\n"
        elif "config" in cmd:
            result.stdout = "https://example.com/repo.git\n"
        elif "log" in cmd:
            result.stdout = "2026-04-08T12:00:00+00:00\n"
        else:
            result.stdout = ""
        return result

    def test_cache_hits_avoid_subprocess_calls(self):
        with patch(
            "src.plugin_system.store_manager.subprocess.run",
            side_effect=self._fake_subprocess_run,
        ) as mock_run:
            first = self.sm._get_local_git_info(self.plugin_path)
            self.assertIsNotNone(first)
            self.assertEqual(first["short_sha"], "abcdef1")
            calls_after_first = mock_run.call_count
            self.assertEqual(calls_after_first, 4)

            # Second call with unchanged HEAD: zero new subprocess calls.
            second = self.sm._get_local_git_info(self.plugin_path)
            self.assertEqual(second, first)
            self.assertEqual(mock_run.call_count, calls_after_first)

    def test_cache_invalidates_on_head_mtime_change(self):
        with patch(
            "src.plugin_system.store_manager.subprocess.run",
            side_effect=self._fake_subprocess_run,
        ) as mock_run:
            self.sm._get_local_git_info(self.plugin_path)
            calls_after_first = mock_run.call_count

            # Bump mtime on .git/HEAD to simulate a new commit being checked out.
            head = self.plugin_path / ".git" / "HEAD"
            new_time = head.stat().st_mtime + 10
            os.utime(head, (new_time, new_time))

            self.sm._get_local_git_info(self.plugin_path)
            self.assertEqual(mock_run.call_count, calls_after_first + 4)

    def test_no_git_directory_returns_none(self):
        non_git = self.plugins_dir / "no_git"
        non_git.mkdir()
        self.assertIsNone(self.sm._get_local_git_info(non_git))

    def test_cache_invalidates_on_fast_forward_of_current_branch(self):
        """Regression: .git/HEAD mtime alone is not enough.

        ``git pull`` that fast-forwards the current branch touches
        ``.git/refs/heads/<branch>`` (or packed-refs) but NOT HEAD. If
        we cache on HEAD mtime alone, we serve a stale SHA indefinitely.
        """
        # Build a realistic loose-ref layout.
        refs_heads = self.plugin_path / ".git" / "refs" / "heads"
        refs_heads.mkdir(parents=True)
        branch_file = refs_heads / "main"
        branch_file.write_text("a" * 40 + "\n")
        # Overwrite HEAD to point at refs/heads/main.
        (self.plugin_path / ".git" / "HEAD").write_text("ref: refs/heads/main\n")

        call_log = []

        def fake_subprocess_run(*args, **kwargs):
            call_log.append(args[0])
            result = MagicMock()
            result.returncode = 0
            cmd = args[0]
            if "rev-parse" in cmd and "--abbrev-ref" not in cmd:
                result.stdout = branch_file.read_text().strip() + "\n"
            elif "--abbrev-ref" in cmd:
                result.stdout = "main\n"
            elif "config" in cmd:
                result.stdout = "https://example.com/repo.git\n"
            elif "log" in cmd:
                result.stdout = "2026-04-08T12:00:00+00:00\n"
            else:
                result.stdout = ""
            return result

        with patch(
            "src.plugin_system.store_manager.subprocess.run",
            side_effect=fake_subprocess_run,
        ):
            first = self.sm._get_local_git_info(self.plugin_path)
            calls_after_first = len(call_log)
            self.assertIsNotNone(first)
            self.assertTrue(first["sha"].startswith("a"))

            # Second call: unchanged. Cache hit → no new subprocess calls.
            self.sm._get_local_git_info(self.plugin_path)
            self.assertEqual(len(call_log), calls_after_first,
                             "cache should hit on unchanged state")

            # Simulate a fast-forward: the branch ref file gets a new SHA
            # and a new mtime, but .git/HEAD is untouched.
            branch_file.write_text("b" * 40 + "\n")
            new_time = branch_file.stat().st_mtime + 10
            os.utime(branch_file, (new_time, new_time))

            second = self.sm._get_local_git_info(self.plugin_path)
            # Cache MUST have been invalidated — we should have re-run git.
            self.assertGreater(
                len(call_log), calls_after_first,
                "cache should have invalidated on branch ref update",
            )
            self.assertTrue(second["sha"].startswith("b"))


class TestSearchPluginsParallel(unittest.TestCase):
    """Plugin Store browse path — the per-plugin GitHub enrichment used to
    run serially, turning a browse of 15 plugins into 30–45 sequential HTTP
    requests on a cold cache. This batch of tests locks in the parallel
    fan-out and verifies output shape/ordering haven't regressed.
    """

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.sm = PluginStoreManager(plugins_dir=self._tmp.name)

        # Fake registry with 5 plugins.
        self.registry = {
            "plugins": [
                {"id": f"plg{i}", "name": f"Plugin {i}",
                 "repo": f"https://github.com/owner/plg{i}", "category": "util"}
                for i in range(5)
            ]
        }
        self.sm.registry_cache = self.registry
        self.sm.registry_cache_time = time.time()

        self._enrich_calls = []

        def fake_repo(repo_url):
            self._enrich_calls.append(("repo", repo_url))
            return {"stars": 1, "default_branch": "main",
                    "last_commit_iso": "2026-04-08T00:00:00Z",
                    "last_commit_date": "2026-04-08"}

        def fake_commit(repo_url, branch):
            self._enrich_calls.append(("commit", repo_url, branch))
            return {"short_sha": "abc1234", "sha": "abc1234" + "0" * 33,
                    "date_iso": "2026-04-08T00:00:00Z", "date": "2026-04-08",
                    "message": "m", "author": "a", "branch": branch}

        def fake_manifest(repo_url, branch, manifest_path):
            self._enrich_calls.append(("manifest", repo_url, branch))
            return {"description": "desc"}

        self.sm._get_github_repo_info = fake_repo
        self.sm._get_latest_commit_info = fake_commit
        self.sm._fetch_manifest_from_github = fake_manifest

    def test_results_preserve_registry_order(self):
        results = self.sm.search_plugins(include_saved_repos=False)
        self.assertEqual([p["id"] for p in results],
                         [f"plg{i}" for i in range(5)])

    def test_filters_applied_before_enrichment(self):
        # Filter down to a single plugin via category — ensures we don't
        # waste GitHub calls enriching plugins that won't be returned.
        self.registry["plugins"][2]["category"] = "special"
        self.sm.registry_cache = self.registry
        self._enrich_calls.clear()
        results = self.sm.search_plugins(category="special", include_saved_repos=False)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], "plg2")
        # Only one plugin should have been enriched.
        repo_calls = [c for c in self._enrich_calls if c[0] == "repo"]
        self.assertEqual(len(repo_calls), 1)

    def test_enrichment_runs_concurrently(self):
        """Verify the thread pool actually runs fetches in parallel.

        Deterministic check: each stub repo fetch holds a lock while it
        increments a "currently running" counter, then sleeps briefly,
        then decrements. If execution is serial, the peak counter can
        never exceed 1. If the thread pool is engaged, we see at least
        2 concurrent workers.

        We deliberately do NOT assert on elapsed wall time — that check
        was flaky on low-power / CI boxes where scheduler noise dwarfed
        the 50ms-per-worker budget. ``peak["count"] >= 2`` is the signal
        we actually care about.
        """
        import threading
        peak_lock = threading.Lock()
        peak = {"count": 0, "current": 0}

        def slow_repo(repo_url):
            with peak_lock:
                peak["current"] += 1
                if peak["current"] > peak["count"]:
                    peak["count"] = peak["current"]
            # Small sleep gives other workers a chance to enter the
            # critical section before we leave it. 50ms is large enough
            # to dominate any scheduling jitter without slowing the test
            # suite meaningfully.
            time.sleep(0.05)
            with peak_lock:
                peak["current"] -= 1
            return {"stars": 0, "default_branch": "main",
                    "last_commit_iso": "", "last_commit_date": ""}

        self.sm._get_github_repo_info = slow_repo
        self.sm._get_latest_commit_info = lambda *a, **k: None
        self.sm._fetch_manifest_from_github = lambda *a, **k: None

        results = self.sm.search_plugins(fetch_commit_info=False, include_saved_repos=False)

        self.assertEqual(len(results), 5)
        self.assertGreaterEqual(
            peak["count"], 2,
            "no concurrent fetches observed — thread pool not engaging",
        )


class TestStaleOnErrorFallbacks(unittest.TestCase):
    """When GitHub is unreachable, previously-cached values should still be
    returned rather than zero/None. Important on Pi's WiFi links.
    """

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.sm = PluginStoreManager(plugins_dir=self._tmp.name)

    def test_repo_info_stale_on_network_error(self):
        cache_key = "owner/repo"
        good = {"stars": 42, "default_branch": "main",
                "last_commit_iso": "", "last_commit_date": "",
                "forks": 0, "open_issues": 0, "updated_at_iso": "",
                "language": "", "license": ""}
        # Seed the cache with a known-good value, then force expiry.
        self.sm.github_cache[cache_key] = (time.time() - 10_000, good)
        self.sm.cache_timeout = 1  # force re-fetch

        import requests as real_requests
        with patch("src.plugin_system.store_manager.requests.get",
                   side_effect=real_requests.ConnectionError("boom")):
            result = self.sm._get_github_repo_info("https://github.com/owner/repo")
        self.assertEqual(result["stars"], 42)

    def test_repo_info_stale_bumps_timestamp_into_backoff(self):
        """Regression: after serving stale, next lookup must hit cache.

        Without the failure-backoff timestamp bump, a repeat request
        would see the cache as still expired and re-hit the network,
        amplifying the original failure. The fix is to update the
        cached entry's timestamp so ``(now - ts) < cache_timeout`` holds
        for the backoff window.
        """
        cache_key = "owner/repo"
        good = {"stars": 99, "default_branch": "main",
                "last_commit_iso": "", "last_commit_date": "",
                "forks": 0, "open_issues": 0, "updated_at_iso": "",
                "language": "", "license": ""}
        self.sm.github_cache[cache_key] = (time.time() - 10_000, good)
        self.sm.cache_timeout = 1
        self.sm._failure_backoff_seconds = 60

        import requests as real_requests
        call_count = {"n": 0}

        def counting_get(*args, **kwargs):
            call_count["n"] += 1
            raise real_requests.ConnectionError("boom")

        with patch("src.plugin_system.store_manager.requests.get", side_effect=counting_get):
            first = self.sm._get_github_repo_info("https://github.com/owner/repo")
            self.assertEqual(first["stars"], 99)
            self.assertEqual(call_count["n"], 1)

            # Second call must hit the bumped cache and NOT make another request.
            second = self.sm._get_github_repo_info("https://github.com/owner/repo")
            self.assertEqual(second["stars"], 99)
            self.assertEqual(
                call_count["n"], 1,
                "stale-cache fallback must bump the timestamp to avoid "
                "re-retrying on every request during the backoff window",
            )

    def test_repo_info_stale_on_403_also_backs_off(self):
        """Same backoff requirement for 403 rate-limit responses."""
        cache_key = "owner/repo"
        good = {"stars": 7, "default_branch": "main",
                "last_commit_iso": "", "last_commit_date": "",
                "forks": 0, "open_issues": 0, "updated_at_iso": "",
                "language": "", "license": ""}
        self.sm.github_cache[cache_key] = (time.time() - 10_000, good)
        self.sm.cache_timeout = 1

        rate_limited = MagicMock()
        rate_limited.status_code = 403
        rate_limited.text = "rate limited"
        call_count = {"n": 0}

        def counting_get(*args, **kwargs):
            call_count["n"] += 1
            return rate_limited

        with patch("src.plugin_system.store_manager.requests.get", side_effect=counting_get):
            self.sm._get_github_repo_info("https://github.com/owner/repo")
            self.assertEqual(call_count["n"], 1)
            self.sm._get_github_repo_info("https://github.com/owner/repo")
            self.assertEqual(
                call_count["n"], 1,
                "403 stale fallback must also bump the timestamp",
            )

    def test_commit_info_stale_on_network_error(self):
        cache_key = "owner/repo:main"
        good = {"branch": "main", "sha": "a" * 40, "short_sha": "aaaaaaa",
                "date_iso": "2026-04-08T00:00:00Z", "date": "2026-04-08",
                "author": "x", "message": "y"}
        self.sm.commit_info_cache[cache_key] = (time.time() - 10_000, good)
        self.sm.commit_cache_timeout = 1  # force re-fetch

        import requests as real_requests
        with patch("src.plugin_system.store_manager.requests.get",
                   side_effect=real_requests.ConnectionError("boom")):
            result = self.sm._get_latest_commit_info(
                "https://github.com/owner/repo", branch="main"
            )
        self.assertIsNotNone(result)
        self.assertEqual(result["short_sha"], "aaaaaaa")

    def test_commit_info_preserves_good_cache_on_all_branches_404(self):
        """Regression: all-branches-404 used to overwrite good cache with None.

        The previous implementation unconditionally wrote
        ``self.commit_info_cache[cache_key] = (time.time(), None)`` after
        the branch loop, which meant a single transient failure (e.g. an
        odd 5xx or an ls-refs hiccup) wiped out the commit info we had
        just served to the UI the previous minute.
        """
        cache_key = "owner/repo:main"
        good = {"branch": "main", "sha": "a" * 40, "short_sha": "aaaaaaa",
                "date_iso": "2026-04-08T00:00:00Z", "date": "2026-04-08",
                "author": "x", "message": "y"}
        self.sm.commit_info_cache[cache_key] = (time.time() - 10_000, good)
        self.sm.commit_cache_timeout = 1

        # Each branches_to_try attempt returns a 404. No network error
        # exception — just a non-200 response. This is the code path
        # that used to overwrite the cache with None.
        not_found = MagicMock()
        not_found.status_code = 404
        not_found.text = "Not Found"
        with patch("src.plugin_system.store_manager.requests.get", return_value=not_found):
            result = self.sm._get_latest_commit_info(
                "https://github.com/owner/repo", branch="main"
            )

        self.assertIsNotNone(result, "good cache was wiped out by transient 404s")
        self.assertEqual(result["short_sha"], "aaaaaaa")
        # The cache entry must still be the good value, not None.
        self.assertIsNotNone(self.sm.commit_info_cache[cache_key][1])


class TestInstallUpdateUninstallInvariants(unittest.TestCase):
    """Regression guard: the caching and tombstone work added in this PR
    must not break the install / update / uninstall code paths.

    Specifically:
    - ``install_plugin`` bypasses commit/manifest caches via force_refresh,
      so the 5→30 min TTL bump cannot cause users to install a stale commit.
    - ``update_plugin`` does the same.
    - The uninstall tombstone is only honored by the state reconciler, not
      by explicit ``install_plugin`` calls — so a user can uninstall and
      immediately reinstall from the store UI without the tombstone getting
      in the way.
    - ``was_recently_uninstalled`` is not touched by ``install_plugin``.
    """

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.sm = PluginStoreManager(plugins_dir=self._tmp.name)

    def test_get_plugin_info_with_force_refresh_forwards_to_commit_fetch(self):
        """install_plugin's code path must reach the network bypass."""
        self.sm.registry_cache = {
            "plugins": [{"id": "foo", "repo": "https://github.com/o/r"}]
        }
        self.sm.registry_cache_time = time.time()

        repo_calls = []
        commit_calls = []
        manifest_calls = []

        def fake_repo(url):
            repo_calls.append(url)
            return {"default_branch": "main", "stars": 0,
                    "last_commit_iso": "", "last_commit_date": ""}

        def fake_commit(url, branch, force_refresh=False):
            commit_calls.append((url, branch, force_refresh))
            return {"short_sha": "deadbee", "sha": "d" * 40,
                    "message": "m", "author": "a", "branch": branch,
                    "date": "2026-04-08", "date_iso": "2026-04-08T00:00:00Z"}

        def fake_manifest(url, branch, manifest_path, force_refresh=False):
            manifest_calls.append((url, branch, manifest_path, force_refresh))
            return None

        self.sm._get_github_repo_info = fake_repo
        self.sm._get_latest_commit_info = fake_commit
        self.sm._fetch_manifest_from_github = fake_manifest

        info = self.sm.get_plugin_info("foo", fetch_latest_from_github=True, force_refresh=True)

        self.assertIsNotNone(info)
        self.assertEqual(info["last_commit_sha"], "d" * 40)
        # force_refresh must have propagated through to the fetch helpers.
        self.assertTrue(commit_calls, "commit fetch was not called")
        self.assertTrue(commit_calls[0][2], "force_refresh=True did not reach _get_latest_commit_info")
        self.assertTrue(manifest_calls, "manifest fetch was not called")
        self.assertTrue(manifest_calls[0][3], "force_refresh=True did not reach _fetch_manifest_from_github")

    def test_install_plugin_is_not_blocked_by_tombstone(self):
        """A tombstone must only gate the reconciler, not explicit installs.

        Uses a complete, valid manifest stub and a no-op dependency
        installer so ``install_plugin`` runs all the way through to a
        True return. Anything less (e.g. swallowing exceptions) would
        hide real regressions in the install path.
        """
        import json as _json
        self.sm.registry_cache = {
            "plugins": [{"id": "bar", "repo": "https://github.com/o/bar",
                         "plugin_path": ""}]
        }
        self.sm.registry_cache_time = time.time()

        # Mark it recently uninstalled (simulates a user who just clicked
        # uninstall and then immediately clicked install again).
        self.sm.mark_recently_uninstalled("bar")
        self.assertTrue(self.sm.was_recently_uninstalled("bar"))

        # Stub the heavy bits so install_plugin can run without network.
        self.sm._get_github_repo_info = lambda url: {
            "default_branch": "main", "stars": 0,
            "last_commit_iso": "", "last_commit_date": ""
        }
        self.sm._get_latest_commit_info = lambda *a, **k: {
            "short_sha": "abc1234", "sha": "a" * 40, "branch": "main",
            "message": "m", "author": "a",
            "date": "2026-04-08", "date_iso": "2026-04-08T00:00:00Z",
        }
        self.sm._fetch_manifest_from_github = lambda *a, **k: None
        # Skip dependency install entirely (real install calls pip).
        self.sm._install_dependencies = lambda *a, **k: True

        def fake_install_via_git(repo_url, plugin_path, branches):
            # Write a COMPLETE valid manifest so install_plugin's
            # post-download validation succeeds. Required fields come
            # from install_plugin itself: id, name, class_name, display_modes.
            plugin_path.mkdir(parents=True, exist_ok=True)
            manifest = {
                "id": "bar",
                "name": "Bar Plugin",
                "version": "1.0.0",
                "class_name": "BarPlugin",
                "entry_point": "manager.py",
                "display_modes": ["bar_mode"],
            }
            (plugin_path / "manifest.json").write_text(_json.dumps(manifest))
            return branches[0]

        self.sm._install_via_git = fake_install_via_git

        # No exception-swallowing: if install_plugin fails for ANY reason
        # unrelated to the tombstone, the test fails loudly.
        result = self.sm.install_plugin("bar")

        self.assertTrue(
            result,
            "install_plugin returned False — the tombstone should not gate "
            "explicit installs and all other stubs should allow success.",
        )
        # Tombstone survives install (harmless — nothing reads it for installed plugins).
        self.assertTrue(self.sm.was_recently_uninstalled("bar"))


class TestRegistryStaleCacheFallback(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.sm = PluginStoreManager(plugins_dir=self._tmp.name)

    def test_network_failure_returns_stale_cache(self):
        # Prime the cache with a known-good registry.
        self.sm.registry_cache = {"plugins": [{"id": "cached"}]}
        self.sm.registry_cache_time = time.time() - 10_000  # very old
        self.sm.registry_cache_timeout = 1  # force re-fetch attempt

        import requests as real_requests
        with patch.object(
            self.sm,
            "_http_get_with_retries",
            side_effect=real_requests.RequestException("boom"),
        ):
            result = self.sm.fetch_registry()

        self.assertEqual(result, {"plugins": [{"id": "cached"}]})

    def test_network_failure_with_no_cache_returns_empty(self):
        self.sm.registry_cache = None
        import requests as real_requests
        with patch.object(
            self.sm,
            "_http_get_with_retries",
            side_effect=real_requests.RequestException("boom"),
        ):
            result = self.sm.fetch_registry()
        self.assertEqual(result, {"plugins": []})


class TestFetchRegistryRaiseOnFailure(unittest.TestCase):
    """``fetch_registry(raise_on_failure=True)`` must propagate errors
    instead of silently falling back to the stale cache / empty dict.

    Regression guard: the state reconciler relies on this to distinguish
    "plugin genuinely not in registry" from "I can't reach the registry
    right now". Without it, a fresh boot with flaky WiFi would poison
    ``_unrecoverable_missing_on_disk`` with every config entry.
    """

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.sm = PluginStoreManager(plugins_dir=self._tmp.name)

    def test_request_exception_propagates_when_flag_set(self):
        import requests as real_requests
        self.sm.registry_cache = None  # no stale cache
        with patch.object(
            self.sm,
            "_http_get_with_retries",
            side_effect=real_requests.RequestException("boom"),
        ):
            with self.assertRaises(real_requests.RequestException):
                self.sm.fetch_registry(raise_on_failure=True)

    def test_request_exception_propagates_even_with_stale_cache(self):
        """Explicit caller opt-in beats the stale-cache convenience."""
        import requests as real_requests
        self.sm.registry_cache = {"plugins": [{"id": "stale"}]}
        self.sm.registry_cache_time = time.time() - 10_000
        self.sm.registry_cache_timeout = 1
        with patch.object(
            self.sm,
            "_http_get_with_retries",
            side_effect=real_requests.RequestException("boom"),
        ):
            with self.assertRaises(real_requests.RequestException):
                self.sm.fetch_registry(raise_on_failure=True)

    def test_json_decode_error_propagates_when_flag_set(self):
        import json as _json
        self.sm.registry_cache = None
        bad_response = MagicMock()
        bad_response.status_code = 200
        bad_response.raise_for_status = MagicMock()
        bad_response.json = MagicMock(
            side_effect=_json.JSONDecodeError("bad", "", 0)
        )
        with patch.object(self.sm, "_http_get_with_retries", return_value=bad_response):
            with self.assertRaises(_json.JSONDecodeError):
                self.sm.fetch_registry(raise_on_failure=True)

    def test_default_behavior_unchanged_by_new_parameter(self):
        """UI callers that don't pass the flag still get stale-cache fallback."""
        import requests as real_requests
        self.sm.registry_cache = {"plugins": [{"id": "cached"}]}
        self.sm.registry_cache_time = time.time() - 10_000
        self.sm.registry_cache_timeout = 1
        with patch.object(
            self.sm,
            "_http_get_with_retries",
            side_effect=real_requests.RequestException("boom"),
        ):
            result = self.sm.fetch_registry()  # default raise_on_failure=False
        self.assertEqual(result, {"plugins": [{"id": "cached"}]})


if __name__ == "__main__":
    unittest.main()
