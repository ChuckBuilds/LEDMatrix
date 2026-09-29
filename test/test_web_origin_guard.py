"""State-changing requests from another website's page are refused.

The interface has no login and was defended only by "it is on the LAN". But
any site a LAN user opens can make their browser POST to http://<pi>:5000: a
plain HTML form is a CORS "simple" request, so it arrives and runs even though
the attacking page never sees the answer. /api/v3/system/action accepted
form-encoded bodies and reboots, powers off and pulls code.

web_interface/origin_guard.py refuses POST/PUT/PATCH/DELETE whose Origin (or
Referer) is not this server's own host, and /system/action only takes a
form-encoded body from HTMX (a cross-site form cannot set HX-Request).
Requests with neither Origin nor Referer are not from a browser -- curl, Home
Assistant, the MQTT bridge -- and still pass.
"""

import subprocess
from unittest.mock import patch

import pytest
from flask import Flask, jsonify

from test._api_v3_test_helpers import (  # noqa: F401 - fixture
    api_v3_module, build_app,
)
from web_interface import origin_guard

# Flask's test client addresses requests to Host: localhost.
SELF = 'http://localhost'
EVIL = 'http://evil.example'


# --- The guard itself, on a throwaway app ---------------------------------

@pytest.fixture
def probe():
    app = Flask(__name__)
    app.config['TESTING'] = True
    origin_guard.init_app(app)

    @app.route('/change', methods=['GET', 'POST', 'PUT', 'PATCH', 'DELETE'])
    def change():
        return jsonify({'status': 'success'})

    return app.test_client()


@pytest.mark.parametrize('method', ['post', 'put', 'patch', 'delete'])
def test_a_cross_site_origin_is_refused_for_every_changing_method(probe, method):
    resp = getattr(probe, method)('/change', headers={'Origin': EVIL})
    assert resp.status_code == 403
    body = resp.get_json()
    assert body['status'] == 'error'
    assert body['error_code'] == 'CROSS_SITE_REQUEST'
    assert 'evil.example' in body['details']


@pytest.mark.parametrize('origin', [
    SELF,
    'http://LOCALHOST',          # host case is not significant
    'http://localhost:80',       # explicit default port
    'https://localhost:80',      # TLS proxy passing Host through: scheme ignored
])
def test_the_interfaces_own_origin_passes(probe, origin):
    assert probe.post('/change', headers={'Origin': origin}).status_code == 200


def test_the_host_the_browser_used_is_what_counts(probe):
    # Whatever name or address the user typed -- mDNS name, LAN IP, or the
    # access-point address the captive portal answers on.
    for host in ('ledpi.local:5000', '192.168.1.40:5000', '192.168.4.1',
                 '[fe80::1]:5000'):
        resp = probe.post('/change', headers={
            'Host': host, 'Origin': f'http://{host}'})
        assert resp.status_code == 200, host


def test_captive_portal_via_port_80_redirect_passes(probe):
    # iptables REDIRECT 80 -> 5000 keeps the Host the browser sent, which
    # carries no port; the page's Origin carries none either.
    resp = probe.post('/change', headers={
        'Host': '192.168.4.1', 'Origin': 'http://192.168.4.1'})
    assert resp.status_code == 200


def test_the_same_host_on_another_port_is_another_site(probe):
    resp = probe.post('/change', headers={
        'Host': 'ledpi.local:5000', 'Origin': 'http://ledpi.local:8080'})
    assert resp.status_code == 403


def test_no_origin_and_no_referer_passes(probe):
    # curl, Home Assistant, the MQTT bridge: not a browser.
    assert probe.post('/change').status_code == 200
    assert probe.post('/change', json={'action': 'x'}).status_code == 200


def test_a_null_origin_is_refused(probe):
    # Sandboxed iframes and file:// pages send "Origin: null".
    resp = probe.post('/change', headers={'Origin': 'null'})
    assert resp.status_code == 403
    assert 'null' in resp.get_json()['details']


def test_the_referer_is_checked_when_origin_is_absent(probe):
    assert probe.post('/change', headers={
        'Referer': EVIL + '/attack.html'}).status_code == 403
    assert probe.post('/change', headers={
        'Referer': SELF + '/v3'}).status_code == 200


def test_origin_wins_over_referer(probe):
    resp = probe.post('/change', headers={
        'Origin': EVIL, 'Referer': SELF + '/'})
    assert resp.status_code == 403


@pytest.mark.parametrize('value', [
    'not a url', 'ftp://localhost', 'http://', 'http://localhost:notaport',
])
def test_an_unreadable_origin_is_refused(probe, value):
    assert probe.post('/change', headers={'Origin': value}).status_code == 403


def test_gets_are_never_checked(probe):
    assert probe.get('/change', headers={'Origin': EVIL}).status_code == 200
    assert probe.get('/change', headers={'Origin': 'null'}).status_code == 200


# --- /api/v3/system/action -------------------------------------------------

@pytest.fixture
def api_client(api_v3_module):  # noqa: F811 - pytest fixture injection
    app = build_app(api_v3_module.api_v3)
    origin_guard.init_app(app)
    return app.test_client()


def _ok(args, **kwargs):
    return subprocess.CompletedProcess(args, 0, stdout='', stderr='')


def test_a_cross_site_form_post_never_reaches_the_reboot(api_client):
    with patch('subprocess.run', side_effect=_ok) as run:
        resp = api_client.post('/api/v3/system/action',
                               data={'action': 'reboot_system'},
                               headers={'Origin': EVIL})
    assert resp.status_code == 403
    run.assert_not_called()


def test_a_form_post_without_hx_request_is_refused_even_without_origin(api_client):
    # Belt and braces: a browser whose Origin/Referer never arrived (a
    # privacy proxy stripping both) still cannot send the form.
    with patch('subprocess.run', side_effect=_ok) as run:
        resp = api_client.post('/api/v3/system/action',
                               data={'action': 'reboot_system'})
    assert resp.status_code == 415
    assert 'JSON' in resp.get_json()['message']
    run.assert_not_called()


def test_a_text_plain_body_is_refused(api_client):
    # enctype="text/plain" is the other cross-site form encoding.
    with patch('subprocess.run', side_effect=_ok) as run:
        resp = api_client.post('/api/v3/system/action',
                               data='{"action": "reboot_system"}',
                               content_type='text/plain')
    assert resp.status_code == 415
    run.assert_not_called()


def test_an_htmx_form_post_from_the_interface_runs(api_client):
    with patch('subprocess.run', side_effect=_ok) as run:
        resp = api_client.post('/api/v3/system/action',
                               data={'action': 'stop_display'},
                               headers={'Origin': SELF, 'HX-Request': 'true'})
    assert resp.status_code == 200
    assert resp.get_json()['status'] == 'success'
    assert run.call_args[0][0] == ['sudo', 'systemctl', 'stop', 'ledmatrix.service']


def test_a_same_origin_json_post_runs(api_client):
    # What every button and fetch() in the interface sends.
    with patch('subprocess.run', side_effect=_ok):
        resp = api_client.post('/api/v3/system/action',
                               json={'action': 'stop_display'},
                               headers={'Origin': SELF})
    assert resp.status_code == 200
    assert resp.get_json()['status'] == 'success'


def test_a_json_post_with_no_origin_runs(api_client):
    # The MQTT bridge, Home Assistant, curl.
    with patch('subprocess.run', side_effect=_ok):
        resp = api_client.post('/api/v3/system/action',
                               json={'action': 'stop_display'})
    assert resp.status_code == 200


def test_an_empty_json_body_still_asks_for_an_action(api_client):
    resp = api_client.post('/api/v3/system/action', json={})
    assert resp.status_code == 400
    assert resp.get_json()['message'] == 'Action required'


def test_a_json_body_that_is_not_an_object_asks_for_an_action(api_client):
    resp = api_client.post('/api/v3/system/action', json=['reboot_system'])
    assert resp.status_code == 400


# --- The real app ----------------------------------------------------------

def test_the_real_app_has_the_guard():
    import web_interface.app as web_app
    web_app.app.config['TESTING'] = True
    with patch('subprocess.run', side_effect=_ok) as run, \
            web_app.app.test_client() as c:
        resp = c.post('/api/v3/system/action',
                      json={'action': 'reboot_system'},
                      headers={'Origin': EVIL})
    assert resp.status_code == 403
    assert resp.get_json()['error_code'] == 'CROSS_SITE_REQUEST'
    run.assert_not_called()


def test_the_real_app_leaves_gets_alone():
    import web_interface.app as web_app
    web_app.app.config['TESTING'] = True
    with web_app.app.test_client() as c:
        resp = c.get('/api/v3/no-such-endpoint-for-origin-test',
                     headers={'Origin': EVIL})
    assert resp.status_code == 404
