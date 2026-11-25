"""#4123: a background result that changed nothing renders and sends nothing.

The async result paths (the runtime's ``_render_async_result`` and the
consumer's ``_render_background``) synced and rendered unconditionally. When
nothing changed since the last render, the sync named no changed key, so Rust
took its "no change info" branch and rendered the whole template to send
``patches: []``: the normal case when several tasks finish close together.

``_sync_state_to_rust()`` now reports whether a render has anything to do, and
both result paths skip the render and the frame when it says no. The client's
loading state ends with the batch's ``async_complete`` frame (#4309), which is
sent whether or not a result rendered. A result that changed something,
including by mutating a list in place, still patches. A background completion
has no post-event snapshot, so the #3999 walk reuse never applies to it: the
sync fingerprints the context afresh.
"""

from __future__ import annotations

import pytest
from asgiref.sync import sync_to_async
from channels.testing import WebsocketCommunicator
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.test import override_settings

from djust import LiveView, state
from djust._background_render import UNCHANGED, snapshot_delivers_nothing
from djust.decorators import background, event_handler
from djust.websocket import LiveViewConsumer

from ._ws_frames import drain_extra, receive_until

SETTINGS = dict(DEBUG=False, DJUST_TENANTS=None, DJUST_CONFIG={})

#: render_with_diff() calls, per view class name.
RENDERS: dict = {}


class _Counted(LiveView):
    """Counts the template renders a result path asks for."""

    def render_with_diff(self, *args, **kwargs):
        RENDERS[type(self).__name__] = RENDERS.get(type(self).__name__, 0) + 1
        return super().render_with_diff(*args, **kwargs)


class ResultView(_Counted):
    exposure_policy = "explicit"
    template = (
        "<div dj-root><p>{{ count }}</p><i>{{ error }}</i>"
        "<ul>{% for e in entries %}<li>{{ e.name }}</li>{% endfor %}</ul></div>"
    )
    count = state(0)
    error = state("")
    entries = state()

    def mount(self, request, **kwargs):
        self.count = 0
        self.error = ""
        self.entries = [{"name": "row0"}, {"name": "row1"}]

    def handle_async_result(self, name, result=None, error=None):
        if name == "set_by_handler":
            self.count = 42
        if error is not None and name == "fail_recorded":
            self.error = "failed"

    # --- callbacks -----------------------------------------------------
    def _bump(self):
        self.count += 1

    def _nothing(self):
        return None

    def _same_value(self):
        self.count = self.count

    def _append(self):
        self.entries.append({"name": "appended"})

    def _rename_in_place(self):
        self.entries[0]["name"] = "renamed"

    def _fail(self):
        raise RuntimeError("background failure")

    # --- events --------------------------------------------------------
    @event_handler()
    def start_bump(self, **kwargs):
        self.start_async(self._bump, name="bump")

    @event_handler()
    def start_nothing(self, **kwargs):
        self.start_async(self._nothing, name="nothing")

    @event_handler()
    def start_handler_sets(self, **kwargs):
        self.start_async(self._nothing, name="set_by_handler")

    @event_handler()
    def start_fail_recorded(self, **kwargs):
        self.start_async(self._fail, name="fail_recorded")

    @event_handler()
    def start_fail_silent(self, **kwargs):
        self.start_async(self._fail, name="fail_silent")

    @event_handler()
    def start_append(self, **kwargs):
        self.start_async(self._append, name="append")

    @event_handler()
    @background
    def bg_bump(self, **kwargs):
        self.count += 1

    @event_handler()
    @background
    def bg_nothing(self, **kwargs):
        self.count = self.count


class NoHandlerView(_Counted):
    """ResultView's template and state, without a ``handle_async_result``."""

    exposure_policy = "explicit"
    template = ResultView.template
    count = state(0)
    error = state("")
    entries = state()

    def mount(self, request, **kwargs):
        self.count = 0
        self.error = ""
        self.entries = []

    def _nothing(self):
        return None

    def _fail(self):
        raise RuntimeError("background failure")


class Label:
    """A plain object: the change detector can compare it only by ``id()``."""

    def __init__(self, label):
        self.label = label


class PlainObjectView(_Counted):
    exposure_policy = "explicit"
    template = "<div dj-root><b>{{ obj.label }}</b></div>"
    obj = state()

    def mount(self, request, **kwargs):
        self.obj = Label("before")

    def _relabel(self):
        self.obj.label = "after"

    def _nothing(self):
        return None


# ---------------------------------------------------------------------------
# Socket harness (the runtime's result turn)
# ---------------------------------------------------------------------------


def _session():
    session = SessionStore()
    session.save()
    return session


async def _mounted():
    socket = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    socket.scope.update(session=await sync_to_async(_session)(), user=AnonymousUser(), tenant=None)
    assert (await socket.connect())[0]
    await socket.receive_json_from()
    await socket.send_json_to({"type": "mount", "view": __name__ + ".ResultView", "url": "/p/"})
    assert (await socket.receive_json_from())["type"] == "mount"
    return socket


async def _event(socket, name, ref):
    """The event's frames up to its batch's ``async_complete``, then any late ones.

    ``async_complete`` is sent once every task of the batch settled, after the
    task's own result frame (if any), so a result frame cannot be missed.
    """
    await socket.send_json_to({"type": "event", "event": name, "params": {}, "ref": ref})
    frames = await receive_until(
        socket, lambda seen: any(f.get("type") == "async_complete" for f in seen)
    )
    frames += await drain_extra(socket)
    (reply,) = [f for f in frames if f.get("ref") == ref]
    assert reply.get("async_pending") is True, frames
    (complete,) = [f for f in frames if f.get("type") == "async_complete"]
    assert complete["async_batch"] == reply["async_batch"], frames
    return frames


def _results(frames):
    return [f for f in frames if f.get("source") == "async"]


async def _run(events):
    """Mount, send each event in turn; returns ``[(frames, renders), ...]``."""
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__], **SETTINGS):
        socket = await _mounted()
        out = []
        try:
            for ref, name in enumerate(events, start=1):
                before = RENDERS.get("ResultView", 0)
                frames = await _event(socket, name, ref)
                out.append((frames, RENDERS.get("ResultView", 0) - before))
        finally:
            await socket.disconnect()
        return out


def _patch_texts(frame):
    """Every text a frame's patches set or insert, as one string per patch."""
    import json

    return [json.dumps(p) for p in frame.get("patches") or []]


def _sets(frame, text):
    return any(f'"{text}"' in p for p in _patch_texts(frame))


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_result_that_changes_state_still_patches():
    ((frames, renders),) = await _run(["start_bump"])
    (result,) = _results(frames)
    assert result["type"] == "patch", frames
    assert _sets(result, "1"), result
    assert result["event_name"] == "start_bump"
    assert renders == 1


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_result_that_changes_nothing_renders_and_sends_nothing():
    ((frames, renders),) = await _run(["start_nothing"])
    assert _results(frames) == [], frames
    assert renders == 0
    # The loading state still ends: the batch's completion frame arrived (_event).


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_handle_async_result_that_sets_state_patches():
    ((frames, renders),) = await _run(["start_handler_sets"])
    (result,) = _results(frames)
    assert _sets(result, "42"), result
    assert renders == 1


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_error_path_patches_what_the_handler_recorded():
    ((frames, renders),) = await _run(["start_fail_recorded"])
    (result,) = _results(frames)
    assert _sets(result, "failed"), result
    assert renders == 1
    assert "background failure" not in str(frames)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_error_path_that_changes_nothing_sends_nothing_but_ends_loading():
    ((frames, renders),) = await _run(["start_fail_silent"])
    assert _results(frames) == [], frames
    assert renders == 0


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_background_decorator_changed_and_unchanged():
    (bumped, bumped_renders), (same, same_renders) = await _run(["bg_bump", "bg_nothing"])
    (result,) = _results(bumped)
    assert _sets(result, "1"), result
    assert bumped_renders == 1
    assert _results(same) == [], same
    assert same_renders == 0


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_in_place_list_mutation_in_the_callback_is_detected():
    ((frames, renders),) = await _run(["start_append"])
    (result,) = _results(frames)
    assert result["type"] == "patch", frames
    assert result["patches"], result
    assert "appended" in str(result["patches"]), result
    assert renders == 1


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_unchanged_result_does_not_leave_the_next_event_unsynced():
    """The skipped result synced without rendering. The next turn must still
    sync and patch: a stale "already synced" mark would skip its sync."""
    (_, _), (frames, renders) = await _run(["start_nothing", "start_bump"])
    (result,) = _results(frames)
    assert _sets(result, "1"), result


# ---------------------------------------------------------------------------
# The consumer's own result turn (server-originated work) and cancellation
# ---------------------------------------------------------------------------


async def _consumer_with_view(view_name="ResultView"):
    from djust.tests._live_session import amount_on_consumer

    consumer = LiveViewConsumer()
    consumer.sent = []

    async def _capture(payload):
        consumer.sent.append(payload)

    consumer.send_json = _capture
    view = await amount_on_consumer(consumer, f"{__name__}.{view_name}")
    # The first committed turn after this harness's mount saves the view's
    # state for the first time: its frame carries a new turn serial the page
    # must learn, so it is sent (with ``patches: []``) even though nothing
    # rendered changed. Settle that here; the socket tests' mount and event
    # reply do it for them.
    await consumer._run_async_work("settle", view._nothing, (), {}, event_name=None)
    consumer.sent.clear()
    return consumer, view


def _async_frames(consumer):
    return [f for f in consumer.sent if f.get("source") == "async"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_consumer_result_renders_once_then_skips_repeats():
    """The issue's repro: the first completion renders, later ones that find
    the state already rendered render nothing and send nothing."""
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__], **SETTINGS):
        consumer, view = await _consumer_with_view()
        before = RENDERS.get("ResultView", 0)
        await consumer._run_async_work("one", view._bump, (), {}, event_name=None)
        (first,) = _async_frames(consumer)
        assert _sets(first, "1"), first
        assert RENDERS["ResultView"] - before == 1

        for name, callback in (("two", view._same_value), ("three", view._nothing)):
            await consumer._run_async_work(name, callback, (), {}, event_name=None)
        assert len(_async_frames(consumer)) == 1, consumer.sent
        assert RENDERS["ResultView"] - before == 1


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_consumer_result_detects_in_place_nested_mutation():
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__], **SETTINGS):
        consumer, view = await _consumer_with_view()
        await consumer._run_async_work("rename", view._rename_in_place, (), {}, event_name=None)
        (frame,) = _async_frames(consumer)
        assert "renamed" in str(frame.get("patches")), frame


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("recorded", [False, True], ids=["silent", "recorded"])
async def test_consumer_error_arm(recorded):
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__], **SETTINGS):
        consumer, view = await _consumer_with_view()
        before = RENDERS.get("ResultView", 0)
        name = "fail_recorded" if recorded else "fail_silent"
        await consumer._run_async_work(name, view._fail, (), {}, event_name="work")
        frames = _async_frames(consumer)
        if recorded:
            (frame,) = frames
            assert _sets(frame, "failed"), frame
            assert RENDERS["ResultView"] - before == 1
        else:
            assert frames == [], consumer.sent
            assert RENDERS.get("ResultView", 0) - before == 0


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_consumer_error_arm_without_a_handler_still_sends_its_frame():
    """No handler ran, so nothing recorded the failure: the frame is sent as
    before (#2963), even though nothing changed (the conservative arm)."""
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__], **SETTINGS):
        consumer, view = await _consumer_with_view("NoHandlerView")
        await consumer._run_async_work("fail", view._fail, (), {}, event_name=None)
        (frame,) = _async_frames(consumer)
        assert frame["type"] in ("patch", "html_update"), frame


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("changed_before", [False, True], ids=["unchanged", "changed"])
async def test_cancelled_task_settle_frame_is_never_skipped(changed_before):
    """The settle frame names the event and ends its loading state on a path
    with no batch: it is sent whether or not anything changed."""
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__], **SETTINGS):
        consumer, view = await _consumer_with_view()
        if changed_before:
            view.count = 7  # changed since the last render, never sent
        view._async_cancelled = {"job"}
        await consumer._run_async_work("job", view._bump, (), {}, event_name="work")
        assert view.count == (7 if changed_before else 0)  # the callback never ran
        (frame,) = _async_frames(consumer)
        assert frame["event_name"] == "work"
        if changed_before:
            assert _sets(frame, "7"), frame


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_in_place_write_on_a_plain_object_still_delivers():
    """A plain object is compared only by ``id()``, so an in-place attribute
    write is invisible to the detector. The skip must never drop what the
    unconditional render delivered: such a value always renders."""
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__], **SETTINGS):
        consumer, view = await _consumer_with_view("PlainObjectView")
        await consumer._run_async_work("relabel", view._relabel, (), {}, event_name=None)
        (frame,) = _async_frames(consumer)
        assert "after" in str(frame), frame


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_sync_reports_a_render_for_an_opaque_value_and_none_without_one():
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__], **SETTINGS):
        _consumer, plain = await _consumer_with_view("PlainObjectView")
        assert await sync_to_async(plain._sync_state_to_rust)() is True
        _consumer, rows = await _consumer_with_view("ResultView")
        assert await sync_to_async(rows._sync_state_to_rust)() is False
        rows.entries.append({"name": "x"})
        assert await sync_to_async(rows._sync_state_to_rust)() is True


# ---------------------------------------------------------------------------
# Units
# ---------------------------------------------------------------------------


def test_snapshot_fields_decide_whether_a_frame_is_owed():
    assert snapshot_delivers_nothing({}, None)
    assert snapshot_delivers_nothing(None, 3)
    fields = {"view": "v", "state_snapshot_signed": None, "state_serial": 3}
    assert snapshot_delivers_nothing(fields, 3)
    # A save wrote a new state: the page must learn its serial.
    assert not snapshot_delivers_nothing(fields, 2)
    # A client-persisted token can change while the template did not.
    assert not snapshot_delivers_nothing({**fields, "state_snapshot_signed": "tok"}, 3)


def test_unchanged_sentinel_carries_no_frame():
    assert UNCHANGED.unchanged is True
    assert UNCHANGED.patches is None and UNCHANGED.content is None
    assert UNCHANGED.send_fields == {}
