"""``params_form`` initials: a left-out multi-select (#3922), mutable initials (#3926).

#3922: a multi-select widget (``SelectMultiple``, ``CheckboxSelectMultiple``) says
it is never omitted from the data, so a key the URL left out counted as supplied
and its ``initial`` was not applied after ``clean()``.

#3926: ``Field.__deepcopy__`` does not copy ``initial``, so the object a bound
form hands ``handle_params()`` was the one stored on the form class. A
``handle_params()`` that mutated it changed the default for every later request.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django import forms

from djust import LiveView
from djust._params_form import _url_supplied, bind_params, fallback_values, initial_values
from djust.testing import LiveViewTestClient

pytestmark = pytest.mark.django_db


def _bound(form_class, params):
    view = SimpleNamespace()
    form, values = bind_params(view, form_class, params)
    return form, values


# --------------------------------------------------------------------------- #
# #3922 — a multi-select the URL left out is not supplied
# --------------------------------------------------------------------------- #

CHOICES = [("a", "A"), ("b", "B"), ("ok", "ok")]


def _tags_form(widget):
    class Tags(forms.Form):
        tags = forms.MultipleChoiceField(
            choices=CHOICES, required=False, initial=["a"], widget=widget
        )
        q = forms.CharField(max_length=5, required=False, initial="")

        def clean(self):
            cleaned = super().clean()
            if "q" in self.data:
                cleaned["tags"] = [self.data["q"]]
            return cleaned

    return Tags


MULTI_WIDGETS = [forms.SelectMultiple, forms.CheckboxSelectMultiple, forms.MultipleHiddenInput]


@pytest.mark.parametrize("widget", MULTI_WIDGETS)
def test_a_multi_select_key_the_url_left_out_is_not_supplied(widget):
    form, _ = _bound(_tags_form(widget), {"q": "ok"})
    assert _url_supplied(form, "tags", form.fields["tags"]) is False


@pytest.mark.parametrize("widget", MULTI_WIDGETS)
def test_a_multi_select_key_the_url_carried_is_supplied(widget):
    form, _ = _bound(_tags_form(widget), {"tags": ["b"]})
    assert _url_supplied(form, "tags", form.fields["tags"]) is True


@pytest.mark.parametrize("widget", MULTI_WIDGETS)
def test_an_empty_multi_select_value_is_not_supplied(widget):
    """``?tags=`` and an empty list are no value: the field takes its initial.

    (``?tags=`` itself fails validation, ``""`` being no choice; an empty list
    binds as absent.)
    """
    form, _ = _bound(_tags_form(widget), {"tags": ""})
    assert _url_supplied(form, "tags", form.fields["tags"]) is False
    form, values = _bound(_tags_form(widget), {"tags": []})
    assert values["tags"] == ["a"]


@pytest.mark.parametrize("widget", MULTI_WIDGETS)
def test_the_valid_path_applies_the_initial_over_what_clean_wrote(widget):
    _, values = _bound(_tags_form(widget), {"q": "ok"})
    assert values == {"tags": ["a"], "q": "ok"}


@pytest.mark.parametrize("widget", MULTI_WIDGETS)
def test_the_fields_fallback_applies_the_initial_over_a_failed_raw_value(widget):
    form, values = _bound(_tags_form(widget), {"q": "toolong!"})
    assert values is None
    assert fallback_values(form, "fields")["tags"] == ["a"]


@pytest.mark.parametrize("widget", MULTI_WIDGETS)
def test_a_supplied_multi_select_value_is_kept(widget):
    class Plain(forms.Form):
        tags = forms.MultipleChoiceField(
            choices=CHOICES, required=False, initial=["a"], widget=widget
        )

    _, values = _bound(Plain, {"tags": ["b", "ok"]})
    assert values == {"tags": ["b", "ok"]}


# --------------------------------------------------------------------------- #
# #3926 — a handed-over initial is a copy
# --------------------------------------------------------------------------- #


class MutableInitials(forms.Form):
    j = forms.JSONField(required=False, initial=[])
    tags = forms.MultipleChoiceField(choices=CHOICES, required=False, initial=["a"])
    q = forms.CharField(max_length=5, required=False, initial="")


def _assert_class_untouched():
    assert MutableInitials.base_fields["j"].initial == []
    assert MutableInitials.base_fields["tags"].initial == ["a"]
    assert MutableInitials().fields["j"].initial == []


def _mutate(values):
    values["j"].append("x")
    values["tags"].append("b")


def test_the_valid_path_hands_over_a_copy():
    _, values = _bound(MutableInitials, {"q": "ok"})
    _mutate(values)
    _assert_class_untouched()


def test_the_initial_mode_hands_over_a_copy():
    form, values = _bound(MutableInitials, {"q": "toolong!"})
    assert values is None
    _mutate(fallback_values(form, "initial"))
    _mutate(initial_values(form))
    _assert_class_untouched()


def test_the_fields_mode_replaced_field_and_refused_recheck_hand_over_a_copy():
    class Refuses(MutableInitials):
        def clean(self):
            cleaned = super().clean()
            if cleaned.get("q") == "no":
                raise forms.ValidationError("refused")
            return cleaned

    # ``j`` fails (not JSON), so it is replaced by its initial.
    form, values = _bound(Refuses, {"j": "{not json", "q": "ok"})
    assert values is None
    out = fallback_values(form, "fields")
    assert out["j"] == [] and out["tags"] == ["a"]
    _mutate(out)
    assert Refuses.base_fields["j"].initial == []
    assert Refuses.base_fields["tags"].initial == ["a"]

    # The re-check refuses: the plain initials.
    form, values = _bound(Refuses, {"j": "{not json", "q": "no"})
    out = fallback_values(form, "fields")
    _mutate(out)
    assert Refuses.base_fields["j"].initial == []
    assert Refuses.base_fields["tags"].initial == ["a"]


SEEN = []


class MutatingView(LiveView):
    exposure_policy = "explicit"
    login_required = False
    template = "<div dj-root></div>"
    params_form = MutableInitials

    def handle_params(self, params, uri):
        SEEN.append((list(params["j"]), list(params["tags"])))
        params["j"].append("x")
        params["tags"].append("b")


def test_two_sequential_requests_see_the_same_default():
    """The leak across requests, end to end through the test client (the HTTP GET's path)."""
    SEEN.clear()
    LiveViewTestClient(MutatingView).mount()
    LiveViewTestClient(MutatingView).mount(
        query_params={"q": "zzzzzzzz"}
    )  # invalid: "initial" mode
    LiveViewTestClient(MutatingView).mount(query_params={"q": "ok"})
    assert SEEN == [([], ["a"])] * 3
    _assert_class_untouched()


# --------------------------------------------------------------------------- #
# The same leak in FormMixin's ``form_data`` (found while fixing #3926)
# --------------------------------------------------------------------------- #


def test_form_mixin_form_data_holds_a_copy_of_a_mutable_initial():
    """``form_data`` was seeded with the class's own ``initial`` object (``prepare_value``
    returns a list as it is), so a handler that appended to it changed the default."""
    from djust.forms import FormMixin

    class TagForm(forms.Form):
        tags = forms.MultipleChoiceField(choices=CHOICES, required=False, initial=["a"])
        j = forms.JSONField(required=False, initial={"k": []})

    class TagView(FormMixin, LiveView):
        exposure_policy = "explicit"
        login_required = False
        template = "<div dj-root></div>"
        form_class = TagForm

    for _ in range(2):
        client = LiveViewTestClient(TagView).mount()
        data = client.view_instance.form_data
        assert data["tags"] == ["a"]
        data["tags"].append("b")
        client.view_instance.reset_form()
        client.view_instance.form_data["tags"].append("b")
    assert TagForm.base_fields["tags"].initial == ["a"]
