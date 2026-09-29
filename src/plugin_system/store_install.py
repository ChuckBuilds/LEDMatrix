"""Plugin store: installing plugins (registry, URL, git, monorepo ZIP or
Trees API) and their Python dependencies.

Part of PluginStoreManager (store_manager.py), which mixes this class in;
methods reach shared state and helpers through ``self``.
"""

import errno
import os
import re
import json
import subprocess  # nosec B404 - list-form argv only, no shell  # nosemgrep
import shutil
import zipfile
import tempfile
from pathlib import Path
from typing import List, Dict, Optional, Any
from src.common.permission_utils import (
    ensure_directory_permissions, get_plugin_dir_mode, install_requirements_file,
)
from src.plugin_system.plugin_loader import (
    contained_plugin_dir, requirements_to_install,
)
from src.plugin_system.plugin_dirs import BACKUP_MARKER
from src.plugin_system.repo_urls import (
    USER_AGENT, github_api_headers, github_owner_repo, normalize_repo_url,
)


class _InstallMixin:
    """PluginStoreManager methods: see the module docstring."""

    def install_plugin(self, plugin_id: str, branch: Optional[str] = None) -> bool:
        """Install a plugin, keeping any existing install until the new one is
        known good.

        `_install_plugin_impl` deletes the existing directory *before*
        downloading, so every failure after that point — a dropped connection, a
        malformed manifest, or the compatibility gate refusing the new version —
        left the user with no plugin at all. `_reinstall_with_rollback` gives the
        *update* path exactly this protection; a direct install had none, and the
        compatibility gate added a new way to reach it.

        Pass-through when nothing is installed, and when called from
        `_reinstall_with_rollback`, which has already moved the old copy aside.

        The aside name embeds BACKUP_MARKER ('.standalone-backup-') so every
        plugin directory lookup (src/plugin_system/plugin_dirs.py) skips it
        even though it still holds a manifest.json.

        Held under the per-plugin reinstall lock for the same reason
        `_reinstall_with_rollback` is: the web UI runs Flask with
        threaded=True, so a double-clicked Install button gives two threads the
        same plugin_id. Interleaved, one thread's restore would delete the
        other's freshly installed copy. The lock is reentrant because the
        rollback path already holds it when it calls in here.
        """
        # Before anything touches the filesystem: plugin_id comes from the
        # request body, and the set-aside below moves plugins_dir / plugin_id
        # -- which for "../x" is a directory outside the plugins directory.
        if not self._is_valid_plugin_id(plugin_id):
            self.logger.error(f"Refusing to install invalid plugin id: {plugin_id!r}")
            return False

        with self._get_reinstall_lock(plugin_id):
            # The copy to protect is wherever this plugin is installed, not
            # necessarily plugins_dir/<id>: asked for the registry id
            # `weather`, the install lives in `ledmatrix-weather/`, the
            # manifest's id. Backing up only `weather/` protected nothing,
            # and _install_plugin_impl then deleted `ledmatrix-weather/` to
            # make room for the download -- so a refusal after that point
            # (the post-download compatibility gate) left no plugin at all.
            plugin_path = self._existing_install(plugin_id) or self.plugins_dir / plugin_id
            if not plugin_path.exists():
                return self._install_plugin_impl(plugin_id, branch)

            backup_path = plugin_path.with_name(
                f"{plugin_path.name}{BACKUP_MARKER}preinstall")
            problem = self._set_aside(plugin_path, backup_path)
            if problem:
                # Can't stage a safety net. Attempting the install anyway is
                # what callers got before the net existed; refusing would be
                # a new failure mode for a direct install.
                self.logger.warning(
                    "Installing %s without a rollback net: %s", plugin_id, problem)
                return self._install_plugin_impl(plugin_id, branch)

            try:
                installed = self._install_plugin_impl(plugin_id, branch)
            except Exception:
                self._restore_backup(plugin_id, plugin_path, backup_path, "Install")
                raise

            if installed:
                self._discard_backup(plugin_id, backup_path, "install")
                return True

            self._restore_backup(plugin_id, plugin_path, backup_path, "Install")
            return False

    def _existing_install(self, plugin_id: str) -> Optional[Path]:
        """The installed copy of ``plugin_id`` in plugins_dir, by the id or an
        alias the registry proves (`_installed_id_candidates`).

        When nothing matches but a ``ledmatrix-<id>`` folder exists and no
        registry is loaded yet, the registry is fetched first -- the install
        fetches it anyway -- because without it that folder can be neither
        protected nor trusted: the download may be renamed onto it.
        """
        dirs = [self.plugins_dir]
        found = self._resolve_installed(plugin_id, dirs)
        if (found is None and not getattr(self, 'registry_cache', None)
                and self._unproven_prefix_folder(plugin_id, dirs) is not None):
            try:
                self.fetch_registry()
            except Exception as e:  # noqa: BLE001 - proceed as before without proof
                self.logger.debug("Registry fetch before installing %s failed: %s", plugin_id, e)
            found = self._resolve_installed(plugin_id, dirs)
        return found

    def _set_aside(self, plugin_path: Path, backup_path: Path) -> Optional[str]:
        """Rename an installed plugin to ``backup_path`` so a failed
        (re)install can put it back.

        A stale backup left by a crash is cleared first, since it would block
        the rename. Returns None on success, otherwise why it could not.
        """
        if backup_path.exists() and not self._safe_remove_directory(backup_path):
            return f"could not clear stale backup at {backup_path}"
        try:
            plugin_path.rename(backup_path)
        except OSError as e:
            return f"could not set aside {plugin_path}: {e}"
        return None

    def _discard_backup(self, plugin_id: str, backup_path: Path, action: str) -> None:
        """Remove the set-aside copy after a successful (re)install."""
        if not self._safe_remove_directory(backup_path):
            self.logger.warning(
                "%s of %s succeeded but the previous copy at %s could not be "
                "removed; it will be cleared on the next %s",
                action.capitalize(), plugin_id, backup_path, action)

    def _restore_backup(
        self, plugin_id: str, plugin_path: Path, backup_path: Path, action: str
    ) -> None:
        """Put the set-aside copy back after a failed (re)install."""
        self.logger.error(
            "%s of %s failed; restoring the previous version", action, plugin_id)
        try:
            if plugin_path.exists():
                # Partial download debris from the failed install.
                self._safe_remove_directory(plugin_path)
            backup_path.rename(plugin_path)
            self.logger.info("Restored previous install of %s", plugin_id)
        except OSError as e:
            self.logger.error(
                "CRITICAL: could not restore %s from %s: %s. The previous "
                "install is preserved there — rename it back manually.",
                plugin_id, backup_path, e)

    def _install_plugin_impl(self, plugin_id: str, branch: Optional[str] = None) -> bool:
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

        # Remember the originally-requested id so we can clear its uninstall
        # record on success even if the manifest renames the directory below.
        requested_id = plugin_id

        plugin_info = self.get_plugin_info(plugin_id, fetch_latest_from_github=True, force_refresh=True)
        if not plugin_info:
            self.logger.error(f"Plugin not found in registry: {plugin_id}")
            return False
        if not self.is_plugin_entry(plugin_info):
            self.logger.error(f"Not installing {plugin_id}: registry entry type "
                              f"{plugin_info.get('type')!r} is not a plugin")
            return False

        repo_url = plugin_info.get('repo')
        if not repo_url:
            self.logger.error(f"Plugin {plugin_id} missing repository URL")
            return False

        # The registry's floor describes the release on the entry's branch.
        # Checked here, before anything is removed or downloaded; the gate on
        # the downloaded manifest below stays as the fallback (older
        # registries, compatible_versions ranges). A different branch asked
        # for by name is a different release, so only the fallback applies.
        registry_branch = plugin_info.get('branch') or plugin_info.get('default_branch')
        if (not branch or not registry_branch or branch == registry_branch) and \
                self._refuse_if_registry_incompatible(plugin_id, plugin_info, "install"):
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
                if branch_used is None:
                    self.logger.info("Git not available or clone failed, attempting archive download...")
                    for candidate in branch_candidates:
                        download_url = f"{repo_url}/archive/refs/heads/{candidate}.zip"
                        if self._install_via_download(download_url, plugin_path):
                            branch_used = candidate
                            break

                if branch_used is None:
                    self.logger.error(f"Failed to install plugin {plugin_id} via git or archive download")
                    return False

            manifest_path = plugin_path / "manifest.json"
            if not manifest_path.exists():
                self.logger.error(f"No manifest.json found in plugin: {plugin_id}")
                self._safe_remove_directory(plugin_path)
                return False

            try:
                with open(manifest_path, 'r', encoding='utf-8') as mf:
                    manifest = json.load(mf)

                # Get the actual plugin ID from manifest (source of truth)
                manifest_plugin_id = manifest.get('id')
                if not manifest_plugin_id:
                    self.logger.error("Plugin manifest missing 'id' field")
                    self._safe_remove_directory(plugin_path)
                    return False
                # The manifest id becomes a directory name below (and the old
                # directory is removed to make room), so a downloaded manifest
                # saying "../x" must not steer that outside plugins_dir.
                if not self._is_valid_plugin_id(manifest_plugin_id):
                    self.logger.error(f"Plugin manifest has an invalid 'id': {manifest_plugin_id!r}")
                    self._safe_remove_directory(plugin_path)
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
                        if not self._safe_remove_directory(correct_path):
                            self.logger.error(f"Failed to remove existing directory {correct_path}, cannot rename plugin")
                            return False
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
                    self._safe_remove_directory(plugin_path)
                    return False

                # Refuse a plugin that needs a newer core than this one. The
                # registry's `ledmatrix_min_version` already refused the
                # common case before the download (above); this is the
                # fallback for a registry without it, a branch other than the
                # registry's, and `compatible_versions`, which only the
                # manifest carries. Before dependency installation, still.
                #
                # Refusing costs the user nothing: on an update this returns
                # False and _reinstall_with_rollback restores the version they
                # already had. Allowing it costs them a plugin that raises
                # ModuleNotFoundError at load and is reported only as one line
                # in the journal. See docs/SPORTS_UNIFICATION.md (phase B4/B6).
                from src.plugin_system import compatibility
                # On disk, not as imported: this process may predate the core
                # update that made the plugin compatible. See
                # compatibility.current_core_version.
                core_version = compatibility.current_core_version()

                compatible, reason = compatibility.check(manifest, core_version)
                if not compatible:
                    self.logger.error(
                        "Refusing to install %s: %s", plugin_id, reason)
                    self._note_refusal(requested_id, reason)
                    self._safe_remove_directory(plugin_path)
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
                self._safe_remove_directory(plugin_path)
                return False

            if not self._install_dependencies(plugin_path):
                self.logger.warning(f"Some dependencies may not have installed correctly for {plugin_id}")

            branch_display = branch_used or plugin_info.get('branch') or plugin_info.get('default_branch', 'unknown')
            self.logger.info(f"Successfully installed plugin: {plugin_id} (branch {branch_display})")
            # User deliberately (re)installed this plugin — clear any persistent
            # uninstall record so future core updates keep it.
            self.forget_uninstalled_plugin(requested_id, plugin_id)
            return True

        except Exception as e:
            self.logger.error(f"Error installing plugin {plugin_id}: {e}", exc_info=True)
            if plugin_path.exists():
                self._safe_remove_directory(plugin_path)
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
        
        repo_url = normalize_repo_url(repo_url)
        
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
                branch_used = self._install_via_git(repo_url, temp_dir, branch_candidates)
                if branch_used is not None:
                    self.logger.info(f"Cloned via git (branch: {branch_used})")
                else:
                    self.logger.info("Git not available or clone failed, attempting archive download...")
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
            
            with open(manifest_path, 'r', encoding='utf-8') as f:
                manifest = json.load(f)
            
            requested_id = plugin_id
            plugin_id = plugin_id or manifest.get('id')
            if not plugin_id:
                return {
                    'success': False,
                    'error': 'No plugin ID found in manifest'
                }
            # plugin_id names the directory that is removed and then replaced
            # below, and it comes from the request body or a downloaded
            # manifest -- so "../x" would reach outside plugins_dir.
            if not self._is_valid_plugin_id(plugin_id):
                return {
                    'success': False,
                    'error': f'Invalid plugin ID: {plugin_id!r}'
                }
            
            # Validate manifest has required fields
            required_fields = ['id', 'name', 'class_name', 'display_modes']
            missing_fields = [field for field in required_fields if field not in manifest]
            if missing_fields:
                return {
                    'success': False,
                    'error': f'Manifest missing required fields: {", ".join(missing_fields)}'
                }
            
            # Refuse a plugin that needs a newer core than this one, exactly as
            # _install_plugin_impl does after its download. Sideloading is an
            # explicit act rather than an automatic store update, but the floor
            # is not advice about intent -- it is a statement that the plugin
            # cannot run here, and letting it through produces the same silent
            # PluginState.ERROR at load. This was the last of the three routes
            # in that skipped the check.
            #
            # Before the move, so the `finally` below removes the temp tree and
            # nothing half-installed is left behind.
            from src.plugin_system import compatibility
            core_version = compatibility.current_core_version()

            compatible, reason = compatibility.check(manifest, core_version)
            if not compatible:
                self.logger.error(
                    "Refusing to install %s from %s: %s",
                    plugin_id, repo_url, reason)
                return {'success': False, 'error': reason}

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
                with open(manifest_path, 'w', encoding='utf-8') as f:
                    json.dump(manifest, f, indent=2)
                self.logger.info(f"Added missing entry_point field to {plugin_id} manifest (defaulted to manager.py)")
            
            # The directory is named for the caller's plugin_id when one was
            # given (update_plugin passes the installed id), else for the
            # manifest's id -- so it can differ from the manifest id, which
            # discovery tolerates by reading the manifest.
            final_path = self.plugins_dir / plugin_id
            # Set the existing copy aside rather than deleting it, and put it
            # back if the move fails: deleting first left the user with no
            # plugin at all whenever the move broke part-way. Under the
            # per-plugin reinstall lock, as install_plugin() is, so two
            # overlapping installs of one id can't interleave their renames.
            with self._get_reinstall_lock(plugin_id):
                backup_path = None
                if final_path.exists():
                    self.logger.warning(f"Plugin {plugin_id} already exists, replacing existing copy")
                    backup_path = final_path.with_name(
                        f"{final_path.name}{BACKUP_MARKER}preinstall")
                    problem = self._set_aside(final_path, backup_path)
                    if problem:
                        return {
                            'success': False,
                            'error': f'Failed to replace existing plugin directory: {problem}'
                        }

                try:
                    shutil.move(str(temp_dir), str(final_path))
                except Exception:
                    if backup_path is not None:
                        self._restore_backup(plugin_id, final_path, backup_path, "Install")
                    raise
                temp_dir = None  # Prevent cleanup since we moved it
                if backup_path is not None:
                    self._discard_backup(plugin_id, backup_path, "install")

            # Install dependencies
            self._install_dependencies(final_path)
            
            branch_info = f" (branch: {branch_used})" if branch_used else ""
            self.logger.info(f"Successfully installed plugin from URL: {plugin_id}{branch_info}")
            # User deliberately (re)installed this plugin -- clear any persistent
            # uninstall record, exactly as install_plugin() does. Without this the
            # id stays in config/uninstalled_plugins.json and
            # purge_uninstalled_plugins(), which runs at every web-app startup,
            # deletes the directory again: the plugin works for the rest of the
            # session and is gone after the next reboot.
            self.forget_uninstalled_plugin(
                *(pid for pid in (requested_id, plugin_id, manifest.get('id')) if pid)
            )
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
                shutil.rmtree(temp_dir, ignore_errors=True)

    def _detect_class_name(self, manager_file: Path) -> Optional[str]:
        """
        Attempt to auto-detect the plugin class name from the manager file.
        
        Args:
            manager_file: Path to the manager.py file
            
        Returns:
            Class name if found, None otherwise
        """
        try:
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
        """Clone a repository into ``target_path``.

        Tries each of ``branches`` (default ``main``, ``master``), then the
        repository's own default branch, so a repository whose only branch
        is e.g. ``develop`` still installs.

        Returns:
            The branch that was cloned, or None when every clone failed and
            ``target_path`` has been removed. After a default-branch clone
            this is the branch the clone checked out (``'HEAD'`` if the
            remote's HEAD is detached), never None.
        """
        branches_to_try = self._distinct_sequence(branches or [])
        if not branches_to_try:
            branches_to_try = ['main', 'master']

        last_error = None
        for try_branch in branches_to_try:
            try:
                cmd = ['git', 'clone', '--depth', '1', '--branch', try_branch, repo_url, str(target_path)]
                subprocess.run(  # nosec B603 - list-form argv, no shell  # nosemgrep
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
                    self._safe_remove_directory(target_path)

        # Try default branch (Git's configured default) as last resort
        try:
            cmd = ['git', 'clone', '--depth', '1', repo_url, str(target_path)]
            subprocess.run(  # nosec B603 - list-form argv, no shell  # nosemgrep
                cmd,
                check=True,
                capture_output=True,
                text=True,
                timeout=60
            )
            self.logger.debug(f"Successfully cloned {repo_url} (git default branch) to {target_path}")
            return self._checked_out_branch(target_path)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as e:
            last_error = e
            if target_path.exists():
                self._safe_remove_directory(target_path)

        self.logger.error(f"Git clone failed for all attempted branches: {last_error}")
        return None

    @staticmethod
    def _checked_out_branch(checkout: Path) -> str:
        """The branch a fresh clone has checked out, read from ``.git/HEAD``.

        ``'HEAD'`` when HEAD is detached or unreadable.
        """
        try:
            head = (checkout / '.git' / 'HEAD').read_text(encoding='utf-8').strip()
        except OSError:
            return 'HEAD'
        prefix = 'ref: refs/heads/'
        return head[len(prefix):] if head.startswith(prefix) else 'HEAD'

    def _install_from_monorepo(self, download_url: str, plugin_subpath: str, target_path: Path) -> bool:
        """
        Install a plugin from a monorepo by downloading only the target subdirectory.

        Uses the GitHub Git Trees API to list files, then downloads each file
        individually from raw.githubusercontent.com. Falls back to downloading
        the full ZIP archive if the API approach fails.

        Args:
            download_url: URL to download zip from (used as fallback and to extract repo info)
            plugin_subpath: Path within repo (e.g., "plugins/hello-world")
            target_path: Target directory for plugin

        Returns:
            True if successful
        """
        # Try the API-based approach first (downloads only the target directory)
        repo_url, branch = self._parse_monorepo_download_url(download_url)
        if repo_url and branch:
            result = self._install_from_monorepo_api(repo_url, branch, plugin_subpath, target_path)
            if result:
                return True
            self.logger.info(f"API-based install failed for {plugin_subpath}, falling back to ZIP download")
            # Ensure no partial files remain before ZIP fallback
            if target_path.exists():
                self._safe_remove_directory(target_path)

        # Fallback: download full ZIP and extract subdirectory
        return self._install_from_monorepo_zip(download_url, plugin_subpath, target_path)

    @staticmethod
    def _parse_monorepo_download_url(download_url: str):
        """Extract repo URL and branch from a GitHub archive download URL.

        Example: "https://github.com/ChuckBuilds/ledmatrix-plugins/archive/refs/heads/main.zip"
        Returns: ("https://github.com/ChuckBuilds/ledmatrix-plugins", "main")
        """
        try:
            # Pattern: {repo_url}/archive/refs/heads/{branch}.zip
            if '/archive/refs/heads/' in download_url:
                parts = download_url.split('/archive/refs/heads/')
                repo_url = parts[0]
                branch = parts[1].removesuffix('.zip')
                return repo_url, branch
        except (IndexError, AttributeError):
            pass
        return None, None

    def _install_from_monorepo_api(self, repo_url: str, branch: str, plugin_subpath: str, target_path: Path) -> bool:
        """
        Install a plugin subdirectory using the GitHub Git Trees API.

        Downloads only the files in the target subdirectory (~200KB) instead
        of the entire repository ZIP (~5MB+). Uses one API call for the tree
        listing, then downloads individual files from raw.githubusercontent.com.

        Args:
            repo_url: GitHub repository URL (e.g., "https://github.com/owner/repo")
            branch: Branch name (e.g., "main")
            plugin_subpath: Path within repo (e.g., "plugins/hello-world")
            target_path: Target directory for plugin

        Returns:
            True if successful, False to trigger ZIP fallback
        """
        try:
            owner_repo = github_owner_repo(repo_url)
            if owner_repo is None:
                return False
            owner, repo = owner_repo

            # Step 1: Get the recursive tree listing (1 API call)
            api_url = f"https://api.github.com/repos/{owner}/{repo}/git/trees/{branch}?recursive=true"
            tree_response = self._http_get_with_retries(
                api_url, timeout=15, headers=github_api_headers(self.github_token))
            if tree_response.status_code != 200:
                self.logger.debug(f"Trees API returned {tree_response.status_code} for {owner}/{repo}")
                return False

            tree_data = tree_response.json()
            if tree_data.get('truncated'):
                self.logger.debug(f"Tree response truncated for {owner}/{repo}, falling back to ZIP")
                return False

            # Step 2: Filter for files in the target subdirectory
            prefix = f"{plugin_subpath.strip('/')}/"
            file_entries = [
                entry for entry in tree_data.get('tree', [])
                if entry['path'].startswith(prefix) and entry['type'] == 'blob'
            ]

            if not file_entries:
                self.logger.error(f"No files found under '{plugin_subpath}' in tree for {owner}/{repo}")
                return False

            # Sanity check: refuse unreasonably large plugin directories
            max_files = 500
            if len(file_entries) > max_files:
                self.logger.error(
                    f"Plugin {plugin_subpath} has {len(file_entries)} files (limit {max_files}), "
                    f"falling back to ZIP"
                )
                return False

            self.logger.info(f"Downloading {len(file_entries)} files for {plugin_subpath} via API")

            # Step 3: Create target directory and download each file
            ensure_directory_permissions(target_path.parent, get_plugin_dir_mode())
            target_path.mkdir(parents=True, exist_ok=True)

            prefix_len = len(prefix)
            target_root = target_path.resolve()
            for entry in file_entries:
                # Relative path within the plugin directory
                rel_path = entry['path'][prefix_len:]
                dest_file = target_path / rel_path

                # Guard against path traversal
                if not dest_file.resolve().is_relative_to(target_root):
                    self.logger.error(
                        f"Path traversal detected: {entry['path']!r} resolves outside target directory"
                    )
                    if target_path.exists():
                        self._safe_remove_directory(target_path)
                    return False

                # Create parent directories
                dest_file.parent.mkdir(parents=True, exist_ok=True)

                # Download from raw.githubusercontent.com (no API rate limit cost)
                raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{entry['path']}"
                file_response = self._http_get_with_retries(raw_url, timeout=30)
                if file_response.status_code != 200:
                    self.logger.error(f"Failed to download {entry['path']}: HTTP {file_response.status_code}")
                    # Clean up partial download
                    if target_path.exists():
                        self._safe_remove_directory(target_path)
                    return False

                dest_file.write_bytes(file_response.content)

            self.logger.info(f"Successfully installed {plugin_subpath} via API ({len(file_entries)} files)")
            return True

        except Exception as e:
            self.logger.debug(f"API-based monorepo install failed: {e}")
            # Clean up partial download
            if target_path.exists():
                self._safe_remove_directory(target_path)
            return False

    def _install_from_monorepo_zip(self, download_url: str, plugin_subpath: str, target_path: Path) -> bool:
        """
        Fallback: install a plugin from a monorepo by downloading the full ZIP.

        Used when the API-based approach fails (rate limited, auth issues, etc.).
        """
        tmp_zip_path = None
        temp_extract = None
        try:
            self.logger.info(f"Downloading monorepo ZIP from: {download_url}")
            response = self._http_get_with_retries(download_url, timeout=60, stream=True)
            response.raise_for_status()

            # Download to temporary file
            with tempfile.NamedTemporaryFile(suffix='.zip', delete=False) as tmp_file:
                for chunk in response.iter_content(chunk_size=8192):
                    tmp_file.write(chunk)
                tmp_zip_path = tmp_file.name

            with zipfile.ZipFile(tmp_zip_path, 'r') as zip_ref:
                zip_contents = zip_ref.namelist()
                if not zip_contents:
                    return False

                root_dir = zip_contents[0].split('/')[0]
                plugin_prefix = f"{root_dir}/{plugin_subpath}/"

                # Extract ONLY files under the plugin subdirectory
                plugin_members = [m for m in zip_contents if m.startswith(plugin_prefix)]

                if not plugin_members:
                    self.logger.error(f"Plugin path not found in archive: {plugin_subpath}")
                    return False

                temp_extract = Path(tempfile.mkdtemp())
                temp_extract_resolved = temp_extract.resolve()

                for member in plugin_members:
                    # Guard against zip-slip (directory traversal)
                    member_dest = (temp_extract / member).resolve()
                    if not member_dest.is_relative_to(temp_extract_resolved):
                        self.logger.error(
                            f"Zip-slip detected: member {member!r} resolves outside "
                            f"temp directory, aborting"
                        )
                        shutil.rmtree(temp_extract, ignore_errors=True)
                        return False
                    zip_ref.extract(member, temp_extract)

                source_plugin_dir = temp_extract / root_dir / plugin_subpath

                ensure_directory_permissions(target_path.parent, get_plugin_dir_mode())
                # Ensure target doesn't exist to prevent shutil.move nesting
                if target_path.exists():
                    if not self._safe_remove_directory(target_path):
                        self.logger.error(f"Cannot remove existing target {target_path} for monorepo install")
                        return False
                shutil.move(str(source_plugin_dir), str(target_path))

            return True

        except Exception as e:
            self.logger.error(f"Monorepo ZIP download failed: {e}", exc_info=True)
            return False
        finally:
            if tmp_zip_path and os.path.exists(tmp_zip_path):
                os.remove(tmp_zip_path)
            if temp_extract and temp_extract.exists():
                shutil.rmtree(temp_extract, ignore_errors=True)

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
            response = self._http_get_with_retries(download_url, timeout=60, stream=True, headers={'User-Agent': USER_AGENT})
            response.raise_for_status()
            
            # Download to temporary file
            with tempfile.NamedTemporaryFile(suffix='.zip', delete=False) as tmp_file:
                for chunk in response.iter_content(chunk_size=8192):
                    tmp_file.write(chunk)
                tmp_zip_path = tmp_file.name
            
            temp_extract = None
            try:
                # Extract zip
                with zipfile.ZipFile(tmp_zip_path, 'r') as zip_ref:
                    # GitHub zips have a root directory, we need to extract contents
                    zip_contents = zip_ref.namelist()
                    if not zip_contents:
                        return False
                    
                    # Find the root directory in the zip
                    root_dir = zip_contents[0].split('/')[0]
                    
                    # Extract to temp location with zip-slip protection
                    temp_extract = Path(tempfile.mkdtemp())
                    temp_extract_resolved = temp_extract.resolve()
                    for member in zip_ref.namelist():
                        member_dest = (temp_extract / member).resolve()
                        if not member_dest.is_relative_to(temp_extract_resolved):
                            self.logger.error(
                                f"Zip-slip detected: member {member!r} resolves outside "
                                f"temp directory, aborting"
                            )
                            return False
                    zip_ref.extractall(temp_extract)
                    
                    # Move contents from root_dir to target
                    source_dir = temp_extract / root_dir
                    if source_dir.exists():
                        ensure_directory_permissions(target_path.parent, get_plugin_dir_mode())
                        shutil.move(str(source_dir), str(target_path))
                    else:
                        # No root dir, move everything
                        shutil.move(str(temp_extract), str(target_path))
                
                return True
                
            finally:
                # Remove temporary zip file
                if os.path.exists(tmp_zip_path):
                    os.remove(tmp_zip_path)
                # Cleanup temp extract dir here rather than on the success
                # path, so a failed extract or move doesn't leave a copy of
                # the plugin in /tmp on every attempt. (Gone already when the
                # whole dir was moved into place.)
                if temp_extract is not None and temp_extract.exists():
                    shutil.rmtree(temp_extract, ignore_errors=True)
            
        except Exception as e:
            self.logger.error(f"Download failed: {e}")
            return False

    def _install_dependencies(self, plugin_path: Path) -> bool:
        """
        Install Python dependencies from requirements.txt.

        ``plugin_path`` is ultimately derived from a plugin-supplied manifest
        ``id``, so it is only used after contained_plugin_dir() has rebuilt it
        from a listing of ``self.plugins_dir``.

        Args:
            plugin_path: Path to plugin directory

        Returns:
            True if successful or no requirements file
        """
        safe_plugin_dir = contained_plugin_dir(plugin_path, self.plugins_dir)
        if safe_plugin_dir is None:
            self.logger.error("Plugin directory not found inside plugins dir for dependency install")
            return False

        requirements_file = requirements_to_install(safe_plugin_dir, self.logger, plugin_path.name)
        if requirements_file is None:
            return True

        try:
            self.logger.info(f"Installing dependencies for {plugin_path.name}")
            # Routed through the shared root-visible installer (same one the
            # web UI's "Reinstall Plugin Deps" tool uses) rather than a bare
            # `pip`/`pip3` off PATH: a bare pip binary can silently resolve to
            # a different Python installation than the one that actually runs
            # ledmatrix.service, so pip reports success while the package
            # stays invisible to the running plugin (e.g. missing `astral`
            # for the weather plugin even though "install" succeeded).
            result = install_requirements_file(Path(requirements_file), timeout=300)
            if result.returncode != 0:
                self.logger.error(
                    f"Error installing dependencies for {plugin_path.name}: {result.stderr}"
                )
                return False
            self.logger.info(f"Dependencies installed successfully for {plugin_path.name}")
            return True

        except subprocess.TimeoutExpired:
            self.logger.error("Dependency installation timed out")
            return False
        except OSError as e:
            # A broken pipe (EPIPE) happens when pip's output pipe closes
            # mid-download, usually a network interruption.
            if e.errno == errno.EPIPE:
                self.logger.error(
                    f"Broken pipe error during dependency installation for {plugin_path.name}. "
                    f"This usually indicates a network interruption or pip output buffer issue. "
                    f"Try installing again or check your network connection."
                )
            else:
                self.logger.error(f"OS error during dependency installation: {e}")
            return False
        except Exception as e:
            self.logger.error(f"Unexpected error installing dependencies for {plugin_path.name}: {e}", exc_info=True)
            return False
