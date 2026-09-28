"""Plugin store: the plugin registry, GitHub metadata, search and manifest
validation.

Part of PluginStoreManager (store_manager.py), which mixes this class in;
methods reach shared state and helpers through ``self``.
"""

import json
import requests
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional, Any
from jsonschema import Draft7Validator, ValidationError
from src.plugin_system.repo_urls import (
    github_api_headers, github_owner_repo, normalize_repo_url,
)


class _RegistryMixin:
    """PluginStoreManager methods: see the module docstring."""

    def _load_github_token(self) -> Optional[str]:
        """
        Load GitHub API token from config_secrets.json if available.
        
        Returns:
            GitHub token or None if not configured
        """
        try:
            config_path = Path(__file__).parent.parent.parent / "config" / "config_secrets.json"
            if config_path.exists():
                with open(config_path, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    token = config.get('github', {}).get('api_token', '').strip()
                    # The config template's placeholder, not a credential.
                    if token and token != "YOUR_GITHUB_PERSONAL_ACCESS_TOKEN":  # nosec B105  # nosemgrep
                        return token
        except Exception as e:
            self.logger.debug(f"Could not load GitHub token: {e}")
        return None

    def _validate_github_token(self, token: str) -> tuple[bool, Optional[str]]:
        """
        Validate a GitHub token by making a lightweight API call.
        
        Args:
            token: GitHub personal access token to validate
            
        Returns:
            Tuple of (is_valid, error_message)
            - is_valid: True if token is valid, False otherwise
            - error_message: None if valid, error description if invalid
        """
        if not token:
            return (False, "No token provided")
        
        # Check cache first
        cache_key = token[:10]  # Use first 10 chars as cache key for privacy
        if cache_key in self._token_validation_cache:
            cached_valid, cached_time, cached_error = self._token_validation_cache[cache_key]
            if time.time() - cached_time < self._token_validation_cache_timeout:
                return (cached_valid, cached_error)
        
        # Validate token by making a lightweight API call to /user endpoint
        try:
            api_url = "https://api.github.com/user"
            response = requests.get(api_url, headers=github_api_headers(token), timeout=5)
            
            if response.status_code == 200:
                # Token is valid
                result = (True, None)
                self._token_validation_cache[cache_key] = (True, time.time(), None)
                return result
            elif response.status_code == 401:
                # Token is invalid or expired
                error_msg = "Token is invalid or expired"
                result = (False, error_msg)
                self._token_validation_cache[cache_key] = (False, time.time(), error_msg)
                return result
            elif response.status_code == 403:
                # Rate limit or forbidden (but token might be valid)
                # Check if it's a rate limit issue
                if 'rate limit' in response.text.lower():
                    # Rate limit: return error but don't cache (rate limits are temporary)
                    error_msg = "Rate limit exceeded"
                    result = (False, error_msg)
                    return result
                else:
                    # Token lacks permissions: cache the result (permissions don't change)
                    error_msg = "Token lacks required permissions"
                    result = (False, error_msg)
                    self._token_validation_cache[cache_key] = (False, time.time(), error_msg)
                    return result
            else:
                # Other error
                error_msg = f"GitHub API error: {response.status_code}"
                result = (False, error_msg)
                self._token_validation_cache[cache_key] = (False, time.time(), error_msg)
                return result
                
        except requests.exceptions.Timeout:
            error_msg = "GitHub API request timed out"
            result = (False, error_msg)
            # Don't cache timeout errors
            return result
        except requests.exceptions.RequestException as e:
            error_msg = f"Network error: {str(e)}"
            result = (False, error_msg)
            # Don't cache network errors
            return result
        except Exception as e:
            error_msg = f"Unexpected error: {str(e)}"
            result = (False, error_msg)
            # Don't cache unexpected errors
            return result

    @staticmethod
    def _iso_to_date(iso_timestamp: str) -> str:
        """Convert an ISO timestamp to YYYY-MM-DD string."""
        if not iso_timestamp:
            return ""

        try:
            dt = datetime.fromisoformat(iso_timestamp.replace('Z', '+00:00'))
            return dt.strftime('%Y-%m-%d')
        except Exception:
            return ""

    @staticmethod
    def _distinct_sequence(values: List[str]) -> List[str]:
        """Return list preserving order while removing duplicates and falsey entries."""
        seen = set()
        ordered = []
        for value in values:
            if not value:
                continue
            if value in seen:
                continue
            seen.add(value)
            ordered.append(value)
        return ordered

    def _validate_manifest_version_fields(self, manifest: Dict[str, Any]) -> List[str]:
        """
        Validate version-related fields in manifest for consistency.
        
        Checks:
        - compatible_versions is present and is an array
        - Standardized field names are used (min_ledmatrix_version, max_ledmatrix_version)
        - Deprecated fields are not used (ledmatrix_version)
        - versions array entries use ledmatrix_min_version instead of ledmatrix_min
        
        Args:
            manifest: Manifest dictionary to validate
            
        Returns:
            List of validation error/warning messages (empty if valid)
        """
        errors = []
        
        # Check compatible_versions is an array
        if 'compatible_versions' in manifest:
            if not isinstance(manifest['compatible_versions'], list):
                errors.append("compatible_versions must be an array")
            elif len(manifest['compatible_versions']) == 0:
                errors.append("compatible_versions array cannot be empty")
        
        # Warn about deprecated ledmatrix_version field
        if 'ledmatrix_version' in manifest:
            errors.append("ledmatrix_version is deprecated, use compatible_versions instead")
        
        # Check versions array entries use standardized field names
        if 'versions' in manifest and isinstance(manifest['versions'], list):
            for i, version_entry in enumerate(manifest['versions']):
                if not isinstance(version_entry, dict):
                    continue
                
                # Check for old ledmatrix_min field
                if 'ledmatrix_min' in version_entry and 'ledmatrix_min_version' not in version_entry:
                    errors.append(f"versions[{i}] uses deprecated 'ledmatrix_min', should use 'ledmatrix_min_version'")
        
        return errors

    def _validate_manifest_schema(self, manifest: Dict[str, Any], plugin_id: str) -> List[str]:
        """
        Validate manifest against JSON schema if available.
        
        Args:
            manifest: Manifest dictionary to validate
            plugin_id: Plugin ID for error messages
            
        Returns:
            List of validation error messages (empty if valid or schema unavailable)
        """
        try:
            # Load manifest schema
            schema_path = Path(__file__).parent.parent.parent / "schema" / "manifest_schema.json"
            if not schema_path.exists():
                return []  # Schema not available, skip validation
            
            with open(schema_path, 'r', encoding='utf-8') as f:
                schema = json.load(f)
            
            # Validate schema itself
            Draft7Validator.check_schema(schema)
            
            # Validate manifest against schema
            validator = Draft7Validator(schema)
            errors = []
            for error in validator.iter_errors(manifest):
                error_path = '.'.join(str(p) for p in error.path)
                errors.append(f"{error_path}: {error.message}")
            
            return errors
        except json.JSONDecodeError as e:
            self.logger.warning(f"Could not parse manifest schema: {e}")
            return []
        except ValidationError as e:
            self.logger.warning(f"Manifest schema is invalid: {e}")
            return []
        except Exception as e:
            self.logger.debug(f"Error validating manifest schema for {plugin_id}: {e}")
            return []

    _EMPTY_REPO_INFO: Dict[str, Any] = {
        'stars': 0,
        'forks': 0,
        'open_issues': 0,
        'updated_at_iso': '',
        'last_commit_iso': '',
        'last_commit_date': '',
        'language': '',
        'license': '',
        'default_branch': 'main',
    }

    def _get_github_repo_info(self, repo_url: str) -> Dict[str, Any]:
        """GitHub metadata for a repository (stars, default branch, last push).

        Returns zeroed defaults (``_EMPTY_REPO_INFO``) for a non-GitHub URL or
        when GitHub cannot be asked and nothing is cached.
        """
        try:
            owner_repo = github_owner_repo(repo_url)
            if owner_repo is None:
                return dict(self._EMPTY_REPO_INFO)
            owner, repo = owner_repo
            cache_key = f"{owner}/{repo}"

            if cache_key in self.github_cache:
                cached_time, cached_data = self.github_cache[cache_key]
                if time.time() - cached_time < self.cache_timeout:
                    return cached_data

            api_url = f"https://api.github.com/repos/{owner}/{repo}"
            try:
                response = requests.get(
                    api_url, headers=github_api_headers(self.github_token), timeout=10)
            except requests.RequestException as req_err:
                # Network error: prefer a stale cache hit over an empty
                # default so the UI keeps working on a flaky Pi WiFi link.
                # Bump the cached entry's timestamp into a short backoff
                # window so subsequent requests serve the stale payload
                # cheaply instead of re-hitting the network on every request.
                if cache_key in self.github_cache:
                    _, stale = self.github_cache[cache_key]
                    self._record_cache_backoff(self.github_cache, cache_key, self.cache_timeout, stale)
                    self.logger.warning(
                        "GitHub repo info fetch failed for %s (%s); serving stale cache.",
                        cache_key, req_err,
                    )
                    return stale
                raise

            if response.status_code == 200:
                data = response.json()
                pushed_at = data.get('pushed_at', '') or data.get('updated_at', '')
                repo_info = {
                    'stars': data.get('stargazers_count', 0),
                    'forks': data.get('forks_count', 0),
                    'open_issues': data.get('open_issues_count', 0),
                    'updated_at_iso': data.get('updated_at', ''),
                    'last_commit_iso': pushed_at,
                    'last_commit_date': self._iso_to_date(pushed_at),
                    'language': data.get('language', ''),
                    'license': data.get('license', {}).get('name', '') if data.get('license') else '',
                    'default_branch': data.get('default_branch', 'main')
                }
                self.github_cache[cache_key] = (time.time(), repo_info)
                return repo_info

            if response.status_code == 403:
                # Rate limit or authentication issue. A stale star count is
                # better than a reset to zero, and the backoff bump stops the
                # store hammering the API while rate-limited.
                if cache_key in self.github_cache:
                    _, stale = self.github_cache[cache_key]
                    self._record_cache_backoff(self.github_cache, cache_key, self.cache_timeout, stale)
                    self.logger.warning(
                        "GitHub API 403 for %s; serving stale cache.", cache_key,
                    )
                    return stale
                if not self.github_token:
                    self.logger.warning(
                        "GitHub API rate limit likely exceeded (403). "
                        "Add a GitHub personal access token to config/config_secrets.json "
                        "under 'github.api_token' to increase rate limits from 60 to 5000/hour."
                    )
                else:
                    self.logger.warning(
                        f"GitHub API request failed: 403 for {api_url}. "
                        f"Your token may have insufficient permissions or rate limit exceeded."
                    )
            else:
                self.logger.warning(f"GitHub API request failed: {response.status_code} for {api_url}")
                if cache_key in self.github_cache:
                    _, stale = self.github_cache[cache_key]
                    self._record_cache_backoff(self.github_cache, cache_key, self.cache_timeout, stale)
                    return stale

            return dict(self._EMPTY_REPO_INFO)

        except requests.exceptions.RequestException as e:
            # Offline, DNS or a timeout reaching GitHub: the listing still
            # works without the extra repo info, so this is not an error.
            self.logger.warning("GitHub repo info unavailable for %s: %s", repo_url, e)
            return dict(self._EMPTY_REPO_INFO)
        except Exception as e:
            self.logger.error(f"Error fetching GitHub repo info for {repo_url}: {e}")
            return dict(self._EMPTY_REPO_INFO)

    def _http_get_with_retries(self, url: str, *, timeout: int = 10, stream: bool = False, headers: Dict[str, str] = None, max_retries: int = 3, backoff_sec: float = 0.75):
        """
        HTTP GET with simple retry strategy and exponential backoff.

        Returns a requests.Response or raises the last exception.
        """
        last_exc = None
        for attempt in range(1, max_retries + 1):
            try:
                resp = requests.get(url, timeout=timeout, stream=stream, headers=headers)
                return resp
            except requests.RequestException as e:
                last_exc = e
                self.logger.warning(f"HTTP GET failed (attempt {attempt}/{max_retries}) for {url}: {e}")
                if attempt < max_retries:
                    time.sleep(backoff_sec * attempt)
        # Exhausted retries
        raise last_exc

    def fetch_registry_from_url(self, repo_url: str) -> Optional[Dict]:
        """
        Fetch a registry-style plugins.json from a custom GitHub repository URL.
        
        This allows users to point to a registry-style monorepo (like the official
        ledmatrix-plugins repo) and browse/install plugins from it.
        
        Args:
            repo_url: GitHub repository URL (e.g., https://github.com/user/ledmatrix-plugins)
            
        Returns:
            Registry dict with plugins list, or None if not found/invalid
        """
        try:
            repo_url = normalize_repo_url(repo_url)

            # plugins.json or registry.json at the root of main, then master.
            registry_urls = []
            owner_repo = github_owner_repo(repo_url)
            if owner_repo is not None:
                owner, repo = owner_repo
                for branch in ['main', 'master']:
                    registry_urls.append(f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/plugins.json")
                    registry_urls.append(f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/registry.json")

            for url in registry_urls:
                try:
                    response = self._http_get_with_retries(url, timeout=10)
                    if response.status_code == 200:
                        registry = response.json()
                        # Validate it looks like a registry
                        if isinstance(registry, dict) and 'plugins' in registry:
                            self.logger.info(f"Successfully fetched registry from {url}")
                            return registry
                except Exception as e:
                    self.logger.debug(f"Failed to fetch from {url}: {e}")
                    continue
            
            self.logger.warning(f"No valid registry found at {repo_url}")
            return None
            
        except Exception as e:
            self.logger.error(f"Error fetching registry from URL: {e}", exc_info=True)
            return None

    def fetch_registry(self, force_refresh: bool = False, raise_on_failure: bool = False) -> Dict:
        """
        Fetch the plugin registry from GitHub.

        Args:
            force_refresh: Force refresh even if cached
            raise_on_failure: If True, re-raise network / JSON errors instead
                of silently falling back to stale cache / empty dict. UI
                callers prefer the stale-fallback default so the plugin
                list keeps working on flaky WiFi; the state reconciler
                needs the explicit failure signal so it can distinguish
                "plugin genuinely not in registry" from "I couldn't reach
                the registry at all" and not mark everything unrecoverable.

        Returns:
            Registry data with list of available plugins

        Raises:
            requests.RequestException / json.JSONDecodeError when
            ``raise_on_failure`` is True and the fetch fails.
        """
        # Check if cache is still valid (within timeout)
        current_time = time.time()
        if (self.registry_cache and self.registry_cache_time and
            not force_refresh and
            (current_time - self.registry_cache_time) < self.registry_cache_timeout):
            return self.registry_cache

        with self._registry_fetch_lock:
            # Re-check inside the lock — a concurrent caller that was waiting
            # may have already populated the cache while we blocked.
            current_time = time.time()
            if (self.registry_cache and self.registry_cache_time and
                    not force_refresh and
                    (current_time - self.registry_cache_time) < self.registry_cache_timeout):
                return self.registry_cache

            try:
                self.logger.info(f"Fetching plugin registry from {self.REGISTRY_URL}")
                response = self._http_get_with_retries(self.REGISTRY_URL, timeout=10)
                response.raise_for_status()
                self.registry_cache = response.json()
                self.registry_cache_time = current_time
                self.logger.info(f"Fetched registry with {len(self.registry_cache.get('plugins', []))} plugins")
                return self.registry_cache
            except requests.RequestException as e:
                self.logger.error(f"Error fetching registry: {e}")
                if raise_on_failure:
                    raise
                # Prefer stale cache over an empty list so the plugin list UI
                # keeps working on a flaky connection (e.g. Pi on WiFi). Bump
                # registry_cache_time into a short backoff window so the next
                # request serves the stale payload cheaply instead of
                # re-hitting the network on every request (matches the
                # pattern used by github_cache / commit_info_cache).
                if self.registry_cache:
                    self.logger.warning("Falling back to stale registry cache")
                    self.registry_cache_time = (
                        time.time() + self._failure_backoff_seconds - self.registry_cache_timeout
                    )
                    return self.registry_cache
                return {"plugins": []}
            except json.JSONDecodeError as e:
                self.logger.error(f"Error parsing registry JSON: {e}")
                if raise_on_failure:
                    raise
                if self.registry_cache:
                    self.registry_cache_time = (
                        time.time() + self._failure_backoff_seconds - self.registry_cache_timeout
                    )
                    return self.registry_cache
                return {"plugins": []}

    def search_plugins(self, query: str = "", category: str = "", tags: List[str] = None, fetch_commit_info: bool = True, include_saved_repos: bool = True, saved_repositories_manager = None) -> List[Dict]:
        """
        Search for plugins in the registry with enhanced metadata.

        GitHub supplies live metadata such as stars and last commit
        timestamps; the registry supplies descriptive information (name,
        description, repo URL, etc.).

        Args:
            query: Search query string (searches name, description, id, author)
            category: Filter by category (e.g., 'sports', 'weather', 'time')
            tags: Filter by tags (matches any tag in list)
            fetch_commit_info: If True (default), fetch commit metadata from GitHub.
            include_saved_repos: If True (default), also search the
                registry-style repositories the user saved.
            saved_repositories_manager: The SavedRepositoriesManager holding
                those repositories; without it only the official registry is
                searched.

        Returns:
            List of matching plugin metadata enriched with GitHub information
        """
        if tags is None:
            tags = []

        # Fetch from official registry
        registry = self.fetch_registry()
        plugins = registry.get('plugins', []) or []
        
        # Also fetch from saved repositories if enabled
        if include_saved_repos and saved_repositories_manager:
            saved_repos = saved_repositories_manager.get_registry_repositories()
            for repo_info in saved_repos:
                repo_url = repo_info.get('url')
                if repo_url:
                    try:
                        custom_registry = self.fetch_registry_from_url(repo_url)
                        if custom_registry:
                            custom_plugins = custom_registry.get('plugins', []) or []
                            # Mark these as from custom repository
                            for plugin in custom_plugins:
                                plugin['_source'] = 'custom_repository'
                                plugin['_repository_url'] = repo_url
                                plugin['_repository_name'] = repo_info.get('name', repo_url)
                            plugins.extend(custom_plugins)
                    except Exception as e:
                        self.logger.warning(f"Failed to fetch plugins from saved repository {repo_url}: {e}")

        # First pass: apply cheap filters (category/tags/query) so we only
        # fetch GitHub metadata for plugins that will actually be returned.
        filtered: List[Dict] = []
        for plugin in plugins:
            if category and plugin.get('category') != category:
                continue
            if tags and not any(tag in plugin.get('tags', []) for tag in tags):
                continue
            if query:
                query_lower = query.lower()
                searchable_text = ' '.join([
                    plugin.get('name', ''),
                    plugin.get('description', ''),
                    plugin.get('id', ''),
                    plugin.get('author', ''),
                ]).lower()
                if query_lower not in searchable_text:
                    continue
            filtered.append(plugin)

        def _enrich(plugin: Dict) -> Dict:
            """Enrich a single plugin with GitHub metadata.

            Called concurrently from a ThreadPoolExecutor. Both HTTP helpers
            (``_get_github_repo_info`` / ``_get_latest_commit_info``) are
            thread-safe -- they use ``requests`` and write their own cache
            keys on Python dicts, which is atomic under the GIL for
            single-key assignments.
            """
            enhanced_plugin = plugin.copy()
            repo_url = plugin.get('repo', '')
            if not repo_url:
                return enhanced_plugin

            github_info = self._get_github_repo_info(repo_url)
            enhanced_plugin['stars'] = github_info.get('stars', plugin.get('stars', 0))
            enhanced_plugin['default_branch'] = github_info.get('default_branch', plugin.get('branch', 'main'))
            enhanced_plugin['last_updated_iso'] = github_info.get('last_commit_iso')
            enhanced_plugin['last_updated'] = github_info.get('last_commit_date')

            if fetch_commit_info:
                branch = plugin.get('branch') or github_info.get('default_branch', 'main')

                commit_info = self._get_latest_commit_info(repo_url, branch)
                if commit_info:
                    enhanced_plugin['last_commit'] = commit_info.get('short_sha')
                    enhanced_plugin['last_commit_sha'] = commit_info.get('sha')
                    enhanced_plugin['last_updated'] = commit_info.get('date') or enhanced_plugin.get('last_updated')
                    enhanced_plugin['last_updated_iso'] = commit_info.get('date_iso') or enhanced_plugin.get('last_updated_iso')
                    enhanced_plugin['last_commit_message'] = commit_info.get('message')
                    enhanced_plugin['last_commit_author'] = commit_info.get('author')
                    enhanced_plugin['branch'] = commit_info.get('branch', branch)
                    enhanced_plugin['last_commit_branch'] = commit_info.get('branch')

                # Intentionally NO per-plugin manifest.json fetch here.
                # The registry's plugins.json already carries ``description``
                # (it is generated from each plugin's manifest by
                # ``update_registry.py``), and ``last_updated`` is filled in
                # from the commit info above. Fetching manifest.json per
                # plugin costs one extra HTTPS round trip per result; on a Pi4
                # with a flaky WiFi link the tail retries of that one call
                # (_http_get_with_retries does 3 attempts with exponential
                # backoff) dominate wall time even with the thread pool.

            return enhanced_plugin

        # Fan out the per-plugin GitHub enrichment. Serially, a Pi4 with ~15
        # plugins and a cold cache makes 30+ HTTP requests in strict sequence
        # (the "connecting to display" hang users reported). With a thread
        # pool, latency is dominated by the slowest request rather than
        # their sum. Workers capped at 10 to stay well under the
        # unauthenticated GitHub rate limit burst and avoid overwhelming a
        # Pi's WiFi link.
        if not filtered:
            return []

        # Not worth the pool overhead for tiny workloads. Parenthesized to
        # make Python's default ``and`` > ``or`` precedence explicit: a
        # single plugin, OR a small batch where we don't need commit info.
        if (len(filtered) == 1) or ((not fetch_commit_info) and (len(filtered) < 4)):
            return [_enrich(p) for p in filtered]

        max_workers = min(10, len(filtered))
        with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix='plugin-search') as executor:
            # executor.map preserves input order, which the UI relies on.
            return list(executor.map(_enrich, filtered))

    def _fetch_manifest_from_github(self, repo_url: str, branch: str = "master", manifest_path: str = "manifest.json", force_refresh: bool = False) -> Optional[Dict]:
        """
        Fetch manifest.json directly from a GitHub repository.

        Args:
            repo_url: GitHub repository URL
            branch: Branch name (default: master)
            manifest_path: Path to manifest within the repo (default: manifest.json).
                          For monorepo plugins this will be e.g. "plugins/football-scoreboard/manifest.json".
            force_refresh: If True, bypass the cache.

        Returns:
            Manifest data or None if not found
        """
        try:
            owner_repo = github_owner_repo(repo_url)
            if owner_repo is None:
                return None
            owner, repo = owner_repo

            cache_key = f"{owner}/{repo}:{branch}:{manifest_path}"
            if not force_refresh and cache_key in self.manifest_cache:
                cached_time, cached_data = self.manifest_cache[cache_key]
                if time.time() - cached_time < self.manifest_cache_timeout:
                    return cached_data

            raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{manifest_path}"
            response = self._http_get_with_retries(raw_url, timeout=10)
            if response.status_code == 200:
                result = response.json()
                self.manifest_cache[cache_key] = (time.time(), result)
                return result
            if response.status_code == 404 and branch != "main":
                raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/main/{manifest_path}"
                response = self._http_get_with_retries(raw_url, timeout=10)
                if response.status_code == 200:
                    result = response.json()
                    self.manifest_cache[cache_key] = (time.time(), result)
                    return result

            # Cache the miss too, so a plugin without a manifest at this path
            # is not re-fetched on every browse.
            self.manifest_cache[cache_key] = (time.time(), None)
        except Exception as e:
            self.logger.debug(f"Could not fetch manifest from GitHub for {repo_url}: {e}")

        return None

    def _get_latest_commit_info(self, repo_url: str, branch: str = "main", force_refresh: bool = False) -> Optional[Dict[str, Any]]:
        """Return metadata about the latest commit on the given branch."""
        try:
            owner_repo = github_owner_repo(repo_url)
            if owner_repo is None:
                return None
            owner, repo = owner_repo

            cache_key = f"{owner}/{repo}:{branch}"
            if not force_refresh and cache_key in self.commit_info_cache:
                cached_time, cached_data = self.commit_info_cache[cache_key]
                if time.time() - cached_time < self.commit_cache_timeout:
                    return cached_data

            branches_to_try = self._distinct_sequence([branch, 'main', 'master'])
            headers = github_api_headers(self.github_token)

            last_error = None
            for branch_name in branches_to_try:
                api_url = f"https://api.github.com/repos/{owner}/{repo}/commits/{branch_name}"
                try:
                    response = requests.get(api_url, headers=headers, timeout=10)
                except requests.RequestException as req_err:
                    # Network failure: fall back to a stale cache hit if
                    # available so the plugin store UI keeps populating
                    # commit info on a flaky WiFi link. Bump the cached
                    # timestamp into the backoff window so we don't
                    # re-retry on every request.
                    if cache_key in self.commit_info_cache:
                        _, stale = self.commit_info_cache[cache_key]
                        if stale is not None:
                            self._record_cache_backoff(
                                self.commit_info_cache, cache_key,
                                self.commit_cache_timeout, stale,
                            )
                            self.logger.warning(
                                "GitHub commit fetch failed for %s (%s); serving stale cache.",
                                cache_key, req_err,
                            )
                            return stale
                    last_error = str(req_err)
                    continue
                if response.status_code == 200:
                    commit_data = response.json()
                    commit_sha_full = commit_data.get('sha', '')
                    commit_sha_short = commit_sha_full[:7] if commit_sha_full else ''
                    commit_meta = commit_data.get('commit', {})
                    commit_author = commit_meta.get('author', {})
                    commit_date_iso = commit_author.get('date', '')

                    result = {
                        'branch': branch_name,
                        'sha': commit_sha_full,
                        'short_sha': commit_sha_short,
                        'date_iso': commit_date_iso,
                        'date': self._iso_to_date(commit_date_iso),
                        'author': commit_author.get('name', ''),
                        'message': commit_meta.get('message', ''),
                    }
                    self.commit_info_cache[cache_key] = (time.time(), result)
                    return result

                if response.status_code == 403 and not self.github_token:
                    self.logger.debug("GitHub commit API rate limited (403). Consider adding a token.")
                    last_error = response.text
                else:
                    last_error = response.text

            if last_error:
                self.logger.debug(f"Unable to fetch commit info for {repo_url}: {last_error}")

            # All branches returned a non-200 response (e.g. 404 on every
            # candidate, or a transient 5xx). If we already had a good
            # cached value, prefer serving that — overwriting it with
            # None here would wipe out commit info the UI just showed
            # on the previous request. Bump the timestamp into the
            # backoff window so subsequent lookups hit the cache.
            if cache_key in self.commit_info_cache:
                _, prior = self.commit_info_cache[cache_key]
                if prior is not None:
                    self._record_cache_backoff(
                        self.commit_info_cache, cache_key,
                        self.commit_cache_timeout, prior,
                    )
                    return prior

            # No prior good value — cache the negative result so we don't
            # hammer a plugin that genuinely has no reachable commits.
            self.commit_info_cache[cache_key] = (time.time(), None)

        except Exception as e:
            self.logger.debug(f"Error fetching latest commit metadata for {repo_url}: {e}")

        return None

    def get_plugin_info(self, plugin_id: str, fetch_latest_from_github: bool = True, force_refresh: bool = False) -> Optional[Dict]:
        """
        Get detailed information about a plugin from the registry.

        GitHub provides authoritative metadata such as stars and the latest
        commit. The registry supplies descriptive information (name, id, repo URL).

        Args:
            plugin_id: Plugin identifier
            fetch_latest_from_github: If True (default), augment with GitHub commit metadata.
            force_refresh: If True, bypass caches for commit/manifest data.

        Returns:
            Plugin metadata or None if not found
        """
        registry = self.fetch_registry()
        plugins = registry.get('plugins', []) or []
        plugin_info = self._match_registry_entry(plugins, plugin_id)

        if not plugin_info:
            return None

        if fetch_latest_from_github:
            repo_url = plugin_info.get('repo')
            if repo_url:
                plugin_info = plugin_info.copy()

                github_info = self._get_github_repo_info(repo_url)
                branch = plugin_info.get('branch') or github_info.get('default_branch', 'main')

                plugin_info['default_branch'] = github_info.get('default_branch', branch)
                plugin_info['stars'] = github_info.get('stars', plugin_info.get('stars', 0))
                plugin_info['last_updated'] = github_info.get('last_commit_date', plugin_info.get('last_updated'))
                plugin_info['last_updated_iso'] = github_info.get('last_commit_iso', plugin_info.get('last_updated_iso'))

                commit_info = self._get_latest_commit_info(repo_url, branch, force_refresh=force_refresh)
                if commit_info:
                    plugin_info['last_commit'] = commit_info.get('short_sha')
                    plugin_info['last_commit_sha'] = commit_info.get('sha')
                    plugin_info['last_commit_message'] = commit_info.get('message')
                    plugin_info['last_commit_author'] = commit_info.get('author')
                    plugin_info['last_updated'] = commit_info.get('date') or plugin_info.get('last_updated')
                    plugin_info['last_updated_iso'] = commit_info.get('date_iso') or plugin_info.get('last_updated_iso')
                    plugin_info['branch'] = commit_info.get('branch', branch)
                    plugin_info['last_commit_branch'] = commit_info.get('branch')

                plugin_subpath = plugin_info.get('plugin_path', '')
                manifest_rel = f"{plugin_subpath}/manifest.json" if plugin_subpath else "manifest.json"
                github_manifest = self._fetch_manifest_from_github(repo_url, branch, manifest_rel, force_refresh=force_refresh)
                if github_manifest:
                    if 'last_updated' in github_manifest and not plugin_info.get('last_updated'):
                        plugin_info['last_updated'] = github_manifest['last_updated']
                    if 'description' in github_manifest:
                        plugin_info['description'] = github_manifest['description']

        return plugin_info

    @staticmethod
    def _match_registry_entry(plugins: List[Dict], plugin_id: str) -> Optional[Dict]:
        """Find a registry entry by its id, or by the directory it installs to.

        Four shipped plugins have a registry ``id`` that differs from the ``id``
        in their own manifest: ``weather`` installs to ``plugins/ledmatrix-weather``,
        and likewise stocks, music and leaderboard. Installation already prefers
        the manifest id for the directory name, so on disk, in ``config.json``
        and in a backup manifest those plugins are called ``ledmatrix-weather``.

        Only the registry calls them ``weather``, and nothing resolved that in
        reverse: restoring a backup asked the store for ``ledmatrix-weather``
        and got "Plugin not found in registry", silently dropping four enabled
        plugins from a restored device.

        Matching ``plugin_path`` fixes it without renaming any published id,
        which would orphan ``plugin_state.json`` entries keyed on the old ones.
        Exact id always wins, so an entry whose *path* happens to collide with
        another entry's id cannot shadow it.
        """
        if not plugin_id:
            return None
        exact = next((p for p in plugins if p.get('id') == plugin_id), None)
        if exact is not None:
            return exact
        for entry in plugins:
            path = (entry.get('plugin_path') or '').rstrip('/')
            if path and path.rsplit('/', 1)[-1] == plugin_id:
                return entry
        return None

    def get_registry_info(self, plugin_id: str) -> Optional[Dict]:
        """
        Get plugin information from the registry cache only (no GitHub API calls).

        Use this for lightweight lookups where only registry fields are needed
        (e.g., verified status, latest_version).

        Args:
            plugin_id: Plugin identifier

        Returns:
            Plugin metadata from registry or None if not found
        """
        registry = self.fetch_registry()
        plugins = registry.get('plugins', []) or []
        return self._match_registry_entry(plugins, plugin_id)
