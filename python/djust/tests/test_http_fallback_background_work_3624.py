"""The HTTP POST fallback runs ``start_async`` / ``@background`` work in the turn.

The fallback has one response and no later frame to deliver a background
result, so it used to discard the queued tasks with the request's view (and,
since #3544, say so in a one-off warning). A page that loads after its first
render then rendered its loading state on every POST, and a write scheduled
with ``start_async`` after a "Sending..." render never ran (#3624).

The request is a plain one, so the work can finish before the answer is
rendered. These tests drive the real ``LiveView.get`` / ``.post`` with a real
session and read the answer the browser would get.
"""

from __future__ import annotations

import json
import logging
import sys

import pytest
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory, override_settings

from djust import LiveView
from djust.decorators import background, event_handler
from djust.mixins import request as request_mixin

pytestmark = pytest.mark.django_db

STATELESS = override_settings(LIVEVIEW_CONFIG={"view_state_in_session": False})
PATH = "/bg/"

LOG: list = []


class BackgroundView(LiveView):
    template = '<div dj-root dj-id="0"><p>{{ status }}</p><p>{{ data }}</p></div>'

    def mount(self, request, **kwargs):
        self.status = "idle"
        self.data = "-"
        self.results = []

    def handle_async_result(self, name, result=None, error=None):
        LOG.append(("result", name, result, type(error).__name__ if error else None))
        if error is not None:
            self.status = "failed"

    @event_handler()
    def kick_off(self, **kwargs):
        self.status = "working"  # the loading state a socket would flush first
        self.start_async(self._work, name="work")

    def _work(self):
        self.status = "done"
        return "ok"

    @event_handler()
    @background
    def decorated(self, **kwargs):
        self.status = "decorated"

    @event_handler()
    def kick_off_async_def(self, **kwargs):
        self.status = "working"

        async def work():
            self.status = "async-done"

        self.start_async(work, name="coro")

    @event_handler()
    def kick_off_failing(self, **kwargs):
        self.status = "working"
        self.start_async(self._boom, name="boom")

    def _boom(self):
        raise ValueError("loader failed")

    @event_handler()
    def kick_off_then_cancel(self, **kwargs):
        self.status = "cancelled"
        self.start_async(self._work, name="work")
        self.cancel_async("work")

    @event_handler()
    def kick_off_two(self, **kwargs):
        self.start_async(self._cancel_the_other, name="first")
        self.start_async(self._work, name="second")

    def _cancel_the_other(self):
        # "second" was taken with the batch; cancel_async marks it for skipping.
        self.cancel_async("second")
        LOG.append(("first-ran",))

    @event_handler()
    def chain(self, **kwargs):
        self.start_async(self._next_page, 0, name="page")

    def _next_page(self, n):
        LOG.append(("page", n))
        self.start_async(self._next_page, n + 1, name="page")

    @event_handler()
    def plain(self, **kwargs):
        self.status = "plain"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["status"] = self.status
        ctx["data"] = self.data
        return ctx


setattr(sys.modules[__name__], "BackgroundView", BackgroundView)


class LoadsAfterFirstRender(BackgroundView):
    """The page reads its rows after its first render, as ``assign_async`` does."""

    loads = 0

    def mount(self, request, **kwargs):
        super().mount(request, **kwargs)
        self.start_async(self._load, name="load")

    def _load(self):
        type(self).loads += 1
        self.data = "rows"

    @event_handler()
    def select(self, **kwargs):
        # The event meets the loaded page, not the loading state.
        self.status = "saw:" + self.data


setattr(sys.modules[__name__], "LoadsAfterFirstRender", LoadsAfterFirstRender)


@pytest.fixture(autouse=True)
def _reset():
    LOG.clear()
    LoadsAfterFirstRender.loads = 0
    yield
    LOG.clear()


def _get_request():
    request = RequestFactory().get(PATH)
    request.user = AnonymousUser()
    request.tenant = None
    request.session = SessionStore()
    request.session.create()
    return request


def _post(view_cls, event, initial=None):
    initial = initial or _get_request()
    view_cls().get(initial)
    request = RequestFactory().post(
        PATH, data=json.dumps({}), content_type="application/json", HTTP_X_DJUST_EVENT=event
    )
    request.user, request.session, request.tenant = initial.user, initial.session, None
    view = view_cls()
    response = view.post(request)
    return view, response, response.content.decode("utf-8")


class TestHandlerWork:
    def test_start_async_work_runs_before_the_answer_is_rendered(self):
        with STATELESS:
            view, response, body = _post(BackgroundView, "kick_off")
        assert response.status_code == 200, body
        assert view.status == "done"
        assert "done" in body and "working" not in body, body

    def test_the_result_handler_sees_the_outcome(self):
        with STATELESS:
            _post(BackgroundView, "kick_off")
        assert LOG == [("result", "work", "ok", None)]

    def test_a_background_decorated_handler_runs(self):
        with STATELESS:
            view, _, body = _post(BackgroundView, "decorated")
        assert view.status == "decorated", body

    def test_an_async_def_callback_runs(self):
        with STATELESS:
            view, _, body = _post(BackgroundView, "kick_off_async_def")
        assert view.status == "async-done", body

    def test_default_policy_runs_it_too(self):
        """Not only ``view_state_in_session=False``: any POST is one response."""
        view, _, body = _post(BackgroundView, "kick_off")
        assert view.status == "done", body

    def test_a_failure_goes_to_handle_async_result_not_to_a_500(self, caplog):
        with STATELESS, caplog.at_level(logging.ERROR, logger="djust.mixins.request"):
            view, response, body = _post(BackgroundView, "kick_off_failing")
        assert response.status_code == 200, body
        assert view.status == "failed"
        assert LOG == [("result", "boom", None, "ValueError")]
        assert any("start_async task boom" in r.getMessage() for r in caplog.records)

    def test_a_cancelled_task_does_not_run(self):
        with STATELESS:
            view, _, body = _post(BackgroundView, "kick_off_then_cancel")
        assert view.status == "cancelled", body
        assert LOG == []

    def test_a_task_cancelled_by_an_earlier_one_is_skipped(self):
        with STATELESS:
            view, _, body = _post(BackgroundView, "kick_off_two")
        assert LOG == [("first-ran",), ("result", "first", None, None)], LOG
        assert view.status == "idle", body

    def test_work_that_keeps_queueing_work_is_bounded_and_says_so(self, caplog):
        with STATELESS, caplog.at_level(logging.WARNING, logger="djust.mixins.request"):
            _, response, body = _post(BackgroundView, "chain")
        assert response.status_code == 200, body
        pages = [entry for entry in LOG if entry[0] == "page"]
        assert len(pages) == request_mixin._HTTP_BACKGROUND_MAX_ROUNDS
        assert any("kept queueing background work" in r.getMessage() for r in caplog.records)

    def test_no_work_no_noise(self, caplog):
        with STATELESS, caplog.at_level(logging.WARNING, logger="djust.mixins.request"):
            view, _, _ = _post(BackgroundView, "plain")
        assert view.status == "plain"
        assert LOG == []
        assert not [r for r in caplog.records if "background" in r.getMessage()]


class TestWorkMountQueued:
    def test_a_page_that_loads_after_its_first_render_is_loaded_for_the_event(self):
        with STATELESS:
            view, response, body = _post(LoadsAfterFirstRender, "select")
        assert response.status_code == 200, body
        assert view.status == "saw:rows"

    def test_the_answer_carries_the_loaded_rows_not_the_loading_state(self):
        """The one answer holds the rows; it does not leave ``-`` on the page."""
        with STATELESS:
            _, _, body = _post(LoadsAfterFirstRender, "select")
        assert '"text": "rows"' in body, body
        assert "saw:rows" in body, body

    def test_a_denied_object_is_never_read(self):
        class Denied(LoadsAfterFirstRender):
            def get_object(self):
                return object()

            def has_object_permission(self, request, obj):
                return False

        setattr(sys.modules[__name__], "Denied", Denied)
        Denied.loads = 0
        with STATELESS:
            _, response, _ = _post(Denied, "select")
        assert response.status_code == 403
        assert Denied.loads == 0, "mount's background read ran for an object the user may not see"


class BgChild(BackgroundView):
    exposure_policy = "legacy"
    template = '<div><p id="c">{{ status }}</p></div>'


class BgPage(LiveView):
    exposure_policy = "legacy"
    template = (
        "{% load live_tags %}"
        '<div dj-root dj-view="' + __name__ + '.BgPage">'
        '{% live_render "' + __name__ + '.BgChild" view_id="kid" %}'
        "</div>"
    )

    def mount(self, request, **kwargs):
        pass


setattr(sys.modules[__name__], "BgChild", BgChild)
setattr(sys.modules[__name__], "BgPage", BgPage)


class TestEmbeddedChild:
    def test_an_embedded_child_s_background_work_runs_before_its_answer(self):
        initial = _get_request()
        BgPage.as_view()(initial)
        initial.session.save()
        request = RequestFactory().post(
            PATH,
            data=json.dumps({"view_id": "kid"}),
            content_type="application/json",
            HTTP_X_DJUST_EVENT="kick_off",
        )
        request.user, request.session, request.tenant = initial.user, initial.session, None
        response = BgPage.as_view()(request)
        assert response.status_code == 200, response.content
        html = json.loads(response.content)["html"]
        assert ">done<" in html and "working" not in html, html


class _Client:
    """A non-JSON private attribute: the session cannot hold it, so mount() reruns."""


class RebuiltWithSavedState(LoadsAfterFirstRender):
    def mount(self, request, **kwargs):
        super().mount(request, **kwargs)
        self._client = _Client()


setattr(sys.modules[__name__], "RebuiltWithSavedState", RebuiltWithSavedState)


class TestMountWorkIsNotRepeatedOverRestoredState:
    def test_default_policy_with_saved_state_does_not_run_mount_s_reads_again(self):
        """Saved state holds mount's results already; running them again repeats the read."""
        RebuiltWithSavedState.loads = 0
        view, response, body = _post(RebuiltWithSavedState, "plain")
        assert response.status_code == 200, body
        assert RebuiltWithSavedState.loads == 0
