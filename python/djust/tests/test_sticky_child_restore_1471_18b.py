"""Tests for #1471 / ADR-018 iter 18b — sticky-child state persistence: LOAD side.

ADR-018 (``docs/adr/018-sticky-child-state-persistence.md``) closes the gap
where a sticky-child LiveView's event-driven state is silently lost across a
WebSocket reconnect. Iteration 18a is the SAVE side; iteration 18b is the
LOAD/restore side exercised here:

* **Decision 2** — when ``{% live_render sticky=True %}`` constructs a sticky
  child during render, the tag — BEFORE calling the child's ``mount()`` —
  reads ``liveview_<parent_path>__sticky__<sticky_id>`` from the session. If
  saved state exists AND the both-opt-in gate holds, the saved public +
  private state is applied IN LIEU OF ``mount()`` state-init.
* **Decision 5** — restore is gated on BOTH child and parent
  ``enable_state_snapshot = True`` (tree-consistency).
* **Decision 6** — a restored child runs the same ``_restore_*`` side-effect
  replay the parent uses.

The first test (``test_sticky_child_state_restored_on_next_render``) is the
PLANNING-STAGE FAILING TEST written FIRST per Action #1210. The rest grow it
into the full 18b regression suite.

Gate-off self-test (#1468): gating the ``restore_sticky_child_state`` call in
``live_tags.py`` off (e.g. ``if False and sticky_kwarg:``) makes 6 of the 8
tests FAIL — every test that exercises the restore path end-to-end (the
round-trip, private-state, mount-skip and ``_restore_*``-replay cases). They
are NOT tautological. ``test_no_saved_state_mounts_fresh`` and
``test_both_opt_in_gate_blocks_restore`` stay green with the gate off (they
assert the fresh-``mount()`` fallthrough), which is correct.

Reuses the ``_DashParent`` / ``_StickyCounterChild`` fixtures from the 18a
test module so the save format under test is the verbatim 18a contract.
"""

from __future__ import annotations

import pytest
from asgiref.sync import sync_to_async
from django.contrib.sessions.backends.db import SessionStore
from django.test import override_settings

from djust import LiveView
from djust.decorators import event_handler

# Reuse the 18a fixtures verbatim — the save format under test is 18a's.
from djust.tests.test_sticky_child_persistence_1471 import (
    _DashParent,
    _setup_consumer,
)

_ALLOWED = [
    "djust.tests.test_sticky_child_persistence_1471",
    "djust.tests.test_sticky_child_restore_1471_18b",
]


# ---------------------------------------------------------------------------
# Helpers — render a fresh parent against an existing session, mirroring a
# WS reconnect / HTTP GET re-render: a brand-new parent instance renders its
# template, {% live_render %} constructs a brand-new sticky child.
# ---------------------------------------------------------------------------


def _rerender(parent_cls, path, session_key):
    """Build a fresh ``parent_cls`` against the SAME session and render its
    template (so ``{% live_render %}`` constructs + restores the child).

    Synchronous — call via ``sync_to_async`` from an async test.
    """
    from django.contrib.auth.models import AnonymousUser
    from django.template import Context, Template
    from django.test import RequestFactory

    session = SessionStore(session_key=session_key)
    rf = RequestFactory()
    request = rf.get(path)
    request.user = AnonymousUser()
    request.session = session

    parent = parent_cls()
    parent.request = request
    parent.mount(request)
    parent._djust_mount_request = request
    Template(parent.template).render(Context({"view": parent, "request": request}))
    return parent


# ---------------------------------------------------------------------------
# Module-scope views for the restore-specific cases (sentinels / _restore_*).
# ---------------------------------------------------------------------------


class _SentinelChild(LiveView):
    """Sticky child whose ``mount()`` stamps a sentinel attr. After a restore
    the sentinel must be ABSENT — proving ``mount()`` did NOT run.
    """

    sticky = True
    sticky_id = "counter"
    enable_state_snapshot = True
    template = "<div><button dj-click='increment'>Counter: {{ count }}</button></div>"

    def mount(self, request, **kwargs):
        self.count = 0
        self._mount_ran = True  # sentinel — must be absent after a restore

    @event_handler()
    def increment(self, **kwargs):
        self.count += 1

    def get_context_data(self, **kwargs):
        return {"view": self, "count": getattr(self, "count", 0)}


class _SentinelParent(LiveView):
    """Parent embedding :class:`_SentinelChild`."""

    enable_state_snapshot = True
    template = (
        "{% load live_tags %}"
        "<div dj-root>"
        '{% live_render "djust.tests.test_sticky_child_restore_1471_18b._SentinelChild" '
        "sticky=True %}"
        "</div>"
    )

    def mount(self, request, **kwargs):
        self.title = "Sentinel"

    def get_context_data(self, **kwargs):
        return {"view": self, "title": getattr(self, "title", "Sentinel")}


class _ReplayChild(LiveView):
    """Sticky child overriding ``_restore_presence`` so the suite can assert
    the Decision-6 ``_restore_*`` replay runs on a restored child.
    """

    sticky = True
    sticky_id = "counter"
    enable_state_snapshot = True
    template = "<div><button dj-click='increment'>Counter: {{ count }}</button></div>"

    def mount(self, request, **kwargs):
        self.count = 0

    @event_handler()
    def increment(self, **kwargs):
        self.count += 1

    def _restore_presence(self):  # noqa: D401 — replay marker for the test
        self._presence_replayed = True

    def get_context_data(self, **kwargs):
        return {"view": self, "count": getattr(self, "count", 0)}


class _ReplayParent(LiveView):
    """Parent embedding :class:`_ReplayChild`."""

    enable_state_snapshot = True
    template = (
        "{% load live_tags %}"
        "<div dj-root>"
        '{% live_render "djust.tests.test_sticky_child_restore_1471_18b._ReplayChild" '
        "sticky=True %}"
        "</div>"
    )

    def mount(self, request, **kwargs):
        self.title = "Replay"

    def get_context_data(self, **kwargs):
        return {"view": self, "title": getattr(self, "title", "Replay")}


# ---------------------------------------------------------------------------
# Test 1 (planning test) — sticky child restored on next render (WS save).
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_sticky_child_state_restored_on_next_render():
    """A sticky child whose state was saved (via 18a's WS save path) must have
    that state restored when the parent re-renders {% live_render %} — instead
    of the child's mount() re-initializing it to count=0.

    This is the core 18b LOAD-side contract (ADR-018 Decision 2). It FAILS on
    the current branch because the {% live_render %} tag has no restore branch.
    """
    from django.contrib.auth.models import AnonymousUser
    from django.template import Context, Template
    from django.test import RequestFactory

    with override_settings(
        DJUST_LIVE_RENDER_ALLOWED_MODULES=["djust.tests.test_sticky_child_persistence_1471"]
    ):
        # --- Phase 1: mount + drive a child event so 18a saves the state. ---
        consumer, session_key = await sync_to_async(_setup_consumer)(_DashParent, "/dash/")
        await consumer.handle_event(
            {
                "type": "event",
                "event": "increment",
                "params": {"view_id": "counter"},
                "ref": 1,
            }
        )
        # 18a should have persisted count=1 under the stable key.
        saved = await SessionStore(session_key=session_key).aget(
            "liveview_/dash/__sticky__counter", None
        )
        assert saved is not None and saved.get("count") == 1, (
            f"precondition (18a save): expected count=1 saved, got {saved!r}"
        )

        # --- Phase 2: a FRESH parent re-renders against the SAME session. ---
        # This simulates a WS reconnect — a new parent instance renders its
        # template, {% live_render %} constructs a brand-new sticky child.
        def _rerender_dash() -> _DashParent:
            session = SessionStore(session_key=session_key)
            rf = RequestFactory()
            request = rf.get("/dash/")
            request.user = AnonymousUser()
            request.session = session

            parent = _DashParent()
            parent.request = request
            parent.mount(request)
            parent._djust_mount_request = request
            Template(parent.template).render(Context({"view": parent, "request": request}))
            return parent

        parent2 = await sync_to_async(_rerender_dash)()

        # The newly-constructed sticky child must carry the RESTORED count=1,
        # not the mount() default count=0.
        child2 = parent2._get_all_child_views().get("counter")
        assert child2 is not None, "sticky child must be re-registered on re-render"
        assert getattr(child2, "count", None) == 1, (
            "ADR-018 Decision 2: on re-render the {% live_render %} tag must "
            "restore the saved sticky-child state (count=1) in lieu of running "
            "mount() fresh — but the child re-mounted at count="
            f"{getattr(child2, 'count', None)!r}."
        )


# ---------------------------------------------------------------------------
# Test 2 — private state restored via _restore_private_state.
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_sticky_child_private_state_restored():
    """After a child event sets the user-private ``_note`` attr, a re-render
    restores it via ``_restore_private_state`` (reads the ``__private`` key).

    ADR-018 Decision 2 — the private-state half of the restore contract.
    """
    with override_settings(DJUST_LIVE_RENDER_ALLOWED_MODULES=_ALLOWED):
        consumer, session_key = await sync_to_async(_setup_consumer)(_DashParent, "/dash/")
        await consumer.handle_event(
            {
                "type": "event",
                "event": "increment",
                "params": {"view_id": "counter"},
                "ref": 1,
            }
        )
        # Precondition: 18a saved _note under the __private key.
        saved_private = await SessionStore(session_key=session_key).aget(
            "liveview_/dash/__sticky__counter__private", None
        )
        assert saved_private is not None and saved_private.get("_note") == "incremented", (
            f"precondition (18a private save): got {saved_private!r}"
        )

        parent2 = await sync_to_async(_rerender)(_DashParent, "/dash/", session_key)
        child2 = parent2._get_all_child_views().get("counter")
        assert child2 is not None
        assert getattr(child2, "_note", None) == "incremented", (
            "ADR-018 Decision 2: the restored child must carry the private "
            "_note='incremented' from the __private key, not the mount() "
            f"default 'fresh' — got {getattr(child2, '_note', None)!r}."
        )


# ---------------------------------------------------------------------------
# Test 3 — full WS round-trip restore.
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_ws_round_trip_restore():
    """Full WS round-trip: a child ``increment`` event (18a saves) followed by
    a fresh parent re-render against the same session restores the child.

    Drives the child event TWICE so the restored count is unambiguously >0
    and could not be the mount() default. ADR-018 round-trip acceptance (WS).
    """
    with override_settings(DJUST_LIVE_RENDER_ALLOWED_MODULES=_ALLOWED):
        consumer, session_key = await sync_to_async(_setup_consumer)(_DashParent, "/dash/")
        for ref in (1, 2):
            await consumer.handle_event(
                {
                    "type": "event",
                    "event": "increment",
                    "params": {"view_id": "counter"},
                    "ref": ref,
                }
            )

        parent2 = await sync_to_async(_rerender)(_DashParent, "/dash/", session_key)
        child2 = parent2._get_all_child_views().get("counter")
        assert child2 is not None, "sticky child must be re-registered on WS reconnect"
        assert getattr(child2, "count", None) == 2, (
            "WS round-trip: two increments must round-trip through the session "
            f"to count=2 on reconnect — got {getattr(child2, 'count', None)!r}."
        )


# ---------------------------------------------------------------------------
# Test 4 — full HTTP round-trip restore.
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_http_round_trip_restore():
    """Full HTTP round-trip: an HTTP POST sticky-child save (18a HTTP path)
    followed by a fresh parent GET-render against the same session restores
    the child via the {% live_render %} tag on the HTTP-GET render path.

    ADR-018 round-trip acceptance (HTTP). The HTTP POST has no ``view_id``
    routing, so a child-driven event isn't expressible over POST — instead
    the test pre-seeds a non-default child state into the session under the
    stable key (the exact shape 18a's parent-driven sweep would write) and
    asserts the subsequent GET render restores it. This exercises the HTTP
    restore code path (the {% live_render %} tag runs on a sync GET render),
    which is the deliverable under test.
    """
    from django.contrib.auth.models import AnonymousUser
    from django.template import Context, Template
    from django.test import RequestFactory

    with override_settings(DJUST_LIVE_RENDER_ALLOWED_MODULES=_ALLOWED):
        # --- Phase 1: an HTTP POST persists the sticky child (18a HTTP path).
        session = SessionStore()
        session.create()
        session_key = session.session_key

        rf = RequestFactory()
        post_request = rf.post(
            "/dash/",
            data="{}",
            content_type="application/json",
            HTTP_X_DJUST_EVENT="increment",
        )
        post_request.user = AnonymousUser()
        post_request.session = session

        parent = _DashParent()
        response = parent.post(post_request)
        assert response.status_code == 200, f"HTTP POST should succeed, got {response.status_code}"
        # RequestFactory runs no middleware, so persist the session explicitly
        # (matching the established HTTP-POST test pattern).
        post_request.session.save()

        # The HTTP parent-driven sweep wrote the child under its stable key
        # (a child-routed event isn't expressible over POST, so the child is
        # at its mount() default). Overwrite with a non-default state — the
        # exact JSON shape 18a's sweep produces — so the restore assertion
        # below cannot be satisfied by the mount() default.
        seed_session = SessionStore(session_key=session_key)
        assert seed_session.get("liveview_/dash/__sticky__counter") is not None, (
            "precondition: 18a's HTTP path must have written the stable key."
        )
        seed_session["liveview_/dash/__sticky__counter"] = {"count": 5}
        seed_session.save()

        # --- Phase 2: a fresh parent GET-renders against the same session.
        get_session = SessionStore(session_key=session_key)
        get_request = rf.get("/dash/")
        get_request.user = AnonymousUser()
        get_request.session = get_session

        parent2 = _DashParent()
        parent2.request = get_request
        parent2.mount(get_request)
        parent2._djust_mount_request = get_request
        Template(parent2.template).render(Context({"view": parent2, "request": get_request}))

        child2 = parent2._get_all_child_views().get("counter")
        assert child2 is not None, "sticky child must be re-registered on HTTP re-render"
        assert getattr(child2, "count", None) == 5, (
            "HTTP round-trip: the child state persisted via the HTTP path must "
            "restore on the next GET render in lieu of mount() — got "
            f"{getattr(child2, 'count', None)!r}."
        )


# ---------------------------------------------------------------------------
# Test 5 — mount() state-init skipped when saved state present.
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_mount_skipped_when_saved_state_present():
    """When saved state is present, the child's ``mount()`` must NOT run — the
    restore is IN LIEU OF mount() state-init (ADR-018 Decision 2).

    ``_SentinelChild.mount`` stamps ``_mount_ran = True``. After a save +
    re-render, the restored child must NOT carry that sentinel.
    """
    with override_settings(DJUST_LIVE_RENDER_ALLOWED_MODULES=_ALLOWED):
        consumer, session_key = await sync_to_async(_setup_consumer)(_SentinelParent, "/sentinel/")
        await consumer.handle_event(
            {
                "type": "event",
                "event": "increment",
                "params": {"view_id": "counter"},
                "ref": 1,
            }
        )

        parent2 = await sync_to_async(_rerender)(_SentinelParent, "/sentinel/", session_key)
        child2 = parent2._get_all_child_views().get("counter")
        assert child2 is not None
        assert getattr(child2, "count", None) == 1, (
            f"restored count must be 1, got {getattr(child2, 'count', None)!r}"
        )
        assert not hasattr(child2, "_mount_ran"), (
            "ADR-018 Decision 2: mount() must be SKIPPED when saved state is "
            "present — but the _mount_ran sentinel is set, meaning mount() ran."
        )


# ---------------------------------------------------------------------------
# Test 6 — _restore_* replay runs for a restored child.
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_restore_star_replay_runs_for_restored_child():
    """A restored sticky child runs the Decision-6 ``_restore_*`` replay.

    ``_ReplayChild`` overrides ``_restore_presence`` to set a marker; after a
    save + re-render the marker must be present on the restored child.
    """
    with override_settings(DJUST_LIVE_RENDER_ALLOWED_MODULES=_ALLOWED):
        consumer, session_key = await sync_to_async(_setup_consumer)(_ReplayParent, "/replay/")
        await consumer.handle_event(
            {
                "type": "event",
                "event": "increment",
                "params": {"view_id": "counter"},
                "ref": 1,
            }
        )

        parent2 = await sync_to_async(_rerender)(_ReplayParent, "/replay/", session_key)
        child2 = parent2._get_all_child_views().get("counter")
        assert child2 is not None
        assert getattr(child2, "_presence_replayed", False) is True, (
            "ADR-018 Decision 6: a restored sticky child must run the "
            "_restore_* side-effect replay — _restore_presence was not "
            "invoked."
        )


# ---------------------------------------------------------------------------
# Test 7 — no saved state mounts fresh (no behavior change when unused).
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_no_saved_state_mounts_fresh():
    """A sticky child with NO prior save runs ``mount()`` normally (count=0).

    Pins "no behavior change when unused" — a fresh session with no
    ``__sticky__`` keys must behave exactly as it does today.
    """
    with override_settings(DJUST_LIVE_RENDER_ALLOWED_MODULES=_ALLOWED):
        # Build a fresh session, render WITHOUT any prior event/save.
        consumer, session_key = await sync_to_async(_setup_consumer)(_DashParent, "/dash/")

        # No save happened — re-render against the still-empty session.
        parent2 = await sync_to_async(_rerender)(_DashParent, "/dash/", session_key)
        child2 = parent2._get_all_child_views().get("counter")
        assert child2 is not None, "sticky child must still register with no saved state"
        assert getattr(child2, "count", None) == 0, (
            "with no saved state the child must mount() fresh at count=0 — got "
            f"{getattr(child2, 'count', None)!r}."
        )
        # mount() ran, so the user-private _note carries the mount() default.
        assert getattr(child2, "_note", None) == "fresh", (
            "with no saved state mount() runs and sets _note='fresh' — got "
            f"{getattr(child2, '_note', None)!r}."
        )


# ---------------------------------------------------------------------------
# Test 8 — both-opt-in gate blocks restore (Decision 5).
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_both_opt_in_gate_blocks_restore():
    """ADR-018 Decision 5 — a sticky child opted in under a NON-opted-in
    parent must NOT restore; it mounts fresh.

    Pre-seeds a ``__sticky__counter`` value in the session, then renders a
    parent that does NOT opt into ``enable_state_snapshot``. The gate blocks
    the restore, so the child mounts fresh at count=0.
    """
    from djust.tests.test_sticky_child_persistence_1471 import _ParentNoOptIn

    with override_settings(DJUST_LIVE_RENDER_ALLOWED_MODULES=_ALLOWED):
        # Build a session and pre-seed a saved sticky-child value.
        def _seed_session() -> str:
            session = SessionStore()
            session.create()
            session["liveview_/cin/__sticky__counter"] = {"count": 7}
            session.save()
            return session.session_key

        session_key = await sync_to_async(_seed_session)()

        parent = await sync_to_async(_rerender)(_ParentNoOptIn, "/cin/", session_key)
        child = parent._get_all_child_views().get("counter")
        assert child is not None, "sticky child must still register"
        assert getattr(child, "count", None) == 0, (
            "ADR-018 Decision 5: the parent does NOT opt into "
            "enable_state_snapshot, so the both-opt-in gate must BLOCK the "
            "restore — the child must mount() fresh at count=0, not the "
            f"pre-seeded count=7 — got {getattr(child, 'count', None)!r}."
        )
