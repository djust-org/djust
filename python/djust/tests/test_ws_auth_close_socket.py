"""Reproducer + regression for the WebSocket auth bypass (threat model T1/T2).

See ``docs/audits/websocket-auth-2026-06.md``. On a ``login_required`` WS mount
for an anonymous client, ``handle_mount`` sent a ``{"type":"navigate"}`` frame
but did NOT close the socket (the ``PermissionDenied`` branch did). The socket
stayed open with ``view_instance`` set but ``mount()`` never run, and
``handle_event`` never re-checks auth — so a raw client that ignores the
navigate frame could dispatch events to ``@event_handler`` methods
unauthenticated. The fix: send the navigate frame, then ``close(code=4403)`` and
clear ``view_instance`` on the redirect branch (mirroring ``PermissionDenied``).

These tests connect a real ``WebsocketCommunicator`` (no user in scope =
anonymous) and assert the socket is terminated and no handler runs. They FAIL
against the pre-fix code (socket open, handler dispatched) and PASS after.
"""

from __future__ import annotations

import sys

import pytest

pytest.importorskip("channels")

from django.test import override_settings  # noqa: E402

from djust import LiveView  # noqa: E402
from djust.decorators import event_handler  # noqa: E402

# Module-level flag the mutating handler flips if it ever executes. A raw client
# that bypasses auth and reaches the handler would set this True.
_HANDLER_RAN = False


class _LoginRequiredView(LiveView):
    """A login-gated view whose handler records execution."""

    login_required = True
    template = (
        '<div dj-view="djust.tests.test_ws_auth_close_socket._LoginRequiredView" dj-id="0">x</div>'
    )

    def mount(self, request, **kwargs):
        self.count = 0

    @event_handler
    def do_mutation(self, **kwargs):
        global _HANDLER_RAN
        _HANDLER_RAN = True  # a real handler would write to the DB here


class _PublicView(LiveView):
    """A public view used to prove a batch survives a login-redirecting sibling.

    ``login_required = False`` is load-bearing for test isolation, not just
    documentation: this is a *module-level* ``LiveView`` subclass, so it
    permanently joins ``LiveView.__subclasses__()`` and is walked by the
    ``djust.S005`` system check (LiveView exposes state without auth). Without
    the explicit ``login_required = False`` ("intentionally public"), S005 sees
    ``login_required is None`` + exposed state (``self.ok``) and fires on
    ``_PublicView``, polluting any later serial test that asserts the S005
    result set (issue #1794:
    ``test_s005_suppressed_with_login_required_false`` matched
    ``"PublicView" in msg``). Marking it public both reflects the view's actual
    contract and tells S005 the auth decision was made.
    """

    login_required = False

    template = (
        '<div dj-view="djust.tests.test_ws_auth_close_socket._PublicView" dj-id="0">pub</div>'
    )

    def mount(self, request, **kwargs):
        self.ok = True


class _ObjPermDeniedView(LiveView):
    """An object-scoped view that ALWAYS denies object permission (#1922).

    Mirrors the ``_DocAPIView`` / ``_DocSSEView`` shape from
    ``test_object_perm_api_sse_paths.py``: a custom ``get_object`` +
    ``has_object_permission`` activates the ADR-017 object-permission lifecycle,
    and ``has_object_permission`` returns ``False`` so the runtime mount path
    raises ``PermissionDenied`` post-mount (runtime.py:2215) → sends an
    ``error`` (``permission_denied``) frame → calls
    ``_finalize_mount_auth("permission_denied")`` → clears ``view_instance``.

    Pre-#1922 ``WSConsumerTransport.finalize_mount_auth`` closed ``4403``
    UNCONDITIONALLY on this verdict, so a batched denial dropped the SHARED
    socket and killed the sibling mounts (#291 multiplexed-path failure).

    ``login_required = False`` for the same S005 test-isolation reason as
    ``_PublicView`` (this is a module-level subclass walked by S005).
    """

    login_required = False

    template = (
        '<div dj-view="djust.tests.test_ws_auth_close_socket._ObjPermDeniedView"'
        ' dj-id="0">secret</div>'
    )

    def mount(self, request, **kwargs):
        self.loaded = True

    def get_object(self):
        return type("Obj", (), {"id": 99})()

    def has_object_permission(self, request, obj):
        return False  # always denied → permission_denied verdict


# Ensure the dotted paths resolve for the consumer's import.
setattr(sys.modules[__name__], "_LoginRequiredView", _LoginRequiredView)
setattr(sys.modules[__name__], "_PublicView", _PublicView)
setattr(sys.modules[__name__], "_ObjPermDeniedView", _ObjPermDeniedView)

_VIEW_PATH = f"{__name__}._LoginRequiredView"
_PUBLIC_PATH = f"{__name__}._PublicView"
_OBJPERM_PATH = f"{__name__}._ObjPermDeniedView"


async def _connect_and_mount(communicator):
    connected, _ = await communicator.connect()
    assert connected
    # Drain the initial connect/handshake frame if one is sent.
    try:
        await communicator.receive_json_from(timeout=2)
    except Exception:
        pass
    await communicator.send_json_to({"type": "mount", "view": _VIEW_PATH})


@pytest.mark.django_db
@override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__])
async def test_login_required_ws_mount_closes_socket_after_redirect():
    """Anonymous mount of a login_required view → navigate frame THEN the
    socket is closed (4403). Pre-fix the socket stayed open (T1)."""
    from channels.testing import WebsocketCommunicator

    from djust.websocket import LiveViewConsumer

    communicator = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    await _connect_and_mount(communicator)

    nav = await communicator.receive_json_from(timeout=2)
    assert nav.get("type") == "navigate", f"expected a navigate redirect frame, got {nav!r}"

    # The auth failure must terminate the transport, not just nudge the UI.
    out = await communicator.receive_output(timeout=2)
    assert out["type"] == "websocket.close", f"socket not closed after auth redirect: {out!r}"
    assert out.get("code") == 4403
    await communicator.disconnect()


@pytest.mark.django_db
@override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__])
async def test_anonymous_cannot_dispatch_event_after_login_redirect():
    """The actual bypass: after the login redirect, an event must NOT reach the
    handler. Pre-fix the open socket let handle_event dispatch unauthenticated."""
    global _HANDLER_RAN
    _HANDLER_RAN = False
    from channels.testing import WebsocketCommunicator

    from djust.websocket import LiveViewConsumer

    communicator = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    await _connect_and_mount(communicator)

    nav = await communicator.receive_json_from(timeout=2)
    assert nav.get("type") == "navigate"

    # A raw client ignores the navigate and tries to mutate state.
    await communicator.send_json_to({"type": "event", "event": "do_mutation", "params": {}})
    # Give the consumer a chance to (wrongly) process it.
    try:
        await communicator.receive_json_from(timeout=1)
    except Exception:
        pass

    assert _HANDLER_RAN is False, (
        "AUTH BYPASS: an anonymous client dispatched an event to a login_required "
        "view's @event_handler after the mount redirect (T1)"
    )
    await communicator.disconnect()


@pytest.mark.django_db
@override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__])
async def test_mount_batch_with_login_view_does_not_close_shared_socket():
    """Regression (review of the T1/T2 fix): a login-redirecting view inside a
    mount_batch must NOT close the shared socket — the surviving public view
    mounts and the redirecting view is reported in navigate[]. (The close() is
    suppressed inside _mount_one; view_instance is still cleared.)

    Order-independence (#1875): this test was order-fragile under full
    ``-n auto`` saturation. The original ``receive_nothing(timeout=0.5)``
    "no mid-batch close" check was doubly racy:

    1. Process-global channel layer. ``LiveViewConsumer.connect`` joins the
       ``"djust_hotreload"`` group (websocket.py:1676) on the cached
       process-global ``InMemoryChannelLayer`` (``get_channel_layer()``). A
       sibling test in the same xdist worker (e.g.
       ``test_ws_send_version_1788.test_hotreload_frame_advances_…`` does
       ``group_send("djust_hotreload", …)``) can leave stale group membership
       / buffered frames in that shared layer, delivering a stray
       ``hotreload``/``reload`` frame into this test's ``receive_nothing``
       window — failing the assert for the wrong reason. We reset the
       channel-layer cache so this consumer connects to a FRESH layer with no
       cross-test pollution.
    2. Wall-clock window. ``receive_nothing(timeout=0.5)`` proves "open" by the
       ABSENCE of a frame over a fixed wall-clock window — fundamentally flaky
       under CPU saturation (#1830/#1795). We replace it with a DETERMINISTIC
       openness probe: a ``ping``→``pong`` round-trip. A closed socket cannot
       pong, so receiving a ``pong`` proves the transport stayed open without
       racing a timer.
    """
    from channels.layers import channel_layers
    from channels.testing import WebsocketCommunicator

    from djust.websocket import LiveViewConsumer

    # Isolate the process-global channel layer: drop the cached backend so this
    # consumer connects to a fresh InMemoryChannelLayer with no stale
    # "djust_hotreload" membership or buffered frames from a sibling test in the
    # same xdist worker (the #1875 pollution source).
    channel_layers.backends.clear()

    communicator = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    connected, _ = await communicator.connect()
    assert connected
    try:
        await communicator.receive_json_from(timeout=2)
    except Exception:
        pass
    await communicator.send_json_to(
        {
            "type": "mount_batch",
            "views": [
                {"view": _PUBLIC_PATH, "target_id": "pub", "url": "/pub/"},
                {"view": _VIEW_PATH, "target_id": "secret", "url": "/secret/"},
            ],
        }
    )
    resp = await communicator.receive_json_from(timeout=3)
    assert resp.get("type") == "mount_batch", f"got {resp!r}"
    # Public view survived; login-required view is a navigate[] redirect.
    survivor_targets = [v.get("target_id") for v in resp.get("views", [])]
    nav_targets = [n.get("target_id") for n in resp.get("navigate", [])]
    assert "pub" in survivor_targets, f"public view dropped: {resp!r}"
    assert "secret" in nav_targets, f"login view not reported as redirect: {resp!r}"
    # The shared socket must still be open (no mid-batch close). Probe it
    # deterministically: a closed socket cannot answer a ping. Receiving a
    # pong proves the transport stayed open without racing a wall-clock window.
    await communicator.send_json_to({"type": "ping"})
    pong = await communicator.receive_json_from(timeout=3)
    assert pong.get("type") == "pong", (
        "shared socket did not pong after the batch — it was likely closed "
        f"mid-batch (a websocket.close would suppress the pong); got {pong!r}"
    )
    await communicator.disconnect()


# --------------------------------------------------------------------------- #
# Object-permission denial: batch gate parity (#1922 / #291-consistency)
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
@override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__])
async def test_mount_batch_with_objperm_denied_view_does_not_close_shared_socket():
    """Regression (#1922 / #291-consistency): an OBJECT-PERMISSION-DENIED view
    inside a ``mount_batch`` must NOT close the shared socket — the surviving
    public view mounts, the denied view is reported in ``failed[]``, and the
    transport stays open for the siblings.

    Pre-#1922 ``finalize_mount_auth`` closed ``4403`` UNCONDITIONALLY on the
    ``permission_denied`` verdict (it gated only the redirect verdicts on
    ``not _mounting_in_batch``), so a single batched object-perm denial dropped
    the SHARED socket and killed the sibling mounts (the #291 multiplexed-path
    failure). The fix gates the ``permission_denied`` close on
    ``not mounting_in_batch`` too.

    Channel-layer isolation + deterministic ping→pong openness probe follow the
    sibling ``..._with_login_view_...`` test (see its docstring for the #1875 /
    #1830 rationale).
    """
    from channels.layers import channel_layers
    from channels.testing import WebsocketCommunicator

    from djust.websocket import LiveViewConsumer

    channel_layers.backends.clear()

    communicator = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    connected, _ = await communicator.connect()
    assert connected
    try:
        await communicator.receive_json_from(timeout=2)
    except Exception:
        pass
    await communicator.send_json_to(
        {
            "type": "mount_batch",
            "views": [
                {"view": _PUBLIC_PATH, "target_id": "pub", "url": "/pub/"},
                {"view": _OBJPERM_PATH, "target_id": "secret", "url": "/secret/"},
            ],
        }
    )
    resp = await communicator.receive_json_from(timeout=3)
    assert resp.get("type") == "mount_batch", f"got {resp!r}"
    # Public view survived; object-perm-denied view is isolated into failed[].
    survivor_targets = [v.get("target_id") for v in resp.get("views", [])]
    failed_targets = [f.get("target_id") for f in resp.get("failed", [])]
    assert "pub" in survivor_targets, f"public view dropped by a sibling denial: {resp!r}"
    assert "secret" in failed_targets, f"object-perm-denied view not reported in failed[]: {resp!r}"
    # The denied object's content must not leak into the batch response.
    assert "secret" not in str(resp.get("views", [])), (
        "denied object rendered into a survivor frame"
    )
    # The shared socket must still be open (no mid-batch close): a closed socket
    # cannot pong. Deterministic openness probe, not a wall-clock window.
    await communicator.send_json_to({"type": "ping"})
    pong = await communicator.receive_json_from(timeout=3)
    assert pong.get("type") == "pong", (
        "shared socket did not pong after a batched object-perm denial — it was "
        f"likely closed mid-batch (#1922 regression); got {pong!r}"
    )
    await communicator.disconnect()


@pytest.mark.django_db
@override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__])
async def test_single_objperm_denied_mount_still_closes_socket():
    """Regression guard (#1922): a SINGLE (non-batch) object-permission-denied
    mount STILL closes the socket (4403) — the batch gate must NOT over-gate the
    single-mount path. ``_mounting_in_batch`` is ``False`` outside a batch, so the
    close fires exactly as it did pre-#1922 for the unbatched denial.

    Mirrors ``test_login_required_ws_mount_closes_socket_after_redirect`` but for
    the object-permission verdict: error frame THEN ``websocket.close`` 4403.
    """
    from channels.testing import WebsocketCommunicator

    from djust.websocket import LiveViewConsumer

    communicator = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    connected, _ = await communicator.connect()
    assert connected
    try:
        await communicator.receive_json_from(timeout=2)
    except Exception:
        pass
    await communicator.send_json_to({"type": "mount", "view": _OBJPERM_PATH})

    err = await communicator.receive_json_from(timeout=2)
    assert err.get("type") == "error", f"expected an object-perm error frame, got {err!r}"

    # The denial must terminate the transport on the single-mount path.
    out = await communicator.receive_output(timeout=2)
    assert out["type"] == "websocket.close", f"socket not closed after object-perm denial: {out!r}"
    assert out.get("code") == 4403
    await communicator.disconnect()
