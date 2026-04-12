"""Regression tests for #683: as_live_field() must honour widget.attrs.

The ``as_live_field()`` method (and by extension the ``{% live_field %}`` tag)
renders a Django form field for live binding.  Before the fix, any attributes
defined on the field's *widget* — ``type``, ``placeholder``, ``pattern``,
``min``/``max``, custom ``data-*`` — were silently dropped.

These tests exercise the ``BaseAdapter`` rendering helpers directly to verify
that ``field.widget.attrs`` are merged into the output HTML, and that
djust-specific attributes (``dj-change``, ``name``, ``class``, etc.) always
take precedence over widget defaults when there is a clash.
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

from djust.frameworks import Bootstrap5Adapter, PlainAdapter


class EmailWidgetAttrsTest(TestCase):
    """Widget attrs on an EmailInput are rendered in as_live_field output."""

    def setUp(self):
        class EmailForm(forms.Form):
            email = forms.EmailField(
                widget=forms.EmailInput(
                    attrs={
                        "type": "email",
                        "placeholder": "you@example.com",
                    }
                )
            )

        self.field = EmailForm().fields["email"]
        self.adapter = Bootstrap5Adapter()

    def test_placeholder_rendered(self):
        html = self.adapter.render_field(self.field, "email", "", [])
        self.assertIn('placeholder="you@example.com"', html)

    def test_type_email_rendered(self):
        """The widget's type=email should appear (field type detection also
        produces 'email', but the widget attr must not be dropped)."""
        html = self.adapter.render_field(self.field, "email", "", [])
        self.assertIn('type="email"', html)


class PatternMinMaxAttrsTest(TestCase):
    """pattern, min, max widget attrs are preserved."""

    def setUp(self):
        class CodeForm(forms.Form):
            code = forms.CharField(
                widget=forms.TextInput(
                    attrs={
                        "pattern": "[A-Z]{3}-\\d{4}",
                        "title": "Format: ABC-1234",
                    }
                )
            )
            age = forms.IntegerField(
                widget=forms.NumberInput(
                    attrs={
                        "min": "0",
                        "max": "150",
                        "step": "1",
                    }
                )
            )

        self.form = CodeForm()
        self.adapter = Bootstrap5Adapter()

    def test_pattern_rendered(self):
        html = self.adapter.render_field(self.form.fields["code"], "code", "", [])
        self.assertIn("pattern=", html)
        self.assertIn("[A-Z]{3}", html)

    def test_title_rendered(self):
        html = self.adapter.render_field(self.form.fields["code"], "code", "", [])
        self.assertIn('title="Format: ABC-1234"', html)

    def test_min_max_rendered(self):
        html = self.adapter.render_field(self.form.fields["age"], "age", "", [])
        self.assertIn('min="0"', html)
        self.assertIn('max="150"', html)

    def test_step_rendered(self):
        html = self.adapter.render_field(self.form.fields["age"], "age", "", [])
        self.assertIn('step="1"', html)


class DjustAttrsOverrideWidgetTest(TestCase):
    """djust-specific attrs (dj-change, name, class) must NOT be overridden
    by widget.attrs if there is a key clash."""

    def setUp(self):
        class ClashForm(forms.Form):
            username = forms.CharField(
                widget=forms.TextInput(
                    attrs={
                        "name": "widget-name-should-lose",
                        "class": "widget-class-should-lose",
                        "dj-change": "widget-handler-should-lose",
                    }
                )
            )

        self.field = ClashForm().fields["username"]
        self.adapter = Bootstrap5Adapter()

    def test_name_not_overridden(self):
        html = self.adapter.render_field(self.field, "username", "", [])
        self.assertIn('name="username"', html)
        self.assertNotIn("widget-name-should-lose", html)

    def test_class_not_overridden(self):
        html = self.adapter.render_field(self.field, "username", "", [])
        # Bootstrap adapter sets its own class; widget class must not leak
        self.assertNotIn("widget-class-should-lose", html)

    def test_dj_change_not_overridden(self):
        html = self.adapter.render_field(self.field, "username", "", [])
        self.assertIn('dj-change="validate_field"', html)
        self.assertNotIn("widget-handler-should-lose", html)


class EmptyWidgetAttrsTest(TestCase):
    """Empty widget.attrs must not break rendering."""

    def test_empty_attrs_dict(self):
        class EmptyForm(forms.Form):
            name = forms.CharField(widget=forms.TextInput(attrs={}))

        adapter = PlainAdapter()
        html = adapter.render_field(EmptyForm().fields["name"], "name", "", [])
        self.assertIn('name="name"', html)
        self.assertIn('type="text"', html)

    def test_default_widget_no_attrs(self):
        """A plain CharField with no explicit widget attrs."""

        class DefaultForm(forms.Form):
            name = forms.CharField()

        adapter = PlainAdapter()
        html = adapter.render_field(DefaultForm().fields["name"], "name", "", [])
        self.assertIn('name="name"', html)
        self.assertIn('type="text"', html)


class TextareaWidgetAttrsTest(TestCase):
    """Widget attrs on a Textarea are merged."""

    def test_textarea_rows_cols(self):
        class NoteForm(forms.Form):
            body = forms.CharField(
                widget=forms.Textarea(attrs={"rows": "10", "cols": "40", "placeholder": "Write..."})
            )

        adapter = Bootstrap5Adapter()
        html = adapter.render_field(NoteForm().fields["body"], "body", "", [])
        self.assertIn('rows="10"', html)
        self.assertIn('cols="40"', html)
        self.assertIn('placeholder="Write..."', html)


class CheckboxWidgetAttrsTest(TestCase):
    """Widget attrs on a CheckboxInput are merged."""

    def test_data_attr_on_checkbox(self):
        class ToggleForm(forms.Form):
            agree = forms.BooleanField(widget=forms.CheckboxInput(attrs={"data-toggle": "tooltip"}))

        adapter = Bootstrap5Adapter()
        html = adapter.render_field(ToggleForm().fields["agree"], "agree", False, [])
        self.assertIn('data-toggle="tooltip"', html)


class RadioWidgetAttrsTest(TestCase):
    """Widget attrs on a RadioSelect are merged into each radio input."""

    def test_data_attr_on_radio(self):
        class ColorForm(forms.Form):
            color = forms.ChoiceField(
                choices=[("r", "Red"), ("g", "Green")],
                widget=forms.RadioSelect(attrs={"data-group": "colors"}),
            )

        adapter = Bootstrap5Adapter()
        html = adapter.render_field(ColorForm().fields["color"], "color", "r", [])
        # Each radio input should carry the widget attr
        self.assertEqual(html.count('data-group="colors"'), 2)


class SelectWidgetAttrsTest(TestCase):
    """Widget attrs on a Select are merged."""

    def test_data_attr_on_select(self):
        class SizeForm(forms.Form):
            size = forms.ChoiceField(
                choices=[("s", "Small"), ("m", "Medium")],
                widget=forms.Select(attrs={"data-live-search": "true"}),
            )

        adapter = Bootstrap5Adapter()
        html = adapter.render_field(SizeForm().fields["size"], "size", "s", [])
        self.assertIn('data-live-search="true"', html)


class BooleanWidgetAttrTest(TestCase):
    """Boolean True/False values in widget.attrs are handled."""

    def test_boolean_true_attr(self):
        class Form1(forms.Form):
            f = forms.CharField(widget=forms.TextInput(attrs={"autofocus": True}))

        adapter = PlainAdapter()
        html = adapter.render_field(Form1().fields["f"], "f", "", [])
        # _build_tag renders True as key="key"
        self.assertIn("autofocus=", html)

    def test_boolean_false_attr_omitted(self):
        class Form2(forms.Form):
            f = forms.CharField(widget=forms.TextInput(attrs={"disabled": False}))

        adapter = PlainAdapter()
        html = adapter.render_field(Form2().fields["f"], "f", "", [])
        self.assertNotIn("disabled", html)
