"""Background work must remain owned by its registered explicit child."""

import asyncio

import pytest
from asgiref.sync import sync_to_async
from django.contrib.sessions.backends.db import SessionStore

from djust import LiveView, event_handler
from djust._exposure_children import child_state_key
from djust.tests.test_exposure_child_events import EventChild, mount

pytestmark = [pytest.mark.asyncio, pytest.mark.django_db(transaction=True)]


@pytest.fixture(autouse=True)
def staged(monkeypatch, settings):
    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)
    settings.DJUST_LIVE_RENDER_ALLOWED_MODULES = ["djust.tests.test_exposure_child_events"]


async def drain_owned_tasks(child):
    handles = tuple(getattr(child, "_async_task_handles", ()))
    assert handles, "The routed child must own its dispatched background task"
    await asyncio.wait_for(asyncio.gather(*handles), 3)


@pytest.mark.parametrize("explicit", [False, True])
async def test_background_contract_failure_withholds_html_and_completes_batch(
    monkeypatch, explicit, caplog
):
    from djust.tests.test_runtime_child_routing_1892 import (
        _StickyChildView,
        _make_runtime_with_view,
        _make_sticky_parent,
    )

    started, release, completed = asyncio.Event(), asyncio.Event(), asyncio.Event()

    @event_handler(parameter_policy="strict")
    def begin(self):
        async def work():
            started.set()
            await release.wait()
            self.count = 4

        self.start_async(work, name="contract-failure")

    monkeypatch.setattr(EventChild if explicit else _StickyChildView, "begin", begin, raising=False)
    if explicit:
        runtime, transport, _ = await mount()
        child_id = "menu"
        child = runtime.view_instance._get_child_view(child_id)
    else:
        parent, child = _make_sticky_parent()
        runtime, transport = _make_runtime_with_view(parent)
        runtime._parameter_contract_view = "tests.Parent"
        child_id = "child-1"

    original_send = transport.send

    async def observe(frame):
        await original_send(frame)
        if frame.get("type") == "async_complete":
            completed.set()

    monkeypatch.setattr(transport, "send", observe)
    try:
        await runtime.dispatch_event({"event": "begin", "params": {"view_id": child_id}, "ref": 43})
        ack = next(frame for frame in transport.sent if frame.get("ref") == 43)
        assert ack["async_pending"] is True
        await asyncio.wait_for(started.wait(), 1)
        transport.sent.clear()

        def invalid(_root):
            raise ValueError("SECRET_CONTRACT_DISCOVERY")

        monkeypatch.setattr("djust._parameter_metadata.parameter_contract_manifest", invalid)
        release.set()
        await drain_owned_tasks(child)
        await asyncio.wait_for(completed.wait(), 1)
        assert child.count == 4
        assert not any(frame.get("type") == "embedded_update" for frame in transport.sent)
        assert any(error.get("code") == "render_error" for error in transport.errors)
        assert {"type": "async_complete", "async_batch": ack["async_batch"]} in transport.sent
        assert "SECRET_CONTRACT_DISCOVERY" not in repr(transport.sent)
        assert "SECRET_CONTRACT_DISCOVERY" not in caplog.text
    finally:
        release.set()
        child.cancel_async_all()
        runtime.view_instance.cancel_async_all()


async def test_background_batch_acknowledges_only_after_all_child_tasks(monkeypatch):
    releases = [asyncio.Event(), asyncio.Event()]
    entered = [asyncio.Event(), asyncio.Event()]

    @event_handler()
    def begin(self):
        for index in range(2):

            async def work(index=index):
                entered[index].set()
                await releases[index].wait()

            self.start_async(work, name=f"job-{index}")

    monkeypatch.setattr(EventChild, "begin", begin, raising=False)
    runtime, transport, _ = await mount()
    child = runtime.view_instance._get_child_view("menu")
    completed = asyncio.Event()
    original_send = transport.send

    async def observe(frame):
        await original_send(frame)
        if frame.get("type") == "async_complete":
            completed.set()

    monkeypatch.setattr(transport, "send", observe)
    try:
        await runtime.dispatch_event({"event": "begin", "params": {"view_id": "menu"}, "ref": 42})
        ack = next(frame for frame in transport.sent if frame.get("ref") == 42)
        assert ack.get("async_pending") is True
        batch = ack.get("async_batch")
        assert isinstance(batch, str) and batch
        await asyncio.wait_for(asyncio.gather(*(event.wait() for event in entered)), 1)
        handles = tuple(child._async_task_handles)
        releases[0].set()
        await asyncio.wait_for(asyncio.wait(handles, return_when=asyncio.FIRST_COMPLETED), 2)
        assert not any(frame.get("type") == "async_complete" for frame in transport.sent)
        releases[1].set()
        await asyncio.wait_for(asyncio.gather(*handles), 3)
        await asyncio.wait_for(completed.wait(), 1)
        completions = [frame for frame in transport.sent if frame.get("type") == "async_complete"]
        assert completions == [{"type": "async_complete", "async_batch": batch}]
    finally:
        for release in releases:
            release.set()
        child.cancel_async_all()
        runtime.view_instance.cancel_async_all()


@pytest.mark.parametrize("shape", ["sync", "async", "returned-coroutine"])
@pytest.mark.parametrize("queue", ["named", "legacy"])
@pytest.mark.parametrize("strict", [False, True])
async def test_child_event_runs_its_queue_and_persists_a_scoped_completion(
    monkeypatch, shape, queue, strict
):
    calls = []
    root_calls = []

    @event_handler(parameter_policy="strict" if strict else "legacy")
    def begin(self):
        def finish():
            calls.append(self)
            self.count = 4

        async def async_finish():
            finish()

        callback = {
            "sync": finish,
            "async": async_finish,
            "returned-coroutine": lambda: async_finish(),
        }[shape]
        if queue == "legacy":
            self._async_pending = (callback, (), {})
        else:
            self.start_async(callback, name="child-job")

    monkeypatch.setattr(EventChild, "begin", begin, raising=False)
    runtime, transport, request = await mount()
    root = runtime.view_instance
    child = root._get_child_view("menu")
    root.start_async(lambda: root_calls.append(root))
    try:
        await runtime.dispatch_event({"event": "begin", "params": {"view_id": "menu"}})
        await drain_owned_tasks(child)
        assert calls == [child]
        assert root._async_tasks
        assert root_calls == []
        frames = [frame for frame in transport.sent if frame.get("source") == "async"]
        assert len(frames) == 1
        assert frames[0]["type"] == "embedded_update"
        assert frames[0]["view_id"] == "menu"
        assert "Count=4" in frames[0]["html"]
        assert "SERVER_SENTINEL" not in str(frames)
        assert not transport.errors
        if strict:
            owner = next(
                owner
                for owner in frames[0]["parameter_contracts"]["owners"]
                if owner["view_id"] == "menu"
            )
            assert owner["handlers"]["begin"]["policy"] == "strict"
            assert frames[0]["parameter_contract_view"] == runtime._parameter_contract_view
        else:
            assert "parameter_contracts" not in frames[0]
        stored = await sync_to_async(SessionStore(request.session.session_key).load)()
        assert stored[child_state_key(request.path, ("menu",))]["state"]["values"]["count"] == 4
    finally:
        root.cancel_async_all()
        child.cancel_async_all()


async def test_unregister_cancels_real_child_event_work_before_completion(monkeypatch):
    entered, stopped = asyncio.Event(), asyncio.Event()

    @event_handler()
    def begin(self):
        async def finish():
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

        self.start_async(finish, name="child-job")

    monkeypatch.setattr(EventChild, "begin", begin, raising=False)
    runtime, transport, _ = await mount()
    root = runtime.view_instance
    child = root._get_child_view("menu")
    try:
        await runtime.dispatch_event({"event": "begin", "params": {"view_id": "menu"}})
        await asyncio.wait_for(entered.wait(), 1)
        handles = tuple(child._async_task_handles)
        root._unregister_child("menu")
        await asyncio.wait_for(stopped.wait(), 1)
        await asyncio.gather(*handles, return_exceptions=True)
        assert not any(frame.get("source") == "async" for frame in transport.sent)
        assert not transport.errors
    finally:
        root.cancel_async_all()
        child.cancel_async_all()


@pytest.mark.parametrize("revoke", ["permission", "session"])
async def test_completion_reloads_authority_before_result_handler(monkeypatch, revoke):
    completions = []

    def allowed(self, request):
        return not request.session.get("child_denied", False)

    @event_handler()
    def begin(self):
        def finish():
            self.count = 4
            if revoke == "session":
                self.request.session.delete()
            else:
                self.request.session["child_denied"] = True
                self.request.session.save()
            return "CALLBACK_SECRET_SENTINEL"

        self.start_async(finish)

    def on_result(self, name, result=None, error=None):
        completions.append(result)

    monkeypatch.setattr(EventChild, "check_permissions", allowed)
    monkeypatch.setattr(EventChild, "begin", begin, raising=False)
    monkeypatch.setattr(EventChild, "handle_async_result", on_result, raising=False)
    runtime, transport, _ = await mount()
    child = runtime.view_instance._get_child_view("menu")
    await runtime.dispatch_event({"event": "begin", "params": {"view_id": "menu"}})
    await drain_owned_tasks(child)
    assert completions == []
    assert not any(
        frame.get("source") == "async" and frame.get("type") != "error" for frame in transport.sent
    )
    assert transport.errors[-1]["code"] == "async_error"
    assert transport.errors[-1]["source"] == "async"
    assert transport.errors[-1]["async_batch"]
    assert "SECRET_SENTINEL" not in str(transport.sent)


async def test_callback_error_is_not_logged_or_sent(monkeypatch, caplog):
    @event_handler()
    def begin(self):
        def fail():
            raise RuntimeError("CALLBACK_SECRET_SENTINEL")

        self.start_async(fail)

    monkeypatch.setattr(EventChild, "begin", begin, raising=False)
    runtime, transport, _ = await mount()
    child = runtime.view_instance._get_child_view("menu")
    await runtime.dispatch_event({"event": "begin", "params": {"view_id": "menu"}})
    await drain_owned_tasks(child)
    assert transport.errors[-1]["code"] == "async_error"
    assert "CALLBACK_SECRET_SENTINEL" not in str(transport.sent) + caplog.text


async def test_root_replacement_drops_late_child_completion(monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    completions = []

    @event_handler()
    def begin(self):
        async def finish():
            entered.set()
            await release.wait()
            return 4

        self.start_async(finish)

    def on_result(self, name, result=None, error=None):
        completions.append(result)

    monkeypatch.setattr(EventChild, "begin", begin, raising=False)
    monkeypatch.setattr(EventChild, "handle_async_result", on_result, raising=False)
    runtime, transport, _ = await mount()
    child = runtime.view_instance._get_child_view("menu")
    try:
        await runtime.dispatch_event({"event": "begin", "params": {"view_id": "menu"}})
        await asyncio.wait_for(entered.wait(), 1)
        handles = tuple(child._async_task_handles)
        runtime.view_instance = None
        release.set()
        await asyncio.wait_for(asyncio.gather(*handles, return_exceptions=True), 3)
        assert completions == []
        assert not any(frame.get("source") == "async" for frame in transport.sent)
        assert not transport.errors
    finally:
        child.cancel_async_all()


async def test_authorization_hook_cannot_replace_owner_then_deliver_result(monkeypatch):
    completions = []

    @event_handler()
    def begin(self):
        def finish():
            self._replace_on_auth = True
            return 4

        self.start_async(finish)

    def allowed(self, request):
        if getattr(self, "_replace_on_auth", False):
            runtime.view_instance = None
        return True

    def on_result(self, name, result=None, error=None):
        completions.append(result)

    monkeypatch.setattr(EventChild, "begin", begin, raising=False)
    monkeypatch.setattr(EventChild, "check_permissions", allowed)
    monkeypatch.setattr(EventChild, "handle_async_result", on_result, raising=False)
    runtime, transport, _ = await mount()
    child = runtime.view_instance._get_child_view("menu")
    await runtime.dispatch_event({"event": "begin", "params": {"view_id": "menu"}})
    handles = tuple(child._async_task_handles)
    assert handles
    await asyncio.wait_for(asyncio.gather(*handles, return_exceptions=True), 3)
    assert completions == []
    assert not any(frame.get("source") == "async" for frame in transport.sent)


async def test_child_result_handler_can_recover_error_without_exporting_it(monkeypatch, caplog):
    errors = []

    @event_handler()
    def begin(self):
        def fail():
            raise RuntimeError("CALLBACK_SECRET_SENTINEL")

        self.start_async(fail, name="recover")

    def on_result(self, name, result=None, error=None):
        assert name == "recover" and result is None
        errors.append(error)
        self.count = 4

    monkeypatch.setattr(EventChild, "begin", begin, raising=False)
    monkeypatch.setattr(EventChild, "handle_async_result", on_result, raising=False)
    runtime, transport, _ = await mount()
    child = runtime.view_instance._get_child_view("menu")
    await runtime.dispatch_event({"event": "begin", "params": {"view_id": "menu"}})
    await drain_owned_tasks(child)
    assert len(errors) == 1 and isinstance(errors[0], RuntimeError)
    frames = [frame for frame in transport.sent if frame.get("source") == "async"]
    assert len(frames) == 1 and "Count=4" in frames[0]["html"]
    assert not transport.errors
    assert "CALLBACK_SECRET_SENTINEL" not in str(transport.sent) + caplog.text
