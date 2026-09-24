"""A field whose ``initial`` is a callable gets the value, not the function.

Django's bound field calls a callable ``initial`` (``uuid.uuid4``,
``timezone.now``). ``FormMixin`` filled ``form_data`` from ``field.initial``
directly, so the function object itself was stored. Until #2974 the sync after
``form_valid`` overwrote it; once ``reset_form()`` inside ``form_valid`` stopped
being undone, the function reached the page and failed the next submit.
"""

from __future__ import annotations

import uuid

import pytest
from django import forms
from django.test import RequestFactory

from djust import LiveView
from djust.forms import FormMixin

pytestmark = [pytest.mark.django_db]


class TokenForm(forms.Form):
    body = forms.CharField()
    token = forms.UUIDField(initial=uuid.uuid4)


class TokenView(FormMixin, LiveView):
    form_class = TokenForm
    template = "<div dj-root>{{ form_data.token }}</div>"

    def form_valid(self, form):
        self.reset_form()


def _mounted():
    view = TokenView()
    view.mount(RequestFactory().get("/"))
    return view


def test_mount_calls_a_callable_initial():
    view = _mounted()
    assert not callable(view.form_data["token"])
    uuid.UUID(str(view.form_data["token"]))


def test_reset_in_form_valid_calls_a_callable_initial_and_the_next_submit_passes():
    view = _mounted()
    first = view.form_data["token"]
    view.submit_form(body="hello", token=str(first))
    assert view.field_errors == {}

    token = view.form_data["token"]
    assert not callable(token)
    assert str(token) != str(first), "reset draws a fresh value from the callable"

    view.submit_form(body="second", token=str(token))
    assert view.field_errors == {}


def test_plain_initial_and_no_initial_are_unchanged():
    class Plain(forms.Form):
        name = forms.CharField(initial="ann")
        note = forms.CharField(required=False)

    class PlainView(FormMixin, LiveView):
        form_class = Plain
        template = "<div dj-root></div>"

    view = PlainView()
    view.mount(RequestFactory().get("/"))
    assert view.form_data == {"name": "ann", "note": ""}
    view.reset_form()
    assert view.form_data == {"name": "ann", "note": ""}
