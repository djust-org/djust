"""InlineEdit component."""

import html
from djust import Component


class InlineEdit(Component):
    """Inline edit component for in-place text editing.

    Args:
        value: current display value
        name: field name
        event: dj-input event name
        editing: whether currently in edit mode
        edit_event: dj-click event to enter edit mode"""

    def __init__(
        self,
        value: str = "",
        name: str = "",
        event: str = "",
        editing: bool = False,
        edit_event: str = "inline_edit",
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            value=value,
            name=name,
            event=event,
            editing=editing,
            edit_event=edit_event,
            custom_class=custom_class,
            **kwargs,
        )
        self.value = value
        self.name = name
        self.event = event
        self.editing = editing
        self.edit_event = edit_event
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        """Render the inlineedit HTML."""
        cls = "dj-inline-edit"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"
        e_value = html.escape(self.value)
        e_name = html.escape(self.name)
        if self.editing:
            e_event = html.escape(self.event or self.name)
            return (
                f'<span class="{cls} dj-inline-edit--editing">'
                f'<input class="dj-inline-edit__input" type="text" '
                f'name="{e_name}" value="{e_value}" dj-input="{e_event}" autofocus>'
                f"</span>"
            )
        e_edit = html.escape(self.edit_event)
        return (
            f'<span class="{cls}">'
            f'<span class="dj-inline-edit__value" dj-click="{e_edit}">{e_value}</span>'
            f"</span>"
        )
