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
        
        # Ensure plugins directory exists
        self.plugins_dir.mkdir(exist_ok=True)
        
    def fetch_registry(self, force_refresh: bool = False) -> Dict:
        """
        Fetch the plugin registry from GitHub.
        
        Args:
            force_refresh: Force refresh even if cached
            
        Returns:
            Registry data with list of available plugins
        """
        if self.registry_cache and not force_refresh:
            return self.registry_cache
        
        try:
            self.logger.info(f"Fetching plugin registry from {self.REGISTRY_URL}")
            response = requests.get(self.REGISTRY_URL, timeout=10)
            response.raise_for_status()
            self.registry_cache = response.json()
            self.logger.info(f"Fetched registry with {len(self.registry_cache.get('plugins', []))} plugins")
            return self.registry_cache
        except requests.RequestException as e:
            self.logger.error(f"Error fetching registry: {e}")
            return {"version": "1.0.0", "plugins": []}
        except json.JSONDecodeError as e:
            self.logger.error(f"Error parsing registry JSON: {e}")
            return {"version": "1.0.0", "plugins": []}
    
    def search_plugins(self, query: str = "", category: str = "", tags: List[str] = None) -> List[Dict]:
        """
        Search for plugins in the registry.
        
        Args:
            query: Search query string (searches name, description, id)
            category: Filter by category (e.g., 'sports', 'weather', 'time')
            tags: Filter by tags (matches any tag in list)
            
        Returns:
            List of matching plugin metadata
        """
        if tags is None:
            tags = []
            
        registry = self.fetch_registry()
        plugins = registry.get('plugins', [])
        
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
            
            results.append(plugin)
        
        return results
    
    def get_plugin_info(self, plugin_id: str) -> Optional[Dict]:
        """
        Get detailed information about a plugin from the registry.
        
        Args:
            plugin_id: Plugin identifier
            
        Returns:
            Plugin metadata or None if not found
        """
        registry = self.fetch_registry()
        return next((p for p in registry.get('plugins', []) if p['id'] == plugin_id), None)
    
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
                version_info = versions[0]  # First is latest
            else:
                version_info = next((v for v in versions if v['version'] == version), None)
                if not version_info:
                    self.logger.error(f"Version not found: {version}")
                    return False
            
            # Get repo URL
            repo_url = plugin_info['repo']
            
            # Check if plugin already exists
            plugin_path = self.plugins_dir / plugin_id
            if plugin_path.exists():
                self.logger.warning(f"Plugin directory already exists: {plugin_id}. Removing old version.")
                shutil.rmtree(plugin_path)
            
            # Try to install via git clone first (preferred method)
            if self._install_via_git(repo_url, version_info['version'], plugin_path):
                self.logger.info(f"Installed {plugin_id} via git clone")
            else:
                # Fall back to download zip
                self.logger.info("Git not available or failed, trying download...")
                download_url = version_info.get('download_url')
                if not download_url:
                    # Construct GitHub download URL if not provided
                    download_url = f"{repo_url}/archive/refs/tags/v{version_info['version']}.zip"
                
                if not self._install_via_download(download_url, plugin_path):
                    self.logger.error(f"Failed to download plugin: {plugin_id}")
                    return False
            
            # Validate manifest exists
            manifest_path = plugin_path / "manifest.json"
            if not manifest_path.exists():
                self.logger.error(f"No manifest.json found in plugin: {plugin_id}")
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
    
    def install_from_url(self, repo_url: str, plugin_id: str = None) -> Dict[str, Any]:
        """
        Install a plugin directly from a GitHub URL.
        This allows users to install custom/unverified plugins.
        
        Args:
            repo_url: GitHub repository URL (e.g., https://github.com/user/repo)
            plugin_id: Optional plugin ID (extracted from manifest if not provided)
            
        Returns:
            Dict with status and plugin_id or error message
        """
        self.logger.info(f"Installing plugin from custom URL: {repo_url}")
        
        # Clean up URL (remove .git suffix if present)
        repo_url = repo_url.rstrip('/').replace('.git', '')
        
        temp_dir = None
        try:
            # Create temporary directory
            temp_dir = Path(tempfile.mkdtemp(prefix='ledmatrix_plugin_'))
            
            # Try git clone
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
                    'error': 'No manifest.json found in repository'
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
            required_fields = ['id', 'name', 'version', 'entry_point', 'class_name']
            missing_fields = [field for field in required_fields if field not in manifest]
            if missing_fields:
                return {
                    'success': False,
                    'error': f'Manifest missing required fields: {", ".join(missing_fields)}'
                }
            
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
        try:
            cmd = ['git', 'clone', '--depth', '1']
            
            if version and not branch:
                # Clone specific tag
                cmd.extend(['--branch', f"v{version}"])
            elif branch:
                # Clone specific branch
                cmd.extend(['--branch', branch])
            
            cmd.extend([repo_url, str(target_path)])
            
            result = subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                text=True,
                timeout=60
            )
            
            # Remove .git directory to save space
            git_dir = target_path / '.git'
            if git_dir.exists():
                shutil.rmtree(git_dir)
            
            return True
            
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as e:
            self.logger.debug(f"Git clone failed: {e}")
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
            response = requests.get(download_url, timeout=60, stream=True)
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
            True if uninstalled successfully
        """
        plugin_path = self.plugins_dir / plugin_id
        
        if not plugin_path.exists():
            self.logger.warning(f"Plugin not found: {plugin_id}")
            return False
        
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
            # Check if this is a git repository
            git_dir = plugin_path / ".git"
            if git_dir.exists():
                # Try git pull
                result = subprocess.run(
                    ['git', '-C', str(plugin_path), 'pull'],
                    capture_output=True,
                    text=True,
                    timeout=60
                )
                if result.returncode == 0:
                    self.logger.info(f"Updated plugin {plugin_id} via git pull")
                    # Reinstall dependencies in case they changed
                    self._install_dependencies(plugin_path)
                    return True
            
            # Not a git repo or git pull failed, try registry update
            self.logger.info(f"Re-downloading plugin {plugin_id} from registry")
            return self.install_plugin(plugin_id, version="latest")
            
        except Exception as e:
            self.logger.error(f"Error updating plugin {plugin_id}: {e}")
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
