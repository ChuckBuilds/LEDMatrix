"""
Cross-site request guard for the web interface.

The threat: the interface has no login, and "it is only on the LAN" does not
keep other websites out of it. Any page a person on the LAN opens in their
browser can make that browser send a request to ``http://<pi>:5000``. A plain
HTML form POST (``application/x-www-form-urlencoded``, ``multipart/form-data``
or ``text/plain``) is a "simple" request: CORS does not preflight it and does
not stop it from arriving, it only hides the response from the page. So a
hostile or compromised site could reboot the Pi, pull code, install or remove
plugins or rewrite the config, without the user ever seeing the interface.

The defence here needs no tokens and no frontend change. Browsers attach an
``Origin`` header to every cross-site POST (and to same-origin ones in all
current browsers), and it cannot be set or removed by page script. So for any
state-changing method:

* ``Origin`` present -> it must name this server's own host, else 403.
  ``Origin: null`` (a sandboxed iframe, a ``file://`` page, some cross-site
  redirect chains) is never this server, so it is refused too.
* ``Origin`` absent, ``Referer`` present -> the same check on the Referer.
* neither -> allowed. That is curl, Home Assistant, the MQTT bridge and every
  other script: not a browser, so not a confused deputy. A browser making a
  cross-site request always sends ``Origin``.

"This server's own host" is the ``Host`` header the request arrived with, so
it follows whatever name or address the user typed: ``ledpi.local:5000``,
``192.168.1.40:5000``, or ``192.168.4.1`` in access-point mode (the captive
portal's port 80 -> 5000 redirect keeps the Host the browser sent, and the
setup page's fetches go back to that same host).

The scheme is deliberately not compared, only host and port (with each side's
default port filled in from its own scheme). A TLS-terminating reverse proxy
that passes ``Host`` through makes the browser say ``https://pi.example`` while
Flask sees ``http``; an attacker cannot use that gap, because to match they
would need to serve a page from this same host and port. The app does not use
``ProxyFix`` and so does not trust ``X-Forwarded-Host``: a proxy that rewrites
``Host`` to the upstream address (nginx's default ``proxy_pass`` does) must
be configured to pass the original one (``proxy_set_header Host $host;``).

Not covered: DNS rebinding (an attacker's hostname re-pointed at the Pi is
"same origin" to the browser), and anyone who can reach the port directly.
Neither is new; the interface is still meant for a trusted network.
"""
import logging
from urllib.parse import urlsplit

from flask import Flask, jsonify, request

logger = logging.getLogger('web_interface.origin_guard')

#: Methods that change state and so must come from this interface's own pages.
STATE_CHANGING_METHODS = frozenset({'POST', 'PUT', 'PATCH', 'DELETE'})

_DEFAULT_PORTS = {'http': 80, 'https': 443}


def _host_port(scheme: str, netloc: str):
    """``(hostname, port)`` for a URL's authority, or None if it has none.

    Lower-cases the host and fills in the scheme's default port, so
    ``http://Pi.local`` and a ``Host: pi.local:80`` header compare equal.
    """
    try:
        parts = urlsplit(f'{scheme}://{netloc}')
        hostname = parts.hostname
        port = parts.port
    except ValueError:
        # A malformed port or bracketed address.
        return None
    if not hostname:
        return None
    if port is None:
        port = _DEFAULT_PORTS.get(scheme.lower())
    return hostname.lower().rstrip('.'), port


def _url_host_port(url: str):
    """``(hostname, port)`` for an Origin or Referer value, or None."""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    if parts.scheme.lower() not in _DEFAULT_PORTS or not parts.netloc:
        return None
    return _host_port(parts.scheme.lower(), parts.netloc.rsplit('@', 1)[-1])


def _request_host_port():
    """``(hostname, port)`` this request was addressed to, per its Host."""
    return _host_port(request.scheme, request.host)


def check_request_origin():
    """None when the request may proceed, else the reason it may not.

    The reason is a short phrase for the log and the error message.
    """
    if request.method not in STATE_CHANGING_METHODS:
        return None

    origin = request.headers.get('Origin')
    if origin is not None:
        header, value = 'Origin', origin
    else:
        referer = request.headers.get('Referer')
        if referer is None:
            # Not a browser (curl, Home Assistant, the MQTT bridge, scripts).
            return None
        header, value = 'Referer', referer

    if value.strip().lower() == 'null':
        return f'{header} is "null" (sandboxed or file:// page)'

    claimed = _url_host_port(value)
    if claimed is None:
        return f'{header} header is not a valid http(s) URL'
    if claimed != _request_host_port():
        # The claimed value is attacker-chosen: the hook logs it, but the
        # reason (echoed in the 403 body) never repeats it.
        return header + ' names a different host than this interface'
    return None


def init_app(app: Flask) -> None:
    """Refuse state-changing requests that another website's page sent."""

    @app.before_request
    def _refuse_cross_site_requests():
        reason = check_request_origin()
        if reason is None:
            return None
        logger.warning("Refused cross-site %s %s: %s (Origin=%r, Referer=%r)",
                       request.method, request.path, reason,
                       request.headers.get('Origin'),
                       request.headers.get('Referer'))
        return jsonify({
            'status': 'error',
            'error_code': 'CROSS_SITE_REQUEST',
            'message': ('Refused: this request came from another website, not '
                        'from the LEDMatrix interface. Open the interface '
                        'directly (the address in your browser bar must be the '
                        'same one the request goes to) and try again.'),
            'details': reason,
        }), 403
