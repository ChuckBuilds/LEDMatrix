"""
Plugin Store Manager for LEDMatrix

Handles plugin discovery, installation, updates, and uninstallation
from both the official registry and custom GitHub repositories.
"""

import os
import json
import subprocess
import shutil
import zipfile
import tempfile
import requests
import time
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional, Any
import logging

try:
    import jsonschema
    from jsonschema import Draft7Validator, ValidationError
    JSONSCHEMA_AVAILABLE = True
except ImportError:
    JSONSCHEMA_AVAILABLE = False


class PluginStoreManager:
    """
    Manages plugin discovery, installation, and updates from GitHub.
    
    Supports two installation methods:
    1. From official registry (curated plugins)
    2. From custom GitHub URL (any repo)
    """
    
    REGISTRY_URL = "https://raw.githubusercontent.com/ChuckBuilds/ledmatrix-plugins/main/plugins.json"
    
    def __init__(self, plugins_dir: str = "plugins"):
        """
        Initialize the plugin store manager.

        Args:
            plugins_dir: Directory where plugins are installed
        """
        self.plugins_dir = Path(plugins_dir)
        self.logger = logging.getLogger(__name__)
        self.registry_cache = None
        self.registry_cache_time = None  # Timestamp of when registry was cached
        self.github_cache = {}  # Cache for GitHub API responses
        self.cache_timeout = 3600  # 1 hour cache timeout
        self.registry_cache_timeout = 300  # 5 minutes for registry cache
        self.github_token = self._load_github_token()
        self._token_validation_cache = {}  # Cache for token validation results: {token: (is_valid, timestamp, error_message)}
        self._token_validation_cache_timeout = 300  # 5 minutes cache for token validation

        # Ensure plugins directory exists
        self.plugins_dir.mkdir(exist_ok=True)

    def _load_github_token(self) -> Optional[str]:
        """
        Load GitHub API token from config_secrets.json if available.
        
        Returns:
            GitHub token or None if not configured
        """
        try:
            config_path = Path(__file__).parent.parent.parent / "config" / "config_secrets.json"
            if config_path.exists():
                with open(config_path, 'r') as f:
                    config = json.load(f)
                    token = config.get('github', {}).get('api_token', '').strip()
                    if token and token != "YOUR_GITHUB_PERSONAL_ACCESS_TOKEN":
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
            headers = {
                'Accept': 'application/vnd.github.v3+json',
                'User-Agent': 'LEDMatrix-Plugin-Manager/1.0',
                'Authorization': f'token {token}'
            }
            
            response = requests.get(api_url, headers=headers, timeout=5)
            
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
        if not JSONSCHEMA_AVAILABLE:
            return []
        
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

    def _get_github_repo_info(self, repo_url: str) -> Dict[str, Any]:
        """Fetch GitHub repository information (stars, etc.)"""
        # Extract owner/repo from URL
        try:
            # Handle different URL formats
            if 'github.com' in repo_url:
                parts = repo_url.strip('/').split('/')
                if len(parts) >= 2:
                    owner = parts[-2]
                    repo = parts[-1]
                    if repo.endswith('.git'):
                        repo = repo[:-4]

                    cache_key = f"{owner}/{repo}"

                    # Check cache first
                    if cache_key in self.github_cache:
                        cached_time, cached_data = self.github_cache[cache_key]
                        if time.time() - cached_time < self.cache_timeout:
                            return cached_data

                    # Fetch from GitHub API
                    api_url = f"https://api.github.com/repos/{owner}/{repo}"
                    headers = {
                        'Accept': 'application/vnd.github.v3+json',
                        'User-Agent': 'LEDMatrix-Plugin-Manager/1.0'
                    }
                    
                    # Add authentication if token is available
                    if self.github_token:
                        headers['Authorization'] = f'token {self.github_token}'

                    response = requests.get(api_url, headers=headers, timeout=10)
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

                        # Cache the result
                        self.github_cache[cache_key] = (time.time(), repo_info)
                        return repo_info
                    elif response.status_code == 403:
                        # Rate limit or authentication issue
                        if not self.github_token:
                            self.logger.warning(
                                f"GitHub API rate limit likely exceeded (403). "
                                f"Add a GitHub personal access token to config/config_secrets.json "
                                f"under 'github.api_token' to increase rate limits from 60 to 5000/hour."
                            )
                        else:
                            self.logger.warning(
                                f"GitHub API request failed: 403 for {api_url}. "
                                f"Your token may have insufficient permissions or rate limit exceeded."
                            )
                    else:
                        self.logger.warning(f"GitHub API request failed: {response.status_code} for {api_url}")

            return {
                'stars': 0,
                'forks': 0,
                'open_issues': 0,
                'updated_at_iso': '',
                'last_commit_iso': '',
                'last_commit_date': '',
                'language': '',
                'license': '',
                'default_branch': 'main'
            }

        except Exception as e:
            self.logger.error(f"Error fetching GitHub repo info for {repo_url}: {e}")
            return {
                'stars': 0,
                'forks': 0,
                'open_issues': 0,
                'updated_at_iso': '',
                'last_commit_iso': '',
                'last_commit_date': '',
                'language': '',
                'license': '',
                'default_branch': 'main'
            }

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
            # Clean up URL
            repo_url = repo_url.rstrip('/').replace('.git', '')
            
            # Try to find plugins.json in common locations
            # First try root directory
            registry_urls = []
            
            # Extract owner/repo from URL
            if 'github.com' in repo_url:
                parts = repo_url.split('/')
                if len(parts) >= 2:
                    owner = parts[-2]
                    repo = parts[-1]
                    
                    # Try common branch names
                    for branch in ['main', 'master']:
                        registry_urls.append(f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/plugins.json")
                        registry_urls.append(f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/registry.json")
            
            # Try each URL
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
    
    def fetch_registry(self, force_refresh: bool = False) -> Dict:
        """
        Fetch the plugin registry from GitHub.
        
        Args:
            force_refresh: Force refresh even if cached
            
        Returns:
            Registry data with list of available plugins
        """
        # Check if cache is still valid (within timeout)
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
            return {"plugins": []}
        except json.JSONDecodeError as e:
            self.logger.error(f"Error parsing registry JSON: {e}")
            return {"plugins": []}
    
    def search_plugins(self, query: str = "", category: str = "", tags: List[str] = None, fetch_commit_info: bool = True, include_saved_repos: bool = True, saved_repositories_manager = None) -> List[Dict]:
        """
        Search for plugins in the registry with enhanced metadata.

        GitHub is now treated as the source of truth for live metadata like
        stars and last commit timestamps. The registry provides descriptive
        information (name, description, repo URL, etc.).

        Args:
            query: Search query string (searches name, description, id)
            category: Filter by category (e.g., 'sports', 'weather', 'time')
            tags: Filter by tags (matches any tag in list)
            fetch_commit_info: If True (default), fetch commit metadata from GitHub.

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

        results = []
        for plugin in plugins:
            # Category filter
            if category and plugin.get('category') != category:
                continue

            # Tags filter (match any tag)
            if tags and not any(tag in plugin.get('tags', []) for tag in tags):
                continue

            # Query search (case-insensitive)
            if query:
                query_lower = query.lower()
                searchable_text = ' '.join([
                    plugin.get('name', ''),
                    plugin.get('description', ''),
                    plugin.get('id', ''),
                    plugin.get('author', '')
                ]).lower()

                if query_lower not in searchable_text:
                    continue

            # Enhance plugin data with GitHub metadata
            enhanced_plugin = plugin.copy()

            # Get real GitHub stars
            repo_url = plugin.get('repo', '')
            if repo_url:
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

                    # Fetch manifest from GitHub for additional metadata (description, etc.)
                    github_manifest = self._fetch_manifest_from_github(repo_url, branch)
                    if github_manifest:
                        if 'last_updated' in github_manifest and not enhanced_plugin.get('last_updated'):
                            enhanced_plugin['last_updated'] = github_manifest['last_updated']
                        if 'description' in github_manifest:
                            enhanced_plugin['description'] = github_manifest['description']

            results.append(enhanced_plugin)

        return results
    
    def _fetch_manifest_from_github(self, repo_url: str, branch: str = "master", manifest_path: str = "manifest.json") -> Optional[Dict]:
        """
        Fetch manifest.json directly from a GitHub repository.

        Args:
            repo_url: GitHub repository URL
            branch: Branch name (default: master)
            manifest_path: Path to manifest within the repo (default: manifest.json).
                          For monorepo plugins this will be e.g. "plugins/football-scoreboard/manifest.json".

        Returns:
            Manifest data or None if not found
        """
        try:
            # Convert repo URL to raw content URL
            # https://github.com/user/repo -> https://raw.githubusercontent.com/user/repo/branch/manifest.json
            if 'github.com' in repo_url:
                # Handle different URL formats
                repo_url = repo_url.rstrip('/')
                if repo_url.endswith('.git'):
                    repo_url = repo_url[:-4]

                parts = repo_url.split('/')
                if len(parts) >= 2:
                    owner = parts[-2]
                    repo = parts[-1]

                    raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{manifest_path}"

                    response = self._http_get_with_retries(raw_url, timeout=10)
                    if response.status_code == 200:
                        return response.json()
                    elif response.status_code == 404:
                        # Try main branch instead
                        if branch != "main":
                            raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/main/{manifest_path}"
                            response = self._http_get_with_retries(raw_url, timeout=10)
                            if response.status_code == 200:
                                return response.json()
        except Exception as e:
            self.logger.debug(f"Could not fetch manifest from GitHub for {repo_url}: {e}")

        return None
    
    def _get_latest_commit_info(self, repo_url: str, branch: str = "main") -> Optional[Dict[str, Any]]:
        """Return metadata about the latest commit on the given branch."""
        try:
            if 'github.com' not in repo_url:
                return None

            repo_url = repo_url.rstrip('/')
            if repo_url.endswith('.git'):
                repo_url = repo_url[:-4]

            parts = repo_url.split('/')
            if len(parts) < 2:
                return None

            owner = parts[-2]
            repo = parts[-1]

            branches_to_try = self._distinct_sequence([branch, 'main', 'master'])

            headers = {
                'Accept': 'application/vnd.github.v3+json',
                'User-Agent': 'LEDMatrix-Plugin-Manager/1.0'
            }

            if self.github_token:
                headers['Authorization'] = f'token {self.github_token}'

            last_error = None
            for branch_name in branches_to_try:
                api_url = f"https://api.github.com/repos/{owner}/{repo}/commits/{branch_name}"
                response = requests.get(api_url, headers=headers, timeout=10)
                if response.status_code == 200:
                    commit_data = response.json()
                    commit_sha_full = commit_data.get('sha', '')
                    commit_sha_short = commit_sha_full[:7] if commit_sha_full else ''
                    commit_meta = commit_data.get('commit', {})
                    commit_author = commit_meta.get('author', {})
                    commit_date_iso = commit_author.get('date', '')

                    return {
                        'branch': branch_name,
                        'sha': commit_sha_full,
                        'short_sha': commit_sha_short,
                        'date_iso': commit_date_iso,
                        'date': self._iso_to_date(commit_date_iso),
                        'author': commit_author.get('name', ''),
                        'message': commit_meta.get('message', ''),
                    }

                if response.status_code == 403 and not self.github_token:
                    self.logger.debug("GitHub commit API rate limited (403). Consider adding a token.")
                    last_error = response.text
                else:
                    last_error = response.text

            if last_error:
                self.logger.debug(f"Unable to fetch commit info for {repo_url}: {last_error}")

        except Exception as e:
            self.logger.debug(f"Error fetching latest commit metadata for {repo_url}: {e}")

        return None
    
    
    def get_plugin_info(self, plugin_id: str, fetch_latest_from_github: bool = True) -> Optional[Dict]:
        """
        Get detailed information about a plugin from the registry.

        GitHub provides authoritative metadata such as stars and the latest
        commit. The registry supplies descriptive information (name, id, repo URL).
        
        Args:
            plugin_id: Plugin identifier
            fetch_latest_from_github: If True (default), augment with GitHub commit metadata.
            
        Returns:
            Plugin metadata or None if not found
        """
        registry = self.fetch_registry()
        plugins = registry.get('plugins', []) or []
        plugin_info = next((p for p in plugins if p['id'] == plugin_id), None)
        
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

                commit_info = self._get_latest_commit_info(repo_url, branch)
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
                github_manifest = self._fetch_manifest_from_github(repo_url, branch, manifest_rel)
                if github_manifest:
                    if 'last_updated' in github_manifest and not plugin_info.get('last_updated'):
                        plugin_info['last_updated'] = github_manifest['last_updated']
                    if 'description' in github_manifest:
                        plugin_info['description'] = github_manifest['description']
        
        return plugin_info
    
    def install_plugin(self, plugin_id: str, branch: Optional[str] = None) -> bool:
        """
        Install a plugin from the official registry. Always installs the latest commit
        from the repository's default branch (or specified branch).
        
        Args:
            plugin_id: Plugin identifier
            branch: Optional branch name to install from. If provided, this branch will be
                   prioritized. If not provided or branch doesn't exist, falls back to
                   default branch logic.
        """
        branch_info = f" (branch: {branch})" if branch else " (latest branch head)"
        self.logger.info(f"Installing plugin: {plugin_id}{branch_info}")

        plugin_info = self.get_plugin_info(plugin_id, fetch_latest_from_github=True)
        if not plugin_info:
            self.logger.error(f"Plugin not found in registry: {plugin_id}")
            return False

        repo_url = plugin_info.get('repo')
        if not repo_url:
            self.logger.error(f"Plugin {plugin_id} missing repository URL")
            return False

        plugin_subpath = plugin_info.get('plugin_path')
        # If branch is provided, prioritize it; otherwise use default logic
        branch_candidates = self._distinct_sequence([
            branch,  # User-specified branch takes highest priority
            plugin_info.get('branch'),
            plugin_info.get('default_branch'),
            plugin_info.get('last_commit_branch'),
            'main',
            'master'
        ])

        # Use manifest ID for directory name (not registry plugin_id) to ensure consistency
        # We'll read the manifest after installation to get the actual ID
        # For now, use plugin_id but we'll correct it after reading manifest
        plugin_path = self.plugins_dir / plugin_id
        if plugin_path.exists():
            self.logger.warning(f"Plugin directory already exists: {plugin_id}. Removing it before reinstall.")
            if not self._safe_remove_directory(plugin_path):
                self.logger.error(f"Failed to remove existing plugin directory: {plugin_path}")
                return False

        try:
            branch_used = None

            if plugin_subpath:
                self.logger.info(f"Installing from monorepo subdirectory: {plugin_subpath}")
                for candidate in branch_candidates:
                    download_url = f"{repo_url}/archive/refs/heads/{candidate}.zip"
                    if self._install_from_monorepo(download_url, plugin_subpath, plugin_path):
                        branch_used = candidate
                        break

                if branch_used is None:
                    self.logger.error(f"Failed to install plugin from monorepo path {plugin_subpath} for {plugin_id}")
                    return False
            else:
                branch_used = self._install_via_git(repo_url, plugin_path, branch_candidates)
                if branch_used is None and not plugin_path.exists():
                    # Git failed entirely; fall back to zip download
                    self.logger.info("Git not available or clone failed, attempting archive download...")
                    for candidate in branch_candidates:
                        download_url = f"{repo_url}/archive/refs/heads/{candidate}.zip"
                        if self._install_via_download(download_url, plugin_path):
                            branch_used = candidate
                            break

                if branch_used is None and not plugin_path.exists():
                    self.logger.error(f"Failed to install plugin {plugin_id} via git or archive download")
                    return False

            manifest_path = plugin_path / "manifest.json"
            if not manifest_path.exists():
                self.logger.error(f"No manifest.json found in plugin: {plugin_id}")
                shutil.rmtree(plugin_path, ignore_errors=True)
                return False

            try:
                with open(manifest_path, 'r', encoding='utf-8') as mf:
                    manifest = json.load(mf)

                # Get the actual plugin ID from manifest (source of truth)
                manifest_plugin_id = manifest.get('id')
                if not manifest_plugin_id:
                    self.logger.error(f"Plugin manifest missing 'id' field")
                    shutil.rmtree(plugin_path, ignore_errors=True)
                    return False
                
                # If manifest ID doesn't match directory name, rename directory to match manifest
                if manifest_plugin_id != plugin_id:
                    self.logger.warning(
                        f"Manifest ID '{manifest_plugin_id}' doesn't match registry ID '{plugin_id}'. "
                        f"Renaming directory to match manifest ID."
                    )
                    correct_path = self.plugins_dir / manifest_plugin_id
                    if correct_path.exists():
                        self.logger.warning(f"Target directory {manifest_plugin_id} already exists, removing it")
                        shutil.rmtree(correct_path)
                    shutil.move(str(plugin_path), str(correct_path))
                    plugin_path = correct_path
                    manifest_path = plugin_path / "manifest.json"
                    # Update plugin_id to match manifest for rest of function
                    plugin_id = manifest_plugin_id

                required_fields = ['id', 'name', 'class_name', 'display_modes']
                missing = [field for field in required_fields if field not in manifest]

                manifest_modified = False

                if 'class_name' in missing:
                    entry_point = manifest.get('entry_point', 'manager.py')
                    manager_file = plugin_path / entry_point
                    if manager_file.exists():
                        try:
                            detected_class = self._detect_class_name(manager_file)
                            if detected_class:
                                manifest['class_name'] = detected_class
                                missing.remove('class_name')
                                manifest_modified = True
                                self.logger.info(f"Auto-detected class_name '{detected_class}' from {entry_point}")
                        except Exception as err:
                            self.logger.warning(f"Could not auto-detect class_name for {plugin_id}: {err}")

                if missing:
                    self.logger.error(f"Plugin manifest missing required fields for {plugin_id}: {', '.join(missing)}")
                    shutil.rmtree(plugin_path, ignore_errors=True)
                    return False

                if 'entry_point' not in manifest:
                    manifest['entry_point'] = 'manager.py'
                    manifest_modified = True
                    self.logger.info(f"Added missing entry_point field to {plugin_id} manifest (defaulted to manager.py)")

                if manifest_modified:
                    with open(manifest_path, 'w', encoding='utf-8') as mf:
                        json.dump(manifest, mf, indent=2)

            except Exception as manifest_error:
                self.logger.error(f"Failed to read/validate manifest for {plugin_id}: {manifest_error}")
                shutil.rmtree(plugin_path, ignore_errors=True)
                return False

            if not self._install_dependencies(plugin_path):
                self.logger.warning(f"Some dependencies may not have installed correctly for {plugin_id}")

            branch_display = branch_used or plugin_info.get('branch') or plugin_info.get('default_branch', 'unknown')
            self.logger.info(f"Successfully installed plugin: {plugin_id} (branch {branch_display})")
            return True

        except Exception as e:
            self.logger.error(f"Error installing plugin {plugin_id}: {e}", exc_info=True)
            if plugin_path.exists():
                shutil.rmtree(plugin_path, ignore_errors=True)
            return False
    
    def install_from_url(self, repo_url: str, plugin_id: str = None, plugin_path: str = None, branch: Optional[str] = None) -> Dict[str, Any]:
        """
        Install a plugin directly from a GitHub URL.
        This allows users to install custom/unverified plugins.
        
        Supports two installation modes:
        1. Direct plugin repo: Repository contains a single plugin with manifest.json at root
        2. Monorepo with plugin_path: Repository contains multiple plugins, install from subdirectory
        
        Args:
            repo_url: GitHub repository URL (e.g., https://github.com/user/repo)
            plugin_id: Optional plugin ID (extracted from manifest if not provided)
            plugin_path: Optional subdirectory path for monorepo installations (e.g., "plugins/hello-world")
            branch: Optional branch name to install from. If provided, this branch will be
                   prioritized. If not provided or branch doesn't exist, falls back to
                   default branch logic (main, then master).
            
        Returns:
            Dict with status and plugin_id or error message
        """
        branch_info = f" (branch: {branch})" if branch else ""
        self.logger.info(f"Installing plugin from custom URL: {repo_url}{branch_info}" + (f" (subpath: {plugin_path})" if plugin_path else ""))
        
        # Clean up URL (remove .git suffix if present)
        repo_url = repo_url.rstrip('/').replace('.git', '')
        
        temp_dir = None
        try:
            # Create temporary directory
            temp_dir = Path(tempfile.mkdtemp(prefix='ledmatrix_plugin_'))
            
            # Build branch candidates list - prioritize user-specified branch
            branch_candidates = self._distinct_sequence([branch, 'main', 'master']) if branch else ['main', 'master']
            
            # For monorepo installations, download and extract subdirectory
            if plugin_path:
                branch_used = None
                for candidate in branch_candidates:
                    download_url = f"{repo_url}/archive/refs/heads/{candidate}.zip"
                    if self._install_from_monorepo(download_url, plugin_path, temp_dir):
                        branch_used = candidate
                        break
                
                if branch_used is None:
                    return {
                        'success': False,
                        'error': f'Failed to download or extract plugin from monorepo subdirectory: {plugin_path}'
                    }
            else:
                # Try git clone for direct plugin repos
                branch_used = self._install_via_git(repo_url, temp_dir, branch_candidates)
                if branch_used:
                    self.logger.info(f"Cloned via git (branch: {branch_used})")
                else:
                    # Git failed; try downloading as zip
                    branch_used = None
                    for candidate in branch_candidates:
                        download_url = f"{repo_url}/archive/refs/heads/{candidate}.zip"
                        if self._install_via_download(download_url, temp_dir):
                            branch_used = candidate
                            break
                    
                    if branch_used is None:
                        return {
                            'success': False,
                            'error': 'Failed to clone or download repository'
                        }
            
            # Read manifest to get plugin ID
            manifest_path = temp_dir / "manifest.json"
            if not manifest_path.exists():
                return {
                    'success': False,
                    'error': 'No manifest.json found in repository' + (f' at path: {plugin_path}' if plugin_path else '')
                }
            
            with open(manifest_path, 'r') as f:
                manifest = json.load(f)
            
            plugin_id = plugin_id or manifest.get('id')
            if not plugin_id:
                return {
                    'success': False,
                    'error': 'No plugin ID found in manifest'
                }
            
            # Validate manifest has required fields
            required_fields = ['id', 'name', 'class_name', 'display_modes']
            missing_fields = [field for field in required_fields if field not in manifest]
            if missing_fields:
                return {
                    'success': False,
                    'error': f'Manifest missing required fields: {", ".join(missing_fields)}'
                }
            
            # Validate version fields consistency (warnings only, not required)
            validation_errors = self._validate_manifest_version_fields(manifest)
            if validation_errors:
                self.logger.warning(f"Manifest version field validation warnings for {plugin_id}: {', '.join(validation_errors)}")
            
            # Optional: Full schema validation if available
            schema_errors = self._validate_manifest_schema(manifest, plugin_id)
            if schema_errors:
                self.logger.warning(f"Manifest schema validation warnings for {plugin_id}: {', '.join(schema_errors)}")
            
            # entry_point is optional, default to "manager.py" if not specified
            if 'entry_point' not in manifest:
                manifest['entry_point'] = 'manager.py'
                # Write updated manifest back to file
                with open(manifest_path, 'w') as f:
                    json.dump(manifest, f, indent=2)
                self.logger.info(f"Added missing entry_point field to {plugin_id} manifest (defaulted to manager.py)")
            
            # Move to plugins directory - use manifest ID as source of truth
            # This ensures directory name always matches manifest ID
            final_path = self.plugins_dir / plugin_id
            if final_path.exists():
                self.logger.warning(f"Plugin {plugin_id} already exists, removing existing copy")
                if not self._safe_remove_directory(final_path):
                    return {
                        'success': False,
                        'error': f'Failed to remove existing plugin directory: {final_path}'
                    }
            
            shutil.move(str(temp_dir), str(final_path))
            temp_dir = None  # Prevent cleanup since we moved it
            
            # Note: plugin_id here is already from manifest (line 749), so directory name matches manifest ID
            
            # Install dependencies
            self._install_dependencies(final_path)
            
            branch_info = f" (branch: {branch_used})" if branch_used else ""
            self.logger.info(f"Successfully installed plugin from URL: {plugin_id}{branch_info}")
            result = {
                'success': True,
                'plugin_id': plugin_id,
                'name': manifest.get('name')
            }
            if branch_used:
                result['branch'] = branch_used
            return result
            
        except json.JSONDecodeError as e:
            self.logger.error(f"Error parsing manifest JSON: {e}")
            return {
                'success': False,
                'error': f'Invalid manifest.json: {str(e)}'
            }
        except Exception as e:
            self.logger.error(f"Error installing from URL: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e)
            }
        finally:
            # Cleanup temp directory if it still exists
            if temp_dir and temp_dir.exists():
                shutil.rmtree(temp_dir)
    
    def _detect_class_name(self, manager_file: Path) -> Optional[str]:
        """
        Attempt to auto-detect the plugin class name from the manager file.
        
        Args:
            manager_file: Path to the manager.py file
            
        Returns:
            Class name if found, None otherwise
        """
        try:
            import re
            with open(manager_file, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Look for class definition that inherits from BasePlugin
            pattern = r'class\s+(\w+)\s*\([^)]*BasePlugin[^)]*\)'
            match = re.search(pattern, content)
            if match:
                return match.group(1)
            
            # Fallback: find first class definition
            pattern = r'^class\s+(\w+)'
            match = re.search(pattern, content, re.MULTILINE)
            if match:
                return match.group(1)
            
            return None
        except Exception as e:
            self.logger.warning(f"Error detecting class name from {manager_file}: {e}")
            return None
    
    def _install_via_git(self, repo_url: str, target_path: Path, branches: Optional[List[str]] = None) -> Optional[str]:
        """Clone a repository into ``target_path``. Returns the branch name on success."""
        branches_to_try = self._distinct_sequence(branches or [])
        if not branches_to_try:
            branches_to_try = ['main', 'master']

        last_error = None
        for try_branch in branches_to_try:
            try:
                cmd = ['git', 'clone', '--depth', '1', '--branch', try_branch, repo_url, str(target_path)]
                subprocess.run(
                    cmd,
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=60
                )
                self.logger.debug(f"Successfully cloned {repo_url} (branch: {try_branch}) to {target_path}")
                return try_branch
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as e:
                last_error = e
                self.logger.debug(f"Git clone failed for branch {try_branch}: {e}")
                if target_path.exists():
                    shutil.rmtree(target_path)

        # Try default branch (Git's configured default) as last resort
        try:
            cmd = ['git', 'clone', '--depth', '1', repo_url, str(target_path)]
            subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                text=True,
                timeout=60
            )
            self.logger.debug(f"Successfully cloned {repo_url} (git default branch) to {target_path}")
            return None  # Unknown branch name, git default used
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as e:
            last_error = e
            if target_path.exists():
                shutil.rmtree(target_path)

        self.logger.error(f"Git clone failed for all attempted branches: {last_error}")
        return None
    
    def _install_from_monorepo(self, download_url: str, plugin_subpath: str, target_path: Path) -> bool:
        """
        Install a plugin from a monorepo by downloading and extracting a subdirectory.
        
        Args:
            download_url: URL to download zip from
            plugin_subpath: Path within repo (e.g., "plugins/hello-world")
            target_path: Target directory for plugin
            
        Returns:
            True if successful
        """
        try:
            self.logger.info(f"Downloading monorepo from: {download_url}")
            response = self._http_get_with_retries(download_url, timeout=60, stream=True)
            response.raise_for_status()
            
            # Download to temporary file
            with tempfile.NamedTemporaryFile(suffix='.zip', delete=False) as tmp_file:
                for chunk in response.iter_content(chunk_size=8192):
                    tmp_file.write(chunk)
                tmp_zip_path = tmp_file.name
            
            try:
                # Extract zip
                with zipfile.ZipFile(tmp_zip_path, 'r') as zip_ref:
                    zip_contents = zip_ref.namelist()
                    if not zip_contents:
                        return False
                    
                    # GitHub zips have a root directory like "repo-main/"
                    root_dir = zip_contents[0].split('/')[0]
                    
                    # Build path to plugin within extracted archive
                    # e.g., "ledmatrix-plugins-main/plugins/hello-world/"
                    plugin_path_in_zip = f"{root_dir}/{plugin_subpath}/"
                    
                    # Extract to temp location
                    temp_extract = Path(tempfile.mkdtemp())
                    zip_ref.extractall(temp_extract)
                    
                    # Find the plugin directory
                    source_plugin_dir = temp_extract / root_dir / plugin_subpath
                    
                    if not source_plugin_dir.exists():
                        self.logger.error(f"Plugin path not found in archive: {plugin_subpath}")
                        self.logger.error(f"Expected at: {source_plugin_dir}")
                        shutil.rmtree(temp_extract, ignore_errors=True)
                        return False
                    
                    # Move plugin contents to target
                    from src.common.permission_utils import (
                        ensure_directory_permissions,
                        get_plugin_dir_mode
                    )
                    ensure_directory_permissions(target_path.parent, get_plugin_dir_mode())
                    shutil.move(str(source_plugin_dir), str(target_path))
                    
                    # Cleanup temp extract dir
                    if temp_extract.exists():
                        shutil.rmtree(temp_extract, ignore_errors=True)
                
                return True
                
            finally:
                # Remove temporary zip file
                if os.path.exists(tmp_zip_path):
                    os.remove(tmp_zip_path)
            
        except Exception as e:
            self.logger.error(f"Monorepo download failed: {e}", exc_info=True)
            return False
    
    def _install_via_download(self, download_url: str, target_path: Path) -> bool:
        """
        Install plugin by downloading and extracting zip archive.
        
        Args:
            download_url: URL to download zip from
            target_path: Target directory
            
        Returns:
            True if successful
        """
        try:
            self.logger.info(f"Downloading from: {download_url}")
            # Allow redirects (GitHub archive URLs redirect to codeload.github.com)
            response = self._http_get_with_retries(download_url, timeout=60, stream=True, headers={'User-Agent': 'LEDMatrix-Plugin-Manager/1.0'})
            response.raise_for_status()
            
            # Download to temporary file
            with tempfile.NamedTemporaryFile(suffix='.zip', delete=False) as tmp_file:
                for chunk in response.iter_content(chunk_size=8192):
                    tmp_file.write(chunk)
                tmp_zip_path = tmp_file.name
            
            try:
                # Extract zip
                with zipfile.ZipFile(tmp_zip_path, 'r') as zip_ref:
                    # GitHub zips have a root directory, we need to extract contents
                    zip_contents = zip_ref.namelist()
                    if not zip_contents:
                        return False
                    
                    # Find the root directory in the zip
                    root_dir = zip_contents[0].split('/')[0]
                    
                    # Extract to temp location
                    temp_extract = Path(tempfile.mkdtemp())
                    zip_ref.extractall(temp_extract)
                    
                    # Move contents from root_dir to target
                    source_dir = temp_extract / root_dir
                    if source_dir.exists():
                        from src.common.permission_utils import (
                            ensure_directory_permissions,
                            get_plugin_dir_mode
                        )
                        ensure_directory_permissions(target_path.parent, get_plugin_dir_mode())
                        shutil.move(str(source_dir), str(target_path))
                    else:
                        # No root dir, move everything
                        shutil.move(str(temp_extract), str(target_path))
                    
                    # Cleanup temp extract dir
                    if temp_extract.exists():
                        shutil.rmtree(temp_extract, ignore_errors=True)
                
                return True
                
            finally:
                # Remove temporary zip file
                if os.path.exists(tmp_zip_path):
                    os.remove(tmp_zip_path)
            
        except Exception as e:
            self.logger.error(f"Download failed: {e}")
            return False
    
    def _install_dependencies(self, plugin_path: Path) -> bool:
        """
        Install Python dependencies from requirements.txt.
        
        Args:
            plugin_path: Path to plugin directory
            
        Returns:
            True if successful or no requirements file
        """
        requirements_file = plugin_path / "requirements.txt"
        
        if not requirements_file.exists():
            self.logger.debug(f"No requirements.txt found in {plugin_path.name}")
            return True
        
        try:
            self.logger.info(f"Installing dependencies for {plugin_path.name}")
            result = subprocess.run(
                ['pip3', 'install', '--break-system-packages', '-r', str(requirements_file)],
                check=True,
                capture_output=True,
                text=True,
                timeout=300
            )
            self.logger.info(f"Dependencies installed successfully for {plugin_path.name}")
            return True
            
        except subprocess.CalledProcessError as e:
            self.logger.error(f"Error installing dependencies: {e.stderr}")
            return False
        except subprocess.TimeoutExpired:
            self.logger.error("Dependency installation timed out")
            return False
        except (BrokenPipeError, OSError) as e:
            # Handle broken pipe errors (errno 32) which can occur during pip downloads
            # Often caused by network interruptions or output buffer issues
            if isinstance(e, OSError) and e.errno == 32:
                self.logger.error(
                    f"Broken pipe error during dependency installation for {plugin_path.name}. "
                    f"This usually indicates a network interruption or pip output buffer issue. "
                    f"Try installing again or check your network connection."
                )
            else:
                self.logger.error(f"OS error during dependency installation: {e}")
            return False
        except Exception as e:
            # Catch any other unexpected errors
            self.logger.error(f"Unexpected error installing dependencies for {plugin_path.name}: {e}", exc_info=True)
            return False

    def _get_local_git_info(self, plugin_path: Path) -> Optional[Dict[str, str]]:
        """Return local git branch, commit hash, and commit date if the plugin is a git checkout."""
        git_dir = plugin_path / '.git'
        if not git_dir.exists():
            return None

        try:
            sha_result = subprocess.run(
                ['git', '-C', str(plugin_path), 'rev-parse', 'HEAD'],
                capture_output=True,
                text=True,
                timeout=10,
                check=True
            )
            sha = sha_result.stdout.strip()

            branch_result = subprocess.run(
                ['git', '-C', str(plugin_path), 'rev-parse', '--abbrev-ref', 'HEAD'],
                capture_output=True,
                text=True,
                timeout=10,
                check=True
            )
            branch = branch_result.stdout.strip()

            if branch == 'HEAD':
                branch = ''

            # Get remote URL
            remote_url_result = subprocess.run(
                ['git', '-C', str(plugin_path), 'config', '--get', 'remote.origin.url'],
                capture_output=True,
                text=True,
                timeout=10,
                check=False
            )
            remote_url = remote_url_result.stdout.strip() if remote_url_result.returncode == 0 else None

            # Get commit date in ISO format
            date_result = subprocess.run(
                ['git', '-C', str(plugin_path), 'log', '-1', '--format=%cI', 'HEAD'],
                capture_output=True,
                text=True,
                timeout=10,
                check=True
            )
            commit_date_iso = date_result.stdout.strip()

            result = {
                'sha': sha,
                'short_sha': sha[:7] if sha else '',
                'branch': branch
            }
            
            # Add remote URL if available
            if remote_url:
                result['remote_url'] = remote_url

            # Add commit date if available
            if commit_date_iso:
                result['date_iso'] = commit_date_iso
                result['date'] = self._iso_to_date(commit_date_iso)

            return result
        except subprocess.CalledProcessError as err:
            self.logger.debug(f"Failed to read git info for {plugin_path.name}: {err}")
        except subprocess.TimeoutExpired:
            self.logger.debug(f"Timed out reading git info for {plugin_path.name}")

        return None
    
    def _safe_remove_directory(self, path: Path) -> bool:
        """
        Safely remove a directory, handling permission errors for __pycache__ directories.
        
        This function attempts to remove a directory and handles permission errors
        gracefully, especially for __pycache__ directories that may have been created
        by Python with different permissions.
        
        Args:
            path: Path to directory to remove
            
        Returns:
            True if directory was removed successfully, False otherwise
        """
        if not path.exists():
            return True  # Already removed
        
        try:
            # First, try normal removal
            shutil.rmtree(path)
            return True
        except PermissionError as e:
            # Handle permission errors, especially for __pycache__ directories
            self.logger.warning(f"Permission error removing {path}: {e}. Attempting to fix permissions...")
            
            try:
                # Try to fix permissions on __pycache__ directories recursively
                import stat
                for root, dirs, files in os.walk(path):
                    root_path = Path(root)
                    try:
                        # Make directory writable
                        os.chmod(root_path, stat.S_IRWXU | stat.S_IRWXG | stat.S_IRWXO)
                    except (OSError, PermissionError):
                        pass
                    
                    # Fix file permissions
                    for file in files:
                        file_path = root_path / file
                        try:
                            os.chmod(file_path, stat.S_IRWXU | stat.S_IRWXG | stat.S_IRWXO)
                        except (OSError, PermissionError):
                            pass
                
                # Try removal again after fixing permissions
                shutil.rmtree(path)
                self.logger.info(f"Successfully removed {path} after fixing permissions")
                return True
            except Exception as e2:
                self.logger.error(f"Failed to remove {path} even after fixing permissions: {e2}")
                # Last resort: try with ignore_errors
                try:
                    shutil.rmtree(path, ignore_errors=True)
                    # Check if it actually got removed
                    if not path.exists():
                        self.logger.warning(f"Removed {path} with ignore_errors=True (some files may remain)")
                        return True
                    else:
                        self.logger.error(f"Could not remove {path} even with ignore_errors")
                        return False
                except Exception as e3:
                    self.logger.error(f"Final removal attempt failed for {path}: {e3}")
                    return False
        except Exception as e:
            self.logger.error(f"Unexpected error removing {path}: {e}")
            return False
    
    def _find_plugin_path(self, plugin_id: str) -> Optional[Path]:
        """
        Find the plugin path by checking the configured directory and standard plugins directory.
        
        Args:
            plugin_id: Plugin identifier
            
        Returns:
            Path to plugin directory if found, None otherwise
        """
        # First check the configured plugins directory
        plugin_path = self.plugins_dir / plugin_id
        if plugin_path.exists():
            return plugin_path
        
        # Also check the standard 'plugins/' directory if it's different
        # This handles the case where plugins are in plugins/ but config says plugin-repos/
        try:
            if self.plugins_dir.is_absolute():
                project_root = self.plugins_dir.parent
            else:
                project_root = self.plugins_dir.resolve().parent
            
            standard_plugins_dir = project_root / 'plugins'
            if standard_plugins_dir.exists() and standard_plugins_dir != self.plugins_dir:
                plugin_path = standard_plugins_dir / plugin_id
                if plugin_path.exists():
                    return plugin_path
        except (OSError, ValueError):
            pass
        
        return None
    
    def uninstall_plugin(self, plugin_id: str) -> bool:
        """
        Uninstall a plugin by removing its directory.
        
        Args:
            plugin_id: Plugin identifier
            
        Returns:
            True if uninstalled successfully (or already not installed)
        """
        plugin_path = self._find_plugin_path(plugin_id)
        
        if plugin_path is None or not plugin_path.exists():
            self.logger.info(f"Plugin {plugin_id} not found (already uninstalled)")
            return True  # Already uninstalled, consider this success
        
        try:
            self.logger.info(f"Uninstalling plugin: {plugin_id}")
            if self._safe_remove_directory(plugin_path):
                self.logger.info(f"Successfully uninstalled plugin: {plugin_id}")
                return True
            else:
                self.logger.error(f"Failed to remove plugin directory: {plugin_path}")
                return False
        except Exception as e:
            self.logger.error(f"Error uninstalling plugin {plugin_id}: {e}")
            return False
    
    def update_plugin(self, plugin_id: str) -> bool:
        """
        Update a plugin to the latest commit on its upstream branch.
        """
        plugin_path = self._find_plugin_path(plugin_id)
        
        if plugin_path is None or not plugin_path.exists():
            self.logger.error(f"Plugin not installed: {plugin_id}")
            return False
        
        try:
            self.logger.info(f"Checking for updates to plugin {plugin_id}")
            
            # First check if it's a git repository - if so, we can update directly
            git_info = self._get_local_git_info(plugin_path)
            
            if git_info:
                # Plugin is a git repository - try to update via git
                local_branch = git_info.get('branch') or 'main'
                local_sha = git_info.get('sha')

                # Try to get remote info from registry (optional)
                self.fetch_registry(force_refresh=True)
                plugin_info_remote = self.get_plugin_info(plugin_id, fetch_latest_from_github=True)
                remote_branch = None
                remote_sha = None

                if plugin_info_remote:
                    remote_branch = plugin_info_remote.get('branch') or plugin_info_remote.get('default_branch')
                    remote_sha = plugin_info_remote.get('last_commit_sha')

                    # Check if the local git remote still matches the registry repo URL.
                    # After monorepo migration, old clones point to archived individual repos
                    # while the registry now points to the monorepo. Detect this and reinstall.
                    registry_repo = plugin_info_remote.get('repo', '')
                    local_remote = git_info.get('remote_url', '')
                    # Normalize for comparison: strip trailing slashes and .git suffix
                    def _normalize_url(url):
                        url = url.rstrip('/')
                        if url.endswith('.git'):
                            url = url[:-4]
                        return url.lower()
                    if local_remote and registry_repo and _normalize_url(local_remote) != _normalize_url(registry_repo):
                        self.logger.info(
                            f"Plugin {plugin_id} git remote ({local_remote}) differs from registry ({registry_repo}). "
                            f"Reinstalling from registry to migrate to new source."
                        )
                        shutil.rmtree(plugin_path, ignore_errors=True)
                        return self.install_plugin(plugin_id)

                    # Check if already up to date
                    if remote_sha and local_sha and remote_sha.startswith(local_sha):
                        self.logger.info(f"Plugin {plugin_id} already matches remote commit {remote_sha[:7]}")
                        return True

                # Update via git pull
                self.logger.info(f"Updating {plugin_id} via git pull (local branch: {local_branch})...")
                try:
                    # Fetch latest changes first to get all remote branch info
                    # If fetch fails, we'll still try to pull (might work with existing remote refs)
                    fetch_result = subprocess.run(
                        ['git', '-C', str(plugin_path), 'fetch', 'origin'],
                        capture_output=True,
                        text=True,
                        timeout=60,
                        check=False
                    )
                    if fetch_result.returncode != 0:
                        self.logger.warning(f"Git fetch failed for {plugin_id}: {fetch_result.stderr or fetch_result.stdout}. Will still attempt pull.")
                    else:
                        self.logger.debug(f"Successfully fetched remote changes for {plugin_id}")

                    # Determine which remote branch to pull from
                    # Strategy: Use what the local branch is tracking, or find the best match
                    remote_pull_branch = None
                    
                    # First, check what the local branch is tracking
                    tracking_result = subprocess.run(
                        ['git', '-C', str(plugin_path), 'rev-parse', '--abbrev-ref', '--symbolic-full-name', f'{local_branch}@{{upstream}}'],
                        capture_output=True,
                        text=True,
                        timeout=10,
                        check=False
                    )
                    
                    if tracking_result.returncode == 0 and tracking_result.stdout.strip():
                        # Local branch is tracking a remote branch
                        tracking_ref = tracking_result.stdout.strip()
                        # Extract branch name from refs/remotes/origin/branch-name or origin/branch-name
                        if tracking_ref.startswith('refs/remotes/origin/'):
                            remote_pull_branch = tracking_ref.replace('refs/remotes/origin/', '')
                            self.logger.info(f"Local branch {local_branch} is tracking origin/{remote_pull_branch}")
                        elif tracking_ref.startswith('origin/'):
                            remote_pull_branch = tracking_ref.replace('origin/', '')
                            self.logger.info(f"Local branch {local_branch} is tracking origin/{remote_pull_branch}")
                    
                    # If not tracking anything, try to find the best remote branch match
                    if not remote_pull_branch:
                        # Check if remote branch from registry exists
                        if remote_branch:
                            remote_check = subprocess.run(
                                ['git', '-C', str(plugin_path), 'ls-remote', '--heads', 'origin', remote_branch],
                                capture_output=True,
                                text=True,
                                timeout=10,
                                check=False
                            )
                            if remote_check.returncode == 0 and remote_check.stdout.strip():
                                remote_pull_branch = remote_branch
                                self.logger.info(f"Using remote branch {remote_branch} from registry")
                        
                        # If registry branch doesn't exist, check if local branch name exists on remote
                        if not remote_pull_branch:
                            local_as_remote_check = subprocess.run(
                                ['git', '-C', str(plugin_path), 'ls-remote', '--heads', 'origin', local_branch],
                                capture_output=True,
                                text=True,
                                timeout=10,
                                check=False
                            )
                            if local_as_remote_check.returncode == 0 and local_as_remote_check.stdout.strip():
                                remote_pull_branch = local_branch
                                self.logger.info(f"Using local branch name {local_branch} as remote branch")
                        
                        # Last resort: try to get remote's default branch
                        if not remote_pull_branch:
                            default_branch_result = subprocess.run(
                                ['git', '-C', str(plugin_path), 'symbolic-ref', 'refs/remotes/origin/HEAD'],
                                capture_output=True,
                                text=True,
                                timeout=10,
                                check=False
                            )
                            if default_branch_result.returncode == 0:
                                default_ref = default_branch_result.stdout.strip()
                                if default_ref.startswith('refs/remotes/origin/'):
                                    remote_pull_branch = default_ref.replace('refs/remotes/origin/', '')
                                    self.logger.info(f"Using remote default branch {remote_pull_branch}")
                    
                    # If we still don't have a remote branch, use local branch name (git will handle it)
                    if not remote_pull_branch:
                        remote_pull_branch = local_branch
                        self.logger.info(f"Falling back to local branch name {local_branch} for pull")
                    
                    # Ensure we're on the local branch
                    checkout_result = subprocess.run(
                        ['git', '-C', str(plugin_path), 'checkout', local_branch],
                        capture_output=True,
                        text=True,
                        timeout=30,
                        check=False
                    )
                    if checkout_result.returncode != 0:
                        self.logger.warning(f"Git checkout to {local_branch} failed for {plugin_id}: {checkout_result.stderr or checkout_result.stdout}. Will still attempt pull.")

                    # Check for local changes and untracked files that might conflict
                    # First, check for untracked files that would be overwritten
                    try:
                        # Check for untracked files
                        untracked_result = subprocess.run(
                            ['git', '-C', str(plugin_path), 'status', '--porcelain', '--untracked-files=all'],
                            capture_output=True,
                            text=True,
                            timeout=30,
                            check=False
                        )
                        untracked_files = []
                        if untracked_result.returncode == 0:
                            for line in untracked_result.stdout.strip().split('\n'):
                                if line.startswith('??'):
                                    # Untracked file
                                    file_path = line[3:].strip()
                                    untracked_files.append(file_path)
                        
                        # Remove marker files that are safe to delete (they'll be regenerated)
                        safe_to_remove = ['.dependencies_installed']
                        removed_files = []
                        for file_name in safe_to_remove:
                            file_path = plugin_path / file_name
                            if file_path.exists() and file_name in untracked_files:
                                try:
                                    file_path.unlink()
                                    removed_files.append(file_name)
                                    self.logger.info(f"Removed marker file {file_name} from {plugin_id} before update")
                                except Exception as e:
                                    self.logger.warning(f"Could not remove {file_name} from {plugin_id}: {e}")
                        
                        # Check for tracked file changes
                        status_result = subprocess.run(
                            ['git', '-C', str(plugin_path), 'status', '--porcelain', '--untracked-files=no'],
                            capture_output=True,
                            text=True,
                            timeout=30,
                            check=False
                        )
                        has_changes = bool(status_result.stdout.strip())
                        
                        # If there are remaining untracked files (not safe to remove), stash them
                        remaining_untracked = [f for f in untracked_files if f not in removed_files]
                        if remaining_untracked:
                            self.logger.info(f"Found {len(remaining_untracked)} untracked files in {plugin_id}, will stash them")
                            has_changes = True
                    except subprocess.TimeoutExpired:
                        # If status check times out, assume there might be changes and proceed
                        self.logger.warning(f"Git status check timed out for {plugin_id}, proceeding with update")
                        has_changes = True
                        status_result = type('obj', (object,), {'stdout': '', 'stderr': 'Status check timed out'})()
                    
                    stash_info = ""
                    if has_changes:
                        self.logger.info(f"Stashing local changes in {plugin_id} before update")
                        try:
                            # Use -u to include untracked files in stash
                            stash_result = subprocess.run(
                                ['git', '-C', str(plugin_path), 'stash', 'push', '-u', '-m', f'LEDMatrix auto-stash before update {plugin_id}'],
                                capture_output=True,
                                text=True,
                                timeout=30,
                                check=False
                            )
                            if stash_result.returncode == 0:
                                stash_info = " (local changes were stashed)"
                                self.logger.info(f"Stashed local changes (including untracked files) for {plugin_id}")
                            else:
                                self.logger.warning(f"Failed to stash local changes for {plugin_id}: {stash_result.stderr}")
                        except subprocess.TimeoutExpired:
                            self.logger.warning(f"Stash operation timed out for {plugin_id}, proceeding with pull")

                    # Pull from the determined remote branch
                    self.logger.info(f"Pulling from origin/{remote_pull_branch} for {plugin_id}...")
                    pull_result = subprocess.run(
                        ['git', '-C', str(plugin_path), 'pull', 'origin', remote_pull_branch],
                        capture_output=True,
                        text=True,
                        timeout=120,
                        check=True
                    )

                    pull_message = pull_result.stdout.strip() or f"Pulled latest changes for {plugin_id}"
                    if stash_info:
                        pull_message += stash_info
                    self.logger.info(pull_message)

                    updated_git_info = self._get_local_git_info(plugin_path) or {}
                    updated_sha = updated_git_info.get('sha', '')
                    if remote_sha and updated_sha and remote_sha.startswith(updated_sha):
                        self.logger.info(f"Plugin {plugin_id} now at remote commit {remote_sha[:7]}{stash_info}")
                    elif updated_sha:
                        self.logger.info(f"Plugin {plugin_id} updated to commit {updated_sha[:7]}{stash_info}")

                    self._install_dependencies(plugin_path)
                    return True

                except subprocess.CalledProcessError as git_error:
                    error_output = git_error.stderr or git_error.stdout or "Unknown error"
                    cmd_str = ' '.join(git_error.cmd) if hasattr(git_error, 'cmd') else 'unknown'
                    self.logger.error(f"Git update failed for {plugin_id}")
                    self.logger.error(f"Command: {cmd_str}")
                    self.logger.error(f"Return code: {git_error.returncode}")
                    self.logger.error(f"Error output: {error_output}")
                    
                    # Check for specific error conditions
                    error_lower = error_output.lower()
                    if "would be overwritten" in error_output or "local changes" in error_lower:
                        self.logger.warning(f"Plugin {plugin_id} has local changes that prevent update. Consider committing or stashing changes manually.")
                    elif "refusing to merge unrelated histories" in error_lower:
                        self.logger.error(f"Plugin {plugin_id} has unrelated git histories. Plugin may need to be reinstalled.")
                    elif "authentication" in error_lower or "permission denied" in error_lower:
                        self.logger.error(f"Authentication failed for {plugin_id}. Check git credentials or repository permissions.")
                    elif "not found" in error_lower or "does not exist" in error_lower:
                        self.logger.error(f"Remote branch or repository not found for {plugin_id}. Check repository URL and branch name.")
                    elif "merge conflict" in error_lower or "conflict" in error_lower:
                        self.logger.error(f"Merge conflict detected for {plugin_id}. Resolve conflicts manually or reinstall plugin.")
                    
                    return False
                except subprocess.TimeoutExpired:
                    self.logger.warning(f"Git update timed out for {plugin_id}")
                    return False
            
            # Not a git repository - try to get repo URL from git config if it exists
            # (in case .git directory was removed but remote URL is still in config)
            repo_url = None
            try:
                remote_url_result = subprocess.run(
                    ['git', '-C', str(plugin_path), 'config', '--get', 'remote.origin.url'],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False
                )
                if remote_url_result.returncode == 0:
                    repo_url = remote_url_result.stdout.strip()
                    self.logger.info(f"Found git remote URL for {plugin_id}: {repo_url}")
            except Exception as e:
                self.logger.debug(f"Could not get git remote URL: {e}")
            
            # Try registry-based update
            self.logger.info(f"Plugin {plugin_id} is not a git repository, checking registry...")
            self.fetch_registry(force_refresh=True)
            plugin_info_remote = self.get_plugin_info(plugin_id, fetch_latest_from_github=True)
            
            # If not in registry but we have a repo URL, try reinstalling from that URL
            if not plugin_info_remote and repo_url:
                self.logger.info(f"Plugin {plugin_id} not in registry but has git remote URL. Reinstalling from {repo_url} to enable updates...")
                try:
                    # Get current branch if possible
                    branch_result = subprocess.run(
                        ['git', '-C', str(plugin_path), 'rev-parse', '--abbrev-ref', 'HEAD'],
                        capture_output=True,
                        text=True,
                        timeout=10,
                        check=False
                    )
                    branch = branch_result.stdout.strip() if branch_result.returncode == 0 else None
                    if branch == 'HEAD' or not branch:
                        branch = 'main'
                    
                    # Reinstall from URL
                    result = self.install_from_url(repo_url, plugin_id=plugin_id, branch=branch)
                    if result.get('success'):
                        self.logger.info(f"Successfully reinstalled {plugin_id} from {repo_url} as git repository")
                        return True
                    else:
                        self.logger.warning(f"Failed to reinstall {plugin_id} from {repo_url}: {result.get('error')}")
                except Exception as e:
                    self.logger.error(f"Error reinstalling {plugin_id} from URL: {e}")
            
            if not plugin_info_remote:
                self.logger.warning(f"Plugin {plugin_id} not found in registry and not a git repository; cannot update automatically")
                if not repo_url:
                    self.logger.warning(f"Plugin may have been installed via ZIP download. Try reinstalling from GitHub URL to enable updates.")
                return False

            repo_url = plugin_info_remote.get('repo')
            remote_sha = plugin_info_remote.get('last_commit_sha')
            remote_branch = plugin_info_remote.get('branch') or plugin_info_remote.get('default_branch')

            # If we get here, plugin is not a git repo but is in registry - reinstall
            self.logger.info(f"Plugin {plugin_id} not installed via git; re-installing latest archive")

            # Remove directory and reinstall fresh
            shutil.rmtree(plugin_path, ignore_errors=True)
            return self.install_plugin(plugin_id)

        except Exception as e:
            import traceback
            self.logger.error(f"Error updating plugin {plugin_id}: {e}")
            self.logger.debug(traceback.format_exc())
            return False
    
    def list_installed_plugins(self) -> List[str]:
        """
        Get list of installed plugin IDs.
        
        Returns:
            List of plugin IDs
        """
        if not self.plugins_dir.exists():
            return []
        
        installed = []
        for item in self.plugins_dir.iterdir():
            if item.is_dir() and (item / "manifest.json").exists():
                installed.append(item.name)
        
        return installed
    
    def get_installed_plugin_info(self, plugin_id: str) -> Optional[Dict]:
        """
        Get manifest information for an installed plugin.
        
        Args:
            plugin_id: Plugin identifier
            
        Returns:
            Manifest data or None if not found
        """
        manifest_path = self.plugins_dir / plugin_id / "manifest.json"
        
        if not manifest_path.exists():
            return None
        
        try:
            with open(manifest_path, 'r') as f:
                return json.load(f)
        except Exception as e:
            self.logger.error(f"Error reading manifest for {plugin_id}: {e}")
            return None
