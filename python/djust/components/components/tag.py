"""Tag/Chip component for labels and categorization."""

import html
from typing import Optional

from djust import Component


class Tag(Component):
    """Style-agnostic tag/chip component using CSS custom properties.

    Displays a compact label with optional color variants and dismiss functionality.

    Usage in a LiveView::

        # Basic tag
        self.tag = Tag("Python")

        # Colored variant
        self.status = Tag("Active", variant="success")

        # Dismissible tag
        self.removable = Tag(
            "filter: open",
            variant="info",
            dismissible=True,
            action="remove_filter",
        )

    In template::

        {{ tag|safe }}
        {{ status|safe }}

    CSS Custom Properties::

        --dj-tag-bg: background color
        --dj-tag-fg: text color
        --dj-tag-border: border color
        --dj-tag-radius: border radius (default: 9999px)
        --dj-tag-padding: internal padding (default: 0.125rem 0.5rem)
        --dj-tag-font-size: text size (default: 0.75rem)

        # Variant-specific colors
        --dj-tag-primary-bg, --dj-tag-primary-fg
        --dj-tag-success-bg, --dj-tag-success-fg
        --dj-tag-info-bg, --dj-tag-info-fg
        --dj-tag-warning-bg, --dj-tag-warning-fg
        --dj-tag-danger-bg, --dj-tag-danger-fg

    Args:
        label: Tag text content
        variant: Color variant (default, primary, success, info, warning, danger)
        size: Size variant (sm, md, lg)
        dismissible: Whether the tag can be dismissed
        action: djust event handler name for dismiss (for dj-click)
        custom_class: Additional CSS classes
    """

    def __init__(
        self,
        label: str,
        variant: str = "default",
        size: str = "md",
        dismissible: bool = False,
        action: Optional[str] = None,
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            label=label,
            variant=variant,
            size=size,
            dismissible=dismissible,
            action=action,
            custom_class=custom_class,
            **kwargs,
        )
        self.label = label
        self.variant = variant
        self.size = size
        self.dismissible = dismissible
        self.action = action
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        """Render the tag HTML."""
        classes = ["dj-tag"]

        if self.variant != "default":
            classes.append(f"dj-tag-{self.variant}")

        if self.size != "md":
            classes.append(f"dj-tag-{self.size}")

        if self.custom_class:
            classes.append(html.escape(self.custom_class))

        class_str = " ".join(classes)
        label_escaped = html.escape(self.label)

        parts = [f'<span class="dj-tag-label">{label_escaped}</span>']

        if self.dismissible:
            dismiss_attrs = ['class="dj-tag-dismiss"', 'aria-label="Remove"']
            if self.action:
                dismiss_attrs.append(f'dj-click="{html.escape(self.action)}"')
            dismiss_str = " ".join(dismiss_attrs)
            parts.append(f"<button {dismiss_str}>&times;</button>")

        content = "".join(parts)
        return f'<span class="{class_str}">{content}</span>'
