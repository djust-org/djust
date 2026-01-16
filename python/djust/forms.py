"""
Django Forms integration for djust

This module provides seamless integration between Django's forms system and LiveView,
enabling real-time validation, error display, and reactive form handling.
"""

from typing import Dict, Any, Optional, Type, List
from django import forms
from django.core.exceptions import ValidationError
import json


class FormMixin:
    """
    Mixin for LiveView classes to add Django Forms support with real-time validation.

    Usage:
        class MyFormView(FormMixin, LiveView):
            form_class = MyDjangoForm

            def form_valid(self, form):
                # Handle valid form submission
                form.save()
                self.success_message = "Form saved successfully!"

            def form_invalid(self, form):
                # Handle invalid form submission
                self.error_message = "Please correct the errors below"
    """

    form_class: Optional[Type[forms.Form]] = None
    form_instance: Optional[forms.Form] = None

    def mount(self, request, **kwargs):
        """Initialize form on view mount"""
        super().mount(request, **kwargs)

        # Initialize form state with all form fields set to empty strings
        # This ensures that when template renders {{ form_data.field_name }},
        # it doesn't render missing keys as empty, which would clear user input
        self.form_data = {}
        if self.form_class:
            form = self.form_class()
            # Initialize all fields with their initial values or empty string
            for field_name, field in form.fields.items():
                initial = field.initial
                if initial is None:
                    initial = ""
                self.form_data[field_name] = initial

        self.form_errors = {}
        self.field_errors = {}
        self.is_valid = False
        self.success_message = ""
        self.error_message = ""

        # Create initial form instance
        if self.form_class:
            self.form_instance = self._create_form()

    def _create_form(self, data: Optional[Dict[str, Any]] = None) -> forms.Form:
        """
        Create a form instance with optional data.

        Args:
            data: Form data dictionary

        Returns:
            Django Form instance
        """
        if not self.form_class:
            raise ValueError("form_class must be set to use FormMixin")

        if data:
            return self.form_class(data)
        else:
            return self.form_class()

    def validate_field(self, field_name: str = "", value: Any = None, **kwargs):
        """
        Validate a single field in real-time.

        This is called when a field changes (@change event).

        Args:
            field_name: Name of the field to validate
            value: Current field value
        """
        if not field_name:
            return

        # Ensure form state is initialized (defensive check)
        if not hasattr(self, "form_data"):
            self.form_data = {}
        if not hasattr(self, "field_errors"):
            self.field_errors = {}
        if not hasattr(self, "form_instance"):
            self.form_instance = None

        # Update form data
        self.form_data[field_name] = value

        # Create form with current data
        form = self._create_form(self.form_data)

        # Clear previous error for this field
        if field_name in self.field_errors:
            del self.field_errors[field_name]

        # Validate the specific field
        try:
            # Get the field
            field = form.fields.get(field_name)
            if field:
                # Clean the value
                cleaned_value = field.clean(value)

                # Run field-specific validators
                field.run_validators(cleaned_value)

                # Set up cleaned_data for custom clean methods
                if not hasattr(form, "cleaned_data"):
                    form.cleaned_data = {}
                form.cleaned_data[field_name] = cleaned_value

                # Run form's clean method for this field if it exists
                clean_method = getattr(form, f"clean_{field_name}", None)
                if clean_method:
                    clean_method()

        except ValidationError as e:
            # Store field error
            self.field_errors[field_name] = e.messages

        # Update form instance
        self.form_instance = form

    def submit_form(self, **kwargs):
        """
        Handle form submission.

        This is called when the form is submitted (@submit event).
        Validates all fields and calls form_valid() or form_invalid().
        """
        # Merge kwargs into form_data (for fields submitted with the form)
        self.form_data.update(kwargs)

        # Create form with all data
        form = self._create_form(self.form_data)

        # Validate entire form
        if form.is_valid():
            self.is_valid = True
            self.field_errors = {}
            self.form_errors = {}
            self.form_instance = form

            # Call form_valid hook
            if hasattr(self, "form_valid"):
                self.form_valid(form)
        else:
            self.is_valid = False

            # Store all errors
            self.field_errors = {field: errors for field, errors in form.errors.items()}

            # Store non-field errors
            if form.non_field_errors():
                self.form_errors = form.non_field_errors()

            self.form_instance = form

            # Call form_invalid hook
            if hasattr(self, "form_invalid"):
                self.form_invalid(form)

    def reset_form(self, **kwargs):
        """Reset form to initial state"""
        # Reset form_data with all field keys initialized (matching mount() behavior)
        # This ensures consistent VDOM state and prevents alternating patches/html_update
        self.form_data = {}
        if self.form_class:
            form = self.form_class()
            # Initialize all fields with their initial values or empty string
            for field_name, field in form.fields.items():
                initial = field.initial
                if initial is None:
                    initial = ""
                self.form_data[field_name] = initial

        self.form_errors = {}
        self.field_errors = {}
        self.is_valid = False
        self.success_message = ""
        self.error_message = ""

        if self.form_class:
            self.form_instance = self._create_form()

        # Signal to WebSocket handler that we need to reset the form on client-side
        # This bypasses VDOM form value preservation
        self._should_reset_form = True

    def get_field_value(self, field_name: str, default: Any = "") -> Any:
        """Get current value for a field"""
        return self.form_data.get(field_name, default)

    def get_field_errors(self, field_name: str) -> List[str]:
        """Get errors for a specific field"""
        return self.field_errors.get(field_name, [])

    def has_field_errors(self, field_name: str) -> bool:
        """Check if a field has errors"""
        return field_name in self.field_errors

    def render_field(self, field_name: str) -> str:
        """
        Render a form field with current value and errors.

        Args:
            field_name: Name of the field to render

        Returns:
            HTML string for the field
        """
        if not self.form_instance:
            return ""

        field = self.form_instance.fields.get(field_name)
        if not field:
            return ""

        # Get current value
        value = self.get_field_value(field_name)

        # Get bound field
        bound_field = self.form_instance[field_name]

        # Get errors
        errors = self.get_field_errors(field_name)
        has_errors = len(errors) > 0

        # Build field HTML
        field_type = self._get_field_type(field)
        field_html = self._render_field_widget(
            field_name, field, bound_field, value, field_type, has_errors
        )

        # Build error HTML
        error_html = ""
        if has_errors:
            error_html = '<div class="invalid-feedback d-block">'
            for error in errors:
                error_html += f"<div>{error}</div>"
            error_html += "</div>"

        return field_html + error_html

    def _get_field_type(self, field: forms.Field) -> str:
        """Determine field type for rendering"""
        if isinstance(field, forms.CharField):
            if isinstance(field.widget, forms.Textarea):
                return "textarea"
            elif isinstance(field.widget, forms.PasswordInput):
                return "password"
            else:
                return "text"
        elif isinstance(field, forms.EmailField):
            return "email"
        elif isinstance(field, forms.IntegerField):
            return "number"
        elif isinstance(field, forms.BooleanField):
            return "checkbox"
        elif isinstance(field, forms.ChoiceField):
            if isinstance(field.widget, forms.RadioSelect):
                return "radio"
            else:
                return "select"
        elif isinstance(field, forms.DateField):
            return "date"
        elif isinstance(field, forms.DateTimeField):
            return "datetime-local"
        else:
            return "text"

    def _render_field_widget(
        self,
        field_name: str,
        field: forms.Field,
        bound_field: forms.BoundField,
        value: Any,
        field_type: str,
        has_errors: bool,
    ) -> str:
        """Render the field widget HTML"""
        # Base attributes
        attrs = {"name": field_name, "id": f"id_{field_name}", "class": "form-control"}

        if has_errors:
            attrs["class"] += " is-invalid"

        if field.required:
            attrs["required"] = "required"

        # Add LiveView change event for real-time validation
        attrs["@change"] = "validate_field"
        attrs["data-field"] = field_name

        # Render based on field type
        if field_type == "textarea":
            return f"<textarea {self._attrs_to_string(attrs)}>{value}</textarea>"

        elif field_type == "checkbox":
            attrs["class"] = "form-check-input"
            attrs["type"] = "checkbox"
            if value:
                attrs["checked"] = "checked"
            return f"<input {self._attrs_to_string(attrs)} />"

        elif field_type == "select":
            choices_html = ""
            if not field.required:
                choices_html += '<option value="">---------</option>'

            for choice_value, choice_label in field.choices:
                selected = "selected" if str(value) == str(choice_value) else ""
                choices_html += f'<option value="{choice_value}" {selected}>{choice_label}</option>'

            del attrs["@change"]  # Select uses different event
            attrs["@change"] = "validate_field"
            return f"<select {self._attrs_to_string(attrs)}>{choices_html}</select>"

        else:
            # Standard input field
            attrs["type"] = field_type
            attrs["value"] = value
            return f"<input {self._attrs_to_string(attrs)} />"

    def _attrs_to_string(self, attrs: Dict[str, str]) -> str:
        """Convert attributes dict to HTML string"""
        return " ".join(f'{k}="{v}"' for k, v in attrs.items())

    def as_live(self, **kwargs) -> str:
        """
        Render the entire form automatically using the configured CSS framework.

        This eliminates the need for manual field-by-field rendering. The form
        will use the framework adapter (Bootstrap 5, Tailwind, etc.) to render
        all fields with proper styling, labels, errors, and event handlers.

        Args:
            **kwargs: Rendering options
                - framework: Override the configured CSS framework
                - render_labels: Whether to render field labels (default: True)
                - render_help_text: Whether to render help text (default: True)
                - render_errors: Whether to render errors (default: True)
                - auto_validate: Whether to add validation on change (default: True)
                - wrapper_class: Custom wrapper class for each field

        Returns:
            HTML string for the entire form

        Example:
            # In template:
            <form @submit="submit_form">
                {{ form.as_live }}
                <button type="submit">Submit</button>
            </form>
        """
        from .frameworks import get_adapter

        if not hasattr(self, "form_instance") or not self.form_instance:
            return "<!-- ERROR: form_instance not initialized. Did you call super().mount()? -->"

        framework = kwargs.pop("framework", None)
        adapter = get_adapter(framework)

        html = ""
        for field_name in self.form_instance.fields.keys():
            html += self.as_live_field(field_name, adapter=adapter, **kwargs)

        return html

    def as_live_field(self, field_name: str, adapter=None, **kwargs) -> str:
        """
        Render a single form field automatically using the configured CSS framework.

        This method uses the framework adapter to render a field with proper styling,
        labels, errors, help text, and LiveView event handlers automatically.

        Args:
            field_name: Name of the field to render
            adapter: Framework adapter to use (if None, uses configured framework)
            **kwargs: Rendering options
                - framework: Override the configured CSS framework
                - render_labels: Whether to render field labels (default: True)
                - render_help_text: Whether to render help text (default: True)
                - render_errors: Whether to render errors (default: True)
                - auto_validate: Whether to add validation on change (default: True)
                - wrapper_class: Custom wrapper class for the field
                - label: Custom label text

        Returns:
            HTML string for the field

        Example:
            # In template:
            {{ form.as_live_field("email") }}
            {{ form.as_live_field("password", label="Custom Password Label") }}
        """
        from .frameworks import get_adapter

        if not self.form_instance:
            return ""

        field = self.form_instance.fields.get(field_name)
        if not field:
            return ""

        # Get adapter
        if adapter is None:
            framework = kwargs.pop("framework", None)
            adapter = get_adapter(framework)

        # Get current value and errors
        value = self.get_field_value(field_name, default="")
        errors = self.get_field_errors(field_name)

        # Render using adapter
        return adapter.render_field(field, field_name, value, errors, **kwargs)


class LiveViewForm(forms.Form):
    """
    Base form class optimized for LiveView usage.

    Provides additional utilities for real-time validation and error handling.
    """

    def get_field_errors_json(self) -> str:
        """Get field errors as JSON string"""
        return json.dumps({field: errors for field, errors in self.errors.items()})

    def get_field_value(self, field_name: str, default: Any = "") -> Any:
        """Get cleaned value for a field"""
        if hasattr(self, "cleaned_data"):
            return self.cleaned_data.get(field_name, default)
        return self.data.get(field_name, default)


def form_field(field_name: str, **field_kwargs):
    """
    Template helper to render a form field.

    Usage in template:
        {{ form.render_field('email') }}
    """

    def render(view):
        if hasattr(view, "render_field"):
            return view.render_field(field_name)
        return ""

    return render
