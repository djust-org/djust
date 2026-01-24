"""
CSS Framework Adapters for djust

Provides pluggable adapters for different CSS frameworks (Bootstrap 5, Tailwind, etc.)
to render form fields with appropriate styling.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List
from django import forms
from django.utils.html import escape
from .config import config


class FrameworkAdapter(ABC):
    """
    Abstract base class for CSS framework adapters.

    Each adapter implements field rendering logic for a specific CSS framework.
    """

    @abstractmethod
    def render_field(
        self, field: forms.Field, field_name: str, value: Any, errors: List[str], **kwargs
    ) -> str:
        """
        Render a form field with framework-specific styling.

        Args:
            field: Django form field instance
            field_name: Name of the field
            value: Current field value
            errors: List of error messages
            **kwargs: Additional rendering options

        Returns:
            HTML string for the field
        """
        pass

    @abstractmethod
    def render_errors(self, errors: List[str], **kwargs) -> str:
        """
        Render field errors with framework-specific styling.

        Args:
            errors: List of error messages
            **kwargs: Additional rendering options

        Returns:
            HTML string for errors
        """
        pass

    @abstractmethod
    def get_field_class(self, field: forms.Field, has_errors: bool = False) -> str:
        """
        Get CSS classes for a field widget.

        Args:
            field: Django form field instance
            has_errors: Whether the field has validation errors

        Returns:
            CSS class string
        """
        pass


class Bootstrap5Adapter(FrameworkAdapter):
    """Bootstrap 5 CSS framework adapter"""

    def render_field(
        self, field: forms.Field, field_name: str, value: Any, errors: List[str], **kwargs
    ) -> str:
        """Render field with Bootstrap 5 styling"""
        has_errors = len(errors) > 0
        field_type = self._get_field_type(field)

        # Build wrapper
        wrapper_class = kwargs.get(
            "wrapper_class", config.get_framework_class("field_wrapper_class")
        )
        html = f'<div class="{wrapper_class}">'

        # Render label
        if kwargs.get("render_label", config.get("render_labels", True)):
            label_class = config.get_framework_class("label_class")
            label_text = kwargs.get("label", field.label or field_name.replace("_", " ").title())
            required = ' <span class="text-danger">*</span>' if field.required else ""
            html += f'<label for="id_{field_name}" class="{label_class}">{escape(label_text)}{required}</label>'

        # Render field widget
        if field_type == "checkbox":
            html += self._render_checkbox(field, field_name, value, has_errors, **kwargs)
        elif field_type == "radio":
            html += self._render_radio(field, field_name, value, has_errors, **kwargs)
        else:
            html += self._render_input(field, field_name, value, has_errors, field_type, **kwargs)

        # Render help text
        if kwargs.get("render_help_text", config.get("render_help_text", True)) and field.help_text:
            html += f'<div class="form-text">{escape(field.help_text)}</div>'

        # Render errors
        if kwargs.get("render_errors", config.get("render_errors", True)) and has_errors:
            html += self.render_errors(errors)

        html += "</div>"
        return html

    def render_errors(self, errors: List[str], **kwargs) -> str:
        """Render errors with Bootstrap 5 styling"""
        error_class = config.get_framework_class("error_class_block")
        html = f'<div class="{error_class}">'
        for error in errors:
            html += f"<div>{escape(error)}</div>"
        html += "</div>"
        return html

    def get_field_class(self, field: forms.Field, has_errors: bool = False) -> str:
        """Get Bootstrap 5 field classes"""
        if isinstance(field, forms.BooleanField):
            return config.get_framework_class("checkbox_class")
        elif has_errors:
            return config.get_framework_class("field_class_invalid")
        else:
            return config.get_framework_class("field_class")

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

    def _render_input(
        self,
        field: forms.Field,
        field_name: str,
        value: Any,
        has_errors: bool,
        field_type: str,
        **kwargs,
    ) -> str:
        """Render standard input field"""
        field_class = self.get_field_class(field, has_errors)
        attrs = {
            "class": field_class,
            "name": field_name,
            "id": f"id_{field_name}",
        }

        if field.required:
            attrs["required"] = "required"

        # Add LiveView event handler
        if kwargs.get("auto_validate", config.get("auto_validate_on_change", True)):
            attrs["@change"] = "validate_field"

        # Render based on field type
        if field_type == "textarea":
            return self._build_tag("textarea", attrs, str(value))
        elif field_type == "select":
            return self._render_select(field, field_name, value, has_errors, attrs)
        else:
            attrs["type"] = field_type
            attrs["value"] = str(value) if value else ""
            return self._build_tag("input", attrs)

    def _render_select(
        self,
        field: forms.Field,
        field_name: str,
        value: Any,
        has_errors: bool,
        attrs: Dict[str, str],
    ) -> str:
        """Render select field"""
        options_html = ""
        if not field.required:
            options_html += '<option value="">---------</option>'

        for choice_value, choice_label in field.choices:
            selected = "selected" if str(value) == str(choice_value) else ""
            options_html += (
                f'<option value="{escape(choice_value)}" {selected}>{escape(choice_label)}</option>'
            )

        return self._build_tag("select", attrs, options_html)

    def _render_checkbox(
        self, field: forms.Field, field_name: str, value: Any, has_errors: bool, **kwargs
    ) -> str:
        """Render checkbox field with Bootstrap styling"""
        wrapper_class = config.get_framework_class("checkbox_wrapper_class")
        field_class = self.get_field_class(field, has_errors)
        label_class = config.get_framework_class("checkbox_label_class")

        attrs = {
            "class": field_class,
            "type": "checkbox",
            "name": field_name,
            "id": f"id_{field_name}",
        }

        if value:
            attrs["checked"] = "checked"

        if field.required:
            attrs["required"] = "required"

        if kwargs.get("auto_validate", config.get("auto_validate_on_change", True)):
            attrs["@change"] = "validate_field"

        label_text = kwargs.get("label", field.label or field_name.replace("_", " ").title())

        return f"""
            <div class="{wrapper_class}">
                {self._build_tag("input", attrs)}
                <label class="{label_class}" for="id_{field_name}">
                    {escape(label_text)}
                </label>
            </div>
        """

    def _render_radio(
        self, field: forms.Field, field_name: str, value: Any, has_errors: bool, **kwargs
    ) -> str:
        """Render radio buttons field with Bootstrap styling"""
        html = ""
        field_class = "form-check-input"
        wrapper_class = "form-check"

        if not hasattr(field, "choices"):
            return "<!-- ERROR: Radio field must have choices -->"

        for choice_value, choice_label in field.choices:
            radio_id = f"id_{field_name}_{choice_value}"
            attrs = {
                "class": field_class,
                "type": "radio",
                "name": field_name,
                "id": radio_id,
                "value": str(choice_value),
            }

            if str(value) == str(choice_value):
                attrs["checked"] = "checked"

            if field.required:
                attrs["required"] = "required"

            if kwargs.get("auto_validate", config.get("auto_validate_on_change", True)):
                attrs["@change"] = "validate_field"

            html += f"""
            <div class="{wrapper_class}">
                {self._build_tag("input", attrs)}
                <label class="form-check-label" for="{radio_id}">
                    {escape(choice_label)}
                </label>
            </div>
            """

        return html

    def _build_tag(self, tag: str, attrs: Dict[str, str], content: str = None) -> str:
        """Build an HTML tag with attributes"""
        attrs_str = " ".join(f'{k}="{escape(str(v))}"' for k, v in attrs.items())

        if content is not None:
            return f"<{tag} {attrs_str}>{content}</{tag}>"
        else:
            return f"<{tag} {attrs_str} />"


class TailwindAdapter(FrameworkAdapter):
    """Tailwind CSS framework adapter"""

    def render_field(
        self, field: forms.Field, field_name: str, value: Any, errors: List[str], **kwargs
    ) -> str:
        """Render field with Tailwind CSS styling"""
        has_errors = len(errors) > 0
        field_type = self._get_field_type(field)

        # Build wrapper
        wrapper_class = kwargs.get(
            "wrapper_class", config.get_framework_class("field_wrapper_class")
        )
        html = f'<div class="{wrapper_class}">'

        # Render label
        if kwargs.get("render_label", config.get("render_labels", True)):
            label_class = config.get_framework_class("label_class")
            label_text = kwargs.get("label", field.label or field_name.replace("_", " ").title())
            required = ' <span class="text-red-600">*</span>' if field.required else ""
            html += f'<label for="id_{field_name}" class="{label_class}">{escape(label_text)}{required}</label>'

        # Render field widget
        if field_type == "checkbox":
            html += self._render_checkbox(field, field_name, value, has_errors, **kwargs)
        else:
            html += self._render_input(field, field_name, value, has_errors, field_type, **kwargs)

        # Render help text
        if kwargs.get("render_help_text", config.get("render_help_text", True)) and field.help_text:
            html += f'<p class="mt-2 text-sm text-gray-500">{escape(field.help_text)}</p>'

        # Render errors
        if kwargs.get("render_errors", config.get("render_errors", True)) and has_errors:
            html += self.render_errors(errors)

        html += "</div>"
        return html

    def render_errors(self, errors: List[str], **kwargs) -> str:
        """Render errors with Tailwind CSS styling"""
        error_class = config.get_framework_class("error_class")
        html = ""
        for error in errors:
            html += f'<p class="{error_class}">{escape(error)}</p>'
        return html

    def get_field_class(self, field: forms.Field, has_errors: bool = False) -> str:
        """Get Tailwind CSS field classes"""
        if isinstance(field, forms.BooleanField):
            return config.get_framework_class("checkbox_class")
        elif has_errors:
            return config.get_framework_class("field_class_invalid")
        else:
            return config.get_framework_class("field_class")

    def _get_field_type(self, field: forms.Field) -> str:
        """Determine field type for rendering (same as Bootstrap)"""
        # Reuse Bootstrap logic
        bootstrap_adapter = Bootstrap5Adapter()
        return bootstrap_adapter._get_field_type(field)

    def _render_input(
        self,
        field: forms.Field,
        field_name: str,
        value: Any,
        has_errors: bool,
        field_type: str,
        **kwargs,
    ) -> str:
        """Render standard input field (similar to Bootstrap but with Tailwind classes)"""
        field_class = self.get_field_class(field, has_errors)
        attrs = {
            "class": field_class,
            "name": field_name,
            "id": f"id_{field_name}",
        }

        if field.required:
            attrs["required"] = "required"

        if kwargs.get("auto_validate", config.get("auto_validate_on_change", True)):
            attrs["@change"] = "validate_field"

        if field_type == "textarea":
            return self._build_tag("textarea", attrs, str(value))
        elif field_type == "select":
            return self._render_select(field, field_name, value, has_errors, attrs)
        else:
            attrs["type"] = field_type
            attrs["value"] = str(value) if value else ""
            return self._build_tag("input", attrs)

    def _render_select(
        self,
        field: forms.Field,
        field_name: str,
        value: Any,
        has_errors: bool,
        attrs: Dict[str, str],
    ) -> str:
        """Render select field"""
        options_html = ""
        if not field.required:
            options_html += '<option value="">---------</option>'

        for choice_value, choice_label in field.choices:
            selected = "selected" if str(value) == str(choice_value) else ""
            options_html += (
                f'<option value="{escape(choice_value)}" {selected}>{escape(choice_label)}</option>'
            )

        return self._build_tag("select", attrs, options_html)

    def _render_checkbox(
        self, field: forms.Field, field_name: str, value: Any, has_errors: bool, **kwargs
    ) -> str:
        """Render checkbox field with Tailwind styling"""
        wrapper_class = config.get_framework_class("checkbox_wrapper_class")
        field_class = self.get_field_class(field, has_errors)
        label_class = config.get_framework_class("checkbox_label_class")

        attrs = {
            "class": field_class,
            "type": "checkbox",
            "name": field_name,
            "id": f"id_{field_name}",
        }

        if value:
            attrs["checked"] = "checked"

        if field.required:
            attrs["required"] = "required"

        if kwargs.get("auto_validate", config.get("auto_validate_on_change", True)):
            attrs["@change"] = "validate_field"

        label_text = kwargs.get("label", field.label or field_name.replace("_", " ").title())

        return f"""
            <div class="{wrapper_class}">
                {self._build_tag("input", attrs)}
                <label class="{label_class}" for="id_{field_name}">
                    {escape(label_text)}
                </label>
            </div>
        """

    def _build_tag(self, tag: str, attrs: Dict[str, str], content: str = None) -> str:
        """Build an HTML tag with attributes"""
        attrs_str = " ".join(f'{k}="{escape(str(v))}"' for k, v in attrs.items())

        if content is not None:
            return f"<{tag} {attrs_str}>{content}</{tag}>"
        else:
            return f"<{tag} {attrs_str} />"


class PlainAdapter(FrameworkAdapter):
    """Plain HTML adapter (no CSS framework)"""

    def render_field(
        self, field: forms.Field, field_name: str, value: Any, errors: List[str], **kwargs
    ) -> str:
        """Render field with minimal plain HTML"""
        has_errors = len(errors) > 0
        field_type = self._get_field_type(field)

        html = "<div>"

        # Render label
        if kwargs.get("render_label", config.get("render_labels", True)):
            label_text = kwargs.get("label", field.label or field_name.replace("_", " ").title())
            required = " *" if field.required else ""
            html += f'<label for="id_{field_name}">{escape(label_text)}{required}</label>'

        # Render field widget
        if field_type == "checkbox":
            html += self._render_checkbox(field, field_name, value, has_errors, **kwargs)
        else:
            html += self._render_input(field, field_name, value, has_errors, field_type, **kwargs)

        # Render help text
        if kwargs.get("render_help_text", config.get("render_help_text", True)) and field.help_text:
            html += f"<small>{escape(field.help_text)}</small>"

        # Render errors
        if kwargs.get("render_errors", config.get("render_errors", True)) and has_errors:
            html += self.render_errors(errors)

        html += "</div>"
        return html

    def render_errors(self, errors: List[str], **kwargs) -> str:
        """Render errors with plain HTML"""
        html = '<div class="error-message">'
        for error in errors:
            html += f"<div>{escape(error)}</div>"
        html += "</div>"
        return html

    def get_field_class(self, field: forms.Field, has_errors: bool = False) -> str:
        """Get plain HTML classes"""
        if has_errors:
            return "error"
        return ""

    def _get_field_type(self, field: forms.Field) -> str:
        """Determine field type for rendering"""
        bootstrap_adapter = Bootstrap5Adapter()
        return bootstrap_adapter._get_field_type(field)

    def _render_input(
        self,
        field: forms.Field,
        field_name: str,
        value: Any,
        has_errors: bool,
        field_type: str,
        **kwargs,
    ) -> str:
        """Render standard input field"""
        field_class = self.get_field_class(field, has_errors)
        attrs = {
            "name": field_name,
            "id": f"id_{field_name}",
        }

        if field_class:
            attrs["class"] = field_class

        if field.required:
            attrs["required"] = "required"

        if kwargs.get("auto_validate", config.get("auto_validate_on_change", True)):
            attrs["@change"] = "validate_field"

        if field_type == "textarea":
            return self._build_tag("textarea", attrs, str(value))
        elif field_type == "select":
            return self._render_select(field, field_name, value, has_errors, attrs)
        else:
            attrs["type"] = field_type
            attrs["value"] = str(value) if value else ""
            return self._build_tag("input", attrs)

    def _render_select(
        self,
        field: forms.Field,
        field_name: str,
        value: Any,
        has_errors: bool,
        attrs: Dict[str, str],
    ) -> str:
        """Render select field"""
        options_html = ""
        if not field.required:
            options_html += '<option value="">---------</option>'

        for choice_value, choice_label in field.choices:
            selected = "selected" if str(value) == str(choice_value) else ""
            options_html += (
                f'<option value="{escape(choice_value)}" {selected}>{escape(choice_label)}</option>'
            )

        return self._build_tag("select", attrs, options_html)

    def _render_checkbox(
        self, field: forms.Field, field_name: str, value: Any, has_errors: bool, **kwargs
    ) -> str:
        """Render checkbox field"""
        field_class = self.get_field_class(field, has_errors)
        attrs = {
            "type": "checkbox",
            "name": field_name,
            "id": f"id_{field_name}",
        }

        if field_class:
            attrs["class"] = field_class

        if value:
            attrs["checked"] = "checked"

        if field.required:
            attrs["required"] = "required"

        if kwargs.get("auto_validate", config.get("auto_validate_on_change", True)):
            attrs["@change"] = "validate_field"

        label_text = kwargs.get("label", field.label or field_name.replace("_", " ").title())

        return f"""
            <div>
                {self._build_tag("input", attrs)}
                <label for="id_{field_name}">{escape(label_text)}</label>
            </div>
        """

    def _build_tag(self, tag: str, attrs: Dict[str, str], content: str = None) -> str:
        """Build an HTML tag with attributes"""
        attrs_str = " ".join(f'{k}="{escape(str(v))}"' for k, v in attrs.items())

        if content is not None:
            return f"<{tag} {attrs_str}>{content}</{tag}>"
        else:
            return f"<{tag} {attrs_str} />"


# Registry of available adapters
_adapters: Dict[str, FrameworkAdapter] = {
    "bootstrap5": Bootstrap5Adapter(),
    "tailwind": TailwindAdapter(),
    "plain": PlainAdapter(),
}


def get_adapter(framework: Optional[str] = None) -> FrameworkAdapter:
    """
    Get a framework adapter by name.

    Args:
        framework: Framework name ('bootstrap5', 'tailwind', 'plain', or None)
                  If None, uses the configured default

    Returns:
        FrameworkAdapter instance

    Example:
        adapter = get_adapter('bootstrap5')
        html = adapter.render_field(form.fields['email'], 'email', '', [])
    """
    if framework is None:
        framework = config.get("css_framework", "bootstrap5")

    if framework is None:
        framework = "plain"

    return _adapters.get(framework, _adapters["plain"])


def register_adapter(name: str, adapter: FrameworkAdapter):
    """
    Register a custom framework adapter.

    Args:
        name: Adapter name
        adapter: FrameworkAdapter instance

    Example:
        class MyCustomAdapter(FrameworkAdapter):
            ...

        register_adapter('my_framework', MyCustomAdapter())
    """
    _adapters[name] = adapter
