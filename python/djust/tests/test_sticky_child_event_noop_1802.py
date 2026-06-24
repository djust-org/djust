"""Regression tests for #1802 — embedded sticky-child events return ``noop``.

Root cause: ``handle_event``'s auto-skip-render block (``websocket.py`` ~3157 /
~3460) snapshots ``self.view_instance`` (the PARENT) both before and after the
handler runs. When the event targets an embedded child (``view_id`` routing —
sticky or non-sticky ``{% live_render %}``), the handler mutates ``target_view``
(the CHILD), not the parent. So the parent's assigns are unchanged →
``pre_assigns == post_assigns`` → ``skip_render = True`` → ``_send_noop`` fires
BEFORE the embedded-child render branch (~3514) is ever reached.

Effect: a sticky/embedded child whose ``@event_handler`` mutates state produces
no patch/HTML frame — the DOM never updates → sticky widgets are render-only.

The fix snapshots ``target_view`` (which is ``self.view_instance`` for the
top-level view, the child for an embedded target) so the auto-skip decision is
made against the view the handler actually mutated.

These tests drive a real channels ``WebsocketCommunicator`` end-to-end:
mount a parent embedding a sticky child, fire the child's ``dismiss`` event
the way the client does (``view_id`` inside ``params``), and assert the
response is an ``embedded_update`` carrying the mutated HTML — NOT ``noop``.
A control test mounts the same child directly and confirms it always worked.
"""

from __future__ import annotations

import inspect

import pytest
from asgiref.sync import sync_to_async

from djust import LiveView
from djust.decorators import event_handler


# ---------------------------------------------------------------------------
# Test views — module-level so they resolve by dotted path for import_string().
# ---------------------------------------------------------------------------


class NotificationsView(LiveView):
    """Sticky child with a state-mutating ``dismiss`` handler (issue repro)."""

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
        '<div dj-root dj-view="djust.tests.test_sticky_child_event_noop_1802.HomeView" '
        'dj-id="0">'
        "<h1>Home</h1>"
        '{% live_render "djust.tests.test_sticky_child_event_noop_1802.NotificationsView" '
        "sticky=True %}"
        "</div>"
    )

    def mount(self, request, **kwargs):
        pass

    def get_context_data(self, **kwargs):
        return {"view": self}


# ---------------------------------------------------------------------------
# Helpers
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
# 1. The load-bearing regression test — embedded sticky-child event.
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_embedded_sticky_child_event_produces_update_not_noop():
    """Mount HomeView (embeds NotificationsView sticky=True), fire the child's
    ``dismiss`` event with ``view_id`` inside ``params`` (the way the client
    routes embedded-child events), and assert the response is an
    ``embedded_update`` whose HTML reflects the mutated state — NOT ``noop``.

    Gate-off: reverting the fix (snapshot ``self.view_instance`` instead of
    ``target_view``) makes the parent's assigns compare equal across the
    child-mutating handler → ``skip_render = True`` → response is ``{"type":
    "noop"}`` and this test fails on the first assertion.
    """
    pytest.importorskip("channels")
    from django.test import override_settings

    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
        communicator, mount_resp = await _connect_and_mount(f"{__name__}.HomeView", "/home/")

        # Sanity: the mounted HTML shows both notifications.
        mount_html = mount_resp.get("html", "")
        assert ">a<" in mount_html and ">b<" in mount_html, (
            f"mount HTML should render both notifications; got {mount_html!r}"
        )

        # Fire the child's dismiss event — view_id inside params (client shape:
        # 09-event-binding.js stamps params.view_id = embeddedViewId). The
        # event response is a single frame; receive it directly so a bare
        # ``noop`` (the bug) is caught explicitly rather than timing out.
        await communicator.send_json_to(
            {
                "type": "event",
                "event": "dismiss",
                "params": {"view_id": "notifications", "id": 1},
                "ref": 1,
            }
        )
        resp = await communicator.receive_json_from(timeout=3)

        assert resp.get("type") != "noop", (
            "Embedded sticky-child event returned a bare noop — no patch/HTML was "
            "produced. The auto-skip-render block compared the PARENT's assigns "
            "(unchanged) instead of the child's, so the embedded render branch "
            "never ran. This is #1802."
        )
        assert resp.get("type") == "embedded_update", (
            f"expected an embedded_update frame for the sticky child; got {resp!r}"
        )
        assert resp.get("view_id") == "notifications", (
            f"embedded_update must target the child's view_id; got {resp!r}"
        )

        # The mutated state is reflected: notification 1 ("a") removed, 2 ("b") kept.
        html = resp.get("html", "")
        assert ">b<" in html, f"surviving notification 'b' must be present; got {html!r}"
        assert ">a<" not in html, (
            f"dismissed notification 'a' must be gone from the child HTML; got {html!r}"
        )

        await communicator.disconnect()


# ---------------------------------------------------------------------------
# 2. Control — the same child mounted DIRECTLY always worked (returns a patch).
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_notifications_view_dismiss_works_standalone_control():
    """Control: mounting NotificationsView directly (top-level) and firing
    ``dismiss`` returns a render frame (patch or html_update) that removes the
    item — proving the handler + render are fine standalone. Only the
    embedded-via-parent path noops (the bug in test #1)."""
    pytest.importorskip("channels")
    from django.test import override_settings

    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
        communicator, _ = await _connect_and_mount(
            f"{__name__}.NotificationsView", "/notifications/"
        )

        await communicator.send_json_to(
            {"type": "event", "event": "dismiss", "params": {"id": 1}, "ref": 1}
        )
        resp = await communicator.receive_json_from(timeout=3)

        assert resp.get("type") != "noop", (
            "Standalone NotificationsView.dismiss must NOT noop — the handler "
            f"mutates state. Got {resp!r}"
        )
        assert resp.get("type") in ("patch", "html_update"), (
            f"standalone dismiss should produce a render frame; got {resp!r}"
        )

        await communicator.disconnect()


# ---------------------------------------------------------------------------
# 3. Source pin — the auto-skip block must compare target_view, not the parent.
# ---------------------------------------------------------------------------


def test_handle_event_autoskip_snapshots_target_view_source():
    """Belt-and-suspenders source pin: a ``view_id``-routed sticky-child event must
    run the handler + render against the CHILD (``target_view``), not the parent —
    so an embedded child's state change is reflected (never noop'd, #1802).

    #1907 THE FLIP: WS events route through ``ViewRuntime.dispatch_event``; the
    sticky-child handling moved off the deleted ``_handle_event_inner`` into the
    runtime's ``_dispatch_sticky_child_event``. The runtime's design RETIRES the
    bespoke ``change_target = target_view`` skip-render snapshot entirely: a
    ``view_id``-routed event is dispatched to a DEDICATED method that resolves the
    child, validates + calls the handler against the CHILD, and ALWAYS re-renders
    the child's subtree (``render_embedded_child_html(target_view)``) → a scoped
    ``embedded_update`` frame. There is no parent-snapshot skip path to mis-target,
    so the #1802 noop bug is structurally impossible. Pin the runtime structure.
    """
    import djust.runtime as rt_mod

    source = inspect.getsource(rt_mod.ViewRuntime._dispatch_sticky_child_event)
    # The child (target_view) is resolved from the registry and is the handler +
    # render subject — NOT the parent self.view_instance.
    assert "target_view = all_children.get(view_id)" in source, (
        "the runtime sticky-child path must resolve target_view (the CHILD) from "
        "_get_all_child_views() by view_id (#1802)."
    )
    # The child subtree is ALWAYS re-rendered via the single-sourced render core
    # (wrapped in sync_to_async, hence the whitespace-tolerant collapse).
    source_collapsed = " ".join(source.split())
    assert "render_embedded_child_html)(target_view)" in source_collapsed or (
        "render_embedded_child_html" in source and "(target_view)" in source_collapsed
    ), (
        "the runtime sticky-child path must ALWAYS re-render the CHILD's subtree "
        "(render_embedded_child_html(target_view)) — so an embedded child's "
        "mutation is reflected, never auto-skipped to a parent noop (#1802 / #1907)."
    )
    assert '"type": "embedded_update"' in source and "view_id" in source, (
        "the runtime sticky-child render must emit a child-scoped embedded_update "
        "frame keyed by view_id (#1802)."
    )
