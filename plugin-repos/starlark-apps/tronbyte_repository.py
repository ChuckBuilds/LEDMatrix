"""
Tronbyte Repository Module

Handles interaction with the Tronbyte apps repository on GitHub.
Fetches app listings, metadata, and downloads .star files.
"""

import logging
import requests
import yaml
from typing import Dict, Any, Optional, List, Tuple
from pathlib import Path

logger = logging.getLogger(__name__)


class TronbyteRepository:
    """
    Interface to the Tronbyte apps repository.

    Provides methods to:
    - List available apps
    - Fetch app metadata
    - Download .star files
    - Parse manifest.yaml files
    """

    REPO_OWNER = "tronbyt"
    REPO_NAME = "apps"
    DEFAULT_BRANCH = "main"
    APPS_PATH = "apps"

    def __init__(self, github_token: Optional[str] = None):
        """
        Initialize repository interface.

        Args:
            github_token: Optional GitHub personal access token for higher rate limits
        """
        self.github_token = github_token
        self.base_url = "https://api.github.com"
        self.raw_url = "https://raw.githubusercontent.com"

        self.session = requests.Session()
        if github_token:
            self.session.headers.update({
                'Authorization': f'token {github_token}'
            })
        self.session.headers.update({
            'Accept': 'application/vnd.github.v3+json',
            'User-Agent': 'LEDMatrix-Starlark-Plugin'
        })

    def _make_request(self, url: str, timeout: int = 10) -> Optional[Dict[str, Any]]:
        """
        Make a request to GitHub API with error handling.

        Args:
            url: API URL to request
            timeout: Request timeout in seconds

        Returns:
            JSON response or None on error
        """
        try:
            response = self.session.get(url, timeout=timeout)

            if response.status_code == 403:
                # Rate limit exceeded
                logger.warning("GitHub API rate limit exceeded")
                return None
            elif response.status_code == 404:
                logger.warning(f"Resource not found: {url}")
                return None
            elif response.status_code != 200:
                logger.error(f"GitHub API error: {response.status_code}")
                return None

            return response.json()

        except requests.Timeout:
            logger.error(f"Request timeout: {url}")
            return None
        except requests.RequestException as e:
            logger.error(f"Request error: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
            return None

    def _fetch_raw_file(self, file_path: str, branch: str = None) -> Optional[str]:
        """
        Fetch raw file content from repository.

        Args:
            file_path: Path to file in repository
            branch: Branch name (default: DEFAULT_BRANCH)

        Returns:
            File content as string, or None on error
        """
        branch = branch or self.DEFAULT_BRANCH
        url = f"{self.raw_url}/{self.REPO_OWNER}/{self.REPO_NAME}/{branch}/{file_path}"

        try:
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                return response.text
            else:
                logger.warning(f"Failed to fetch raw file: {file_path} ({response.status_code})")
                return None
        except Exception as e:
            logger.error(f"Error fetching raw file {file_path}: {e}")
            return None

    def list_apps(self) -> Tuple[bool, Optional[List[Dict[str, Any]]], Optional[str]]:
        """
        List all available apps in the repository.

        Returns:
            Tuple of (success, apps_list, error_message)
        """
        url = f"{self.base_url}/repos/{self.REPO_OWNER}/{self.REPO_NAME}/contents/{self.APPS_PATH}"

        data = self._make_request(url)
        if not data:
            return False, None, "Failed to fetch repository contents"

        if not isinstance(data, list):
            return False, None, "Invalid response format"

        # Filter directories (apps)
        apps = []
        for item in data:
            if item.get('type') == 'dir':
                app_id = item.get('name')
                if app_id and not app_id.startswith('.'):
                    apps.append({
                        'id': app_id,
                        'path': item.get('path'),
                        'url': item.get('url')
                    })

        logger.info(f"Found {len(apps)} apps in repository")
        return True, apps, None

    def get_app_metadata(self, app_id: str) -> Tuple[bool, Optional[Dict[str, Any]], Optional[str]]:
        """
        Fetch metadata for a specific app.

        Reads the manifest.yaml file for the app and parses it.

        Args:
            app_id: App identifier

        Returns:
            Tuple of (success, metadata_dict, error_message)
        """
        manifest_path = f"{self.APPS_PATH}/{app_id}/manifest.yaml"

        content = self._fetch_raw_file(manifest_path)
        if not content:
            return False, None, f"Failed to fetch manifest for {app_id}"

        try:
            metadata = yaml.safe_load(content)

            # Validate that metadata is a dict before mutating
            if not isinstance(metadata, dict):
                if metadata is None:
                    logger.warning(f"Manifest for {app_id} is empty or None, initializing empty dict")
                    metadata = {}
                else:
                    logger.error(f"Manifest for {app_id} is not a dict (got {type(metadata).__name__}), skipping")
                    return False, None, f"Invalid manifest format: expected dict, got {type(metadata).__name__}"

            # Enhance with app_id
            metadata['id'] = app_id

            # Parse schema if present
            if 'schema' in metadata:
                # Schema is already parsed from YAML
                pass

            return True, metadata, None

        except (yaml.YAMLError, TypeError) as e:
            logger.error(f"Failed to parse manifest for {app_id}: {e}")
            return False, None, f"Invalid manifest format: {e}"

    def list_apps_with_metadata(self, max_apps: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        List all apps with their metadata.

        This is slower as it fetches manifest.yaml for each app.

        Args:
            max_apps: Optional limit on number of apps to fetch

        Returns:
            List of app metadata dictionaries
        """
        success, apps, error = self.list_apps()

        if not success:
            logger.error(f"Failed to list apps: {error}")
            return []

        if max_apps is not None:
            apps = apps[:max_apps]

        apps_with_metadata = []
        for app_info in apps:
            app_id = app_info['id']
            success, metadata, error = self.get_app_metadata(app_id)

            if success and metadata:
                # Merge basic info with metadata
                metadata.update({
                    'repository_path': app_info['path']
                })
                apps_with_metadata.append(metadata)
            else:
                # Add basic info even if metadata fetch failed
                apps_with_metadata.append({
                    'id': app_id,
                    'name': app_id.replace('_', ' ').title(),
                    'summary': 'No description available',
                    'repository_path': app_info['path'],
                    'metadata_error': error
                })

        return apps_with_metadata

    def download_star_file(self, app_id: str, output_path: Path) -> Tuple[bool, Optional[str]]:
        """
        Download the .star file for an app.

        Args:
            app_id: App identifier
            output_path: Where to save the .star file

        Returns:
            Tuple of (success, error_message)
        """
        star_path = f"{self.APPS_PATH}/{app_id}/{app_id}.star"

        content = self._fetch_raw_file(star_path)
        if not content:
            return False, f"Failed to download .star file for {app_id}"

        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(content)

            logger.info(f"Downloaded {app_id}.star to {output_path}")
            return True, None

        except Exception as e:
            logger.error(f"Failed to save .star file: {e}")
            return False, f"Failed to save file: {e}"

    def get_app_files(self, app_id: str) -> Tuple[bool, Optional[List[str]], Optional[str]]:
        """
        List all files in an app directory.

        Args:
            app_id: App identifier

        Returns:
            Tuple of (success, file_list, error_message)
        """
        url = f"{self.base_url}/repos/{self.REPO_OWNER}/{self.REPO_NAME}/contents/{self.APPS_PATH}/{app_id}"

        data = self._make_request(url)
        if not data:
            return False, None, "Failed to fetch app files"

        if not isinstance(data, list):
            return False, None, "Invalid response format"

        files = [item['name'] for item in data if item.get('type') == 'file']
        return True, files, None

    def search_apps(self, query: str, apps_with_metadata: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Search apps by name, summary, or description.

        Args:
            query: Search query string
            apps_with_metadata: List of apps with metadata

        Returns:
            Filtered list of apps matching query
        """
        if not query:
            return apps_with_metadata

        query_lower = query.lower()
        results = []

        for app in apps_with_metadata:
            # Search in name, summary, description, author
            searchable = ' '.join([
                app.get('name', ''),
                app.get('summary', ''),
                app.get('desc', ''),
                app.get('author', ''),
                app.get('id', '')
            ]).lower()

            if query_lower in searchable:
                results.append(app)

        return results

    def filter_by_category(self, category: str, apps_with_metadata: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Filter apps by category.

        Args:
            category: Category name (or 'all' for no filtering)
            apps_with_metadata: List of apps with metadata

        Returns:
            Filtered list of apps
        """
        if not category or category.lower() == 'all':
            return apps_with_metadata

        category_lower = category.lower()
        results = []

        for app in apps_with_metadata:
            app_category = app.get('category', '').lower()
            if app_category == category_lower:
                results.append(app)

        return results

    def get_rate_limit_info(self) -> Dict[str, Any]:
        """
        Get current GitHub API rate limit information.

        Returns:
            Dictionary with rate limit info
        """
        url = f"{self.base_url}/rate_limit"
        data = self._make_request(url)

        if data:
            core = data.get('resources', {}).get('core', {})
            return {
                'limit': core.get('limit', 0),
                'remaining': core.get('remaining', 0),
                'reset': core.get('reset', 0),
                'used': core.get('used', 0)
            }

        return {
            'limit': 0,
            'remaining': 0,
            'reset': 0,
            'used': 0
        }
