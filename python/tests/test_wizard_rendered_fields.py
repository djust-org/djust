"""Regression tests for #1097: WizardMixin.wizard_rendered_fields opt-in.

When a step's form has many fields but the template only references a subset
(e.g. conditional fields hidden behind a flag), `wizard_rendered_fields` lets
the wizard author skip `field_html` generation for the unused fields. Default
(`None`) preserves existing behavior — render all.
"""

import django
from django.conf import settings

if not settings.configured:
    settings.configure(
        INSTALLED_APPS=["django.contrib.contenttypes", "django.contrib.auth"],
        DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
    )
    django.setup()

from django import forms
from django.test import TestCase

from djust.wizard import WizardMixin


class _BigForm(forms.Form):
    """Form with several fields — only some are 'visible' in template."""

    first_name = forms.CharField(max_length=100)
    last_name = forms.CharField(max_length=100)
    email = forms.EmailField()
    phone = forms.CharField(max_length=20, required=False)
    is_vehicle_owner = forms.ChoiceField(choices=[("yes", "Yes"), ("no", "No")])
    # Conditional fields only shown when is_vehicle_owner == "no"
    owner_first_name = forms.CharField(max_length=100, required=False)
    owner_last_name = forms.CharField(max_length=100, required=False)


class _StubLiveView:
    """Minimal stub providing get_context_data for WizardMixin to super() into."""

    def get_context_data(self, **kwargs):
        return dict(kwargs)


class _W(WizardMixin, _StubLiveView):
    wizard_steps = [
        {"name": "details", "form_class": _BigForm, "title": "Details"},
    ]

    def __init__(self, rendered=None, per_step_rendered=None):
        if rendered is not None:
            self.wizard_rendered_fields = rendered
        # Optional per-step override
        if per_step_rendered is not None:
            type(self).wizard_steps = [
                {
                    "name": "details",
                    "form_class": _BigForm,
                    "title": "Details",
                    "rendered_fields": per_step_rendered,
                },
            ]
        else:
            type(self).wizard_steps = [
                {"name": "details", "form_class": _BigForm, "title": "Details"},
            ]
        self.wizard_step_index = 0
        self.wizard_step_data = {}
        self.wizard_step_errors = {}
        self.wizard_completed_steps = []


class DefaultRendersAllFieldsTest(TestCase):
    """Without `wizard_rendered_fields`, legacy behavior renders every field."""

    def test_field_html_keys_match_form_fields(self):
        view = _W()
        ctx = view.get_context_data()
        self.assertEqual(
            set(ctx["field_html"].keys()),
            {
                "first_name",
                "last_name",
                "email",
                "phone",
                "is_vehicle_owner",
                "owner_first_name",
                "owner_last_name",
            },
        )

    def test_default_class_attribute_is_none(self):
        self.assertIsNone(WizardMixin.wizard_rendered_fields)


class ClassAttributeFiltersTest(TestCase):
    """A class-level list filters `field_html` to that subset."""

    def test_only_listed_fields_render(self):
        view = _W(rendered=["first_name", "last_name", "email"])
        ctx = view.get_context_data()
        self.assertEqual(
            set(ctx["field_html"].keys()),
            {"first_name", "last_name", "email"},
        )
        # Excluded fields should NOT have field_html entries
        self.assertNotIn("phone", ctx["field_html"])
        self.assertNotIn("owner_first_name", ctx["field_html"])

    def test_form_data_still_contains_all_fields(self):
        # form_data, form_required, form_choices are NOT filtered — only
        # field_html (which is the expensive HTML rendering). Non-rendered
        # fields are still part of validation/state.
        view = _W(rendered=["first_name"])
        ctx = view.get_context_data()
        self.assertIn("first_name", ctx["form_data"])
        self.assertIn("last_name", ctx["form_data"])  # all fields tracked
        self.assertIn("phone", ctx["form_data"])

    def test_empty_list_renders_no_fields(self):
        view = _W(rendered=[])
        ctx = view.get_context_data()
        self.assertEqual(ctx["field_html"], {})

    def test_unknown_field_in_list_silently_ignored(self):
        # If the developer lists a name that's not in the form, just no-op.
        view = _W(rendered=["first_name", "nonexistent_field"])
        ctx = view.get_context_data()
        self.assertIn("first_name", ctx["field_html"])
        self.assertNotIn("nonexistent_field", ctx["field_html"])


class PerStepOverrideTest(TestCase):
    """A step's `"rendered_fields"` key overrides the class-level default."""

    def test_per_step_overrides_class_attr(self):
        # Class attr says render only first_name, but step dict says
        # render only email — step-level wins.
        view = _W(rendered=["first_name"], per_step_rendered=["email"])
        ctx = view.get_context_data()
        self.assertEqual(set(ctx["field_html"].keys()), {"email"})

    def test_per_step_with_no_class_attr(self):
        view = _W(per_step_rendered=["first_name", "last_name"])
        ctx = view.get_context_data()
        self.assertEqual(set(ctx["field_html"].keys()), {"first_name", "last_name"})
