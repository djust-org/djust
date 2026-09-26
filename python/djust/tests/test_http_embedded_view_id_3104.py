"""#3104 (partial): the HTTP fallback refuses an event addressed to an embedded child.

Over WebSocket and SSE an event carrying an embedded child's ``view_id``
runs on that child, and an unknown ``view_id`` is refused ("Embedded view not
found"). The HTTP fallback ignored ``view_id`` and ran the event on the
parent, so with matching handler names it silently changed the parent's
state. It now refuses, like the socket runtime refuses an unknown id: an HTTP
request has no registered child to route to (children register during the
render, after dispatch, under fresh ids). Routing to the child over HTTP
remains open in #3104.
"""

import json
import re

import pytest
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory

from djust_demos.views.embedded_directives_demo import EmbeddedDirectivesView

PATH = "/demos/embedded-directives/"


def _request(method, session, body=None, event=None):
    factory = RequestFactory()
    if method == "get":
        request = factory.get(PATH)
    else:
        request = factory.post(
            PATH,
            data=json.dumps(body),
            content_type="application/json",
            HTTP_X_DJUST_EVENT=event,
        )
    request.user = AnonymousUser()
    request.tenant = None
    request.session = session
    return request


def _page(session):
    response = EmbeddedDirectivesView.as_view()(_request("get", session))
    html = response.content.decode()
    child_id = re.search(r'data-djust-embedded="([^"]+)"', html).group(1)
    session.save()
    return child_id


def _post(session, body, event="got_click"):
    response = EmbeddedDirectivesView.as_view()(_request("post", session, body, event))
    session.save()
    return response


def _parent_received(session):
    """What the parent recorded, from the state the POST saved."""
    return session.get("liveview_" + PATH, {}).get("received", "").split()


@pytest.fixture
def session():
    store = SessionStore()
    store.create()
    return store


@pytest.mark.django_db
@pytest.mark.parametrize("flat", [True, False])
def test_an_event_for_the_embedded_child_is_refused_not_run_on_the_parent(session, flat):
    child_id = _page(session)
    body = {"view_id": child_id}
    response = _post(session, body if flat else {"event": "got_click", "params": body})
    assert response.status_code == 400
    assert json.loads(response.content) == {"error": "Embedded view not found"}
    # The parent never ran the child's handler.
    assert "click" not in _parent_received(session)
    follow = _post(session, {}, event="got_away")
    assert follow.status_code == 200
    assert _parent_received(session) == ["away"]


@pytest.mark.django_db
def test_a_forged_view_id_is_refused(session):
    _page(session)
    response = _post(session, {"view_id": "child_999999"})
    assert response.status_code == 400
    assert json.loads(response.content) == {"error": "Embedded view not found"}


@pytest.mark.django_db
def test_a_parent_event_still_runs_on_the_parent(session):
    _page(session)
    response = _post(session, {})
    assert response.status_code == 200
    assert _parent_received(session) == ["click"]


@pytest.mark.django_db
@pytest.mark.parametrize("flat", [True, False])
@pytest.mark.parametrize("empty", [None, "", 0, False, []])
def test_an_empty_view_id_is_the_parent_as_on_the_socket_runtime(session, flat, empty):
    """PR #3159 review: the runtime pops ``view_id`` and routes a falsy one to
    the root view; the HTTP fallback now does the same."""
    _page(session)
    body = {"view_id": empty}
    response = _post(session, body if flat else {"event": "got_click", "params": body})
    assert response.status_code == 200, response.content
    assert _parent_received(session) == ["click"]


@pytest.mark.django_db
@pytest.mark.parametrize("forged", ["child_999999", "__root", ["child_1"], 1])
def test_every_truthy_foreign_view_id_is_refused(session, forged):
    _page(session)
    response = _post(session, {"view_id": forged})
    assert response.status_code == 400
    assert _parent_received(session) == []
