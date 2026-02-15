"""
Django Forms integration for djust

This module provides seamless integration between Django's forms system and LiveView,
enabling real-time validation, error display, and reactive form handling.
"""

import warnings
from typing import Dict, Any, Optional, Type, List
from django import forms
from django.core.exceptions import ValidationError


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

    ModelForm usage:
        class MyModelFormView(FormMixin, LiveView):
            form_class = MyModelForm
            _model_instance = None  # Set in mount() for editing

            def mount(self, request, **kwargs):
                super().mount(request, **kwargs)
                self._model_instance = MyModel.objects.get(pk=kwargs['pk'])
    """

    form_class: Optional[Type[forms.Form]] = None
    form_instance: Optional[forms.Form] = None
    _model_instance = None

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

            # If _model_instance is set and this is a ModelForm, populate from instance
            if self._model_instance and issubclass(self.form_class, forms.ModelForm):
                for field_name in form.fields:
                    if hasattr(self._model_instance, field_name):
                        val = getattr(self._model_instance, field_name)
                        if val is not None:
                            self.form_data[field_name] = val

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

        kwargs = {}
        if self._model_instance and issubclass(self.form_class, forms.ModelForm):
            kwargs["instance"] = self._model_instance

        if data:
            return self.form_class(data, **kwargs)
        else:
            return self.form_class(**kwargs)

    def validate_field(self, field_name: str = "", value: Any = None, **kwargs):
        """
        Validate a single field in real-time.

        This is called when a field changes (dj-change event).

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

        This is called when the form is submitted (dj-submit event).
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
            <form dj-submit="submit_form">
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

        Returns:
            HTML string for the field
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
    Base form class for LiveView usage.

    .. deprecated::
        LiveViewForm adds no functionality over ``forms.Form``.
        Use ``django.forms.Form`` directly instead.
    """

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        warnings.warn(
            "LiveViewForm is deprecated and will be removed in djust 0.4. "
            "Use django.forms.Form directly instead.",
            DeprecationWarning,
            stacklevel=2,
        )

    def get_field_errors_json(self) -> str:
        """Get field errors as JSON string"""
        import json

        return json.dumps({field: errors for field, errors in self.errors.items()})

    def get_field_value(self, field_name: str, default: Any = "") -> Any:
        """Get cleaned value for a field"""
        if hasattr(self, "cleaned_data"):
            return self.cleaned_data.get(field_name, default)
        return self.data.get(field_name, default)
