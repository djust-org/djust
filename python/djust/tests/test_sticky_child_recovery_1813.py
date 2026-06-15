"""Regression tests for #1813 — ``html_recovery`` resets a sticky child to mount defaults.

Root cause (verified by local reproduction; see CLAUDE.md "Reproduce a production
incident LOCALLY before changing infra"):

On an HTTP-prerendered page embedding ``{% live_render "Child" sticky=True %}``, after
the user interacts with the sticky child and a later parent patch fails on the client,
the client sends ``request_html`` → the server replays ``_recovery_html`` → the sticky
child is reset to its ``mount()`` defaults, discarding the user's interactions.

TWO compounding server-side defects (both fixed here):

(b1) The DAMAGE / structural cure. ``{% live_render sticky=True %}`` (live_tags.py)
     constructed a FRESH ``child_cls()`` + ``mount()`` on EVERY parent render. The two
     pre-existing escape hatches (``_sticky_preserved`` auto-reattach; session-backed
     ``restore_sticky_child_state``) are both inert by default — the latter is gated
     behind ``enable_state_snapshot=True`` on BOTH parent + child. So in the default
     case every parent re-render (and therefore every ``_recovery_html`` snapshot taken
     during a parent event) rendered the child at mount defaults. The fix adds a
     live-instance-reuse hatch: if the parent already has a registered live child for
     this ``sticky_id`` (in ``_child_views``), re-render THAT instance's current
     ``get_context_data()`` instead of mounting fresh.

(b2) Recovery freshness. The embedded-child event branch in ``handle_event`` renders
     ONLY the child subtree (``embedded_update``) and never re-arms recovery, so after a
     child drift the cached ``_recovery_html`` still holds an OLD parent render. We chose
     option (ii): ``handle_request_html`` re-renders the parent FRESH at recovery time.
     This is correct AND lowest-overhead (recovery is rare, child events frequent) — and
     it is only safe BECAUSE (b1) makes the fresh parent render faithful to the live
     child's current state.

These tests run under the DEFAULT config (NO ``enable_state_snapshot``) and drive a real
``WebsocketCommunicator`` end-to-end — they reproduce the data loss WITHOUT the
client-side prerender-morph trigger (a), which is exercised separately in
``tests/js/ws-mount-prerender-divergence-1813-sticky-djid.test.js``.
"""

from __future__ import annotations

import inspect

import pytest
from asgiref.sync import sync_to_async

from djust import LiveView
from djust.decorators import event_handler


# ---------------------------------------------------------------------------
# Test views — module-level so they resolve by dotted path via import_string().
# ---------------------------------------------------------------------------


class NotificationsView(LiveView):
    """Sticky child with a state-mutating ``dismiss`` handler."""

    sticky = True
    sticky_id = "notifications"
    template = (
        "<div>"
        "{% for n in notifications %}"
        '<span dj-click="dismiss({{ n.id }})">{{ n.text }}</span>'
        "{% endfor %}"
        "</div>"
    )

    def mount(self, request, **kwargs):
        self.notifications = [{"id": 1, "text": "a"}, {"id": 2, "text": "b"}]

    @event_handler()
    def dismiss(self, id="", **kwargs):
        try:
            t = int(id)
        except (TypeError, ValueError):
            return
        self.notifications = [n for n in self.notifications if n["id"] != t]

    def get_context_data(self, **kwargs):
        return {"notifications": self.notifications, "view": self}


class HomeView(LiveView):
    """Parent page embedding the sticky child via ``{% live_render sticky=True %}``."""

    template = (
        "{% load live_tags %}"
        '<div dj-root dj-view="djust.tests.test_sticky_child_recovery_1813.HomeView" '
        'dj-id="0">'
        "<h1>Home {{ ticks }}</h1>"
        '{% live_render "djust.tests.test_sticky_child_recovery_1813.NotificationsView" '
        "sticky=True %}"
        "</div>"
    )

    def mount(self, request, **kwargs):
        self.ticks = 0

    @event_handler()
    def refresh(self, **kwargs):
        # A pure-parent event: mutates only the parent's own state. The parent
        # re-render runs ``{% live_render %}`` again, which must NOT reset the
        # already-mounted sticky child.
        self.ticks += 1

    def get_context_data(self, **kwargs):
        return {"view": self, "ticks": self.ticks}


# ---------------------------------------------------------------------------
# Helpers (mirrors test_sticky_child_event_noop_1802.py)
# ---------------------------------------------------------------------------


async def _receive_until(communicator, wanted_type, *, tries=6, timeout=3):
    """Drain frames until one whose ``type`` == ``wanted_type`` (or return last seen)."""
    last = None
    for _ in range(tries):
        last = await communicator.receive_json_from(timeout=timeout)
        if last.get("type") == wanted_type:
            return last
    return last


class _ScopeSession:
    def __init__(self, key):
        self.session_key = key


async def _connect_and_mount(view_path: str, url: str):
    """Connect a WebsocketCommunicator and mount ``view_path``. Returns
    (communicator, mount_response)."""
    from channels.testing import WebsocketCommunicator
    from django.contrib.sessions.backends.db import SessionStore

    from djust.websocket import LiveViewConsumer

    def _create_session():
        s = SessionStore()
        s.create()
        return s.session_key

    session_key = await sync_to_async(_create_session)()

    communicator = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    communicator.scope["session"] = _ScopeSession(session_key)

    connected, _ = await communicator.connect()
    assert connected, "WebsocketCommunicator must connect"
    await communicator.receive_json_from(timeout=2)  # drain connect frame

    await communicator.send_json_to({"type": "mount", "view": view_path, "url": url})
    mount_resp = await _receive_until(communicator, "mount")
    assert mount_resp.get("type") == "mount", f"expected mount, got {mount_resp!r}"
    return communicator, mount_resp


# ---------------------------------------------------------------------------
# 1. THE load-bearing test — child drift then request_html recovery.
#    Reproducible WITHOUT the client-side prerender-morph trigger (a).
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_request_html_recovery_preserves_sticky_child_drift():
    """Mount parent + sticky child (two notifications a, b), dismiss(a) so the child
    drifts to [b], then send ``request_html`` and assert the recovered HTML reflects
    the DRIFTED child state (only b) — NOT the mount defaults (a AND b).

    Gate-off: reverting the (b1) live-instance-reuse hatch in live_tags.py (so the
    parent re-render at recovery time mounts a FRESH child) makes the recovered HTML
    show BOTH 'a' and 'b' → this test fails on the ``>a<`` assertion. (Verified during
    the #1813 investigation.)
    """
    pytest.importorskip("channels")
    from django.test import override_settings

    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
        communicator, mount_resp = await _connect_and_mount(f"{__name__}.HomeView", "/home/")

        # Sanity: mount renders both notifications.
        mount_html = mount_resp.get("html", "")
        assert ">a<" in mount_html and ">b<" in mount_html, (
            f"mount HTML should render both notifications; got {mount_html!r}"
        )

        # Child drift: dismiss notification a (id=1). Child now [b].
        await communicator.send_json_to(
            {
                "type": "event",
                "event": "dismiss",
                "params": {"view_id": "notifications", "id": 1},
                "ref": 1,
            }
        )
        ev = await _receive_until(communicator, "embedded_update")
        assert ev.get("type") == "embedded_update", f"expected embedded_update; got {ev!r}"
        assert ">b<" in ev.get("html", "") and ">a<" not in ev.get("html", ""), (
            f"embedded_update HTML should show only b after dismiss(a); got {ev!r}"
        )

        # Now the client's applyPatches fails (e.g. DOM structure shifted) and it
        # requests full recovery HTML.
        await communicator.send_json_to({"type": "request_html"})
        recovery = await _receive_until(communicator, "html_recovery")

        assert recovery.get("type") == "html_recovery", (
            f"expected html_recovery frame; got {recovery!r}"
        )
        html = recovery.get("html", "")
        assert html, "html_recovery frame must carry the recovery HTML"

        # The load-bearing assertions: the recovered HTML must reflect the DRIFTED
        # child state, not the mount defaults.
        assert ">b<" in html, (
            f"Recovery HTML must still contain the surviving notification 'b'. Got: {html!r}"
        )
        assert ">a<" not in html, (
            "DATA LOSS (#1813): recovery HTML reset the sticky child to its mount() "
            "defaults — the dismissed notification 'a' reappeared. The parent "
            "re-render at recovery time mounted a fresh child instead of reusing the "
            f"live registered instance. Got: {html!r}"
        )

        await communicator.disconnect()


# ---------------------------------------------------------------------------
# 2. Reported browser scenario: child drift → PARENT event → recovery.
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_parent_event_then_recovery_preserves_sticky_child_drift():
    """Mirror the reported scenario: child drifts, THEN a parent event (``refresh``)
    fires a full parent re-render, THEN recovery — assert the child state is faithful
    throughout.

    This pins both halves of the fix:
      * (b1) the parent ``refresh`` re-render (which runs ``{% live_render %}`` again)
        must NOT reset the child — its frame must still show only 'b';
      * (b2)+(b1) the subsequent recovery must also show only 'b'.

    Gate-off (b1): the ``refresh`` patch/html_update would re-render the child at mount
    defaults (a AND b), failing the parent-frame assertion.
    """
    pytest.importorskip("channels")
    from django.test import override_settings

    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
        communicator, mount_resp = await _connect_and_mount(f"{__name__}.HomeView", "/home/")

        # Child drift: dismiss a.
        await communicator.send_json_to(
            {
                "type": "event",
                "event": "dismiss",
                "params": {"view_id": "notifications", "id": 1},
                "ref": 1,
            }
        )
        await _receive_until(communicator, "embedded_update")

        # Parent event: refresh (mutates only parent.ticks). This triggers a full
        # parent render that re-runs {% live_render %}.
        await communicator.send_json_to(
            {"type": "event", "event": "refresh", "params": {}, "ref": 2}
        )
        parent_resp = await communicator.receive_json_from(timeout=3)

        # The parent re-render must reflect the drifted child, not mount defaults.
        # The frame may be a patch (with html) or html_update; check whichever HTML
        # the frame carries. For a patch frame the child subtree is untouched by the
        # parent diff, so the load-bearing check is the recovery below — but if the
        # frame carries html, it must be faithful.
        parent_html = parent_resp.get("html", "")
        if parent_html:
            assert ">a<" not in parent_html, (
                "DATA LOSS (#1813): a PARENT event re-rendered the sticky child at "
                "mount defaults — dismissed 'a' reappeared in the parent frame. "
                f"Got: {parent_resp!r}"
            )

        # Recovery must show the drifted child too.
        await communicator.send_json_to({"type": "request_html"})
        recovery = await _receive_until(communicator, "html_recovery")
        html = recovery.get("html", "")
        assert ">b<" in html, f"recovery must keep surviving 'b'; got {html!r}"
        assert ">a<" not in html, (
            "DATA LOSS (#1813): recovery after a parent event reset the sticky child "
            f"to mount defaults. Got: {html!r}"
        )

        await communicator.disconnect()


# ---------------------------------------------------------------------------
# 3. The (b1) hatch must NOT bypass the existing escape hatches.
#    A child mounted fresh on FIRST render still works (no live instance yet).
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_first_render_still_mounts_child_fresh():
    """On the very first parent render there is no live child instance registered yet,
    so the (b1) hatch must fall through to the existing fresh-mount path. The mounted
    HTML must show the child's mount() defaults (a AND b)."""
    pytest.importorskip("channels")
    from django.test import override_settings

    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
        communicator, mount_resp = await _connect_and_mount(f"{__name__}.HomeView", "/home/")
        mount_html = mount_resp.get("html", "")
        assert ">a<" in mount_html and ">b<" in mount_html, (
            f"first render must mount the child fresh (a AND b); got {mount_html!r}"
        )
        await communicator.disconnect()


# ---------------------------------------------------------------------------
# 4. Source pins (belt-and-suspenders; the WebsocketCommunicator tests are
#    the load-bearing ones — these fail fast if a refactor drops a fix even
#    when the integration tests are skipped under some CI config).
# ---------------------------------------------------------------------------


def test_live_render_has_live_instance_reuse_hatch_source():
    """live_tags.py must look up the parent's already-registered live child before
    constructing a fresh one (the (b1) hatch)."""
    import djust.templatetags.live_tags as lt

    source = inspect.getsource(lt)
    assert "_get_all_child_views" in source or "_get_child_view" in source, (
        "live_render must consult the parent's child-view registry to reuse a live "
        "sticky child across re-renders (#1813 (b1))."
    )


def test_handle_request_html_rerenders_parent_fresh_source():
    """handle_request_html must re-render the parent fresh at recovery time so the
    recovery HTML reflects the live child's current state (the (b2)(ii) choice)."""
    import djust.websocket as ws_mod

    source = inspect.getsource(ws_mod.LiveViewConsumer.handle_request_html)
    assert "render_with_diff" in source or "render_full_template" in source, (
        "handle_request_html must re-render the parent fresh at recovery time so the "
        "recovery HTML is faithful to the live sticky child's current state "
        "(#1813 (b2)(ii))."
    )
