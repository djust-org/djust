"""The stateless HTTP page-POST fallback runs ``handle_params()`` and background work.

With ``view_state_in_session=False`` (and for any view the fallback rebuilds
with ``mount()``) the POST builds a fresh view. The GET and the socket mount
call ``handle_params(params, uri)`` right after ``mount()``; the fallback did
not, so a page that reads its URL there (filters, a scope, ``?tab=``) met the
event with its defaults and rendered a different page from the one the browser
showed (#3624). It also dropped ``start_async`` / ``@background`` work, so a
page that loads after its first render kept its loading state on every POST.

The tests drive the real ``LiveView.get`` / ``.post`` with a ``RequestFactory``
and a real session, the shape the browser's POST to ``window.location.href``
takes: the query string rides on the POST's own URL.
"""

from __future__ import annotations

import json
import sys

import pytest
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory, override_settings

from djust import LiveView
from djust.decorators import event_handler

pytestmark = pytest.mark.django_db

STATELESS = override_settings(LIVEVIEW_CONFIG={"view_state_in_session": False})
PATH = "/scoped/"

ROWS = {"all": ["a", "b", "c"], "mine": ["a"]}


class ScopedView(LiveView):
    """Reads its scope from the URL in ``handle_params()``, like a filtered list."""

    template = (
        '<div dj-root dj-id="0"><p>{{ scope }}</p><p>{{ rows_text }}</p><p>{{ note }}</p></div>'
    )
    handle_params_calls: list = []

    def mount(self, request, **kwargs):
        self.scope = "all"
        self.rows = list(ROWS["all"])
        self.note = ""

    def handle_params(self, params, uri):
        type(self).handle_params_calls.append((dict(params), uri))
        self.scope = params.get("scope", "all")
        self.rows = list(ROWS.get(self.scope, ROWS["all"]))

    @event_handler()
    def archive(self, **kwargs):
        # The event meets the page the browser shows: its scope and its rows.
        self.note = "%s:%s" % (self.scope, ",".join(self.rows))

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["scope"] = self.scope
        ctx["rows_text"] = ",".join(self.rows)
        ctx["note"] = self.note
        return ctx


setattr(sys.modules[__name__], "ScopedView", ScopedView)


@pytest.fixture(autouse=True)
def _reset_calls():
    ScopedView.handle_params_calls = []
    yield
    ScopedView.handle_params_calls = []


def _get_request(url=PATH):
    request = RequestFactory().get(url)
    request.user = AnonymousUser()
    request.tenant = None
    request.session = SessionStore()
    request.session.create()
    return request


def _post(initial, view, event, url=PATH, params=None):
    request = RequestFactory().post(
        url,
        data=json.dumps(params or {}),
        content_type="application/json",
        HTTP_X_DJUST_EVENT=event,
    )
    request.user, request.session, request.tenant = initial.user, initial.session, None
    return view.post(request)


def _answer_text(response) -> str:
    return response.content.decode("utf-8")


class TestHandleParamsOnTheFallbackPost:
    def test_the_event_meets_the_page_the_url_names(self):
        """The reproducer: the handler sees ``?scope=mine``, not ``mount()``'s default."""
        with STATELESS:
            url = PATH + "?scope=mine"
            initial = _get_request(url)
            ScopedView().get(initial)
            ScopedView.handle_params_calls = []

            view = ScopedView()
            response = _post(initial, view, "archive", url=url)

        assert response.status_code == 200, response.content
        assert view.note == "mine:a"
        assert ScopedView.handle_params_calls == [({"scope": "mine"}, url)]

    def test_the_answer_keeps_the_rows_the_user_was_acting_on(self):
        """The render the answer diffs is the page's, not the defaults'."""
        with STATELESS:
            url = PATH + "?scope=mine"
            initial = _get_request(url)
            get_html = _answer_text(ScopedView().get(initial))
            assert "<p>a</p>" in get_html and "a,b,c" not in get_html

            response = _post(initial, ScopedView(), "archive", url=url)

        body = _answer_text(response)
        # Whatever shape the answer takes (patches or html), it never carries
        # the default page's rows: that would remove the rows the user acted on.
        assert "a,b,c" not in body, body
        assert "mine:a" in body, body

    def test_params_have_the_shape_the_get_passes(self):
        """A repeated key stays a list, a single value is a scalar (as on GET)."""
        with STATELESS:
            url = PATH + "?tag=x&tag=y&scope=mine"
            initial = _get_request(url)
            _post(initial, ScopedView(), "archive", url=url)

        [(params, uri)] = ScopedView.handle_params_calls
        assert params == {"tag": ["x", "y"], "scope": "mine"}
        assert uri == url

    def test_no_query_string_still_calls_handle_params_with_empty_params(self):
        with STATELESS:
            initial = _get_request()
            view = ScopedView()
            _post(initial, view, "archive")

        assert ScopedView.handle_params_calls == [({}, PATH)]
        assert view.note == "all:a,b,c"


class TestWhenStateWasRestored:
    def test_a_restored_view_does_not_run_handle_params_again(self):
        """Default policy: the saved state already holds what handle_params set.

        The negative half of the rule "when no state was restored": running it
        over restored state would overwrite what the previous event changed.
        """
        url = PATH + "?scope=mine"
        initial = _get_request(url)
        ScopedView().get(initial)  # saves scope=mine in the session
        ScopedView.handle_params_calls = []

        view = ScopedView()
        response = _post(initial, view, "archive", url=url)

        assert response.status_code == 200, response.content
        assert view.note == "mine:a"
        assert ScopedView.handle_params_calls == []


class ExplicitScopedView(ScopedView):
    exposure_policy = "explicit"


setattr(sys.modules[__name__], "ExplicitScopedView", ExplicitScopedView)


class TestExplicitExposure:
    def test_an_explicit_exposure_view_gets_handle_params_after_mount(self):
        """Explicit exposure re-mounts on every POST: the URL is read again too."""
        with STATELESS:
            url = PATH + "?scope=mine"
            initial = _get_request(url)
            view = ExplicitScopedView()
            response = _post(initial, view, "archive", url=url)

        assert response.status_code == 200, response.content
        assert view.scope == "mine"
        assert ScopedView.handle_params_calls == [({"scope": "mine"}, url)]
