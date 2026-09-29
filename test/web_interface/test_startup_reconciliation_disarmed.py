"""
No test may start the real app's startup reconciliation.

web_interface/app.py reads the checkout's real config/config.json and
plugin-repos/ at import, and its first request launches a reconciliation
thread that reinstalls every configured-but-missing plugin from the live
store. A full suite run on a dev checkout used to leave whole plugins
untracked in plugin-repos/ that way. test/conftest.py disarms the run-once
latch on every import of the module; this pins that.
"""

from unittest.mock import MagicMock, patch


def test_a_request_to_the_imported_app_launches_no_reconciliation():
    import web_interface.app as web_app

    assert web_app._reconciliation_started is True

    with patch.object(web_app, "threading", MagicMock()) as threading_mock:
        web_app.app.test_client().get("/favicon.ico")

    threading_mock.Thread.assert_not_called()
