"""
Admin-specific CSS framework adapters for djust admin_ext.

Extends djust's framework adapters with support for ForeignKey, ManyToMany,
and date/time fields commonly used in Django admin interfaces.
"""

from typing import Any, Dict, List

from django import forms
from django.utils.html import escape
from djust.frameworks import TailwindAdapter, register_adapter


class AdminTailwindAdapter(TailwindAdapter):
    """
    Tailwind CSS adapter extended for admin field types.

    Adds support for:
    - ForeignKey fields (rendered as select dropdowns)
    - ManyToMany fields (rendered as multi-select)
    - Date/DateTime/Time fields (proper input types)
    - Readonly fields

    Usage:
        # Register at app startup
        from djust.admin_ext.adapters import register_admin_adapters
        register_admin_adapters()

        # Use in views
        self.as_live_field(field_name, framework='admin_tailwind', **field_info)
    """

    # Tailwind classes for admin fields
    FIELD_CLASS = (
        "block w-full rounded-md border-gray-300 shadow-sm "
        "focus:border-indigo-500 focus:ring-indigo-500 sm:text-sm"
    )
    FIELD_CLASS_INVALID = (
        "block w-full rounded-md border-red-300 shadow-sm "
        "focus:border-red-500 focus:ring-red-500 sm:text-sm"
    )
    SELECT_CLASS = (
        "block w-full rounded-md border-gray-300 shadow-sm "
        "focus:border-indigo-500 focus:ring-indigo-500 sm:text-sm"
    )
    MULTI_SELECT_CLASS = (
        "block w-full rounded-md border-gray-300 shadow-sm "
        "focus:border-indigo-500 focus:ring-indigo-500 sm:text-sm"
    )
    LABEL_CLASS = "block text-sm font-medium text-gray-700"
    ERROR_CLASS = "mt-2 text-sm text-red-600"
    HELP_TEXT_CLASS = "mt-2 text-sm text-gray-500"
    WRAPPER_CLASS = "mb-4"
    CHECKBOX_CLASS = "h-4 w-4 text-indigo-600 focus:ring-indigo-500 border-gray-300 rounded"
    CHECKBOX_WRAPPER_CLASS = "flex items-center"
    CHECKBOX_LABEL_CLASS = "ml-2 block text-sm text-gray-900"

    def render_field(
        self, field: forms.Field, field_name: str, value: Any, errors: List[str], **kwargs
    ) -> str:
        """
        Render field with admin-specific handling.

        Checks for admin field types (FK, M2M, date) passed in kwargs and
        routes to appropriate rendering methods.

        Args:
            field: Django form field instance
            field_name: Name of the field
            value: Current field value
            errors: List of error messages
            **kwargs: Additional options including:
                - is_foreign_key: True if FK field
                - is_many_to_many: True if M2M field
                - is_date/is_datetime/is_time: True for date fields
                - options: List of {"value": ..., "label": ...} for FK/M2M
                - readonly: True if readonly field
                - input_type: Override input type (date, datetime-local, time)

        Returns:
            HTML string for the field
        """
        # Extract admin-specific options
        options = kwargs.pop("options", None)
        is_foreign_key = kwargs.pop("is_foreign_key", False)
        is_many_to_many = kwargs.pop("is_many_to_many", False)
        is_date = kwargs.pop("is_date", False)
        is_datetime = kwargs.pop("is_datetime", False)
        is_time = kwargs.pop("is_time", False)
        is_readonly = kwargs.pop("readonly", False)
        input_type = kwargs.pop("input_type", None)

        has_errors = len(errors) > 0

        # Build wrapper
        wrapper_class = kwargs.get("wrapper_class", self.WRAPPER_CLASS)
        html = f'<div class="{wrapper_class}">'

        # Render label
        if kwargs.get("render_label", True):
            label_text = kwargs.get("label", field.label or field_name.replace("_", " ").title())
            required = ' <span class="text-red-500">*</span>' if field.required else ""
            html += (
                f'<label for="id_{field_name}" class="{self.LABEL_CLASS}">'
                f"{escape(label_text)}{required}</label>"
            )

        html += '<div class="mt-1">'

        # Render based on field type
        if is_readonly:
            html += self._render_readonly(field_name, value)
        elif is_foreign_key and options:
            html += self._render_fk_select(field, field_name, value, errors, options, **kwargs)
        elif is_many_to_many and options:
            html += self._render_m2m_select(field, field_name, value, errors, options, **kwargs)
        elif is_datetime:
            html += self._render_datetime_input(
                field, field_name, value, has_errors, "datetime-local", **kwargs
            )
        elif is_date:
            html += self._render_datetime_input(
                field, field_name, value, has_errors, "date", **kwargs
            )
        elif is_time:
            html += self._render_datetime_input(
                field, field_name, value, has_errors, "time", **kwargs
            )
        elif input_type:
            html += self._render_datetime_input(
                field, field_name, value, has_errors, input_type, **kwargs
            )
        else:
            # Fall back to parent implementation for standard fields
            html += self._render_standard_field(field, field_name, value, has_errors, **kwargs)

        html += "</div>"

        # Render errors
        if kwargs.get("render_errors", True) and has_errors:
            html += self.render_errors(errors)

        # Render help text
        if kwargs.get("render_help_text", True) and field.help_text:
            html += f'<p class="{self.HELP_TEXT_CLASS}">{escape(field.help_text)}</p>'

        html += "</div>"
        return html

    def render_errors(self, errors: List[str], **kwargs) -> str:
        """Render errors with admin Tailwind styling."""
        html = ""
        for error in errors:
            html += f'<p class="{self.ERROR_CLASS}">{escape(error)}</p>'
        return html

    def get_field_class(self, field: forms.Field, has_errors: bool = False) -> str:
        """Get Tailwind CSS field classes for admin."""
        if isinstance(field, forms.BooleanField):
            return self.CHECKBOX_CLASS
        elif has_errors:
            return self.FIELD_CLASS_INVALID
        else:
            return self.FIELD_CLASS

    def _render_readonly(self, field_name: str, value: Any) -> str:
        """Render a readonly field display."""
        display_value = value if value else "-"
        return f'<p class="py-2 text-gray-900">{escape(str(display_value))}</p>'

    def _render_fk_select(
        self,
        field: forms.Field,
        field_name: str,
        value: Any,
        errors: List[str],
        options: List[Dict[str, str]],
        **kwargs,
    ) -> str:
        """Render ForeignKey as select dropdown."""
        has_errors = len(errors) > 0
        field_class = self.FIELD_CLASS_INVALID if has_errors else self.SELECT_CLASS

        html = f'<select name="{field_name}" id="id_{field_name}" class="{field_class}" '
        html += f"dj-change=\"validate_field('{field_name}', value)\">"
        html += '<option value="">---------</option>'

        for opt in options:
            selected = "selected" if str(value) == opt["value"] else ""
            html += (
                f'<option value="{escape(opt["value"])}" {selected}>{escape(opt["label"])}</option>'
            )

        html += "</select>"
        return html

    def _render_m2m_select(
        self,
        field: forms.Field,
        field_name: str,
        value: Any,
        errors: List[str],
        options: List[Dict[str, str]],
        **kwargs,
    ) -> str:
        """Render ManyToMany as multi-select."""
        has_errors = len(errors) > 0
        field_class = self.FIELD_CLASS_INVALID if has_errors else self.MULTI_SELECT_CLASS

        # Ensure value is a list of strings (form field.value() may return ints)
        selected_values = [str(v) for v in value] if isinstance(value, list) else []

        html = f'<select name="{field_name}" id="id_{field_name}" class="{field_class}" '
        html += 'multiple size="6" '
        # dj-change handler for M2M - extracts selected option values as array
        dj_change = (
            f"validate_field('{field_name}', Array.from(this.selectedOptions).map(o => o.value))"
        )
        html += f'dj-change="{dj_change}">'

        for opt in options:
            selected = "selected" if opt["value"] in selected_values else ""
            html += (
                f'<option value="{escape(opt["value"])}" {selected}>{escape(opt["label"])}</option>'
            )

        html += "</select>"
        html += '<p class="mt-1 text-xs text-gray-500">Hold Ctrl/Cmd to select multiple</p>'
        return html

    def _render_datetime_input(
        self,
        field: forms.Field,
        field_name: str,
        value: Any,
        has_errors: bool,
        input_type: str,
        **kwargs,
    ) -> str:
        """Render date/datetime/time input."""
        field_class = self.FIELD_CLASS_INVALID if has_errors else self.FIELD_CLASS

        # Format value based on type
        formatted_value = ""
        if value:
            if hasattr(value, "strftime"):
                if input_type == "datetime-local":
                    formatted_value = value.strftime("%Y-%m-%dT%H:%M")
                elif input_type == "date":
                    formatted_value = value.strftime("%Y-%m-%d")
                elif input_type == "time":
                    formatted_value = value.strftime("%H:%M")
            else:
                formatted_value = str(value)

        html = f'<input type="{input_type}" name="{field_name}" id="id_{field_name}" '
        html += f'value="{escape(formatted_value)}" class="{field_class}" '
        html += f"dj-change=\"validate_field('{field_name}', value)\""
        if field.required:
            html += " required"
        html += " />"
        return html

    def _render_standard_field(
        self,
        field: forms.Field,
        field_name: str,
        value: Any,
        has_errors: bool,
        **kwargs,
    ) -> str:
        """Render standard input/textarea/select field."""
        field_class = self.get_field_class(field, has_errors)
        field_type = self._get_field_type(field)

        if field_type == "textarea":
            html = f'<textarea name="{field_name}" id="id_{field_name}" class="{field_class}" '
            html += f'rows="4" dj-input="validate_field(\'{field_name}\', value)"'
            if field.required:
                html += " required"
            html += f">{escape(str(value) if value else '')}</textarea>"
            return html

        elif field_type == "checkbox":
            checked = "checked" if value else ""
            html = f'<input type="checkbox" name="{field_name}" id="id_{field_name}" '
            html += f'class="{self.CHECKBOX_CLASS}" {checked} '
            html += f"dj-change=\"validate_field('{field_name}', this.checked)\""
            if field.required:
                html += " required"
            html += " />"
            return html

        elif field_type == "select":
            html = f'<select name="{field_name}" id="id_{field_name}" class="{field_class}" '
            html += f"dj-change=\"validate_field('{field_name}', value)\">"

            if not field.required:
                html += '<option value="">---------</option>'

            for choice_value, choice_label in field.choices:
                selected = "selected" if str(value) == str(choice_value) else ""
                opt_val = escape(str(choice_value))
                opt_label = escape(str(choice_label))
                html += f'<option value="{opt_val}" {selected}>{opt_label}</option>'

            html += "</select>"
            return html

        else:
            # Standard input field
            html = f'<input type="{field_type}" name="{field_name}" id="id_{field_name}" '
            html += f'value="{escape(str(value) if value else "")}" class="{field_class}" '
            html += f"dj-input=\"validate_field('{field_name}', value)\""
            if field.required:
                html += " required"
            html += " />"
            return html


def register_admin_adapters():
    """
    Register admin-specific framework adapters.

    Call this at app startup (e.g., in AppConfig.ready()).
    """
    register_adapter("admin_tailwind", AdminTailwindAdapter())


# Auto-register on import
register_admin_adapters()
