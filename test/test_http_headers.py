"""One set of HTTP headers for core's ESPN/data requests.

The logo downloader and the background data service each carried their own
header dict with a placeholder User-Agent (``yourusername/LEDMatrix;
contact@example.com``) -- the kind of nonconforming token ESPN began 403ing
around 2026-08-04 -- and a hand-set ``Accept-Encoding: ... br`` although brotli
is not installed, so a ``br`` body could not have been decoded.
"""

from unittest.mock import MagicMock

import pytest

from src.common.api_helper import DEFAULT_HTTP_HEADERS, USER_AGENT, APIHelper


def _lower_keys(headers):
    return {k.lower(): v for k, v in headers.items()}


class TestSharedHeaders:
    def test_user_agent_names_the_project(self):
        assert USER_AGENT == 'LEDMatrix/1.0 (+https://github.com/ChuckBuilds/LEDMatrix)'
        assert DEFAULT_HTTP_HEADERS['User-Agent'] == USER_AGENT

    def test_no_hand_set_accept_encoding(self):
        assert 'accept-encoding' not in _lower_keys(DEFAULT_HTTP_HEADERS)

    def test_is_read_only(self):
        with pytest.raises(TypeError):
            DEFAULT_HTTP_HEADERS['User-Agent'] = 'x'  # type: ignore[index]

    def test_api_helper_sends_the_same_user_agent(self):
        assert APIHelper().session.headers['User-Agent'] == USER_AGENT


class TestLogoDownloaderHeaders:
    def test_uses_the_shared_headers(self):
        from src.logo_downloader import LogoDownloader
        headers = LogoDownloader().headers
        assert headers['User-Agent'] == USER_AGENT
        assert 'accept-encoding' not in _lower_keys(headers)
        assert 'yourusername' not in str(headers)

    def test_instance_headers_are_a_private_copy(self):
        from src.logo_downloader import LogoDownloader
        downloader = LogoDownloader()
        downloader.headers['X-Test'] = '1'
        assert 'X-Test' not in DEFAULT_HTTP_HEADERS
        assert 'X-Test' not in LogoDownloader().headers

    def test_logo_request_sends_the_user_agent_and_asks_for_an_image(self, tmp_path):
        from src.logo_downloader import LogoDownloader
        downloader = LogoDownloader()
        downloader.session.get = MagicMock(side_effect=RuntimeError("stop"))
        downloader.download_logo("http://x/a.png", tmp_path / "A.png", "A")
        sent = _lower_keys(downloader.session.get.call_args.kwargs['headers'])
        assert sent['user-agent'] == USER_AGENT
        assert sent['accept'].startswith('image/')
        assert 'accept-encoding' not in sent


class TestBackgroundDataServiceHeaders:
    def test_uses_the_shared_headers(self):
        from src.background_data_service import BackgroundDataService
        service = BackgroundDataService(MagicMock(), max_workers=1)
        try:
            headers = service.default_headers
            assert headers['User-Agent'] == USER_AGENT
            assert 'accept-encoding' not in _lower_keys(headers)
            assert 'yourusername' not in str(headers)
        finally:
            service.shutdown(wait=False)
