"""TagInput component."""

import html
import json

from djust import Component


class TagInput(Component):
    """Tag input component for adding/removing tags.

    The hidden form field carries the serialized tag list as **JSON** so
    values containing commas round-trip intact (#949). Server-side, parse
    with ``json.loads(request.POST["<name>"])`` to recover the list.
    Prior versions comma-joined the values, which was ambiguous when a
    tag contained a comma; existing tags without commas still decode
    cleanly under the new format.

    Args:
        name: form field name
        tags: list of current tag strings
        event: dj-click event name
        placeholder: input placeholder
        label: label text"""

    def __init__(
        self,
        name: str = "",
        tags: list = None,
        event: str = "",
        placeholder: str = "Add tag...",
        label: str = "",
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            name=name,
            tags=tags,
            event=event,
            placeholder=placeholder,
            label=label,
            custom_class=custom_class,
            **kwargs,
        )
        self.name = name
        self.tags = tags or []
        self.event = event
        self.placeholder = placeholder
        self.label = label
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        """Render the taginput HTML."""
        tags = self.tags or []
        cls = "tag-input"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"
        e_name = html.escape(self.name)
        e_label = html.escape(self.label)
        e_placeholder = html.escape(self.placeholder)
        dj_event = html.escape(self.event or self.name)
        label_html = f'<label class="form-label">{e_label}</label>' if self.label else ""
        tag_parts = []
        for tag in tags:
            e_tag = html.escape(str(tag))
            tag_parts.append(
                f'<span class="tag-input-tag">{e_tag}'
                f'<button type="button" class="tag-input-remove" '
                f'dj-click="{dj_event}" data-value="remove:{e_tag}">&times;</button>'
                f"</span>"
            )
        # Hidden input carries the serialized tag list under the field name
        # so that form submissions POST the current tags, even though the
        # visible `.tag-input-field` is a transient "type to add" input.
        #
        # Serialized as JSON (#949) so tag values containing commas
        # round-trip intact. `ensure_ascii=True` keeps the HTML attribute
        # ASCII-clean; `html.escape` escapes the `"` characters JSON
        # emits so the attribute is well-formed even for tags containing
        # `<`, `&`, or quotes.
        hidden_value = html.escape(json.dumps([str(t) for t in tags]))
        hidden_html = (
            f'<input type="hidden" name="{e_name}" value="{hidden_value}">' if self.name else ""
        )
        return (
            f'<div class="{cls}">{label_html}'
            f"{hidden_html}"
            f'<div class="tag-input-tags">{"".join(tag_parts)}</div>'
            f'<input type="text" class="tag-input-field" placeholder="{e_placeholder}">'
            f"</div>"
        )
