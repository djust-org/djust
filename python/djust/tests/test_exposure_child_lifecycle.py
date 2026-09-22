"""Explicit subtree teardown must remove real work, not just call a hook."""

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from djust import LiveView


def _authorized_runtime(view):
    """A bare runtime that grants an explicit root authority for its turns.

    These tests exercise task tracking and cancellation, not authorization: a
    real explicit root authorizes each background turn against its mount
    binding (test_exposure_root_background_turns.py covers that). An unmounted
    runtime has no binding, so authority is granted here.
    """
    from djust.runtime import ViewRuntime
    from djust.tests.test_runtime_state_save_tt_1894 import MockTransport

    host = ViewRuntime(MockTransport())
    host.view_instance = view
    host.authorize_explicit_turn = AsyncMock()
    host.commit_explicit_turn = AsyncMock(return_value=True)
    return host


def _authorized_consumer(view):
    """A bare consumer whose runtime grants an explicit root authority.

    Same reason as :func:`_authorized_runtime`: these tests are about task
    cancellation, and the consumer's server-originated turns authorize through
    its runtime (test_exposure_consumer_turns.py covers that).
    """
    from djust.websocket import LiveViewConsumer

    host = LiveViewConsumer()
    host.view_instance = view
    host._runtime = SimpleNamespace(
        view_instance=view,
        authorize_explicit_turn=AsyncMock(),
        commit_explicit_turn=AsyncMock(return_value=True),
        _parameter_contracts_active=False,
    )
    return host


class LifecycleView(LiveView):
    exposure_policy = "explicit"

    def __init__(self):
        super().__init__()
        self._hooks = []

    def _cleanup_on_unregister(self):
        self._hooks.append("unregister")

    def _on_sticky_unmount(self):
        self._hooks.append("unmount")
        super()._on_sticky_unmount()


@pytest.fixture(autouse=True)
def staged(monkeypatch):
    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)


def tree():
    parent, child, grandchild = LifecycleView(), LifecycleView(), LifecycleView()
    parent._register_child("child", child)
    child._register_child("grandchild", grandchild)
    for view in (child, grandchild):
        view.start_async(lambda: None, name="pending")
        view.defer(lambda: None)
    return parent, child, grandchild


def test_unregister_clears_nested_ownership_and_pending_work():
    parent, child, grandchild = tree()
    parent._unregister_child("child")
    for view in (child, grandchild):
        assert not view._get_all_child_views()
        assert view._parent_view is None
        assert view._view_id is None
        assert not view._async_tasks
        assert not view._drain_deferred()
        assert view._hooks == ["unregister", "unmount"]
    parent._unregister_child("child")
    assert grandchild._hooks == ["unregister", "unmount"]


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["runtime", "websocket"])
@pytest.mark.parametrize("from_thread", [False, True])
async def test_cancel_all_stops_running_coroutine_and_drops_queued_work(transport, from_thread):
    from asgiref.sync import sync_to_async

    view = LifecycleView()
    entered, stopped = asyncio.Event(), asyncio.Event()

    async def running():
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    view.start_async(running, name="running")
    if transport == "runtime":
        host = _authorized_runtime(view)
        host._dispatch_async_work("start")
    else:
        host = _authorized_consumer(view)
        await host._dispatch_async_work()
    await asyncio.wait_for(entered.wait(), 1)
    handles = tuple(getattr(view, "_async_task_handles", ()))
    try:
        view.start_async(lambda: pytest.fail("Cancelled queued callback ran"), name="queued")
        if from_thread:
            await sync_to_async(view.cancel_async_all)()
        else:
            view.cancel_async_all()
        await asyncio.wait_for(stopped.wait(), 1)
        assert handles and all(handle.cancelled() for handle in handles)
        assert not view._async_tasks
        assert not view._async_task_handles
    finally:
        for handle in handles:
            handle.cancel()
        if handles:
            await asyncio.gather(*handles, return_exceptions=True)


@pytest.mark.asyncio
async def test_post_render_discard_cleans_entire_preserved_subtree():
    from djust.runtime import WSConsumerTransport

    parent, child, grandchild = tree()
    frames = []

    async def send_json(frame):
        frames.append(frame)

    consumer = SimpleNamespace(_sticky_preserved={"child": child}, send_json=send_json)
    await WSConsumerTransport(consumer).on_mount_render_ready(LifecycleView(), "<div></div>")
    assert not child._get_all_child_views()
    assert not grandchild._async_tasks
    assert child._hooks == ["unmount"]
    assert grandchild._hooks == ["unmount"]
    assert not parent._get_all_child_views()
    assert frames == [{"type": "sticky_hold", "views": []}]


def test_broken_hook_does_not_skip_descendants_or_log_values(caplog):
    parent, child, grandchild = tree()

    def broken():
        raise RuntimeError("PRIVATE_CLEANUP_SENTINEL")

    grandchild._cleanup_on_unregister = broken
    parent._unregister_child("child")
    assert not grandchild._async_tasks
    assert grandchild._hooks == ["unmount"]
    assert child._hooks == ["unregister", "unmount"]
    assert "PRIVATE_CLEANUP_SENTINEL" not in caplog.text
    assert "cleanup failed" in caplog.text


def test_reentrant_cleanup_and_later_cleanup_run_hooks_once():
    from djust._child_lifecycle import dispose_child_subtree

    parent, child, grandchild = tree()
    calls = []

    def reentrant():
        calls.append(1)
        dispose_child_subtree(child)

    child._cleanup_on_unregister = reentrant
    parent._unregister_child("child")
    dispose_child_subtree(child, navigation=True)
    assert calls == [1]
    assert grandchild._hooks == ["unregister", "unmount"]


def test_disposed_instances_cannot_be_registered_or_queue_more_work():
    parent, child, grandchild = tree()
    parent._unregister_child("child")
    with pytest.raises(RuntimeError, match="disposed"):
        parent._register_child("again", child)
    with pytest.raises(RuntimeError, match="disposed"):
        child._register_child("new", LifecycleView())
    for view in (child, grandchild):
        with pytest.raises(RuntimeError, match="disposed"):
            view.start_async(lambda: None)
        with pytest.raises(RuntimeError, match="disposed"):
            view.defer(lambda: None)


def test_foreign_registry_alias_does_not_dispose_other_owners_child():
    parent, child, grandchild = tree()
    other, foreign = LifecycleView(), LifecycleView()
    other._register_child("owned", foreign)
    child._child_views["bad_alias"] = foreign
    parent._unregister_child("child")
    assert other._get_child_view("owned") is foreign
    assert not foreign._hooks
    assert foreign._parent_view is other
    assert grandchild._hooks == ["unregister", "unmount"]


def test_cyclic_owned_graph_is_detached_and_cleaned_once():
    from djust._child_lifecycle import dispose_child_subtree

    first, second = LifecycleView(), LifecycleView()
    first._register_child("second", second)
    second._register_child("first", first)
    dispose_child_subtree(first)
    for view in (first, second):
        assert not view._get_all_child_views()
        assert view._parent_view is None
        assert view._hooks == ["unregister", "unmount"]


@pytest.mark.asyncio
async def test_threaded_render_teardown_cancels_waiters_on_their_own_loop():
    from asgiref.sync import sync_to_async

    parent, child, grandchild = tree()
    loop = asyncio.get_running_loop()
    was_debug = loop.get_debug()
    loop.set_debug(True)  # asyncio rejects cross-thread Future.cancel misuse
    waiter = asyncio.create_task(grandchild.wait_for_event("never", timeout=10))
    try:
        await asyncio.sleep(0)
        assert grandchild._waiters
        await sync_to_async(parent._unregister_child)("child")
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(waiter, 1)
        assert not grandchild._waiters
    finally:
        waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)
        loop.set_debug(was_debug)


@pytest.mark.asyncio
async def test_threaded_waiter_batch_cannot_skip_waiter_removed_by_loop(monkeypatch):
    from asgiref.sync import sync_to_async

    from djust.mixins import async_work

    parent, child, grandchild = tree()
    first_done = threading.Event()
    waiters = [asyncio.create_task(grandchild.wait_for_event("same", timeout=10)) for _ in range(2)]
    waiters[0].add_done_callback(lambda task: first_done.set())
    await asyncio.sleep(0)
    original = async_work.cancel_on_owner_loop
    calls = []

    def cancel_and_allow_loop_to_remove(future):
        original(future)
        calls.append(1)
        if len(calls) == 1:
            assert first_done.wait(2)

    monkeypatch.setattr(async_work, "cancel_on_owner_loop", cancel_and_allow_loop_to_remove)
    try:
        await sync_to_async(parent._unregister_child)("child")
        await asyncio.sleep(0)
        assert all(waiter.done() for waiter in waiters)
        assert len(calls) == 2
    finally:
        for waiter in waiters:
            waiter.cancel()
        await asyncio.gather(*waiters, return_exceptions=True)


@pytest.mark.asyncio
async def test_disposed_view_cannot_register_new_waiter():
    parent, child, grandchild = tree()
    parent._unregister_child("child")
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(grandchild.wait_for_event("never"), 0.1)


@pytest.mark.asyncio
async def test_sync_work_cannot_be_interrupted_but_completion_is_suppressed():

    view = LifecycleView()
    runtime = _authorized_runtime(view)
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    completions = []
    view.handle_async_result = lambda *args, **kwargs: completions.append(1)

    def running():
        entered.set()
        assert release.wait(3)
        finished.set()

    view.start_async(running, name="running")
    runtime._dispatch_async_work("start")
    handles = tuple(view._async_task_handles)
    try:
        assert await asyncio.to_thread(entered.wait, 1)
        view.cancel_async_all()
        await asyncio.wait_for(asyncio.gather(*handles, return_exceptions=True), 1)
        assert not finished.is_set()
        release.set()
        assert await asyncio.to_thread(finished.wait, 1)
        assert not completions
        assert not runtime.transport.sent
    finally:
        release.set()
        for handle in handles:
            handle.cancel()
        await asyncio.gather(*handles, return_exceptions=True)


@pytest.mark.asyncio
async def test_sse_shutdown_disposes_root_and_nested_children_once():
    from djust.sse import SSESession

    parent, child, grandchild = tree()
    session = SSESession("lifecycle-test")
    session.view_instance = session.runtime.view_instance = parent
    session.shutdown()
    session.shutdown()
    assert session.view_instance is None
    assert session.runtime.view_instance is None
    for view in (parent, child, grandchild):
        assert view._hooks == ["unregister", "unmount"]


@pytest.mark.asyncio
async def test_websocket_disconnect_disposes_active_and_staged_subtrees_once():
    from djust.websocket import LiveViewConsumer

    parent, child, grandchild = tree()
    _, staged_child, staged_grandchild = tree()
    consumer = LiveViewConsumer()
    consumer.view_instance = parent
    consumer._sticky_preserved = {"active": child, "staged": staged_child}
    consumer.channel_layer = SimpleNamespace(group_discard=AsyncMock())
    consumer.channel_name = "test"
    consumer._view_group = consumer._presence_group = consumer._tick_task = None
    await consumer.disconnect(1000)
    assert consumer.view_instance is None
    assert not consumer._sticky_preserved
    for view in (child, grandchild):
        assert view._hooks == ["unregister", "unmount"]
    for view in (staged_child, staged_grandchild):
        assert view._hooks == ["unmount"]


@pytest.mark.asyncio
async def test_finished_tasks_release_tracking_handles():

    view = LifecycleView()
    host = _authorized_runtime(view)
    host._render_async_result = AsyncMock()

    async def done():
        return 42

    view.start_async(done)
    host._dispatch_async_work("start")
    handles = tuple(view._async_task_handles)
    assert len(handles) == 1
    await asyncio.wait_for(asyncio.gather(*handles), 1)
    assert not view._async_task_handles
    host._render_async_result.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancel_before_dispatch_task_starts_never_enters_callback():

    view = LifecycleView()
    host = _authorized_runtime(view)
    entered = []

    async def callback():
        entered.append(1)

    view.start_async(callback)
    host._dispatch_async_work("start")
    handles = tuple(view._async_task_handles)
    view.cancel_async_all()
    await asyncio.wait_for(asyncio.gather(*handles, return_exceptions=True), 1)
    assert not entered
    assert all(handle.cancelled() for handle in handles)


def test_native_render_replacement_disposes_registered_descendants(rf, settings):
    from djust.tests.test_exposure_child_identity import IdentityParent, render, request_for

    settings.DJUST_LIVE_RENDER_ALLOWED_MODULES = ["djust.tests.test_exposure_child_identity"]
    parent = IdentityParent()
    render(parent, request_for(rf), inputs={"id": 1})
    previous = parent._get_child_view("identity")
    nested = LifecycleView()
    nested.start_async(lambda: None)
    previous._register_child("nested", nested)
    render(parent, request_for(rf), inputs={"id": 2})
    assert parent._get_child_view("identity") is not previous
    assert not previous._get_all_child_views()
    assert previous._explicit_child_reuse_identity is None
    assert not nested._async_tasks
    assert nested._hooks == ["unregister", "unmount"]


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_real_sse_navigation_disposes_old_registered_subtree(settings):
    from django.test import override_settings

    from djust.sse import _sse_sessions
    from djust.tests.test_exposure_sse_navigation import SecondPage, post, start

    with override_settings(
        ROOT_URLCONF="djust.tests.test_exposure_sse_navigation",
        LIVEVIEW_ALLOWED_MODULES=["djust"],
        DEBUG=False,
    ):
        session, key = await start()
        old = session.view_instance
        child, nested = LifecycleView(), LifecycleView()
        old._register_child("child", child)
        child._register_child("nested", nested)
        nested.start_async(lambda: None)
        try:
            response = await post(
                session, key, {"type": "live_redirect_mount", "url": "/second/2/"}
            )
            assert response.status_code == 200
            assert isinstance(session.view_instance, SecondPage)
            assert old._djust_child_disposed
            assert not nested._async_tasks
            assert child._hooks == nested._hooks == ["unmount"]
        finally:
            session.shutdown()
            _sse_sessions.pop(session.session_id, None)


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["runtime", "websocket"])
@pytest.mark.parametrize("raises", [False, True])
async def test_swallowed_cancellation_cannot_deliver_result_or_error(transport, raises, caplog):

    view = LifecycleView()
    entered = asyncio.Event()
    completions = []
    view.handle_async_result = lambda *args, **kwargs: completions.append(1)

    async def stubborn():
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            if raises:
                raise RuntimeError("PRIVATE_CANCEL_SENTINEL")
            return "stale result"

    view.start_async(stubborn)
    if transport == "runtime":
        host = _authorized_runtime(view)
        host._dispatch_async_work("start")
    else:
        host = _authorized_consumer(view)
        await host._dispatch_async_work()
    handles = tuple(view._async_task_handles)
    try:
        await asyncio.wait_for(entered.wait(), 1)
        view.cancel_async_all()
        await asyncio.wait_for(asyncio.gather(*handles, return_exceptions=True), 1)
        assert all(task.cancelled() for task in handles)
        assert not completions
        assert "PRIVATE_CANCEL_SENTINEL" not in caplog.text
    finally:
        for handle in handles:
            handle.cancel()
        await asyncio.gather(*handles, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["survives", "invalid_request", "denied", "mount_error"])
async def test_websocket_redirect_cleans_only_discarded_owned_subtrees(outcome):
    from django.contrib.auth.models import AnonymousUser

    from djust.websocket import LiveViewConsumer

    parent, child, grandchild = tree()
    child.sticky, child.sticky_id = True, "child"
    ordinary = LifecycleView()
    parent._register_child("ordinary", ordinary)
    consumer = LiveViewConsumer()
    consumer.view_instance = parent
    consumer._view_group = consumer._tick_task = None
    consumer._flush_all_pending = AsyncMock()
    request = SimpleNamespace(user=AnonymousUser(), tenant=None, path="/page/")
    consumer._build_live_redirect_request = lambda data: (
        None if outcome == "invalid_request" else request
    )
    consumer._resolve_view_path_from_url = lambda url: None
    if outcome == "denied":
        child.login_required = True

    async def mount(*args, **kwargs):
        if outcome == "mount_error":
            raise RuntimeError("new mount failed")
        consumer.view_instance = LifecycleView()
        if outcome == "survives":
            consumer.view_instance._register_child("child", child)

    consumer.handle_mount = mount
    data = {"url": "/page/", "view": "test.Page"}
    if outcome == "mount_error":
        with pytest.raises(RuntimeError, match="new mount failed"):
            await consumer.handle_live_redirect_mount(data)
    else:
        await consumer.handle_live_redirect_mount(data)
    assert parent._hooks == ordinary._hooks == ["unmount"]
    if outcome == "survives":
        assert not child._hooks and not grandchild._hooks
        assert grandchild._async_tasks
        assert consumer.view_instance._get_child_view("child") is child
    else:
        assert child._hooks == grandchild._hooks == ["unmount"]
        assert not grandchild._async_tasks
        assert not consumer._sticky_preserved
