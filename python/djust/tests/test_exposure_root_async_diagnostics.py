"""Root background diagnostics must not export explicit callback values."""

import logging
from types import SimpleNamespace

import pytest

from djust.runtime import ViewRuntime
from djust.tests.test_runtime_child_routing_1892 import MockTransport


@pytest.mark.asyncio
@pytest.mark.parametrize("policy", ["explicit", "invalid", None, "legacy_to_explicit"])
@pytest.mark.parametrize("failure", ["callback", "result_handler", "stale"])
async def test_root_async_diagnostics_redact_nonlegacy_values(caplog, policy, failure):
    caplog.set_level(logging.DEBUG)
    runtime = ViewRuntime(MockTransport())
    view = SimpleNamespace(exposure_policy="legacy" if policy == "legacy_to_explicit" else policy)
    runtime.view_instance = view

    async def callback():
        if policy == "legacy_to_explicit":
            view.exposure_policy = "explicit"
        if failure == "stale":
            runtime.view_instance = None
            return None
        raise ValueError("CALLBACK_SECRET_SENTINEL")

    if failure == "result_handler":

        def handle_result(name, result=None, error=None):
            assert str(error) == "CALLBACK_SECRET_SENTINEL"
            raise RuntimeError("RESULT_SECRET_SENTINEL")

        view.handle_async_result = handle_result

    await runtime._execute_async_task("TASK_SECRET_SENTINEL", callback, (), {}, "work")
    assert "SENTINEL" not in caplog.text
    assert not any(record.exc_info for record in caplog.records)
