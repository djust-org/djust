"""The work left before ``DJUST_STATE_SAVE_INTERVAL`` became the default (#4164).

* item 2: an HTTP fallback request that changes nothing does not take the tab
  from a socket that holds newer, unsaved changes; one that changes the state
  still does;
* item 3: the session write of an over-limit turn that asked for its save is
  ordered with the runtime's other saves and bounded by the save deadline;
* #4160: a handler that sends the page elsewhere with its own push event is
  saved before its reply when it says so (``save=True`` / ``save_state()``).

Timers are fired by hand, never waited for.
"""

from __future__ import annotations

from typing import Any, List

import pytest
from asgiref.sync import sync_to_async
from django.urls import path as _path

from djust import LiveView, runtime
from djust.decorators import event_handler
from djust.tests import _per_turn_harness as harness
from djust.tests import _save_frequency as sf

pytestmark = pytest.mark.django_db(transaction=True)

MODULE = __name__


class Counter(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root><pre>{{ count }}</pre></div>"

    def mount(self, request, **kwargs):
        self.count = 0

    @event_handler()
    def inc(self, **kwargs):
        self.count += 1

    @event_handler()
    def nothing(self, **kwargs):
        pass

    @event_handler(save=True)
    def checkout(self, **kwargs):
        """Sends the page to another URL with its own push event (#4160)."""
        self.count += 100
        self.push_event("go", {"url": "/thanks/"})

    @event_handler()
    def leave(self, **kwargs):
        self.count += 10
        self.save_state()
        self.push_event("go", {"url": "/thanks/"})

    @event_handler()
    def leave_unsaved(self, **kwargs):
        self.count += 10
        self.push_event("go", {"url": "/thanks/"})

    def get_context_data(self, **kwargs):
        return {"count": self.count}


urlpatterns = [_path(sf.PATH.strip("/") + "/", Counter.as_view())]


@pytest.fixture(autouse=True)
def _each(monkeypatch):
    sf.one_tab(monkeypatch)


@pytest.fixture
def runtimes(monkeypatch):
    built: List[Any] = []
    real = runtime.ViewRuntime.__init__

    def recording(self, *args, **kwargs):
        real(self, *args, **kwargs)
        built.append(self)

    monkeypatch.setattr(runtime.ViewRuntime, "__init__", recording)
    return built


def _settings(**extra):
    return harness.ws_settings(MODULE, ROOT_URLCONF=MODULE, **extra)


def _count(key: str) -> Any:
    return sf.only_envelope(key)["values"].get("count")


async def _stored(key: str) -> Any:
    return await sync_to_async(_count)(key)


def _fallback_post(key: str, event: str) -> Any:
    """A fallback POST through Django's ``SessionMiddleware``, which saves the
    session whenever the request modified it (as a deployed stack does)."""
    from django.contrib.sessions.middleware import SessionMiddleware

    view = Counter()
    request = sf.fallback_request(key, "POST", event)
    response = view.post(request)
    assert response.status_code == 200, response.content
    SessionMiddleware(lambda r: response).process_response(request, response)
    return view


async def _dirty(runtimes) -> tuple:
    key = await sync_to_async(sf.page_get)(Counter)
    comm, _ = await harness.mount_view(Counter, key)
    await harness.event(comm, "inc")
    rt = runtimes[-1]
    assert rt.state_unsaved
    return key, comm, rt


# ---------------------------------------------------------------------------
# item 2: a fallback request that saves nothing does not take the tab
# ---------------------------------------------------------------------------


async def test_a_fallback_request_that_changes_nothing_leaves_the_tab_to_the_socket(runtimes):
    with _settings(DJUST_STATE_SAVE_INTERVAL=60):
        key, comm, rt = await _dirty(runtimes)
        try:
            view = await sync_to_async(_fallback_post)(key, "nothing")
            assert view.count == 0  # it rendered the older stored state
            assert await rt.flush_state()
            assert not rt.view_instance.__dict__.get("_djust_auto_superseded")
        finally:
            await comm.disconnect()
    assert await _stored(key) == 1


async def test_a_fallback_request_that_changes_the_state_still_takes_the_tab(runtimes):
    with _settings(DJUST_STATE_SAVE_INTERVAL=60):
        key, comm, rt = await _dirty(runtimes)
        try:
            await sync_to_async(_fallback_post)(key, "nothing")
            await sync_to_async(_fallback_post)(key, "inc")  # 0 -> 1, claims
            await sync_to_async(_fallback_post)(key, "inc")  # 1 -> 2
            assert not await rt.flush_state()
            assert rt.view_instance.__dict__.get("_djust_auto_superseded") is True
        finally:
            await comm.disconnect()
    assert await _stored(key) == 2
