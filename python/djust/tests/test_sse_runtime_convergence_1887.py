"""End-to-end SSE-via-ViewRuntime integration tests (#1887, ADR-022 Iter 1).

Iter 1 converged the legacy bespoke SSE mount/event helpers
(``_sse_mount_view`` / ``_sse_handle_event``) onto the shared
``ViewRuntime.dispatch_mount`` / ``dispatch_event`` spine — the SAME path the
SSE ``/message/`` endpoint + the WS ``url_change`` shim already use — retiring
the SSE fork of the #1646 parallel-path-drift class.

These tests drive the REAL Django views (``DjustSSEStreamView.get`` +
``DjustSSEEventView.post``) end-to-end against a real-rendering LiveView, so they
exercise the actual converged code path (not a unit-level dispatch_* call):

* mount: GET stream → runtime.dispatch_mount → real render → ``mount`` frame +
  session registered (the 'mounted' gate is now ``runtime.view_instance``).
* event: ``/event/`` POST → runtime.dispatch_event → handler + render →
  ``patch`` / ``html_update`` frame.
* object-perm denial blocks the mount (Iter 0 #1885 check, now on the live SSE
  path) — with a gate-off witness proving the assertion is non-tautological.
* background work (``start_async`` / ``@background``): the biggest legacy delta —
  the runtime set the ``async_pending`` wire flag but had NO dispatcher before
  this PR, so converging without porting ``_sse_run_async_work`` would have
  silently broken background work for SSE consumers. Verified end-to-end + a
  gate-off witness (neuter ``_dispatch_async_work`` → the follow-up frame never
  arrives), per #1468.

The view is mounted against the REAL HTTP request (real ``request.user``),
preserved via ``SSESessionTransport.build_request`` reading ``session._request``.
"""

from __future__ import annotations

import asyncio
import json
import sys
from unittest.mock import patch

import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory, override_settings

from djust import LiveView
from djust.decorators import event_handler
from djust.sse import DjustSSEEventView, DjustSSEStreamView, _sse_sessions

pytestmark = pytest.mark.django_db

ALLOWED_ORIGIN = "https://example.com"


# --------------------------------------------------------------------------- #
# Real-rendering test views (inline template so the Rust render path runs).
# --------------------------------------------------------------------------- #


class _CounterSSEView(LiveView):
    """A minimal counter view used for the mount + event + async paths."""

    template = "<div dj-root><span>{{ count }}</span></div>"

    def mount(self, request, **kwargs):
        self.count = 0

    @event_handler()
    def increment(self, **kwargs):
        self.count += 1

    @event_handler()
    def begin_async(self, **kwargs):
        """Schedule background work that bumps the counter when it completes."""
        self.loading = True
        self.start_async(self._do_work)

    def _do_work(self):
        self.count = 99
        self.loading = False

    def get_context_data(self, **kwargs):
        return {"count": self.count}


class _DocSSEView(LiveView):
    """Object-scoped view: only doc_id == 1 is permitted (IDOR gate)."""

    template = "<div dj-root><main>{{ doc }}</main></div>"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.doc_id = 2  # default to the DENIED object

    def get_object(self):
        return type("Doc", (), {"id": self.doc_id})()

    def has_object_permission(self, request, obj):
        return obj.id == 1

    def mount(self, request, **kwargs):
        if "doc_id" in kwargs:
            self.doc_id = int(kwargs["doc_id"])

    def get_context_data(self, **kwargs):
        return {"doc": {1: "ACME (yours)", 2: "GLOBEX (NOT yours)"}[self.doc_id]}


def _register(view_cls):
    setattr(sys.modules[__name__], view_cls.__name__, view_cls)
    return f"{__name__}.{view_cls.__name__}"


def _allowlist():
    return override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__.split(".")[0]])


def _drain(session):
    msgs = []
    while not session.queue.empty():
        msg = session.queue.get_nowait()
        if msg is None:
            continue
        msgs.append(msg)
    return msgs


async def _mount_stream(view_path, *, query=""):
    """Drive the REAL GET-stream endpoint and return (response, session)."""
    sid = "11111111-1111-1111-1111-111111111111"
    _sse_sessions.pop(sid, None)
    rf = RequestFactory()
    q = f"?view={view_path}" + (f"&{query}" if query else "")
    request = rf.get(f"/djust/sse/{sid}/{q}", HTTP_ORIGIN=ALLOWED_ORIGIN)
    request.user = AnonymousUser()
    view = DjustSSEStreamView()
    response = await view.get(request, session_id=sid)
    return response, _sse_sessions.get(sid), sid


async def _post_event(session, sid, event, params=None):
    """Drive the REAL /event/ endpoint against an owner-bound session.

    Owner-binding (Finding #24) is an endpoint-level check, not part of the
    runtime convergence under test; bind the session to a known pk and stamp the
    event request's user with the same pk so the POST owns the session and
    reaches dispatch_event (mirrors _attach_owner in the other SSE test files).
    """
    from unittest.mock import MagicMock

    session._owner_user_pk = 7
    session._owner_session_key = None
    rf = RequestFactory()
    request = rf.post(
        f"/djust/sse/{sid}/event/",
        data=json.dumps({"event": event, "params": params or {}}),
        content_type="application/json",
        HTTP_ORIGIN=ALLOWED_ORIGIN,
    )
    request.user = MagicMock(is_authenticated=True, pk=7)
    view = DjustSSEEventView()
    return await view.post(request, session_id=sid)


# --------------------------------------------------------------------------- #
# Mount (GET stream) — end-to-end through the converged runtime.
# --------------------------------------------------------------------------- #


class TestSSEMountViaRuntime:
    @override_settings(ALLOWED_HOSTS=["example.com"])
    @pytest.mark.asyncio
    async def test_mount_renders_and_registers_session(self):
        path = _register(_CounterSSEView)
        with _allowlist():
            response, session, sid = await _mount_stream(path)

        assert session is not None, "successful mount must register the session"
        assert session.runtime.view_instance is not None
        msgs = _drain(session)
        mount_msgs = [m for m in msgs if m.get("type") == "mount"]
        assert mount_msgs, f"expected a mount frame, got {msgs!r}"
        assert "0" in mount_msgs[0]["html"], "initial counter render missing"
        # SSE-specific identity stamped via on_view_mounted hook.
        assert session.runtime.view_instance._sse_session_id == sid

    @override_settings(ALLOWED_HOSTS=["example.com"])
    @pytest.mark.asyncio
    async def test_mount_uses_real_request_user(self):
        """The runtime mounts against the REAL request.user (not a synthesized
        userless RequestFactory request) — build_request hook (#1887)."""
        path = _register(_CounterSSEView)
        with _allowlist():
            _resp, session, _sid = await _mount_stream(path)
        view = session.runtime.view_instance
        assert view is not None
        assert isinstance(view.request.user, AnonymousUser)


# --------------------------------------------------------------------------- #
# Event (/event/) — end-to-end through the converged runtime.
# --------------------------------------------------------------------------- #


class TestSSEEventViaRuntime:
    @override_settings(ALLOWED_HOSTS=["example.com"])
    @pytest.mark.asyncio
    async def test_event_updates_and_streams_frame(self):
        path = _register(_CounterSSEView)
        with _allowlist():
            _resp, session, sid = await _mount_stream(path)
        _drain(session)  # clear the mount frame

        response = await _post_event(session, sid, "increment")
        assert response.status_code == 200
        assert json.loads(response.content) == {"ok": True}

        msgs = _drain(session)
        render_frames = [m for m in msgs if m.get("type") in ("patch", "html_update")]
        assert render_frames, f"increment must stream a render frame, got {msgs!r}"
        assert session.runtime.view_instance.count == 1


# --------------------------------------------------------------------------- #
# Object-permission denial on the live SSE mount path (Iter 0 #1885 + gate-off).
# --------------------------------------------------------------------------- #


class TestSSEObjectPermViaRuntime:
    @override_settings(ALLOWED_HOSTS=["example.com"])
    @pytest.mark.asyncio
    async def test_denied_object_blocks_mount(self):
        path = _register(_DocSSEView)
        with _allowlist():
            _resp, session, _sid = await _mount_stream(path, query="doc_id=2")
        # Denied object: session not registered, error frame queued, no render.
        assert session is None, "denied object must NOT register a POST-routable session"

    @override_settings(ALLOWED_HOSTS=["example.com"])
    @pytest.mark.asyncio
    async def test_permitted_object_mounts(self):
        path = _register(_DocSSEView)
        with _allowlist():
            _resp, session, _sid = await _mount_stream(path, query="doc_id=1")
        assert session is not None, "permitted object wrongly denied over SSE"
        msgs = _drain(session)
        assert any("ACME" in m.get("html", "") for m in msgs if m.get("type") == "mount")

    @override_settings(ALLOWED_HOSTS=["example.com"])
    @pytest.mark.asyncio
    async def test_gate_off_denied_object_would_render(self):
        """Gate-off witness (#1468): neuter the object-perm enforcement and the
        denied object now mounts/renders — proving the deny assertion above is
        non-tautological (the check is what blocks it)."""
        path = _register(_DocSSEView)
        with (
            _allowlist(),
            patch("djust.auth.core.enforce_object_permission", lambda view, request: None),
        ):
            _resp, session, _sid = await _mount_stream(path, query="doc_id=2")
        assert session is not None, (
            "gate-off must let the denied object mount (else the deny test is a tautology)"
        )
        msgs = _drain(session)
        assert any("GLOBEX" in m.get("html", "") for m in msgs if m.get("type") == "mount"), (
            "with the object-perm gate off, the denied object should render"
        )


# --------------------------------------------------------------------------- #
# Background work (start_async) over SSE — the biggest legacy delta (#1887).
# --------------------------------------------------------------------------- #


class TestSSEAsyncWorkViaRuntime:
    @override_settings(ALLOWED_HOSTS=["example.com"])
    @pytest.mark.asyncio
    async def test_start_async_streams_followup_frame(self):
        """An event scheduling ``start_async`` must run the background task
        off-thread and stream a follow-up render frame when it completes.

        The runtime had NO async dispatcher before #1887 — it set the
        ``async_pending`` wire flag but never dispatched, so converging SSE onto
        it without porting the dispatcher would have silently dropped all
        ``start_async`` / ``@background`` work for SSE consumers. (The legacy SSE
        path itself only dispatched the unused ``_async_pending`` single-task
        format; ``start_async`` populates ``_async_tasks``, which the converged
        runtime dispatches unconditionally, matching WS handle_event — so this
        also FIXES a latent legacy-SSE drop, #1646.)"""
        path = _register(_CounterSSEView)
        with _allowlist():
            _resp, session, sid = await _mount_stream(path)
        _drain(session)

        response = await _post_event(session, sid, "begin_async")
        assert response.status_code == 200
        _drain(session)  # the immediate event frame

        # Let the fire-and-forget background task run (sync_to_async threadpool
        # round-trip) AND its follow-up render+send complete. Poll the queue for
        # the follow-up frame (the task may set count before its render finishes).
        followup = []
        for _ in range(200):
            await asyncio.sleep(0.01)
            followup += _drain(session)
            if any(m.get("type") in ("patch", "html_update") for m in followup):
                break

        assert session.runtime.view_instance.count == 99, "background task did not run"
        assert any(m.get("type") in ("patch", "html_update") for m in followup), (
            f"background completion must stream a follow-up render frame, got {followup!r}"
        )

    @override_settings(ALLOWED_HOSTS=["example.com"])
    @pytest.mark.asyncio
    async def test_gate_off_async_work_not_dispatched(self):
        """Gate-off witness (#1468): neuter the runtime async dispatcher and the
        background task never runs / never streams a follow-up — proving the test
        above asserts on the new dispatch path, not a coincidence."""
        path = _register(_CounterSSEView)
        with _allowlist():
            _resp, session, sid = await _mount_stream(path)
        _drain(session)

        with patch("djust.runtime.ViewRuntime._dispatch_async_work", lambda self, ev: None):
            response = await _post_event(session, sid, "begin_async")
            assert response.status_code == 200
            _drain(session)
            for _ in range(20):
                await asyncio.sleep(0)

        assert session.runtime.view_instance.count != 99, (
            "with the async dispatcher gated off, the background task must NOT have run"
        )
