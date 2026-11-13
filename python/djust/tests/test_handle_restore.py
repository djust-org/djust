"""``LiveView.handle_restore(request)``: re-read after a state restore.

Reported downstream (djust 1.3.0rc11): the HTTP page-POST fallback rebuilt a
view from its saved session state and never let it read again what the session
did not hold. The hook runs whenever saved state is put back over ``mount()``:
an explicit view's (``test_handle_restore_explicit_3989``), and, since #4107,
a legacy view's over the fallback, whose public attributes are saved again.
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
    exposure_policy = "legacy"
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
def test_called_on_a_legacy_fallback_post_once_state_was_saved():
    ListView.restores = 0
    initial = _get(ListView)
    _post(ListView, initial, "next_page")  # nothing stored yet: no restore
    assert ListView.restores == 0
    view = _post(ListView, initial, "show")
    assert ListView.restores == 1
    assert view.cursor == 2  # the saved cursor, and rows read again from it
    assert view.shown == "row-2,row-3"


@pytest.mark.django_db
def test_not_called_on_a_plain_get():
    ListView.restores = 0
    _get(ListView)
    assert ListView.restores == 0


def test_default_is_a_no_op():
    assert LiveView().handle_restore(None) is None
