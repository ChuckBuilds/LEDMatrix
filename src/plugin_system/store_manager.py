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
from pathlib import Path
from typing import List, Dict, Optional, Any
import logging


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
        self.github_releases_cache = {}  # Cache for GitHub releases/tags
        self.cache_timeout = 3600  # 1 hour cache timeout
        self.registry_cache_timeout = 300  # 5 minutes for registry cache
        self.github_token = self._load_github_token()

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
                        repo_info = {
                            'stars': data.get('stargazers_count', 0),
                            'forks': data.get('forks_count', 0),
                            'open_issues': data.get('open_issues_count', 0),
                            'updated_at': data.get('updated_at', ''),
                            'language': data.get('language', ''),
                            'license': data.get('license', {}).get('name', '') if data.get('license') else ''
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

            return {'stars': 0, 'forks': 0, 'open_issues': 0, 'updated_at': '', 'language': '', 'license': ''}

        except Exception as e:
            self.logger.error(f"Error fetching GitHub repo info for {repo_url}: {e}")
            return {'stars': 0, 'forks': 0, 'open_issues': 0, 'updated_at': '', 'language': '', 'license': ''}

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
            return {"version": "1.0.0", "plugins": []}
        except json.JSONDecodeError as e:
            self.logger.error(f"Error parsing registry JSON: {e}")
            return {"version": "1.0.0", "plugins": []}
    
    def search_plugins(self, query: str = "", category: str = "", tags: List[str] = None, fetch_latest_versions: bool = False, include_saved_repos: bool = True, saved_repositories_manager = None) -> List[Dict]:
        """
        Search for plugins in the registry with enhanced metadata.

        Args:
            query: Search query string (searches name, description, id)
            category: Filter by category (e.g., 'sports', 'weather', 'time')
            tags: Filter by tags (matches any tag in list)
            fetch_latest_versions: If True, fetch latest manifest from GitHub for each plugin to get current versions

        Returns:
            List of matching plugin metadata with real stars and downloads
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

            # Enhance plugin data with real GitHub stars
            enhanced_plugin = plugin.copy()

            # Get real GitHub stars
            repo_url = plugin.get('repo', '')
            if repo_url:
                github_info = self._get_github_repo_info(repo_url)
                enhanced_plugin['stars'] = github_info.get('stars', plugin.get('stars', 0))
                
                # Optionally fetch latest manifest and releases from GitHub to get current version
                if fetch_latest_versions:
                    # First, try to get latest version from GitHub releases/tags (most accurate)
                    github_releases = self._fetch_github_releases(repo_url)
                    if github_releases and len(github_releases) > 0:
                        # Get the latest release (first in list, as they're sorted by date)
                        latest_release = github_releases[0]
                        latest_version = latest_release.get('version', '')
                        
                        if latest_version:
                            # Update latest_version field
                            enhanced_plugin['latest_version'] = latest_version
                            
                            # Add to versions array if not already present
                            if 'versions' not in enhanced_plugin or not isinstance(enhanced_plugin['versions'], list):
                                enhanced_plugin['versions'] = []
                            
                            # Check if this version is already in the array
                            existing_versions = [v.get('version', '') if isinstance(v, dict) else str(v) 
                                                for v in enhanced_plugin['versions']]
                            
                            if latest_version not in existing_versions:
                                # Add latest version to the front of versions array
                                enhanced_plugin['versions'].insert(0, {
                                    'version': latest_version,
                                    'ledmatrix_min': enhanced_plugin.get('versions', [{}])[0].get('ledmatrix_min', '2.0.0') if enhanced_plugin.get('versions') else '2.0.0',
                                    'released': latest_release.get('published_at', '').split('T')[0] if latest_release.get('published_at') else '',
                                    'download_url': f"https://github.com/{repo_url.split('/')[-2]}/{repo_url.split('/')[-1]}/archive/refs/tags/v{latest_version}.zip"
                                })
                    
                    # Also fetch manifest from GitHub for additional metadata
                    branch = plugin.get('branch', 'master')
                    github_manifest = self._fetch_manifest_from_github(repo_url, branch)
                    if github_manifest:
                        # Update version from GitHub manifest (if releases didn't provide it)
                        if 'version' in github_manifest and 'latest_version' not in enhanced_plugin:
                            enhanced_plugin['version'] = github_manifest['version']
                            enhanced_plugin['latest_version'] = github_manifest['version']
                        
                        # Update versions array if available and releases didn't update it
                        if 'versions' in github_manifest and 'latest_version' not in enhanced_plugin:
                            enhanced_plugin['versions'] = github_manifest['versions']
                        
                        # Update latest_version from versions array if not set
                        if 'latest_version' not in enhanced_plugin:
                            if 'versions' in enhanced_plugin and isinstance(enhanced_plugin['versions'], list) and len(enhanced_plugin['versions']) > 0:
                                latest_ver = enhanced_plugin['versions'][0]
                                if isinstance(latest_ver, dict) and 'version' in latest_ver:
                                    enhanced_plugin['latest_version'] = latest_ver['version']
                            elif 'version' in enhanced_plugin:
                                enhanced_plugin['latest_version'] = enhanced_plugin['version']
                        
                        # Update other fields that might be more current
                        if 'last_updated' in github_manifest:
                            enhanced_plugin['last_updated'] = github_manifest['last_updated']
                        if 'description' in github_manifest:
                            enhanced_plugin['description'] = github_manifest['description']

            results.append(enhanced_plugin)

        return results
    
    def _fetch_manifest_from_github(self, repo_url: str, branch: str = "master") -> Optional[Dict]:
        """
        Fetch manifest.json directly from a GitHub repository.
        
        Args:
            repo_url: GitHub repository URL
            branch: Branch name (default: master)
            
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
                    
                    raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/manifest.json"
                    
                    response = self._http_get_with_retries(raw_url, timeout=10)
                    if response.status_code == 200:
                        return response.json()
                    elif response.status_code == 404:
                        # Try main branch instead
                        if branch != "main":
                            raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/main/manifest.json"
                            response = self._http_get_with_retries(raw_url, timeout=10)
                            if response.status_code == 200:
                                return response.json()
        except Exception as e:
            self.logger.debug(f"Could not fetch manifest from GitHub for {repo_url}: {e}")
        
        return None
    
    def _fetch_github_releases(self, repo_url: str) -> Optional[List[Dict[str, Any]]]:
        """
        Fetch GitHub releases/tags for a repository to get latest version.
        
        Args:
            repo_url: GitHub repository URL
            
        Returns:
            List of release/tag info, or None if not found
        """
        try:
            # Extract owner/repo from URL
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
            cache_key = f"{owner}/{repo}/releases"
            
            # Check cache first
            if cache_key in self.github_releases_cache:
                cached_time, cached_data = self.github_releases_cache[cache_key]
                if time.time() - cached_time < self.cache_timeout:
                    return cached_data
            
            # Try GitHub Releases API first (more reliable)
            api_url = f"https://api.github.com/repos/{owner}/{repo}/releases"
            headers = {
                'Accept': 'application/vnd.github.v3+json',
                'User-Agent': 'LEDMatrix-Plugin-Manager/1.0'
            }
            
            if self.github_token:
                headers['Authorization'] = f'token {self.github_token}'
            
            response = requests.get(api_url, headers=headers, timeout=10)
            if response.status_code == 200:
                releases = response.json()
                # Filter to only get published releases (not drafts/prereleases)
                published_releases = [
                    {
                        'tag_name': r.get('tag_name', ''),
                        'version': r.get('tag_name', '').lstrip('v'),  # Remove 'v' prefix if present
                        'published_at': r.get('published_at', ''),
                        'name': r.get('name', ''),
                        'prerelease': r.get('prerelease', False),
                        'draft': r.get('draft', False)
                    }
                    for r in releases if not r.get('draft', False) and not r.get('prerelease', False)
                ]
                
                # Cache the result
                self.github_releases_cache[cache_key] = (time.time(), published_releases)
                return published_releases
            elif response.status_code == 404:
                # No releases found, try tags API instead
                api_url = f"https://api.github.com/repos/{owner}/{repo}/tags"
                response = requests.get(api_url, headers=headers, timeout=10)
                if response.status_code == 200:
                    tags = response.json()
                    tag_list = [
                        {
                            'tag_name': t.get('name', ''),
                            'version': t.get('name', '').lstrip('v'),
                            'published_at': '',  # Tags don't have publish date
                            'name': t.get('name', ''),
                            'prerelease': False,
                            'draft': False
                        }
                        for t in tags[:10]  # Limit to first 10 tags
                    ]
                    self.github_releases_cache[cache_key] = (time.time(), tag_list)
                    return tag_list
            elif response.status_code == 403:
                if not self.github_token:
                    self.logger.debug(f"GitHub API rate limit for releases (403). Consider adding a token.")
            else:
                self.logger.debug(f"GitHub API request failed: {response.status_code} for {api_url}")
                
        except Exception as e:
            self.logger.debug(f"Error fetching GitHub releases for {repo_url}: {e}")
        
        return None
    
    def get_plugin_info(self, plugin_id: str, fetch_latest_from_github: bool = False) -> Optional[Dict]:
        """
        Get detailed information about a plugin from the registry.
        
        Args:
            plugin_id: Plugin identifier
            fetch_latest_from_github: If True, fetch latest manifest from GitHub to get current version
            
        Returns:
            Plugin metadata or None if not found
        """
        registry = self.fetch_registry()
        plugins = registry.get('plugins', []) or []
        plugin_info = next((p for p in plugins if p['id'] == plugin_id), None)
        
        if not plugin_info:
            return None
        
        # Optionally fetch latest manifest from GitHub to get the most current version
        if fetch_latest_from_github:
            repo_url = plugin_info.get('repo')
            branch = plugin_info.get('branch', 'master')
            
            if repo_url:
                plugin_info = plugin_info.copy()
                
                # First, try to get latest version from GitHub releases/tags (most accurate)
                github_releases = self._fetch_github_releases(repo_url)
                if github_releases and len(github_releases) > 0:
                    # Get the latest release (first in list, as they're sorted by date)
                    latest_release = github_releases[0]
                    latest_version = latest_release.get('version', '')
                    
                    if latest_version:
                        # Update latest_version field
                        plugin_info['latest_version'] = latest_version
                        
                        # Add to versions array if not already present
                        if 'versions' not in plugin_info or not isinstance(plugin_info['versions'], list):
                            plugin_info['versions'] = []
                        
                        # Check if this version is already in the array
                        existing_versions = [v.get('version', '') if isinstance(v, dict) else str(v) 
                                            for v in plugin_info['versions']]
                        
                        if latest_version not in existing_versions:
                            # Add latest version to the front of versions array
                            plugin_info['versions'].insert(0, {
                                'version': latest_version,
                                'ledmatrix_min': plugin_info.get('versions', [{}])[0].get('ledmatrix_min', '2.0.0') if plugin_info.get('versions') else '2.0.0',
                                'released': latest_release.get('published_at', '').split('T')[0] if latest_release.get('published_at') else '',
                                'download_url': f"https://github.com/{repo_url.split('/')[-2]}/{repo_url.split('/')[-1]}/archive/refs/tags/v{latest_version}.zip"
                            })
                
                # Also fetch manifest from GitHub for additional metadata
                github_manifest = self._fetch_manifest_from_github(repo_url, branch)
                if github_manifest:
                    # Merge GitHub manifest data (which has the latest version)
                    # into the registry data (which has metadata like verified, stars, etc.)
                    
                    # Update version from GitHub manifest (if releases didn't provide it)
                    if 'version' in github_manifest and 'latest_version' not in plugin_info:
                        plugin_info['version'] = github_manifest['version']
                        plugin_info['latest_version'] = github_manifest['version']
                    
                    # Update versions array if available and releases didn't update it
                    if 'versions' in github_manifest and 'latest_version' not in plugin_info:
                        plugin_info['versions'] = github_manifest['versions']
                    
                    # Update latest_version from versions array if not set
                    if 'latest_version' not in plugin_info:
                        if 'versions' in plugin_info and isinstance(plugin_info['versions'], list) and len(plugin_info['versions']) > 0:
                            latest_ver = plugin_info['versions'][0]
                            if isinstance(latest_ver, dict) and 'version' in latest_ver:
                                plugin_info['latest_version'] = latest_ver['version']
                        elif 'version' in plugin_info:
                            plugin_info['latest_version'] = plugin_info['version']
                    
                    # Update other fields that might be more current
                    if 'last_updated' in github_manifest:
                        plugin_info['last_updated'] = github_manifest['last_updated']
                    if 'description' in github_manifest:
                        plugin_info['description'] = github_manifest['description']
        
        return plugin_info
    
    def install_plugin(self, plugin_id: str, version: str = "latest") -> bool:
        """
        Install a plugin from the official registry.
        
        Args:
            plugin_id: Plugin identifier
            version: Version to install (default: latest)
            
        Returns:
            True if installed successfully
        """
        self.logger.info(f"Installing plugin: {plugin_id} (version: {version})")
        
        # Get plugin info from registry
        plugin_info = self.get_plugin_info(plugin_id)
        
        if not plugin_info:
            self.logger.error(f"Plugin not found in registry: {plugin_id}")
            return False
        
        try:
            # Get version info
            versions = plugin_info.get('versions', [])
            if not versions:
                self.logger.error(f"No versions available for plugin: {plugin_id}")
                return False
                
            if version == "latest":
                # Check for explicit latest_version field, otherwise use first in list
                latest_ver = plugin_info.get('latest_version')
                if latest_ver:
                    version_info = next((v for v in versions if v['version'] == latest_ver), None)
                    if not version_info:
                        self.logger.warning(f"latest_version {latest_ver} not found, using first version")
                        version_info = versions[0]
                else:
                    version_info = versions[0]  # First is latest
            else:
                version_info = next((v for v in versions if v['version'] == version), None)
                if not version_info:
                    self.logger.error(f"Version not found: {version}")
                    return False
            
            # Get repo URL and plugin path (for monorepo support)
            repo_url = plugin_info['repo']
            plugin_subpath = plugin_info.get('plugin_path')  # e.g., "plugins/hello-world"
            
            # Check if plugin already exists
            plugin_path = self.plugins_dir / plugin_id
            if plugin_path.exists():
                self.logger.warning(f"Plugin directory already exists: {plugin_id}. Removing old version.")
                shutil.rmtree(plugin_path)
            
            # For monorepo plugins, we need to download and extract from subdirectory
            if plugin_subpath:
                self.logger.info(f"Installing from monorepo subdirectory: {plugin_subpath}")
                download_url = version_info.get('download_url')
                if not download_url:
                    # Check for download_url_template at plugin level
                    download_template = plugin_info.get('download_url_template')
                    if download_template:
                        # Use template with version substitution
                        download_url = download_template.format(version=version_info['version'])
                    else:
                        # Construct GitHub download URL
                        download_url = f"{repo_url}/archive/refs/heads/{plugin_info.get('branch', 'main')}.zip"
                
                if not self._install_from_monorepo(download_url, plugin_subpath, plugin_path):
                    self.logger.error(f"Failed to install plugin from monorepo: {plugin_id}")
                    return False
            else:
                # Standard installation (plugin at repo root)
                # Try to install via git clone first (preferred method)
                # Use branch from registry if available, otherwise try version tag
                branch = plugin_info.get('branch')
                if self._install_via_git(repo_url, version=version_info.get('version'), target_path=plugin_path, branch=branch):
                    self.logger.info(f"Installed {plugin_id} via git clone")
                else:
                    # Fall back to download zip
                    self.logger.info("Git not available or failed, trying download...")
                    download_url = version_info.get('download_url')
                    if not download_url:
                        # Check for download_url_template at plugin level
                        download_template = plugin_info.get('download_url_template')
                        if download_template:
                            # Use template with version substitution
                            download_url = download_template.format(version=version_info['version'])
                        else:
                            # Construct GitHub download URL if not provided
                            download_url = f"{repo_url}/archive/refs/tags/v{version_info['version']}.zip"
                    
                    # Try downloading the version-specific URL
                    download_success = self._install_via_download(download_url, plugin_path)
                    
                    # If version-specific download fails, try branch-based download as fallback
                    if not download_success:
                        self.logger.warning(f"Version-specific download failed for {plugin_id}, trying branch-based download...")
                        branch = plugin_info.get('branch', 'main')
                        branch_download_url = f"{repo_url}/archive/refs/heads/{branch}.zip"
                        download_success = self._install_via_download(branch_download_url, plugin_path)
                        
                        if not download_success:
                            # Try master branch as last resort
                            if branch != 'master':
                                self.logger.warning(f"Branch {branch} download failed, trying master branch...")
                                download_success = self._install_via_download(
                                    f"{repo_url}/archive/refs/heads/master.zip", 
                                    plugin_path
                                )
                    
                    if not download_success:
                        self.logger.error(f"Failed to download plugin: {plugin_id} (tried version tag and branch downloads)")
                        return False
            
            # Validate manifest exists
            manifest_path = plugin_path / "manifest.json"
            if not manifest_path.exists():
                self.logger.error(f"No manifest.json found in plugin: {plugin_id}")
                self.logger.error(f"Expected at: {manifest_path}")
                shutil.rmtree(plugin_path)
                return False
            
            # Validate manifest required fields
            try:
                with open(manifest_path, 'r', encoding='utf-8') as mf:
                    manifest = json.load(mf)
                required_fields = ['id', 'name', 'version', 'class_name']
                missing = [f for f in required_fields if f not in manifest]
                
                manifest_modified = False
                
                # Try to auto-detect class_name from manager.py if missing
                if 'class_name' in missing:
                    entry_point = manifest.get('entry_point', 'manager.py')
                    manager_file = plugin_path / entry_point
                    if manager_file.exists():
                        try:
                            detected_class = self._detect_class_name(manager_file)
                            if detected_class:
                                manifest['class_name'] = detected_class
                                self.logger.info(f"Auto-detected class_name '{detected_class}' from {entry_point}")
                                missing.remove('class_name')
                                manifest_modified = True
                        except Exception as e:
                            self.logger.warning(f"Could not auto-detect class_name: {e}")
                
                if missing:
                    self.logger.error(f"Plugin manifest missing required fields for {plugin_id}: {', '.join(missing)}")
                    shutil.rmtree(plugin_path)
                    return False
                
                # entry_point is optional, default to "manager.py" if not specified
                if 'entry_point' not in manifest:
                    manifest['entry_point'] = 'manager.py'
                    manifest_modified = True
                    self.logger.info(f"Added missing entry_point field to {plugin_id} manifest (defaulted to manager.py)")
                
                # Write manifest back if we modified it
                if manifest_modified:
                    with open(manifest_path, 'w', encoding='utf-8') as mf:
                        json.dump(manifest, mf, indent=2)
            except Exception as me:
                self.logger.error(f"Failed to read/validate manifest for {plugin_id}: {me}")
                shutil.rmtree(plugin_path)
                return False
            
            # Install Python dependencies
            if not self._install_dependencies(plugin_path):
                self.logger.warning(f"Some dependencies may not have installed correctly for {plugin_id}")

            self.logger.info(f"Successfully installed plugin: {plugin_id} v{version_info['version']}")
            return True
            
        except Exception as e:
            self.logger.error(f"Error installing plugin {plugin_id}: {e}", exc_info=True)
            # Cleanup on failure
            plugin_path = self.plugins_dir / plugin_id
            if plugin_path.exists():
                shutil.rmtree(plugin_path)
            return False
    
    def install_from_url(self, repo_url: str, plugin_id: str = None, plugin_path: str = None) -> Dict[str, Any]:
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
            
        Returns:
            Dict with status and plugin_id or error message
        """
        self.logger.info(f"Installing plugin from custom URL: {repo_url}" + (f" (subpath: {plugin_path})" if plugin_path else ""))
        
        # Clean up URL (remove .git suffix if present)
        repo_url = repo_url.rstrip('/').replace('.git', '')
        
        temp_dir = None
        try:
            # Create temporary directory
            temp_dir = Path(tempfile.mkdtemp(prefix='ledmatrix_plugin_'))
            
            # For monorepo installations, download and extract subdirectory
            if plugin_path:
                # Try downloading as zip (main branch)
                download_url = f"{repo_url}/archive/refs/heads/main.zip"
                if not self._install_from_monorepo(download_url, plugin_path, temp_dir):
                    # Try master branch
                    download_url = f"{repo_url}/archive/refs/heads/master.zip"
                    if not self._install_from_monorepo(download_url, plugin_path, temp_dir):
                        return {
                            'success': False,
                            'error': f'Failed to download or extract plugin from monorepo subdirectory: {plugin_path}'
                        }
            else:
                # Try git clone for direct plugin repos
                if self._install_via_git(repo_url, branch='main', target_path=temp_dir):
                    self.logger.info("Cloned via git")
                elif self._install_via_git(repo_url, branch='master', target_path=temp_dir):
                    self.logger.info("Cloned via git (master branch)")
                else:
                    # Try downloading as zip (main branch)
                    download_url = f"{repo_url}/archive/refs/heads/main.zip"
                    if not self._install_via_download(download_url, temp_dir):
                        # Try master branch
                        download_url = f"{repo_url}/archive/refs/heads/master.zip"
                        if not self._install_via_download(download_url, temp_dir):
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
            required_fields = ['id', 'name', 'version', 'class_name']
            missing_fields = [field for field in required_fields if field not in manifest]
            if missing_fields:
                return {
                    'success': False,
                    'error': f'Manifest missing required fields: {", ".join(missing_fields)}'
                }
            
            # entry_point is optional, default to "manager.py" if not specified
            if 'entry_point' not in manifest:
                manifest['entry_point'] = 'manager.py'
                # Write updated manifest back to file
                with open(manifest_path, 'w') as f:
                    json.dump(manifest, f, indent=2)
                self.logger.info(f"Added missing entry_point field to {plugin_id} manifest (defaulted to manager.py)")
            
            # Move to plugins directory
            final_path = self.plugins_dir / plugin_id
            if final_path.exists():
                self.logger.warning(f"Plugin {plugin_id} already exists, removing old version")
                shutil.rmtree(final_path)
            
            shutil.move(str(temp_dir), str(final_path))
            temp_dir = None  # Prevent cleanup since we moved it
            
            # Install dependencies
            self._install_dependencies(final_path)
            
            self.logger.info(f"Successfully installed plugin from URL: {plugin_id}")
            return {
                'success': True,
                'plugin_id': plugin_id,
                'name': manifest.get('name'),
                'version': manifest.get('version')
            }
            
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
    
    def _install_via_git(self, repo_url: str, version: str = None, target_path: Path = None, branch: str = None) -> bool:
        """
        Install plugin by cloning git repository.
        
        Args:
            repo_url: Repository URL
            version: Version tag to checkout (optional)
            target_path: Target directory
            branch: Branch to clone (optional, used instead of version)
            
        Returns:
            True if successful
        """
        branches_to_try = []
        if version and not branch:
            # Try version tag first
            branches_to_try.append(f"v{version}")
            branches_to_try.append(version)  # Try without v prefix
        elif branch:
            branches_to_try.append(branch)
        else:
            # Try common branch names if none specified
            branches_to_try = ['main', 'master']
        
        last_error = None
        for try_branch in branches_to_try:
            try:
                cmd = ['git', 'clone', '--depth', '1', '--branch', try_branch, repo_url, str(target_path)]
                
                result = subprocess.run(
                    cmd,
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=60
                )
                
                # Keep .git directory for update functionality
                # This allows plugins to be updated via 'git pull'
                self.logger.debug(f"Successfully cloned {repo_url} (branch: {try_branch}) to {target_path}")
                
                return True
                
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as e:
                last_error = e
                self.logger.debug(f"Git clone failed for branch {try_branch}: {e}")
                # Try next branch
                if target_path.exists():
                    shutil.rmtree(target_path)
                continue
        
        # If all branches failed and we had a specific branch, try without branch specification (default branch)
        if (version or branch) and not any(b in branches_to_try for b in ['main', 'master']):
            try:
                cmd = ['git', 'clone', '--depth', '1', repo_url, str(target_path)]
                result = subprocess.run(
                    cmd,
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=60
                )
                self.logger.debug(f"Successfully cloned {repo_url} (default branch) to {target_path}")
                return True
            except Exception as e:
                self.logger.debug(f"Git clone failed for default branch: {e}")
        
        self.logger.error(f"Git clone failed for all attempted branches: {last_error}")
        return False
    
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
                    target_path.parent.mkdir(parents=True, exist_ok=True)
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
                        target_path.parent.mkdir(parents=True, exist_ok=True)
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
    
    def uninstall_plugin(self, plugin_id: str) -> bool:
        """
        Uninstall a plugin by removing its directory.
        
        Args:
            plugin_id: Plugin identifier
            
        Returns:
            True if uninstalled successfully (or already not installed)
        """
        plugin_path = self.plugins_dir / plugin_id
        
        if not plugin_path.exists():
            # Plugin already not installed - check if it might be in a different directory
            # (e.g., if plugin_id in manifest doesn't match directory name)
            found = False
            if self.plugins_dir.exists():
                for item in self.plugins_dir.iterdir():
                    if item.is_dir() and (item / "manifest.json").exists():
                        try:
                            with open(item / "manifest.json", 'r', encoding='utf-8') as f:
                                manifest = json.load(f)
                                if manifest.get('id') == plugin_id:
                                    # Found plugin with matching ID but different directory name
                                    plugin_path = item
                                    found = True
                                    self.logger.info(f"Found plugin {plugin_id} in directory {item.name}")
                                    break
                        except Exception:
                            continue
            
            if not found:
                self.logger.info(f"Plugin {plugin_id} not found (already uninstalled)")
                return True  # Already uninstalled, consider this success
        
        try:
            self.logger.info(f"Uninstalling plugin: {plugin_id}")
            shutil.rmtree(plugin_path)
            self.logger.info(f"Successfully uninstalled plugin: {plugin_id}")
            return True
        except Exception as e:
            self.logger.error(f"Error uninstalling plugin {plugin_id}: {e}")
            return False
    
    def update_plugin(self, plugin_id: str) -> bool:
        """
        Update a plugin to the latest version.
        
        Args:
            plugin_id: Plugin identifier
            
        Returns:
            True if updated successfully
        """
        plugin_path = self.plugins_dir / plugin_id
        
        if not plugin_path.exists():
            self.logger.error(f"Plugin not installed: {plugin_id}")
            return False
        
        try:
            # Force refresh the registry cache to get latest version info
            self.logger.info(f"Checking for updates to plugin {plugin_id}")
            self.fetch_registry(force_refresh=True)
            
            # Get current installed version from manifest
            current_version = None
            manifest_path = plugin_path / "manifest.json"
            if manifest_path.exists():
                try:
                    with open(manifest_path, 'r', encoding='utf-8') as f:
                        manifest = json.load(f)
                        current_version = manifest.get('version')
                except Exception as e:
                    self.logger.warning(f"Could not read manifest for {plugin_id}: {e}")
            
            # Get latest version from registry
            plugin_info = self.get_plugin_info(plugin_id, fetch_latest_from_github=True)
            if plugin_info:
                latest_version = plugin_info.get('latest_version') or plugin_info.get('version')
                if current_version and latest_version:
                    if current_version == latest_version:
                        self.logger.info(f"Plugin {plugin_id} is already at latest version {latest_version}")
                        return True
                    self.logger.info(f"Update available: {plugin_id} {current_version} → {latest_version}")
            
            # Check if this is a git repository
            git_dir = plugin_path / ".git"
            if git_dir.exists():
                # Check if we're on a branch or detached HEAD (tag)
                result = subprocess.run(
                    ['git', '-C', str(plugin_path), 'symbolic-ref', '-q', 'HEAD'],
                    capture_output=True,
                    text=True,
                    timeout=10
                )
                
                if result.returncode == 0:
                    # On a branch, try git pull
                    self.logger.info(f"Updating {plugin_id} via git pull...")
                    pull_result = subprocess.run(
                        ['git', '-C', str(plugin_path), 'pull'],
                        capture_output=True,
                        text=True,
                        timeout=60
                    )
                    
                    if pull_result.returncode == 0:
                        pull_output = pull_result.stdout.strip()
                        if "Already up to date" in pull_output:
                            # Check if there's actually a newer version we should get
                            if plugin_info and latest_version and current_version:
                                if latest_version != current_version:
                                    # Version mismatch - try fetching and pulling
                                    self.logger.info(f"Git says up to date but version mismatch detected, fetching latest...")
                                    fetch_result = subprocess.run(
                                        ['git', '-C', str(plugin_path), 'fetch', 'origin'],
                                        capture_output=True,
                                        text=True,
                                        timeout=30
                                    )
                                    if fetch_result.returncode == 0:
                                        pull_result = subprocess.run(
                                            ['git', '-C', str(plugin_path), 'pull', 'origin', 'master'],
                                            capture_output=True,
                                            text=True,
                                            timeout=60
                                        )
                                        if pull_result.returncode != 0:
                                            self.logger.warning(f"Git pull after fetch failed, trying branch 'main'...")
                                            pull_result = subprocess.run(
                                                ['git', '-C', str(plugin_path), 'pull', 'origin', 'main'],
                                                capture_output=True,
                                                text=True,
                                                timeout=60
                                            )
                        
                        if pull_result.returncode == 0:
                            # Check if anything actually changed
                            if "Already up to date" not in pull_result.stdout:
                                self.logger.info(f"Updated plugin {plugin_id} via git pull")
                            else:
                                self.logger.info(f"Plugin {plugin_id} is already up to date")
                            
                            # Reinstall dependencies in case they changed
                            try:
                                self._install_dependencies(plugin_path)
                            except Exception as deps_error:
                                self.logger.warning(f"Warning: Could not reinstall dependencies: {deps_error}")
                            
                            return True
                        else:
                            self.logger.warning(f"Git pull failed for {plugin_id}: {pull_result.stderr}")
                            # Fall through to registry reinstall
                    else:
                        self.logger.warning(f"Git pull failed for {plugin_id}: {pull_result.stderr}")
                        # Fall through to registry reinstall
                else:
                    # Detached HEAD (installed from tag), need to fetch and checkout latest tag
                    self.logger.info(f"Plugin {plugin_id} is on a tag (detached HEAD), fetching latest version from registry")
                    # For tagged installations, always reinstall from registry to get the latest version
                    return self.install_plugin(plugin_id, version="latest")
            
            # Not a git repo, or git pull failed - try registry reinstall
            if not plugin_info:
                self.logger.warning(f"Plugin {plugin_id} not found in registry and is not a git repository. Cannot update automatically.")
                self.logger.warning(f"To update this plugin, manually update it via git or reinstall from registry.")
                return False
            
            # Plugin is in registry, try to reinstall from registry
            self.logger.info(f"Re-downloading plugin {plugin_id} from registry")
            return self.install_plugin(plugin_id, version="latest")
            
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
