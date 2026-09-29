"""Optional web login: password and API-token management.

The login itself, the access hook and the storage live in
web_interface/auth.py; these routes are the settings the General tab's
Security section drives. None of them ever returns the password hash, a token
hash, or the cookie-signing key.

Routes decorate the shared `api_v3` Blueprint from the package `__init__`,
so their endpoint names are `api_v3.<function>` like every other route.
"""
from web_interface import auth as web_auth
from web_interface.blueprints.api_v3 import (
    api_v3, describe_exception, jsonify, logger, request,
)


def _store_or_error():
    store = web_auth.get_store()
    if store is None:
        return None, (jsonify({'status': 'error', 'error_code': 'AUTH_UNAVAILABLE',
                               'message': 'Login settings are not available in this process.'}), 503)
    if web_auth.auth_via() == 'token':
        # An integration's token is for driving the display, not for
        # changing who can log in or minting more tokens.
        return None, (jsonify({'status': 'error', 'error_code': 'TOKEN_NOT_ALLOWED',
                               'message': 'API tokens cannot change login settings. '
                                          'Log in to the web interface instead.'}), 403)
    return store, None


def _json_body():
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else None


def _status_payload(store):
    return {
        'enabled': store.is_enabled(),
        'signed_in': bool(store.is_enabled() and web_auth.auth_via() == 'session'),
        'access': web_auth.auth_via(),
        'min_password_length': web_auth.MIN_PASSWORD_LENGTH,
        'tokens': store.list_tokens(),
    }


def _save_failed(e, what):
    logger.error("Could not save web login settings (%s)", what, exc_info=True)
    return jsonify({'status': 'error', 'error_code': 'AUTH_SAVE_FAILED',
                    'message': f'Could not save the {what}; see logs for details',
                    'details': describe_exception(e)}), 500


@api_v3.route('/auth/status', methods=['GET'])
def get_web_auth_status():
    """Whether login is on, how this request got in, and the token list."""
    store, error = _store_or_error()
    if error:
        return error
    return jsonify({'status': 'success', 'data': _status_payload(store)})


@api_v3.route('/auth/password', methods=['POST'])
def set_web_password():
    """Set the password (turns login on) or change it.

    Body: ``{"new_password": "...", "current_password": "..."}``; the current
    one is required once login is on. Signs every other browser out, and
    keeps this one signed in.
    """
    store, error = _store_or_error()
    if error:
        return error
    data = _json_body()
    if data is None:
        return jsonify({'status': 'error', 'message': 'Body must be a JSON object'}), 400
    if store.is_enabled() and not store.check_password(data.get('current_password')):
        # 403, not 401: the caller is signed in; the password is what's wrong.
        # This is what the rate limit counts.
        return jsonify({'status': 'error', 'error_code': 'WRONG_PASSWORD',
                        'message': 'The current password is not right.'}), 403
    new_password = data.get('new_password')
    problem = web_auth.password_problem(new_password)
    if problem:
        return jsonify({'status': 'error', 'error_code': 'WEAK_PASSWORD',
                        'message': problem}), 400
    was_enabled = store.is_enabled()
    try:
        store.set_password(new_password)
    except web_auth.AuthError as e:
        return jsonify({'status': 'error', 'message': e.user_message}), 400
    except Exception as e:
        return _save_failed(e, 'password')
    web_auth.sign_in_this_session()
    # Names the event only; the new value is never logged.
    logger.info("Web login %s from %s",
                'password changed' if was_enabled else 'turned on (password set)', request.remote_addr)
    return jsonify({'status': 'success',
                    'message': 'Password changed.' if was_enabled else
                               'Login is on. Other browsers now need the password.',
                    'data': _status_payload(store)})


@api_v3.route('/auth/disable', methods=['POST'])
def disable_web_login():
    """Turn login off. Body: ``{"current_password": "..."}``. Tokens are kept."""
    store, error = _store_or_error()
    if error:
        return error
    if not store.is_enabled():
        return jsonify({'status': 'success', 'message': 'Login is already off.',
                        'data': _status_payload(store)})
    data = _json_body() or {}
    if not store.check_password(data.get('current_password')):
        return jsonify({'status': 'error', 'error_code': 'WRONG_PASSWORD',
                        'message': 'The current password is not right.'}), 403
    try:
        store.disable()
    except Exception as e:
        return _save_failed(e, 'login setting')
    logger.info("Web login turned off from %s", request.remote_addr)
    return jsonify({'status': 'success',
                    'message': 'Login is off. Anyone on your network can open the interface.',
                    'data': _status_payload(store)})


@api_v3.route('/auth/tokens', methods=['GET'])
def list_api_tokens():
    """Token names, ids, first characters and creation times. Never the token."""
    store, error = _store_or_error()
    if error:
        return error
    return jsonify({'status': 'success', 'data': {'tokens': store.list_tokens()}})


@api_v3.route('/auth/tokens', methods=['POST'])
def create_api_token():
    """Create a token. Body: ``{"name": "Home Assistant"}``.

    The answer's ``data.token`` is the only time the token is ever shown.
    """
    store, error = _store_or_error()
    if error:
        return error
    data = _json_body()
    if data is None:
        return jsonify({'status': 'error', 'message': 'Body must be a JSON object'}), 400
    try:
        record, token = store.create_token(str(data.get('name') or ''))
    except web_auth.AuthError as e:
        return jsonify({'status': 'error', 'message': e.user_message}), 400
    except Exception as e:
        return _save_failed(e, 'token')
    # The name and id only, never the token or its hash.
    logger.info("API access %r (id %s) created from %s", record['name'], record['id'],
                request.remote_addr)
    return jsonify({'status': 'success',
                    'message': 'Token created. Copy it now: it is not shown again.',
                    'data': {'token': token, 'record': record}}), 201


@api_v3.route('/auth/tokens/<token_id>', methods=['DELETE'])
def revoke_api_token(token_id):
    """Revoke a token by id. It stops working on the next request."""
    store, error = _store_or_error()
    if error:
        return error
    try:
        revoked = store.revoke_token(token_id)
    except Exception as e:
        return _save_failed(e, 'token list')
    if not revoked:
        return jsonify({'status': 'error', 'error_code': 'NOT_FOUND',
                        'message': 'No token with that id.'}), 404
    logger.info("API access id %s revoked from %s", token_id, request.remote_addr)
    return jsonify({'status': 'success', 'message': 'Token revoked.',
                    'data': {'tokens': store.list_tokens()}})
