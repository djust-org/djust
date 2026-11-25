"""Template-path stand-ins for the hybrid sweeps (#4002).

Until #4002 ``ui.Avatar`` and ``ui.TextArea`` also had an inline ``template``, and the hybrid
(template) sweeps of #3900 / #3914 drove the template path through them: ``Avatar``'s
``<img src>`` was the shipped judged URL slot, ``TextArea``'s ``<textarea>`` text the shipped
raw-text slot. #4002 gave both one renderer (``_render_custom``), and no shipped template has
either slot any more. These two classes keep exactly those templates so the sweeps still drive
a URL slot and a raw-text slot through ``Component.render``'s template path.
"""

from __future__ import annotations

from typing import Any, Optional

from djust.components.base import Component


class TemplateAvatar(Component):
    """``ui.Avatar``'s template before #4002: a URL slot, attribute slots and a text slot."""

    template = """<div class="avatar avatar-{{ size }}{% if shape == "square" %} avatar-square{% endif %}{% if status %} avatar-{{ status }}{% endif %}">{% if src %}
    <img src="{{ src }}" alt="{{ alt }}" class="avatar-img">{% endif %}{% if initials %}
    <span class="avatar-initials">{{ initials }}</span>{% endif %}{% if status %}
    <span class="avatar-status"></span>{% endif %}
</div>"""

    def __init__(
        self,
        src: Optional[str] = None,
        alt: str = "",
        initials: Optional[str] = None,
        size: str = "md",
        shape: str = "circle",
        status: Optional[str] = None,
    ) -> None:
        super().__init__(src=src, alt=alt, initials=initials, size=size, shape=shape, status=status)

    def get_context_data(self) -> dict[str, Any]:
        return {k: self.state[k] for k in ("src", "alt", "initials", "size", "shape", "status")}


class TemplateTextArea(Component):
    """``ui.TextArea``'s template before #4002: a raw-text (``<textarea>``) slot."""

    template = """<div class="mb-3">{% if label %}
    <label for="{{ textarea_id }}" class="form-label">{{ label }}{% if required %} <span class="text-danger">*</span>{% endif %}</label>{% endif %}
    <textarea class="form-control{% if validation_state == "valid" %} is-valid{% endif %}{% if validation_state == "invalid" %} is-invalid{% endif %}"
              id="{{ textarea_id }}"
              name="{{ name }}"
              rows="{{ rows }}"{% if placeholder %} placeholder="{{ placeholder }}"{% endif %}{% if required %} required{% endif %}{% if disabled %} disabled{% endif %}{% if readonly %} readonly{% endif %}>{{ value }}</textarea>{% if help_text %}
    <div class="form-text">{{ help_text }}</div>{% endif %}{% if validation_message %}
    <div class="{% if validation_state == "valid" %}valid{% else %}invalid{% endif %}-feedback">{{ validation_message }}</div>{% endif %}
</div>"""

    def __init__(
        self,
        name: str,
        id: Optional[str] = None,
        label: Optional[str] = None,
        value: str = "",
        placeholder: Optional[str] = None,
        help_text: Optional[str] = None,
        rows: int = 3,
        required: bool = False,
        disabled: bool = False,
        readonly: bool = False,
        validation_state: Optional[str] = None,
        validation_message: Optional[str] = None,
    ) -> None:
        super().__init__(
            name=name,
            id=id or name,
            label=label,
            value=value,
            placeholder=placeholder,
            help_text=help_text,
            rows=rows,
            required=required,
            disabled=disabled,
            readonly=readonly,
            validation_state=validation_state,
            validation_message=validation_message,
        )

    def get_context_data(self) -> dict[str, Any]:
        context = dict(self.state)
        context["textarea_id"] = self.id
        return context


#: ``key -> class``, keyed like ``url_sweep.component_classes()``.
STAND_INS: dict[str, type] = {
    f"{cls.__module__}.{cls.__name__}": cls for cls in (TemplateAvatar, TemplateTextArea)
}
