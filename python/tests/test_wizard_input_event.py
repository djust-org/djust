"""Regression tests for #1095: WizardMixin.as_live_field() must support
configurable DOM event for real-time validation binding.

Default behavior (``dj-change``) preserves existing semantics. The new
``wizard_input_event`` class attribute and per-call ``dom_event`` kwarg
let wizard authors opt into ``dj-input`` (fires on every keystroke) so
edits to pre-filled fields aren't lost when the user clicks Next without
blurring.
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

from djust.frameworks import PlainAdapter


class _Form(forms.Form):
    name = forms.CharField(max_length=100)
    bio = forms.CharField(widget=forms.Textarea)
    role = forms.ChoiceField(choices=[("a", "A"), ("b", "B")])
    agree = forms.BooleanField(required=False)


class DefaultEventBindingTest(TestCase):
    """Without ``dom_event`` kwarg, adapter emits ``dj-change``."""

    def setUp(self):
        self.adapter = PlainAdapter()
        self.form = _Form()

    def test_text_input_default_dj_change(self):
        html = self.adapter.render_field(
            self.form.fields["name"], "name", "", [], event_name="validate_field"
        )
        self.assertIn('dj-change="validate_field"', html)
        self.assertNotIn("dj-input", html)

    def test_textarea_default_dj_change(self):
        html = self.adapter.render_field(
            self.form.fields["bio"], "bio", "", [], event_name="validate_field"
        )
        self.assertIn('dj-change="validate_field"', html)

    def test_select_default_dj_change(self):
        html = self.adapter.render_field(
            self.form.fields["role"], "role", "", [], event_name="validate_field"
        )
        self.assertIn('dj-change="validate_field"', html)

    def test_checkbox_default_dj_change(self):
        html = self.adapter.render_field(
            self.form.fields["agree"], "agree", False, [], event_name="validate_field"
        )
        self.assertIn('dj-change="validate_field"', html)


class DomEventOverrideTest(TestCase):
    """Passing ``dom_event="dj-input"`` swaps the binding."""

    def setUp(self):
        self.adapter = PlainAdapter()
        self.form = _Form()

    def test_text_input_dj_input(self):
        html = self.adapter.render_field(
            self.form.fields["name"],
            "name",
            "",
            [],
            event_name="validate_field",
            dom_event="dj-input",
        )
        self.assertIn('dj-input="validate_field"', html)
        self.assertNotIn("dj-change", html)

    def test_textarea_dj_input(self):
        html = self.adapter.render_field(
            self.form.fields["bio"],
            "bio",
            "",
            [],
            event_name="validate_field",
            dom_event="dj-input",
        )
        self.assertIn('dj-input="validate_field"', html)
        self.assertNotIn("dj-change", html)

    def test_select_dj_input_works_too(self):
        # dj-input on select is unusual but the API shouldn't reject it —
        # the developer made the choice explicitly.
        html = self.adapter.render_field(
            self.form.fields["role"],
            "role",
            "",
            [],
            event_name="validate_field",
            dom_event="dj-input",
        )
        self.assertIn('dj-input="validate_field"', html)


class WizardMixinClassAttributeTest(TestCase):
    """``wizard_input_event`` class attribute flows through to as_live_field()."""

    def setUp(self):
        from djust.wizard import WizardMixin

        # Build a minimal subclass that exercises as_live_field without the
        # full LiveView WebSocket lifecycle.
        class _W(WizardMixin):
            wizard_steps = [{"name": "step1", "form_class": _Form, "title": "S1"}]

            def __init__(self, input_event="dj-change"):
                self.wizard_input_event = input_event
                self.wizard_step_index = 0
                self.wizard_step_data = {}
                self.wizard_step_errors = {}

        self._W = _W

    def test_default_class_attribute_is_dj_change(self):
        from djust.wizard import WizardMixin

        self.assertEqual(WizardMixin.wizard_input_event, "dj-change")

    def test_default_renders_dj_change(self):
        view = self._W(input_event="dj-change")
        html = view.as_live_field("name")
        self.assertIn("dj-change=", html)
        self.assertNotIn("dj-input=", html)

    def test_class_attr_dj_input_renders_dj_input(self):
        view = self._W(input_event="dj-input")
        html = view.as_live_field("name")
        self.assertIn('dj-input="validate_field"', html)
        self.assertNotIn('dj-change="validate_field"', html)

    def test_per_call_kwarg_overrides_class_attr(self):
        view = self._W(input_event="dj-change")  # class default
        html = view.as_live_field("name", dom_event="dj-input")
        self.assertIn('dj-input="validate_field"', html)
        self.assertNotIn('dj-change="validate_field"', html)

    def test_dom_event_none_coalesces_to_class_attr(self):
        # Caller passing dom_event=None must NOT produce attrs[None]; the
        # class attribute should fill in instead.
        view = self._W(input_event="dj-input")
        html = view.as_live_field("name", dom_event=None)
        self.assertIn('dj-input="validate_field"', html)
        self.assertNotIn("None=", html)


class RadioFieldTest(TestCase):
    """RadioSelect widget honors the dom_event kwarg (frameworks.py:345 site)."""

    def setUp(self):
        class _RadioForm(forms.Form):
            color = forms.ChoiceField(
                choices=[("r", "Red"), ("g", "Green")],
                widget=forms.RadioSelect,
            )

        self.form = _RadioForm()
        self.adapter = PlainAdapter()

    def test_radio_default_dj_change(self):
        html = self.adapter.render_field(
            self.form.fields["color"], "color", "", [], event_name="validate_field"
        )
        self.assertIn('dj-change="validate_field"', html)
        self.assertNotIn("dj-input", html)

    def test_radio_dom_event_dj_input(self):
        html = self.adapter.render_field(
            self.form.fields["color"],
            "color",
            "",
            [],
            event_name="validate_field",
            dom_event="dj-input",
        )
        self.assertIn('dj-input="validate_field"', html)
        self.assertNotIn('dj-change="validate_field"', html)
