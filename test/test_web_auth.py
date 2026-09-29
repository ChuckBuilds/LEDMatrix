"""The optional web login: off by default, and complete when it is on.

web_interface/auth.py adds a password (a login session) and API tokens
(``Authorization: Bearer``) on top of the always-on Origin guard. With no
password stored nothing may change for anyone. With one, every page and API
route needs a session or a token, except requests from the Pi itself, the
Wi-Fi setup flow in access-point mode, static files and a minimal health
answer. The password hash, token hashes and cookie key must never leave the
process through any API.

Flask's test client reports REMOTE_ADDR 127.0.0.1, which the login exempts,
so every "someone on the LAN" client here sets a LAN address explicitly.
"""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from flask import Blueprint, Flask, jsonify

from src.config_manager import ConfigManager
from web_interface import auth as web_auth
from web_interface import origin_guard
from test._api_v3_test_helpers import api_v3_module  # noqa: F401 - fixture

REPO = Path(__file__).resolve().parent.parent
LAN = '192.168.1.50'
PASSWORD = 'correct horse battery'


def _flask_limiter():
    try:
        from flask_limiter import Limiter
        from flask_limiter.util import get_remote_address
    except ImportError:
        return None
    return Limiter, get_remote_address


@pytest.fixture
def config_manager(tmp_path):
    (tmp_path / 'config.json').write_text(json.dumps({'display': {'hardware': {'brightness': 50}}}))
    (tmp_path / 'secrets.json').write_text(json.dumps({'github': {'api_token': 'ghp_realtoken'}}))
    return ConfigManager(config_path=str(tmp_path / 'config.json'),
                         secrets_path=str(tmp_path / 'secrets.json'))


def build(config_manager, api_v3_module, ap_mode=False, limiter=True):
    """An app shaped like the real one: api_v3, a page, the setup page, the
    Origin guard and the login hook, in the real app's order."""
    app = Flask(__name__,
                template_folder=str(REPO / 'web_interface' / 'templates'),
                static_folder=str(REPO / 'web_interface' / 'static'))
    app.config['TESTING'] = True
    app.secret_key = 'per-process-random-in-the-real-app'
    api_v3_module.api_v3.config_manager = config_manager
    lim = None
    if limiter and _flask_limiter():
        Limiter, get_remote_address = _flask_limiter()
        lim = Limiter(app=app, key_func=get_remote_address, storage_uri='memory://')
    app.register_blueprint(api_v3_module.api_v3, url_prefix='/api/v3')
    # Stand-ins for routes whose real bodies need hardware or nmcli.
    app.view_functions['api_v3.get_wifi_status'] = lambda: jsonify({'status': 'success'})
    app.view_functions['api_v3.scan_wifi_networks'] = lambda: jsonify({'status': 'success'})

    pages = Blueprint('pages_v3', __name__)

    @pages.route('/')
    def index():
        return 'the interface'

    @pages.route('/partials/<name>')
    def load_partial(name):
        return 'a partial'

    @pages.route('/setup')
    def captive_setup():
        return 'wifi setup'

    app.register_blueprint(pages)
    origin_guard.init_app(app)
    ap = {'active': ap_mode}
    web_auth.init_app(app, config_manager, limiter=lim,
                      is_ap_mode_active=lambda: ap['active'])
    app.ap = ap
    app.limiter = lim
    return app


def lan_client(app, address=LAN):
    client = app.test_client()
    client.environ_base['REMOTE_ADDR'] = address
    return client


def secrets_on_disk(config_manager):
    with open(config_manager.get_secrets_path()) as fh:
        return json.load(fh)


def enable(app, password=PASSWORD):
    """Turn login on the way the Security section does, from the LAN."""
    client = lan_client(app)
    r = client.post('/api/v3/auth/password', json={'new_password': password})
    assert r.status_code == 200, r.get_json()
    return client


# --- Off by default -----------------------------------------------------------

class TestOffByDefault:
    def test_nothing_is_stored_and_nothing_is_asked(self, config_manager, api_v3_module):
        app = build(config_manager, api_v3_module)
        assert web_auth.get_store(app).is_enabled() is False
        c = lan_client(app)
        page = c.get('/', headers={'Accept': 'text/html'})
        assert page.status_code == 200 and page.get_data(as_text=True) == 'the interface'
        assert c.get('/partials/general', headers={'HX-Request': 'true'}).status_code == 200
        api = c.get('/api/v3/auth/status')
        assert api.status_code == 200
        assert api.get_json()['data']['enabled'] is False
        # No login cookie is handed out when there is no login.
        assert 'Set-Cookie' not in page.headers
        assert 'web_auth' not in secrets_on_disk(config_manager)

    def test_the_login_page_just_goes_home(self, config_manager, api_v3_module):
        c = lan_client(build(config_manager, api_v3_module))
        r = c.get('/login')
        assert r.status_code == 302 and r.headers['Location'] == '/'

    def test_the_health_answer_is_the_full_one(self, config_manager, api_v3_module):
        c = lan_client(build(config_manager, api_v3_module))
        data = c.get('/api/v3/health').get_json()['data']
        assert 'services' in data and 'checks' in data

    def test_the_real_app_registers_it(self):
        from web_interface.app import app as real_app
        assert isinstance(web_auth.get_store(real_app), web_auth.AuthStore)
        assert 'ledmatrix_auth.login' in real_app.view_functions
        hooks = [f.__name__ for f in real_app.before_request_funcs[None]]
        assert '_require_login' in hooks
        # After the captive-portal redirect, so AP mode still lands on /setup.
        assert hooks.index('_require_login') > hooks.index('captive_portal_redirect')


# --- Turned on ------------------------------------------------------------------

class TestTurnedOn:
    def test_setting_a_password_turns_it_on_and_keeps_that_browser_in(
            self, config_manager, api_v3_module):
        app = build(config_manager, api_v3_module)
        setter = enable(app)
        assert web_auth.get_store(app).is_enabled()
        assert setter.get('/', headers={'Accept': 'text/html'}).status_code == 200
        stored = secrets_on_disk(config_manager)
        assert stored['github'] == {'api_token': 'ghp_realtoken'}   # untouched
        assert stored['web_auth']['password_hash'].startswith(('scrypt:', 'pbkdf2:'))
        assert PASSWORD not in json.dumps(stored)

    def test_a_page_load_goes_to_the_login_page(self, config_manager, api_v3_module):
        app = build(config_manager, api_v3_module)
        enable(app)
        r = lan_client(app).get('/partials/general?x=1',
                                headers={'Accept': 'text/html', 'Sec-Fetch-Mode': 'navigate'})
        assert r.status_code == 302
        assert r.headers['Location'] == '/login?next=/partials/general?x%3D1'

    def test_an_htmx_request_gets_hx_redirect(self, config_manager, api_v3_module):
        app = build(config_manager, api_v3_module)
        enable(app)
        r = lan_client(app).get('/partials/general', headers={
            'HX-Request': 'true', 'HX-Current-URL': 'http://ledpi.local:5000/?tab=general'})
        assert r.status_code == 401
        assert r.headers['HX-Redirect'] == '/login?next=/?tab%3Dgeneral'

    def test_an_api_request_gets_401_json(self, config_manager, api_v3_module):
        app = build(config_manager, api_v3_module)
        enable(app)
        r = lan_client(app).get('/api/v3/auth/tokens')
        assert r.status_code == 401
        assert r.get_json()['error_code'] == 'AUTH_REQUIRED'
        assert r.headers['WWW-Authenticate'].startswith('Bearer')
        assert r.headers['X-LEDMatrix-Login'] == '/login'

    def test_a_post_without_login_is_refused_not_run(self, config_manager, api_v3_module):
        app = build(config_manager, api_v3_module)
        enable(app)
        r = lan_client(app).post('/api/v3/auth/tokens', json={'name': 'sneaky'})
        assert r.status_code == 401
        assert 'tokens' not in secrets_on_disk(config_manager)['web_auth']

    def test_unknown_paths_do_not_leak_a_404_first(self, config_manager, api_v3_module):
        app = build(config_manager, api_v3_module)
        enable(app)
        assert lan_client(app).get('/api/v3/no-such-thing').status_code == 401


#: next= values that must never send the browser anywhere but '/'.
OFFSITE_NEXT_TARGETS = [
    '//evil.example/x',
    '/\\evil.example',
    '/\\/evil.example',
    '\\\\evil.example',
    'http://evil.example/',
    'https:evil.example',
    'HTTP://evil.example',
    'javascript:alert(1)',
    'JaVaScRiPt:alert(1)',
    '/logout',
    '/login?next=//evil.example',
    # Percent-encoded: a later decode must not turn them into the above.
    '/%2F%2Fevil.example',
    '/%2fevil.example',
    '/%5Cevil.example',
    '/%5cevil.example',
    '%2F%2Fevil.example',
    '%252F%252Fevil.example',
    # Browsers drop tabs and newlines inside URLs, so '/\t/' would be '//'.
    '/\t/evil.example',
    '/\n/evil.example',
    '/%09/evil.example',
    '/%0D%0A/evil.example',
    '/\x7f/evil.example',
    ' //evil.example',
]


class TestLogin:
    def test_the_right_password_logs_in_and_returns_to_next(self, config_manager, api_v3_module):
        app = build(config_manager, api_v3_module)
        enable(app)
        c = lan_client(app)
        page = c.get('/login?next=/partials/plugins')
        assert page.status_code == 200
        assert 'name="password"' in page.get_data(as_text=True)
        r = c.post('/login', data={'password': PASSWORD, 'next': '/partials/plugins'})
        assert r.status_code == 302 and r.headers['Location'] == '/partials/plugins'
        assert c.get('/api/v3/auth/tokens').status_code == 200
        assert c.get('/api/v3/auth/status').get_json()['data']['signed_in'] is True

    def test_a_wrong_password_is_refused(self, config_manager, api_v3_module):
        app = build(config_manager, api_v3_module)
        enable(app)
        c = lan_client(app)
        r = c.post('/login', data={'password': 'not it'})
        assert r.status_code == 401
        assert 'not right' in r.get_data(as_text=True)
        assert c.get('/api/v3/auth/tokens').status_code == 401

    @pytest.mark.parametrize('target', OFFSITE_NEXT_TARGETS)
    def test_next_never_leaves_the_interface(self, config_manager, api_v3_module, target):
        app = build(config_manager, api_v3_module)
        enable(app)
        r = lan_client(app).post('/login', data={'password': PASSWORD, 'next': target})
        assert r.status_code == 302 and r.headers['Location'] == '/'

    @pytest.mark.parametrize('target', OFFSITE_NEXT_TARGETS)
    def test_a_get_with_an_offsite_next_redirects_home(self, config_manager, api_v3_module,
                                                       target):
        # Already signed in: GET /login redirects straight to next.
        app = build(config_manager, api_v3_module)
        c = enable(app)
        c.post('/login', data={'password': PASSWORD})
        r = c.get('/login', query_string={'next': target})
        assert r.status_code == 302 and r.headers['Location'] == '/'


@pytest.mark.parametrize('target', OFFSITE_NEXT_TARGETS)
def test_safe_next_refuses_anything_off_this_server(target):
    assert web_auth.safe_next(target) == '/'


@pytest.mark.parametrize('target', [
    '/', '/partials/plugins', '/?tab=general', '/v3?tab=plugins&x=1',
    '/partials/general?x%3D1', '/a%20b',
])
def test_safe_next_keeps_a_local_path(target):
    assert web_auth.safe_next(target) == target


@pytest.mark.parametrize('target', [None, 42, '', 'partials/plugins'])
def test_safe_next_refuses_a_non_path(target):
    assert web_auth.safe_next(target) == '/'

    def test_logout_ends_the_session(self, config_manager, api_v3_module):
        app = build(config_manager, api_v3_module)
        c = enable(app)
        r = c.post('/logout')
        assert r.status_code == 302 and r.headers['Location'] == '/login'
        assert c.get('/api/v3/auth/tokens').status_code == 401

    def test_the_session_survives_a_restart(self, config_manager, api_v3_module):
        c = enable(build(config_manager, api_v3_module))
        cookie = c.get_cookie('session')
        assert cookie is not None
        # A new process: a new random app.secret_key, the same stored key.
        restarted = lan_client(build(config_manager, api_v3_module))
        restarted.set_cookie('session', cookie.value)
        assert restarted.get('/api/v3/auth/tokens').status_code == 200

    def test_changing_the_password_signs_other_browsers_out(self, config_manager, api_v3_module):
        app = build(config_manager, api_v3_module)
        changer = enable(app)
        other = lan_client(app)
        other.post('/login', data={'password': PASSWORD})
        assert other.get('/api/v3/auth/tokens').status_code == 200
        r = changer.post('/api/v3/auth/password', json={
            'current_password': PASSWORD, 'new_password': 'a brand new one'})
        assert r.status_code == 200
        assert changer.get('/api/v3/auth/tokens').status_code == 200
        assert other.get('/api/v3/auth/tokens').status_code == 401

    def test_changing_or_disabling_needs_the_current_password(self, config_manager, api_v3_module):
        app = build(config_manager, api_v3_module)
        c = enable(app)
        r = c.post('/api/v3/auth/password', json={'current_password': 'wrong',
                                                  'new_password': 'another one'})
        assert r.status_code == 403 and r.get_json()['error_code'] == 'WRONG_PASSWORD'
        assert c.post('/api/v3/auth/disable', json={'current_password': 'wrong'}).status_code == 403
        assert web_auth.get_store(app).is_enabled()
        r = c.post('/api/v3/auth/disable', json={'current_password': PASSWORD})
        assert r.status_code == 200
        assert not web_auth.get_store(app).is_enabled()
        assert lan_client(app).get('/api/v3/auth/tokens').status_code == 200

    def test_a_short_password_is_refused(self, config_manager, api_v3_module):
        app = build(config_manager, api_v3_module)
        r = lan_client(app).post('/api/v3/auth/password', json={'new_password': 'short'})
        assert r.status_code == 400 and r.get_json()['error_code'] == 'WEAK_PASSWORD'
        assert not web_auth.get_store(app).is_enabled()


class TestRateLimit:
    @pytest.fixture(autouse=True)
    def _needs_limiter(self):
        if _flask_limiter() is None:
            pytest.skip('flask-limiter is not installed (web_interface/requirements.txt)')

    def test_wrong_passwords_are_rate_limited(self, config_manager, api_v3_module):
        app = build(config_manager, api_v3_module)
        enable(app)
        c = lan_client(app)
        for _ in range(5):
            assert c.post('/login', data={'password': 'guess'}).status_code == 401
        blocked = c.post('/login', data={'password': 'guess'})
        assert blocked.status_code == 429
        assert 'Too many' in blocked.get_data(as_text=True)
        # Even the right password waits: that is what makes guessing slow.
        assert c.post('/login', data={'password': PASSWORD}).status_code == 429
        # Per address: someone else on the LAN can still log in.
        assert lan_client(app, '192.168.1.51').post(
            '/login', data={'password': PASSWORD}).status_code == 302

    def test_successful_logins_do_not_count(self, config_manager, api_v3_module):
        app = build(config_manager, api_v3_module)
        enable(app)
        for _ in range(8):
            c = lan_client(app)
            assert c.post('/login', data={'password': PASSWORD}).status_code == 302

    def test_guessing_through_the_settings_routes_is_limited_too(
            self, config_manager, api_v3_module):
        app = build(config_manager, api_v3_module)
        c = enable(app)
        for _ in range(5):
            assert c.post('/api/v3/auth/disable',
                          json={'current_password': 'guess'}).status_code == 403
        assert c.post('/api/v3/auth/disable',
                      json={'current_password': 'guess'}).status_code == 429


# --- Tokens ---------------------------------------------------------------------

class TestTokens:
    def test_a_token_grants_api_access_until_revoked(self, config_manager, api_v3_module):
        app = build(config_manager, api_v3_module)
        admin = enable(app)
        created = admin.post('/api/v3/auth/tokens', json={'name': 'Home Assistant'})
        assert created.status_code == 201
        body = created.get_json()['data']
        token, record = body['token'], body['record']
        assert token.startswith('lmx_') and record['prefix'] == token[:8]
        assert 'hash' not in record

        integration = lan_client(app, '192.168.1.77')
        auth = {'Authorization': f'Bearer {token}'}
        assert integration.get('/api/v3/health', headers=auth).get_json()['data'].get('checks') is not None
        assert integration.get('/', headers=auth).status_code == 200

        listed = admin.get('/api/v3/auth/tokens').get_json()['data']['tokens']
        assert [t['name'] for t in listed] == ['Home Assistant']
        assert token not in json.dumps(listed)

        assert admin.delete(f"/api/v3/auth/tokens/{record['id']}").status_code == 200
        r = integration.get('/api/v3/auth/status', headers=auth)
        assert r.status_code == 401 and r.get_json()['error_code'] == 'INVALID_TOKEN'

    def test_only_a_hash_is_stored(self, config_manager, api_v3_module):
        app = build(config_manager, api_v3_module)
        admin = enable(app)
        token = admin.post('/api/v3/auth/tokens', json={'name': 'script'}).get_json()['data']['token']
        stored = json.dumps(secrets_on_disk(config_manager))
        assert token not in stored
        assert web_auth._token_digest(token) in stored

    def test_a_made_up_token_is_refused(self, config_manager, api_v3_module):
        app = build(config_manager, api_v3_module)
        enable(app)
        r = lan_client(app).get('/api/v3/auth/status',
                                headers={'Authorization': 'Bearer lmx_madeup'})
        assert r.status_code == 401 and r.get_json()['error_code'] == 'INVALID_TOKEN'

    def test_a_token_cannot_change_login_settings(self, config_manager, api_v3_module):
        app = build(config_manager, api_v3_module)
        admin = enable(app)
        token = admin.post('/api/v3/auth/tokens', json={'name': 'ha'}).get_json()['data']['token']
        auth = {'Authorization': f'Bearer {token}'}
        integration = lan_client(app, '192.168.1.77')
        assert integration.post('/api/v3/auth/tokens', json={'name': 'more'},
                                headers=auth).status_code == 403
        assert integration.post('/api/v3/auth/disable', json={'current_password': PASSWORD},
                                headers=auth).status_code == 403
        assert web_auth.get_store(app).is_enabled()

    def test_revoking_an_unknown_token_is_a_404(self, config_manager, api_v3_module):
        admin = enable(build(config_manager, api_v3_module))
        assert admin.delete('/api/v3/auth/tokens/nope').status_code == 404

    @pytest.mark.parametrize('name, expected', [
        ('', 'Give the token a name'),
        ('x' * (web_auth.MAX_TOKEN_NAME_LENGTH + 1), 'at most'),
    ])
    def test_a_bad_token_name_gets_the_fixed_message(self, config_manager, api_v3_module,
                                                     name, expected):
        admin = enable(build(config_manager, api_v3_module))
        r = admin.post('/api/v3/auth/tokens', json={'name': name})
        assert r.status_code == 400
        assert expected in r.get_json()['message']

    def test_the_log_never_holds_a_password_token_or_hash(
            self, config_manager, api_v3_module, caplog):
        app = build(config_manager, api_v3_module)
        with caplog.at_level('DEBUG'):
            admin = enable(app)
            admin.post('/api/v3/auth/password', json={
                'current_password': PASSWORD, 'new_password': 'another fine phrase'})
            created = admin.post('/api/v3/auth/tokens', json={'name': 'Home Assistant'})
            token = created.get_json()['data']['token']
            record = created.get_json()['data']['record']
            admin.delete(f"/api/v3/auth/tokens/{record['id']}")
        logged = caplog.text
        assert 'Home Assistant' in logged and record['id'] in logged
        for secret in (PASSWORD, 'another fine phrase', token,
                       web_auth._token_digest(token)):
            assert secret not in logged


# --- Exemptions -----------------------------------------------------------------

class TestExemptions:
    def test_the_wifi_setup_flow_is_open_in_ap_mode(self, config_manager, api_v3_module):
        app = build(config_manager, api_v3_module, ap_mode=True)
        enable(app)
        phone = lan_client(app, '192.168.4.20')
        assert phone.get('/setup').status_code == 200
        assert phone.get('/api/v3/wifi/status').status_code == 200
        assert phone.get('/api/v3/wifi/scan').status_code == 200
        # Only the setup flow: the rest of the interface still needs a login.
        assert phone.get('/api/v3/auth/tokens').status_code == 401
        assert phone.get('/', headers={'Accept': 'text/html'}).status_code == 302

    def test_the_setup_flow_is_not_open_outside_ap_mode(self, config_manager, api_v3_module):
        app = build(config_manager, api_v3_module, ap_mode=False)
        enable(app)
        assert lan_client(app).get('/api/v3/wifi/status').status_code == 401

    def test_requests_from_the_pi_itself_are_open(self, config_manager, api_v3_module):
        app = build(config_manager, api_v3_module)
        enable(app)
        for address in ('127.0.0.1', '::1'):
            local = lan_client(app, address)
            assert local.get('/api/v3/auth/tokens').status_code == 200
            assert local.get('/', headers={'Accept': 'text/html'}).status_code == 200

    def test_a_proxy_on_the_pi_does_not_make_everyone_local(self, config_manager, api_v3_module):
        app = build(config_manager, api_v3_module)
        enable(app)
        proxied = lan_client(app, '127.0.0.1')
        r = proxied.get('/api/v3/auth/tokens', headers={'X-Forwarded-For': '203.0.113.9'})
        assert r.status_code == 401

    def test_static_files_are_open(self, config_manager, api_v3_module):
        app = build(config_manager, api_v3_module)
        enable(app)
        assert lan_client(app).get('/static/v3/app.css').status_code == 200

    def test_health_is_open_but_minimal(self, config_manager, api_v3_module):
        app = build(config_manager, api_v3_module)
        admin = enable(app)
        r = lan_client(app).get('/api/v3/health')
        assert r.status_code == 200
        assert set(r.get_json()['data']) == {'status'}
        assert 'checks' in admin.get('/api/v3/health').get_json()['data']


# --- The hash never leaves ------------------------------------------------------

class TestSecretsNeverLeave:
    @pytest.fixture
    def enabled(self, config_manager, api_v3_module):
        app = build(config_manager, api_v3_module)
        admin = enable(app)
        token = admin.post('/api/v3/auth/tokens', json={'name': 'ha'}).get_json()['data']['token']
        section = secrets_on_disk(config_manager)['web_auth']
        needles = [section['password_hash'], section['session_secret'],
                   section['tokens'][0]['hash'], token]
        return app, admin, needles

    def test_no_config_or_auth_endpoint_returns_them(self, enabled):
        app, admin, needles = enabled
        for url in ('/api/v3/config/main', '/api/v3/config/secrets',
                    '/api/v3/auth/status', '/api/v3/auth/tokens'):
            r = admin.get(url)
            assert r.status_code == 200, url
            text = r.get_data(as_text=True)
            for needle in needles:
                assert needle not in text, url
            assert 'web_auth' not in text, url
        # The other secrets are still there (masked), so the strip was targeted.
        assert 'github' in admin.get('/api/v3/config/secrets').get_json()['data']

    def test_the_raw_json_editor_does_not_show_them(self, enabled, config_manager, monkeypatch):
        app, _, needles = enabled
        from web_interface.blueprints import pages_v3 as pages_module
        monkeypatch.setattr(pages_module.pages_v3, 'config_manager', config_manager, raising=False)
        with app.test_request_context('/partials/raw-json'):
            html = pages_module._load_raw_json_partial()
        for needle in needles:
            assert needle not in html
        assert 'web_auth' not in html

    def test_the_raw_secrets_save_cannot_overwrite_the_login(self, enabled, config_manager):
        app, admin, _ = enabled
        before = secrets_on_disk(config_manager)['web_auth']
        r = admin.post('/api/v3/config/raw/secrets', json={
            'github': {'api_token': 'ghp_new'},
            'web_auth': {'password_hash': 'plaintext-that-matches-nothing'}})
        assert r.status_code == 200
        after = secrets_on_disk(config_manager)
        assert after['web_auth'] == before
        assert after['github']['api_token'] == 'ghp_new'

    def test_a_main_config_save_cannot_plant_one(self, config_manager, api_v3_module):
        app = build(config_manager, api_v3_module)
        r = lan_client(app).post('/api/v3/config/main', json={
            'web_auth': {'password_hash': 'x'}, 'timezone': 'America/Chicago'})
        assert r.status_code == 200, r.get_json()
        with open(config_manager.get_config_path()) as fh:
            assert 'web_auth' not in json.load(fh)
        assert not web_auth.get_store(app).is_enabled()

    def test_orphan_cleanup_keeps_the_section(self, enabled, config_manager):
        config_manager.cleanup_orphaned_plugin_configs([])
        assert 'password_hash' in secrets_on_disk(config_manager)['web_auth']


# --- Recovery -------------------------------------------------------------------

class TestRecovery:
    def _script(self):
        sys.path.insert(0, str(REPO / 'scripts'))
        try:
            import reset_web_password
        finally:
            sys.path.remove(str(REPO / 'scripts'))
        return reset_web_password

    def test_the_reset_script_turns_login_off_and_keeps_the_rest(
            self, config_manager, api_v3_module):
        app = build(config_manager, api_v3_module)
        admin = enable(app)
        admin.post('/api/v3/auth/tokens', json={'name': 'ha'})
        message = self._script().reset(Path(config_manager.get_secrets_path()))
        assert 'off' in message
        stored = secrets_on_disk(config_manager)
        assert 'password_hash' not in stored['web_auth']
        assert 'session_secret' not in stored['web_auth']
        assert len(stored['web_auth']['tokens']) == 1
        assert stored['github'] == {'api_token': 'ghp_realtoken'}
        # The running app sees it without a restart.
        assert not web_auth.get_store(app).is_enabled()
        assert lan_client(app).get('/api/v3/auth/tokens').status_code == 200

    def test_revoke_tokens_removes_the_section(self, config_manager, api_v3_module):
        app = build(config_manager, api_v3_module)
        admin = enable(app)
        admin.post('/api/v3/auth/tokens', json={'name': 'ha'})
        self._script().reset(Path(config_manager.get_secrets_path()), revoke_tokens=True)
        assert 'web_auth' not in secrets_on_disk(config_manager)

    def test_the_script_prints_nothing_from_the_file(self, config_manager, api_v3_module,
                                                    capsys):
        app = build(config_manager, api_v3_module)
        admin = enable(app)
        token = admin.post('/api/v3/auth/tokens', json={'name': 'ha'}).get_json()['data']['token']
        secrets_before = json.dumps(secrets_on_disk(config_manager))
        assert self._script().main(['--secrets', config_manager.get_secrets_path()]) == 0
        out = capsys.readouterr()
        printed = out.out + out.err
        assert 'off' in printed
        section = json.loads(secrets_before)['web_auth']
        for secret in (PASSWORD, token, section['password_hash'], section['session_secret'],
                       'ghp_realtoken'):
            assert secret not in printed

    def test_nothing_to_do_writes_nothing(self, config_manager):
        path = Path(config_manager.get_secrets_path())
        before = path.stat().st_mtime_ns
        assert 'Nothing to do' in self._script().reset(path)
        assert path.stat().st_mtime_ns == before


# --- The MQTT bridge ------------------------------------------------------------

def test_the_mqtt_bridge_sends_its_token():
    bridge_path = REPO / 'integrations' / 'mqtt_bridge' / 'ledmatrix_mqtt_bridge.py'
    import importlib.util
    spec = importlib.util.spec_from_file_location('ledmatrix_mqtt_bridge_auth_test', bridge_path)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except ImportError as e:
        pytest.skip(f'bridge dependencies are not installed: {e}')
    assert 'ledmatrix_api_token' in module.DEFAULTS
    with_token = module.LEDMatrixClient('http://pi:5000', session=MagicMock(headers={}),
                                        api_token='lmx_abc')
    assert with_token.session.headers['Authorization'] == 'Bearer lmx_abc'
    without = module.LEDMatrixClient('http://pi:5000', session=MagicMock(headers={}))
    assert 'Authorization' not in without.session.headers


def test_the_bridge_settings_keep_the_token_write_only(tmp_path, monkeypatch, api_v3_module):
    import web_interface.blueprints.api_v3.misc as misc
    cfg = tmp_path / 'bridge_config.json'
    for mod in (misc, api_v3_module):
        monkeypatch.setattr(mod, '_MQTT_BRIDGE_CONFIG', cfg, raising=False)
        monkeypatch.setattr(mod, '_MQTT_BRIDGE_DIR', tmp_path, raising=False)
    app = Flask(__name__)
    app.register_blueprint(api_v3_module.api_v3, url_prefix='/api/v3')
    c = app.test_client()
    url = '/api/v3/integrations/mqtt-bridge'
    assert c.put(url + '/config', json={'ledmatrix_api_token': 'lmx_secret'}).status_code == 200
    got = c.get(url).get_json()['data']
    assert got['api_token_set'] is True
    assert 'lmx_secret' not in json.dumps(got)
    # Saving other fields leaves it alone; clear_api_token removes it.
    c.put(url + '/config', json={'mqtt_topic': 'x/y'})
    assert json.loads(cfg.read_text())['ledmatrix_api_token'] == 'lmx_secret'
    c.put(url + '/config', json={'clear_api_token': True})
    assert json.loads(cfg.read_text())['ledmatrix_api_token'] is None
