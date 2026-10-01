"""#3303: the HTTP-POST fallback honours a navigation the handler queued.

``LiveView.post()`` rendered after every handler and drained only flash and
page metadata, so a queued ``live_redirect`` / ``live_patch`` was dropped, and
after ``logout()`` the render ran against an anonymous request (a 500 for a
``get_context_data`` that reads ``self.request.user``).

The WebSocket path sends a ``navigation`` frame. Here the same command rides
the JSON answer as ``_navigation`` (a list of frames in the WS shape, which the
client's ``handleNavigation`` consumes). A ``live_redirect`` leaves the page, so
nothing is rendered or saved; a ``live_patch`` keeps the view, so the render
still goes out beside the navigation.
"""

import json
from typing import Any

import pytest
from django.contrib.auth import get_user_model, login, logout
from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory

from djust import LiveView
from djust.decorators import event_handler

PATH = "/nav-probe/"


class NavPage(LiveView):
    template = '<div dj-root dj-id="0"><h1>{{ title }}</h1><p>{{ whoami }}</p></div>'

    rendered_for: list = []

    def mount(self, request: Any, **kwargs: Any) -> None:
        self.title = "X"

    def get_context_data(self, **kwargs: Any) -> dict:
        context = super().get_context_data(**kwargs)
        user = self.request.user
        # Like a view that filters a queryset on the user: unusable once anonymous.
        if not user.is_authenticated:
            raise TypeError("Field 'id' expected a number but got <AnonymousUser>")
        context["whoami"] = user.get_username()
        type(self).rendered_for.append(user.get_username())
        return context

    @event_handler()
    def leave(self, **kwargs: Any) -> None:
        logout(self.request)
        self.live_redirect("/x/")

    @event_handler()
    def go(self, **kwargs: Any) -> None:
        self.live_redirect("/detail/", params={"a": "1"}, replace=True)

    @event_handler()
    def filter_it(self, value: str = "", **kwargs: Any) -> None:
        self.title = value
        self.live_patch(params={"q": value})

    @event_handler()
    def rename(self, value: str = "", **kwargs: Any) -> None:
        self.title = value


@pytest.fixture
def session_user(db):
    user = get_user_model().objects.create_user("ann", password="pw")
    request = RequestFactory().get(PATH)
    request.session = SessionStore()
    login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    request.session.save()
    NavPage.rendered_for = []
    return request.session, user


def _request(session, user, event, **params):
    request = RequestFactory().post(
        PATH,
        data=json.dumps({"event": event, "params": params}),
        content_type="application/json",
    )
    request.session = session
    request.user = user
    return request


def _post(session, user, event, **params):
    request = _request(session, user, event, **params)
    return NavPage.as_view()(request)


def _get(session, user):
    request = RequestFactory().get(PATH)
    request.session = session
    request.user = user
    assert NavPage.as_view()(request).status_code == 200


def test_logout_then_live_redirect_returns_navigation_without_rendering(session_user):
    session, user = session_user
    _get(session, user)
    NavPage.rendered_for = []
    response = _post(session, user, "leave")
    assert response.status_code == 200, response.content
    body = json.loads(response.content)
    assert body["_navigation"] == [
        {"type": "navigation", "action": "live_redirect", "path": "/x/", "replace": False}
    ]
    assert "html" not in body and "patches" not in body and "version" not in body
    # The view never rendered for the logged-out request.
    assert NavPage.rendered_for == []


def test_live_redirect_carries_params_and_replace(session_user):
    session, user = session_user
    _get(session, user)
    body = json.loads(_post(session, user, "go").content)
    assert body["_navigation"] == [
        {
            "type": "navigation",
            "action": "live_redirect",
            "path": "/detail/",
            "replace": True,
            "params": {"a": "1"},
        }
    ]
    assert "html" not in body and "patches" not in body


def test_navigation_is_drained_so_the_next_event_does_not_replay_it(session_user):
    session, user = session_user
    _get(session, user)
    _post(session, user, "go")
    body = json.loads(_post(session, user, "rename", value="Z").content)
    assert "_navigation" not in body
    assert body.get("patches") or body.get("html")


def test_live_patch_renders_and_carries_the_navigation(session_user):
    session, user = session_user
    _get(session, user)
    response = _post(session, user, "filter_it", value="Q")
    assert response.status_code == 200, response.content
    body = json.loads(response.content)
    assert body["_navigation"] == [
        {"type": "navigation", "action": "live_patch", "replace": False, "params": {"q": "Q"}}
    ]
    assert body.get("patches") or body.get("html")


def test_a_plain_event_has_no_navigation_key(session_user):
    session, user = session_user
    _get(session, user)
    body = json.loads(_post(session, user, "rename", value="Z").content)
    assert "_navigation" not in body
