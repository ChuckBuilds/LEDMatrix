"""Plugin store: updating installed plugins, with rollback, and reading their
local git state.

Part of PluginStoreManager (store_manager.py), which mixes this class in;
methods reach shared state and helpers through ``self``.
"""

import json
import subprocess  # nosec B404 - list-form argv only, no shell  # nosemgrep
from pathlib import Path
from typing import Dict, Optional, Tuple
from src.plugin_system.plugin_dirs import BACKUP_MARKER
from src.plugin_system.repo_urls import same_repo


class _UpdateMixin:
    """PluginStoreManager methods: see the module docstring."""

    def _git_cache_signature(self, git_dir: Path) -> Optional[Tuple]:
        """Build a cache signature that invalidates on the kind of updates
        a plugin user actually cares about.

        Caching on ``.git/HEAD`` mtime alone is not enough: a ``git pull``
        that fast-forwards the current branch updates
        ``.git/refs/heads/<branch>`` (or ``.git/packed-refs``) but leaves
        HEAD's contents and mtime untouched. And the cached ``result``
        dict includes ``remote_url`` — a value read from ``.git/config`` —
        so a config-only change (e.g. a monorepo-migration re-pointing
        ``remote.origin.url``) must also invalidate the cache.

        Signature components:
          - HEAD contents (catches detach / branch switch)
          - HEAD mtime
          - if HEAD points at a ref, that ref file's mtime (catches
            fast-forward / reset on the current branch)
          - packed-refs mtime as a coarse fallback for repos using packed refs
          - .git/config contents + mtime (catches remote URL changes and
            any other config-only edit that affects what the cached
            ``remote_url`` field should contain)

        Returns ``None`` if HEAD cannot be read at all (caller will skip
        the cache and take the slow path).
        """
        head_file = git_dir / 'HEAD'
        try:
            head_mtime = head_file.stat().st_mtime
            head_contents = head_file.read_text(encoding='utf-8', errors='replace').strip()
        except OSError:
            return None

        ref_mtime = None
        if head_contents.startswith('ref: '):
            ref_path = head_contents[len('ref: '):].strip()
            # ``ref_path`` looks like ``refs/heads/main``. It lives either
            # as a loose file under .git/ or inside .git/packed-refs.
            loose_ref = git_dir / ref_path
            try:
                ref_mtime = loose_ref.stat().st_mtime
            except OSError:
                ref_mtime = None

        packed_refs_mtime = None
        if ref_mtime is None:
            try:
                packed_refs_mtime = (git_dir / 'packed-refs').stat().st_mtime
            except OSError:
                packed_refs_mtime = None

        config_mtime = None
        config_contents = None
        config_file = git_dir / 'config'
        try:
            config_mtime = config_file.stat().st_mtime
            config_contents = config_file.read_text(encoding='utf-8', errors='replace').strip()
        except OSError:
            config_mtime = None
            config_contents = None

        return (
            head_contents, head_mtime,
            ref_mtime, packed_refs_mtime,
            config_contents, config_mtime,
        )

    def _get_local_git_info(self, plugin_path: Path) -> Optional[Dict[str, str]]:
        """Return local git branch, commit hash, and commit date if the plugin is a git checkout.

        Results are cached keyed on a signature that includes HEAD
        contents plus the mtime of HEAD AND the resolved ref (or
        packed-refs). Repeated calls skip the ``git log`` subprocess when
        nothing has changed, and a ``git pull`` that fast-forwards the
        branch correctly invalidates the cache.
        """
        git_dir = plugin_path / '.git'
        if not git_dir.exists():
            return None

        cache_key = str(plugin_path)
        signature = self._git_cache_signature(git_dir)

        if signature is not None:
            cached = self._git_info_cache.get(cache_key)
            if cached is not None and cached[0] == signature:
                return cached[1]

        try:
            # .git may be a file (worktree / submodule) containing "gitdir: <path>".
            # Resolve it to the actual git directory before reading any files.
            try:
                if git_dir.is_file():
                    pointer = git_dir.read_text(encoding='utf-8', errors='replace').strip()
                    if pointer.startswith('gitdir:'):
                        resolved = (plugin_path / pointer[len('gitdir:'):].strip()).resolve()
                        if resolved.is_dir():
                            git_dir = resolved
                        else:
                            return None
                    else:
                        return None
            except (OSError, NotADirectoryError):
                return None

            # Read branch directly from .git/HEAD (no subprocess).
            branch = ''
            try:
                head_text = (git_dir / 'HEAD').read_text(encoding='utf-8', errors='replace').strip()
                if head_text.startswith('ref: refs/heads/'):
                    branch = head_text[len('ref: refs/heads/'):]
                elif head_text.startswith('ref: '):
                    branch = head_text[len('ref: '):]
                # else: detached HEAD — branch stays ''
            except (OSError, NotADirectoryError):
                pass

            # Remote URL from .git/config — parse [remote "origin"] url line.
            remote_url = None
            try:
                config_text = (git_dir / 'config').read_text(encoding='utf-8', errors='replace')
                in_origin = False
                for line in config_text.splitlines():
                    stripped = line.strip()
                    if stripped == '[remote "origin"]':
                        in_origin = True
                    elif stripped.startswith('['):
                        in_origin = False
                    elif in_origin and stripped.startswith('url') and '=' in stripped:
                        remote_url = stripped.split('=', 1)[1].strip()
                        break
            except (OSError, NotADirectoryError):
                pass

            # Single subprocess: SHA + commit date in one call.
            log_result = subprocess.run(
                ['git', '-C', str(plugin_path), 'log', '-1', '--format=%H%n%cI', 'HEAD'],
                capture_output=True,
                text=True,
                timeout=10,
                check=True
            )
            lines = log_result.stdout.strip().splitlines()
            sha = lines[0] if lines else ''
            commit_date_iso = lines[1] if len(lines) > 1 else ''

            result = {
                'sha': sha,
                'short_sha': sha[:7] if sha else '',
                'branch': branch,
            }

            if remote_url:
                result['remote_url'] = remote_url

            if commit_date_iso:
                result['date_iso'] = commit_date_iso
                result['date'] = self._iso_to_date(commit_date_iso)

            if signature is not None:
                self._git_info_cache[cache_key] = (signature, result)
            return result
        except subprocess.CalledProcessError as err:
            self.logger.debug(f"Failed to read git info for {plugin_path.name}: {err}")
        except subprocess.TimeoutExpired:
            self.logger.debug(f"Timed out reading git info for {plugin_path.name}")

        return None

    def _gate_pulled_commit(self, plugin_id: str, plugin_path: Path,
                            previous_sha: Optional[str]) -> bool:
        """Apply the compatibility gate to a commit that arrived via git pull.

        Every other route into an installed plugin goes through
        ``install_plugin``, which gates in ``_install_plugin_impl``. This one
        did not: a ``git pull`` could deliver a manifest flooring above this
        core and nothing would notice until the plugin failed to load, which
        surfaces as one line in the journal and a scoreboard that silently
        stopped appearing.

        The registry's ``ledmatrix_min_version`` refuses most of these before
        the pull (``update_plugin``). This is the fallback, for the same cases
        ``_install_plugin_impl``'s post-download gate covers: a registry
        without the field, a checkout on another branch than the registry's,
        and ``compatible_versions`` -- all only knowable once the new commit
        is on disk.

        Undone with ``git reset --hard`` rather than by removing the directory.
        This is a live checkout, the previous commit is still in the object
        store, and the reset leaves the user on the exact version they were
        already running -- the same promise ``_reinstall_with_rollback`` makes,
        reached by the means this path actually has. It is also the gentler
        option: no window in which the plugin directory does not exist, and no
        ``.standalone-backup-`` debris if the process dies mid-way.

        A manifest that cannot be read is not evidence of incompatibility, so
        it allows. ``compatibility.check`` refuses only on evidence for the
        same reason: a wrong refusal breaks a working install, while a wrong
        allowance degrades to exactly the behaviour this path had before the
        gate existed.
        """
        manifest_path = plugin_path / "manifest.json"
        try:
            with open(manifest_path, 'r', encoding='utf-8') as mf:
                manifest = json.load(mf)
        except (OSError, ValueError) as e:
            self.logger.warning(
                "Could not read %s after updating %s (%s); allowing the "
                "update, as an unreadable manifest declares no floor",
                manifest_path, plugin_id, e)
            return True

        from src.plugin_system import compatibility
        core_version = compatibility.current_core_version()

        compatible, reason = compatibility.check(manifest, core_version)
        if compatible:
            return True

        self.logger.error("Refusing the update to %s: %s", plugin_id, reason)
        self._note_refusal(plugin_id, reason)

        if not previous_sha:
            self.logger.error(
                "Cannot roll %s back: the commit it was on before the pull is "
                "unknown. It is now on a version this core cannot run — "
                "reinstall it from the plugin store.", plugin_id)
            return False

        # Safe by construction: update_plugin returns before pulling unless the
        # tree was clean or successfully stashed, so there are no uncommitted
        # tracked edits for --hard to discard. The stash is not popped on the
        # success path either, so the reset leaves the working tree exactly
        # where a successful pull would have. Say "commit", not "changes".
        reset = subprocess.run(
            ['git', '-C', str(plugin_path), 'reset', '--hard', previous_sha],
            capture_output=True, text=True, timeout=60, check=False)
        if reset.returncode != 0:
            self.logger.error(
                "CRITICAL: could not roll %s back to commit %s: %s. It is left "
                "on a version this core cannot run; "
                "`git -C %s reset --hard %s` restores it.",
                plugin_id, previous_sha[:7],
                (reset.stderr or reset.stdout or '').strip(),
                plugin_path, previous_sha)
        else:
            self.logger.info(
                "Rolled %s back to commit %s; it stays on the version it was "
                "already running.", plugin_id, previous_sha[:7])
        return False

    def _reinstall_with_rollback(self, plugin_id: str, plugin_path: Path) -> bool:
        """Replace an installed plugin with a fresh install, atomically.

        The old install is renamed aside (not deleted) until the new install
        succeeds, then removed; on ANY install failure the old directory is
        restored. Deleting first turns a failed download into a destroyed
        plugin: during the monorepo migration a Pi with broken DNS lost every
        old-remote plugin that way, with none able to be re-downloaded.

        The aside name embeds BACKUP_MARKER ('.standalone-backup-') so every
        plugin directory lookup (src/plugin_system/plugin_dirs.py) ignores it
        even though it still contains a manifest.json.

        Held for the whole operation under a per-plugin_id lock: two
        overlapping requests for the same plugin (double-click, two
        browser tabs — the web UI runs Flask with threaded=True) must not
        interleave their renames, or the second could steal the first's
        rollback safety net mid-install. Other plugin_ids are unaffected.
        """
        with self._get_reinstall_lock(plugin_id):
            backup_path = plugin_path.with_name(
                f"{plugin_path.name}{BACKUP_MARKER}migrating")
            problem = self._set_aside(plugin_path, backup_path)
            if problem:
                self.logger.error(
                    "Not updating %s: %s; the installed version is left in place",
                    plugin_id, problem)
                return False

            try:
                installed = self.install_plugin(plugin_id)
            except Exception as e:
                self.logger.error(f"Reinstall of {plugin_id} raised: {e}")
                installed = False

            if installed:
                self._discard_backup(plugin_id, backup_path, "update")
                return True

            # Bad network, registry error...: the user keeps a working plugin.
            self._restore_backup(plugin_id, plugin_path, backup_path, "Reinstall")
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

            # Check if this is a bundled/unmanaged plugin (no registry entry, no git remote)
            # These are plugins shipped with LEDMatrix itself and updated via LEDMatrix updates.
            metadata_path = plugin_path / ".plugin_metadata.json"
            if metadata_path.exists():
                try:
                    with open(metadata_path, 'r', encoding='utf-8') as f:
                        metadata = json.load(f)
                    if metadata.get('install_type') == 'bundled':
                        self.logger.info(f"Plugin {plugin_id} is a bundled plugin; updates are delivered via LEDMatrix itself")
                        return True
                except (OSError, ValueError) as e:
                    self.logger.debug(f"[PluginStore] Could not read metadata for {plugin_id} at {metadata_path}: {e}")

            # First check if it's a git repository - if so, we can update directly
            git_info = self._get_local_git_info(plugin_path)
            
            if git_info:
                # Plugin is a git repository - try to update via git
                local_branch = git_info.get('branch') or 'main'
                local_sha = git_info.get('sha')

                # Try to get remote info from registry (optional)
                self.fetch_registry(force_refresh=True)
                plugin_info_remote = self.get_plugin_info(plugin_id, fetch_latest_from_github=True, force_refresh=True)
                # Try without 'ledmatrix-' prefix (monorepo migration)
                resolved_id = plugin_id
                if not plugin_info_remote and plugin_id.startswith('ledmatrix-'):
                    alt_id = plugin_id[len('ledmatrix-'):]
                    plugin_info_remote = self.get_plugin_info(alt_id, fetch_latest_from_github=True, force_refresh=True)
                    if plugin_info_remote:
                        resolved_id = alt_id
                        self.logger.info(f"Plugin {plugin_id} found in registry as {resolved_id}")
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
                    if local_remote and registry_repo and not same_repo(local_remote, registry_repo):
                        self.logger.info(
                            f"Plugin {resolved_id} git remote ({local_remote}) differs from registry ({registry_repo}). "
                            f"Reinstalling from registry to migrate to new source."
                        )
                        # Before the old copy is moved aside: the reinstall
                        # would only refuse after a download and a restore.
                        if self._refuse_if_registry_incompatible(
                                resolved_id, plugin_info_remote, "update", record_as=plugin_id):
                            return False
                        return self._reinstall_with_rollback(resolved_id, plugin_path)

                    # Check if already up to date
                    if remote_sha and local_sha and remote_sha.startswith(local_sha):
                        self.logger.info(f"Plugin {plugin_id} already matches remote commit {remote_sha[:7]}")
                        return True

                    # The registry's floor describes its branch; a checkout
                    # on another branch pulls another release, and the gate
                    # after the pull (_gate_pulled_commit) still covers it.
                    if (not remote_branch or remote_branch == local_branch) and \
                            self._refuse_if_registry_incompatible(
                                resolved_id, plugin_info_remote, "update", record_as=plugin_id):
                        return False

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
                        
                        # Check for tracked file changes
                        status_result = subprocess.run(
                            ['git', '-C', str(plugin_path), 'status', '--porcelain', '--untracked-files=no'],
                            capture_output=True,
                            text=True,
                            timeout=30,
                            check=False
                        )
                        has_changes = bool(status_result.stdout.strip())
                        
                        # If there are untracked files, stash them
                        if untracked_files:
                            self.logger.info(f"Found {len(untracked_files)} untracked files in {plugin_id}, will stash them")
                            has_changes = True
                    except subprocess.TimeoutExpired:
                        # If status check times out, assume there might be changes and proceed
                        self.logger.warning(f"Git status check timed out for {plugin_id}, proceeding with update")
                        has_changes = True
                    
                    stash_info = ""
                    # Whether the pull can be undone without destroying work.
                    tree_is_recoverable = not has_changes
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
                                tree_is_recoverable = True
                                self.logger.info(f"Stashed local changes (including untracked files) for {plugin_id}")
                            else:
                                self.logger.warning(f"Failed to stash local changes for {plugin_id}: {stash_result.stderr}")
                        except subprocess.TimeoutExpired:
                            self.logger.warning(f"Stash operation timed out for {plugin_id}, proceeding with pull")

                    # Do not pull what cannot be un-pulled.
                    #
                    # The compatibility gate below can refuse the commit this
                    # pull brings down, and its only way back is `git reset
                    # --hard`, which discards uncommitted tracked edits. Those
                    # edits are exactly what the stash above exists to protect,
                    # so a stash that failed or timed out leaves the rollback
                    # unable to run without destroying them.
                    #
                    # A pull does not necessarily refuse on a dirty tree -- git
                    # merges happily as long as the incoming commit touches
                    # different files -- so without this the update would
                    # succeed, the gate would refuse, and the reset would take
                    # the user's work with it. Refusing here costs an update in
                    # a case that already went wrong; the alternative costs
                    # data.
                    if not tree_is_recoverable:
                        self.logger.error(
                            "Refusing to update %s: it has local changes that could "
                            "not be stashed, and an incompatible update could then "
                            "only be rolled back by discarding them. Commit or stash "
                            "them by hand, then update.", plugin_id)
                        return False

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

                    # The install gate, at the only point on this path where
                    # it can be answered. Every other route in goes through
                    # install_plugin, which gates in _install_plugin_impl; this
                    # one did not, so a pull could deliver a manifest flooring
                    # above this core and nothing would notice.
                    if not self._gate_pulled_commit(plugin_id, plugin_path, local_sha):
                        return False

                    self._install_dependencies(plugin_path)
                    return True

                except subprocess.CalledProcessError as git_error:
                    error_output = git_error.stderr or git_error.stdout or "Unknown error"
                    cmd_str = ' '.join(git_error.cmd)
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
                    elif "conflict" in error_lower:
                        self.logger.error(f"Merge conflict detected for {plugin_id}. Resolve conflicts manually or reinstall plugin.")
                    
                    return False
                except subprocess.TimeoutExpired:
                    self.logger.warning(f"Git update timed out for {plugin_id}")
                    return False
            
            # A plugin with its own .git that _get_local_git_info could not
            # read (e.g. no commits yet) may still name a remote to reinstall
            # from. Without its own .git, `git -C <plugin>` walks up and finds
            # the enclosing LEDMatrix checkout when plugins live in
            # plugin-repos/ -- `--local` does not prevent that -- and the
            # "plugin's" remote would be LEDMatrix itself.
            repo_url = None
            if (plugin_path / '.git').exists():
                try:
                    remote_url_result = subprocess.run(
                        ['git', '-C', str(plugin_path), 'config', '--local', '--get', 'remote.origin.url'],
                        capture_output=True,
                        text=True,
                        timeout=10,
                        check=False
                    )
                    if remote_url_result.returncode == 0:
                        repo_url = remote_url_result.stdout.strip() or None
                        if repo_url:
                            self.logger.info(f"Found git remote URL for {plugin_id}: {repo_url}")
                except (OSError, subprocess.SubprocessError) as e:
                    self.logger.debug(f"Could not get git remote URL: {e}")
            
            # Try registry-based update
            self.logger.info(f"Plugin {plugin_id} is not a git repository, checking registry...")
            self.fetch_registry(force_refresh=True)
            plugin_info_remote = self.get_plugin_info(plugin_id, fetch_latest_from_github=True, force_refresh=True)

            # If not found, try without 'ledmatrix-' prefix (monorepo migration)
            registry_id = plugin_id
            if not plugin_info_remote and plugin_id.startswith('ledmatrix-'):
                alt_id = plugin_id[len('ledmatrix-'):]
                plugin_info_remote = self.get_plugin_info(alt_id, fetch_latest_from_github=True, force_refresh=True)
                if plugin_info_remote:
                    registry_id = alt_id
                    self.logger.info(f"Plugin {plugin_id} found in registry as {alt_id}")

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
                    self.logger.warning("Plugin may have been installed via ZIP download. Try reinstalling from GitHub URL to enable updates.")
                return False

            repo_url = plugin_info_remote.get('repo')
            remote_sha = plugin_info_remote.get('last_commit_sha')
            remote_branch = plugin_info_remote.get('branch') or plugin_info_remote.get('default_branch')

            # Compare local manifest version against registry latest_version
            # to avoid unnecessary reinstalls for monorepo plugins. Uses the
            # same semantic comparator as the web UI's update badge, so
            # equivalent spellings ("v1.2.0" vs "1.2.0") never trigger a
            # reinstall and a locally-ahead version is never downgraded.
            try:
                local_manifest_path = plugin_path / "manifest.json"
                if local_manifest_path.exists():
                    with open(local_manifest_path, 'r', encoding='utf-8') as f:
                        local_manifest = json.load(f)
                    local_version = local_manifest.get('version', '')
                    remote_version = plugin_info_remote.get('latest_version', '')
                    from src.plugin_system.compatibility import is_update_available
                    # No truthiness gate: the shared comparator already treats
                    # a missing version on either side as "no update", and the
                    # store must agree with the UI badge in that case too. A
                    # missing manifest (not just a missing version field)
                    # still falls through to the reinstall recovery path.
                    if not is_update_available(local_version, remote_version):
                        self.logger.info(
                            f"Plugin {plugin_id} already at latest version "
                            f"(installed {local_version}, registry {remote_version})")
                        return True
            except Exception as e:
                self.logger.debug(f"Could not compare versions for {plugin_id}: {e}")

            # A newer version this core cannot run: refuse now, while the
            # installed copy is untouched, rather than after a download.
            if self._refuse_if_registry_incompatible(
                    registry_id, plugin_info_remote, "update", record_as=plugin_id):
                return False

            # Plugin is not a git repo but is in registry and has a newer version - reinstall
            self.logger.info(f"Plugin {plugin_id} not installed via git; re-installing latest archive (registry id: {registry_id})")

            # Reinstall with the old version kept aside until the new
            # download succeeds — this is the path every routine store
            # update takes, and a mid-update network failure must not
            # destroy the user's plugin.
            return self._reinstall_with_rollback(registry_id, plugin_path)

        except Exception as e:
            self.logger.error(f"Error updating plugin {plugin_id}: {e}", exc_info=True)
            return False
