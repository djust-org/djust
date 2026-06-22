"""WebSocket transport hardening — security findings F21 and F17.

Both findings live in ``python/djust/websocket.py`` and concern the WebSocket
transport's handling of two specific message classes.

F21 (CWE-915 / CWE-913 — mass assignment via channel-layer ``server_push``):
    ``LiveViewConsumer.server_push`` applied the channel-layer message ``state``
    dict via RAW ``setattr(self.view_instance, key, value)`` in a loop, while the
    sibling ``handler`` field one line below was already restricted against a
    channel-layer attacker (the framework's own stated threat model). A raw
    ``setattr`` lets such an attacker overwrite ``__class__`` (type confusion),
    ``__init__``, ``_framework_attrs`` / ``_components`` / ``_rust_view``
    (framework internals), and private ``_`` state. The fix routes the loop
    through ``safe_setattr(..., allow_private=False)`` — the same guard every
    other state-restore sink uses.

F17 (CWE-770 / CWE-400 — binary upload frames bypass the global rate limiter):
    In ``LiveViewConsumer.receive``, binary upload frames (first byte
    0x01/0x02/0x03, len >= 17) were dispatched to ``_handle_upload_frame`` and
    ``return``ed BEFORE the global rate-limit gate whose comment claims it
    "applies to ALL message types (#107)". So the highest-volume message class
    was unthrottled and never tripped the abuse-disconnect (``close(4429)`` + IP
    cooldown). The fix routes binary upload frames through a dedicated
    higher-ceiling upload bucket (``ConnectionRateLimiter.check_upload``) BEFORE
    dispatch, mirroring the text-path disconnect/error behavior, so a flood
    still trips ``should_disconnect()`` while legitimate uploads aren't throttled.

Gate-off (#1468): reverting F21 to raw ``setattr`` makes the
``TestServerPushStateMassAssignment`` dunder/private/framework tests fail;
reverting F17 to the early-return-before-the-gate makes the
``TestUploadFrameRateLimit`` flood test fail.
"""

from __future__ import annotations

import sys
import uuid

import pytest

pytest.importorskip("channels")

from django.test import override_settings  # noqa: E402

from djust import LiveView  # noqa: E402
from djust.config import config  # noqa: E402
from djust.rate_limit import ConnectionRateLimiter  # noqa: E402
from djust.uploads import UploadMixin  # noqa: E402


# ---------------------------------------------------------------------------
# F21 — server_push state mass assignment
# ---------------------------------------------------------------------------


class _StateTargetView:
    """A real (non-Mock) view stand-in for server_push state application.

    A real object is required, not a MagicMock: a MagicMock would silently
    auto-create / accept any attribute, so an assertion that ``__class__`` /
    ``_framework_attrs`` are "unchanged" would be meaningless. With a real
    object, a raw ``setattr`` that the bug allows genuinely mutates the
    attribute and the test can observe it (#1650 reproduction fidelity).
    """

    def __init__(self):
        # User-facing public state (legitimately settable).
        self.count = 0
        # Framework internals an attacker must NOT be able to overwrite.
        self._framework_attrs = frozenset({"count"})
        self._components = {"original": object()}
        self._rust_view = object()


def _make_consumer(view):
    """Build a minimal consumer wired to drive ``server_push`` directly.

    Mirrors ``tests/unit/test_server_push.py::_make_consumer`` but uses the
    provided real view instead of a MagicMock so attribute mutations are
    observable.
    """
    from unittest.mock import AsyncMock, MagicMock

    from djust.websocket import LiveViewConsumer

    consumer = LiveViewConsumer()
    consumer.view_instance = view
    # Render path is mocked — server_push re-renders after applying state.
    consumer.use_binary = False
    consumer.send = AsyncMock()
    consumer._send_update = AsyncMock()
    # render_with_diff returns (html, patches, version).
    view._sync_state_to_rust = MagicMock()
    view.render_with_diff = MagicMock(return_value=("<div>ok</div>", '[{"op":"replace"}]', 2))
    return consumer


class TestServerPushStateMassAssignment:
    """F21: server_push must route state through safe_setattr."""

    @pytest.mark.asyncio
    async def test_legit_public_state_applies(self):
        """A legitimate public attribute in ``state`` must still apply —
        the fix tightens the blocklist without breaking normal usage."""
        view = _StateTargetView()
        consumer = _make_consumer(view)

        await consumer.server_push({"state": {"count": 5}, "handler": None, "payload": None})

        assert view.count == 5

    @pytest.mark.asyncio
    async def test_dunder_class_is_blocked(self):
        """``__class__`` overwrite (type confusion) must be blocked."""
        view = _StateTargetView()
        original_cls = view.__class__
        consumer = _make_consumer(view)

        await consumer.server_push({"state": {"__class__": dict}, "handler": None, "payload": None})

        assert view.__class__ is original_cls, "F21: __class__ was overwritten via server_push"
        assert type(view) is _StateTargetView

    @pytest.mark.asyncio
    async def test_dunder_init_is_blocked(self):
        """``__init__`` overwrite must be blocked."""
        view = _StateTargetView()
        original_init = type(view).__init__
        consumer = _make_consumer(view)

        await consumer.server_push(
            {"state": {"__init__": lambda self: None}, "handler": None, "payload": None}
        )

        # No instance-level __init__ override should have been written.
        assert "__init__" not in vars(view)
        assert type(view).__init__ is original_init

    @pytest.mark.asyncio
    async def test_framework_attrs_is_blocked(self):
        """``_framework_attrs`` (snapshot-order invariant, #1393) must be
        blocked — it is private and framework-critical."""
        view = _StateTargetView()
        original = view._framework_attrs
        consumer = _make_consumer(view)

        await consumer.server_push(
            {"state": {"_framework_attrs": set()}, "handler": None, "payload": None}
        )

        assert view._framework_attrs is original, "F21: _framework_attrs was overwritten"
        assert view._framework_attrs == frozenset({"count"})

    @pytest.mark.asyncio
    async def test_components_is_blocked(self):
        """``_components`` (component registry) must be blocked."""
        view = _StateTargetView()
        original = view._components
        consumer = _make_consumer(view)

        await consumer.server_push({"state": {"_components": {}}, "handler": None, "payload": None})

        assert view._components is original, "F21: _components was overwritten"

    @pytest.mark.asyncio
    async def test_private_state_is_blocked(self):
        """Arbitrary private ``_`` state must be blocked with
        ``allow_private=False`` (matches every other restore sink)."""
        view = _StateTargetView()
        consumer = _make_consumer(view)

        await consumer.server_push(
            {"state": {"_rust_view": "hijacked"}, "handler": None, "payload": None}
        )

        assert view._rust_view != "hijacked", "F21: private _rust_view was overwritten"

    @pytest.mark.asyncio
    async def test_mixed_state_applies_safe_blocks_dangerous(self):
        """A single push mixing safe + dangerous keys: safe applies,
        dangerous is dropped (the loop doesn't abort on a blocked key)."""
        view = _StateTargetView()
        original_cls = view.__class__
        consumer = _make_consumer(view)

        await consumer.server_push(
            {
                "state": {"count": 99, "__class__": dict, "_components": {}},
                "handler": None,
                "payload": None,
            }
        )

        assert view.count == 99
        assert view.__class__ is original_cls


# ---------------------------------------------------------------------------
# F17 — binary upload frames bypass the rate limiter
# ---------------------------------------------------------------------------


def _cancel_frame() -> bytes:
    """Build a minimal valid binary upload frame (0x03 cancel = 17 bytes).

    A cancel frame needs no registered upload — it is the cheapest flood
    payload (the finding's "response-amplification flood"), so it faithfully
    exercises the frame class an attacker would use.
    """
    return bytes([0x03]) + uuid.uuid4().bytes


class _UploadView(LiveView, UploadMixin):
    """An upload-enabled LiveView so ``_upload_manager`` is non-None and the
    real ``_handle_upload_frame`` path is reachable for legit frames."""

    template = (
        '<div dj-view="djust.tests.test_ws_transport_hardening_f21_f17._UploadView"'
        ' dj-id="0">up</div>'
    )

    def mount(self, request, **kwargs):
        self.allow_upload("files", max_entries=5)


setattr(sys.modules[__name__], "_UploadView", _UploadView)
_UPLOAD_VIEW_PATH = f"{__name__}._UploadView"

# Small upload bucket so the flood test is fast but faithful: burst=5,
# max_warnings=2 means ~7 cancel frames deplete the bucket and trip the
# abuse-disconnect. The general bucket stays large so the upload throttle is
# what fires (not the global one).
_FLOOD_CONFIG = {
    "rate_limit": {
        "rate": 100,
        "burst": 50,
        "max_warnings": 2,
        "upload_rate": 1,  # ~1 token/sec refill — effectively burst-only in-test
        "upload_burst": 5,
    }
}


async def _connect_and_mount():
    from channels.testing import WebsocketCommunicator

    from djust.websocket import LiveViewConsumer

    communicator = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    connected, _ = await communicator.connect()
    assert connected
    # Drain the initial connect/ack frame.
    try:
        await communicator.receive_json_from(timeout=2)
    except Exception:
        pass
    await communicator.send_json_to({"type": "mount", "view": _UPLOAD_VIEW_PATH})
    # Drain the mount response.
    try:
        await communicator.receive_output(timeout=2)
    except Exception:
        pass
    return communicator


class TestUploadFrameRateLimit:
    """F17: binary upload frames must pass through rate accounting before
    dispatch so a flood trips the abuse-disconnect (close 4429)."""

    @pytest.mark.django_db
    @override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__], LIVEVIEW_CONFIG=_FLOOD_CONFIG)
    async def test_upload_frame_flood_disconnects_4429(self):
        config.reset()
        try:
            communicator = await _connect_and_mount()

            saw_close_4429 = False
            # upload_burst=5 + max_warnings=2 → ~7 frames trip disconnect.
            # Send well past that and look for the close frame.
            for _ in range(40):
                await communicator.send_to(bytes_data=_cancel_frame())
                try:
                    out = await communicator.receive_output(timeout=1)
                except Exception:
                    break
                if out["type"] == "websocket.close":
                    assert out.get("code") == 4429, f"wrong close code: {out!r}"
                    saw_close_4429 = True
                    break

            assert saw_close_4429, (
                "F17: upload-frame flood did NOT trip the abuse-disconnect "
                "(close 4429) — binary frames bypassed the rate limiter"
            )
            await communicator.disconnect()
        finally:
            config.reset()

    @pytest.mark.django_db
    @override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__], LIVEVIEW_CONFIG=_FLOOD_CONFIG)
    async def test_small_number_of_upload_frames_not_throttled(self):
        """A legitimate small burst (within the upload bucket) must NOT be
        rate-limited or disconnected — the fix must not throttle real uploads
        to nothing."""
        config.reset()
        try:
            communicator = await _connect_and_mount()

            # upload_burst=5: send 3 cancel frames (well within budget).
            for _ in range(3):
                await communicator.send_to(bytes_data=_cancel_frame())
                out = await communicator.receive_output(timeout=2)
                # Each legit frame yields a normal progress/cancelled response,
                # never a close and never a rate_limit_exceeded.
                assert out["type"] != "websocket.close", "legit upload frame caused disconnect"
                if out["type"] == "websocket.send":
                    import json

                    payload = json.loads(out["text"])
                    assert payload.get("type") != "rate_limit_exceeded", (
                        "legit upload frame was rate-limited"
                    )
            await communicator.disconnect()
        finally:
            config.reset()


class TestUploadBucketAccounting:
    """Unit-level pins on the ConnectionRateLimiter upload bucket — proves the
    accounting is invoked and that a flood increments warnings toward
    should_disconnect() (the mechanism the integration test relies on)."""

    def test_check_upload_uses_dedicated_bucket(self):
        rl = ConnectionRateLimiter(rate=100, burst=20, upload_rate=1, upload_burst=3)
        # First 3 frames fit the burst.
        assert rl.check_upload() is True
        assert rl.check_upload() is True
        assert rl.check_upload() is True
        # 4th depletes the bucket.
        assert rl.check_upload() is False

    def test_upload_bucket_independent_of_global_bucket(self):
        """Depleting the global bucket must not deplete the upload bucket and
        vice versa — they are separate ceilings."""
        rl = ConnectionRateLimiter(rate=1, burst=2, upload_rate=1, upload_burst=2)
        # Drain the global bucket.
        assert rl.check("event") is True
        assert rl.check("event") is True
        assert rl.check("event") is False
        # Upload bucket is untouched.
        assert rl.check_upload() is True
        assert rl.check_upload() is True
        assert rl.check_upload() is False

    def test_upload_flood_increments_warnings_to_disconnect(self):
        rl = ConnectionRateLimiter(
            rate=100, burst=20, max_warnings=2, upload_rate=1, upload_burst=1
        )
        assert rl.check_upload() is True  # consumes the single token
        assert rl.should_disconnect() is False
        assert rl.check_upload() is False  # warning 1
        assert rl.check_upload() is False  # warning 2
        assert rl.should_disconnect() is True, "upload-frame flood must trip should_disconnect()"


class TestReceiveRateGateParity:
    """#1646 parity pin: source-grep that the binary-upload-frame branch in
    receive() routes through check_upload() BEFORE _handle_upload_frame, so a
    future fast-path can't silently re-introduce the bypass."""

    def test_receive_gates_upload_frames_before_dispatch(self):
        import inspect

        from djust.websocket import LiveViewConsumer

        src = inspect.getsource(LiveViewConsumer.receive)
        check_idx = src.find("check_upload()")
        dispatch_idx = src.find("_handle_upload_frame(bytes_data)")
        assert check_idx != -1, "receive() no longer calls check_upload() (F17 gate removed)"
        assert dispatch_idx != -1, "receive() no longer dispatches upload frames"
        assert check_idx < dispatch_idx, (
            "F17: check_upload() must run BEFORE _handle_upload_frame dispatch"
        )
