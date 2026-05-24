"""
Tests for src/background_data_service.py

Covers BackgroundDataService: submit_fetch_request, get_result,
is_request_complete, get_request_status, cancel_request, get_statistics,
_cleanup_completed_requests, shutdown, and get_background_service singleton.
"""

import time
import pytest
from unittest.mock import MagicMock, patch, Mock
from concurrent.futures import Future

from src.background_data_service import (
    BackgroundDataService,
    FetchStatus,
    FetchResult,
    FetchRequest,
    get_background_service,
    shutdown_background_service,
)
import src.background_data_service as bds_module


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_global_service():
    """Ensure each test starts with no global singleton."""
    shutdown_background_service()
    yield
    shutdown_background_service()


@pytest.fixture
def mock_cache_manager():
    m = MagicMock()
    m.get.return_value = None
    m.set.return_value = None
    m.generate_sport_cache_key.return_value = "test_key"
    return m


@pytest.fixture
def service(mock_cache_manager):
    svc = BackgroundDataService(mock_cache_manager, max_workers=2, request_timeout=5)
    yield svc
    svc.shutdown(wait=False)


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------

class TestInitialisation:
    def test_stats_zeroed(self, service):
        stats = service.get_statistics()
        assert stats["total_requests"] == 0
        assert stats["completed_requests"] == 0
        assert stats["failed_requests"] == 0

    def test_no_active_requests(self, service):
        assert len(service.active_requests) == 0

    def test_not_shutdown(self, service):
        assert service._shutdown is False


# ---------------------------------------------------------------------------
# Cache hit path
# ---------------------------------------------------------------------------

class TestCacheHit:
    def test_cache_hit_returns_request_id(self, service, mock_cache_manager):
        mock_cache_manager.get.return_value = {"events": [{"id": "1"}]}
        req_id = service.submit_fetch_request(
            sport="nfl", year=2024,
            url="https://example.com/nfl",
            cache_key="nfl_key",
        )
        assert req_id is not None
        # Request should be immediately complete due to cache hit
        result = service.get_result(req_id)
        assert result is not None
        assert result.success is True
        assert result.cached is True

    def test_cache_hit_increments_stat(self, service, mock_cache_manager):
        mock_cache_manager.get.return_value = {"events": []}
        service.submit_fetch_request(sport="nba", year=2024, url="https://x.com", cache_key="k")
        stats = service.get_statistics()
        assert stats["cached_hits"] == 1


# ---------------------------------------------------------------------------
# Actual fetch path (mocked HTTP)
# ---------------------------------------------------------------------------

class TestFetchPath:
    def _valid_payload(self) -> dict:
        return {"events": [{"id": "g1"}, {"id": "g2"}]}

    def test_successful_fetch_completes(self, service, mock_cache_manager):
        mock_resp = Mock()
        mock_resp.json.return_value = self._valid_payload()
        mock_resp.raise_for_status.return_value = None

        with patch.object(service.session, "get", return_value=mock_resp):
            req_id = service.submit_fetch_request(
                sport="nfl", year=2024,
                url="https://example.com/nfl",
                cache_key="nfl_test",
            )
            # Wait for the background thread
            deadline = time.time() + 5
            while not service.is_request_complete(req_id) and time.time() < deadline:
                time.sleep(0.05)

        result = service.get_result(req_id)
        assert result is not None
        assert result.success is True
        assert result.data == self._valid_payload()

    def test_failed_fetch_records_error(self, service, mock_cache_manager):
        with patch.object(service.session, "get", side_effect=Exception("network error")):
            req_id = service.submit_fetch_request(
                sport="nba", year=2024,
                url="https://example.com/nba",
                cache_key="nba_test",
                max_retries=0,
            )
            deadline = time.time() + 5
            while not service.is_request_complete(req_id) and time.time() < deadline:
                time.sleep(0.05)

        result = service.get_result(req_id)
        assert result is not None
        assert result.success is False
        assert result.error is not None

    def test_cache_miss_increments_stat(self, service, mock_cache_manager):
        mock_resp = Mock()
        mock_resp.json.return_value = self._valid_payload()
        mock_resp.raise_for_status.return_value = None

        with patch.object(service.session, "get", return_value=mock_resp):
            service.submit_fetch_request(
                sport="nfl", year=2024, url="https://x.com", cache_key="new_key",
            )
        stats = service.get_statistics()
        assert stats["cache_misses"] == 1

    def test_callback_called_on_success(self, service, mock_cache_manager):
        callback = Mock()
        mock_resp = Mock()
        mock_resp.json.return_value = self._valid_payload()
        mock_resp.raise_for_status.return_value = None

        with patch.object(service.session, "get", return_value=mock_resp):
            req_id = service.submit_fetch_request(
                sport="nfl", year=2024, url="https://x.com",
                cache_key="cb_key", callback=callback, max_retries=0,
            )
            deadline = time.time() + 5
            while not service.is_request_complete(req_id) and time.time() < deadline:
                time.sleep(0.05)

        callback.assert_called_once()
        call_arg = callback.call_args[0][0]
        assert isinstance(call_arg, FetchResult)

    def test_data_cached_after_successful_fetch(self, service, mock_cache_manager):
        mock_resp = Mock()
        mock_resp.json.return_value = self._valid_payload()
        mock_resp.raise_for_status.return_value = None

        with patch.object(service.session, "get", return_value=mock_resp):
            req_id = service.submit_fetch_request(
                sport="nfl", year=2024, url="https://x.com", cache_key="cache_after_key",
            )
            deadline = time.time() + 5
            while not service.is_request_complete(req_id) and time.time() < deadline:
                time.sleep(0.05)

        mock_cache_manager.set.assert_called()


# ---------------------------------------------------------------------------
# Request status / cancel
# ---------------------------------------------------------------------------

class TestRequestStatusAndCancel:
    def test_unknown_request_status_is_none(self, service):
        assert service.get_request_status("nonexistent") is None

    def test_cancel_active_request(self, service, mock_cache_manager):
        # Manually insert an active request
        req = FetchRequest(
            id="r1", sport="nfl", year=2024,
            cache_key="k", url="https://x.com",
        )
        req.status = FetchStatus.PENDING
        service.active_requests["r1"] = req
        result = service.cancel_request("r1")
        assert result is True
        assert "r1" not in service.active_requests

    def test_cancel_nonexistent_request(self, service):
        assert service.cancel_request("does-not-exist") is False

    def test_is_request_complete_false_for_active(self, service, mock_cache_manager):
        req = FetchRequest(
            id="r2", sport="mlb", year=2024,
            cache_key="k2", url="https://x.com",
        )
        service.active_requests["r2"] = req
        assert service.is_request_complete("r2") is False

    def test_is_request_complete_true_for_done(self, service):
        result = FetchResult(request_id="r3", success=True)
        service.completed_requests["r3"] = result
        assert service.is_request_complete("r3") is True

    def test_get_result_returns_none_for_unknown(self, service):
        assert service.get_result("unknown") is None


# ---------------------------------------------------------------------------
# Shutdown
# ---------------------------------------------------------------------------

class TestShutdown:
    def test_shutdown_sets_flag(self, service):
        service.shutdown(wait=False)
        assert service._shutdown is True

    def test_submit_after_shutdown_raises(self, service, mock_cache_manager):
        service.shutdown(wait=False)
        with pytest.raises(RuntimeError, match="shutting down"):
            service.submit_fetch_request(
                sport="nfl", year=2024, url="https://x.com", cache_key="k"
            )


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

class TestCleanup:
    def test_cleanup_removes_old_requests(self, service):
        old_result = FetchResult(request_id="old", success=True)
        old_result.completed_at = time.time() - 7200  # 2 hours ago
        service.completed_requests["old"] = old_result
        service._last_completed_requests_cleanup = 0  # force cleanup
        removed = service._cleanup_completed_requests(force=True)
        assert removed >= 1
        assert "old" not in service.completed_requests

    def test_cleanup_respects_interval(self, service):
        old_result = FetchResult(request_id="r", success=True)
        old_result.completed_at = time.time() - 7200
        service.completed_requests["r"] = old_result
        # Cleanup interval not passed, should skip
        service._last_completed_requests_cleanup = time.time()
        removed = service._cleanup_completed_requests(force=False)
        assert removed == 0

    def test_size_limit_enforcement(self, service):
        service._max_completed_requests = 3
        for i in range(5):
            result = FetchResult(request_id=str(i), success=True)
            result.completed_at = time.time() - (5 - i) * 100  # oldest first
            service.completed_requests[str(i)] = result
        service._last_completed_requests_cleanup = 0
        service._cleanup_completed_requests(force=True)
        assert len(service.completed_requests) <= 3


# ---------------------------------------------------------------------------
# Singleton get_background_service
# ---------------------------------------------------------------------------

class TestGetBackgroundService:
    def test_first_call_requires_cache_manager(self):
        with pytest.raises(ValueError, match="cache_manager is required"):
            get_background_service()

    def test_creates_singleton(self, mock_cache_manager):
        svc1 = get_background_service(mock_cache_manager)
        svc2 = get_background_service()
        assert svc1 is svc2

    def test_shutdown_clears_singleton(self, mock_cache_manager):
        get_background_service(mock_cache_manager)
        shutdown_background_service()
        with pytest.raises(ValueError):
            get_background_service()
