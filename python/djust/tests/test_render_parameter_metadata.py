"""Render frames own detached snapshots; legacy-only sessions stay unchanged."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from djust.runtime import ViewRuntime
from djust.tests.test_parameter_metadata import LegacyOwner, StrictOwner


def runtime(root):
    transport = SimpleNamespace(send=AsyncMock(), send_error=AsyncMock())
    result = ViewRuntime(transport)
    result.view_instance = root
    result._parameter_contract_view = "app.Page"
    return result, transport


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["patch", "html_update", "embedded_update"])
async def test_render_contracts_follow_owner_replacement_and_removal(kind):
    root = SimpleNamespace(_components={"menu": StrictOwner()})
    owner, transport = runtime(root)
    first = {"type": kind, "version": 1}
    await owner._send_render_frame(first)
    assert first["parameter_contract_view"] == "app.Page"
    assert first["parameter_contracts"]["owners"][1]["handlers"]["choose"]["policy"] == "strict"

    root._components["menu"] = LegacyOwner()
    second = {"type": kind, "version": 2}
    await owner._send_render_frame(second)
    assert second["parameter_contracts"] is None
    # Delayed references to the first frame must not observe newer owners.
    assert first["parameter_contracts"]["owners"][1]["handlers"]["choose"]["policy"] == "strict"
    del root._components["menu"]
    third = {"type": kind, "version": 3}
    await owner._send_render_frame(third)
    assert third["parameter_contracts"] is None
    assert transport.send.await_count == 3


@pytest.mark.asyncio
async def test_legacy_render_does_not_gain_contract_fields_or_affect_another_runtime():
    legacy, legacy_transport = runtime(LegacyOwner())
    strict, _ = runtime(StrictOwner())
    await strict._send_render_frame({"type": "patch"})
    frame = {"type": "patch", "version": 2}
    await legacy._send_render_frame(frame)
    assert frame == {"type": "patch", "version": 2}
    legacy_transport.send.assert_awaited_once_with(frame)


@pytest.mark.asyncio
async def test_invalid_render_contract_does_not_emit_dom_or_sensitive_error(monkeypatch):
    owner, transport = runtime(StrictOwner())

    def broken(_root):
        raise ValueError("SECRET_DECLARATION")

    monkeypatch.setattr("djust._parameter_metadata.parameter_contract_manifest", broken)
    await owner._send_render_frame({"type": "patch"})
    transport.send.assert_not_awaited()
    transport.send_error.assert_awaited_once_with(
        "Render parameter contracts unavailable.", code="render_error"
    )
