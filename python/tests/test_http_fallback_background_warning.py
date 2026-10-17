"""The HTTP POST fallback says so when a handler's background work is dropped.

The fallback does not run ``start_async`` / ``@background`` work: the queued
tasks are discarded with the request's view instance. Until the 2.0 design
settles what it should do instead, it logs one WARNING per view class naming
the handler, so the drop is no longer silent.
"""

import json
import logging

import pytest
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory

from djust import LiveView
from djust.decorators import background, event_handler
from djust.mixins import request as request_mixin


class BackgroundView(LiveView):
    template = "<div dj-root><span>{{ status }}</span></div>"

    def mount(self, request, **kwargs):
        self.status = "idle"

    @event_handler()
    def kick_off(self, **kwargs):
        self.status = "working"
        self.start_async(self._work)

    @event_handler()
    @background
    def decorated(self, **kwargs):
        self.status = "done"

    @event_handler()
    def plain(self, **kwargs):
        self.status = "plain"

    def _work(self):
        self.status = "done"


class OtherBackgroundView(BackgroundView):
    pass


def _post(view_cls, event_name):
    view = view_cls()
    factory = RequestFactory()
    get_request = factory.get("/test/")
    SessionMiddleware(lambda r: None).process_request(get_request)
    get_request.session.save()
    view.get(get_request)
    body = json.dumps({"event": event_name, "params": {}})
    post_request = factory.post("/test/", data=body, content_type="application/json")
    post_request.session = get_request.session
    return view.post(post_request)


@pytest.fixture(autouse=True)
def _reset_warned():
    request_mixin._HTTP_BACKGROUND_WARNED.clear()
    yield
    request_mixin._HTTP_BACKGROUND_WARNED.clear()


def _warnings(caplog):
    return [
        r.getMessage()
        for r in caplog.records
        if r.levelno == logging.WARNING and "background work does not run" in r.getMessage()
    ]


@pytest.mark.django_db
@pytest.mark.parametrize("event_name", ["kick_off", "decorated"])
def test_dropped_background_work_is_logged_with_the_handler(caplog, event_name):
    with caplog.at_level(logging.WARNING, logger="djust.mixins.request"):
        response = _post(BackgroundView, event_name)
    assert response.status_code == 200
    [message] = _warnings(caplog)
    assert "BackgroundView.%s" % event_name in message
    assert "HTTP fallback" in message


@pytest.mark.django_db
def test_the_warning_is_logged_once_per_view_class(caplog):
    with caplog.at_level(logging.WARNING, logger="djust.mixins.request"):
        _post(BackgroundView, "kick_off")
        _post(BackgroundView, "kick_off")
        _post(OtherBackgroundView, "kick_off")
    messages = _warnings(caplog)
    assert len(messages) == 2, messages
    assert any(m.startswith("OtherBackgroundView.") for m in messages)


@pytest.mark.django_db
def test_no_warning_without_background_work(caplog):
    with caplog.at_level(logging.WARNING, logger="djust.mixins.request"):
        _post(BackgroundView, "plain")
    assert _warnings(caplog) == []
