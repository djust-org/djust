"""ADR-038 E6-1: measured cost of ``exposure_policy="explicit"`` vs ``"legacy"``.

Measurement only. There are deliberately NO pass/fail latency thresholds here:
ADR-038 E6 asks for evidence before activation, not a new SLA, and the
conftest docstring (#2156) records how local latency thresholds flake. Each
test asserts only that the path it measures actually ran (correct frame type,
rendered value present), so a number cannot come from a short-circuit.

The same view logic is measured under both policies:

* ``LegacyCostView`` keeps state in plain attributes and relies on legacy
  automatic context, snapshots and ``liveview_<path>`` session persistence.
* ``ExplicitCostView`` declares the same fields with ``state(persist="server")``
  and supplies render context explicitly from ``get_context_data``.

Explicit mode is still behind the constructor guard; the ``staged`` fixture
bypasses it the same way the ``python/djust/tests/test_exposure_*`` suites do.

Segments (group names):
  exposure_http        HTTP GET through ``View.as_view()`` with a DB session.
  exposure_ws_mount    connect + mount + disconnect via the real consumer.
  exposure_ws_event    one event -> handler -> re-render -> reply frame.
  exposure_render_cache  renderer setup + first render on a repeated mount:
                       legacy warm cache hit, legacy cold miss, explicit.
  exposure_server_state  ``ServerStateSession`` save / load of a 50-row payload.

Run: ``make benchmark-python`` or
``.venv/bin/python -m pytest tests/benchmarks/test_exposure_cost.py --benchmark-only``.
Results are recorded in docs/adr/notes/038-cost-measurement.md.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List

import pytest

from djust import LiveView, event_handler
from djust.decorators import state

TEMPLATE = """<div dj-root dj-view="{view}">
  <h1>{{{{ title }}}}</h1>
  <p class="count">Count: {{{{ count }}}}</p>
  <button dj-click="increment">+</button>
  <table>
    {{% for row in items %}}
    <tr id="row-{{{{ row.id }}}}"><td>{{{{ row.sku }}}}</td><td>{{{{ row.name }}}}</td>
      <td>{{{{ row.price }}}}</td><td>{{{{ row.qty }}}}</td></tr>
    {{% endfor %}}
  </table>
</div>"""

PATH = "/exposure-cost/"


def make_items(n: int = 50) -> List[Dict[str, Any]]:
    """A realistic list payload: 50 rows, mixed primitive fields, ~6 KB as JSON."""
    return [
        {
            "id": i,
            "sku": f"SKU-{i:05d}",
            "name": f"Catalogue item number {i}",
            "price": round(i * 3.75, 2),
            "qty": i % 7,
            "active": i % 3 != 0,
            "tags": ["alpha", "beta", f"t{i % 5}"],
        }
        for i in range(n)
    ]


class LegacyCostView(LiveView):
    exposure_policy = "legacy"
    template = TEMPLATE.format(view=f"{__name__}.LegacyCostView")

    def mount(self, request, **kwargs):
        self.title = "Inventory"
        self.count = 0
        self.items = make_items()

    @event_handler()
    def increment(self, **kwargs):
        self.count += 1


class ExplicitCostView(LiveView):
    exposure_policy = "explicit"
    template = TEMPLATE.format(view=f"{__name__}.ExplicitCostView")
    title = state("", persist="server")
    count = state(0, persist="server")
    items = state(default_factory=list, persist="server")

    def mount(self, request, **kwargs):
        self.title = "Inventory"
        self.count = 0
        self.items = make_items()

    def get_context_data(self, **kwargs):
        return super().get_context_data(
            title=self.title, count=self.count, items=self.items, **kwargs
        )

    @event_handler()
    def increment(self, **kwargs):
        self.count += 1


VIEWS = {"legacy": LegacyCostView, "explicit": ExplicitCostView}


@pytest.fixture
def staged(monkeypatch):
    """Bypass ONLY the constructor guard, as the exposure test suites do."""
    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)


def _request(session, method: str = "get"):
    from django.contrib.auth.models import AnonymousUser
    from django.test import RequestFactory

    request = getattr(RequestFactory(), method)(PATH)
    request.session = session
    request.user = AnonymousUser()
    request.tenant = None
    return request


def _new_session():
    from django.contrib.sessions.backends.db import SessionStore

    session = SessionStore()
    session.create()
    return session


# ---------------------------------------------------------------------------
# HTTP GET
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.benchmark(group="exposure_http")
@pytest.mark.parametrize("policy", ["legacy", "explicit"])
def test_http_get(benchmark, staged, policy):
    """Full GET through the Django view: mount, context, render, persistence.

    The same session is reused across rounds (a page reload), so legacy pays
    its ``liveview_<path>`` session write and explicit its envelope write.
    """
    from django.contrib.sessions.backends.db import SessionStore

    view = VIEWS[policy].as_view()
    key = _new_session().session_key

    def _get():
        return view(_request(SessionStore(key)))

    response = benchmark(_get)
    assert response.status_code == 200
    assert b"SKU-00049" in response.content and b"Count: 0" in response.content


# ---------------------------------------------------------------------------
# WebSocket: mount and event
# ---------------------------------------------------------------------------


@contextmanager
def _ws_settings() -> Iterator[None]:
    """Allow this module's views, lift the event rate limit, drop tenants.

    The demo settings configure a header tenant resolver, and a WS-synthesized
    request carries no tenant, so explicit request binding would (correctly)
    refuse it. The default token bucket (100/s, burst 20) would throttle a
    benchmark loop, which measures the limiter, not the policy. Both policies
    run under the same settings.
    """
    from django.conf import settings
    from django.test import override_settings

    from djust.config import config as djust_config

    config = {k: v for k, v in settings.DJUST_CONFIG.items() if not k.startswith("TENANT_")}
    liveview = dict(getattr(settings, "LIVEVIEW_CONFIG", {}))
    liveview["rate_limit"] = {"rate": 10**6, "burst": 10**6, "max_warnings": 10**6}
    try:
        with override_settings(
            LIVEVIEW_ALLOWED_MODULES=[__name__],
            DEBUG=False,
            DJUST_CONFIG=config,
            DJUST_TENANTS={},
            LIVEVIEW_CONFIG=liveview,
        ):
            djust_config.reset()
            yield
    finally:
        djust_config.reset()  # reload the unmodified project settings


async def _connect(session_key: str, policy: str):
    from channels.testing import WebsocketCommunicator
    from django.contrib.auth.models import AnonymousUser
    from django.contrib.sessions.backends.db import SessionStore

    from djust.websocket import LiveViewConsumer

    comm = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    comm.scope["session"] = SessionStore(session_key)
    comm.scope["user"] = AnonymousUser()
    connected, _ = await comm.connect()
    assert connected
    await comm.receive_json_from(timeout=5)  # connect ack
    await comm.send_json_to(
        {"type": "mount", "view": f"{__name__}.{VIEWS[policy].__name__}", "url": PATH}
    )
    frame = await comm.receive_json_from(timeout=5)
    assert frame["type"] == "mount", frame
    return comm, frame


@pytest.mark.django_db(transaction=True)
@pytest.mark.benchmark(group="exposure_ws_mount")
@pytest.mark.parametrize("policy", ["legacy", "explicit"])
def test_ws_mount(benchmark, staged, policy):
    """Connect + mount + disconnect on the same session (a reconnect/reload).

    Repeating on one session is the case where legacy may reuse its shared
    render cache and explicit rebuilds its renderer; see the render_cache group
    for that component in isolation.
    """
    pytest.importorskip("channels")
    key = _new_session().session_key

    async def _once() -> Dict[str, Any]:
        comm, frame = await _connect(key, policy)
        await comm.disconnect()
        return frame

    def _run() -> Dict[str, Any]:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(_once())
        finally:
            loop.close()

    with _ws_settings():
        frame = benchmark(_run)
    assert "SKU-00049" in frame["html"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.benchmark(group="exposure_ws_event")
@pytest.mark.parametrize("policy", ["legacy", "explicit"])
def test_ws_event(benchmark, staged, policy):
    """One ``increment`` event with re-render, over one open socket.

    Explicit events additionally reload session authentication and save the
    declared server state; legacy events do what legacy does today.
    """
    pytest.importorskip("channels")
    key = _new_session().session_key
    loop = asyncio.new_event_loop()
    replies: List[str] = []

    async def _event() -> Dict[str, Any]:
        await comm.send_json_to({"type": "event", "event": "increment", "params": {}})
        while True:
            frame = await comm.receive_json_from(timeout=5)
            if frame.get("type") in {"patch", "html_update", "error"}:
                replies.append(frame["type"])
                return frame

    with _ws_settings():
        comm, _ = loop.run_until_complete(_connect(key, policy))
        try:
            frame = benchmark(lambda: loop.run_until_complete(_event()))
        finally:
            loop.run_until_complete(comm.disconnect())
            loop.close()
    assert frame["type"] in {"patch", "html_update"}, frame
    assert "error" not in replies
    if policy == "explicit":
        # Prove the explicit event path persisted the declared state each time.
        from django.contrib.sessions.backends.db import SessionStore

        from djust._exposure_sessions import load_server_state

        restored = load_server_state(ExplicitCostView(), _request(SessionStore(key)))
        assert restored is not None and restored["count"] == len(replies)


# ---------------------------------------------------------------------------
# Lost shared render-cache reuse on repeated mount
# ---------------------------------------------------------------------------


@pytest.fixture
def memory_backend(monkeypatch):
    from djust.state_backends.memory import InMemoryStateBackend

    backend = InMemoryStateBackend()
    monkeypatch.setattr("djust.state_backend.get_backend", lambda: backend)
    return backend


@pytest.mark.benchmark(group="exposure_render_cache")
@pytest.mark.parametrize("case", ["legacy_warm", "legacy_cold", "explicit"])
def test_repeated_mount_renderer(benchmark, staged, memory_backend, rf, case):
    """Renderer setup + first render for a second mount of the same page.

    ``legacy_warm``: the shared backend already holds this session+path's
    RustLiveView (the reuse legacy gets on reconnect). ``legacy_cold``: same
    code with the backend emptied first (a cache miss). ``explicit``: never
    consults the shared backend and always builds its own renderer.
    """
    from types import SimpleNamespace

    policy = "explicit" if case == "explicit" else "legacy"
    context = {"title": "Inventory", "count": 0, "items": make_items()}

    def _mount_render():
        view = VIEWS[policy]()
        request = rf.get(PATH)
        request.session = SimpleNamespace(session_key="bench-session")
        view._websocket_session_id = "bench-session"
        view._initialize_rust_view(request)
        view._rust_view.update_state(context)
        return view._rust_view.render()

    _mount_render()  # prime: legacy stores its renderer in the backend
    if case == "legacy_warm":
        assert memory_backend._cache, "legacy warm case must hit a populated cache"

    def _setup():
        if case == "legacy_cold":
            memory_backend._cache.clear()
        return (), {}

    html = benchmark.pedantic(_mount_render, setup=_setup, rounds=200, warmup_rounds=5)
    assert "SKU-00049" in html
    if case == "explicit":
        assert memory_backend._cache == {}


# ---------------------------------------------------------------------------
# Explicit server-state envelope I/O
# ---------------------------------------------------------------------------


def _adapter(engine: str):
    from django.contrib.sessions.backends.cache import SessionStore as CacheSession
    from django.contrib.sessions.backends.db import SessionStore as DBSession

    from djust._exposure import ExposureContract
    from djust._exposure_sessions import ServerStateSession, request_binding

    session = (DBSession if engine == "db" else CacheSession)()
    session.create()
    request = _request(session)
    contract = ExposureContract.from_view_class(ExplicitCostView)
    return ServerStateSession(session, contract, request_binding(request))


VALUES = {"title": "Inventory", "count": 3, "items": make_items()}


@pytest.mark.django_db
@pytest.mark.benchmark(group="exposure_server_state")
@pytest.mark.parametrize("engine", ["db", "cache"])
def test_server_state_save(benchmark, staged, engine):
    """Capture (bounded clone) + envelope + session write, 50-row payload."""
    adapter = _adapter(engine)
    benchmark(adapter.save, VALUES)
    assert adapter.load() == VALUES


@pytest.mark.django_db
@pytest.mark.benchmark(group="exposure_server_state")
@pytest.mark.parametrize("engine", ["db", "cache"])
def test_server_state_load(benchmark, staged, engine):
    """Fresh session read + full envelope validation, 50-row payload."""
    from djust._exposure_sessions import ServerStateSession

    adapter = _adapter(engine)
    adapter.save(VALUES)
    session_class = type(adapter.session)
    key = adapter.session.session_key

    def _load():
        # A fresh store per round: SessionStore caches its first read.
        fresh = ServerStateSession(session_class(key), adapter.contract, adapter.binding)
        return fresh.load()

    assert benchmark(_load) == VALUES
