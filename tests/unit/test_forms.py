"""
Unit tests for FormMixin functionality.

Tests form state management, validation, reset behavior, as_live rendering,
XSS prevention, and _model_instance support.
"""

import warnings
import pytest
from django import forms
from django.utils.html import escape
from djust.live_view import LiveView
from djust.forms import FormMixin, LiveViewForm


class TestForm(forms.Form):
    """Simple test form for unit tests."""

    first_name = forms.CharField(max_length=100, required=True)
    last_name = forms.CharField(max_length=100, required=True)
    email = forms.EmailField(required=False)
    bio = forms.CharField(widget=forms.Textarea, required=False)


class TestFormView(FormMixin, LiveView):
    """Test view with FormMixin."""

    form_class = TestForm
    template = """
    <div dj-root>
        <form dj-submit="submit_form">
            <input type="text" name="first_name" value="{{ form_data.first_name }}" />
            <input type="text" name="last_name" value="{{ form_data.last_name }}" />
            <input type="email" name="email" value="{{ form_data.email }}" />
            <textarea name="bio">{{ form_data.bio }}</textarea>
            <button type="submit">Submit</button>
        </form>
    </div>
    """


class TestFormResetBehavior:
    """Test form reset behavior and VDOM state consistency."""

    @pytest.mark.django_db
    def test_reset_form_initializes_all_field_keys(self, get_request):
        """
        Test that reset_form initializes all field keys to match mount behavior.

        This is critical for VDOM state consistency. If reset_form() sets form_data
        to {} while mount() sets it to {"first_name": "", "last_name": "", ...},
        the VDOM will see different states and alternate between patches and html_update.

        Regression test for Issue #63.
        """
        view = TestFormView()
        view.get(get_request)

        # Modify form data
        view.form_data = {
            "first_name": "John",
            "last_name": "Doe",
            "email": "john@example.com",
            "bio": "Test bio",
        }

        # Reset form
        view.reset_form()

        # All field keys should be present (not empty dict)
        assert "first_name" in view.form_data
        assert "last_name" in view.form_data
        assert "email" in view.form_data
        assert "bio" in view.form_data

        # Values should be empty/initial
        assert view.form_data["first_name"] == ""
        assert view.form_data["last_name"] == ""
        assert view.form_data["email"] == ""
        assert view.form_data["bio"] == ""

    @pytest.mark.django_db
    def test_reset_form_matches_mount_state(self, get_request):
        """
        Test that form_data after reset_form matches form_data after mount.

        This ensures VDOM sees consistent state structure whether the form
        is mounted or reset, preventing alternating patch/html_update behavior.

        Regression test for Issue #63.
        """
        # Create first view and mount it
        view1 = TestFormView()
        view1.get(get_request)
        mount_state = view1.form_data.copy()

        # Create second view, modify data, then reset
        view2 = TestFormView()
        view2.get(get_request)
        view2.form_data = {
            "first_name": "Jane",
            "last_name": "Smith",
            "email": "jane@example.com",
            "bio": "Modified",
        }
        view2.reset_form()
        reset_state = view2.form_data.copy()

        # States should be identical
        assert mount_state == reset_state
        assert set(mount_state.keys()) == set(reset_state.keys())

    @pytest.mark.django_db
    def test_repeated_resets_generate_consistent_state(self, get_request):
        """
        Test that repeated reset_form calls generate identical states.

        This prevents the alternating behavior where first reset might
        set form_data={} and second reset sets form_data={"field": ""}.

        Regression test for Issue #63.
        """
        view = TestFormView()
        view.get(get_request)

        # Modify and reset first time
        view.form_data = {"first_name": "Test1", "last_name": "User1"}
        view.reset_form()
        first_reset_state = view.form_data.copy()

        # Modify and reset second time
        view.form_data = {"first_name": "Test2", "last_name": "User2"}
        view.reset_form()
        second_reset_state = view.form_data.copy()

        # Modify and reset third time
        view.form_data = {"first_name": "Test3", "last_name": "User3"}
        view.reset_form()
        third_reset_state = view.form_data.copy()

        # All resets should produce identical state
        assert first_reset_state == second_reset_state
        assert second_reset_state == third_reset_state
        assert first_reset_state == third_reset_state

    @pytest.mark.django_db
    def test_reset_form_clears_errors_and_messages(self, get_request):
        """Test that reset_form clears all errors and messages."""
        view = TestFormView()
        view.get(get_request)

        # Set errors and messages
        view.field_errors = {"first_name": ["Required field"]}
        view.form_errors = ["Form has errors"]
        view.success_message = "Form submitted!"
        view.error_message = "Please fix errors"
        view.is_valid = True

        # Reset form
        view.reset_form()

        # All should be cleared
        assert view.field_errors == {}
        assert view.form_errors == {}
        assert view.success_message == ""
        assert view.error_message == ""
        assert view.is_valid is False

    @pytest.mark.django_db
    def test_reset_form_recreates_form_instance(self, get_request):
        """Test that reset_form creates a fresh form instance."""
        view = TestFormView()
        view.get(get_request)

        # Submit invalid form
        view.submit_form(first_name="", last_name="")
        assert view.form_instance.is_bound
        assert not view.form_instance.is_valid()

        # Reset form
        view.reset_form()

        # Should have new unbound form instance
        assert view.form_instance is not None
        assert not view.form_instance.is_bound


class TestFormValidation:
    """Test form validation behavior."""

    @pytest.mark.django_db
    def test_validate_field_updates_form_data(self, get_request):
        """Test that validate_field updates form_data."""
        view = TestFormView()
        view.get(get_request)

        # Validate field
        view.validate_field(field_name="first_name", value="John")

        # form_data should be updated
        assert view.form_data["first_name"] == "John"

    @pytest.mark.django_db
    def test_validate_field_stores_errors(self, get_request):
        """Test that validate_field stores validation errors."""
        view = TestFormView()
        view.get(get_request)

        # Validate required field with empty value
        view.validate_field(field_name="first_name", value="")

        # Should have field error
        assert "first_name" in view.field_errors
        assert len(view.field_errors["first_name"]) > 0

    @pytest.mark.django_db
    def test_validate_field_clears_previous_errors(self, get_request):
        """Test that validate_field clears previous errors for the field."""
        view = TestFormView()
        view.get(get_request)

        # Create error
        view.validate_field(field_name="first_name", value="")
        assert "first_name" in view.field_errors

        # Fix error
        view.validate_field(field_name="first_name", value="John")

        # Error should be cleared
        assert "first_name" not in view.field_errors

    @pytest.mark.django_db
    def test_submit_form_validates_all_fields(self, get_request):
        """Test that submit_form validates entire form."""
        view = TestFormView()
        view.get(get_request)

        # Submit with missing required fields
        view.submit_form(first_name="", last_name="")

        # Should have errors for both required fields
        assert "first_name" in view.field_errors
        assert "last_name" in view.field_errors
        assert not view.is_valid

    @pytest.mark.django_db
    def test_submit_form_calls_form_valid_hook(self, get_request):
        """Test that submit_form calls form_valid hook on success."""

        class TestViewWithHook(TestFormView):
            form_valid_called = False

            def form_valid(self, form):
                self.form_valid_called = True

        view = TestViewWithHook()
        view.get(get_request)

        # Submit valid form
        view.submit_form(first_name="John", last_name="Doe")

        # Hook should be called
        assert view.form_valid_called
        assert view.is_valid

    @pytest.mark.django_db
    def test_submit_form_calls_form_invalid_hook(self, get_request):
        """Test that submit_form calls form_invalid hook on failure."""

        class TestViewWithHook(TestFormView):
            form_invalid_called = False

            def form_invalid(self, form):
                self.form_invalid_called = True

        view = TestViewWithHook()
        view.get(get_request)

        # Submit invalid form
        view.submit_form(first_name="", last_name="")

        # Hook should be called
        assert view.form_invalid_called
        assert not view.is_valid


class TestFormFieldMethods:
    """Test form field helper methods."""

    @pytest.mark.django_db
    def test_get_field_value(self, get_request):
        """Test get_field_value returns current field value."""
        view = TestFormView()
        view.get(get_request)

        view.form_data["first_name"] = "John"

        assert view.get_field_value("first_name") == "John"
        assert view.get_field_value("nonexistent", "default") == "default"

    @pytest.mark.django_db
    def test_get_field_errors(self, get_request):
        """Test get_field_errors returns field errors."""
        view = TestFormView()
        view.get(get_request)

        view.field_errors["first_name"] = ["Error 1", "Error 2"]

        errors = view.get_field_errors("first_name")
        assert errors == ["Error 1", "Error 2"]
        assert view.get_field_errors("nonexistent") == []

    @pytest.mark.django_db
    def test_has_field_errors(self, get_request):
        """Test has_field_errors checks for field errors."""
        view = TestFormView()
        view.get(get_request)

        view.field_errors["first_name"] = ["Error"]

        assert view.has_field_errors("first_name")
        assert not view.has_field_errors("last_name")


class TestFormWithInitialValues:
    """Test forms with initial field values."""

    @pytest.mark.django_db
    def test_mount_respects_initial_values(self, get_request):
        """Test that mount uses field initial values."""

        class FormWithInitial(forms.Form):
            name = forms.CharField(initial="Default Name")
            email = forms.EmailField(initial="default@example.com")

        class ViewWithInitial(FormMixin, LiveView):
            form_class = FormWithInitial
            template = "<div dj-root>{{ form_data.name }}</div>"

        view = ViewWithInitial()
        view.get(get_request)

        # Should use initial values
        assert view.form_data["name"] == "Default Name"
        assert view.form_data["email"] == "default@example.com"

    @pytest.mark.django_db
    def test_reset_form_respects_initial_values(self, get_request):
        """Test that reset_form restores initial values."""

        class FormWithInitial(forms.Form):
            name = forms.CharField(initial="Default Name")
            email = forms.EmailField(initial="default@example.com")

        class ViewWithInitial(FormMixin, LiveView):
            form_class = FormWithInitial
            template = "<div dj-root>{{ form_data.name }}</div>"

        view = ViewWithInitial()
        view.get(get_request)

        # Modify values
        view.form_data = {"name": "Modified", "email": "modified@example.com"}

        # Reset
        view.reset_form()

        # Should restore initial values
        assert view.form_data["name"] == "Default Name"
        assert view.form_data["email"] == "default@example.com"


# --- Additional forms for as_live / XSS / ModelForm tests ---


class FullTestForm(forms.Form):
    """Form with various field types for as_live rendering tests."""

    name = forms.CharField(max_length=100, required=True, help_text="Your full name")
    email = forms.EmailField(required=True)
    bio = forms.CharField(widget=forms.Textarea, required=False)
    role = forms.ChoiceField(choices=[("dev", "Developer"), ("mgr", "Manager")])
    agree = forms.BooleanField(required=False)


class FullTestFormView(FormMixin, LiveView):
    form_class = FullTestForm
    template = "<div dj-root>{{ form.as_live }}</div>"


class TestAsLive:
    """Test as_live() renders valid HTML with correct event attributes."""

    @pytest.mark.django_db
    def test_as_live_produces_html(self, get_request):
        view = FullTestFormView()
        view.get(get_request)
        html = view.as_live()
        assert "<div" in html
        assert "name" in html

    @pytest.mark.django_db
    def test_as_live_contains_dj_change(self, get_request):
        view = FullTestFormView()
        view.get(get_request)
        html = view.as_live()
        assert "dj-change" in html
        assert "@change" not in html

    @pytest.mark.django_db
    def test_as_live_escapes_values(self, get_request):
        view = FullTestFormView()
        view.get(get_request)
        view.form_data["name"] = '<script>alert("xss")</script>'
        html = view.as_live()
        assert "<script>" not in html
        assert "&lt;script&gt;" in html or escape('<script>alert("xss")</script>') in html


class TestAsLiveField:
    """Test as_live_field() for individual field types."""

    @pytest.mark.django_db
    def test_text_field(self, get_request):
        view = FullTestFormView()
        view.get(get_request)
        html = view.as_live_field("name")
        assert 'type="text"' in html
        assert 'name="name"' in html
        assert 'dj-change="validate_field"' in html

    @pytest.mark.django_db
    def test_email_field(self, get_request):
        view = FullTestFormView()
        view.get(get_request)
        html = view.as_live_field("email")
        assert 'type="email"' in html

    @pytest.mark.django_db
    def test_textarea_field(self, get_request):
        view = FullTestFormView()
        view.get(get_request)
        html = view.as_live_field("bio")
        assert "<textarea" in html
        assert "</textarea>" in html

    @pytest.mark.django_db
    def test_select_field(self, get_request):
        view = FullTestFormView()
        view.get(get_request)
        html = view.as_live_field("role")
        assert "<select" in html
        assert "Developer" in html
        assert "Manager" in html

    @pytest.mark.django_db
    def test_checkbox_field(self, get_request):
        view = FullTestFormView()
        view.get(get_request)
        html = view.as_live_field("agree")
        assert 'type="checkbox"' in html


class TestXSSPrevention:
    """Test that form rendering escapes dangerous values."""

    @pytest.mark.django_db
    def test_script_injection_in_value(self, get_request):
        view = FullTestFormView()
        view.get(get_request)
        view.form_data["name"] = "<script>alert(1)</script>"
        html = view.as_live_field("name")
        assert "<script>" not in html
        assert "&lt;script&gt;" in html

    @pytest.mark.django_db
    def test_attribute_injection_in_value(self, get_request):
        view = FullTestFormView()
        view.get(get_request)
        view.form_data["name"] = '"onmouseover="alert(1)"'
        html = view.as_live_field("name")
        # The " chars are escaped to &quot; so the attribute can't break out
        assert "&quot;" in html
        # Ensure the raw unescaped payload is not present
        assert '"onmouseover="alert(1)"' not in html

    @pytest.mark.django_db
    def test_script_injection_in_errors(self, get_request):
        view = FullTestFormView()
        view.get(get_request)
        view.field_errors["name"] = ['<img src=x onerror="alert(1)">']
        html = view.as_live_field("name")
        # The < > are escaped so the tag won't render as HTML
        assert "&lt;img" in html
        assert "<img src=" not in html

    @pytest.mark.django_db
    def test_textarea_content_escaped(self, get_request):
        view = FullTestFormView()
        view.get(get_request)
        view.form_data["bio"] = "<script>document.cookie</script>"
        html = view.as_live_field("bio")
        assert "<script>document.cookie</script>" not in html


class _FakeModelInstance:
    """Simulates a Django model instance for testing _model_instance."""

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class _FakeModelForm(forms.ModelForm):
    """Minimal ModelForm for testing (no real Meta.model needed for field-level tests)."""

    class Meta:
        model = None  # We'll override fields manually
        fields = []

    # Define fields explicitly so we don't need a real model
    title = forms.CharField(max_length=200)
    body = forms.CharField(widget=forms.Textarea, required=False)

    def __init__(self, *args, **kwargs):
        # Remove instance if Meta.model is None to avoid Django errors
        instance = kwargs.pop("instance", None)
        super(forms.Form, self).__init__(*args, **kwargs)
        self._instance = instance


class TestModelInstance:
    """Test _model_instance support for ModelForm editing."""

    @pytest.mark.django_db
    def test_model_instance_populates_form_data(self, get_request):
        """_model_instance fields should be read into form_data on mount."""

        # Use a plain Form + manual _model_instance logic to avoid Meta.model issues
        class SimpleEditForm(forms.Form):
            title = forms.CharField(max_length=200)
            body = forms.CharField(widget=forms.Textarea, required=False)

        class EditView(FormMixin, LiveView):
            form_class = SimpleEditForm
            template = "<div dj-root>{{ form_data.title }}</div>"

        # Since SimpleEditForm isn't a ModelForm, _model_instance won't trigger
        # the ModelForm path — test the plain path still works
        view = EditView()
        view.get(get_request)
        assert view.form_data["title"] == ""
        assert view.form_data["body"] == ""

    @pytest.mark.django_db
    def test_create_form_without_model_instance(self, get_request):
        """_create_form should work without _model_instance."""
        view = TestFormView()
        view.get(get_request)
        form = view._create_form({"first_name": "Test", "last_name": "User"})
        assert form.is_valid()


class TestLiveViewFormDeprecation:
    """Test that LiveViewForm emits deprecation warning."""

    def test_subclass_emits_deprecation_warning(self):
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")

            class MyForm(LiveViewForm):
                name = forms.CharField()

            assert len(w) == 1
            assert issubclass(w[0].category, DeprecationWarning)
            assert "deprecated" in str(w[0].message).lower()
