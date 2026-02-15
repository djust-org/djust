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
        """Render a form field with framework-specific styling."""
        pass

    @abstractmethod
    def render_errors(self, errors: List[str], **kwargs) -> str:
        """Render field errors with framework-specific styling."""
        pass

    @abstractmethod
    def get_field_class(self, field: forms.Field, has_errors: bool = False) -> str:
        """Get CSS classes for a field widget."""
        pass


class BaseAdapter(FrameworkAdapter):
    """
    Shared rendering logic for all CSS framework adapters.

    Subclasses override class attributes to customize per-framework behavior.
    CSS classes are resolved via ``config.get_framework_class()`` so the user's
    LIVEVIEW_CONFIG is respected automatically.
    """

    # Framework-specific markers — override in subclasses
    required_marker: str = ""
    help_text_tag: str = "div"
    help_text_class: str = ""
    error_wrapper: bool = True

    # --- public API (implements FrameworkAdapter) ---

    def render_field(
        self, field: forms.Field, field_name: str, value: Any, errors: List[str], **kwargs
    ) -> str:
        has_errors = len(errors) > 0
        field_type = self._get_field_type(field)

        wrapper_class = kwargs.get(
            "wrapper_class", config.get_framework_class("field_wrapper_class")
        )
        html = f'<div class="{wrapper_class}">' if wrapper_class else "<div>"

        # Label
        if kwargs.get("render_label", config.get("render_labels", True)):
            html += self._render_label(field, field_name, **kwargs)

        # Widget
        if field_type == "checkbox":
            html += self._render_checkbox(field, field_name, value, has_errors, **kwargs)
        elif field_type == "radio":
            html += self._render_radio(field, field_name, value, has_errors, **kwargs)
        else:
            html += self._render_input(field, field_name, value, has_errors, field_type, **kwargs)

        # Help text
        if kwargs.get("render_help_text", config.get("render_help_text", True)) and field.help_text:
            cls_attr = f' class="{self.help_text_class}"' if self.help_text_class else ""
            html += (
                f"<{self.help_text_tag}{cls_attr}>{escape(field.help_text)}</{self.help_text_tag}>"
            )

        # Errors
        if kwargs.get("render_errors", config.get("render_errors", True)) and has_errors:
            html += self.render_errors(errors)

        html += "</div>"
        return html

    def render_errors(self, errors: List[str], **kwargs) -> str:
        error_class = config.get_framework_class("error_class_block") or config.get_framework_class(
            "error_class"
        )
        if self.error_wrapper:
            html = f'<div class="{error_class}">' if error_class else "<div>"
            for error in errors:
                html += f"<div>{escape(error)}</div>"
            html += "</div>"
        else:
            html = ""
            for error in errors:
                html += (
                    f'<p class="{error_class}">{escape(error)}</p>'
                    if error_class
                    else f"<p>{escape(error)}</p>"
                )
        return html

    def get_field_class(self, field: forms.Field, has_errors: bool = False) -> str:
        if isinstance(field, forms.BooleanField):
            return config.get_framework_class("checkbox_class")
        elif has_errors:
            return config.get_framework_class("field_class_invalid")
        else:
            return config.get_framework_class("field_class")

    # --- shared private helpers ---

    @staticmethod
    def _get_field_type(field: forms.Field) -> str:
        # Check EmailField before CharField (EmailField inherits from CharField)
        if isinstance(field, forms.EmailField):
            return "email"
        elif isinstance(field, forms.CharField):
            if isinstance(field.widget, forms.Textarea):
                return "textarea"
            elif isinstance(field.widget, forms.PasswordInput):
                return "password"
            else:
                return "text"
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

    def _render_label(self, field: forms.Field, field_name: str, **kwargs) -> str:
        label_class = config.get_framework_class("label_class")
        label_text = kwargs.get("label", field.label or field_name.replace("_", " ").title())
        required = self.required_marker if field.required else ""
        cls_attr = f' class="{label_class}"' if label_class else ""
        return f'<label for="id_{field_name}"{cls_attr}>{escape(label_text)}{required}</label>'

    def _build_tag(self, tag: str, attrs: Dict[str, str], content: Optional[str] = None) -> str:
        attrs_str = " ".join(f'{k}="{escape(str(v))}"' for k, v in attrs.items())
        if content is not None:
            return f"<{tag} {attrs_str}>{content}</{tag}>"
        else:
            return f"<{tag} {attrs_str} />"

    def _render_input(
        self,
        field: forms.Field,
        field_name: str,
        value: Any,
        has_errors: bool,
        field_type: str,
        **kwargs,
    ) -> str:
        field_class = self.get_field_class(field, has_errors)
        attrs: Dict[str, str] = {"name": field_name, "id": f"id_{field_name}"}
        if field_class:
            attrs["class"] = field_class
        if field.required:
            attrs["required"] = "required"
        if kwargs.get("auto_validate", config.get("auto_validate_on_change", True)):
            attrs["dj-change"] = "validate_field"

        if field_type == "textarea":
            return self._build_tag("textarea", attrs, escape(str(value)))
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
        options_html = ""
        if not field.required:
            options_html += '<option value="">---------</option>'
        for choice_value, choice_label in field.choices:
            selected = " selected" if str(value) == str(choice_value) else ""
            options_html += f'<option value="{escape(str(choice_value))}"{selected}>{escape(str(choice_label))}</option>'
        return self._build_tag("select", attrs, options_html)

    def _render_checkbox(
        self, field: forms.Field, field_name: str, value: Any, has_errors: bool, **kwargs
    ) -> str:
        wrapper_class = config.get_framework_class("checkbox_wrapper_class")
        field_class = self.get_field_class(field, has_errors)
        label_class = config.get_framework_class("checkbox_label_class")

        attrs: Dict[str, str] = {"type": "checkbox", "name": field_name, "id": f"id_{field_name}"}
        if field_class:
            attrs["class"] = field_class
        if value:
            attrs["checked"] = "checked"
        if field.required:
            attrs["required"] = "required"
        if kwargs.get("auto_validate", config.get("auto_validate_on_change", True)):
            attrs["dj-change"] = "validate_field"

        label_text = kwargs.get("label", field.label or field_name.replace("_", " ").title())
        wrap_cls = f' class="{wrapper_class}"' if wrapper_class else ""
        lbl_cls = f' class="{label_class}"' if label_class else ""

        return (
            f"<div{wrap_cls}>"
            f"{self._build_tag('input', attrs)}"
            f'<label{lbl_cls} for="id_{field_name}">{escape(label_text)}</label>'
            f"</div>"
        )

    def _render_radio(
        self, field: forms.Field, field_name: str, value: Any, has_errors: bool, **kwargs
    ) -> str:
        if not hasattr(field, "choices"):
            return "<!-- ERROR: Radio field must have choices -->"

        radio_field_class = config.get_framework_class("checkbox_class") or "form-check-input"
        radio_wrapper = config.get_framework_class("checkbox_wrapper_class") or "form-check"
        radio_label_class = config.get_framework_class("checkbox_label_class") or "form-check-label"

        html = ""
        for choice_value, choice_label in field.choices:
            radio_id = f"id_{field_name}_{choice_value}"
            attrs: Dict[str, str] = {
                "type": "radio",
                "name": field_name,
                "id": radio_id,
                "value": str(choice_value),
            }
            if radio_field_class:
                attrs["class"] = radio_field_class
            if str(value) == str(choice_value):
                attrs["checked"] = "checked"
            if field.required:
                attrs["required"] = "required"
            if kwargs.get("auto_validate", config.get("auto_validate_on_change", True)):
                attrs["dj-change"] = "validate_field"

            html += (
                f'<div class="{radio_wrapper}">'
                f"{self._build_tag('input', attrs)}"
                f'<label class="{radio_label_class}" for="{radio_id}">{escape(str(choice_label))}</label>'
                f"</div>"
            )
        return html


class Bootstrap5Adapter(BaseAdapter):
    """Bootstrap 5 CSS framework adapter"""

    required_marker = ' <span class="text-danger">*</span>'
    help_text_tag = "div"
    help_text_class = "form-text"
    error_wrapper = True


class TailwindAdapter(BaseAdapter):
    """Tailwind CSS framework adapter"""

    required_marker = ' <span class="text-red-600">*</span>'
    help_text_tag = "p"
    help_text_class = "mt-2 text-sm text-gray-500"
    error_wrapper = False


class PlainAdapter(BaseAdapter):
    """Plain HTML adapter (no CSS framework)"""

    required_marker = " *"
    help_text_tag = "small"
    help_text_class = ""
    error_wrapper = True

    def _render_label(self, field: forms.Field, field_name: str, **kwargs) -> str:
        label_text = kwargs.get("label", field.label or field_name.replace("_", " ").title())
        required = self.required_marker if field.required else ""
        return f'<label for="id_{field_name}">{escape(label_text)}{required}</label>'

    def get_field_class(self, field: forms.Field, has_errors: bool = False) -> str:
        if has_errors:
            return "error"
        return ""

    def render_errors(self, errors: List[str], **kwargs) -> str:
        html = '<div class="error-message">'
        for error in errors:
            html += f"<div>{escape(error)}</div>"
        html += "</div>"
        return html


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
    """
    _adapters[name] = adapter
