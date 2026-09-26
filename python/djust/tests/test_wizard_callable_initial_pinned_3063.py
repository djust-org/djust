"""#3063: ``WizardMixin`` resolves a callable field initial once per step.

``form_data`` and ``field_html`` each called the step form's callable
``initial`` (``uuid.uuid4``) on every render, so one render could show two
different UUIDs and every re-render showed a new one. The value is now drawn
once, when the step is first rendered, and kept in ``wizard_step_data`` -- the
value the page shows is the value the step submits.
"""

from __future__ import annotations

import datetime
import json
import re
import uuid

import pytest
from django import forms
from django.test import RequestFactory
from django.utils import timezone

from djust import LiveView
from djust.wizard import WizardMixin

pytestmark = [pytest.mark.django_db]

_calls = []


def _counted_uuid():
    _calls.append(1)
    return uuid.uuid4()


class TicketStep(forms.Form):
    token = forms.UUIDField(initial=_counted_uuid)
    opened = forms.DateTimeField(initial=timezone.now)
    note = forms.CharField(initial="hello", required=False)


class DoneStep(forms.Form):
    ok = forms.BooleanField(required=False)


class TicketWizard(WizardMixin, LiveView):
    wizard_steps = [
        {"name": "ticket", "title": "Ticket", "form_class": TicketStep},
        {"name": "done", "title": "Done", "form_class": DoneStep},
    ]
    template = "<div dj-root></div>"


def _mounted():
    view = TicketWizard()
    view.mount(RequestFactory().get("/"))
    return view


def _html_value(html):
    match = re.search(r'value="([^"]*)"', str(html))
    assert match, html
    return match.group(1)


def test_form_data_and_field_html_show_the_same_value():
    view = _mounted()
    ctx = view.get_context_data()
    token = ctx["form_data"]["token"]
    uuid.UUID(str(token))
    assert _html_value(ctx["field_html"]["token"]) == str(token)


def test_the_value_is_stable_across_renders_and_drawn_once():
    view = _mounted()
    _calls.clear()
    first = view.get_context_data()["form_data"]["token"]
    for _ in range(3):
        ctx = view.get_context_data()
        assert ctx["form_data"]["token"] == first
        assert _html_value(ctx["field_html"]["token"]) == str(first)
    assert _html_value(view.as_live_field("token")) == str(first)
    assert len(_calls) == 1, "the callable initial must be drawn once per step"


def test_the_shown_value_is_the_value_the_step_submits():
    view = _mounted()
    shown = view.get_context_data()["form_data"]["token"]
    view.next_step()
    assert view.wizard_step_index == 1, view.wizard_step_errors
    assert view.wizard_step_data["ticket"]["token"] == shown


def test_pinned_values_are_json_state_and_plain_initials_are_not_pinned():
    view = _mounted()
    view.get_context_data()
    pinned = view.wizard_step_data["ticket"]
    json.dumps(view.wizard_step_data)
    assert set(pinned) == {"token", "opened"}, "only callable initials are pinned"
    assert isinstance(pinned["opened"], str)
    # The stored text parses back to the moment it was drawn.
    parsed = TicketStep.base_fields["opened"].clean(pinned["opened"])
    assert abs(parsed - timezone.now()) < datetime.timedelta(minutes=1)


def test_a_value_the_user_entered_is_never_replaced():
    view = _mounted()
    mine = str(uuid.uuid4())
    view.validate_field(field="token", value=mine)
    ctx = view.get_context_data()
    assert ctx["form_data"]["token"] == mine
    assert _html_value(ctx["field_html"]["token"]) == mine
