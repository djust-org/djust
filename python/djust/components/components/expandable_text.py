"""ExpandableText component for truncating long text with Read more/Show less toggle."""

import html

from djust import Component


class ExpandableText(Component):
    """Style-agnostic expandable text component using CSS line-clamp.

    Truncates text to a specified number of lines with a "Read more" / "Show less"
    toggle. Uses CSS ``-webkit-line-clamp`` for performant truncation with no JS required.

    Usage in a LiveView::

        self.bio = ExpandableText(
            text="Long bio text here...",
            max_lines=3,
        )

        # With custom labels
        self.desc = ExpandableText(
            text=description,
            max_lines=5,
            more_label="Continue reading",
            less_label="Collapse",
        )

    In template::

        {{ bio|safe }}

    CSS Custom Properties::

        --dj-expandable-text-fg: text color (default: inherit)
        --dj-expandable-text-font-size: font size (default: inherit)
        --dj-expandable-text-line-height: line height (default: 1.5)
        --dj-expandable-text-toggle-color: toggle link color (default: var(--primary, #2563eb))
        --dj-expandable-text-toggle-font-size: toggle font size (default: 0.875rem)

    Args:
        text: Text content to display
        max_lines: Maximum visible lines when collapsed (default: 3)
        expanded: Whether text is currently expanded (default: False)
        toggle_event: djust event to toggle expanded state
        more_label: Label for expand action (default: "Read more")
        less_label: Label for collapse action (default: "Show less")
        custom_class: Additional CSS classes
    """

    def __init__(
        self,
        text: str = "",
        max_lines: int = 3,
        expanded: bool = False,
        toggle_event: str = "toggle_expand",
        more_label: str = "Read more",
        less_label: str = "Show less",
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            text=text,
            max_lines=max_lines,
            expanded=expanded,
            toggle_event=toggle_event,
            more_label=more_label,
            less_label=less_label,
            custom_class=custom_class,
            **kwargs,
        )
        self.text = text
        self.max_lines = max_lines
        self.expanded = expanded
        self.toggle_event = toggle_event
        self.more_label = more_label
        self.less_label = less_label
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        cls = "dj-expandable-text"
        if self.expanded:
            cls += " dj-expandable-text--expanded"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"

        e_text = html.escape(self.text)
        e_event = html.escape(self.toggle_event)
        e_more = html.escape(self.more_label)
        e_less = html.escape(self.less_label)
        max_lines = int(self.max_lines)

        if self.expanded:
            style = ""
            label = e_less
        else:
            style = (
                f' style="-webkit-line-clamp:{max_lines};'
                f'display:-webkit-box;-webkit-box-orient:vertical;overflow:hidden"'
            )
            label = e_more

        return (
            f'<div class="{cls}">'
            f'<div class="dj-expandable-text__content"{style}>{e_text}</div>'
            f'<button class="dj-expandable-text__toggle" dj-click="{e_event}">'
            f"{label}</button>"
            f"</div>"
        )
