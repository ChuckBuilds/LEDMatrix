"""
Repository URL helpers shared by the plugin store and saved repositories.

One definition of "the same repository URL", of how a GitHub URL maps to
``owner/repo``, and of the headers sent to the GitHub API.
"""

from typing import Dict, Optional, Tuple
from urllib.parse import urlparse

#: Hosts whose URLs name a GitHub repository. Matched against
#: ``urlparse(url).hostname``, never by substring: a substring test accepts
#: ``https://github.com.example.org/...`` as GitHub.
GITHUB_HOSTS = frozenset({'github.com', 'www.github.com'})

#: Sent on every request the store makes to GitHub.
USER_AGENT = 'LEDMatrix-Plugin-Manager/1.0'


def normalize_repo_url(url: str) -> str:
    """``url`` without surrounding whitespace, trailing slashes or a trailing ``.git``.

    Only a *trailing* ``.git`` is removed. The unanchored
    ``url.replace('.git', '')`` this replaces turned
    ``https://github.com/user/my.github.io`` into ``.../myhub.io``.
    Case is preserved; compare with :func:`same_repo`.
    """
    url = url.strip().rstrip('/')
    if url.endswith('.git'):
        url = url[:-4]
    return url


def same_repo(url_a: str, url_b: str) -> bool:
    """Whether two URLs name the same repository.

    GitHub owner and repository names are case-insensitive, so
    ``ChuckBuilds/LEDMatrix-Plugins`` and ``chuckbuilds/ledmatrix-plugins``
    are one repository.
    """
    return normalize_repo_url(url_a).lower() == normalize_repo_url(url_b).lower()


def github_owner_repo(url: str) -> Optional[Tuple[str, str]]:
    """``(owner, repo)`` for a github.com repository URL, else None.

    The first two path segments, so a URL that points inside the repository
    (``.../owner/repo/tree/main/plugins/x``) still names ``owner/repo``.
    """
    parsed = urlparse(normalize_repo_url(url))
    if parsed.hostname not in GITHUB_HOSTS:
        return None
    parts = [part for part in parsed.path.split('/') if part]
    if len(parts) < 2:
        return None
    return parts[0], normalize_repo_url(parts[1])


def github_api_headers(token: Optional[str] = None) -> Dict[str, str]:
    """Headers for a GitHub REST API request, authenticated when ``token`` is set.

    An authenticated request gets 5000 requests an hour instead of 60.
    """
    headers = {
        'Accept': 'application/vnd.github.v3+json',
        'User-Agent': USER_AGENT,
    }
    if token:
        headers['Authorization'] = f'token {token}'
    return headers
