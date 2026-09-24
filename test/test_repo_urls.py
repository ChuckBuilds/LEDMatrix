"""Repository URL handling shared by the plugin store and saved repositories.

The store cleaned URLs with ``url.rstrip('/').replace('.git', '')`` in two
places, which removes ``.git`` anywhere in the URL:
``https://github.com/user/my.github.io`` became ``.../myhub.io``, so installing
or browsing such a repository asked GitHub for one that does not exist.
"""

from unittest.mock import MagicMock

import pytest

from src.plugin_system.repo_urls import (
    github_api_headers, github_owner_repo, normalize_repo_url, same_repo,
)
from src.plugin_system.store_manager import PluginStoreManager

PAGES_REPO = "https://github.com/user/my.github.io"


class TestNormalizeRepoUrl:
    @pytest.mark.parametrize("raw, expected", [
        (PAGES_REPO, PAGES_REPO),
        (PAGES_REPO + ".git", PAGES_REPO),
        ("https://github.com/user/repo.git/", "https://github.com/user/repo"),
        ("  https://github.com/user/repo/  ", "https://github.com/user/repo"),
    ])
    def test_only_a_trailing_dot_git_is_removed(self, raw, expected):
        assert normalize_repo_url(raw) == expected

    def test_same_repo_ignores_case_and_suffix(self):
        assert same_repo("https://github.com/Owner/Repo.git",
                         "https://github.com/owner/repo/")
        assert not same_repo("https://github.com/owner/repo",
                             "https://github.com/owner/other")


class TestGithubOwnerRepo:
    @pytest.mark.parametrize("url, expected", [
        (PAGES_REPO + ".git", ("user", "my.github.io")),
        ("https://www.github.com/owner/repo", ("owner", "repo")),
        ("https://github.com/owner/repo/tree/main/plugins/x", ("owner", "repo")),
    ])
    def test_github_urls(self, url, expected):
        assert github_owner_repo(url) == expected

    @pytest.mark.parametrize("url", [
        "https://github.com.example.org/owner/repo",
        "https://gitlab.com/owner/repo",
        "https://github.com/owner",
        "github.com/owner/repo",
    ])
    def test_anything_else_is_not_a_github_repo(self, url):
        assert github_owner_repo(url) is None

    def test_headers_carry_the_token_only_when_given(self):
        assert "Authorization" not in github_api_headers(None)
        assert github_api_headers("abc")["Authorization"] == "token abc"


@pytest.fixture
def store(tmp_path):
    return PluginStoreManager(
        plugins_dir=str(tmp_path / "plugins"),
        uninstalled_registry_path=str(tmp_path / "uninstalled.json"))


def test_install_from_url_keeps_an_interior_dot_git(store, monkeypatch):
    cloned_from = []
    monkeypatch.setattr(store, "_install_via_git",
                        lambda url, *a, **k: cloned_from.append(url))
    downloaded = []
    monkeypatch.setattr(store, "_install_via_download",
                        lambda url, *a, **k: downloaded.append(url) or False)

    result = store.install_from_url(PAGES_REPO + ".git")

    assert result["success"] is False
    assert cloned_from == [PAGES_REPO]
    assert all(url.startswith(PAGES_REPO + "/archive/") for url in downloaded)


def test_fetch_registry_from_url_asks_for_the_named_repository(store, monkeypatch):
    requested = []

    def fake_get(url, **kwargs):
        requested.append(url)
        return MagicMock(status_code=404)

    monkeypatch.setattr(store, "_http_get_with_retries", fake_get)

    assert store.fetch_registry_from_url(PAGES_REPO) is None
    assert requested
    assert all(url.startswith("https://raw.githubusercontent.com/user/my.github.io/")
               for url in requested)
