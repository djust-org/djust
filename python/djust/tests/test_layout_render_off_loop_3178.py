"""#3178 — a ``set_layout`` swap renders off the event-loop thread.

``_flush_pending_layout`` (the consumer's and the runtime's twin) ran
``view.get_context_data()`` and ``render_to_string`` on the loop thread. Both
now run in ONE ``sync_to_async`` hop, like every other render path: on the
session's pinned worker thread when ``LIVEVIEW_CONFIG["worker_threads"]`` is
on, else on asgiref's shared thread-sensitive thread.
"""

from __future__ import annotations

import threading
import uuid
from typing import Any, Dict, List
from unittest.mock import AsyncMock

import pytest
from asgiref.sync import sync_to_async

from djust import config as djust_config
from djust.mixins.layout import LayoutMixin


class _LayoutView(LayoutMixin):
    def __init__(self) -> None:
        super().__init__()
        self.context_threads: List[int] = []

    def get_context_data(self, **kwargs: Any) -> Dict[str, Any]:
        self.context_threads.append(threading.get_ident())
        return {"who": "layout"}


class _Transport:
    def __init__(self) -> None:
        self.session_id = str(uuid.uuid4())
        self.client_ip = None
        self.sent: List[Dict[str, Any]] = []

    async def send(self, data: Dict[str, Any]) -> None:
        self.sent.append(data)

    async def send_error(self, error: str, **kwargs: Any) -> None:
        self.sent.append({"type": "error", "error": error, **kwargs})


def _consumer(view: _LayoutView):
    from djust.websocket import LiveViewConsumer

    consumer = LiveViewConsumer.__new__(LiveViewConsumer)
    consumer.view_instance = view
    consumer.send_json = AsyncMock()

    def frames() -> List[Dict[str, Any]]:
        return [c.args[0] for c in consumer.send_json.await_args_list]

    return consumer, frames


def _runtime(view: _LayoutView):
    from djust.runtime import ViewRuntime

    transport = _Transport()
    runtime = ViewRuntime(transport)
    runtime.view_instance = view
    return runtime, lambda: transport.sent


@pytest.fixture
def render_threads(monkeypatch):
    threads: List[int] = []

    def fake_render_to_string(path: str, context: Dict[str, Any]) -> str:
        threads.append(threading.get_ident())
        return "<body>" + path + ":" + context["who"] + "</body>"

    monkeypatch.setattr("django.template.loader.render_to_string", fake_render_to_string)
    return threads


@pytest.mark.asyncio
@pytest.mark.parametrize("make", [_consumer, _runtime], ids=["consumer", "runtime"])
async def test_layout_render_runs_off_the_loop_thread(make, render_threads):
    loop_thread = threading.get_ident()
    view = _LayoutView()
    view.set_layout("layouts/app.html")
    owner, frames = make(view)

    await owner._flush_pending_layout()

    assert frames() == [
        {
            "type": "layout",
            "path": "layouts/app.html",
            "html": "<body>layouts/app.html:layout</body>",
        }
    ]
    assert len(view.context_threads) == 1 and len(render_threads) == 1
    # Neither the context build nor the template render ran on the loop.
    assert view.context_threads[0] != loop_thread
    assert render_threads[0] != loop_thread
    # One hop: both on the same thread, the one thread-sensitive
    # sync_to_async calls from this context use.
    reference = await sync_to_async(threading.get_ident)()
    assert view.context_threads[0] == render_threads[0] == reference


@pytest.mark.asyncio
@pytest.mark.parametrize("make", [_consumer, _runtime], ids=["consumer", "runtime"])
async def test_layout_render_uses_the_sessions_pinned_worker(make, render_threads, monkeypatch):
    """With ``worker_threads`` on, the layout renders on the session's pinned
    pool thread — the thread every other hop of the session uses."""
    from djust import worker_pool

    monkeypatch.setitem(djust_config.config._config, "worker_threads", 2)
    monkeypatch.setattr(worker_pool, "_pool", [])
    binding = worker_pool.bind_session()
    assert binding is not None
    try:
        pinned = await sync_to_async(threading.get_ident)()
        view = _LayoutView()
        view.set_layout("layouts/app.html")
        owner, _frames = make(view)
        await owner._flush_pending_layout()
    finally:
        binding.release()
    assert view.context_threads == [pinned]
    assert render_threads == [pinned]
    assert pinned != threading.get_ident()
