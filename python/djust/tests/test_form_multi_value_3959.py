"""#3959 / #3958: the SERVER half of the form-event wire contract.

A client fix that sends a list is useless if binding or ``FormMixin`` then drops
or mistypes it, so this drives the exact payloads the client now sends
(``tests/js/form_multi_value_3959.test.js``, ``tests/js/submit_submitter_3958.test.js``)
through the real validation + call path (``validate_handler_params`` ->
``validated_call_arguments`` -> the handler), under the legacy policy and under
ADR-036 strict.
"""

import pytest
from django import forms

from djust import LiveView
from djust.config import config
from djust.decorators import event_handler
from djust.forms import FormMixin
from djust.validation import validate_handler_params, validated_call_arguments
from djust import state


class TagsForm(forms.Form):
    tags = forms.MultipleChoiceField(
        choices=[("x", "X"), ("y", "Y"), ("z", "Z")], widget=forms.CheckboxSelectMultiple
    )
    title = forms.CharField(required=False)


class TagsView(FormMixin, LiveView):
    exposure_policy = "explicit"
    form_class = TagsForm
    saved = state(None)

    def form_valid(self, form):
        self.saved = dict(form.cleaned_data)


@pytest.fixture(params=["legacy", "strict"])
def policy(request):
    old = config.get("event_parameter_policy", "legacy")
    config.set("event_parameter_policy", request.param)
    yield request.param
    config.set("event_parameter_policy", old)


def call(handler, name, params):
    """Run one event the way dispatch does: validate (and bind), then invoke."""
    result = validate_handler_params(handler, params, name)
    assert result["valid"], result.get("error")
    args, kwargs = validated_call_arguments(result)
    return handler(*args, **kwargs)


def test_the_policy_fixture_really_selects_the_binder(policy):
    """Guard against a vacuous parametrization: only strict produces a bound call plan."""
    view = TagsView()
    result = validate_handler_params(view.submit_form, {"tags": ["x"]}, "submit_form")
    assert result["valid"]
    assert (result.get("bound_arguments") is not None) == (policy == "strict")


def make_view():
    view = TagsView()
    view.mount(None)
    return view


def test_dj_change_on_a_group_stores_the_list_and_validates(policy):
    view = make_view()
    call(view.validate_field, "validate_field", {"field": "tags", "value": ["x", "y"]})
    assert view.form_data["tags"] == ["x", "y"]
    assert "tags" not in view.field_errors


def test_the_old_boolean_payload_was_the_bug(policy):
    """What dj-change used to send for one box of a group: ``True`` is not a list."""
    view = make_view()
    call(view.validate_field, "validate_field", {"field": "tags", "value": True})
    assert "tags" in view.field_errors


def test_dj_change_with_an_unticked_group_clears_it_rather_than_staying_stale(policy):
    view = make_view()
    call(view.validate_field, "validate_field", {"field": "tags", "value": ["x"]})
    call(view.validate_field, "validate_field", {"field": "tags", "value": []})
    assert view.form_data["tags"] == []
    assert "tags" in view.field_errors  # required: nothing chosen


def test_dj_submit_list_reaches_form_valid(policy):
    view = make_view()
    call(view.submit_form, "submit_form", {"tags": ["x", "y"], "title": "T"})
    assert view.saved == {"tags": ["x", "y"], "title": "T"}


def test_dj_submit_with_an_empty_group_after_a_filled_one_is_invalid(policy):
    view = make_view()
    call(view.submit_form, "submit_form", {"tags": ["x"], "title": "T"})
    view.saved = None
    call(view.submit_form, "submit_form", {"tags": [], "title": "T"})
    assert view.saved is None
    assert "tags" in view.field_errors


def test_single_value_of_a_group_is_a_list_of_one(policy):
    view = make_view()
    call(view.submit_form, "submit_form", {"tags": ["z"]})
    assert view.saved["tags"] == ["z"]


def test_the_pressed_button_is_an_ordinary_field_of_submit_form(policy):
    """Under strict, ``submit_form(**kwargs)`` is an open handler, so it receives
    ``intent``; the form ignores a name it does not declare."""
    view = make_view()
    call(view.submit_form, "submit_form", {"tags": ["x"], "intent": "draft"})
    assert view.saved["tags"] == ["x"]
    assert view.form_data["intent"] == "draft"


# -- strict binding of a declared type (ADR-036 D2) -----------------------------


class DeclaredView(LiveView):
    exposure_policy = "explicit"

    @event_handler(parameter_policy="strict")
    def save_list(self, tags: list[str]):
        return tags

    @event_handler(parameter_policy="strict")
    def save_scalar(self, tags: str):
        return tags

    @event_handler(parameter_policy="strict")
    def save_intent(self, title: str, intent: str = ""):
        return (title, intent)

    @event_handler(parameter_policy="strict")
    def save_closed(self, title: str):
        return title

    @event_handler(parameter_policy="legacy")
    def save_closed_legacy(self, title: str = ""):
        return title


def test_strict_list_str_accepts_the_list():
    assert call(DeclaredView().save_list, "save_list", {"tags": ["x", "y"]}) == ["x", "y"]


def test_strict_str_rejects_a_list_rather_than_guessing_which_value():
    result = validate_handler_params(
        DeclaredView().save_scalar, {"tags": ["x", "y"]}, "save_scalar"
    )
    assert not result["valid"]


def test_strict_list_str_rejects_a_scalar_and_does_not_split_a_string():
    """ADR-036 D2: no comma-separated interpretation of an array."""
    for scalar in ("x", "x,y"):
        result = validate_handler_params(DeclaredView().save_list, {"tags": scalar}, "save_list")
        assert not result["valid"]


def test_strict_declared_submitter_field_binds():
    assert call(DeclaredView().save_intent, "save_intent", {"title": "T", "intent": "submit"}) == (
        "T",
        "submit",
    )


def test_server_stays_authoritative_a_closed_strict_handler_rejects_an_injected_field():
    """The client only sends ``intent`` to a handler that declares it (Q1); a
    hand-crafted extra key is still refused by the server."""
    result = validate_handler_params(
        DeclaredView().save_closed, {"title": "T", "intent": "submit"}, "save_closed"
    )
    assert not result["valid"]


def test_a_legacy_closed_handler_rejects_an_unexpected_field_which_is_why_legacy_gets_no_submitter():
    """#3958: the client adds the pressed button only under strict. Under legacy a
    closed signature rejects an unexpected parameter, so adding ``intent`` would
    break every page whose submit button carries a ``name`` today."""
    ok = validate_handler_params(DeclaredView().save_closed_legacy, {"title": "T"}, "x")
    bad = validate_handler_params(
        DeclaredView().save_closed_legacy, {"title": "T", "intent": "submit"}, "x"
    )
    assert ok["valid"]
    assert not bad["valid"]
    assert "unexpected parameters" in bad["error"]
