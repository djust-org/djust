"""Form adapters render accessible, themeable fields.

``FormMixin.as_live_field`` / ``as_live`` and ``{% live_field %}`` / ``{% live_form %}`` render
through the framework adapters (``djust.frameworks``). These tests pin what an application relies
on to use them instead of hand-written markup:

* help text and errors are linked to the control (``aria-describedby`` with ``<auto_id>-help`` /
  ``<auto_id>-error``), an invalid control is ``aria-invalid="true"``, a required one ``required``;
* a ChoiceField whose choices already have a blank option gets no second one;
* a RadioSelect is a ``<fieldset role="radiogroup">`` named by its ``<legend>``, with one
  ``<input type="radio" dj-change="validate_field">`` per choice and valid, value-free ids, and it
  round-trips through ``validate_field`` and ``submit_form``;
* a CheckboxSelectMultiple is a ``<fieldset>`` of checkboxes;
* the classes come from ``LIVEVIEW_CONFIG["form_classes"]`` or a ``css_classes`` subclass.
"""

from __future__ import annotations

import re
from typing import List

import pytest
from django import forms
from django.template import engines
from django.test import override_settings

from djust import LiveView
from djust.forms import FormMixin
from djust.frameworks import (
    FORM_CLASS_SLOTS,
    Bootstrap5Adapter,
    PlainAdapter,
    TailwindAdapter,
    get_adapter,
    register_adapter,
)
from djust.testing import LiveViewTestClient

pytestmark = [pytest.mark.pr_tier]

ADAPTERS = [Bootstrap5Adapter(), TailwindAdapter(), PlainAdapter(), get_adapter("bootstrap4")]
ADAPTER_IDS = ["bootstrap5", "tailwind", "plain", "bootstrap4"]


class OrderForm(forms.Form):
    name = forms.CharField(help_text="As on the card")
    notes = forms.CharField(widget=forms.Textarea, required=False, help_text="Optional")
    size = forms.ChoiceField(
        choices=[("s", "Small"), ("m", "Medium size"), ("l", "Large")],
        widget=forms.RadioSelect,
        help_text="Pick one",
    )
    kind = forms.ChoiceField(choices=[("", "Pick a kind"), ("a", "Alpha")], required=False)
    colour = forms.ChoiceField(choices=[("r", "Red"), ("g", "Green")], required=False)
    toppings = forms.MultipleChoiceField(
        choices=[("x", "Extra"), ("y", "Why not")],
        widget=forms.CheckboxSelectMultiple,
        required=False,
    )
    agree = forms.BooleanField(help_text="Required to order")


FORM = OrderForm()


def _tag(html: str, pattern: str) -> str:
    """The first opening tag matching *pattern* (a regex for its start)."""
    match = re.search(pattern + r"[^>]*>", html)
    assert match, f"no tag {pattern!r} in {html}"
    return match.group(0)


def _render(adapter, name: str, value="", errors: List[str] = (), **kwargs) -> str:
    return adapter.render_field(FORM.fields[name], name, value, list(errors), **kwargs)


# --------------------------------------------------------------------------- #
# 1. help text and errors are linked to the control
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("adapter", ADAPTERS, ids=ADAPTER_IDS)
class TestDescribedBy:
    def test_help_text_describes_the_input(self, adapter):
        html = _render(adapter, "name")
        control = _tag(html, r'<input name="name"')
        assert 'id="id_name"' in control
        assert 'aria-describedby="id_name-help"' in control
        assert "aria-invalid" not in control
        assert re.search(r'<\w+ id="id_name-help"[^>]*>As on the card<', html)

    def test_an_error_describes_and_invalidates_the_input(self, adapter):
        html = _render(adapter, "name", errors=["This field is required."])
        control = _tag(html, r'<input name="name"')
        assert 'aria-describedby="id_name-help id_name-error"' in control
        assert 'aria-invalid="true"' in control
        error = _tag(html, r'<\w+ id="id_name-error"')
        after = html[html.index(error) :]
        assert "This field is required." in after

    def test_required_is_native(self, adapter):
        assert 'required="required"' in _tag(_render(adapter, "name"), r'<input name="name"')
        assert "required" not in _tag(_render(adapter, "notes"), r"<textarea")

    def test_textarea_and_select_are_described(self, adapter):
        textarea = _tag(_render(adapter, "notes", errors=["Too long."]), r"<textarea")
        assert 'aria-describedby="id_notes-help id_notes-error"' in textarea
        assert 'aria-invalid="true"' in textarea
        select = _tag(_render(adapter, "kind", errors=["Bad."]), r"<select")
        assert 'aria-describedby="id_kind-error"' in select
        assert 'aria-invalid="true"' in select

    def test_checkbox_is_described_and_has_one_label(self, adapter):
        html = _render(adapter, "agree", errors=["Tick it."])
        control = _tag(html, r'<input type="checkbox"')
        assert 'aria-describedby="id_agree-help id_agree-error"' in control
        assert 'aria-invalid="true"' in control
        assert html.count("<label") == 1

    def test_not_rendered_means_not_referenced(self, adapter):
        html = _render(
            adapter, "name", errors=["Bad."], render_help_text=False, render_errors=False
        )
        control = _tag(html, r'<input name="name"')
        assert "aria-describedby" not in control
        assert 'aria-invalid="true"' in control
        assert "id_name-help" not in html and "id_name-error" not in html

    def test_auto_id_sets_every_id(self, adapter):
        html = _render(adapter, "name", errors=["Bad."], auto_id="id_billing-name")
        assert 'for="id_billing-name"' in html
        control = _tag(html, r'<input name="name"')
        assert 'id="id_billing-name"' in control
        assert 'aria-describedby="id_billing-name-help id_billing-name-error"' in control

    def test_widget_describedby_is_kept(self, adapter):
        field = forms.CharField(
            help_text="h", widget=forms.TextInput(attrs={"aria-describedby": "hint-1"})
        )
        control = _tag(adapter.render_field(field, "code", "", []), r'<input name="code"')
        assert 'aria-describedby="id_code-help hint-1"' in control

    def test_required_marker_is_hidden_from_assistive_technology(self, adapter):
        label = re.search(r"<label[^>]*>.*?</label>", _render(adapter, "name")).group(0)
        assert 'aria-hidden="true"' in label


# --------------------------------------------------------------------------- #
# 2. no second blank option
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("adapter", ADAPTERS, ids=ADAPTER_IDS)
class TestBlankOption:
    def test_a_blank_choice_is_not_doubled(self, adapter):
        html = _render(adapter, "kind")
        assert html.count('<option value=""') == 1
        assert '<option value="" selected>Pick a kind</option>' in html

    def test_an_optional_field_without_one_still_gets_one(self, adapter):
        html = _render(adapter, "colour")
        assert html.count('<option value=""') == 1
        assert "---------" in html

    def test_model_choice_empty_label_is_not_doubled(self, adapter):
        field = forms.ChoiceField(choices=[("", "---------"), ("1", "One")], required=False)
        assert adapter.render_field(field, "pick", "", []).count('<option value=""') == 1

    def test_option_groups(self, adapter):
        field = forms.ChoiceField(
            choices=[("Fruit", [("a", "Apple"), ("b", "Banana")]), ("c", "Carrot")]
        )
        html = adapter.render_field(field, "food", "b", [])
        assert '<optgroup label="Fruit"><option value="a">Apple</option>' in html
        assert '<option value="b" selected>Banana</option></optgroup>' in html
        assert '<option value="c">Carrot</option>' in html


# --------------------------------------------------------------------------- #
# 4. RadioSelect / CheckboxSelectMultiple are accessible groups
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("adapter", ADAPTERS, ids=ADAPTER_IDS)
class TestRadioGroup:
    def test_fieldset_radiogroup_named_by_its_legend(self, adapter):
        html = _render(adapter, "size", "m")
        group = _tag(html, r"<fieldset")
        assert 'id="id_size"' in group
        assert 'role="radiogroup"' in group
        assert 'aria-labelledby="id_size-legend"' in group
        assert 'aria-required="true"' in group
        assert 'aria-describedby="id_size-help"' in group
        assert re.search(r'<legend id="id_size-legend"[^>]*>Size', html)
        assert "<label" not in html.split("<fieldset")[0], "no stray label for a group"

    def test_one_radio_per_choice_each_with_its_label(self, adapter):
        html = _render(adapter, "size", "m")
        radios = re.findall(r'<input type="radio"[^>]*>', html)
        assert len(radios) == 3
        for index, (radio, label) in enumerate(zip(radios, ["Small", "Medium size", "Large"])):
            assert f'id="id_size_{index}"' in radio
            assert 'name="size"' in radio
            assert 'dj-change="validate_field"' in radio
            assert 'required="required"' in radio
            assert re.search(rf'<label[^>]*for="id_size_{index}"[^>]*>{label}</label>', html)
        assert "checked" in radios[1] and "checked" not in radios[0] + radios[2]

    def test_ids_are_valid_whatever_the_choice_value(self, adapter):
        field = forms.ChoiceField(
            choices=[("two words", "Two"), ('q"x', "Quote")], widget=forms.RadioSelect
        )
        html = adapter.render_field(field, "pick", "", [])
        ids = re.findall(r' id="([^"]*)"', html)
        assert ids == ["id_pick", "id_pick-legend", "id_pick_0", "id_pick_1"]
        assert all(re.fullmatch(r"[A-Za-z][\w-]*", i) for i in ids)
        assert 'value="two words"' in html and 'value="q&quot;x"' in html

    def test_errors_invalidate_and_describe_the_group(self, adapter):
        html = _render(adapter, "size", errors=["Pick a size."])
        group = _tag(html, r"<fieldset")
        assert 'aria-describedby="id_size-help id_size-error"' in group
        assert 'aria-invalid="true"' in group
        assert 'id="id_size-error"' in html

    def test_hook_classes_and_segmented_variant(self, adapter):
        html = _render(adapter, "size", group_variant="segmented")
        group = _tag(html, r"<fieldset")
        assert re.search(r'class="dj-radio-group dj-radio-group--segmented[" ]', group)
        assert html.count('class="dj-radio-option') == 3
        # input before its label: a theme can style ``input:checked + label``
        assert re.search(r'<input type="radio"[^>]*/><label', html)

    def test_unsafe_variant_is_ignored(self, adapter):
        group = _tag(_render(adapter, "size", group_variant='x" onclick="y'), r"<fieldset")
        assert "onclick" not in group and "--" not in group

    def test_no_label_names_the_group_with_aria_label(self, adapter):
        html = _render(adapter, "size", render_label=False)
        assert "<legend" not in html
        assert 'aria-label="Size"' in _tag(html, r"<fieldset")

    def test_no_auto_validate_means_no_binding(self, adapter):
        assert "dj-change" not in _render(adapter, "size", auto_validate=False)


@pytest.mark.parametrize("adapter", ADAPTERS, ids=ADAPTER_IDS)
class TestCheckboxGroup:
    def test_fieldset_of_checkboxes(self, adapter):
        html = _render(adapter, "toppings", ["y"])
        group = _tag(html, r"<fieldset")
        assert 'class="dj-checkbox-group' in group
        assert "role=" not in group
        assert 'aria-labelledby="id_toppings-legend"' in group
        boxes = re.findall(r'<input type="checkbox"[^>]*>', html)
        assert len(boxes) == 2
        assert 'id="id_toppings_0"' in boxes[0] and 'id="id_toppings_1"' in boxes[1]
        assert "checked" in boxes[1] and "checked" not in boxes[0]
        # ``required`` on a checkbox group would demand every box; a checkbox
        # change sends the checked state, not the list of values, so no binding.
        assert all("required" not in b and "dj-change" not in b for b in boxes)
        assert "<select" not in html


# --------------------------------------------------------------------------- #
# 5. themeable classes
# --------------------------------------------------------------------------- #

THEME = {
    "wrapper": "t-field",
    "label": "t-label",
    "input": "t-input",
    "input_invalid": "t-input t-input--bad",
    "textarea": "t-textarea",
    "select": "t-select",
    "help": "t-help",
    "error": "t-error",
    "legend": "t-legend",
    "radio_group": "t-segmented",
    "radio_option": "t-option",
    "radio_input": "t-radio",
    "radio_label": "t-option-label",
}


class TestThemeClasses:
    def test_every_documented_slot_is_known(self):
        assert set(THEME) <= set(FORM_CLASS_SLOTS)

    @pytest.mark.parametrize("adapter", ADAPTERS, ids=ADAPTER_IDS)
    def test_liveview_config_form_classes(self, adapter):
        with override_settings(LIVEVIEW_CONFIG={"form_classes": THEME}):
            text = _render(adapter, "name", errors=["Bad."])
            assert '<div class="t-field">' in text
            assert 'class="t-label"' in text
            assert 'class="t-input t-input--bad"' in text
            assert 'class="t-help"' in text
            assert 'class="t-error"' in text
            assert 'class="t-input"' in _tag(_render(adapter, "name"), r"<input")
            assert 'class="t-textarea"' in _tag(_render(adapter, "notes"), r"<textarea")
            assert 'class="t-select"' in _tag(_render(adapter, "kind"), r"<select")
            radios = _render(adapter, "size")
            assert 'class="dj-radio-group t-segmented"' in _tag(radios, r"<fieldset")
            assert 'class="t-legend"' in radios
            assert radios.count('class="dj-radio-option t-option"') == 3
            assert radios.count('class="t-radio"') == 3
            assert radios.count('class="t-option-label"') == 3

    def test_empty_string_removes_a_class(self):
        with override_settings(LIVEVIEW_CONFIG={"form_classes": {"wrapper": "", "input": ""}}):
            html = _render(Bootstrap5Adapter(), "name")
        assert html.startswith("<div><label")
        assert "class=" not in _tag(html, r"<input")

    def test_unset_slots_keep_the_framework_classes(self):
        with override_settings(LIVEVIEW_CONFIG={"form_classes": {"label": "t-label"}}):
            html = _render(Bootstrap5Adapter(), "name")
        assert 'class="t-label"' in html and 'class="form-control"' in html

    def test_subclass_css_classes(self):
        class BrandAdapter(Bootstrap5Adapter):
            css_classes = {"input": "brand-input", "radio_group": "brand-segmented"}

        adapter = BrandAdapter()
        assert 'class="brand-input"' in _tag(_render(adapter, "name"), r"<input")
        assert "brand-segmented" in _tag(_render(adapter, "size"), r"<fieldset")
        # everything else is Bootstrap 5's
        assert 'class="form-label"' in _render(adapter, "name")

    def test_subclass_wins_over_config(self):
        class BrandAdapter(Bootstrap5Adapter):
            css_classes = {"input": "brand-input"}

        with override_settings(LIVEVIEW_CONFIG={"form_classes": {"input": "cfg-input"}}):
            assert 'class="brand-input"' in _tag(_render(BrandAdapter(), "name"), r"<input")

    def test_an_explicit_framework_reads_its_own_section(self):
        # css_framework is bootstrap5 by default; framework="tailwind" gets Tailwind's classes.
        assert "rounded-md" in _tag(_render(get_adapter("tailwind"), "name"), r"<input")

    def test_registered_adapter_is_used_by_as_live_field(self):
        class BrandAdapter(PlainAdapter):
            css_classes = {"input": "brand-input"}

        register_adapter("brand-test", BrandAdapter())
        view = _mounted()
        assert 'class="brand-input"' in str(view.as_live_field("name", framework="brand-test"))


# --------------------------------------------------------------------------- #
# round trip: a radio group through validate_field and submit_form
# --------------------------------------------------------------------------- #


class OrderView(FormMixin, LiveView):
    exposure_policy = "legacy"
    form_class = OrderForm
    template = (
        '{% load live_tags %}<div dj-root><form dj-submit="submit_form">'
        '{% live_field view "size" %}{% live_field view "name" %}</form></div>'
    )
    submitted = None

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["view"] = self
        return context

    def form_valid(self, form):
        self.submitted = form.cleaned_data["size"]


def _mounted() -> OrderView:
    view = OrderView()
    view.mount(None)
    return view


def _field(view: OrderView, name: str) -> str:
    body = '{% load live_tags %}{% live_field view "' + name + '" %}'
    return engines["django"].from_string(body).render({"view": view})


class TestRadioRoundTrip:
    def test_validate_field_with_the_payload_a_radio_change_sends(self):
        # 09-event-binding.js ``buildFormEventParams``: {value: the checked
        # radio's value, field: its name}.
        view = _mounted()
        view.validate_field(field="size", value="l")
        assert view.form_data["size"] == "l"
        assert "size" not in view.field_errors
        html = _field(view, "size")
        radios = re.findall(r'<input type="radio"[^>]*>', html)
        assert "checked" in radios[2] and "checked" not in radios[0] + radios[1]
        assert "aria-invalid" not in _tag(html, r"<fieldset")

    def test_an_invalid_choice_is_reported_on_the_group(self):
        view = _mounted()
        view.validate_field(field="size", value="xl")
        assert "size" in view.field_errors
        html = _field(view, "size")
        group = _tag(html, r"<fieldset")
        assert 'aria-invalid="true"' in group
        assert 'aria-describedby="id_size-help id_size-error"' in group
        assert re.search(r'id="id_size-error"[^>]*>.*Select a valid choice', html)
        # a valid change clears it
        view.validate_field(field="size", value="s")
        assert "size" not in view.field_errors
        assert "aria-invalid" not in _tag(_field(view, "size"), r"<fieldset")

    def test_submit_form_with_the_form_data_payload(self):
        # dj-submit sends ``Object.fromEntries(new FormData(form))``: the
        # checked radio's name/value, like any other field (the native HTTP
        # fallback posts the same params).
        view = _mounted()
        view.submit_form(name="Ada", size="m", agree="on")
        assert view.submitted == "m"
        assert 'value="m" class="form-check-input" checked' in _field(view, "size")

    @pytest.mark.django_db
    def test_through_the_test_client(self):
        client = LiveViewTestClient(OrderView)
        client.mount()
        client.send_event("validate_field", field="size", value="m")
        html = client.render()
        assert re.search(r'<input type="radio"[^>]*id="id_size_1"[^>]*checked', html)
        client.send_event("validate_field", field="size", value="nope")
        assert "size" in client.view_instance.field_errors
