"""#4095 review: the over-limit rollback on a live connection.

Through the real runtime (``ViewRuntime`` with a recording transport, real DB
sessions and saves):

1. the rollback runs ``handle_restore`` after putting the stored fields back,
   as the reconnect restore does (#3989): one shared function, pinned to its
   two callers;
2. what the refused handler queued for after its turn (``start_async`` work,
   push events, flash, page metadata, deferred callbacks, navigation) is
   discarded, not sent and not run by the next turn;
3. when an earlier turn's save was deferred and did not land, the store is
   behind the view: rolling back to it would silently drop that turn's
   change, so the refusal is the reload error and the pending catch-up is
   withdrawn.
"""

from __future__ import annotations

import ast
import asyncio
import pathlib
import threading

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory, override_settings

from djust import LiveView, event_handler
from djust._exposure import StateLimits
from djust._exposure_sessions import server_state_adapter
from djust.decorators import state
from djust.runtime import ViewRuntime
from djust.tests.test_runtime_state_save_tt_1894 import MockTransport

pytestmark = [pytest.mark.asyncio, pytest.mark.django_db(transaction=True)]

BIG = "x" * StateLimits().max_bytes  # alone over the limit
ROWS = {1: ["one-a", "one-b"], 2: ["two-a"]}
RAN = []


class Picker(LiveView):
    """``selected_id`` and ``notes`` are saved; ``rows`` is derived from the id."""

    exposure_policy = "explicit"
    template = "<div dj-root><b>{{ selected_id }}</b><i>{{ rows }}</i></div>"
    selected_id = state(1, persist="server")
    notes = state("", persist="server")

    def mount(self, request, **kwargs):
        self.rows = ROWS[self.selected_id]

    def handle_restore(self, request):
        self.rows = ROWS[self.selected_id]

    def get_context_data(self, **kwargs):
        return super().get_context_data(
            selected_id=self.selected_id, rows=",".join(self.rows), **kwargs
        )

    @event_handler()
    def choose(self, id: int = 1, big: bool = False, **kwargs):
        self.selected_id = id
        self.rows = ROWS[id]
        if big:
            self.notes = BIG

    @event_handler()
    def busy(self, big: bool = False, **kwargs):
        self.start_async(self._work)
        self.push_event("refused-push", {"n": 1})
        self.put_flash("info", "refused-flash")
        self.page_title = "refused-title"
        self.defer(RAN.append, "deferred")
        self.live_redirect("/refused-redirect/")
        if big:
            self.notes = BIG

    def _work(self):
        RAN.append("work")


@pytest.fixture(autouse=True)
def staged(monkeypatch):
    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)
    RAN.clear()
    with override_settings(DEBUG=False):
        yield


def _request(session_key=None):
    request = RequestFactory().get("/picker/")
    request.user = AnonymousUser()
    request.tenant = None
    request.session = SessionStore(session_key)
    if session_key is None:
        request.session.create()
    return request


async def _mount(request):
    transport = MockTransport()
    transport.build_request = lambda: request

    async def fresh_event_request(view):
        return await sync_to_async(_request)(request.session.session_key)

    transport.explicit_event_request = fresh_event_request
    runtime = ViewRuntime(transport)
    with override_settings(LIVEVIEW_ALLOWED_MODULES=["djust"]):
        await runtime.dispatch_mount(
            {"type": "mount", "view": __name__ + ".Picker", "url": request.path}
        )
    assert not transport.errors, transport.errors
    return runtime, transport


async def _event(runtime, name, **params):
    await runtime.dispatch_event({"type": "event", "event": name, "params": params})


async def _yield_to_the_loop():
    """Let fire-and-forget sends scheduled by the turn run (no wall clock)."""
    for _ in range(10):
        await asyncio.sleep(0)


async def _stored(key):
    adapter = await sync_to_async(server_state_adapter)(
        Picker(), await sync_to_async(_request)(key)
    )
    values = await adapter.aload()
    return None if values is None else (values["selected_id"], len(values["notes"]))


# --------------------------------------------------------------------------- #
# 1. handle_restore
# --------------------------------------------------------------------------- #


async def test_the_rollback_runs_handle_restore_so_derived_state_follows_the_old_id():
    request = await sync_to_async(_request)()
    runtime, transport = await _mount(request)
    view = runtime.view_instance
    await _event(runtime, "choose", id=1)  # saved: id 1
    assert await _stored(request.session.session_key) == (1, 0)

    transport.sent.clear()
    await _event(runtime, "choose", id=2, big=True)  # id 2, rows for 2, notes over the limit
    [refused] = transport.errors[-1:]
    assert refused["code"] == "state_too_large", refused
    # Back to the stored id, and rows rebuilt for it by handle_restore.
    assert view.selected_id == 1
    assert view.rows == ROWS[1], "rows still describe the refused id"
    assert await _stored(request.session.session_key) == (1, 0)


def test_the_restore_function_has_exactly_the_two_runtime_callers():
    """The reconnect restore and the rollback share ``apply_restored_state``,
    so they cannot drift; a third caller (or a second copy of the restore loop)
    must be reviewed."""
    source = (pathlib.Path(__file__).resolve().parents[1] / "runtime.py").read_text()
    tree = ast.parse(source)
    callers = set()
    for cls in (n for n in tree.body if isinstance(n, ast.ClassDef)):
        for function in cls.body:
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(function):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "apply_restored_state"
                ):
                    callers.add("%s.%s" % (cls.name, function.name))
    assert callers == {"ViewRuntime._dispatch_mount_body", "ViewRuntime._refuse_over_limit_turn"}


# --------------------------------------------------------------------------- #
# 2. queued effects of the refused handler
# --------------------------------------------------------------------------- #


def _record_dispatch(runtime, monkeypatch):
    """Names of background tasks the runtime dispatches, recorded when scheduled."""
    dispatched = []
    original = runtime._execute_async_task

    def record(name, *args, **kwargs):
        dispatched.append(name)
        return original(name, *args, **kwargs)

    monkeypatch.setattr(runtime, "_execute_async_task", record)
    return dispatched


async def test_what_the_refused_handler_queued_is_discarded(monkeypatch):
    request = await sync_to_async(_request)()
    runtime, transport = await _mount(request)
    view = runtime.view_instance
    dispatched = _record_dispatch(runtime, monkeypatch)
    await _event(runtime, "choose", id=1)

    transport.sent.clear()
    await _event(runtime, "busy", big=True)
    assert transport.errors[-1]["code"] == "state_too_large"
    assert not getattr(view, "_async_tasks", None)
    assert not view._pending_push_events
    assert not view._pending_navigation
    assert not view._drain_flash()
    assert not view._drain_page_metadata()
    assert not view._drain_deferred()

    # The next committing turn sends and runs none of it.
    await _event(runtime, "choose", id=2)
    assert dispatched == [], "the refused work was dispatched by the next turn"
    await _yield_to_the_loop()  # push events are sent fire-and-forget
    sent = repr(transport.sent)
    for refused in ("refused-push", "refused-flash", "refused-title", "/refused-redirect/"):
        assert refused not in sent, refused
    assert RAN == []
    assert await _stored(request.session.session_key) == (2, 0)


async def test_the_same_handler_under_the_limit_still_sends_its_effects(monkeypatch):
    """Control: the discard is the refusal's, not the handler's."""
    request = await sync_to_async(_request)()
    runtime, transport = await _mount(request)
    dispatched = _record_dispatch(runtime, monkeypatch)
    transport.sent.clear()
    await _event(runtime, "busy")
    await _yield_to_the_loop()
    sent = repr(transport.sent)
    for queued in ("refused-push", "refused-flash", "/refused-redirect/"):
        assert queued in sent, queued
    assert "deferred" in RAN
    assert len(dispatched) == 1, dispatched


# --------------------------------------------------------------------------- #
# 3. a deferred save that did not land
# --------------------------------------------------------------------------- #


async def test_an_unlanded_earlier_save_makes_the_refusal_the_reload_error(monkeypatch):
    """Turn N's save is deferred and then fails; turn N+1 goes over the limit.

    Rolling back to the store would put back the state before N, and the
    catch-up would then commit it: N's change lost although the user was told
    it would appear. The refusal is the reload error instead, N stays on the
    view, and the catch-up (which would only try the oversized value) is
    withdrawn. The store is held by a gate, never by elapsed time.
    """
    request = await sync_to_async(_request)()
    runtime, transport = await _mount(request)
    view = runtime.view_instance
    await _event(runtime, "choose", id=1)
    key = request.session.session_key
    assert await _stored(key) == (1, 0)

    loop = asyncio.get_running_loop()
    stand_in = loop.create_future()  # the scheduled catch-up, held

    def hold_catch_up(view):
        runtime._explicit_catch_up = stand_in

    monkeypatch.setattr(runtime, "_schedule_explicit_catch_up", hold_catch_up)

    release = threading.Event()
    original = SessionStore.save
    calls = []

    def first_save_blocks_then_fails(self, *args, **kwargs):
        calls.append(True)
        if len(calls) == 1:
            release.wait(timeout=30)
            raise OSError("store unavailable")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(SessionStore, "save", first_save_blocks_then_fails)
    with override_settings(DJUST_EXPLICIT_STATE_SAVE_TIMEOUT=0.05):
        # Turn N: deferred while the gate holds the store.
        await _event(runtime, "choose", id=2)
        assert transport.errors[-1]["code"] == "state_error"
        assert transport.errors[-1].get("transient") is True
        unlanded = runtime._explicit_unlanded
        assert unlanded is not None
        release.set()
        await asyncio.wait({unlanded}, timeout=30)
        assert unlanded.done() and unlanded.exception() is not None  # N did not land
        assert await _stored(key) == (1, 0)

        # Turn N+1: over the limit.
        await _event(runtime, "choose", id=2, big=True)

    final = transport.errors[-1]
    assert final["code"] == "state_error" and "reload" in final["error"].lower(), final
    assert view.selected_id == 2, "turn N's change was rolled back to the store"
    assert stand_in.cancelled(), "the catch-up was not withdrawn"


async def test_a_deferred_save_that_landed_lets_the_rollback_proceed(monkeypatch):
    """Control for 3: the store caught up, so going back to it loses nothing."""
    request = await sync_to_async(_request)()
    runtime, transport = await _mount(request)
    view = runtime.view_instance
    await _event(runtime, "choose", id=1)
    key = request.session.session_key

    monkeypatch.setattr(runtime, "_schedule_explicit_catch_up", lambda view: None)
    release = threading.Event()
    original = SessionStore.save
    calls = []

    def first_save_blocks(self, *args, **kwargs):
        calls.append(True)
        if len(calls) == 1:
            release.wait(timeout=30)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(SessionStore, "save", first_save_blocks)
    with override_settings(DJUST_EXPLICIT_STATE_SAVE_TIMEOUT=0.05):
        await _event(runtime, "choose", id=2)
        unlanded = runtime._explicit_unlanded
        release.set()
        await asyncio.wait({unlanded}, timeout=30)
        assert unlanded.exception() is None
        assert await _stored(key) == (2, 0)
        await _event(runtime, "choose", id=1, big=True)

    assert transport.errors[-1]["code"] == "state_too_large"
    assert view.selected_id == 2 and view.rows == ROWS[2]
