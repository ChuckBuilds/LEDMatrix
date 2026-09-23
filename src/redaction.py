"""Credential redaction for text that leaves the process that produced it.

Kept free of Flask so the display service can redact what it publishes (see
src/error_aggregator.py) as well as the web interface what it returns.
"""

import re

# Credentials that turn up inside exception text. A requests error quotes the
# URL it failed on, and plugins that authenticate by query string put their key
# there, so echoing an exception verbatim can hand out an API key. Redact the
# value, keep the parameter name -- knowing *which* credential was involved is
# part of the diagnosis.
_REDACT_CREDENTIAL = re.compile(
    r'((?:api[_-]?key|access[_-]?token|auth|apikey|key|passwd|password|pwd|'
    r'secret|sig|signature|token)["\']?\s*[=:]\s*["\']?)([^\s&"\'<>,}]+)',
    re.IGNORECASE,
)

# `Authorization: <scheme> <credential>`. The scheme name is kept because it
# says which kind of credential failed; the credential goes. Any scheme
# matches, not a fixed list: ApiKey, Negotiate, NTLM, AWS4-HMAC-SHA256 and
# whatever a plugin's API invents next are all credentials, and a list would
# silently leak the ones nobody thought of. Not covered by the generic pattern
# above, whose value part stops at whitespace and so would keep the credential
# once a space follows the scheme.
_REDACT_AUTH_HEADER = re.compile(
    r'((?:proxy-)?authorization["\']?\s*[=:]\s*["\']?\s*'
    r'(?:[A-Za-z][\w.+-]*[ \t]+)?)'          # optional scheme name, kept
    r'([^\s,"\'<>}]+)',                       # the credential, redacted
    re.IGNORECASE,
)

# Credentials embedded in a URL: https://user:password@host. requests quotes
# the full URL in its exceptions, so this is a realistic leak. The username is
# kept -- it identifies which account failed without being the secret.
_REDACT_URL_USERINFO = re.compile(r'([a-z][a-z0-9+.-]*://[^/\s:@]+:)([^/\s@]+)(@)',
                                  re.IGNORECASE)


def redact_credentials(text: str) -> str:
    """Replace credentials in ``text`` with ``<redacted>``; keep everything
    else, including line breaks, so a stack trace stays readable."""
    text = text or ''
    # Order matters: the URL and header forms are more specific than the
    # generic key=value pattern, which would otherwise chew the scheme.
    text = _REDACT_URL_USERINFO.sub(r'\1<redacted>\3', text)
    text = _REDACT_AUTH_HEADER.sub(r'\1<redacted>', text)
    return _REDACT_CREDENTIAL.sub(r'\1<redacted>', text)
