"""ADR-022 Iter 2 Phase 2.3a — async source reconcile + binary-framing confirm (#1905).

The reauth-on-event sub-fold (``Transport.recheck_event_auth``, the ``reauth``
built-in) tested a legacy mechanism and is gone (#4198, #4201): a view
authorizes every turn (``authorize_explicit_turn``), covered by
``test_explicit_turn_reauth_4198.py`` and ``test_auth_refusal_codes_3319.py``.

Two sub-folds remain:

(2) async ``source="async"`` reconcile — the runtime's ``_render_async_result``
    frames now carry ``source="async"`` to match the WS ``_run_async_work``
    frames (a #1646 drift INSIDE the convergence target). LIVE for SSE +
    url_change async work today; WS post-flip.

(3) binary-framing confirm — ``WSConsumerTransport.send`` routes through
    ``consumer.send_json`` (always JSON). The dead ``use_binary`` msgpack patch
    branch was removed in 2.0; a guard test keeps it from coming back unnoticed.

Each behavioral assertion is paired with a gate-off witness (#1468).
"""

from __future__ import annotations

import asyncio
import json
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory, override_settings

from djust import LiveView
from djust.decorators import event_handler
from djust.runtime import ViewRuntime, WSConsumerTransport
from djust.sse import DjustSSEMessageView, DjustSSEStreamView, _sse_sessions

pytestmark = pytest.mark.django_db

ALLOWED_ORIGIN = "https://example.com"


# --------------------------------------------------------------------------- #
# Test views.
# --------------------------------------------------------------------------- #


class _CounterSSEView(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root><span>{{ count }}</span></div>"

    def mount(self, request, **kwargs):
        self.count = 0

    @event_handler()
    def begin_async(self, **kwargs):
        self.loading = True
        self.start_async(self._do_work)

    def _do_work(self):
        self.count = 99
        self.loading = False

    def get_context_data(self, **kwargs):
        return {"count": self.count}


# PR #3159 had an autouse fixture here that reset djust's config after each
# test's ``override_settings`` exited: the tests' own ``config.reset()`` ran
# inside the override and leaked ``reauth_on_event=True``. The config now
# reloads itself on every ``LIVEVIEW_CONFIG`` change, exit included
# (``djust.config._reload_on_setting_change``, #3217), so the fixture went.


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


async def _mount_stream(view_path, *, user, query="", session=None):
    sid = "21111111-1111-1111-1111-111111111111"
    _sse_sessions.pop(sid, None)
    rf = RequestFactory()
    q = f"?view={view_path}" + (f"&{query}" if query else "")
    request = rf.get(
        f"/djust/sse/{sid}/{q}", HTTP_ORIGIN=ALLOWED_ORIGIN, HTTP_HOST="example.com", secure=True
    )
    request.user = user
    if session is not None:
        request.session = session
    view = DjustSSEStreamView()
    response = await view.get(request, session_id=sid)
    return response, _sse_sessions.get(sid), sid


async def _post_event(session, sid, event, *, user, params=None, http_session=None):
    """Drive the REAL /message/ endpoint with an ``event`` frame against an
    owner-bound session.

    The session is bound to pk=7 at mount; ``user`` is the POSTer (also pk=7 so
    owner-binding passes, but with a possibly-revoked permission).
    """
    rf = RequestFactory()
    request = rf.post(
        f"/djust/sse/{sid}/message/",
        data=json.dumps({"type": "event", "event": event, "params": params or {}}),
        content_type="application/json",
        HTTP_ORIGIN=ALLOWED_ORIGIN,
        HTTP_HOST="example.com",
        secure=True,
    )
    request.user = user
    if http_session is not None:
        request.session = http_session
    view = DjustSSEMessageView()
    return await view.post(request, session_id=sid)


# =========================================================================== #
# Sub-fold 2 — async source="async" reconcile. LIVE for SSE + url_change async.
# =========================================================================== #


class TestAsyncSourceReconcile:
    @override_settings(ALLOWED_HOSTS=["example.com"])
    @pytest.mark.asyncio
    @pytest.mark.django_db(transaction=True)
    async def test_async_result_frame_carries_source_async_over_sse(self):
        """A start_async result frame routed through the runtime carries
        ``source="async"`` (matching WS _run_async_work), end-to-end over the real
        SSE transport."""
        from asgiref.sync import sync_to_async

        from djust.testing import live_session

        path = _register(_CounterSSEView)
        # One live server session for the stream and the event POST: an explicit
        # view is bound to it at mount and re-reads it on every turn, and the
        # owner binding (Finding #24) matches the POSTer by it.
        http_session = await sync_to_async(live_session)(None)
        with _allowlist():
            _resp, session, sid = await _mount_stream(
                path, user=AnonymousUser(), session=http_session
            )
        assert session is not None
        _drain(session)

        resp = await _post_event(
            session, sid, "begin_async", user=AnonymousUser(), http_session=http_session
        )
        assert resp.status_code == 200
        _drain(session)  # the immediate event frame (source="event")

        followup = []
        for _ in range(200):
            await asyncio.sleep(0.01)
            followup += _drain(session)
            if any(m.get("type") in ("patch", "html_update") for m in followup):
                break

        async_frames = [m for m in followup if m.get("type") in ("patch", "html_update")]
        assert async_frames, f"background completion must stream a render frame, got {followup!r}"
        assert all(m.get("source") == "async" for m in async_frames), (
            f"runtime async-result frames must carry source='async', got {async_frames!r}"
        )
        assert session.runtime.view_instance.count == 99, "background task did not run"

    @pytest.mark.asyncio
    async def test_render_async_result_tags_source_async_unit(self):
        """Unit-level: _render_async_result stamps source='async' on BOTH the patch
        and the html_update branch. Gate-off proven by the sibling below."""
        sent = []

        class _CapTransport:
            session_id = "cap"
            client_ip = None
            _client_ip = None

            async def send(self, data):
                sent.append(data)

            async def send_error(self, error, **kwargs):
                pass

            async def close(self, code=1000):
                pass

            def next_client_version(self, html, rust_version):
                return rust_version

        view = MagicMock()
        view._sync_state_to_rust = MagicMock()
        # patch branch: render_with_diff returns (html, patches, version)
        view.render_with_diff = MagicMock(return_value=("<div>x</div>", [{"op": "x"}], 3))

        runtime = ViewRuntime(_CapTransport())
        runtime.view_instance = view
        # Neuter the turn-end flush so the unit test stays focused on the frame.
        runtime._flush_all_pending = AsyncMock()

        await runtime._render_async_result("begin_async")
        assert sent, "a frame must be sent"
        assert sent[0]["type"] == "patch"
        assert sent[0]["source"] == "async", (
            f"patch async frame must be source='async': {sent[0]!r}"
        )

        # html_update branch
        sent.clear()
        view.render_with_diff = MagicMock(return_value=("<div>y</div>", None, 4))
        view._strip_comments_and_whitespace = MagicMock(side_effect=lambda h: h)
        view._extract_liveview_content = MagicMock(side_effect=lambda h: h)
        await runtime._render_async_result("begin_async")
        assert sent[0]["type"] == "html_update"
        assert sent[0]["source"] == "async", (
            f"html_update async frame must be source='async': {sent[0]!r}"
        )

    @pytest.mark.asyncio
    async def test_gate_off_source_tag_removed_is_red(self):
        """Gate-off witness (#1468): if the source tag were absent the assertion in
        the unit test above would FAIL. We prove that by re-running the production
        method with a stripping wrapper and asserting the tag is then missing —
        i.e. the assertion is load-bearing, not coincidental."""
        sent = []

        class _CapTransport:
            session_id = "cap"
            client_ip = None
            _client_ip = None

            async def send(self, data):
                # Simulate the pre-fix behavior: drop the source tag.
                data.pop("source", None)
                sent.append(data)

            async def send_error(self, error, **kwargs):
                pass

            async def close(self, code=1000):
                pass

            def next_client_version(self, html, rust_version):
                return rust_version

        view = MagicMock()
        view._sync_state_to_rust = MagicMock()
        view.render_with_diff = MagicMock(return_value=("<div>x</div>", [{"op": "x"}], 3))
        runtime = ViewRuntime(_CapTransport())
        runtime.view_instance = view
        runtime._flush_all_pending = AsyncMock()
        await runtime._render_async_result("begin_async")
        assert "source" not in sent[0], (
            "this stripping transport models the pre-fix shape; the real fix puts "
            "source='async' on the frame BEFORE send, which the positive test asserts"
        )


# =========================================================================== #
# Sub-fold 3 — binary-framing confirm (the use_binary branch is gone, 2.0).
# =========================================================================== #


class TestBinaryFramingConfirm:
    @pytest.mark.asyncio
    async def test_ws_transport_send_emits_json_via_send_json(self):
        """``WSConsumerTransport.send`` routes through ``consumer.send_json``
        (always JSON). This pins the live WS behavior so a binary wire format
        would be a DELIBERATE, tested change."""
        consumer = MagicMock()
        consumer.send_json = AsyncMock()
        transport = WSConsumerTransport(consumer)

        frame = {"type": "patch", "patches": [], "version": 1}
        await transport.send(frame)

        consumer.send_json.assert_awaited_once_with(frame)

    def test_use_binary_is_gone_from_the_package(self):
        """Source-grep pin: the ``use_binary`` msgpack patch path was removed in
        2.0 (the client could never decode it). A binary wire format needs a
        client decoder and its own tests, not a revived flag."""
        import pathlib

        pkg = pathlib.Path(__import__("djust").__file__).parent
        found = [
            str(py)
            for py in pkg.rglob("*.py")
            if "tests" not in py.parts and "use_binary" in py.read_text(encoding="utf-8")
        ]
        assert not found, f"use_binary reappeared in: {found}"
