"""Tests for #1471 / ADR-018 iter 18a — sticky-child state persistence: SAVE side.

ADR-018 (``docs/adr/018-sticky-child-state-persistence.md``) closes the gap
where a sticky-child LiveView's event-driven state is silently lost across a
WebSocket reconnect. Iteration 18a is the SAVE side + key scheme + GC index:

* **Decision 1** — per-child session key
  ``liveview_<parent_path>__sticky__<sticky_id>`` (+ ``__private``), keyed on
  the stable ``sticky_id`` class attr, never the volatile ``_view_id``.
* **Decision 3** — the parent render writes
  ``liveview_<parent_path>__sticky_ids`` as a GC ledger; the save path prunes
  orphaned ``__sticky__*`` entries no longer in the ledger.
* **Decision 4** — generalize the ``websocket.py`` save-block gate so a
  sticky-child event saves the child under its stable key; mirror on the
  HTTP path.
* **Decision 5** — persist a sticky child only when BOTH the child and its
  parent have ``enable_state_snapshot = True``.

This file is the regression suite for 18a. The LOAD/restore side (iter 18b)
is NOT exercised here.

The test below (``test_ws_sticky_child_event_writes_state_under_stable_key``)
is the FAILING-TEST contract written FIRST per Action #1210: against current
``main`` the WS save block is gated ``target_view is self.view_instance`` so
a sticky-child event (where ``target_view`` is the child) is skipped entirely
— the assertion that the session contains ``liveview_/dash/__sticky__counter``
fails.

Harness note: the test drives the real ``LiveViewConsumer.handle_event``
through the ``_FakeConsumer`` shim (the same shim
``tests/integration/test_sticky_redirect_flow.py`` uses) and reads a REAL
``SessionStore`` afterwards. This is non-tautological per Action #1200/#1468:
the assertion fails if the save block is reverted, and gating the new
sticky-child branch off makes it fail.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

import pytest
from asgiref.sync import sync_to_async
from django.contrib.sessions.backends.db import SessionStore
from django.test import override_settings

from djust import LiveView
from djust.decorators import event_handler
from djust.websocket import LiveViewConsumer


# ---------------------------------------------------------------------------
# Module-scope views — resolvable by dotted path for {% live_render %}.
# ---------------------------------------------------------------------------


class _StickyCounterChild(LiveView):
    """A sticky child with a publicly-mutable counter, opted into snapshot.

    Also carries a user-defined ``_note`` private attr so the suite can
    exercise the ``__private`` save key (ADR-018 Decision 1).
    """

    sticky = True
    sticky_id = "counter"
    enable_state_snapshot = True
    template = "<div><button dj-click='increment'>Counter: {{ count }}</button></div>"

    def mount(self, request, **kwargs):
        self.count = 0
        self._note = "fresh"
        # Register the private attr so _get_private_state() persists it.
        self._user_private_keys.add("_note")

    @event_handler()
    def increment(self, **kwargs):
        self.count += 1
        self._note = "incremented"

    def get_context_data(self, **kwargs):
        return {"view": self, "count": getattr(self, "count", 0)}


class _SecondStickyChild(LiveView):
    """A second sticky child (distinct sticky_id) for the index-set test."""

    sticky = True
    sticky_id = "ticker"
    enable_state_snapshot = True
    template = "<div>Ticker: {{ ticks }}</div>"

    def mount(self, request, **kwargs):
        self.ticks = 0

    @event_handler()
    def tick(self, **kwargs):
        self.ticks += 1

    def get_context_data(self, **kwargs):
        return {"view": self, "ticks": getattr(self, "ticks", 0)}


class _NonStickyChild(LiveView):
    """A plain embedded child — NOT sticky. Gets an auto ``child_N`` id and
    must never be persisted under a ``__sticky__`` key (ADR-018 Decision 1).
    """

    enable_state_snapshot = True
    template = "<div>Plain: {{ n }}</div>"

    def mount(self, request, **kwargs):
        self.n = 0

    @event_handler()
    def bump(self, **kwargs):
        self.n += 1

    def get_context_data(self, **kwargs):
        return {"view": self, "n": getattr(self, "n", 0)}


class _ChildOptInOnly(LiveView):
    """Sticky child that opts into snapshot — used under a parent that does
    NOT opt in, to exercise Decision 5's both-opt-in gate.
    """

    sticky = True
    sticky_id = "counter"
    enable_state_snapshot = True
    template = "<div>Counter: {{ count }}</div>"

    def mount(self, request, **kwargs):
        self.count = 0

    @event_handler()
    def increment(self, **kwargs):
        self.count += 1

    def get_context_data(self, **kwargs):
        return {"view": self, "count": getattr(self, "count", 0)}


class _ChildNoOptIn(LiveView):
    """Sticky child that does NOT opt into snapshot — used under an opted-in
    parent to exercise the inverse of Decision 5's gate.
    """

    sticky = True
    sticky_id = "counter"
    # NOTE: no ``enable_state_snapshot`` opt-in.
    template = "<div>Counter: {{ count }}</div>"

    def mount(self, request, **kwargs):
        self.count = 0

    @event_handler()
    def increment(self, **kwargs):
        self.count += 1

    def get_context_data(self, **kwargs):
        return {"view": self, "count": getattr(self, "count", 0)}


class _DashParent(LiveView):
    """Parent that embeds the sticky child via {% live_render sticky=True %}.

    Both parent and child opt into ``enable_state_snapshot`` so Decision 5's
    both-opt-in gate is satisfied.
    """

    enable_state_snapshot = True
    template = (
        "{% load live_tags %}"
        "<div dj-root>"
        "<h1>Dash</h1>"
        '{% live_render "djust.tests.test_sticky_child_persistence_1471._StickyCounterChild" '
        "sticky=True %}"
        "</div>"
    )

    def mount(self, request, **kwargs):
        self.title = "Dash"

    def get_context_data(self, **kwargs):
        return {"view": self, "title": getattr(self, "title", "Dash")}


class _TwoStickyParent(LiveView):
    """Parent embedding TWO sticky children — for the index-set test."""

    enable_state_snapshot = True
    template = (
        "{% load live_tags %}"
        "<div dj-root>"
        '{% live_render "djust.tests.test_sticky_child_persistence_1471._StickyCounterChild" '
        "sticky=True %}"
        '{% live_render "djust.tests.test_sticky_child_persistence_1471._SecondStickyChild" '
        "sticky=True %}"
        "</div>"
    )

    def mount(self, request, **kwargs):
        self.title = "Two"

    def get_context_data(self, **kwargs):
        return {"view": self, "title": getattr(self, "title", "Two")}


class _NonStickyEmbedParent(LiveView):
    """Parent embedding a NON-sticky child (no ``sticky=True``)."""

    enable_state_snapshot = True
    template = (
        "{% load live_tags %}"
        "<div dj-root>"
        '{% live_render "djust.tests.test_sticky_child_persistence_1471._NonStickyChild" %}'
        "</div>"
    )

    def mount(self, request, **kwargs):
        self.title = "NoStick"

    def get_context_data(self, **kwargs):
        return {"view": self, "title": getattr(self, "title", "NoStick")}


class _ParentNoOptIn(LiveView):
    """Parent that does NOT opt into snapshot but embeds an opted-in sticky
    child — exercises Decision 5 (child-in / parent-out → not saved).
    """

    # NOTE: no ``enable_state_snapshot`` opt-in.
    template = (
        "{% load live_tags %}"
        "<div dj-root>"
        '{% live_render "djust.tests.test_sticky_child_persistence_1471._ChildOptInOnly" '
        "sticky=True %}"
        "</div>"
    )

    def mount(self, request, **kwargs):
        self.title = "NoOptIn"

    def get_context_data(self, **kwargs):
        return {"view": self, "title": getattr(self, "title", "NoOptIn")}


class _ParentChildNoOptIn(LiveView):
    """Opted-in parent embedding a sticky child that does NOT opt in —
    exercises the inverse of Decision 5 (parent-in / child-out → not saved).
    """

    enable_state_snapshot = True
    template = (
        "{% load live_tags %}"
        "<div dj-root>"
        '{% live_render "djust.tests.test_sticky_child_persistence_1471._ChildNoOptIn" '
        "sticky=True %}"
        "</div>"
    )

    def mount(self, request, **kwargs):
        self.title = "ChildOut"

    def get_context_data(self, **kwargs):
        return {"view": self, "title": getattr(self, "title", "ChildOut")}


class _PlainParent(LiveView):
    """An opted-in parent with NO embedded children at all — pins the
    zero-cost-when-unused property.
    """

    enable_state_snapshot = True
    template = "<div dj-root><h1>{{ title }}</h1></div>"

    def mount(self, request, **kwargs):
        self.title = "Plain"

    @event_handler()
    def rename(self, **kwargs):
        self.title = "Renamed"

    def get_context_data(self, **kwargs):
        return {"view": self, "title": getattr(self, "title", "Plain")}


# ---------------------------------------------------------------------------
# Test consumer shim — bypasses Channels ASGI wiring. Mirrors the
# _FakeConsumer in tests/integration/test_sticky_redirect_flow.py.
# ---------------------------------------------------------------------------


class _FakeConsumer(LiveViewConsumer):
    """LiveViewConsumer stand-in: real ``handle_event``, faked I/O."""

    def __init__(self):  # type: ignore[no-untyped-def]
        self.view_instance: Optional[LiveView] = None
        self._view_group = None
        self._presence_group = None
        self._tick_task = None
        self._render_lock = asyncio.Lock()
        self._processing_user_event = False
        self._sticky_preserved: Dict[str, Any] = {}
        self._db_notify_channels: set[str] = set()
        self.sent_frames: List[Dict[str, Any]] = []
        self.session_id = "test-session"
        self.scope = {"path": "/", "query_string": b"", "session": None}
        self.channel_name = "test-channel"
        self.use_actors = False
        self.actor_handle = None
        from djust.websocket import ConnectionRateLimiter

        self._rate_limiter = ConnectionRateLimiter(rate=10000, burst=10000, max_warnings=3)

    class _NullChannelLayer:
        async def group_add(self, *args, **kwargs):
            return None

        async def group_discard(self, *args, **kwargs):
            return None

    @property
    def channel_layer(self):  # type: ignore[override]
        return self._NullChannelLayer()

    async def send_json(self, payload):  # type: ignore[override]
        self.sent_frames.append(payload)

    async def send(self, *args, **kwargs):  # type: ignore[override]
        return None

    async def send_error(self, message: str, **kwargs) -> None:  # type: ignore[override]
        self.sent_frames.append({"type": "error", "message": message})

    async def close(self, code=None):  # type: ignore[override]
        return None


# ---------------------------------------------------------------------------
# FAILING TEST (Action #1210) — sticky-child event persists under stable key
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_ws_sticky_child_event_writes_state_under_stable_key():
    """A sticky-child WS event must persist the child's state to the session
    under ``liveview_<parent_path>__sticky__<sticky_id>``.

    Against current ``main`` this FAILS: the ``websocket.py`` save block is
    gated ``target_view is self.view_instance``, and a sticky-child event sets
    ``target_view`` to the child (``websocket.py:2696``), so the save block is
    skipped entirely. The child's incremented ``count`` is never written.

    18a generalizes the gate (ADR-018 Decision 4) so a sticky child (truthy
    ``sticky_id``) whose parent also opts in (Decision 5) is saved under its
    stable key (Decision 1).
    """
    from django.contrib.auth.models import AnonymousUser
    from django.template import Context, Template
    from django.test import RequestFactory

    with override_settings(DJUST_LIVE_RENDER_ALLOWED_MODULES=[__name__]):

        def _setup() -> tuple[_FakeConsumer, str]:
            # Real DB-backed session so we can inspect the write afterwards.
            session = SessionStore()
            session.create()
            session_key = session.session_key

            rf = RequestFactory()
            request = rf.get("/dash/")
            request.user = AnonymousUser()
            request.session = session

            parent = _DashParent()
            parent.request = request
            parent.mount(request)
            # Mirror handle_mount: stash the mount request on the parent so
            # the save block can discover the session + path.
            parent._djust_mount_request = request

            # Render the parent template so {% live_render %} constructs +
            # registers the sticky child on parent._child_views["counter"].
            Template(parent.template).render(Context({"view": parent, "request": request}))

            consumer = _FakeConsumer()
            consumer.view_instance = parent
            consumer.scope = {
                "path": "/dash/",
                "query_string": b"",
                "session": session,
            }
            return consumer, session_key

        consumer, session_key = await sync_to_async(_setup)()

        # Sanity: the sticky child registered under its sticky_id.
        parent = consumer.view_instance
        assert "counter" in parent._get_all_child_views(), (
            "sticky child must be registered under sticky_id 'counter'"
        )

        # Drive the REAL handle_event with a child-routed event frame.
        # view_id == sticky_id routes target_view to the child.
        await consumer.handle_event(
            {
                "type": "event",
                "event": "increment",
                "params": {"view_id": "counter"},
                "ref": 1,
            }
        )

        # Read the REAL session backend. After 18a the sticky child's state
        # must live under the stable key. Against main the key is absent.
        post_session = SessionStore(session_key=session_key)
        saved = await post_session.aget("liveview_/dash/__sticky__counter", None)
        assert saved is not None, (
            "ADR-018 Decision 1/4: a sticky-child WS event must write the "
            "child's state to 'liveview_/dash/__sticky__counter' — but the "
            "key is absent. Current main's save block is gated "
            "`target_view is self.view_instance`, so sticky-child events are "
            "skipped. 18a generalizes the gate."
        )
        assert saved.get("count") == 1, (
            f"The saved sticky-child state must reflect the post-increment count=1, got: {saved!r}"
        )

        # The child's user-defined ``_note`` private attr must also persist
        # under the ``__private`` sibling key (ADR-018 Decision 1).
        saved_private = await post_session.aget("liveview_/dash/__sticky__counter__private", None)
        assert saved_private is not None, (
            "ADR-018 Decision 1: a sticky-child WS event must write the "
            "child's private state to "
            "'liveview_/dash/__sticky__counter__private' — but the key is "
            "absent."
        )
        assert saved_private.get("_note") == "incremented", (
            f"The saved private state must reflect the post-increment "
            f"_note value, got: {saved_private!r}"
        )

        # The GC ledger must list exactly the rendered sticky_ids.
        saved_index = await post_session.aget("liveview_/dash/__sticky_ids", None)
        assert saved_index == ["counter"], (
            f"ADR-018 Decision 3: the sticky-id ledger must list the "
            f"rendered set ['counter'], got: {saved_index!r}"
        )


# ---------------------------------------------------------------------------
# Shared WS-path harness — mount a parent, render its template (so
# {% live_render %} registers children), and return a wired _FakeConsumer.
# ---------------------------------------------------------------------------


def _setup_consumer(parent_cls, path):
    """Build a parent of ``parent_cls`` mounted at ``path``, render its
    template, and wire a _FakeConsumer. Returns (consumer, session_key).

    Synchronous — call via ``sync_to_async`` from an async test.
    """
    from django.contrib.auth.models import AnonymousUser
    from django.contrib.sessions.backends.db import SessionStore
    from django.template import Context, Template
    from django.test import RequestFactory

    session = SessionStore()
    session.create()
    session_key = session.session_key

    rf = RequestFactory()
    request = rf.get(path)
    request.user = AnonymousUser()
    request.session = session

    parent = parent_cls()
    parent.request = request
    parent.mount(request)
    parent._djust_mount_request = request

    Template(parent.template).render(Context({"view": parent, "request": request}))

    consumer = _FakeConsumer()
    consumer.view_instance = parent
    consumer.scope = {"path": path, "query_string": b"", "session": session}
    return consumer, session_key


# ---------------------------------------------------------------------------
# Case 2 — a non-sticky embed is NOT written under any __sticky__ key.
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_non_sticky_embed_not_written():
    """A ``{% live_render %}`` embed WITHOUT ``sticky=True`` gets an auto
    ``child_N`` id and must NOT be persisted under a ``__sticky__`` key.

    ADR-018 Decision 1: only children with a stable ``sticky_id`` are
    persistable — the volatile ``child_N`` stamp cannot survive a process
    boundary, so non-sticky embeds are explicitly out of scope.
    """
    with override_settings(DJUST_LIVE_RENDER_ALLOWED_MODULES=[__name__]):
        consumer, session_key = await sync_to_async(_setup_consumer)(
            _NonStickyEmbedParent, "/dash/"
        )

        # The non-sticky child registered under an auto child_N id.
        parent = consumer.view_instance
        child_ids = list(parent._get_all_child_views().keys())
        assert child_ids, "non-sticky child must still register for routing"
        non_sticky_id = child_ids[0]
        assert non_sticky_id.startswith("child_"), (
            f"a non-sticky embed should get an auto child_N id, got {non_sticky_id!r}"
        )

        await consumer.handle_event(
            {
                "type": "event",
                "event": "bump",
                "params": {"view_id": non_sticky_id},
                "ref": 1,
            }
        )

        post_session = SessionStore(session_key=session_key)
        loaded = await sync_to_async(post_session.load)()
        sticky_keys = [k for k in loaded if k.startswith("liveview_/dash/__sticky")]
        assert sticky_keys == [], (
            f"ADR-018 Decision 1: a non-sticky embed must NOT write any "
            f"__sticky__ key, but found: {sticky_keys!r}"
        )


# ---------------------------------------------------------------------------
# Case 3 — the sticky-id ledger reflects exactly the rendered set.
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_sticky_ids_index_reflects_rendered_set():
    """After a save, ``liveview_<path>__sticky_ids`` equals exactly the
    sorted set of rendered sticky_ids. ADR-018 Decision 3.

    Uses a parent embedding two sticky children (``counter`` + ``ticker``)
    to assert a 2-element sorted ledger.
    """
    with override_settings(DJUST_LIVE_RENDER_ALLOWED_MODULES=[__name__]):
        consumer, session_key = await sync_to_async(_setup_consumer)(_TwoStickyParent, "/two/")

        parent = consumer.view_instance
        assert set(parent._get_all_child_views().keys()) == {"counter", "ticker"}

        await consumer.handle_event(
            {
                "type": "event",
                "event": "increment",
                "params": {"view_id": "counter"},
                "ref": 1,
            }
        )

        post_session = SessionStore(session_key=session_key)
        ledger = await post_session.aget("liveview_/two/__sticky_ids", None)
        assert ledger == ["counter", "ticker"], (
            f"ADR-018 Decision 3: the sticky-id ledger must be the sorted "
            f"rendered set ['counter', 'ticker'], got: {ledger!r}"
        )


# ---------------------------------------------------------------------------
# Case 4 — an orphaned __sticky__ entry is pruned on the next save.
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_orphaned_sticky_entry_pruned_on_next_save():
    """A ``__sticky__`` entry whose sticky_id is no longer rendered is
    pruned on the next save. ADR-018 Decision 3 (GC ledger).

    Pre-seeds the session with an orphaned ``__sticky__ghost`` entry (+ its
    ``__private`` sibling) and an old ledger ``['counter', 'ghost']``, then
    renders a parent that only has ``counter``. After a save, the ghost
    keys are gone and the ledger is ``['counter']``.
    """
    with override_settings(DJUST_LIVE_RENDER_ALLOWED_MODULES=[__name__]):

        def _setup_with_orphan():
            consumer, session_key = _setup_consumer(_DashParent, "/dash/")
            session = consumer.scope["session"]
            # Pre-seed an orphaned entry + stale ledger.
            session["liveview_/dash/__sticky__ghost"] = {"count": 99}
            session["liveview_/dash/__sticky__ghost__private"] = {"_x": 1}
            session["liveview_/dash/__sticky_ids"] = ["counter", "ghost"]
            session.save()
            return consumer, session_key

        consumer, session_key = await sync_to_async(_setup_with_orphan)()

        await consumer.handle_event(
            {
                "type": "event",
                "event": "increment",
                "params": {"view_id": "counter"},
                "ref": 1,
            }
        )

        post_session = SessionStore(session_key=session_key)
        loaded = await sync_to_async(post_session.load)()

        assert "liveview_/dash/__sticky__ghost" not in loaded, (
            "ADR-018 Decision 3: the orphaned __sticky__ghost entry must be "
            "pruned once 'ghost' is no longer in the rendered set."
        )
        assert "liveview_/dash/__sticky__ghost__private" not in loaded, (
            "the orphaned entry's __private sibling must also be pruned."
        )
        ledger = loaded.get("liveview_/dash/__sticky_ids")
        assert ledger == ["counter"], (
            f"after pruning, the ledger must reflect only the rendered set "
            f"['counter'], got: {ledger!r}"
        )
        # The live child's state is still written.
        assert loaded.get("liveview_/dash/__sticky__counter") is not None


# ---------------------------------------------------------------------------
# Case 5 — both-opt-in gate: child opts in, parent does not (and inverse).
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_both_opt_in_gate_child_in_parent_out():
    """ADR-018 Decision 5 — a sticky child is persisted only when BOTH the
    child and its parent set ``enable_state_snapshot = True``.

    Covers both halves of the gate:
      * child opts in, parent does NOT → child state absent.
      * parent opts in, child does NOT → child state absent.
    """
    with override_settings(DJUST_LIVE_RENDER_ALLOWED_MODULES=[__name__]):
        # Half 1 — child opts in, parent does not.
        consumer, session_key = await sync_to_async(_setup_consumer)(_ParentNoOptIn, "/cin/")
        await consumer.handle_event(
            {
                "type": "event",
                "event": "increment",
                "params": {"view_id": "counter"},
                "ref": 1,
            }
        )
        post_session = SessionStore(session_key=session_key)
        loaded = await sync_to_async(post_session.load)()
        assert "liveview_/cin/__sticky__counter" not in loaded, (
            "ADR-018 Decision 5: child opted in but parent did NOT — the "
            "sticky child must NOT be persisted."
        )
        assert "liveview_/cin/__sticky_ids" not in loaded, (
            "no sticky save means no GC ledger write either."
        )

        # Half 2 — parent opts in, child does not.
        consumer2, session_key2 = await sync_to_async(_setup_consumer)(
            _ParentChildNoOptIn, "/cout/"
        )
        await consumer2.handle_event(
            {
                "type": "event",
                "event": "increment",
                "params": {"view_id": "counter"},
                "ref": 1,
            }
        )
        post_session2 = SessionStore(session_key=session_key2)
        loaded2 = await sync_to_async(post_session2.load)()
        assert "liveview_/cout/__sticky__counter" not in loaded2, (
            "ADR-018 Decision 5: parent opted in but child did NOT — the "
            "sticky child must NOT be persisted."
        )


# ---------------------------------------------------------------------------
# Case 6 — zero behavior change for a parent with no sticky children.
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_no_behavior_change_without_sticky_children():
    """A plain opted-in parent (no ``{% live_render %}``) still saves its own
    ``liveview_<path>`` state on a normal event and writes NO ``__sticky_ids``
    / ``__sticky__*`` keys.

    Pins the zero-cost-when-unused property (ADR-018 Pros).
    """
    with override_settings(DJUST_LIVE_RENDER_ALLOWED_MODULES=[__name__]):
        consumer, session_key = await sync_to_async(_setup_consumer)(_PlainParent, "/plain/")

        await consumer.handle_event({"type": "event", "event": "rename", "params": {}, "ref": 1})

        post_session = SessionStore(session_key=session_key)
        loaded = await sync_to_async(post_session.load)()

        # The parent's own state still persists (Branch A unchanged).
        own = loaded.get("liveview_/plain/")
        assert own is not None and own.get("title") == "Renamed", (
            f"the plain parent's own state must still persist, got: {own!r}"
        )
        # No sticky machinery touched the session.
        sticky_keys = [k for k in loaded if "__sticky" in k]
        assert sticky_keys == [], (
            f"a parent with no sticky children must write NO __sticky keys, got: {sticky_keys!r}"
        )


# ---------------------------------------------------------------------------
# Case 7 — HTTP-path symmetry: a POST sticky-child save writes the stable key.
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_http_sticky_child_save():
    """ADR-018 Decision 4 (HTTP side) — the HTTP POST path persists sticky
    children too.

    The POST path has no ``view_id`` routing, so it is a parent-driven
    sweep: after the parent renders (which registers its sticky children),
    each registered child satisfying the both-opt-in gate is saved under
    its stable key. Drives the real ``RequestMixin.post`` via the
    ``X-Djust-Event`` HTTP-fallback shape.
    """
    from django.contrib.auth.models import AnonymousUser
    from django.contrib.sessions.backends.db import SessionStore
    from django.test import RequestFactory

    with override_settings(DJUST_LIVE_RENDER_ALLOWED_MODULES=[__name__]):
        session = SessionStore()
        session.create()
        session_key = session.session_key

        rf = RequestFactory()
        # HTTP-fallback POST: event name in the X-Djust-Event header.
        request = rf.post(
            "/dash/",
            data="{}",
            content_type="application/json",
            HTTP_X_DJUST_EVENT="increment",
        )
        request.user = AnonymousUser()
        request.session = session

        parent = _DashParent()
        # The HTTP POST always targets ``self`` (the parent); a child-routed
        # event isn't expressible over POST. The parent-driven sweep saves
        # whatever sticky children render. Drive the parent's own ``post``.
        response = parent.post(request)
        assert response.status_code == 200, f"HTTP POST should succeed, got {response.status_code}"
        # The HTTP path mutates ``request.session`` in place; in production
        # ``SessionMiddleware`` persists it on the response. ``RequestFactory``
        # runs no middleware, so the test saves explicitly — matching the
        # established HTTP-POST test pattern (e.g. test_api_dispatch.py).
        request.session.save()

        post_session = SessionStore(session_key=session_key)
        saved = post_session.get("liveview_/dash/__sticky__counter")
        assert saved is not None, (
            "ADR-018 Decision 4 (HTTP side): the HTTP POST path must persist "
            "the sticky child under 'liveview_/dash/__sticky__counter' — but "
            "the key is absent."
        )
        ledger = post_session.get("liveview_/dash/__sticky_ids")
        assert ledger == ["counter"], (
            f"the HTTP path must also write the GC ledger, got: {ledger!r}"
        )
