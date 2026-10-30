"""``LiveView.handle_restore(request)``: re-read after a session restore.

Reported downstream (Point Guard N-40, decision 204, djust 1.3.0rc11): the HTTP
page-POST fallback rebuilds a view from its saved session state, and either does
not call ``mount()`` (everything was saved) or calls it BEFORE the saved state
is put back (a private attribute could not be saved). ``mount()`` never sees
the restored state, so a view that keeps its rows out of the session and reads
them from a restored id never read them again: Point Guard's Activity page
stayed "loading" over the fallback alone.
"""

from __future__ import annotations

import json

import pytest
from django.test import RequestFactory

from djust import LiveView
from djust.decorators import event_handler


class _Rows:
    """Not JSON: djust never saves it, so the fallback must rebuild it."""

    def __init__(self, items):
        self.items = list(items)


def _read(cursor):
    return ["row-%d" % i for i in range(cursor, cursor + 2)]


class ListView(LiveView):
    template = '<div dj-root dj-view="tests.ListView">{{ cursor }} {{ shown }}</div>'
    restores = 0

    def mount(self, request, **kwargs):
        self.cursor = 0
        self.shown = ""
        self._rows = _Rows(_read(self.cursor))

    def handle_restore(self, request):
        type(self).restores += 1
        self._rows = _Rows(_read(self.cursor))  # from the RESTORED cursor

    @event_handler()
    def next_page(self, **kwargs):
        self.cursor += 2
        self._rows = _Rows(_read(self.cursor))

    @event_handler()
    def show(self, **kwargs):
        self.shown = ",".join(self._rows.items)


class AllSavedView(LiveView):
    """Every private attribute is JSON, so the fallback never calls mount()."""

    template = '<div dj-root dj-view="tests.AllSavedView">{{ shown }}</div>'

    def mount(self, request, **kwargs):
        self.chat_id = 7
        self.shown = ""
        self._ids = [1]

    def handle_restore(self, request):
        self._title = "chat %d" % self.chat_id  # never saved: read again

    @event_handler()
    def show(self, **kwargs):
        self.shown = getattr(self, "_title", "MISSING")


def _get(view_class):
    from djust.tests.test_exposure_runtime import make_request

    initial = make_request()
    assert view_class().get(initial).status_code == 200
    return initial


def _post(view_class, initial, event):
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


@pytest.mark.django_db
def test_fallback_rereads_from_the_restored_state_when_mount_runs_first():
    initial = _get(ListView)
    _post(ListView, initial, "next_page")  # cursor 2 is saved

    view = _post(ListView, initial, "show")

    assert view.cursor == 2
    assert view.shown == "row-2,row-3"  # mount() alone read row-0,row-1


@pytest.mark.django_db
def test_fallback_calls_it_when_mount_does_not_run():
    initial = _get(AllSavedView)

    view = _post(AllSavedView, initial, "show")

    assert view.shown == "chat 7"


@pytest.mark.django_db
def test_not_called_on_a_plain_get():
    ListView.restores = 0
    _get(ListView)
    assert ListView.restores == 0


def test_default_is_a_no_op():
    assert LiveView().handle_restore(None) is None
