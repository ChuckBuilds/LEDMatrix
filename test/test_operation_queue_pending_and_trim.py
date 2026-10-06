"""PluginOperationQueue refuses a second queued op per plugin and stays bounded.

enqueue_operation only checked _active_operations, which holds the operation
that is *running*. While a plugin's first operation still waited in the queue
(the worker busy with another plugin), a second one for the same plugin was
accepted and both ran back to back. And _operations kept every operation ever
enqueued, although the history beside it was trimmed to max_history.
"""

import threading
import time

import pytest

from src.plugin_system.operation_queue import PluginOperationQueue
from src.plugin_system.operation_types import OperationStatus, OperationType


@pytest.fixture
def op_queue():
    q = PluginOperationQueue(max_history=3)
    yield q
    q.shutdown()


def _wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.01)
    return predicate()


def test_second_pending_operation_for_a_plugin_is_refused(op_queue):
    release = threading.Event()
    started = threading.Event()

    def blocker(op):
        started.set()
        release.wait(5)
        return {"success": True}

    op_queue.enqueue_operation(OperationType.INSTALL, "busy", operation_callback=blocker)
    assert started.wait(5)

    first = op_queue.enqueue_operation(
        OperationType.INSTALL, "demo", operation_callback=lambda op: {"success": True})
    assert op_queue.get_operation_status(first).status == OperationStatus.PENDING

    with pytest.raises(ValueError, match="already has an active operation"):
        op_queue.enqueue_operation(
            OperationType.INSTALL, "demo", operation_callback=lambda op: {"success": True})

    release.set()
    assert _wait_for(lambda: op_queue.get_operation_status(first).status
                     == OperationStatus.COMPLETED)
    # Once it has finished, the plugin accepts a new operation again.
    op_queue.enqueue_operation(OperationType.UNINSTALL, "demo")


def test_operations_map_is_trimmed_with_history(op_queue):
    ids = [op_queue.enqueue_operation(OperationType.INSTALL, f"p{i}",
                                      operation_callback=lambda op: {"success": True})
           for i in range(8)]
    assert _wait_for(lambda: all(
        (op_queue.get_operation_status(i) is None
         or op_queue.get_operation_status(i).status == OperationStatus.COMPLETED)
        for i in ids) and len(op_queue._operation_history) == 3)

    assert len(op_queue._operations) == 3
    kept = {op.operation_id for op in op_queue._operation_history}
    assert set(op_queue._operations) == kept
