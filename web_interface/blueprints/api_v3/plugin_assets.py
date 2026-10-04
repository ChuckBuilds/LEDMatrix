"""Plugin asset uploads (list, upload, delete) and plugin static files.

Routes decorate the shared `api_v3` Blueprint from the package `__init__`,
so their endpoint names do not depend on which module they live in.
"""
import mimetypes

from flask import send_file

from web_interface.blueprints.api_v3 import (
    PROJECT_ROOT, _plugin_directory, api_v3, datetime, hashlib,
    json, jsonify, logger, os, request, uuid,
)
from src.common.path_safety import (
    resolve_under, safe_path_component, safe_relative_parts,
)
from src.config_manager_atomic import atomic_write_text
import web_interface.blueprints.api_v3 as _pkg
# Read through the module rather than bound by value: tests patch these
# as module attributes, and a value binding would not see the patch.
# Several are also called from helpers that live in __init__, so the
# package is the only patch point that covers every caller.


def _plugin_uploads_dir(plugin_id):
    """assets/plugins/<plugin_id>/uploads for a request-supplied id, or None.

    Same guard as the serving route in app.py: one plain path segment,
    resolved and contained under assets/plugins.
    """
    return resolve_under(PROJECT_ROOT / 'assets' / 'plugins', plugin_id, 'uploads')


def _write_upload_metadata(metadata_file, metadata):
    """Replace an uploads directory's .metadata.json atomically.

    The plugin config page reads this file to list a plugin's images; one cut
    off mid-write (a power loss on a Pi) failed to parse, and every upload it
    recorded dropped out of the list while the files stayed on disk.
    """
    atomic_write_text(metadata_file, json.dumps(metadata, indent=2))


@api_v3.route('/plugins/assets/upload', methods=['POST'])
def upload_plugin_asset():
    """Upload asset files for a plugin"""
    plugin_id = request.form.get('plugin_id')
    if not plugin_id:
        return jsonify({'status': 'error', 'message': 'plugin_id is required'}), 400

    if 'files' not in request.files:
        return jsonify({'status': 'error', 'message': 'No files provided'}), 400

    files = request.files.getlist('files')
    if not files or all(not f.filename for f in files):
        return jsonify({'status': 'error', 'message': 'No files provided'}), 400

    # Validate file count
    if len(files) > 10:
        return jsonify({'status': 'error', 'message': 'Maximum 10 files per upload'}), 400

    # Setup plugin assets directory. plugin_id is a form field: without
    # the guard '../../config' created, listed and wrote into directories
    # outside assets/plugins (the serving route was fixed in #561).
    assets_dir = _plugin_uploads_dir(plugin_id)
    if assets_dir is None:
        return jsonify({'status': 'error', 'message': 'Invalid plugin_id'}), 400
    plugin_id = safe_path_component(plugin_id)
    assets_dir.mkdir(parents=True, exist_ok=True)

    # Load metadata file
    metadata_file = assets_dir / '.metadata.json'
    if metadata_file.exists():
        with open(metadata_file, 'r') as f:
            metadata = json.load(f)
    else:
        metadata = {}

    uploaded_files = []
    total_size = 0
    max_size_per_file = 5 * 1024 * 1024  # 5MB
    max_total_size = 50 * 1024 * 1024  # 50MB

    # Calculate current total size
    for entry in metadata.values():
        if 'size' in entry:
            total_size += entry.get('size', 0)

    # Every file is checked before any is saved. Checking and saving in one
    # loop meant a bad third file answered 400 after the first two were
    # already written -- on disk and in the metadata the UI lists, though
    # the user was told the upload failed.
    accepted = []
    for file in files:
        if not file.filename:
            continue

        # Validate file type
        allowed_extensions = ['.png', '.jpg', '.jpeg', '.bmp', '.gif']
        file_ext = '.' + file.filename.lower().split('.')[-1]
        if file_ext not in allowed_extensions:
            return jsonify({
                'status': 'error',
                'message': f'Invalid file type: {file_ext}. Allowed: {allowed_extensions}'
            }), 400

        # Read file to check size and validate
        file.seek(0, os.SEEK_END)
        file_size = file.tell()
        file.seek(0)

        if file_size > max_size_per_file:
            return jsonify({
                'status': 'error',
                'message': f'File {file.filename} exceeds 5MB limit'
            }), 400

        if total_size + file_size > max_total_size:
            return jsonify({
                'status': 'error',
                'message': 'Upload would exceed 50MB total storage limit'
            }), 400

        # Validate file is actually an image (check magic bytes)
        file_content = file.read(8)
        file.seek(0)
        is_valid_image = False
        if file_content.startswith(b'\x89PNG\r\n\x1a\n'):  # PNG
            is_valid_image = True
        elif file_content[:2] == b'\xff\xd8':  # JPEG
            is_valid_image = True
        elif file_content[:2] == b'BM':  # BMP
            is_valid_image = True
        elif file_content[:6] in [b'GIF87a', b'GIF89a']:  # GIF
            is_valid_image = True

        if not is_valid_image:
            return jsonify({
                'status': 'error',
                'message': f'File {file.filename} is not a valid image file'
            }), 400

        total_size += file_size
        accepted.append((file, file_ext, file_size, file_content))

    for file, file_ext, file_size, file_content in accepted:
        # Generate unique filename
        timestamp = int(_pkg.time.time())
        # Only makes the filename unique; nothing is verified with it.
        file_hash = hashlib.sha256(file_content + file.filename.encode()).hexdigest()[:8]
        safe_filename = f"image_{timestamp}_{file_hash}{file_ext}"
        file_path = assets_dir / safe_filename

        # Ensure filename is unique
        counter = 1
        while file_path.exists():
            safe_filename = f"image_{timestamp}_{file_hash}_{counter}{file_ext}"
            file_path = assets_dir / safe_filename
            counter += 1

        # Save file
        file.save(str(file_path))

        # Make file readable
        os.chmod(file_path, 0o644)

        # Generate unique ID
        image_id = str(uuid.uuid4())

        # Store metadata
        relative_path = f"assets/plugins/{plugin_id}/uploads/{safe_filename}"
        metadata[image_id] = {
            'id': image_id,
            'filename': safe_filename,
            'path': relative_path,
            'size': file_size,
            'uploaded_at': datetime.utcnow().isoformat() + 'Z',
            'original_filename': file.filename
        }

        uploaded_files.append({
            'id': image_id,
            'filename': safe_filename,
            'path': relative_path,
            'size': file_size,
            'uploaded_at': metadata[image_id]['uploaded_at']
        })

    _write_upload_metadata(metadata_file, metadata)

    return jsonify({
        'status': 'success',
        'uploaded_files': uploaded_files,
        'total_files': len(metadata)
    })


@api_v3.route('/plugins/<plugin_id>/static/<path:file_path>', methods=['GET'])
def serve_plugin_static(plugin_id, file_path):
    """Serve static files from plugin directory.

    Both URL parts are validated before anything is opened. This handler used
    to read whatever the path resolved to as long as ``str(file).startswith``
    the plugin directory, which let two different things through:

    * ``plugin_id`` of ``..`` -- Flask's default converter forbids a slash but
      not dots, and ``get_plugin_directory('..')`` happily returned the parent
      of the plugins directory because it exists. Every file under the project
      root then "started with" that directory, ``config/config_secrets.json``
      included.
    * a sibling directory sharing a prefix: with the plugin directory
      ``plugin-repos/foo``, ``../foo-evil/x`` resolves to
      ``plugin-repos/foo-evil/x``, whose string does start with
      ``plugin-repos/foo``.
    """
    safe_plugin_id = safe_path_component(plugin_id)
    if not safe_plugin_id:
        return jsonify({'status': 'error', 'message': 'Invalid plugin ID'}), 400

    safe_parts = safe_relative_parts(file_path)
    if not safe_parts:
        return jsonify({'status': 'error', 'message': 'Invalid file path'}), 400

    plugin_dir = _plugin_directory(safe_plugin_id)
    if not plugin_dir:
        return jsonify({'status': 'error', 'message': 'Plugin not found'}), 404

    # Containment is still checked after resolving: name validation cannot
    # see a symlink inside the plugin directory that points out of it.
    requested_file = resolve_under(plugin_dir, *safe_parts)
    if requested_file is None:
        return jsonify({'status': 'error', 'message': 'Invalid file path'}), 403

    # Check if file exists
    if not requested_file.exists() or not requested_file.is_file():
        return jsonify({'status': 'error', 'message': 'File not found'}), 404

    # Determine content type. Text keeps the types this route always set;
    # anything else (an icon, a preview image) gets its own.
    name = requested_file.name
    if name.endswith('.html'):
        content_type = 'text/html'
    elif name.endswith('.js'):
        content_type = 'application/javascript'
    elif name.endswith('.css'):
        content_type = 'text/css'
    elif name.endswith('.json'):
        content_type = 'application/json'
    else:
        guessed = mimetypes.guess_type(name)[0]
        content_type = ('text/plain' if not guessed or guessed.startswith('text/')
                        else guessed)

    # Sent as bytes. Opening it as UTF-8 text failed to decode any binary
    # file, so an image answered 500 UnicodeDecodeError.
    return send_file(requested_file, mimetype=content_type)


@api_v3.route('/plugins/assets/delete', methods=['POST'])
def delete_plugin_asset():
    """Delete an asset file for a plugin"""
    # silent=True: without it a missing or non-JSON body raised inside
    # get_json() and came back as a 415 in the generic error shape, or, for
    # a JSON array, an AttributeError 500.
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({'status': 'error', 'message': 'plugin_id and image_id are required'}), 400
    plugin_id = data.get('plugin_id')
    image_id = data.get('image_id')

    if not plugin_id or not image_id:
        return jsonify({'status': 'error', 'message': 'plugin_id and image_id are required'}), 400

    # Get asset directory
    assets_dir = _plugin_uploads_dir(plugin_id)
    if assets_dir is None:
        return jsonify({'status': 'error', 'message': 'Invalid plugin_id'}), 400
    metadata_file = assets_dir / '.metadata.json'

    if not metadata_file.exists():
        return jsonify({'status': 'error', 'message': 'Metadata file not found'}), 404

    # Load metadata
    with open(metadata_file, 'r') as f:
        metadata = json.load(f)

    if image_id not in metadata:
        return jsonify({'status': 'error', 'message': 'Image not found'}), 404

    # Delete file. The stored path is data, not a trusted location: only
    # unlink it when it resolves to a file directly inside this plugin's
    # uploads. An entry pointing anywhere else is dropped from the
    # metadata without touching the file it names.
    entry = metadata[image_id] if isinstance(metadata[image_id], dict) else {}
    parts = safe_relative_parts(entry.get('path'))
    file_path = resolve_under(PROJECT_ROOT, *parts) if parts else None
    if file_path is None or file_path.parent != assets_dir:
        logger.warning('Asset %s has a path outside its uploads directory; '
                       'removing the entry without deleting a file', image_id)
    elif file_path.exists():
        file_path.unlink()

    # Remove from metadata
    del metadata[image_id]

    _write_upload_metadata(metadata_file, metadata)

    return jsonify({'status': 'success', 'message': 'Image deleted successfully'})


@api_v3.route('/plugins/assets/list', methods=['GET'])
def list_plugin_assets():
    """List asset files for a plugin"""
    plugin_id = request.args.get('plugin_id')
    if not plugin_id:
        return jsonify({'status': 'error', 'message': 'plugin_id is required'}), 400

    # Get asset directory
    assets_dir = _plugin_uploads_dir(plugin_id)
    if assets_dir is None:
        return jsonify({'status': 'error', 'message': 'Invalid plugin_id'}), 400
    metadata_file = assets_dir / '.metadata.json'

    if not metadata_file.exists():
        return jsonify({'status': 'success', 'data': {'assets': []}})

    # Load metadata
    with open(metadata_file, 'r') as f:
        metadata = json.load(f)

    # Convert to list
    assets = list(metadata.values())

    return jsonify({'status': 'success', 'data': {'assets': assets}})
