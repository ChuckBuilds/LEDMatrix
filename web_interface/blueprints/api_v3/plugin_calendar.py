"""Google Calendar plugin credentials, authentication and calendar listing.

Routes decorate the shared `api_v3` Blueprint from the package `__init__`,
so their endpoint names do not depend on which module they live in.
"""
from web_interface.blueprints.api_v3 import (
    Path, _CALENDAR_LIST_MAX_PAGES, _plugin_directory,
    _prune_credential_backups, _run_calendar_registration, api_v3,
    describe_exception, json, jsonify, logger, os, redact_text, request,
    shutil,
)
from src.config_manager_atomic import atomic_write_text
import web_interface.blueprints.api_v3 as _pkg
# Read through the module rather than bound by value: tests patch these
# as module attributes, and a value binding would not see the patch.
# Several are also called from helpers that live in __init__, so the
# package is the only patch point that covers every caller.


@api_v3.route('/plugins/calendar/upload-credentials', methods=['POST'])
def upload_calendar_credentials():
    """Upload credentials.json file for calendar plugin"""
    if 'file' not in request.files:
        return jsonify({'status': 'error', 'message': 'No file provided'}), 400

    file = request.files['file']
    if not file or not file.filename:
        return jsonify({'status': 'error', 'message': 'No file provided'}), 400

    # Validate file extension
    if not file.filename.lower().endswith('.json'):
        return jsonify({'status': 'error', 'message': 'File must be a JSON file (.json)'}), 400

    # Validate file size (max 1MB for credentials)
    file.seek(0, os.SEEK_END)
    file_size = file.tell()
    file.seek(0)

    if file_size > 1024 * 1024:  # 1MB
        return jsonify({'status': 'error', 'message': 'File exceeds 1MB limit'}), 400

    # Validate it's valid JSON
    try:
        file_content = file.read()
        file.seek(0)
        creds_data = json.loads(file_content)
    except json.JSONDecodeError:
        return jsonify({'status': 'error', 'message': 'File is not valid JSON'}), 400

    # Validate it looks like Google OAuth credentials. A bare scalar, a
    # list, true/null — all valid JSON, none of them credentials. Reject
    # rather than save: a file written as credentials.json but unusable
    # as credentials only fails later, somewhere less obvious.
    if not isinstance(creds_data, dict) or not (
            'installed' in creds_data or 'web' in creds_data):
        return jsonify({
            'status': 'error',
            'message': 'File does not appear to be a valid Google OAuth credentials file'
        }), 400

    plugin_dir = _plugin_directory('calendar')
    if not plugin_dir:
        return jsonify({'status': 'error', 'message': 'Plugin not found'}), 404

    # Save file to plugin directory
    credentials_path = Path(plugin_dir) / 'credentials.json'

    # Backup existing file if it exists
    if credentials_path.exists():
        backup_path = Path(plugin_dir) / f'credentials.json.backup.{int(_pkg.time.time())}'
        shutil.copy2(credentials_path, backup_path)
        _prune_credential_backups(Path(plugin_dir))

    # Save new file: atomically, and created 0o600 (read/write for owner
    # only) rather than chmod-ed after. file.save() truncated the old file
    # first, so a failure mid-write left the plugin with a broken
    # credentials.json, and the secret sat world-readable until the chmod.
    atomic_write_text(credentials_path,
                      file_content.decode(json.detect_encoding(file_content)),
                      mode=0o600)

    return jsonify({
        'status': 'success',
        'message': 'Credentials file uploaded successfully',
        # Relative to the plugin: nothing reads this field, and the server's
        # absolute layout is not the client's business.
        'path': credentials_path.name
    })


@api_v3.route('/plugins/calendar/authenticate', methods=['POST'])
def authenticate_calendar():
    """Google OAuth for the calendar plugin, in the two steps it requires.

    Step 1 (no body) returns the consent URL to open. Step 2 posts back the
    URL Google redirected to -- it fails to load, because the redirect points
    at a loopback address nothing is listening on, but the address bar carries
    the authorization code -- and the script exchanges it for a token.

    Two calls rather than one because the user has to visit Google in between.
    The script persists the PKCE verifier from step 1 for step 2 to reuse; the
    exchange fails with "Missing code verifier" otherwise.
    """
    plugin_dir = _pkg._calendar_plugin_dir()
    if plugin_dir is None:
        return jsonify({
            'status': 'error',
            'message': 'The calendar plugin is not installed'
        }), 404

    if not (plugin_dir / 'credentials.json').exists():
        return jsonify({
            'status': 'error',
            'message': ('No credentials.json yet. Upload your Google OAuth '
                        'client file first (Step 1).')
        }), 400

    data = request.get_json(silent=True) or {}
    redirect_url = (data.get('redirect_url') or data.get('code') or '').strip()

    payload, error = _run_calendar_registration(plugin_dir, redirect_url)
    if error:
        return jsonify({'status': 'error', 'message': error}), 500
    if payload.get('status') != 'success':
        # The script's own diagnosis is more useful than anything that
        # could be reconstructed here -- but it interpolates exceptions
        # into its messages, so it reaches the client redacted and the
        # original goes to the log.
        logger.error('calendar authentication failed: %s', payload)
        safe = dict(payload)
        safe['message'] = redact_text(str(payload.get('message', '')
                                          or 'Authentication failed'))
        return jsonify(safe), 400
    return jsonify(payload)


@api_v3.route('/plugins/calendar/list-calendars', methods=['GET'])
def list_calendar_calendars():
    """The calendars this account can see, for the config picker.

    Reads the token the OAuth flow wrote rather than shelling out again: the
    picker is used interactively and a subprocess per click is slower than the
    API call it would be wrapping.
    """
    plugin_dir = _pkg._calendar_plugin_dir()
    if plugin_dir is None:
        return jsonify({
            'status': 'error',
            'message': 'The calendar plugin is not installed'
        }), 404

    token_file = plugin_dir / 'token.pickle'
    if not token_file.exists():
        return jsonify({
            'status': 'error',
            'message': ('Not authenticated with Google yet. Complete Step 2 '
                        'first, then load your calendars.')
        }), 400

    try:
        import pickle  # nosec B403 - reads only the plugin's own OAuth token  # nosemgrep
        from google.auth.transport.requests import Request as GoogleRequest
        from googleapiclient.discovery import build as build_google_service
    except ImportError as e:
        return jsonify({
            'status': 'error',
            # The name of the missing module is the whole diagnosis, but it
            # arrives as an exception, so it goes through the redactor like
            # any other -- an ImportError can quote a path.
            'message': ('The Google API libraries are not installed. Install '
                        "the calendar plugin's requirements.txt. (%s)"
                        % describe_exception(e))
        }), 500

    with open(token_file, 'rb') as handle:
        # Written only by this plugin's own OAuth flow, into its own
        # directory, and read here exactly as the plugin itself reads it.
        creds = pickle.load(handle)  # nosec B301 - locally generated token  # nosemgrep

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(GoogleRequest())
        with open(token_file, 'wb') as handle:
            pickle.dump(creds, handle)  # nosec B301 - same token, same format  # nosemgrep
        os.chmod(token_file, 0o600)

    if not creds or not creds.valid:
        return jsonify({
            'status': 'error',
            'message': ('Stored Google credentials are no longer valid. '
                        'Run Step 2 again to re-authenticate.')
        }), 400

    service = build_google_service('calendar', 'v3', credentials=creds)

    # calendarList.list returns 100 entries per page by default and caps at
    # 250, handing back a nextPageToken when there are more. Taking only
    # the first page would silently hide calendars from the picker, and the
    # user would have no way to tell the list was truncated.
    entries = []
    page_token = None
    for _ in range(_CALENDAR_LIST_MAX_PAGES):
        response = service.calendarList().list(
            maxResults=250, pageToken=page_token).execute()
        entries.extend(response.get('items', []))
        page_token = response.get('nextPageToken')
        if not page_token:
            break
    else:
        # 2500 calendars in, something is wrong with the account or the
        # token is looping; show what was collected rather than spin.
        logger.warning(
            'calendarList paging stopped at %d pages with more remaining',
            _CALENDAR_LIST_MAX_PAGES)

    calendars = [{
        'id': entry.get('id'),
        # The picker labels each row with summary and falls back to the id
        # only in its own display, so send something either way.
        'summary': entry.get('summary') or entry.get('id'),
        'primary': bool(entry.get('primary', False)),
    } for entry in entries if entry.get('id')]

    # Primary first, then alphabetically: the list is usually short but the
    # one the user wants is almost always their own calendar.
    calendars.sort(key=lambda c: (not c['primary'], c['summary'].lower()))

    return jsonify({'status': 'success', 'calendars': calendars})
