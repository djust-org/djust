"""The HTTP POST fallback delivers every queued side effect, not only flash
and page metadata.

Before this fix ``_inject_side_channels`` drained flash, page metadata and
navigation only: ``push_event`` / ``push_commands`` never reached the client,
``set_layout`` was silently dropped, and ``defer()`` callbacks piled up
unrun until the next WebSocket turn (which an HTTP-only client never has).
One test per frame type, each with the negative case beside it.
"""

import json

import pytest
from django.contrib.sessions.middleware import SessionMiddleware
from django.template.exceptions import TemplateDoesNotExist
from django.test import RequestFactory

from djust import LiveView
from djust.decorators import event_handler

DEFERRED: list = []


class SideChannelView(LiveView):
    template = "<div dj-root><span>{{ message }}</span></div>"

    def mount(self, request, **kwargs):
        self.message = "Hello"

    @event_handler()
    def ping_hooks(self, **kwargs):
        self.message = "Pinged"
        self.push_event("chart:update", {"points": [1, 2, 3]})
        self.push_event("toast", {"text": "hi"})

    @event_handler()
    def run_js(self, **kwargs):
        from djust.js import JS

        self.message = "Ran"
        self.push_commands(JS.add_class("hi", to="#x"))

    @event_handler()
    def swap_layout(self, **kwargs):
        self.message = "Swapped"
        self.set_layout("layouts/fullscreen.html")

    @event_handler()
    def later(self, **kwargs):
        self.message = "Later"
        self.defer(DEFERRED.append, "ran")

    @event_handler()
    def later_twice(self, **kwargs):
        self.message = "Twice"

        def boom():
            raise ValueError("deferred failure")

        self.defer(boom)
        self.defer(DEFERRED.append, "after-failure")

    @event_handler()
    def later_async(self, **kwargs):
        self.message = "Async"

        async def record():
            DEFERRED.append("async-ran")

        self.defer(record)

    @event_handler()
    def noop(self, **kwargs):
        self.message = "Same"


def _add_session(request):
    SessionMiddleware(lambda x: None).process_request(request)
    request.session.save()
    return request


def _post(event_name):
    view = SideChannelView()
    factory = RequestFactory()
    get_request = _add_session(factory.get("/test/"))
    view.get(get_request)
    body = json.dumps({"event": event_name, "params": {}})
    post_request = factory.post("/test/", data=body, content_type="application/json")
    post_request.session = get_request.session
    response = view.post(post_request)
    return view, json.loads(response.content.decode("utf-8"))


@pytest.fixture(autouse=True)
def _reset_deferred():
    DEFERRED.clear()
    yield
    DEFERRED.clear()


@pytest.mark.django_db
class TestPushEvents:
    def test_push_events_ride_the_answer_in_frame_shape(self):
        view, data = _post("ping_hooks")
        assert data["_push_events"] == [
            {"event": "chart:update", "payload": {"points": [1, 2, 3]}},
            {"event": "toast", "payload": {"text": "hi"}},
        ]
        assert view._pending_push_events == [], "the queue must be drained"

    def test_push_commands_arrive_as_a_djust_exec_push_event(self):
        _, data = _post("run_js")
        [event] = data["_push_events"]
        assert event["event"] == "djust:exec"
        assert event["payload"]["ops"]

    def test_no_key_without_push_events(self):
        _, data = _post("noop")
        assert "_push_events" not in data


@pytest.mark.django_db
class TestLayout:
    def test_set_layout_rides_the_answer(self, monkeypatch):
        from djust.mixins import layout

        monkeypatch.setattr(
            layout, "render_pending_layout", lambda view, path: "<main>%s</main>" % path
        )
        view, data = _post("swap_layout")
        assert data["_layout"] == {
            "path": "layouts/fullscreen.html",
            "html": "<main>layouts/fullscreen.html</main>",
        }
        assert view._pending_layout is None

    def test_a_missing_layout_template_is_skipped_not_fatal(self, monkeypatch):
        from djust.mixins import layout

        def missing(view, path):
            raise TemplateDoesNotExist(path)

        monkeypatch.setattr(layout, "render_pending_layout", missing)
        _, data = _post("swap_layout")
        assert "_layout" not in data
        assert data.get("type") != "error", data

    def test_no_key_without_set_layout(self):
        _, data = _post("noop")
        assert "_layout" not in data


@pytest.mark.django_db
class TestDeferred:
    def test_deferred_callback_runs_on_the_http_turn(self):
        view, _ = _post("later")
        assert DEFERRED == ["ran"]
        assert view._drain_deferred() == [], "the queue must be drained"

    def test_a_failing_callback_does_not_stop_the_next(self):
        _, data = _post("later_twice")
        assert DEFERRED == ["after-failure"]
        assert data.get("type") != "error", data

    def test_an_async_callback_is_awaited(self):
        _post("later_async")
        assert DEFERRED == ["async-ran"]

    def test_nothing_runs_without_defer(self):
        _post("noop")
        assert DEFERRED == []
