"""Backup creation, listing and restore.

Routes decorate the shared `api_v3` Blueprint from ._common, so their
endpoint names are unchanged by living here.
"""
from web_interface.blueprints.api_v3 import (
    PROJECT_ROOT, Path, _safe_backup_path, api_v3,
    datetime, json, jsonify, logger, os, plugin_store_manager, request,
    tempfile,
)
import web_interface.blueprints.api_v3 as _pkg
# Read through the module rather than bound by value: tests patch these
# as module attributes, and a value binding would not see the patch.
# Several are also called from helpers that live in __init__, so the
# package is the only patch point that covers every caller.


@api_v3.route('/backup/preview', methods=['GET'])
def backup_preview():
    """Return a summary of what a new backup would include."""
    try:
        from src.backup_manager import preview_backup_contents
        data = preview_backup_contents(PROJECT_ROOT)
        return jsonify({'status': 'success', 'data': data})
    except Exception as e:
        logger.error("backup_preview failed: %s", e, exc_info=True)
        return jsonify({'status': 'error', 'message': 'An internal error occurred; see logs for details'}), 500
@api_v3.route('/backup/list', methods=['GET'])
def backup_list():
    """List backup ZIPs stored in the export directory."""
    try:
        _pkg._BACKUP_EXPORT_DIR.mkdir(parents=True, exist_ok=True)
        entries = []
        for p in sorted(_pkg._BACKUP_EXPORT_DIR.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
            if not p.is_file() or p.suffix != '.zip':
                continue
            st = p.stat()
            entries.append({
                'filename': p.name,
                'size': st.st_size,
                'created_at': datetime.fromtimestamp(st.st_mtime).strftime('%Y-%m-%d %H:%M:%S'),
            })
        return jsonify({'status': 'success', 'data': entries})
    except Exception as e:
        logger.error("backup_list failed: %s", e, exc_info=True)
        return jsonify({'status': 'error', 'message': 'An internal error occurred; see logs for details'}), 500
@api_v3.route('/backup/export', methods=['POST'])
def backup_export():
    """Create a new backup ZIP and return its filename."""
    try:
        from src.backup_manager import create_backup
        zip_path = create_backup(PROJECT_ROOT, output_dir=_pkg._BACKUP_EXPORT_DIR)
        return jsonify({'status': 'success', 'filename': zip_path.name})
    except Exception as e:
        logger.error("backup_export failed: %s", e, exc_info=True)
        return jsonify({'status': 'error', 'message': 'An internal error occurred; see logs for details'}), 500
@api_v3.route('/backup/validate', methods=['POST'])
def backup_validate():
    """Validate an uploaded backup ZIP and return its manifest."""
    try:
        from src.backup_manager import validate_backup
        if 'backup_file' not in request.files:
            return jsonify({'status': 'error', 'message': 'No backup_file in request'}), 400
        f = request.files['backup_file']
        with tempfile.NamedTemporaryFile(suffix='.zip', delete=False) as tmp:
            tmp_path = tmp.name
            f.save(tmp_path)
        try:
            ok, err_msg, manifest = validate_backup(Path(tmp_path))
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        if not ok:
            logger.warning("Backup validation failed: %s", err_msg)
            return jsonify({'status': 'error', 'message': 'Invalid or corrupted backup file'}), 400
        safe_manifest = {
            'schema_version': manifest.get('schema_version'),
            'created_at': manifest.get('created_at'),
            'ledmatrix_version': manifest.get('ledmatrix_version'),
            'hostname': manifest.get('hostname'),
            'contents': manifest.get('contents', []),
            'detected_contents': manifest.get('detected_contents', []),
            'plugins': manifest.get('plugins', []),
            'total_uncompressed': manifest.get('total_uncompressed'),
            'file_count': manifest.get('file_count'),
        }
        return jsonify({'status': 'success', 'data': safe_manifest})
    except Exception as e:
        logger.error("backup_validate failed: %s", e, exc_info=True)
        return jsonify({'status': 'error', 'message': 'An internal error occurred; see logs for details'}), 500
@api_v3.route('/backup/restore', methods=['POST'])
def backup_restore():
    """Restore a backup ZIP with optional RestoreOptions."""
    try:
        from src.backup_manager import restore_backup, RestoreOptions
        if 'backup_file' not in request.files:
            return jsonify({'status': 'error', 'message': 'No backup_file in request'}), 400
        f = request.files['backup_file']
        options_raw = request.form.get('options', '{}')
        try:
            opts_dict = json.loads(options_raw)
        except json.JSONDecodeError:
            opts_dict = None
        if not isinstance(opts_dict, dict):
            # Every option defaults to True, so falling back to {} on a
            # parse failure would silently perform a FULL restore —
            # secrets and all — for a caller who asked for a narrow one
            # and mis-serialized it. Refuse instead of guessing.
            return jsonify({
                'status': 'error',
                'message': 'Invalid options: expected a JSON object',
            }), 400
        options = RestoreOptions(
            restore_config=bool(opts_dict.get('restore_config', True)),
            restore_secrets=bool(opts_dict.get('restore_secrets', True)),
            restore_wifi=bool(opts_dict.get('restore_wifi', True)),
            restore_fonts=bool(opts_dict.get('restore_fonts', True)),
            restore_plugin_uploads=bool(opts_dict.get('restore_plugin_uploads', True)),
            reinstall_plugins=bool(opts_dict.get('reinstall_plugins', True)),
        )
        with tempfile.NamedTemporaryFile(suffix='.zip', delete=False) as tmp:
            tmp_path = tmp.name
            f.save(tmp_path)
        try:
            result = restore_backup(Path(tmp_path), PROJECT_ROOT, options)
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

        # Reinstall plugins if requested and store manager available
        if options.reinstall_plugins and result.plugins_to_install:
            psm = getattr(api_v3, 'plugin_store_manager', None) or plugin_store_manager
            for plug in result.plugins_to_install:
                pid = plug.get('plugin_id')
                if not pid:
                    continue
                try:
                    if psm and hasattr(psm, 'install_plugin'):
                        ok = psm.install_plugin(pid)
                        if ok:
                            result.plugins_installed.append(pid)
                        else:
                            result.plugins_failed.append({'plugin_id': pid, 'error': 'install_plugin returned False'})
                    else:
                        result.plugins_failed.append({'plugin_id': pid, 'error': 'Store manager unavailable'})
                except Exception as pe:
                    logger.error(
                        "[Backup] Failed to reinstall plugin %r: %s", pid, pe, exc_info=True
                    )
                    result.plugins_failed.append({'plugin_id': pid, 'error': 'Installation failed; see server logs'})

        # A restore that dropped files can still report success if the only
        # failures were plugin reinstalls, since those don't touch result.errors.
        if result.plugins_failed:
            result.success = False

        data = result.to_dict()
        if not result.success:
            # Name what failed, and what nonetheless landed. A restore is
            # partial far more often than it is total -- a fresh install can
            # leave config_secrets.json unwritable by the web service, so
            # config restores and secrets do not. "Restore had errors" alone
            # left the user unable to tell a wholly failed restore from one
            # that quietly dropped their API keys.
            failed_plugins = [
                str(p.get('plugin_id')) for p in (result.plugins_failed or []) if p.get('plugin_id')
            ]
            parts = []
            if result.restored:
                parts.append(f"restored: {', '.join(result.restored)}")
            if result.errors:
                parts.append(f"failed: {'; '.join(result.errors)}")
            if failed_plugins:
                parts.append(f"plugins not reinstalled: {', '.join(failed_plugins)}")
            message = 'Restore incomplete — ' + ('. '.join(parts) if parts else 'see logs')
            return jsonify({'status': 'error', 'message': message, 'data': data}), 500
        return jsonify({'status': 'success', 'data': data})
    except Exception as e:
        logger.error("backup_restore failed: %s", e, exc_info=True)
        return jsonify({'status': 'error', 'message': 'An internal error occurred; see logs for details'}), 500
@api_v3.route('/backup/download/<path:filename>', methods=['GET'])
def backup_download(filename):
    """Stream a backup ZIP to the browser."""
    from flask import send_from_directory
    if _safe_backup_path(filename) is None:
        return jsonify({'status': 'error', 'message': 'Backup not found'}), 404
    try:
        # send_from_directory uses werkzeug safe_join internally — CodeQL-recognized sanitizer.
        return send_from_directory(_pkg._BACKUP_EXPORT_DIR, filename, as_attachment=True)
    except FileNotFoundError:
        return jsonify({'status': 'error', 'message': 'Backup not found'}), 404
@api_v3.route('/backup/<path:filename>', methods=['DELETE'])
def backup_delete(filename):
    """Delete a stored backup ZIP."""
    safe = _safe_backup_path(filename)
    if safe is None:
        return jsonify({'status': 'error', 'message': 'Backup not found'}), 404
    # Enumerate the export directory and match by name so the unlink target is
    # a filesystem-derived path rather than one constructed from user input.
    try:
        for entry in _pkg._BACKUP_EXPORT_DIR.iterdir():
            if entry.is_file() and entry.name == safe.name:
                entry.unlink()
                return jsonify({'status': 'success'})
    except OSError as e:
        logger.error("backup_delete failed: %s", e, exc_info=True)
        return jsonify({'status': 'error', 'message': 'An internal error occurred; see logs for details'}), 500
    return jsonify({'status': 'error', 'message': 'Backup not found'}), 404
