"""#3116 (review of PR #3167): no turn-scoped ContextVar keeps a disconnected
session alive through a copy of the context.

The owner slots were only one of them. The per-event SQL capture scope
(``djust_sql_capture_scope``) held the view, and the explicit child-render
scope (``djust_explicit_child_render``) holds the view and its rendered
children for the length of a render. A task created in a handler, or a thread
started from the turn on Python 3.14+, keeps a copy of the context -- and with
it whatever those variables held.

The end-to-end cases drive a real ``LiveViewConsumer`` over a
``WebsocketCommunicator``: a handler keeps ``copy_context()`` or creates a
never-ending task, the client disconnects, and after ``gc.collect()`` the
consumer must be gone. A gate-off sibling holds the owner slots strongly again
and proves the probe can see a pinned consumer.
"""

from __future__ import annotations

import asyncio
import contextvars
import gc
import sys
import weakref

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth.models import AnonymousUser
from django.test import override_settings

from djust import LiveView
from djust import _exposure_diagnostics as ed
from djust.decorators import event_handler

HELD: list = []
TASKS: list = []


class PinView(LiveView):
    template = "<div dj-root><span>{{ n }}</span></div>"

    def mount(self, request, **kwargs):
        self.n = 0

    def get_context_data(self, **kwargs):
        return {"n": self.n}

    @event_handler()
    def keep_context(self, **kwargs):
        # What a thread started inside the turn keeps on 3.14+.
        HELD.append(contextvars.copy_context())
        self.n += 1

    @event_handler()
    async def start_task(self, **kwargs):
        async def forever():
            await asyncio.Event().wait()

        TASKS.append(asyncio.get_running_loop().create_task(forever()))
        self.n += 1


setattr(sys.modules[__name__], "PinView", PinView)


def _session():
    from django.contrib.sessions.backends.db import SessionStore

    store = SessionStore()
    store.save()
    return store


def _consumers():
    from djust.websocket import LiveViewConsumer

    return [o for o in gc.get_objects() if isinstance(o, LiveViewConsumer)]


async def _run_turn_then_disconnect(event: str):
    from channels.testing import WebsocketCommunicator

    from djust.websocket import LiveViewConsumer

    before = {id(c) for c in _consumers()}
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__], DEBUG=False):
        sock = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
        sock.scope.update(
            session=await sync_to_async(_session)(), user=AnonymousUser(), tenant=None
        )
        assert (await sock.connect())[0]
        await sock.receive_json_from(timeout=3)
        await sock.send_json_to({"type": "mount", "view": f"{__name__}.PinView", "url": "/p/"})
        assert (await sock.receive_json_from(timeout=3))["type"] == "mount"
        mine = [c for c in _consumers() if id(c) not in before]
        assert len(mine) == 1
        probe = weakref.ref(mine[0])
        del mine
        await sock.send_json_to({"type": "event", "event": event, "params": {}, "ref": 1})
        for _ in range(5):
            frame = await sock.receive_json_from(timeout=5)
            if frame.get("ref") == 1:
                break
        await sock.disconnect()
        del sock
    for _ in range(5):
        await asyncio.sleep(0)
        gc.collect()
    return probe


@pytest.fixture(autouse=True)
def _clear():
    HELD.clear()
    TASKS.clear()
    yield
    for task in TASKS:
        task.cancel()
    HELD.clear()
    TASKS.clear()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("event", ["keep_context", "start_task"])
async def test_a_context_copy_from_a_turn_does_not_keep_the_consumer(event):
    probe = await _run_turn_then_disconnect(event)
    assert HELD or TASKS, "the turn did not take a context copy"
    assert probe() is None, "a context copy taken in the turn kept the consumer alive"
    # The copy still works, with diagnostics restricted (the owner is gone).
    ctx = HELD[0] if HELD else TASKS[0].get_context()
    assert ctx.run(ed.diagnostics_allowed) is False


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_probe_sees_a_pinned_consumer(monkeypatch):
    """Gate-off sibling: strong owner slots pin the consumer, and the probe
    notices -- so the test above is not green because it cannot see a leak."""
    monkeypatch.setattr(ed, "_owner_ref", lambda container: lambda: container)
    probe = await _run_turn_then_disconnect("keep_context")
    assert probe() is not None


def test_the_explicit_child_render_scope_releases_the_view():
    """A context copied while an explicit view renders must not keep it."""
    from djust._child_rendering import render_view_with_diff

    class Explicit:
        exposure_policy = "explicit"
        _child_views: dict = {}

        def render_with_diff(self):
            HELD.append(contextvars.copy_context())
            return ("<div dj-root></div>", None, 1)

    view = Explicit()
    render_view_with_diff(view)
    assert HELD, "the render did not take a context copy"
    probe = weakref.ref(view)
    del view
    gc.collect()
    assert probe() is None, "the child-render scope in the copy kept the view alive"


def test_sql_params_are_redacted_once_the_scope_owner_is_gone():
    from djust.observability.sql import _active, _params_allowed, capture_for_event

    class Legacy:
        exposure_policy = "legacy"

    owner = Legacy()
    with capture_for_event(owner=owner):
        scope = _active.get()
        assert _params_allowed(scope) is True
    del owner
    gc.collect()
    assert _params_allowed(scope) is False
