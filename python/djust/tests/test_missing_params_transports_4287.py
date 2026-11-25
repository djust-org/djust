"""#4287: ``missing_params`` rides every real transport's refusal, not just the test client.

Each refusal site builds its own ``validation_details`` envelope, so a refactor of one
can silently drop the key. These tests drive the WS/SSE runtime (main view, embedded
child, component) and pin the key on the wire frame.
"""

from __future__ import annotations

import contextlib
import uuid
from typing import Any, Dict, List, Optional

import pytest

from djust import LiveView
from djust.components.base import LiveComponent
from djust.decorators import event_handler
from djust.runtime import ViewRuntime

pytestmark = [pytest.mark.pr_tier]


class _Transport:
    def __init__(self) -> None:
        self.session_id = str(uuid.uuid4())
        self.client_ip: Optional[str] = None
        self.sent: List[Dict[str, Any]] = []
        self.errors: List[Dict[str, Any]] = []

    async def send(self, data: Dict[str, Any]) -> None:
        self.sent.append(data)

    async def send_error(self, error: str, **kwargs: Any) -> None:
        msg = {"type": "error", "error": error, **kwargs}
        self.errors.append(msg)
        self.sent.append(msg)

    async def close(self, code: int = 1000) -> None:
        pass

    def next_client_version(self, html: Optional[str], rust_version: int) -> int:
        return rust_version

    def build_request(self) -> Optional[Any]:
        return None

    def on_view_mounted(self, view_instance: Any) -> None:
        pass

    @contextlib.asynccontextmanager
    async def event_context(self, view: Any):
        yield


class _Child(LiveView):
    exposure_policy = "explicit"
    sticky = True
    sticky_id = "child"
    template = "<div>x</div>"

    @event_handler(parameter_policy="strict")
    def pick(self, choice: str):
        pass


class _Component(LiveComponent):
    @event_handler(parameter_policy="strict")
    def pick(self, choice: str):
        pass


class _Parent(LiveView):
    exposure_policy = "explicit"
    template = '<div dj-root dj-id="0">p</div>'

    @event_handler(parameter_policy="strict")
    def pick(self, choice: str):
        pass


class _ParentWithChild(_Parent):
    """An explicit child is authorized by the mount that rendered it."""

    template = (
        '<div dj-root dj-id="0">p{% load live_tags %}'
        '{% live_render "djust.tests.test_missing_params_transports_4287._Child" sticky=True %}</div>'
    )


async def _runtime(view):
    """``view`` mounted on a hand-built runtime, as a transport mounts it."""
    from djust.tests._live_session import amount_on_runtime

    transport = _Transport()
    runtime = ViewRuntime(transport)
    await amount_on_runtime(runtime, view)
    transport.sent.clear()
    transport.errors.clear()
    return runtime, transport


def _parent():
    return _Parent()


async def _send(runtime, event, params):
    await runtime.dispatch_event({"type": "event", "event": event, "params": params, "ref": 1})


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_main_view_refusal_carries_missing_params():
    runtime, transport = await _runtime(_parent())
    await _send(runtime, "pick", {"choice": "a", "zz": "x"})
    assert transport.errors[-1]["validation_details"]["missing_params"] == []
    await _send(runtime, "pick", {})
    assert transport.errors[-1]["validation_details"]["missing_params"] == ["choice"]


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_embedded_child_refusal_carries_missing_params():
    runtime, transport = await _runtime(_ParentWithChild())
    await _send(runtime, "pick", {"view_id": "child"})
    assert transport.errors, transport.sent
    assert transport.errors[-1]["validation_details"]["missing_params"] == ["choice"]


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_component_refusal_carries_missing_params():
    parent = _parent()
    runtime, transport = await _runtime(parent)
    parent._components["comp-1"] = _Component(component_id="comp-1")
    await _send(runtime, "pick", {"component_id": "comp-1"})
    assert transport.errors, transport.sent
    assert transport.errors[-1]["validation_details"]["missing_params"] == ["choice"]
