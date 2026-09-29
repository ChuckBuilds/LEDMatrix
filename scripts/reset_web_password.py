#!/usr/bin/env python3
"""
Turn the web interface's optional login off, for when the password is lost.

Removes the password (and the key that signs login cookies) from the
``web_auth`` section of ``config/config_secrets.json``. The interface is then
open again, as it is before a password is ever set, and a new password can be
set under General > Security. API tokens are kept unless ``--revoke-tokens``
is given. Nothing else in the secrets file is touched, and the web service
does not need a restart: it notices the change on the next request.

Run it on the Pi, from any directory:

    sudo python3 ~/LEDMatrix/scripts/reset_web_password.py

``sudo`` because the secrets file is not readable by every user. The file
keeps its owner and permissions.

Another way in without the password: open the interface from the Pi itself
(http://localhost:5000). Requests from the Pi are never asked to log in.
"""
import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.config_manager_atomic import atomic_write_json  # noqa: E402

SECTION = 'web_auth'   # web_interface/auth.py; not imported to keep Flask out
LOGIN_KEYS = ('password_hash', 'session_secret', 'password_set_at')


def reset(settings_file: Path, revoke_tokens: bool = False) -> str:
    """Clear the login from ``settings_file`` (config_secrets.json).

    Returns what was done. The message names the file and counts tokens; it
    never includes anything read from the file.
    """
    if not settings_file.exists():
        return f'{settings_file} does not exist, so no password is set. Nothing to do.'
    with open(settings_file, 'r', encoding='utf-8') as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError(f'{settings_file} does not hold a JSON object')

    section = data.get(SECTION)
    if not isinstance(section, dict):
        return 'No web login password is set. Nothing to do.'

    had_password = bool(section.get('password_hash'))
    token_count = len(section.get('tokens') or [])
    for key in LOGIN_KEYS:
        section.pop(key, None)
    if revoke_tokens:
        section.pop('tokens', None)
    if section:
        data[SECTION] = section
    else:
        data.pop(SECTION, None)

    if not had_password and not (revoke_tokens and token_count):
        return 'No web login password is set. Nothing to do.'
    atomic_write_json(settings_file, data)

    done = []
    if had_password:
        done.append('Web login is off: the interface opens without a password. '
                    'Set a new one under General > Security.')
    if revoke_tokens and token_count:
        done.append(f'Revoked {token_count} API token(s).')
    elif token_count:
        done.append(f'{token_count} API token(s) kept (use --revoke-tokens to remove them).')
    return ' '.join(done)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description='Turn the LEDMatrix web login off (lost password recovery).')
    parser.add_argument('--secrets', dest='settings_file', type=Path,
                        default=PROJECT_ROOT / 'config' / 'config_secrets.json',
                        help='secrets file (default: config/config_secrets.json '
                             'in this LEDMatrix checkout)')
    parser.add_argument('--revoke-tokens', action='store_true',
                        help='also delete every API token')
    args = parser.parse_args(argv)
    settings_file = args.settings_file
    try:
        outcome = reset(settings_file, revoke_tokens=args.revoke_tokens)
    except PermissionError:
        print(f'Permission denied reading or writing {settings_file}. Run it with sudo.',
              file=sys.stderr)
        return 1
    except (OSError, ValueError) as err:
        print(f'Could not reset the web login: {err}', file=sys.stderr)
        return 1
    print(outcome)
    return 0


if __name__ == '__main__':
    sys.exit(main())
