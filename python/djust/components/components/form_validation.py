"""Form Validation Display components for rendering Django form errors."""

import html as html_mod

from djust import Component


class FormErrors(Component):
    """Renders all form-level (non-field) validation errors.

    Usage in a LiveView::

        self.form_errors = FormErrors(form=my_form)

    In template::

        {{ form_errors|safe }}

    Args:
        form: A Django form instance.
        custom_class: Additional CSS classes.
    """

    def __init__(self, form=None, custom_class: str = "", **kwargs):
        super().__init__(form=form, custom_class=custom_class, **kwargs)
        self.form = form
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        if self.form is None or not hasattr(self.form, "non_field_errors"):
            return ""

        errors = self.form.non_field_errors()
        if not errors:
            return ""

        cls = "dj-form-errors"
        if self.custom_class:
            cls += f" {html_mod.escape(self.custom_class)}"

        items = []
        for err in errors:
            items.append(f'<li class="dj-form-errors__item">{html_mod.escape(str(err))}</li>')

        return (
            f'<div class="{cls}" role="alert">'
            f'<ul class="dj-form-errors__list">{"".join(items)}</ul>'
            f"</div>"
        )


class FieldError(Component):
    """Renders inline validation error for a single form field.

    Usage in a LiveView::

        self.email_error = FieldError(field=my_form["email"])

    In template::

        {{ email_error|safe }}

    Args:
        field: A Django BoundField instance.
        custom_class: Additional CSS classes.
    """

    def __init__(self, field=None, custom_class: str = "", **kwargs):
        super().__init__(field=field, custom_class=custom_class, **kwargs)
        self.field = field
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        if self.field is None:
            return ""

        if hasattr(self.field, "errors"):
            errors = self.field.errors
        else:
            return ""

        if not errors:
            return ""

        cls = "dj-field-error"
        if self.custom_class:
            cls += f" {html_mod.escape(self.custom_class)}"

        items = []
        for err in errors:
            items.append(
                f'<span class="dj-field-error__message">{html_mod.escape(str(err))}</span>'
            )

        return f'<div class="{cls}" role="alert">{"".join(items)}</div>'
