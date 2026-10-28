"""The HTTP fallback refuses an event the page view has no handler for.

Over WebSocket and SSE an event name that resolves to nothing is answered with
an error frame and no handler runs. The HTTP fallback already did that for a
component's event (``component_id``) but not for the page view's own: a name
that resolved to no attribute, or to a non-callable one, fell through the
dispatch and was answered with 200, a full render and a state save, as if a
handler had run. Found by the 2026-10 explicit-path security probes.

Every case drives the real view (``as_view()``) with a real session, as the
browser's fallback does, mirroring ``test_http_embedded_view_id_3104``.
"""

import json

import pytest
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory

from djust import LiveView
from djust.decorators import event_handler

MOD = __name__
PATH = "/unknown-event/"


class Page(LiveView):
    exposure_policy = "legacy"
    template = (
        '<div dj-root dj-view="' + MOD + '.Page">'
        '<p><span id="r">{{ received }}</span> {{ count }}</p>'
        '<button dj-click="got_click">c</button>'
        "</div>"
    )

    def mount(self, request, **kwargs):
        self.received = ""
        self.count = 7  # a public, non-callable attribute

    @event_handler
    def got_click(self, **kwargs):
        self.received = "click"


def _request(method, session, event=None):
    factory = RequestFactory()
    if method == "get":
        request = factory.get(PATH)
    else:
        request = factory.post(
            PATH,
            data=json.dumps({}),
            content_type="application/json",
            HTTP_X_DJUST_EVENT=event,
        )
    request.user = AnonymousUser()
    request.tenant = None
    request.session = session
    return request


def _post(session, event):
    response = Page.as_view()(_request("post", session, event))
    session.save()
    return response


@pytest.fixture
def session():
    store = SessionStore()
    store.create()
    return store


@pytest.mark.django_db
def test_a_real_handler_still_runs_over_http(session):
    Page.as_view()(_request("get", session))
    response = _post(session, "got_click")
    assert response.status_code == 200
    assert session.get("liveview_" + PATH, {}).get("received") == "click"


@pytest.mark.django_db
@pytest.mark.parametrize("event", ["nonexistent", "count"])
def test_an_unknown_or_non_callable_event_is_refused_and_saves_nothing(session, event):
    """No attribute, or a plain attribute: 400, no render, no state write."""
    Page.as_view()(_request("get", session))
    before = dict(session.get("liveview_" + PATH, {}))
    response = _post(session, event)
    assert response.status_code == 400
    assert json.loads(response.content) == {"error": "Event handler not found"}
    assert dict(session.get("liveview_" + PATH, {})) == before
