"""Regression tests for #1940 — ``_run_async_work`` teardown semantics.

``LiveViewConsumer._run_async_work`` runs as a detached ``ensure_future`` task.
It captures ``view = self.view_instance`` BEFORE its first ``await`` (the
callback). During that await window, ``disconnect`` (-> ``view_instance = None``)
or ``handle_live_redirect_mount`` / a re-mount (-> ``view_instance`` reassigned to
a NEW view) can run. Pre-fix, the completed task wrote against the captured
(now-stale) view: ``handle_async_result`` + ``_sync_state_to_rust`` +
``render_with_diff`` + a ``source="async"`` frame — contaminating a torn-down or
replaced view (origin/main re-read live -> AttributeError on disconnect /
NEW-view contamination on re-mount; #1939 captured the OLD view -> stale write).

The fix: an identity-guard after the callback await (#245/#1198 class). If the
consumer's LIVE view is no longer the captured one, the task drops its
re-render. These tests force the teardown deterministically by blocking the
async callback on a test-controlled event, mutating ``view_instance`` mid-await,
then releasing — and assert the stale view receives NO post-teardown writes and
NO frame is sent.
"""

import asyncio

import pytest

from djust import LiveView


class _SpyView(LiveView):
    """Minimal view stand-in that records every post-callback write.

    Implements just the surface ``_run_async_work`` touches after the callback
    returns: ``handle_async_result``, ``_sync_state_to_rust``,
    ``render_with_diff``, plus the strip/extract helpers used by the HTML
    fallback. Every call appends to ``self.writes`` so the test can assert the
    stale view was never written to once teardown happened.
    """

    exposure_policy = "explicit"
    template = "<div dj-root>x</div>"

    def __init__(self, name="spy", **kwargs):
        super().__init__(**kwargs)
        self.name = name
        self.writes = []

    def _initialize_rust_view(self, request=None):
        pass

    def handle_async_result(self, task_name, result=None, error=None):
        self.writes.append(("handle_async_result", task_name, result, error))

    def _sync_state_to_rust(self):
        self.writes.append(("_sync_state_to_rust",))

    def render_with_diff(self):
        self.writes.append(("render_with_diff",))
        # (html, patches, version) — patches=None forces the HTML fallback path,
        # which also writes against the view via the strip/extract helpers.
        return ("<div>x</div>", None, 1)

    def _strip_comments_and_whitespace(self, h):
        self.writes.append(("_strip_comments_and_whitespace",))
        return h

    def _extract_liveview_content(self, h):
        self.writes.append(("_extract_liveview_content",))
        return h


async def _make_consumer(view):
    """A real ``LiveViewConsumer`` that mounted ``view`` (an explicit view's
    background work is authorized by the runtime that mounted it), sends
    captured, not transmitted. The mount's own writes are cleared."""
    from djust.tests._live_session import amount_on_consumer
    from djust.websocket import LiveViewConsumer

    consumer = LiveViewConsumer()
    consumer.sent_frames = []

    async def _capture(data):
        consumer.sent_frames.append(data)

    # ``_send_update`` calls ``send_json``; capture every outbound frame.
    consumer.send_json = _capture  # type: ignore[assignment]
    await amount_on_consumer(consumer, f"{__name__}._SpyView", view=view)
    consumer.sent_frames.clear()
    view.writes.clear()

    async def _noop():
        return None

    # ``_run_async_work`` flushes pending queues after a successful re-render;
    # stub it so the test doesn't depend on those subsystems.
    consumer._flush_all_pending = _noop  # type: ignore[assignment]
    consumer._flush_push_events = _noop  # type: ignore[assignment]
    return consumer


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
class TestRunAsyncWorkTeardown:
    """The detached async task must not write against a stale/replaced view."""

    async def test_disconnect_during_await_drops_stale_render(self):
        """disconnect (view_instance -> None) mid-callback -> no stale write."""
        view = _SpyView("old")
        consumer = await _make_consumer(view)

        started = asyncio.Event()
        release = asyncio.Event()

        async def slow_callback():
            started.set()
            await release.wait()  # the await window where teardown happens
            return "done"

        task = asyncio.ensure_future(consumer._run_async_work("work", slow_callback, (), {}))

        # Wait until the callback is parked on ``release`` — we are now inside
        # the await window with ``view`` captured.
        await asyncio.wait_for(started.wait(), timeout=1.0)

        # Simulate disconnect tearing the view down mid-await.
        consumer.view_instance = None

        # Let the callback finish; the task resumes and hits the identity-guard.
        release.set()
        await asyncio.wait_for(task, timeout=1.0)

        # The stale view must receive NO post-callback writes, and NO frame
        # must be sent for a view that no longer exists.
        assert view.writes == [], "stale view was written to after disconnect mid-await: %r" % (
            view.writes,
        )
        assert consumer.sent_frames == [], "a frame was sent for a torn-down view: %r" % (
            consumer.sent_frames,
        )

    async def test_remount_during_await_does_not_contaminate_new_view(self):
        """re-mount (view_instance -> NEW view) mid-callback -> old view dropped,
        new view untouched by the old task."""
        old_view = _SpyView("old")
        new_view = _SpyView("new")
        consumer = await _make_consumer(old_view)

        started = asyncio.Event()
        release = asyncio.Event()

        async def slow_callback():
            started.set()
            await release.wait()
            return "done"

        task = asyncio.ensure_future(consumer._run_async_work("work", slow_callback, (), {}))
        await asyncio.wait_for(started.wait(), timeout=1.0)

        # Simulate a live_redirect / re-mount swapping in a brand-new view.
        consumer.view_instance = new_view

        release.set()
        await asyncio.wait_for(task, timeout=1.0)

        # Neither view may be written by the OLD task: the old view is stale,
        # and the new view's mount owns its own render lifecycle.
        assert old_view.writes == [], "stale OLD view written after re-mount: %r" % (
            old_view.writes,
        )
        assert new_view.writes == [], "NEW view contaminated by the stale async task: %r" % (
            new_view.writes,
        )
        assert consumer.sent_frames == []

    async def test_no_teardown_renders_normally(self):
        """Control: with no teardown, the normal async-work re-render still runs.

        This is the gate-off guard — it proves the test asserts on the teardown
        edge specifically, not on async work being broken. Removing the
        identity-guard must NOT change this case (it stays green); the two
        teardown cases above are what flip.
        """
        view = _SpyView("live")
        consumer = await _make_consumer(view)

        async def fast_callback():
            return "done"

        await consumer._run_async_work("work", fast_callback, (), {})

        # Normal path: handle_async_result + sync + render + HTML-fallback strip
        # all ran against the (still-live) view, and a frame was sent.
        names = [w[0] for w in view.writes]
        assert "handle_async_result" in names
        assert "_sync_state_to_rust" in names
        assert "render_with_diff" in names
        assert len(consumer.sent_frames) == 1
        assert consumer.sent_frames[0].get("source") == "async"

    async def test_error_path_disconnect_during_await_drops_stale_render(self):
        """A callback that RAISES during the await window after a disconnect must
        not run the error-state re-render against the stale view."""
        view = _SpyView("old")
        consumer = await _make_consumer(view)

        started = asyncio.Event()
        release = asyncio.Event()

        async def failing_callback():
            started.set()
            await release.wait()
            raise ValueError("boom")

        task = asyncio.ensure_future(consumer._run_async_work("work", failing_callback, (), {}))
        await asyncio.wait_for(started.wait(), timeout=1.0)

        consumer.view_instance = None  # disconnect mid-await

        release.set()
        await asyncio.wait_for(task, timeout=1.0)

        # The error-path handle_async_result + re-render must be skipped.
        assert view.writes == [], "stale view written by the error path after disconnect: %r" % (
            view.writes,
        )
        assert consumer.sent_frames == []


# --------------------------------------------------------------------------- #
# #4155: the two identity guards each catch a case the other cannot.
#
# * After the callback, before the result turn (``_run_async_work_body``, the
#   guard right after ``run_async_callback`` / in the ``except`` arm): a view torn
#   down during the callback gets no ``async``/``result`` turn at all, so no turn
#   middleware (an app's, or the built-in ``reauth``, which can close the socket)
#   runs for a view that is gone.
# * First thing inside the result turn (``complete`` / ``complete_error``, under
#   the render lock): a view replaced while the result WAITED FOR THE LOCK, after
#   the first guard passed, gets no ``handle_async_result``.
# --------------------------------------------------------------------------- #

TURNS = []


async def record_turns(turn, next):
    """``LIVEVIEW_MIDDLEWARE`` entry: records every turn and the view it is for."""
    TURNS.append((turn.kind, turn.source, turn.view))
    return await next(turn)


class _ObservedLock(asyncio.Lock):
    """The consumer's render lock, telling the test when someone waits for it."""

    def __init__(self):
        super().__init__()
        self.contended = asyncio.Event()

    async def acquire(self):
        if self.locked():
            self.contended.set()
        return await super().acquire()


async def _callback_for(outcome, started, release):
    async def callback():
        started.set()
        await release.wait()
        if outcome == "error":
            raise ValueError("boom")
        return "done"

    return callback


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("outcome", ["result", "error"])
@pytest.mark.parametrize("teardown", ["disconnect", "remount"])
async def test_a_view_torn_down_during_the_callback_gets_no_result_turn(outcome, teardown):
    from django.test import override_settings

    old_view = _SpyView("old")
    consumer = await _make_consumer(old_view)
    started, release = asyncio.Event(), asyncio.Event()
    callback = await _callback_for(outcome, started, release)
    TURNS.clear()
    with override_settings(LIVEVIEW_MIDDLEWARE=[f"{__name__}.record_turns"]):
        task = asyncio.ensure_future(consumer._run_async_work("work", callback, (), {}))
        await asyncio.wait_for(started.wait(), timeout=30)
        consumer.view_instance = None if teardown == "disconnect" else _SpyView("new")
        release.set()
        await asyncio.wait_for(task, timeout=30)
    # The start turn ran while the view was mounted; no result turn follows it.
    assert [(kind, source) for kind, source, _ in TURNS] == [("async", "start")], TURNS
    assert old_view.writes == [] and consumer.sent_frames == []


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("outcome", ["result", "error"])
async def test_a_view_replaced_while_the_result_waits_for_the_lock_gets_no_handler(outcome):
    old_view, new_view = _SpyView("old"), _SpyView("new")
    consumer = await _make_consumer(old_view)
    lock = consumer._render_lock = _ObservedLock()
    started, release = asyncio.Event(), asyncio.Event()
    callback = await _callback_for(outcome, started, release)
    task = asyncio.ensure_future(consumer._run_async_work("work", callback, (), {}))
    await asyncio.wait_for(started.wait(), timeout=30)
    # Another turn holds the render lock when the callback finishes: the result
    # passes the after-callback guard (the view is still mounted) and waits.
    await lock.acquire()
    try:
        release.set()
        await asyncio.wait_for(lock.contended.wait(), timeout=30)
        assert consumer.view_instance is old_view
        consumer.view_instance = new_view  # replaced while the result waits
    finally:
        lock.release()
    await asyncio.wait_for(task, timeout=30)
    assert old_view.writes == [], old_view.writes
    assert new_view.writes == [], new_view.writes
    assert consumer.sent_frames == []
