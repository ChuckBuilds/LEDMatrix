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

The scheme is deliberately not compared, only host and port. The claimed
value's default port comes from its own scheme. A ``Host`` without a port
means "the default port of whatever scheme the browser used", and that scheme
is not always the one Flask sees: a TLS-terminating reverse proxy makes the
browser say ``https://pi.example`` (443) while Flask sees ``http`` (80). So a
portless ``Host`` accepts either default. An attacker cannot use that gap,
because to match they would need to serve a page from this same host on its
standard port. The app does not use ``ProxyFix`` and so does not trust
``X-Forwarded-Host`` or ``X-Forwarded-Proto``: a proxy that rewrites ``Host``
to the upstream address (nginx's default ``proxy_pass`` does) must be
configured to pass the original one, port included
(``proxy_set_header Host $http_host;`` -- nginx's ``$host`` drops the port).

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


def _authority(netloc: str):
    """``(hostname, port)`` for an authority; port is None when it has none.

    Lower-cases the host and drops a trailing dot, so ``Pi.local.`` and
    ``pi.local`` compare equal. None if the authority is unreadable.
    """
    try:
        parts = urlsplit(f'//{netloc}')
        hostname = parts.hostname
        port = parts.port
    except ValueError:
        # A malformed port or bracketed address.
        return None
    if not hostname:
        return None
    return hostname.lower().rstrip('.'), port


def _url_host_port(url: str):
    """``(hostname, port, default_port)`` for an Origin or Referer, or None.

    ``port`` is the explicit port or, failing that, the URL scheme's default,
    which is also returned as ``default_port``.
    """
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    if scheme not in _DEFAULT_PORTS or not parts.netloc:
        return None
    authority = _authority(parts.netloc.rsplit('@', 1)[-1])
    if authority is None:
        return None
    hostname, port = authority
    default_port = _DEFAULT_PORTS[scheme]
    return hostname, default_port if port is None else port, default_port


def _names_this_server(claimed) -> bool:
    """Whether a claimed ``(hostname, port, default_port)`` is this request's
    own ``Host``."""
    own = _authority(request.host)
    if own is None:
        return False
    hostname, port = own
    claimed_host, claimed_port, claimed_default = claimed
    if claimed_host != hostname:
        return False
    if port is not None:
        return claimed_port == port
    # A portless Host is the default port of the scheme the browser used.
    # Behind a TLS-terminating proxy that is https/443 while Flask sees
    # http/80, so accept the default of either scheme.
    return claimed_port in (claimed_default,
                            _DEFAULT_PORTS.get(request.scheme))


def _loggable(value: str) -> str:
    """Just the ``scheme://host[:port]`` of an Origin/Referer, for the log.

    A Referer's path and query can carry tokens or other private data, and
    only the site matters when reading a refusal.
    """
    try:
        parts = urlsplit(value.strip())
        netloc = parts.netloc.rsplit('@', 1)[-1]
    except ValueError:
        return '<unreadable>'
    if not parts.scheme or not netloc:
        return '<unreadable>'
    return f'{parts.scheme}://{netloc}'


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
    if not _names_this_server(claimed):
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
        # Only the site each header names, never a Referer's path or query
        # (which can carry tokens); %r keeps CR/LF from forging log lines.
        origin = request.headers.get('Origin')
        referer = request.headers.get('Referer')
        logger.warning("Refused cross-site %s %r: %s (Origin=%r, Referer=%r)",
                       request.method, request.path, reason,
                       None if origin is None else _loggable(origin),
                       None if referer is None else _loggable(referer))
        return jsonify({
            'status': 'error',
            'error_code': 'CROSS_SITE_REQUEST',
            'message': ('Refused: this request came from another website, not '
                        'from the LEDMatrix interface. Open the interface '
                        'directly (the address in your browser bar must be the '
                        'same one the request goes to) and try again.'),
            'details': reason,
        }), 403
