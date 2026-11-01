"""The duck-typed accessibility hooks are deprecated (#3629, follow-up to #3503).

``LiveViewConsumer._flush_accessibility`` and ``ViewRuntime._flush_accessibility``
drain ``view._drain_announcements()`` and ``view._drain_focus()`` behind
``hasattr`` and send an ``accessibility`` / ``focus`` frame. Nothing in djust
defines either hook, and no client module assigns the ``window.djust.accessibility``
object the frames are handed to, so the feature only exists for an application
that supplies both halves. The 2.0 line already deleted the flush (#3539); the
1.x line keeps it working and warns.

What these tests pin:

* a view that defines a hook gets a ``DeprecationWarning`` at its ``class``
  statement, once, pointing at the application's file;
* nothing else warns;
* the warning changes nothing else: the socket and the SSE runtime still send
  the frame, so an application that relies on the hooks keeps working until 2.0.
"""

from __future__ import annotations

import abc
import warnings
import uuid

import pytest
from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer
from channels.testing import WebsocketCommunicator
from django.test import override_settings

from djust import LiveView, event_handler
from djust.js import JS
from djust.presence import PresenceMixin
from djust.sse import SSESession
from djust.tutorials.mixin import TutorialMixin
from djust.websocket import LiveViewConsumer

from ._ws_frames import has_type, receive_settled
from .test_exposure_runtime import make_request

MOD = __name__
HOOKS = ("_drain_announcements", "_drain_focus")


def _deprecations(record):
    return [w for w in record if issubclass(w.category, DeprecationWarning)]


def _define(body: dict, bases=(LiveView,)):
    """A LiveView subclass with ``body``, plus the DeprecationWarnings it raised."""
    with warnings.catch_warnings(record=True) as record:
        warnings.simplefilter("always")
        cls = type("Defined", bases, dict(body))
    return cls, _deprecations(record)


class TestTheWarning:
    @pytest.mark.parametrize("hook", HOOKS)
    def test_defining_a_hook_warns_once_with_the_deprecation_policy_text(self, hook):
        cls, warned = _define({hook: lambda self: None})

        assert len(warned) == 1, [str(w.message) for w in warned]
        text = str(warned[0].message)
        assert hook in text
        assert "deprecated since djust 1.3" in text
        assert "no earlier than 2.0.0" in text
        # The migration path is named, and the text stays short.
        if hook == "_drain_focus":
            assert "JS.focus()" in text and "push_commands()" in text
        else:
            assert 'role="status"' in text
        assert len(text) < 200, text
        assert "nothing in djust" not in text  # the flushers DO consume the hook
        assert cls is not None

    def test_the_warning_points_at_the_application_class_statement(self):
        with warnings.catch_warnings(record=True) as record:
            warnings.simplefilter("always")

            class Announcing(LiveView):
                def _drain_announcements(self):
                    return []

        warned = _deprecations(record)
        assert len(warned) == 1
        assert warned[0].filename == __file__, warned[0].filename

    def test_the_warning_points_at_the_application_whatever_the_base_order(self):
        """Another base's ``__init_subclass__`` or ``ABCMeta`` adds frames; count them."""

        class ChainsFirst:
            def __init_subclass__(cls, **kwargs):
                super().__init_subclass__(**kwargs)

        def hook(self):
            return []

        with warnings.catch_warnings(record=True) as record:
            warnings.simplefilter("always")

            class PresenceFirst(PresenceMixin, LiveView):
                _drain_announcements = hook

            class TutorialFirst(TutorialMixin, LiveView):
                _drain_announcements = hook

            class ChainingMixinFirst(ChainsFirst, LiveView):
                _drain_announcements = hook

            class AbcAfter(LiveView, abc.ABC):
                _drain_announcements = hook

            class MixinAfter(LiveView, ChainsFirst):
                _drain_announcements = hook

            class Plain(LiveView):
                _drain_announcements = hook

        warned = _deprecations(record)
        assert len(warned) == 6, [(w.filename, str(w.message)[:40]) for w in warned]
        assert {w.filename for w in warned} == {__file__}, sorted({w.filename for w in warned})

    def test_both_hooks_warn_separately(self):
        _, warned = _define({"_drain_announcements": lambda s: [], "_drain_focus": lambda s: None})

        texts = sorted(str(w.message) for w in warned)
        assert len(texts) == 2
        assert "_drain_announcements" in texts[0] and "_drain_focus" in texts[1]

    def test_a_hook_reached_through_a_plain_mixin_warns(self):
        class Mixin:
            def _drain_focus(self):
                return None

        _, warned = _define({}, bases=(Mixin, LiveView))

        assert len(warned) == 1 and "_drain_focus" in str(warned[0].message)

    def test_a_subclass_of_a_view_that_already_warned_does_not_warn_again(self):
        base, first = _define({"_drain_announcements": lambda s: []})
        _, second = _define({}, bases=(base,))
        # Overriding the hook is the same hook, not a new one.
        _, third = _define({"_drain_announcements": lambda s: ["x"]}, bases=(base,))

        assert len(first) == 1
        assert second == [] and third == []

    def test_an_ordinary_view_does_not_warn(self):
        _, warned = _define({"title": "x", "mount": lambda self, request, **kw: None})

        assert warned == []

    def test_liveview_defines_neither_hook(self):
        # The premise of the deprecation: no framework producer exists.
        for hook in HOOKS:
            assert not hasattr(LiveView, hook), hook


# --- the behaviour is unchanged on the two transports that still flush ---------------


with warnings.catch_warnings():
    warnings.simplefilter("ignore", DeprecationWarning)

    class AnnouncingView(LiveView):
        template = "<div dj-root>n={{ n }}</div>"

        def mount(self, request, **kwargs):
            self.n = 0
            self._pending_announcements = []
            self._pending_focus = None

        def get_context_data(self, **kwargs):
            return super().get_context_data(n=self.n, **kwargs)

        @event_handler()
        def speak(self, **kwargs):
            self.n += 1
            self._pending_announcements.append("Saved")
            self._pending_focus = ("#name", {"preventScroll": True})

        _listen_channels = frozenset({"a11y_3629"})

        def handle_info(self, message):
            self.n += 1
            self._pending_announcements.append("Pushed")
            self._pending_focus = ("#pushed", {})

        def _drain_announcements(self):
            out, self._pending_announcements = self._pending_announcements, []
            return out

        def _drain_focus(self):
            out, self._pending_focus = self._pending_focus, None
            return out


@pytest.fixture(autouse=True)
def _settings_for_test():
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[MOD], DEBUG=False, DJUST_TENANTS=None):
        yield


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_socket_still_sends_the_accessibility_and_focus_frames():
    request = await sync_to_async(make_request)()
    socket = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    socket.scope.update(session=request.session, user=request.user, tenant=None)
    assert (await socket.connect())[0]
    await socket.receive_json_from()
    await socket.send_json_to({"type": "mount", "view": f"{MOD}.AnnouncingView", "url": "/x/"})
    assert (await socket.receive_json_from())["type"] == "mount"

    await socket.send_json_to({"type": "event", "event": "speak", "params": {}})
    frames = await receive_settled(
        socket,
        lambda fs: has_type("accessibility")(fs) and has_type("focus")(fs),
    )

    assert [f for f in frames if f.get("type") == "accessibility"] == [
        {"type": "accessibility", "announcements": ["Saved"]}
    ]
    assert [f for f in frames if f.get("type") == "focus"] == [
        {"type": "focus", "selector": "#name", "options": {"preventScroll": True}}
    ]
    await socket.disconnect()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_socket_sends_the_frames_a_server_push_queued():
    """A turn the socket runs itself (``handle_info``) flushes through the consumer."""
    request = await sync_to_async(make_request)()
    socket = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    socket.scope.update(session=request.session, user=request.user, tenant=None)
    assert (await socket.connect())[0]
    await socket.receive_json_from()
    await socket.send_json_to({"type": "mount", "view": f"{MOD}.AnnouncingView", "url": "/x/"})
    assert (await socket.receive_json_from())["type"] == "mount"

    await get_channel_layer().group_send(
        "djust_db_notify_a11y_3629",
        {"type": "db_notify", "channel": "a11y_3629", "payload": {}},
    )
    frames = await receive_settled(
        socket,
        lambda fs: has_type("accessibility")(fs) and has_type("focus")(fs),
    )

    assert {"type": "accessibility", "announcements": ["Pushed"]} in frames, frames
    assert {"type": "focus", "selector": "#pushed", "options": {}} in frames
    await socket.disconnect()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_sse_runtime_still_sends_the_accessibility_and_focus_frames():
    request = await sync_to_async(make_request)()
    session = SSESession(str(uuid.uuid4()))
    session._request = request
    await session.runtime.dispatch_mount(
        {"type": "mount", "view": f"{MOD}.AnnouncingView", "url": "/x/"}
    )
    while not session.queue.empty():
        session.queue.get_nowait()

    fresh = await sync_to_async(make_request)(request.session.session_key)
    await session.dispatch(fresh, {"type": "event", "event": "speak", "params": {}})
    frames = []
    while not session.queue.empty():
        frame = session.queue.get_nowait()
        if frame is not None:
            frames.append(frame)

    assert {"type": "accessibility", "announcements": ["Saved"]} in frames, frames
    assert {"type": "focus", "selector": "#name", "options": {"preventScroll": True}} in frames


# --- the documented replacement, and where it differs from the hook --------------------


class LiveRegionView(LiveView):
    template_name = None
    template = '<div dj-root><p role="status" aria-live="polite">{{ status }}</p></div>'

    def mount(self, request, **kwargs):
        self.status = ""
        self._flip = False

    def get_context_data(self, **kwargs):
        return super().get_context_data(status=self.status, **kwargs)

    @event_handler()
    def save(self, **kwargs):
        self.status = "Order saved"
        self.push_commands(JS.focus("#order-summary"))

    @event_handler()
    def save_alternating(self, **kwargs):
        # The documented workaround: a changing no-break-space suffix makes the
        # live region's text differ from the previous turn's.
        self._flip = not self._flip
        self.status = "Order saved" + (" " if self._flip else "")
        self.push_commands(JS.focus("#order-summary"))


async def _live_region_turn(socket, event):
    await socket.send_json_to({"type": "event", "event": event, "params": {}})
    frames = await receive_settled(
        socket, lambda fs: any(f.get("type") in ("patch", "noop", "html_update") for f in fs)
    )
    return [f.get("type") for f in frames if f.get("type") in ("patch", "noop", "html_update")]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_repeated_identical_message_changes_nothing_in_the_live_region():
    """The replacement is not the hook: an unchanged live region announces nothing."""
    request = await sync_to_async(make_request)()
    socket = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    socket.scope.update(session=request.session, user=request.user, tenant=None)
    assert (await socket.connect())[0]
    await socket.receive_json_from()
    await socket.send_json_to({"type": "mount", "view": f"{MOD}.LiveRegionView", "url": "/x/"})
    assert (await socket.receive_json_from())["type"] == "mount"

    first = await _live_region_turn(socket, "save")
    second = await _live_region_turn(socket, "save")
    assert first == ["patch"], first
    assert second == ["noop"], second  # same text: no DOM change, so no announcement

    third = await _live_region_turn(socket, "save_alternating")  # "Order saved" + nbsp
    fourth = await _live_region_turn(socket, "save_alternating")  # back to "Order saved"
    assert third == ["patch"] and fourth == ["patch"], (third, fourth)
    await socket.disconnect()
