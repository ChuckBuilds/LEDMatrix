"""
Optional login for the web interface, off by default.

Nothing here changes a device that has not set a password: with no password
stored, the hook below returns immediately and every page and API route
answers exactly as before. The cross-site Origin guard
(``web_interface/origin_guard.py``) is separate and always on.

Turning it on is setting a password (General tab -> Security). From then on
every page and API route needs one of:

* a login session (the ``/login`` page; a signed cookie, 30 days);
* an API token, sent as ``Authorization: Bearer <token>`` (for Home Assistant,
  scripts and the MQTT bridge when it runs on another machine).

and these stay open without either:

* requests from the Pi itself (loopback, and no proxy headers -- a reverse
  proxy on the same Pi would otherwise make every request look local);
* the Wi-Fi setup flow (``/setup`` and the status/scan/connect routes it
  calls) while the Pi is in access-point mode, so a Pi that lost its network
  can still be put back on one;
* static assets, the captive-portal probe URLs, the login page itself, and
  ``/api/v3/health``, which then answers only its overall status.

Where it is stored: the ``web_auth`` section of ``config/config_secrets.json``,
the file every other credential lives in. The password is a werkzeug hash
(``generate_password_hash``); each API token is stored as a SHA-256 of the
token, which is safe for a 256-bit random value and cheap enough to check on
every request, and is shown once, when it is created. The section also holds
the key that signs login cookies, so they survive a restart and die with a
password change. This module reads that file directly (cached on its mtime)
rather than through the merged config, so a ``web_auth`` key smuggled into
``config.json`` means nothing. The config API and the raw-JSON editor leave
the section out entirely.

Lost password: ``sudo python3 scripts/reset_web_password.py`` on the Pi (or
open the interface from the Pi itself, which is always allowed). See
docs/WEB_INTERFACE_GUIDE.md.
"""
import hashlib
import hmac
import json
import logging
import os
import secrets
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

from flask import (Blueprint, Flask, current_app, g, jsonify, redirect,
                   render_template, request, session, url_for)
from flask.sessions import SecureCookieSessionInterface
from itsdangerous import URLSafeTimedSerializer
from werkzeug.security import check_password_hash, generate_password_hash

logger = logging.getLogger('web_interface.auth')

#: The config_secrets.json section this module owns.
SECTION = 'web_auth'
#: Key under Flask's app.extensions.
EXTENSION_KEY = 'ledmatrix_auth'

MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 256
MAX_TOKENS = 50
MAX_TOKEN_NAME_LENGTH = 60
TOKEN_PREFIX = 'lmx_'
SESSION_KEY = 'ledmatrix_auth'
SESSION_LIFETIME = timedelta(days=30)

#: Failed attempts only (see _counts_as_a_failure). Per client address.
LOGIN_RATE_LIMIT = '5 per minute;30 per hour'

_LOOPBACK_ADDRESSES = frozenset({'127.0.0.1', '::1', '::ffff:127.0.0.1'})
#: A request carrying any of these came through a proxy, so its loopback
#: address says nothing about where the user is.
_PROXY_HEADERS = ('X-Forwarded-For', 'X-Real-IP', 'Forwarded', 'X-Forwarded-Host')

#: Open whether or not auth is on: the login page, core static files, the
#: captive-portal probes (they answer fixed text, or redirect to /setup in AP
#: mode) and the favicon.
_ALWAYS_OPEN_ENDPOINTS = frozenset({
    'static',
    'ledmatrix_auth.login',
    'ledmatrix_auth.logout',
    'hotspot_detect', 'generate_204', 'connecttest_txt', 'success_txt',
    'favicon',
})
#: Open without a session, but answering only a minimal body (see
#: request_is_authenticated).
_HEALTH_ENDPOINTS = frozenset({'api_v3.get_health'})
#: The Wi-Fi setup flow captive_setup.html drives. Open only in AP mode.
_AP_SETUP_ENDPOINTS = frozenset({
    'pages_v3.captive_setup', 'pages_v3_legacy.captive_setup',
    'api_v3.get_wifi_status', 'api_v3.scan_wifi_networks', 'api_v3.connect_wifi',
})


class AuthError(Exception):
    """A request to change auth settings that cannot be applied as asked."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _token_digest(token: str) -> str:
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


class AuthStore:
    """The ``web_auth`` section of config_secrets.json.

    Reads are cached against the file's (mtime, size), so the per-request
    check costs one ``stat``, and a change made by another process (the reset
    script, a backup restore) is seen on the next request. Writes go through
    ``ConfigManager.save_raw_file_content`` like every other secrets save:
    atomic, with the permissions secrets get.
    """

    def __init__(self, config_manager):
        self._config_manager = config_manager
        self._lock = threading.RLock()
        self._signature: Any = object()   # never equal to a real signature
        self._failed_signature: Any = None
        self._section: Dict[str, Any] = {}

    # -- reading ---------------------------------------------------------

    def _path(self) -> str:
        return self._config_manager.get_secrets_path()

    def _file_signature(self):
        try:
            st = os.stat(self._path())
        except FileNotFoundError:
            return None
        except OSError:
            return 'unreadable'
        return (st.st_mtime_ns, st.st_size)

    def _read_secrets_strict(self) -> Dict[str, Any]:
        """The whole secrets file; {} when absent. Raises on anything else.

        Strict on purpose, unlike ConfigManager.get_raw_file_content (which
        answers {} for an unreadable file): a write built on that {} would
        replace every other credential in the file.
        """
        try:
            with open(self._path(), 'r', encoding='utf-8') as fh:
                data = json.load(fh)
        except FileNotFoundError:
            return {}
        if not isinstance(data, dict):
            raise ValueError(f'{self._path()} does not hold a JSON object')
        return data

    def section(self) -> Dict[str, Any]:
        """The current ``web_auth`` section (a cached dict; do not mutate)."""
        signature = self._file_signature()
        with self._lock:
            if signature == self._signature:
                return self._section
            try:
                data = self._read_secrets_strict()
            except (OSError, ValueError) as err:
                # Keep the last good answer rather than guess. A file that was
                # never readable leaves auth off, as the rest of the app treats
                # an unreadable secrets file as "no secrets". Logged once per
                # version of the file, not once per request.
                if signature != self._failed_signature:
                    self._failed_signature = signature
                    logger.error("Could not read %s for web login settings: %s",
                                 self._path(), err)
                return self._section
            raw = data.get(SECTION)
            self._section = raw if isinstance(raw, dict) else {}
            self._signature = signature
            self._failed_signature = None
            return self._section

    def is_enabled(self) -> bool:
        return bool(self.section().get('password_hash'))

    def session_secret(self) -> Optional[str]:
        secret = self.section().get('session_secret')
        return secret if isinstance(secret, str) and secret else None

    def check_password(self, password: Any) -> bool:
        stored = self.section().get('password_hash')
        if not stored or not isinstance(stored, str) or not isinstance(password, str):
            return False
        if len(password) > MAX_PASSWORD_LENGTH:
            return False
        try:
            return check_password_hash(stored, password)
        except (ValueError, TypeError):
            logger.error("The stored web password hash is not usable; reset it "
                         "with scripts/reset_web_password.py", exc_info=True)
            return False

    def _raw_tokens(self) -> List[Dict[str, Any]]:
        tokens = self.section().get('tokens')
        if not isinstance(tokens, list):
            return []
        return [t for t in tokens if isinstance(t, dict)]

    def list_tokens(self) -> List[Dict[str, Any]]:
        """Token records without their hashes, oldest first."""
        return [_public_token(t) for t in self._raw_tokens()]

    def verify_token(self, presented: Any) -> Optional[Dict[str, Any]]:
        """The public record of the token ``presented`` is, or None."""
        if not isinstance(presented, str) or not presented or len(presented) > 200:
            return None
        digest = _token_digest(presented)
        match = None
        for record in self._raw_tokens():
            stored = record.get('hash')
            # Every record is compared, so the time taken does not depend on
            # which one matched.
            if isinstance(stored, str) and hmac.compare_digest(stored, digest):
                match = record
        return _public_token(match) if match else None

    # -- writing ---------------------------------------------------------

    def _update(self, mutate: Callable[[Dict[str, Any]], Any]) -> Any:
        with self._lock:
            data = self._read_secrets_strict()
            current = data.get(SECTION)
            section = dict(current) if isinstance(current, dict) else {}
            result = mutate(section)
            if section:
                data[SECTION] = section
            else:
                data.pop(SECTION, None)
            self._config_manager.save_raw_file_content('secrets', data)
            self._signature = object()   # re-read on next access
            return result

    def set_password(self, password: str) -> None:
        """Store a new password (turning login on) and sign everyone out.

        A new cookie-signing key is generated with it, so every session made
        under the old password stops working.
        """
        problem = password_problem(password)
        if problem:
            raise AuthError(problem)
        hashed = generate_password_hash(password)

        def mutate(section):
            section['password_hash'] = hashed
            section['session_secret'] = secrets.token_hex(32)
            section['password_set_at'] = _now_iso()
        self._update(mutate)

    def disable(self) -> None:
        """Remove the password (login off). API tokens are kept."""
        def mutate(section):
            for key in ('password_hash', 'session_secret', 'password_set_at'):
                section.pop(key, None)
        self._update(mutate)

    def create_token(self, name: str) -> Tuple[Dict[str, Any], str]:
        """A new token: (public record, the token itself -- shown once)."""
        name = (name or '').strip()
        if not name:
            raise AuthError('Give the token a name, such as "Home Assistant".')
        if len(name) > MAX_TOKEN_NAME_LENGTH:
            raise AuthError(f'Token names are at most {MAX_TOKEN_NAME_LENGTH} characters.')
        token = TOKEN_PREFIX + secrets.token_urlsafe(32)
        record = {
            'id': secrets.token_hex(8),
            'name': name,
            'hash': _token_digest(token),
            'prefix': token[:len(TOKEN_PREFIX) + 4],
            'created_at': _now_iso(),
        }

        def mutate(section):
            tokens = [t for t in (section.get('tokens') or []) if isinstance(t, dict)]
            if len(tokens) >= MAX_TOKENS:
                raise AuthError(f'There are already {MAX_TOKENS} tokens; revoke one first.')
            tokens.append(record)
            section['tokens'] = tokens
        self._update(mutate)
        return _public_token(record), token

    def revoke_token(self, token_id: str) -> bool:
        """Delete a token by id. False when there is no such token."""
        def mutate(section):
            tokens = [t for t in (section.get('tokens') or []) if isinstance(t, dict)]
            kept = [t for t in tokens if t.get('id') != token_id]
            if len(kept) == len(tokens):
                return False
            if kept:
                section['tokens'] = kept
            else:
                section.pop('tokens', None)
            return True
        # Check first, so revoking an unknown id writes nothing.
        if not any(t.get('id') == token_id for t in self._raw_tokens()):
            return False
        return self._update(mutate)


def _public_token(record: Dict[str, Any]) -> Dict[str, Any]:
    return {key: record.get(key) for key in ('id', 'name', 'prefix', 'created_at')}


def password_problem(password: Any) -> Optional[str]:
    """Why ``password`` cannot be used, or None."""
    if not isinstance(password, str) or len(password) < MIN_PASSWORD_LENGTH:
        return f'Use at least {MIN_PASSWORD_LENGTH} characters.'
    if len(password) > MAX_PASSWORD_LENGTH:
        return f'Use at most {MAX_PASSWORD_LENGTH} characters.'
    if password.strip() != password:
        return 'The password cannot start or end with a space.'
    return None


def strip_auth_section(data: Any) -> Any:
    """A shallow copy of ``data`` without the ``web_auth`` section.

    For every response that dumps a whole config or secrets dict. The section
    holds the password hash, the token hashes and the cookie-signing key; no
    client needs any of them, and the dedicated /api/v3/auth routes manage it.
    """
    if isinstance(data, dict) and SECTION in data:
        data = {k: v for k, v in data.items() if k != SECTION}
    return data


# -- request-time helpers --------------------------------------------------

def get_store(app: Optional[Flask] = None) -> Optional[AuthStore]:
    app = app or current_app
    return app.extensions.get(EXTENSION_KEY)


def is_local_request() -> bool:
    """From this machine, and not relayed by a proxy on it."""
    address = request.remote_addr or ''
    if address not in _LOOPBACK_ADDRESSES and not address.startswith('127.'):
        return False
    return not any(header in request.headers for header in _PROXY_HEADERS)


def bearer_token() -> Optional[str]:
    header = request.headers.get('Authorization', '')
    scheme, _, value = header.partition(' ')
    if scheme.lower() != 'bearer':
        return None
    return value.strip() or None


def request_is_authenticated() -> bool:
    """True unless this request got in only through an open endpoint.

    Always True when login is off. The health route uses it to decide how
    much to say.
    """
    return getattr(g, 'ledmatrix_auth_via', 'open') != 'unauthenticated'


def auth_via() -> str:
    """How this request was let in: open, localhost, session, token,
    ap-setup, or unauthenticated (an always-open endpoint)."""
    return getattr(g, 'ledmatrix_auth_via', 'open')


def sign_in_this_session() -> None:
    session.clear()
    session[SESSION_KEY] = True
    session.permanent = True


def safe_next(target: Any) -> str:
    """``target`` if it is a local path to return to after login, else ``/``.

    Only a path on this server is accepted: no scheme, no host, and not
    ``//host`` or ``/\\host``, which browsers read as another site.
    """
    if not isinstance(target, str) or not target.startswith('/'):
        return '/'
    if target.startswith(('//', '/\\')) or any(ord(c) < 32 for c in target):
        return '/'
    parts = urlsplit(target)
    if parts.scheme or parts.netloc:
        return '/'
    if parts.path.rstrip('/') in ('/login', '/logout', '/v3/login', '/v3/logout'):
        return '/'
    return target


def _return_path() -> str:
    """Where the login page should send the user back to."""
    if request.headers.get('HX-Request') == 'true':
        current = request.headers.get('HX-Current-URL', '')
        parts = urlsplit(current)
        path = parts.path or '/'
        return safe_next(path + (f'?{parts.query}' if parts.query else ''))
    if _is_page_navigation():
        path = request.full_path if request.query_string else request.path
        return safe_next(path)
    return '/'


def _is_page_navigation() -> bool:
    if request.method not in ('GET', 'HEAD') or request.path.startswith('/api/'):
        return False
    mode = request.headers.get('Sec-Fetch-Mode')
    if mode is not None:
        return mode == 'navigate'
    return 'text/html' in request.headers.get('Accept', '')


def _login_url(next_path: str) -> str:
    if next_path and next_path != '/':
        return url_for('ledmatrix_auth.login', next=next_path)
    return url_for('ledmatrix_auth.login')


def _refuse(error_code: str, message: str):
    """401 in the form the caller can act on.

    A page load is redirected to the login page. An HTMX request gets
    ``HX-Redirect``, which htmx follows whatever the status. Anything else
    (fetch, scripts, EventSource) gets JSON, with the login URL in
    ``X-LEDMatrix-Login`` for the interface's own fetch() wrapper.
    """
    login_url = _login_url(_return_path())
    if _is_page_navigation() and request.headers.get('HX-Request') != 'true':
        return redirect(login_url)
    response = jsonify({'status': 'error', 'error_code': error_code, 'message': message})
    response.status_code = 401
    response.headers['WWW-Authenticate'] = 'Bearer realm="LEDMatrix"'
    response.headers['X-LEDMatrix-Login'] = login_url
    if request.headers.get('HX-Request') == 'true':
        response.headers['HX-Redirect'] = login_url
    return response


class _AuthSessionInterface(SecureCookieSessionInterface):
    """Signs the session cookie with the stored key once login is on.

    ``app.secret_key`` is random per process, which would sign everybody out
    on every restart. The stored key lives next to the password and is
    replaced whenever the password is, which is what signs every other
    browser out after a password change.
    """

    def __init__(self, store: AuthStore):
        self._store = store

    def get_signing_serializer(self, app):
        secret = self._store.session_secret()
        if not secret:
            return super().get_signing_serializer(app)
        return URLSafeTimedSerializer(
            secret,
            salt=self.salt,
            serializer=self.serializer,
            signer_kwargs={
                'key_derivation': self.key_derivation,
                'digest_method': self.digest_method,
            },
        )


# -- login / logout pages ----------------------------------------------------

auth_pages = Blueprint('ledmatrix_auth', __name__)


@auth_pages.route('/login', methods=['GET', 'POST'])
def login():
    store = get_store()
    next_path = safe_next(request.values.get('next', '/'))
    if store is None or not store.is_enabled():
        return redirect(next_path)
    if request.method == 'GET':
        if session.get(SESSION_KEY):
            return redirect(next_path)
        return render_template('v3/login.html', error=None, next_path=next_path)

    if store.check_password(request.form.get('password', '')):
        sign_in_this_session()
        logger.info("Web login from %s", request.remote_addr)
        return redirect(next_path)
    logger.warning("Failed web login from %s", request.remote_addr)
    # 401 is what the rate limit counts (see _counts_as_a_failure).
    return render_template('v3/login.html', next_path=next_path,
                           error='That password is not right. Try again.'), 401


@auth_pages.route('/logout', methods=['POST'])
def logout():
    session.clear()
    store = get_store()
    if store is not None and store.is_enabled():
        return redirect(url_for('ledmatrix_auth.login'))
    return redirect('/')


@auth_pages.errorhandler(429)
def _login_rate_limited(_error):
    """The login page's own answer to too many wrong passwords.

    Scoped to this blueprint, so the API keeps its JSON 429s.
    """
    return render_template(
        'v3/login.html',
        next_path=safe_next(request.values.get('next', '/')),
        error='Too many wrong passwords. Wait a minute and try again.'), 429


def _counts_as_a_failure(response) -> bool:
    """Only wrong passwords use up the login rate limit.

    401 from the login form; 403 from the settings routes that ask for the
    current password.
    """
    return response.status_code in (401, 403)


def init_app(app: Flask, config_manager, *, limiter=None,
             is_ap_mode_active: Optional[Callable[[], bool]] = None,
             store: Optional[AuthStore] = None) -> AuthStore:
    """Register the login pages, the session signer and the access hook.

    Register it after the captive-portal redirect, so in AP mode an unknown
    path still goes to /setup rather than to the login page.
    """
    store = store or AuthStore(config_manager)
    app.extensions[EXTENSION_KEY] = store
    app.session_interface = _AuthSessionInterface(store)
    app.config['PERMANENT_SESSION_LIFETIME'] = SESSION_LIFETIME
    app.config.setdefault('SESSION_COOKIE_SAMESITE', 'Lax')
    app.config.setdefault('SESSION_COOKIE_HTTPONLY', True)
    app.register_blueprint(auth_pages)
    ap_mode_active = is_ap_mode_active or (lambda: False)

    if limiter is not None:
        # flask-limiter enforces a decorated limit inside the wrapper it
        # returns, not in its before_request hook, so the wrapper has to be
        # what the URL map calls: decorating an already-registered function
        # and discarding the result limits nothing.
        limits = {'ledmatrix_auth.login': {'methods': ['POST']},
                  'api_v3.set_web_password': {},
                  'api_v3.disable_web_login': {}}
        for endpoint, options in limits.items():
            view = app.view_functions.get(endpoint)
            if view is not None:
                app.view_functions[endpoint] = limiter.limit(
                    LOGIN_RATE_LIMIT, deduct_when=_counts_as_a_failure, **options)(view)
    else:
        logger.warning("flask-limiter is not installed: failed web logins are "
                       "not rate-limited. Install web_interface/requirements.txt.")

    @app.before_request
    def _require_login():
        if not store.is_enabled():
            return None           # login off: nothing changes
        if request.method == 'OPTIONS':
            return None
        endpoint = request.endpoint or ''
        if endpoint in _ALWAYS_OPEN_ENDPOINTS:
            return None
        if session.get(SESSION_KEY):
            g.ledmatrix_auth_via = 'session'
            return None
        token = bearer_token()
        if token is not None:
            if store.verify_token(token):
                g.ledmatrix_auth_via = 'token'
                return None
            return _refuse('INVALID_TOKEN', 'That API token is not valid. It may '
                           'have been revoked; create a new one in the web '
                           'interface under General > Security.')
        if is_local_request():
            g.ledmatrix_auth_via = 'localhost'
            return None
        if endpoint in _HEALTH_ENDPOINTS:
            g.ledmatrix_auth_via = 'unauthenticated'
            return None
        if endpoint in _AP_SETUP_ENDPOINTS and ap_mode_active():
            g.ledmatrix_auth_via = 'ap-setup'
            return None
        return _refuse('AUTH_REQUIRED', 'Log in to the LEDMatrix interface, or '
                       'send an API token as "Authorization: Bearer <token>".')

    @app.context_processor
    def _auth_template_state():
        enabled = store.is_enabled()
        return {'web_auth_state': {
            'enabled': enabled,
            'signed_in': bool(enabled and session.get(SESSION_KEY)),
        }}

    return store
