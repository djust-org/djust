"""
Unit tests for CSS framework adapters.

Tests adapter rendering, CSS class assignment, adapter registry,
and correct dj-change event attribute usage.
"""

import pytest
from django import forms
from djust.frameworks import (
    BaseAdapter,
    Bootstrap5Adapter,
    TailwindAdapter,
    PlainAdapter,
    get_adapter,
    register_adapter,
)


# --- Test forms ---


class SimpleForm(forms.Form):
    name = forms.CharField(max_length=100, required=True, help_text="Enter your name")
    email = forms.EmailField(required=True)
    bio = forms.CharField(widget=forms.Textarea, required=False)
    role = forms.ChoiceField(choices=[("dev", "Developer"), ("mgr", "Manager")])
    agree = forms.BooleanField(required=False, label="I agree")
    priority = forms.ChoiceField(
        choices=[("low", "Low"), ("high", "High")],
        widget=forms.RadioSelect,
        required=False,
    )


# --- Adapter output tests ---


class TestBootstrap5Adapter:
    """Test Bootstrap 5 adapter rendering."""

    def setup_method(self):
        self.adapter = Bootstrap5Adapter()
        self.form = SimpleForm()

    def test_text_field_has_dj_change(self):
        html = self.adapter.render_field(self.form.fields["name"], "name", "", [])
        assert 'dj-change="validate_field"' in html
        assert "@change" not in html

    def test_text_field_has_input_type(self):
        html = self.adapter.render_field(self.form.fields["name"], "name", "", [])
        assert 'type="text"' in html

    def test_email_field_type(self):
        html = self.adapter.render_field(self.form.fields["email"], "email", "", [])
        assert 'type="email"' in html

    def test_textarea_rendering(self):
        html = self.adapter.render_field(self.form.fields["bio"], "bio", "hello", [])
        assert "<textarea" in html
        assert "hello" in html

    def test_select_rendering(self):
        html = self.adapter.render_field(self.form.fields["role"], "role", "dev", [])
        assert "<select" in html
        assert "Developer" in html
        assert "selected" in html

    def test_checkbox_rendering(self):
        html = self.adapter.render_field(self.form.fields["agree"], "agree", False, [])
        assert 'type="checkbox"' in html

    def test_radio_rendering(self):
        html = self.adapter.render_field(self.form.fields["priority"], "priority", "low", [])
        assert 'type="radio"' in html
        assert "Low" in html
        assert "High" in html

    def test_errors_rendered(self):
        html = self.adapter.render_field(
            self.form.fields["name"], "name", "", ["This field is required."]
        )
        assert "This field is required." in html

    def test_help_text_rendered(self):
        html = self.adapter.render_field(self.form.fields["name"], "name", "", [])
        assert "Enter your name" in html
        assert "form-text" in html

    def test_required_marker(self):
        html = self.adapter.render_field(self.form.fields["name"], "name", "", [])
        assert "text-danger" in html

    def test_label_rendered(self):
        html = self.adapter.render_field(self.form.fields["name"], "name", "", [])
        assert "<label" in html

    def test_label_hidden_when_disabled(self):
        html = self.adapter.render_field(
            self.form.fields["name"], "name", "", [], render_label=False
        )
        assert "<label" not in html


class TestTailwindAdapter:
    """Test Tailwind adapter rendering."""

    def setup_method(self):
        self.adapter = TailwindAdapter()
        self.form = SimpleForm()

    def test_dj_change_present(self):
        html = self.adapter.render_field(self.form.fields["name"], "name", "", [])
        assert 'dj-change="validate_field"' in html
        assert "@change" not in html

    def test_required_marker(self):
        html = self.adapter.render_field(self.form.fields["name"], "name", "", [])
        assert "text-red-600" in html

    def test_help_text_uses_p_tag(self):
        html = self.adapter.render_field(self.form.fields["name"], "name", "", [])
        assert "<p" in html
        assert "text-sm" in html

    def test_errors_not_wrapped_in_div(self):
        html = self.adapter.render_errors(["Error one"])
        assert "<p" in html
        # Tailwind uses <p> tags, not wrapping <div>
        assert html.startswith("<p")


class TestPlainAdapter:
    """Test Plain HTML adapter rendering."""

    def setup_method(self):
        self.adapter = PlainAdapter()
        self.form = SimpleForm()

    def test_dj_change_present(self):
        html = self.adapter.render_field(self.form.fields["name"], "name", "", [])
        assert 'dj-change="validate_field"' in html
        assert "@change" not in html

    def test_required_marker_plain_text(self):
        html = self.adapter.render_field(self.form.fields["name"], "name", "", [])
        assert " *" in html
        # Plain adapter uses " *" not a <span>
        assert "text-danger" not in html

    def test_help_text_uses_small_tag(self):
        html = self.adapter.render_field(self.form.fields["name"], "name", "", [])
        assert "<small>" in html

    def test_error_class(self):
        html = self.adapter.render_errors(["Something went wrong"])
        assert "error-message" in html

    def test_field_class_with_errors(self):
        cls = self.adapter.get_field_class(self.form.fields["name"], has_errors=True)
        assert cls == "error"

    def test_field_class_without_errors(self):
        cls = self.adapter.get_field_class(self.form.fields["name"], has_errors=False)
        assert cls == ""


# --- Registry tests ---


class TestAdapterRegistry:
    """Test get_adapter() and register_adapter()."""

    def test_get_bootstrap_adapter(self):
        adapter = get_adapter("bootstrap5")
        assert isinstance(adapter, Bootstrap5Adapter)

    def test_get_tailwind_adapter(self):
        adapter = get_adapter("tailwind")
        assert isinstance(adapter, TailwindAdapter)

    def test_get_plain_adapter(self):
        adapter = get_adapter("plain")
        assert isinstance(adapter, PlainAdapter)

    def test_unknown_framework_falls_back_to_plain(self):
        adapter = get_adapter("unknown_framework")
        assert isinstance(adapter, PlainAdapter)

    def test_register_custom_adapter(self):
        class CustomAdapter(BaseAdapter):
            required_marker = " (required)"

        register_adapter("custom", CustomAdapter())
        adapter = get_adapter("custom")
        assert isinstance(adapter, CustomAdapter)

        # Clean up
        from djust.frameworks import _adapters

        del _adapters["custom"]


# --- dj-change verification across all adapters ---


class TestDjChangeAttribute:
    """Verify dj-change is used (not @change) across ALL adapters and field types."""

    @pytest.fixture(params=["bootstrap5", "tailwind", "plain"])
    def adapter(self, request):
        return get_adapter(request.param)

    def test_text_field_uses_dj_change(self, adapter):
        field = forms.CharField()
        html = adapter.render_field(field, "test", "", [])
        assert "dj-change" in html
        assert "@change" not in html

    def test_email_field_uses_dj_change(self, adapter):
        field = forms.EmailField()
        html = adapter.render_field(field, "test", "", [])
        assert "dj-change" in html

    def test_textarea_uses_dj_change(self, adapter):
        field = forms.CharField(widget=forms.Textarea)
        html = adapter.render_field(field, "test", "", [])
        assert "dj-change" in html

    def test_select_uses_dj_change(self, adapter):
        field = forms.ChoiceField(choices=[("a", "A"), ("b", "B")])
        html = adapter.render_field(field, "test", "", [])
        assert "dj-change" in html

    def test_checkbox_uses_dj_change(self, adapter):
        field = forms.BooleanField(required=False)
        html = adapter.render_field(field, "test", False, [])
        assert "dj-change" in html

    def test_radio_uses_dj_change(self, adapter):
        field = forms.ChoiceField(choices=[("a", "A"), ("b", "B")], widget=forms.RadioSelect)
        html = adapter.render_field(field, "test", "", [])
        assert "dj-change" in html

    def test_auto_validate_false_omits_dj_change(self, adapter):
        field = forms.CharField()
        html = adapter.render_field(field, "test", "", [], auto_validate=False)
        assert "dj-change" not in html


# --- XSS escaping in adapters ---


class TestAdapterXSSEscaping:
    """Test that adapters escape user-supplied values."""

    @pytest.fixture(params=["bootstrap5", "tailwind", "plain"])
    def adapter(self, request):
        return get_adapter(request.param)

    def test_value_escaped_in_input(self, adapter):
        field = forms.CharField()
        html = adapter.render_field(field, "test", "<script>alert(1)</script>", [])
        assert "<script>" not in html

    def test_value_escaped_in_textarea(self, adapter):
        field = forms.CharField(widget=forms.Textarea)
        html = adapter.render_field(field, "test", "<img src=x onerror=alert(1)>", [])
        # The < > are escaped so the tag won't render as HTML
        assert "&lt;img" in html
        assert "<img src=" not in html

    def test_errors_escaped(self, adapter):
        field = forms.CharField()
        html = adapter.render_field(field, "test", "", ["<b>bold</b>"])
        assert "<b>" not in html
        assert "&lt;b&gt;" in html

    def test_label_escaped(self, adapter):
        field = forms.CharField(label="<script>bad</script>")
        html = adapter.render_field(field, "test", "", [])
        assert "<script>bad</script>" not in html

    def test_help_text_escaped(self, adapter):
        field = forms.CharField(help_text="<img src=x onerror=alert(1)>")
        html = adapter.render_field(field, "test", "", [])
        # The < > are escaped so the tag won't render as HTML
        assert "&lt;img" in html
        assert "<img src=" not in html

    def test_select_choices_escaped(self, adapter):
        field = forms.ChoiceField(choices=[("<script>", "<b>Bad</b>")])
        html = adapter.render_field(field, "test", "", [])
        assert "<script>" not in html
        assert "<b>" not in html
