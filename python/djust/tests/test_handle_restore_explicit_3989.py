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


class RaisingHookView(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root><span>{{ running }}</span></div>"
    running = state(False, persist="server")

    def mount(self, request, **kwargs):
        self.running = False

    def get_context_data(self, **kwargs):
        return super().get_context_data(running=self.running, **kwargs)

    def handle_restore(self, request):
        raise RuntimeError("HOOK_SENTINEL")

    @event_handler()
    def start(self):
        self.running = True


async def test_a_hook_that_raises_fails_the_mount():
    request = await sync_to_async(make_request)()
    first, _ = await mount(request, RaisingHookView)
    await first.dispatch_event({"type": "event", "event": "start", "params": {}})

    again = await sync_to_async(make_request)(request.session.session_key)
    transport = MockTransport()
    transport.build_request = lambda: again
    runtime = ViewRuntime(transport)
    with override_settings(LIVEVIEW_ALLOWED_MODULES=["djust"]):
        await runtime.dispatch_mount(
            {"type": "mount", "view": __name__ + ".RaisingHookView", "url": again.path}
        )

    assert runtime.view_instance is None
    assert not any(frame.get("type") == "mount" for frame in transport.sent), transport.sent
    assert transport.errors or any(f.get("type") == "error" for f in transport.sent)
    # The exception's text reaches neither the client nor (outside diagnostic mode) the log.
    assert "HOOK_SENTINEL" not in repr(transport.sent) + repr(transport.errors)


# ---- the HTTP page-POST fallback restores an explicit view's state too ----------


def _http_post(view_class, initial, event):
    import json

    from django.test import RequestFactory

    request = RequestFactory().post(
        initial.path,
        data=json.dumps({}),
        content_type="application/json",
        HTTP_X_DJUST_EVENT=event,
    )
    request.user, request.session, request.tenant = initial.user, initial.session, None
    view = view_class()
    response = view.post(request)
    assert response.status_code == 200, response.content
    return view


class HttpJobView(JobView):
    running = state(False, persist="server")
    count = state(0, persist="server")

    @event_handler()
    def noop(self):
        pass


def _http_flow():
    HttpJobView.seen = []
    initial = make_request()
    assert HttpJobView().get(initial).status_code == 200
    _http_post(HttpJobView, initial, "start")  # running=True, count=1 saved
    seen_before = list(HttpJobView.seen)
    view = _http_post(HttpJobView, initial, "noop")
    return seen_before, view


async def test_the_http_fallback_calls_the_hook_for_an_explicit_view():
    seen_before, view = await sync_to_async(_http_flow)()
    # The page GET saved the declared defaults, so the first POST restored those ...
    assert seen_before == [(False, 0)]
    # ... and the second restored what the first one saved, then the hook cleared the flag.
    assert HttpJobView.seen == [(False, 0), (True, 1)]
    assert view.running is False
    assert view.count == 1
