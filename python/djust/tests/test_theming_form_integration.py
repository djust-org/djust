"""
Tests for Phase 6: Form Integration.

Covers ThemeFormRenderer (6.1), widget field mapping (6.2),
{% theme_form %} tag (6.3), and error display (6.4).
"""

import django
from django.conf import settings

if not settings.configured:
    settings.configure(
        INSTALLED_APPS=["django.contrib.contenttypes", "djust.theming"],
        DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
        STATIC_URL="/static/",
        TEMPLATES=[
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "APP_DIRS": True,
                "DIRS": [],
                "OPTIONS": {
                    "context_processors": [],
                },
            }
        ],
    )
    django.setup()

from unittest.mock import patch

import pytest
from django import forms
from django.template import Context, Template

pytestmark = pytest.mark.theming


# ===================================================================
# Helper: create a form with ThemeFormRenderer as its renderer
# ===================================================================


def _make_themed_form(form_class, *args, **kwargs):
    """Instantiate a form and set its renderer to ThemeFormRenderer.

    This simulates what happens when FORM_RENDERER is configured in settings.
    Django sets ``form.renderer`` at construction time from settings.FORM_RENDERER.
    BoundField uses ``form.renderer`` for field.html and widget rendering.
    """
    from djust.theming.forms import ThemeFormRenderer

    form = form_class(*args, **kwargs)
    form.renderer = ThemeFormRenderer()
    return form


# ===================================================================
# Sample forms for testing
# ===================================================================


class SimpleForm(forms.Form):
    """Basic form with common field types."""

    name = forms.CharField(max_length=100, help_text="Your full name")
    email = forms.EmailField()
    password = forms.CharField(widget=forms.PasswordInput)
    message = forms.CharField(widget=forms.Textarea)


class FullForm(forms.Form):
    """Form with all supported widget types."""

    text_field = forms.CharField()
    email_field = forms.EmailField()
    password_field = forms.CharField(widget=forms.PasswordInput)
    number_field = forms.IntegerField()
    url_field = forms.URLField()
    date_field = forms.DateField()
    textarea_field = forms.CharField(widget=forms.Textarea)
    select_field = forms.ChoiceField(choices=[("a", "Option A"), ("b", "Option B")])
    checkbox_field = forms.BooleanField(required=False)
    radio_field = forms.ChoiceField(
        choices=[("x", "Choice X"), ("y", "Choice Y")],
        widget=forms.RadioSelect,
    )
    file_field = forms.FileField(required=False)
    search_field = forms.CharField(widget=forms.TextInput(attrs={"type": "search"}))
    hidden_field = forms.CharField(widget=forms.HiddenInput)


class ErrorForm(forms.Form):
    """Form that will produce validation errors."""

    required_field = forms.CharField(required=True)
    email_field = forms.EmailField()

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("required_field") == "trigger_error":
            raise forms.ValidationError("This is a form-level error.")
        return cleaned


# ===================================================================
# 6.1: ThemeFormRenderer tests
# ===================================================================


class TestThemeFormRenderer:
    """Tests for ThemeFormRenderer (6.1)."""

    def test_renderer_importable(self):
        from djust.theming.forms import ThemeFormRenderer

        assert ThemeFormRenderer is not None

    def test_renderer_is_base_renderer_subclass(self):
        from django.forms.renderers import BaseRenderer
        from djust.theming.forms import ThemeFormRenderer

        assert issubclass(ThemeFormRenderer, BaseRenderer)

    def test_renderer_has_engine(self):
        from djust.theming.forms import ThemeFormRenderer

        renderer = ThemeFormRenderer()
        assert renderer.engine is not None

    def test_renderer_can_get_template(self):
        from djust.theming.forms import ThemeFormRenderer

        renderer = ThemeFormRenderer()
        tmpl = renderer.get_template("django/forms/widgets/input.html")
        assert tmpl is not None

    def test_renderer_resolves_themed_input(self):
        """The renderer should resolve our themed input.html, not Django's default."""
        from djust.theming.forms import ThemeFormRenderer

        renderer = ThemeFormRenderer()
        tmpl = renderer.get_template("django/forms/widgets/input.html")
        content = tmpl.template.source
        assert "theme-input" in content

    def test_renderer_resolves_themed_div(self):
        from djust.theming.forms import ThemeFormRenderer

        renderer = ThemeFormRenderer()
        tmpl = renderer.get_template("django/forms/div.html")
        content = tmpl.template.source
        assert "form-group" in content

    def test_renderer_resolves_themed_field(self):
        from djust.theming.forms import ThemeFormRenderer

        renderer = ThemeFormRenderer()
        tmpl = renderer.get_template("django/forms/field.html")
        content = tmpl.template.source
        assert "theme-label" in content

    @patch.object(settings, "LIVEVIEW_CONFIG", {}, create=True)
    def test_form_renders_with_renderer(self):
        """A Django form should render using ThemeFormRenderer."""
        form = _make_themed_form(SimpleForm)
        html = form.render()
        assert html is not None
        assert len(html) > 0

    @patch.object(settings, "LIVEVIEW_CONFIG", {}, create=True)
    def test_form_render_contains_input_class(self):
        form = _make_themed_form(SimpleForm)
        html = form.render()
        assert "theme-input" in html

    @patch.object(settings, "LIVEVIEW_CONFIG", {}, create=True)
    def test_form_render_contains_textarea_class(self):
        form = _make_themed_form(SimpleForm)
        html = form.render()
        assert "theme-textarea" in html


# ===================================================================
# 6.2: Widget template tests
# ===================================================================


class TestWidgetTemplates:
    """Tests for themed widget templates (6.2)."""

    @patch.object(settings, "LIVEVIEW_CONFIG", {}, create=True)
    def test_text_input_has_theme_class(self):
        from djust.theming.forms import ThemeFormRenderer

        renderer = ThemeFormRenderer()
        widget = forms.TextInput()
        html = widget.render("test", "value", renderer=renderer)
        assert "theme-input" in html

    @patch.object(settings, "LIVEVIEW_CONFIG", {}, create=True)
    def test_email_input_has_theme_class(self):
        from djust.theming.forms import ThemeFormRenderer

        renderer = ThemeFormRenderer()
        widget = forms.EmailInput()
        html = widget.render("email", "test@example.com", renderer=renderer)
        assert "theme-input" in html

    @patch.object(settings, "LIVEVIEW_CONFIG", {}, create=True)
    def test_password_input_has_theme_class(self):
        from djust.theming.forms import ThemeFormRenderer

        renderer = ThemeFormRenderer()
        widget = forms.PasswordInput()
        html = widget.render("pass", "", renderer=renderer)
        assert "theme-input" in html

    @patch.object(settings, "LIVEVIEW_CONFIG", {}, create=True)
    def test_number_input_has_theme_class(self):
        from djust.theming.forms import ThemeFormRenderer

        renderer = ThemeFormRenderer()
        widget = forms.NumberInput()
        html = widget.render("num", "42", renderer=renderer)
        assert "theme-input" in html

    @patch.object(settings, "LIVEVIEW_CONFIG", {}, create=True)
    def test_url_input_has_theme_class(self):
        from djust.theming.forms import ThemeFormRenderer

        renderer = ThemeFormRenderer()
        widget = forms.URLInput()
        html = widget.render("url", "https://example.com", renderer=renderer)
        assert "theme-input" in html

    @patch.object(settings, "LIVEVIEW_CONFIG", {}, create=True)
    def test_date_input_has_theme_class(self):
        from djust.theming.forms import ThemeFormRenderer

        renderer = ThemeFormRenderer()
        widget = forms.DateInput()
        html = widget.render("date", "2024-01-01", renderer=renderer)
        assert "theme-input" in html

    @patch.object(settings, "LIVEVIEW_CONFIG", {}, create=True)
    def test_textarea_has_theme_class(self):
        from djust.theming.forms import ThemeFormRenderer

        renderer = ThemeFormRenderer()
        widget = forms.Textarea()
        html = widget.render("msg", "hello", renderer=renderer)
        assert "theme-textarea" in html

    @patch.object(settings, "LIVEVIEW_CONFIG", {}, create=True)
    def test_select_has_theme_class(self):
        from djust.theming.forms import ThemeFormRenderer

        renderer = ThemeFormRenderer()
        widget = forms.Select(choices=[("a", "A"), ("b", "B")])
        html = widget.render("sel", "a", renderer=renderer)
        assert "theme-select" in html

    @patch.object(settings, "LIVEVIEW_CONFIG", {}, create=True)
    def test_checkbox_has_theme_class(self):
        from djust.theming.forms import ThemeFormRenderer

        renderer = ThemeFormRenderer()
        widget = forms.CheckboxInput()
        html = widget.render("check", True, renderer=renderer)
        assert "theme-checkbox" in html

    @patch.object(settings, "LIVEVIEW_CONFIG", {}, create=True)
    def test_radio_has_theme_class(self):
        from djust.theming.forms import ThemeFormRenderer

        renderer = ThemeFormRenderer()
        widget = forms.RadioSelect(choices=[("a", "A"), ("b", "B")])
        html = widget.render("radio", "a", renderer=renderer)
        assert "theme-radio" in html

    @patch.object(settings, "LIVEVIEW_CONFIG", {}, create=True)
    def test_file_input_has_theme_class(self):
        from djust.theming.forms import ThemeFormRenderer

        renderer = ThemeFormRenderer()
        widget = forms.FileInput()
        html = widget.render("file", None, renderer=renderer)
        assert "theme-file-input" in html

    @patch.object(settings, "LIVEVIEW_CONFIG", {}, create=True)
    def test_hidden_input_no_theme_class(self):
        """Hidden inputs should NOT get themed classes."""
        from djust.theming.forms import ThemeFormRenderer

        renderer = ThemeFormRenderer()
        widget = forms.HiddenInput()
        html = widget.render("hidden", "val", renderer=renderer)
        assert "theme-input" not in html

    @patch.object(settings, "LIVEVIEW_CONFIG", {"theme": {"css_prefix": "dj-"}}, create=True)
    def test_css_prefix_applied_to_input(self):
        from djust.theming.forms import ThemeFormRenderer

        renderer = ThemeFormRenderer()
        widget = forms.TextInput()
        html = widget.render("test", "value", renderer=renderer)
        assert "dj-theme-input" in html

    @patch.object(settings, "LIVEVIEW_CONFIG", {"theme": {"css_prefix": "dj-"}}, create=True)
    def test_css_prefix_applied_to_textarea(self):
        from djust.theming.forms import ThemeFormRenderer

        renderer = ThemeFormRenderer()
        widget = forms.Textarea()
        html = widget.render("msg", "hello", renderer=renderer)
        assert "dj-theme-textarea" in html

    @patch.object(settings, "LIVEVIEW_CONFIG", {"theme": {"css_prefix": "dj-"}}, create=True)
    def test_css_prefix_applied_to_select(self):
        from djust.theming.forms import ThemeFormRenderer

        renderer = ThemeFormRenderer()
        widget = forms.Select(choices=[("a", "A")])
        html = widget.render("sel", "a", renderer=renderer)
        assert "dj-theme-select" in html

    @patch.object(settings, "LIVEVIEW_CONFIG", {"theme": {"css_prefix": "dj-"}}, create=True)
    def test_css_prefix_applied_to_checkbox(self):
        from djust.theming.forms import ThemeFormRenderer

        renderer = ThemeFormRenderer()
        widget = forms.CheckboxInput()
        html = widget.render("check", True, renderer=renderer)
        assert "dj-theme-checkbox" in html


# ===================================================================
# 6.2: Form-level template tests (div.html, field.html)
# ===================================================================


class TestFormLevelTemplates:
    """Tests for themed form-level templates."""

    @patch.object(settings, "LIVEVIEW_CONFIG", {}, create=True)
    def test_form_div_wraps_in_form_group(self):
        form = _make_themed_form(SimpleForm)
        html = form.render()
        assert "theme-form-group" in html

    @patch.object(settings, "LIVEVIEW_CONFIG", {}, create=True)
    def test_field_has_label_class(self):
        form = _make_themed_form(SimpleForm)
        html = form.render()
        assert "theme-label" in html

    @patch.object(settings, "LIVEVIEW_CONFIG", {}, create=True)
    def test_field_help_text_has_class(self):
        form = _make_themed_form(SimpleForm)
        html = form.render()
        assert "theme-help-text" in html

    @patch.object(settings, "LIVEVIEW_CONFIG", {}, create=True)
    def test_hidden_fields_rendered(self):
        form = _make_themed_form(FullForm)
        html = form.render()
        assert 'type="hidden"' in html

    @patch.object(settings, "LIVEVIEW_CONFIG", {}, create=True)
    def test_all_field_types_render(self):
        """FullForm with all widget types should render without errors."""
        form = _make_themed_form(FullForm)
        html = form.render()
        assert "text_field" in html
        assert "email_field" in html
        assert "textarea_field" in html
        assert "select_field" in html
        assert "checkbox_field" in html
        assert "radio_field" in html


# ===================================================================
# 6.3: {% theme_form %} tag tests
# ===================================================================


class TestThemeFormTag:
    """Tests for {% theme_form %} template tag (6.3)."""

    def _render_tag(self, template_str, context_dict=None):
        context_dict = context_dict or {}
        with patch.object(settings, "LIVEVIEW_CONFIG", {}, create=True):
            tmpl = Template(template_str)
            return tmpl.render(Context(context_dict))

    def test_theme_form_tag_loads(self):
        html = self._render_tag(
            "{% load theme_form_tags %}{% theme_form form %}",
            {"form": SimpleForm()},
        )
        assert html is not None

    def test_theme_form_renders_fields(self):
        html = self._render_tag(
            "{% load theme_form_tags %}{% theme_form form %}",
            {"form": SimpleForm()},
        )
        assert "name" in html.lower()
        assert "email" in html.lower()
        assert "password" in html.lower()
        assert "message" in html.lower()

    def test_theme_form_stacked_layout(self):
        html = self._render_tag(
            '{% load theme_form_tags %}{% theme_form form layout="stacked" %}',
            {"form": SimpleForm()},
        )
        assert "theme-form-stacked" in html

    def test_theme_form_horizontal_layout(self):
        html = self._render_tag(
            '{% load theme_form_tags %}{% theme_form form layout="horizontal" %}',
            {"form": SimpleForm()},
        )
        assert "theme-form-horizontal" in html

    def test_theme_form_inline_layout(self):
        html = self._render_tag(
            '{% load theme_form_tags %}{% theme_form form layout="inline" %}',
            {"form": SimpleForm()},
        )
        assert "theme-form-inline" in html

    def test_theme_form_default_layout_is_stacked(self):
        html = self._render_tag(
            "{% load theme_form_tags %}{% theme_form form %}",
            {"form": SimpleForm()},
        )
        assert "theme-form-stacked" in html

    def test_theme_form_renders_labels(self):
        html = self._render_tag(
            "{% load theme_form_tags %}{% theme_form form %}",
            {"form": SimpleForm()},
        )
        assert "<label" in html

    def test_theme_form_renders_help_text(self):
        html = self._render_tag(
            "{% load theme_form_tags %}{% theme_form form %}",
            {"form": SimpleForm()},
        )
        assert "Your full name" in html

    def test_theme_form_renders_widgets(self):
        html = self._render_tag(
            "{% load theme_form_tags %}{% theme_form form %}",
            {"form": SimpleForm()},
        )
        assert "<input" in html
        assert "<textarea" in html

    def test_theme_form_hidden_fields_included(self):
        html = self._render_tag(
            "{% load theme_form_tags %}{% theme_form form %}",
            {"form": FullForm()},
        )
        assert 'type="hidden"' in html

    def test_theme_form_uses_theme_classes(self):
        html = self._render_tag(
            "{% load theme_form_tags %}{% theme_form form %}",
            {"form": SimpleForm()},
        )
        assert "theme-form-field" in html

    def test_theme_form_with_css_prefix(self):
        with patch.object(
            settings, "LIVEVIEW_CONFIG", {"theme": {"css_prefix": "dj-"}}, create=True
        ):
            tmpl = Template("{% load theme_form_tags %}{% theme_form form %}")
            html = tmpl.render(Context({"form": SimpleForm()}))
        assert "dj-theme-form-stacked" in html


# ===================================================================
# 6.4: Error display tests
# ===================================================================


class TestErrorDisplay:
    """Tests for form error rendering (6.4)."""

    def _render_tag(self, template_str, context_dict=None):
        context_dict = context_dict or {}
        with patch.object(settings, "LIVEVIEW_CONFIG", {}, create=True):
            tmpl = Template(template_str)
            return tmpl.render(Context(context_dict))

    def _get_invalid_form(self):
        """Return a form with field-level errors."""
        form = ErrorForm(data={"email_field": "not-an-email"})
        form.is_valid()
        return form

    def _get_form_with_non_field_errors(self):
        """Return a form with non-field errors."""
        form = ErrorForm(
            data={"required_field": "trigger_error", "email_field": "test@example.com"}
        )
        form.is_valid()
        return form

    def test_field_errors_rendered(self):
        form = self._get_invalid_form()
        html = self._render_tag(
            "{% load theme_form_tags %}{% theme_form form %}",
            {"form": form},
        )
        assert "theme-field-error" in html

    def test_field_errors_have_role_alert(self):
        form = self._get_invalid_form()
        html = self._render_tag(
            "{% load theme_form_tags %}{% theme_form form %}",
            {"form": form},
        )
        assert 'role="alert"' in html

    def test_non_field_errors_rendered_as_alert(self):
        form = self._get_form_with_non_field_errors()
        html = self._render_tag(
            "{% load theme_form_tags %}{% theme_form form %}",
            {"form": form},
        )
        assert "theme-form-errors" in html
        assert "This is a form-level error." in html

    def test_non_field_errors_have_destructive_variant(self):
        form = self._get_form_with_non_field_errors()
        html = self._render_tag(
            "{% load theme_form_tags %}{% theme_form form %}",
            {"form": form},
        )
        assert "alert-destructive" in html

    def test_theme_form_errors_standalone_tag(self):
        form = self._get_form_with_non_field_errors()
        html = self._render_tag(
            "{% load theme_form_tags %}{% theme_form_errors form %}",
            {"form": form},
        )
        assert "This is a form-level error." in html
        assert "theme-form-errors" in html

    def test_theme_form_errors_empty_when_valid(self):
        form = SimpleForm(
            data={"name": "John", "email": "j@e.com", "password": "p", "message": "hi"}
        )
        form.is_valid()
        html = self._render_tag(
            "{% load theme_form_tags %}{% theme_form_errors form %}",
            {"form": form},
        )
        assert "form-level error" not in html

    def test_field_error_text_displayed(self):
        form = self._get_invalid_form()
        html = self._render_tag(
            "{% load theme_form_tags %}{% theme_form form %}",
            {"form": form},
        )
        assert (
            "required" in html.lower()
            or "valid" in html.lower()
            or "This field is required" in html
        )

    def test_error_display_with_css_prefix(self):
        form = self._get_invalid_form()
        with patch.object(
            settings, "LIVEVIEW_CONFIG", {"theme": {"css_prefix": "dj-"}}, create=True
        ):
            tmpl = Template("{% load theme_form_tags %}{% theme_form form %}")
            html = tmpl.render(Context({"form": form}))
        assert "dj-theme-field-error" in html


# ===================================================================
# 6.3: get_css_prefix tag tests
# ===================================================================


class TestGetCssPrefixTag:
    """Tests for {% get_css_prefix %} template tag."""

    def test_get_css_prefix_no_prefix(self):
        with patch.object(settings, "LIVEVIEW_CONFIG", {}, create=True):
            tmpl = Template("{% load theme_form_tags %}{% get_css_prefix as p %}[{{ p }}]")
            html = tmpl.render(Context({}))
        assert "[]" in html

    def test_get_css_prefix_with_prefix(self):
        with patch.object(
            settings, "LIVEVIEW_CONFIG", {"theme": {"css_prefix": "dj-"}}, create=True
        ):
            tmpl = Template("{% load theme_form_tags %}{% get_css_prefix as p %}[{{ p }}]")
            html = tmpl.render(Context({}))
        assert "[dj-]" in html


# ===================================================================
# Integration tests
# ===================================================================


class TestFormIntegration:
    """End-to-end integration tests."""

    @patch.object(settings, "LIVEVIEW_CONFIG", {}, create=True)
    def test_full_form_render_cycle(self):
        """Render FullForm through ThemeFormRenderer — all widget types themed."""
        form = _make_themed_form(FullForm)
        html = form.render()
        assert "theme-input" in html
        assert "theme-textarea" in html
        assert "theme-select" in html
        assert "theme-checkbox" in html
        assert "theme-radio" in html

    @patch.object(settings, "LIVEVIEW_CONFIG", {}, create=True)
    def test_form_with_errors_through_renderer(self):
        """Render form with errors through ThemeFormRenderer."""
        form = _make_themed_form(ErrorForm, data={})
        form.is_valid()
        html = form.render()
        assert "theme-field-errors" in html

    @patch.object(settings, "LIVEVIEW_CONFIG", {}, create=True)
    def test_form_with_initial_data(self):
        form = _make_themed_form(SimpleForm, initial={"name": "John", "email": "j@e.com"})
        html = form.render()
        assert "John" in html
        assert "j@e.com" in html

    def test_theme_form_tag_with_select_and_radio(self):
        """theme_form tag handles select and radio widgets."""
        with patch.object(settings, "LIVEVIEW_CONFIG", {}, create=True):
            tmpl = Template("{% load theme_form_tags %}{% theme_form form %}")
            html = tmpl.render(Context({"form": FullForm()}))
        assert "<select" in html
        assert 'type="radio"' in html
