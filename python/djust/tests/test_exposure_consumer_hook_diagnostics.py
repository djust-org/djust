"""ADR-038 E1: consumer message hooks that call application code.

``presence_heartbeat`` and ``cursor_move`` are dispatched by
``LiveViewConsumer.receive`` straight to view methods an application may
override (``update_presence_heartbeat``, ``handle_cursor_move``). Their catches
log the exception. For a nonlegacy view that is the same leak ``handle_exception``
refuses: undeclared state can occur in an exception's message.
"""

import json
import logging

import pytest
from asgiref.sync import sync_to_async
from channels.testing import WebsocketCommunicator
from django.test import override_settings

from djust import LiveView
from djust.websocket import LiveViewConsumer

from .test_exposure_runtime import make_request


class HookFailureView(LiveView):
    exposure_policy = "legacy"
    template = "<div dj-root>hooks</div>"

    def update_presence_heartbeat(self):
        raise ValueError("PRESENCE_HOOK_SENTINEL")

    def handle_cursor_move(self, x, y):
        raise ValueError("CURSOR_HOOK_SENTINEL")


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("policy", ["legacy", "explicit", None, "invalid"])
@pytest.mark.parametrize(
    "message,sentinel",
    [
        ({"type": "presence_heartbeat"}, "PRESENCE_HOOK_SENTINEL"),
        ({"type": "cursor_move", "x": 1, "y": 2}, "CURSOR_HOOK_SENTINEL"),
    ],
    ids=["presence_heartbeat", "cursor_move"],
)
async def test_hook_failure_logs_are_value_free_for_nonlegacy_views(
    monkeypatch, caplog, policy, message, sentinel
):
    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)
    with override_settings(
        LIVEVIEW_ALLOWED_MODULES=[__name__], DEBUG=True, DJUST_TENANTS=None, DJUST_CONFIG={}
    ):
        request = await sync_to_async(make_request)()
        socket = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
        socket.scope.update(session=request.session, user=request.user, tenant=None)
        assert (await socket.connect())[0]
        await socket.receive_json_from(timeout=3)
        try:
            await socket.send_json_to(
                {"type": "mount", "view": __name__ + ".HookFailureView", "url": "/hooks/"}
            )
            mounted = await socket.receive_json_from(timeout=3)
            assert mounted["type"] == "mount", mounted

            monkeypatch.setattr(HookFailureView, "exposure_policy", policy)
            caplog.clear()
            with caplog.at_level(logging.DEBUG):
                await socket.send_json_to(message)
                # The hooks send nothing back; a ping round-trip orders the log.
                await socket.send_json_to({"type": "ping"})
                assert (await socket.receive_json_from(timeout=3))["type"] == "pong"

            if policy == "legacy":
                # Control: the hook ran and legacy logging is unchanged.
                assert sentinel in caplog.text
            else:
                assert sentinel not in caplog.text
                assert "Protected view operation failed" in caplog.text
        finally:
            await socket.disconnect()


class PushFailureView(LiveView):
    exposure_policy = "legacy"
    template = "<div dj-root>push</div>"

    def handle_push(self, **kwargs):
        raise ValueError("PUSH_HOOK_SENTINEL")


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("policy", ["legacy", "explicit", None, "invalid"])
async def test_server_push_failure_logs_are_value_free_for_nonlegacy_views(
    monkeypatch, caplog, policy
):
    """``server_push`` runs an application handler from the channel layer
    (Celery, management commands) and its catch logged the exception with a
    traceback. The push arrives on the channel-layer queue, not the socket's,
    so the log is polled for either outcome rather than ordered by a ping."""
    import asyncio

    from djust.push import apush_to_view

    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)
    monkeypatch.setattr(PushFailureView, "exposure_policy", _mount_policy(policy))
    with override_settings(
        LIVEVIEW_ALLOWED_MODULES=[__name__], DEBUG=True, DJUST_TENANTS=None, DJUST_CONFIG={}
    ):
        request = await sync_to_async(make_request)()
        socket = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
        socket.scope.update(session=request.session, user=request.user, tenant=None)
        assert (await socket.connect())[0]
        await socket.receive_json_from(timeout=3)
        try:
            await socket.send_json_to(
                {"type": "mount", "view": __name__ + ".PushFailureView", "url": "/push/"}
            )
            mounted = await socket.receive_json_from(timeout=3)
            assert mounted["type"] == "mount", mounted

            monkeypatch.setattr(PushFailureView, "exposure_policy", policy)
            caplog.clear()
            with caplog.at_level(logging.DEBUG):
                await apush_to_view(__name__ + ".PushFailureView", handler="handle_push")
                if policy in (None, "invalid"):
                    await _expect_denial(socket)
                for _ in range(60):
                    text = caplog.text
                    if "PUSH_HOOK_SENTINEL" in text or "Protected view operation failed" in text:
                        break
                    if policy in (None, "invalid"):
                        break
                    await asyncio.sleep(0.05)

            if policy == "legacy":
                # Unchanged legacy output: message and traceback.
                assert "Error in server_push: PUSH_HOOK_SENTINEL" in caplog.text
                assert "Traceback (most recent call last)" in caplog.text
            elif policy == "explicit":
                assert "PUSH_HOOK_SENTINEL" not in caplog.text
                assert "Protected view operation failed" in caplog.text
            else:
                # Refused before the hook: nothing from it can reach the log.
                assert "PUSH_HOOK_SENTINEL" not in caplog.text
        finally:
            await socket.disconnect()


class TickFailureView(LiveView):
    exposure_policy = "legacy"
    template = "<div dj-root>tick</div>"
    # Longer than a test mount takes. The tick task is created during mount but
    # the consumer's view_instance is assigned after it returns, and _run_tick
    # stops if it wakes before then; a short interval never ticks at all. The
    # legacy control fails loudly rather than passing if that race recurs.
    tick_interval = 300

    def handle_tick(self):
        raise ValueError("TICK_HOOK_SENTINEL")


class NotifyFailureView(LiveView):
    exposure_policy = "legacy"
    template = "<div dj-root>notify</div>"
    # Class-level so the consumer joins the NOTIFY group at wiring time, which
    # reads it before mount(); listen() itself needs a PostgreSQL backend.
    _listen_channels = frozenset({"exposure_hooks"})

    def handle_info(self, message):
        raise ValueError("NOTIFY_HOOK_SENTINEL")


def _mount_policy(policy):
    """The policy a server-originated-turn test mounts under.

    Those turns are authorized fresh against the mount binding (ADR-038 D-l),
    so an explicit view must be explicit from mount for its hook to run at all.
    ``None`` and invalid policies cannot mount; they mount legacy and flip
    afterwards, and the turn is then refused before the hook runs.
    """
    return "explicit" if policy == "explicit" else "legacy"


async def _expect_denial(socket):
    """A refused server-originated turn gets the foreground denial."""
    for _ in range(20):
        out = await socket.receive_output(timeout=3)
        if out["type"] == "websocket.close":
            assert out["code"] == 4403, out
            return
    raise AssertionError("the turn was not denied")


async def _poll_log(caplog, *needles, attempts=80):
    import asyncio

    for _ in range(attempts):
        if any(n in caplog.text for n in needles):
            return
        await asyncio.sleep(0.05)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("policy", ["legacy", "explicit", None, "invalid"])
@pytest.mark.parametrize("hook", ["tick", "db_notify"])
async def test_timer_and_notify_hook_failures_are_value_free_for_nonlegacy_views(
    monkeypatch, caplog, policy, hook
):
    """``handle_tick`` runs on the consumer's tick timer and ``handle_info`` on a
    Postgres NOTIFY delivered through the channel layer; both catches logged the
    exception and its traceback. Both paths are production, not debug-only."""
    from channels.layers import get_channel_layer

    view_cls = TickFailureView if hook == "tick" else NotifyFailureView
    sentinel = "TICK_HOOK_SENTINEL" if hook == "tick" else "NOTIFY_HOOK_SENTINEL"
    expected_legacy = (
        "Error in tick handler: TICK_HOOK_SENTINEL"
        if hook == "tick"
        else "db_notify: handle_info raised on NotifyFailureView: NOTIFY_HOOK_SENTINEL"
    )
    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)
    # Explicit from mount; None/invalid flip after a legacy mount.
    monkeypatch.setattr(view_cls, "exposure_policy", _mount_policy(policy))
    with override_settings(
        LIVEVIEW_ALLOWED_MODULES=[__name__], DEBUG=True, DJUST_TENANTS=None, DJUST_CONFIG={}
    ):
        request = await sync_to_async(make_request)()
        socket = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
        socket.scope.update(session=request.session, user=request.user, tenant=None)
        assert (await socket.connect())[0]
        await socket.receive_json_from(timeout=3)
        try:
            with caplog.at_level(logging.DEBUG):
                await socket.send_json_to(
                    {"type": "mount", "view": f"{__name__}.{view_cls.__name__}", "url": "/h/"}
                )
                mounted = await socket.receive_json_from(timeout=3)
                assert mounted["type"] == "mount", mounted
                monkeypatch.setattr(view_cls, "exposure_policy", policy)
                caplog.clear()
                if hook == "db_notify":
                    await get_channel_layer().group_send(
                        "djust_db_notify_exposure_hooks",
                        {"type": "db_notify", "channel": "exposure_hooks", "payload": {}},
                    )
                if policy in (None, "invalid"):
                    await _expect_denial(socket)
                else:
                    await _poll_log(caplog, sentinel, "Protected view operation failed")

            if policy == "legacy":
                assert expected_legacy in caplog.text
                assert "Traceback (most recent call last)" in caplog.text
            elif policy == "explicit":
                assert sentinel not in caplog.text
                assert "Protected view operation failed" in caplog.text
            else:
                # Refused before the hook: nothing from it can reach the log.
                assert sentinel not in caplog.text
        finally:
            await socket.disconnect()


class UntrackFailureView(LiveView):
    exposure_policy = "legacy"
    template = "<div dj-root>untrack</div>"

    def untrack_presence(self):
        raise ValueError("UNTRACK_HOOK_SENTINEL")


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("policy", ["legacy", "explicit", None, "invalid"])
async def test_disconnect_presence_cleanup_failure_is_value_free_for_nonlegacy_views(
    monkeypatch, caplog, policy
):
    """``disconnect`` calls ``untrack_presence``, which an application may
    override, and its catch logged the exception. Disconnect is the trigger, so
    the log is checked after the socket closes."""
    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)
    monkeypatch.setattr(UntrackFailureView, "exposure_policy", "legacy")
    with override_settings(
        LIVEVIEW_ALLOWED_MODULES=[__name__], DEBUG=True, DJUST_TENANTS=None, DJUST_CONFIG={}
    ):
        request = await sync_to_async(make_request)()
        socket = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
        socket.scope.update(session=request.session, user=request.user, tenant=None)
        assert (await socket.connect())[0]
        await socket.receive_json_from(timeout=3)
        await socket.send_json_to(
            {"type": "mount", "view": __name__ + ".UntrackFailureView", "url": "/u/"}
        )
        mounted = await socket.receive_json_from(timeout=3)
        assert mounted["type"] == "mount", mounted
        monkeypatch.setattr(UntrackFailureView, "exposure_policy", policy)
        caplog.clear()
        with caplog.at_level(logging.DEBUG):
            await socket.disconnect()

    if policy == "legacy":
        assert "Error cleaning up presence: UNTRACK_HOOK_SENTINEL" in caplog.text
        # The original catch logged at WARNING; the converted site keeps it.
        [record] = [r for r in caplog.records if "UNTRACK_HOOK_SENTINEL" in r.getMessage()]
        assert record.levelno == logging.WARNING
    else:
        assert "UNTRACK_HOOK_SENTINEL" not in caplog.text
        assert "Protected view operation failed" in caplog.text


class BatchFailureView(LiveView):
    exposure_policy = "legacy"
    template = "<div dj-root>batch</div>"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("policy", ["legacy", "explicit", None, "invalid"])
async def test_mount_batch_escape_is_value_free_for_nonlegacy_views(monkeypatch, caplog, policy):
    """``_mount_one`` is the batch's last line of defense for anything that
    escapes ``handle_mount``. It logged the exception with its traceback and,
    under DEBUG, sent ``str(exc)`` to the client in ``failed[]``. The trigger is
    synthetic — ``handle_mount`` is stubbed to raise — because the catch exists
    for whatever escapes, not for one known path. The owner is the class the
    batch entry names, resolved by the shared allowlist-first resolver."""
    monkeypatch.setattr(BatchFailureView, "exposure_policy", policy)

    async def escape(self, data, **kwargs):
        raise ValueError("MOUNT_BATCH_SENTINEL")

    monkeypatch.setattr(LiveViewConsumer, "handle_mount", escape)
    with override_settings(
        LIVEVIEW_ALLOWED_MODULES=[__name__], DEBUG=True, DJUST_TENANTS=None, DJUST_CONFIG={}
    ):
        request = await sync_to_async(make_request)()
        socket = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
        socket.scope.update(session=request.session, user=request.user, tenant=None)
        assert (await socket.connect())[0]
        await socket.receive_json_from(timeout=3)
        try:
            caplog.clear()
            with caplog.at_level(logging.DEBUG):
                await socket.send_json_to(
                    {
                        "type": "mount_batch",
                        "views": [
                            {
                                "view": __name__ + ".BatchFailureView",
                                "url": "/b/",
                                "target_id": "t1",
                            }
                        ],
                    }
                )
                frame = await socket.receive_json_from(timeout=3)
            assert frame["type"] == "mount_batch", frame
            [failed] = frame["failed"]
            if policy == "legacy":
                # Unchanged legacy behaviour: DEBUG detail to the client, traceback in the log.
                assert "MOUNT_BATCH_SENTINEL" in failed["error"]
                assert "mount_batch: _mount_one raised for view" in caplog.text
                assert "MOUNT_BATCH_SENTINEL" in caplog.text
            else:
                assert failed["error"] == "mount failed"
                assert "MOUNT_BATCH_SENTINEL" not in json.dumps(frame)
                assert "MOUNT_BATCH_SENTINEL" not in caplog.text
                assert "Protected view operation failed" in caplog.text
        finally:
            await socket.disconnect()


def _fail_pushed_deferred(arg):
    raise ValueError("PUSH_DEFER_EXC_SENTINEL")


class PushDeferView(LiveView):
    exposure_policy = "legacy"
    template = "<div dj-root>push-defer</div>"

    def handle_later(self, **kwargs):
        import functools

        self.defer(functools.partial(_fail_pushed_deferred, "PUSH_DEFER_ARG_SENTINEL"))
        # Skip the render so server_push drains through the consumer's own
        # _flush_all_pending, which calls the consumer's _flush_deferred.
        self._skip_render = True


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("policy", ["legacy", "explicit", None, "invalid"])
async def test_consumer_deferred_callback_failure_is_value_free_for_nonlegacy_views(
    monkeypatch, caplog, policy
):
    """The consumer's ``_flush_deferred`` twin runs on its own flush paths
    (``server_push``, tick, ``db_notify``) and logged failures with the
    exception, traceback and ``repr(callback)``."""
    from djust.push import apush_to_view

    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)
    monkeypatch.setattr(PushDeferView, "exposure_policy", _mount_policy(policy))
    with override_settings(
        LIVEVIEW_ALLOWED_MODULES=[__name__], DEBUG=True, DJUST_TENANTS=None, DJUST_CONFIG={}
    ):
        request = await sync_to_async(make_request)()
        socket = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
        socket.scope.update(session=request.session, user=request.user, tenant=None)
        assert (await socket.connect())[0]
        await socket.receive_json_from(timeout=3)
        try:
            await socket.send_json_to(
                {"type": "mount", "view": __name__ + ".PushDeferView", "url": "/pd/"}
            )
            assert (await socket.receive_json_from(timeout=3))["type"] == "mount"
            monkeypatch.setattr(PushDeferView, "exposure_policy", policy)
            caplog.clear()
            with caplog.at_level(logging.DEBUG):
                await apush_to_view(__name__ + ".PushDeferView", handler="handle_later")
                if policy in (None, "invalid"):
                    await _expect_denial(socket)
                else:
                    await _poll_log(caplog, "Deferred callback", "Protected view operation failed")

            if policy == "legacy":
                assert "PUSH_DEFER_EXC_SENTINEL" in caplog.text
                assert "PUSH_DEFER_ARG_SENTINEL" in caplog.text
            elif policy in (None, "invalid"):
                # Refused before the handler queued anything.
                assert "PUSH_DEFER_EXC_SENTINEL" not in caplog.text
                assert "PUSH_DEFER_ARG_SENTINEL" not in caplog.text
            else:
                assert "PUSH_DEFER_EXC_SENTINEL" not in caplog.text
                assert "PUSH_DEFER_ARG_SENTINEL" not in caplog.text
                assert "Protected view operation failed" in caplog.text
        finally:
            await socket.disconnect()


class PushLayoutView(LiveView):
    exposure_policy = "legacy"
    template = "<div dj-root>push-layout</div>"

    def handle_swap(self, **kwargs):
        self.set_layout("exposure_push_layout.html")
        # Skip the render so the consumer's own _flush_all_pending runs its
        # _flush_pending_layout twin.
        self._skip_render = True


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
# None and invalid policies cannot reach the layout render: the context build
# refuses an unknown policy first ("unknown context exposure policy"), so the
# render the test patches never runs. Those cases would pass vacuously.
@pytest.mark.parametrize("policy", ["legacy", "explicit"])
async def test_consumer_layout_render_failure_is_value_free_for_nonlegacy_views(
    monkeypatch, caplog, policy
):
    """The consumer's ``_flush_pending_layout`` twin logged a ``set_layout``
    render failure with ``logger.exception`` (and re-raises under DEBUG, into
    ``server_push``'s already-protected catch)."""
    import django.template.loader as loader

    from djust.push import apush_to_view

    rendered = []

    def fail(*args, **kwargs):
        rendered.append(args[0] if args else None)
        raise ValueError("PUSH_LAYOUT_SENTINEL")

    monkeypatch.setattr(loader, "render_to_string", fail)
    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)
    monkeypatch.setattr(PushLayoutView, "exposure_policy", _mount_policy(policy))
    with override_settings(
        LIVEVIEW_ALLOWED_MODULES=[__name__], DEBUG=True, DJUST_TENANTS=None, DJUST_CONFIG={}
    ):
        request = await sync_to_async(make_request)()
        socket = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
        socket.scope.update(session=request.session, user=request.user, tenant=None)
        assert (await socket.connect())[0]
        await socket.receive_json_from(timeout=3)
        try:
            await socket.send_json_to(
                {"type": "mount", "view": __name__ + ".PushLayoutView", "url": "/pl/"}
            )
            assert (await socket.receive_json_from(timeout=3))["type"] == "mount"
            monkeypatch.setattr(PushLayoutView, "exposure_policy", policy)
            caplog.clear()
            with caplog.at_level(logging.DEBUG):
                await apush_to_view(__name__ + ".PushLayoutView", handler="handle_swap")
                await _poll_log(caplog, "template rendering raised", "Protected view operation")

            # Every case must reach the layout render; otherwise a value-free
            # line from an earlier failure would pass the test vacuously.
            assert rendered == ["exposure_push_layout.html"], (rendered, caplog.text[-600:])
            if policy == "legacy":
                assert "set_layout('exposure_push_layout.html') — template rendering raised" in (
                    caplog.text
                )
                assert "PUSH_LAYOUT_SENTINEL" in caplog.text
            else:
                assert "PUSH_LAYOUT_SENTINEL" not in caplog.text
                assert "Protected view operation failed" in caplog.text
        finally:
            await socket.disconnect()


class BugShareView(LiveView):
    exposure_policy = "legacy"
    time_travel_enabled = True
    template = "<div dj-root>{{ count }}</div>"

    def mount(self, request, **kwargs):
        self.count = 0


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("policy", ["legacy", "explicit"])
@pytest.mark.parametrize(
    "exc_type,sentinel,channel",
    [
        (ValueError, "BUG_SHARE_VALUE_SENTINEL", "client"),
        (KeyError, "BUG_SHARE_KEY_SENTINEL", "log"),
    ],
)
async def test_bug_capture_share_failure_is_value_free_for_nonlegacy_views(
    monkeypatch, caplog, policy, exc_type, sentinel, channel
):
    """``handle_bug_capture_share`` re-renders the view. A ``ValueError`` or
    ``RuntimeError`` from that render went to the client as ``str(exc)``; any
    other exception was logged with its traceback. Framework ``ExposureError``
    text (value-free by construction) is still passed through."""
    raised = []

    def failing_context(self, **kwargs):
        raised.append(True)
        raise exc_type(sentinel)

    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)
    monkeypatch.setattr(BugShareView, "exposure_policy", "legacy")
    with override_settings(
        LIVEVIEW_ALLOWED_MODULES=[__name__], DEBUG=True, DJUST_TENANTS=None, DJUST_CONFIG={}
    ):
        request = await sync_to_async(make_request)()
        socket = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
        socket.scope.update(session=request.session, user=request.user, tenant=None)
        assert (await socket.connect())[0]
        await socket.receive_json_from(timeout=3)
        try:
            await socket.send_json_to(
                {"type": "mount", "view": __name__ + ".BugShareView", "url": "/bs/"}
            )
            assert (await socket.receive_json_from(timeout=3))["type"] == "mount"
            monkeypatch.setattr(BugShareView, "exposure_policy", policy)
            monkeypatch.setattr(BugShareView, "get_context_data", failing_context)
            caplog.clear()
            with caplog.at_level(logging.DEBUG):
                await socket.send_json_to({"type": "bug_capture_share"})
                frame = await socket.receive_json_from(timeout=3)
            assert raised, "the re-render never ran; the test would be vacuous"
            assert frame["type"] == "error", frame
            text = json.dumps(frame) + caplog.text
            if policy == "legacy":
                # Unchanged legacy behaviour on the channel each exception used.
                if channel == "client":
                    assert sentinel in json.dumps(frame)
                else:
                    assert sentinel in caplog.text
            else:
                assert sentinel not in text
                assert frame["error"] == "bug_capture_share: failed to encode capture"
        finally:
            await socket.disconnect()
