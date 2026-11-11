"""#3989: ``handle_restore`` runs when an explicit view's declared state is restored.

A view that persisted a loading flag (``state(persist="server")``) came back from a
reconnect or a worker restart still showing its background job as running: the job
belonged to the connection that started it and is gone, and nothing told the view.
``mount()`` cannot clear the flag, because the restored state is applied AFTER
``mount()`` returns. ``handle_restore`` is the hook for exactly that ("called after
the view was rebuilt from its saved session state"), and it ran on the legacy
snapshot restore and the HTTP fallback but not here.
"""

import pytest
from asgiref.sync import sync_to_async
from django.test import override_settings

from djust import LiveView, event_handler
from djust.decorators import state
from djust.runtime import ViewRuntime
from djust.tests.test_exposure_runtime import make_request
from djust.tests.test_runtime_state_save_tt_1894 import MockTransport

pytestmark = [pytest.mark.asyncio, pytest.mark.django_db(transaction=True)]


class JobView(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root><span>{{ running }}</span><span>{{ count }}</span></div>"
    running = state(False, persist="server")
    count = state(0, persist="server")
    seen: list = []

    def mount(self, request, **kwargs):
        self.running = False
        self.count = 0

    def get_context_data(self, **kwargs):
        return super().get_context_data(running=self.running, count=self.count, **kwargs)

    def handle_restore(self, request):
        type(self).seen.append((self.running, self.count))
        if self.running:  # restored, but the job did not survive
            self.running = False

    @event_handler()
    def start(self):
        self.running = True  # a real view would also call start_async() here
        self.count += 1


class AsyncRestoreView(JobView):
    # An explicit view redeclares the grants it inherits.
    running = state(False, persist="server")
    count = state(0, persist="server")

    async def handle_restore(self, request):
        type(self).seen.append(("async", self.running, self.count))
        self.running = False


class NoHookView(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root><span>{{ running }}</span></div>"
    running = state(False, persist="server")

    def mount(self, request, **kwargs):
        self.running = False

    @event_handler()
    def start(self):
        self.running = True


@pytest.fixture(autouse=True)
def staged(monkeypatch):
    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)
    JobView.seen = []
    AsyncRestoreView.seen = []


async def mount(request, view_class):
    transport = MockTransport()
    transport.build_request = lambda: request

    async def fresh_event_request(view):
        return await sync_to_async(make_request)(request.session.session_key)

    transport.explicit_event_request = fresh_event_request
    runtime = ViewRuntime(transport)
    with override_settings(LIVEVIEW_ALLOWED_MODULES=["djust"]):
        await runtime.dispatch_mount(
            {"type": "mount", "view": __name__ + "." + view_class.__name__, "url": request.path}
        )
    assert not transport.errors, transport.errors
    assert runtime.view_instance is not None
    return runtime, transport


async def _start_then_reconnect(view_class):
    request = await sync_to_async(make_request)()
    first, _ = await mount(request, view_class)
    await first.dispatch_event({"type": "event", "event": "start", "params": {}})
    assert first.view_instance.running is True
    again = await sync_to_async(make_request)(request.session.session_key)
    return await mount(again, view_class)


async def test_the_hook_runs_on_a_reconnect_with_the_restored_state_in_place():
    second, _ = await _start_then_reconnect(JobView)
    # It saw the RESTORED values (running, count 1), not mount()'s (False, 0) ...
    assert JobView.seen == [(True, 1)]
    # ... and what it changed is the state the view now has.
    assert second.view_instance.running is False
    assert second.view_instance.count == 1


async def test_the_page_a_reconnect_renders_shows_what_the_hook_left():
    _, transport = await _start_then_reconnect(JobView)
    frame = next(f for f in transport.sent if f.get("type") == "mount")
    assert ">False</span>" in frame["html"] and ">True<" not in frame["html"], frame["html"]


async def test_the_hook_does_not_run_on_a_first_mount():
    request = await sync_to_async(make_request)()
    await mount(request, JobView)
    assert JobView.seen == []


async def test_an_async_hook_is_awaited():
    second, _ = await _start_then_reconnect(AsyncRestoreView)
    assert AsyncRestoreView.seen == [("async", True, 1)]
    assert second.view_instance.running is False


async def test_without_the_hook_the_restored_flag_is_what_was_saved():
    """The framework does not guess which field is a loading flag: a view with no
    ``handle_restore`` gets its declared state back as saved."""
    second, _ = await _start_then_reconnect(NoHookView)
    assert second.view_instance.running is True
