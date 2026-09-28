"""/api/v3 JSON GETs must not be served from the browser cache.

They carried ``Cache-Control: private, max-age=5``, so a fetch() made right
after an install, a toggle or a Wi-Fi connect could be answered with the copy
from before it. Non-JSON files served through the API keep the short cache.
"""

from flask import Flask, Response, jsonify

import pytest


@pytest.fixture
def client():
    import web_interface.app as web_app
    # The real after_request hook on a throwaway app, so the header logic is
    # tested without depending on any real endpoint's managers.
    app = Flask(__name__)
    app.after_request(web_app.add_security_headers)
    app.add_url_rule('/api/v3/probe/json', 'probe_json',
                     lambda: jsonify({'status': 'success'}))
    app.add_url_rule('/api/v3/probe/file', 'probe_file',
                     lambda: Response('body{}', mimetype='text/css'))
    app.add_url_rule('/static/v3/probe.css', 'probe_static',
                     lambda: Response('body{}', mimetype='text/css'))
    with app.test_client() as c:
        yield c


def test_json_gets_are_not_stored(client):
    resp = client.get('/api/v3/probe/json')
    assert resp.headers['Cache-Control'] == 'no-store'


def test_non_json_files_keep_the_short_cache(client):
    resp = client.get('/api/v3/probe/file')
    assert resp.headers['Cache-Control'] == 'private, max-age=5, must-revalidate'


def test_static_assets_are_still_long_cached(client):
    resp = client.get('/static/v3/probe.css')
    assert 'immutable' in resp.headers.get('Cache-Control', '')


def test_the_real_app_marks_an_api_json_answer_no_store():
    import web_interface.app as web_app
    web_app.app.config['TESTING'] = True
    with web_app.app.test_client() as c:
        resp = c.get('/api/v3/no-such-endpoint-for-cache-test')
    assert resp.mimetype == 'application/json'
    assert resp.headers['Cache-Control'] == 'no-store'
