"""Tests for the v0.5.1 inputs_for template tag + FormSetHelpersMixin."""

from __future__ import annotations

import pytest
from django import forms
from django.forms import formset_factory
from django.template import Context, Template

from djust.formsets import FormSetHelpersMixin, add_row, remove_row
from djust.live_view import LiveView


class AddressForm(forms.Form):
    street = forms.CharField()
    city = forms.CharField()


AddressFormSet = formset_factory(AddressForm, extra=1)


# ─────────────────────────────────────────────────────────────────────────────
# {% inputs_for %} template tag
# ─────────────────────────────────────────────────────────────────────────────


def _render(src: str, ctx: dict) -> str:
    return Template(src).render(Context(ctx))


def test_inputs_for_iterates_formset_with_prefix():
    formset = AddressFormSet(
        initial=[{"street": "Main", "city": "NY"}, {"street": "Oak", "city": "LA"}]
    )
    src = (
        "{% load djust_formsets %}"
        "{% inputs_for formset as form %}"
        "[{{ form.prefix }}]"
        "{% endinputs_for %}"
    )
    # initial= creates an unbound formset — iterate it to verify prefix wiring.
    out = _render(src, {"formset": formset})
    assert "[form-0]" in out
    assert "[form-1]" in out


def test_inputs_for_exposes_loop_metadata():
    formset = AddressFormSet(initial=[{"street": "A"}, {"street": "B"}, {"street": "C"}])
    src = (
        "{% load djust_formsets %}"
        "{% inputs_for formset as f %}"
        "{{ inputs_for_loop.counter }}/{{ inputs_for_loop.counter0 }}|"
        "{% endinputs_for %}"
    )
    out = _render(src, {"formset": formset})
    # 3 forms + the extra=1 empty one = 4 iterations.
    assert "1/0|" in out
    assert "2/1|" in out


def test_inputs_for_handles_none_formset_gracefully():
    src = "{% load djust_formsets %}{% inputs_for formset as form %}x{% endinputs_for %}"
    out = _render(src, {"formset": None})
    assert out == ""


def test_inputs_for_requires_as_clause():
    from django.template import TemplateSyntaxError

    with pytest.raises(TemplateSyntaxError, match="requires the form"):
        Template("{% load djust_formsets %}{% inputs_for formset %}{% endinputs_for %}")


def test_inputs_for_rejects_non_as_keyword():
    from django.template import TemplateSyntaxError

    with pytest.raises(TemplateSyntaxError, match="expected 'as'"):
        Template("{% load djust_formsets %}{% inputs_for formset into form %}{% endinputs_for %}")


# ─────────────────────────────────────────────────────────────────────────────
# add_row helper
# ─────────────────────────────────────────────────────────────────────────────


def test_add_row_increments_total_forms():
    data = {
        "form-TOTAL_FORMS": "1",  # extra=1 default
        "form-INITIAL_FORMS": "0",
        "form-MIN_NUM_FORMS": "0",
        "form-MAX_NUM_FORMS": "1000",
    }
    updated = add_row(AddressFormSet, data=data)
    assert updated.data["form-TOTAL_FORMS"] == "2"


def test_add_row_caps_at_absolute_max():
    data = {
        "form-TOTAL_FORMS": "2000",
        "form-INITIAL_FORMS": "0",
        "form-MIN_NUM_FORMS": "0",
        "form-MAX_NUM_FORMS": "2000",
    }
    updated = add_row(AddressFormSet, data=data)
    assert updated.data["form-TOTAL_FORMS"] == "2000"  # capped, no increment


def test_add_row_preserves_existing_row_data():
    data = {
        "form-TOTAL_FORMS": "1",
        "form-INITIAL_FORMS": "0",
        "form-MIN_NUM_FORMS": "0",
        "form-MAX_NUM_FORMS": "1000",
        "form-0-street": "Keep me",
        "form-0-city": "Seattle",
    }
    updated = add_row(AddressFormSet, data=data)
    assert updated.data["form-TOTAL_FORMS"] == "2"
    assert updated.data["form-0-street"] == "Keep me"


# ─────────────────────────────────────────────────────────────────────────────
# remove_row helper
# ─────────────────────────────────────────────────────────────────────────────


def test_remove_row_marks_delete_flag():
    data = {
        "form-TOTAL_FORMS": "2",
        "form-INITIAL_FORMS": "0",
        "form-MIN_NUM_FORMS": "0",
        "form-MAX_NUM_FORMS": "1000",
        "form-0-street": "Keep",
        "form-0-city": "Keep",
        "form-1-street": "Drop",
        "form-1-city": "Drop",
    }
    updated = remove_row(AddressFormSet, row_prefix="form-1", data=data)
    assert updated.data["form-1-DELETE"] == "on"
    # Existing rows still present
    assert updated.data["form-0-street"] == "Keep"
    assert updated.data["form-1-street"] == "Drop"


def test_add_row_honors_custom_prefix():
    """Custom formset prefix (addresses-*) must route writes to the right keys."""
    data = {
        "addresses-TOTAL_FORMS": "1",
        "addresses-INITIAL_FORMS": "0",
        "addresses-0-street": "stored",
    }
    updated = add_row(AddressFormSet, data=data, prefix="addresses")
    assert updated.data["addresses-TOTAL_FORMS"] == "2"
    assert updated.data["addresses-0-street"] == "stored"
    # Default 'form-*' keys must NOT have been written.
    assert "form-TOTAL_FORMS" not in updated.data


def test_add_row_caps_at_max_num_when_set():
    CappedFormSet = formset_factory(AddressForm, extra=1, max_num=3)
    data = {
        "form-TOTAL_FORMS": "3",
        "form-INITIAL_FORMS": "0",
        "form-MIN_NUM_FORMS": "0",
        "form-MAX_NUM_FORMS": "3",
    }
    updated = add_row(CappedFormSet, data=data)
    # max_num=3 is the real cap; no row added.
    assert updated.data["form-TOTAL_FORMS"] == "3"


# ─────────────────────────────────────────────────────────────────────────────
# FormSetHelpersMixin
# ─────────────────────────────────────────────────────────────────────────────


class _View(FormSetHelpersMixin, LiveView):
    formset_classes = {"addresses": AddressFormSet}

    def mount(self, request=None, **kwargs):
        self._formset_data = {}
        self.addresses = AddressFormSet()


def test_mixin_add_row_bumps_total_forms():
    v = _View()
    v.mount()
    v.add_row(formset="addresses")
    # Mixin uses the formset NAME as the prefix, so keys live under addresses-*
    assert v.addresses.data["addresses-TOTAL_FORMS"] == "1"


def test_mixin_remove_row_marks_delete():
    v = _View()
    v.mount()
    # Seed data so there's something to remove
    v._formset_data = {
        "addresses-TOTAL_FORMS": "1",
        "addresses-INITIAL_FORMS": "0",
        "addresses-0-street": "s",
    }
    v.remove_row(formset="addresses", prefix="addresses-0")
    assert v.addresses.data["addresses-0-DELETE"] == "on"


def test_mixin_raises_when_formset_data_not_initialized():
    """If mount() forgets to set self._formset_data, add/remove fail loud."""

    class NoInitView(FormSetHelpersMixin, LiveView):
        formset_classes = {"addresses": AddressFormSet}

        def mount(self, request=None, **kwargs):
            # Intentionally NOT initializing self._formset_data
            self.addresses = AddressFormSet(prefix="addresses")

    v = NoInitView()
    v.mount()
    with pytest.raises(RuntimeError, match="did not initialize self._formset_data"):
        v.add_row(formset="addresses")


def test_mixin_rejects_unknown_formset_name():
    v = _View()
    v.mount()
    with pytest.raises(ValueError, match="has no entry for 'widgets'"):
        v.add_row(formset="widgets")


def test_mixin_noops_on_missing_formset_arg():
    v = _View()
    v.mount()
    # Should not raise — event was mis-fired without a formset kwarg
    v.add_row()
    v.remove_row(formset="", prefix="form-0")
    v.remove_row(formset="addresses", prefix="")
