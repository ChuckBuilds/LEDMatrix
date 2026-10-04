"""GET /api/v3/plugins/operation/<id> answers for an operation still waiting.

PluginOperationQueue keeps an operation's callback in its parameters, under
``_callback``, until the worker takes it to run. PluginOperation.to_dict()
returned the parameters as they were, so for a pending operation the route
handed jsonify a function and answered 500 "A system error occurred". That
is every poll of an install queued behind another plugin's: the second of
two installs read as broken until the first one finished.
"""

import json
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from test._api_v3_test_helpers import api_v3_client, api_v3_module  # noqa: F401,E402

from src.plugin_system.operation_queue import PluginOperationQueue  # noqa: E402
from src.plugin_system.operation_types import (  # noqa: E402
    OperationType, PluginOperation,
)


def _callback(op):
    return {"success": True, "message": "done"}


class TestToDict:
    def test_private_parameters_are_left_out(self):
        op = PluginOperation(OperationType.INSTALL, "demo",
                             parameters={"_callback": _callback, "branch": "main"})
        assert op.to_dict()["parameters"] == {"branch": "main"}
        json.dumps(op.to_dict())   # serializable

    def test_the_operation_keeps_its_callback_for_the_worker(self):
        op = PluginOperation(OperationType.INSTALL, "demo",
                             parameters={"_callback": _callback})
        op.to_dict()
        assert op.parameters["_callback"] is _callback

    def test_the_other_fields_are_unchanged(self):
        op = PluginOperation(OperationType.UNINSTALL, "demo", operation_id="op-1")
        assert op.to_dict() == {
            "operation_id": "op-1", "operation_type": "uninstall", "plugin_id": "demo",
            "parameters": {}, "status": "pending", "progress": 0.0, "message": "",
            "error": None, "result": None,
            "created_at": op.created_at.isoformat(), "started_at": None,
            "completed_at": None,
        }


class TestTheRoute:
    @pytest.fixture
    def busy_queue(self, api_v3_module):
        """A real queue whose worker is held by another plugin's operation."""
        queue = PluginOperationQueue(max_history=10)
        api_v3_module.api_v3.operation_queue = queue
        started, release = threading.Event(), threading.Event()

        def blocker(op):
            started.set()
            release.wait(10)
            return {"success": True, "message": "done"}

        queue.enqueue_operation(OperationType.INSTALL, "busy", operation_callback=blocker)
        assert started.wait(5)
        yield queue
        release.set()
        queue.shutdown()

    def test_a_pending_operation_reports_pending(self, api_v3_client, busy_queue):
        op_id = busy_queue.enqueue_operation(
            OperationType.INSTALL, "demo", operation_callback=_callback)
        response = api_v3_client.get(f"/api/v3/plugins/operation/{op_id}")
        assert response.status_code == 200, response.get_json()
        data = response.get_json()["data"]
        assert data["status"] == "pending"
        assert data["plugin_id"] == "demo"
        assert "_callback" not in data["parameters"]
