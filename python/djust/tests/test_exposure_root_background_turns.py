"""ADR-038 E3: an explicit root's background work is authorized and persisted.

``start_async`` work completes outside any event turn. For an explicit root the
runtime ran the callback and ``handle_async_result`` and sent the re-render
without re-authorizing (a revoked session still got its result) and without
saving declared server state (a reconnect restored the pre-background value).
The foreground path also swallowed a failed explicit save and still sent its
success frame. Decisions D-k and D-l: fresh authority before and after the
callback, a revoked result is dropped with the foreground denial, and a failed
save withholds the success frame.
"""

import asyncio
import json
import logging
import threading

import pytest
from asgiref.sync import sync_to_async

from djust import LiveView, event_handler
from djust.decorators import state
from django.test import override_settings

from djust.runtime import ViewRuntime
from djust.tests.test_exposure_runtime import make_request
from djust.tests.test_runtime_state_save_tt_1894 import MockTransport

pytestmark = [pytest.mark.asyncio, pytest.mark.django_db(transaction=True)]

HANDLED = []
GATE = threading.Event()


class BackgroundView(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root><span>{{ count }}</span></div>"
    count = state(0, persist="server")

    def mount(self, request, **kwargs):
        self.count = 5

    def get_context_data(self, **kwargs):
        return super().get_context_data(count=self.count, **kwargs)

    @event_handler()
    def spawn(self):
        self.start_async(self._work)

    @event_handler()
    def increment(self):
        self.count += 1

    def _work(self):
        assert GATE.wait(5), "test never released the background callback"
        return 41

    def handle_async_result(self, name, result=None, error=None):
        HANDLED.append((name, result, error))
        if error is None:
            self.count = result


async def mount(request, view_class):
    """Real runtime mount; the event request is reloaded from the DB session."""
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
    assert runtime.view_instance is not None, transport.sent
    return runtime, transport


@pytest.fixture(autouse=True)
def staged(monkeypatch):
    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)
    HANDLED.clear()
    GATE.clear()
    yield
    GATE.set()


async def _drain(view):
    handles = tuple(getattr(view, "_async_task_handles", ()))
    assert handles, "the root must own its dispatched background task"
    await asyncio.wait_for(asyncio.gather(*handles, return_exceptions=True), 5)


def _async_frames(transport):
    return [f for f in transport.sent if f.get("source") == "async" and f.get("type") != "error"]


async def _stored_count(request):
    fresh = await sync_to_async(make_request)(request.session.session_key)
    runtime, _ = await mount(fresh, BackgroundView)
    return runtime.view_instance.count


async def test_background_result_is_persisted_for_reconnect():
    request = await sync_to_async(make_request)()
    runtime, transport = await mount(request, BackgroundView)
    view = runtime.view_instance
    await runtime.dispatch_event({"type": "event", "event": "spawn", "params": {}})
    GATE.set()
    await _drain(view)

    assert [h[1:] for h in HANDLED] == [(41, None)]
    assert _async_frames(transport), transport.sent
    assert await _stored_count(request) == 41, "background mutation was not persisted"


async def test_revoked_session_drops_the_background_result():
    request = await sync_to_async(make_request)()
    runtime, transport = await mount(request, BackgroundView)
    view = runtime.view_instance
    await runtime.dispatch_event({"type": "event", "event": "spawn", "params": {}})
    sent_before = len(transport.sent)

    await sync_to_async(request.session.delete)()
    GATE.set()
    await _drain(view)

    assert HANDLED == [], "handle_async_result ran without current authorization"
    late = transport.sent[sent_before:]
    assert not [f for f in late if f.get("type") in {"patch", "html_update"}], late
    assert [f.get("code") for f in late if f.get("type") == "error"] == ["permission_denied"]
    assert transport.closed_with == 4403


async def test_failed_background_save_withholds_the_success_frame(monkeypatch, caplog):
    import djust._exposure_sessions as sessions

    request = await sync_to_async(make_request)()
    runtime, transport = await mount(request, BackgroundView)
    view = runtime.view_instance
    await runtime.dispatch_event({"type": "event", "event": "spawn", "params": {}})
    sent_before = len(transport.sent)

    async def fail(view, request):
        raise OSError("STORE_SENTINEL")

    monkeypatch.setattr(sessions, "asave_server_state", fail)
    with caplog.at_level(logging.DEBUG):
        GATE.set()
        await _drain(view)

    assert HANDLED, "the result handler must have run before the save"
    late = transport.sent[sent_before:]
    assert not [f for f in late if f.get("type") in {"patch", "html_update"}], late
    assert [f.get("code") for f in late if f.get("type") == "error"] == ["state_error"]
    assert "STORE_SENTINEL" not in json.dumps(transport.sent)
    assert "STORE_SENTINEL" not in caplog.text
    assert view._force_full_html is True


async def test_failed_foreground_save_withholds_the_success_frame(monkeypatch, caplog):
    import djust._exposure_sessions as sessions

    request = await sync_to_async(make_request)()
    runtime, transport = await mount(request, BackgroundView)
    sent_before = len(transport.sent)

    async def fail(view, request):
        raise OSError("STORE_SENTINEL")

    monkeypatch.setattr(sessions, "asave_server_state", fail)
    with caplog.at_level(logging.DEBUG):
        await runtime.dispatch_event({"type": "event", "event": "increment", "params": {}})

    late = transport.sent[sent_before:]
    assert runtime.view_instance.count == 6, "the handler must have run"
    assert not [f for f in late if f.get("type") in {"patch", "html_update", "noop"}], late
    assert [f.get("code") for f in late if f.get("type") == "error"] == ["state_error"]
    # The withheld success frame would have refreshed the signed snapshot; the
    # error revokes the client's token instead.
    error = next(f for f in late if f.get("type") == "error")
    assert error["state_snapshot_signed"] is None
    assert error["view"] == __name__ + ".BackgroundView"
    assert "STORE_SENTINEL" not in json.dumps(transport.sent)
    assert "STORE_SENTINEL" not in caplog.text
