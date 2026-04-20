"""
Admin-specific form handling for djust admin_ext.

Extends djust's FormMixin with ModelAdmin integration for FK/M2M fields,
readonly fields, and fieldset-based rendering.
"""

import logging
from typing import Any, Dict, List, Optional

from django import forms
from django.db.models import ForeignKey, ManyToManyField, OneToOneField
from django.db.models.fields import DateField, DateTimeField, TimeField
from djust.forms import FormMixin

logger = logging.getLogger(__name__)


class AdminFormMixin(FormMixin):
    """
    FormMixin extended for ModelAdmin integration.

    Provides:
    - Form creation via ModelAdmin.get_form()
    - FK/M2M field option loading
    - Date/DateTime/Time field detection
    - Readonly field support
    - Fieldset-based rendering

    Usage:
        class ModelDetailView(AdminBaseMixin, AdminFormMixin, LiveView):
            template_name = "djust_admin/model_detail.html"

            def form_valid(self, form):
                self.object = form.save()
                self.save_success = True
    """

    # Set by subclass or during mount()
    object: Optional[Any] = None

    def mount(self, request, object_id=None, **kwargs):
        """Initialize form on view mount with model instance."""
        self.request = request
        self.object_id = object_id

        # Load object if editing
        if object_id and self._model:
            self.object = self._model.objects.get(pk=object_id)
        else:
            self.object = None

        # Initialize form state - don't call parent mount() which expects form_class
        self.form_data = {}
        self.form_errors = {}
        self.field_errors = {}
        self.is_valid = False
        self.success_message = ""
        self.error_message = ""

        # Initialize form data from object
        if self.object:
            form = self._create_form()
            for field in form:
                self.form_data[field.name] = field.value()

        # Create initial form instance
        self.form_instance = self._create_form()

    def _create_form(self, data: Optional[Dict[str, Any]] = None) -> forms.Form:
        """
        Create a form instance using ModelAdmin.get_form().

        Overrides FormMixin._create_form() to use the ModelAdmin's form class
        with proper instance binding.

        Args:
            data: Form data dictionary

        Returns:
            Django Form instance
        """
        form_class = self._model_admin.get_form(self.request, self.object)

        if data:
            return form_class(data=data, instance=self.object)
        return form_class(instance=self.object)

    def get_field_options(self, field_name: str) -> List[Dict[str, str]]:
        """
        Get FK/M2M options for a field.

        Returns a list of {"value": pk, "label": str(obj)} for related objects.

        Args:
            field_name: Name of the field

        Returns:
            List of option dicts for select rendering
        """
        try:
            django_field = self._model._meta.get_field(field_name)
            if isinstance(django_field, (ForeignKey, OneToOneField, ManyToManyField)):
                related_model = django_field.remote_field.model
                return [
                    {"value": str(obj.pk), "label": str(obj)}
                    for obj in related_model.objects.all()[:100]
                ]
        except Exception:
            logger.debug("Failed to get field options for %s", field_name, exc_info=True)
        return []

    def get_field_info(self, field_name: str) -> Dict[str, Any]:
        """
        Get field metadata for rendering.

        Returns info about field type (FK, M2M, date, etc.) and options.

        Args:
            field_name: Name of the field

        Returns:
            Dict with field type info and options
        """
        info = {
            "is_foreign_key": False,
            "is_many_to_many": False,
            "is_date": False,
            "is_datetime": False,
            "is_time": False,
            "options": [],
            "input_type": None,  # None = let adapter auto-detect field type
        }

        try:
            django_field = self._model._meta.get_field(field_name)

            if isinstance(django_field, (ForeignKey, OneToOneField)):
                info["is_foreign_key"] = True
                info["options"] = self.get_field_options(field_name)
            elif isinstance(django_field, ManyToManyField):
                info["is_many_to_many"] = True
                info["options"] = self.get_field_options(field_name)
            elif isinstance(django_field, DateTimeField):
                info["is_datetime"] = True
                info["input_type"] = "datetime-local"
            elif isinstance(django_field, DateField):
                info["is_date"] = True
                info["input_type"] = "date"
            elif isinstance(django_field, TimeField):
                info["is_time"] = True
                info["input_type"] = "time"
        except Exception:
            logger.debug("Failed to get field info for %s", field_name, exc_info=True)

        return info

    def get_field_value(self, field_name: str, default: Any = "") -> Any:
        """
        Get current value for a field with FK/M2M and date handling.

        Overrides FormMixin.get_field_value() to properly format FK IDs,
        M2M lists, and date/time values.

        Args:
            field_name: Name of the field
            default: Default value if not found

        Returns:
            Field value, properly formatted
        """
        # Check form_data first
        if field_name in self.form_data:
            return self.form_data.get(field_name, default)

        # Get from object if available
        if not self.object:
            return default

        try:
            django_field = self._model._meta.get_field(field_name)

            # ForeignKey - return the ID
            if isinstance(django_field, (ForeignKey, OneToOneField)):
                fk_value = getattr(self.object, f"{field_name}_id", None)
                return str(fk_value) if fk_value is not None else default

            # ManyToMany - return list of IDs
            elif isinstance(django_field, ManyToManyField):
                if self.object.pk:
                    selected_ids = list(
                        getattr(self.object, field_name).values_list("pk", flat=True)
                    )
                    return [str(pk) for pk in selected_ids]
                return []

            # Date/DateTime/Time - format properly
            elif isinstance(django_field, DateTimeField):
                obj_value = getattr(self.object, field_name, None)
                if obj_value:
                    return obj_value.strftime("%Y-%m-%dT%H:%M")
                return default

            elif isinstance(django_field, DateField):
                obj_value = getattr(self.object, field_name, None)
                if obj_value:
                    return obj_value.strftime("%Y-%m-%d")
                return default

            elif isinstance(django_field, TimeField):
                obj_value = getattr(self.object, field_name, None)
                if obj_value:
                    return obj_value.strftime("%H:%M")
                return default

            # Regular field
            else:
                return getattr(self.object, field_name, default)

        except Exception:
            logger.debug("Failed to get field value for %s", field_name, exc_info=True)
            return default

    def validate_field(self, field_name: str = "", value: Any = None, **kwargs):
        """
        Validate a single field in real-time.

        Extends FormMixin.validate_field() to work with ModelAdmin forms.

        Args:
            field_name: Name of the field to validate
            value: Current field value
        """
        if not field_name:
            return

        # Ensure form state is initialized
        if not hasattr(self, "form_data"):
            self.form_data = {}
        if not hasattr(self, "field_errors"):
            self.field_errors = {}

        # Update form data
        self.form_data[field_name] = value

        # Create form with current data
        form = self._create_form(self.form_data)

        # Clear previous error for this field
        if field_name in self.field_errors:
            del self.field_errors[field_name]

        # Validate the specific field
        if field_name in form.fields:
            field = form.fields[field_name]
            try:
                cleaned_value = field.clean(value)
                field.run_validators(cleaned_value)

                # Run form's clean method for this field if it exists
                if not hasattr(form, "cleaned_data"):
                    form.cleaned_data = {}
                form.cleaned_data[field_name] = cleaned_value

                clean_method = getattr(form, f"clean_{field_name}", None)
                if clean_method:
                    clean_method()

            except forms.ValidationError as e:
                self.field_errors[field_name] = e.messages

        # Update form instance
        self.form_instance = form

    def submit_form(self, **kwargs):
        """
        Handle form submission.

        Extends FormMixin.submit_form() to work with ModelAdmin forms.
        """
        # Merge kwargs into form_data
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
            self.field_errors = {field: errors for field, errors in form.errors.items()}

            if form.non_field_errors():
                self.form_errors = form.non_field_errors()

            self.form_instance = form

            # Call form_invalid hook
            if hasattr(self, "form_invalid"):
                self.form_invalid(form)
