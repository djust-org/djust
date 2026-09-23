"""Card component for content containers."""

import html
from typing import Any, Optional

from django.utils.html import conditional_escape
from djust import Component


class Card(Component):
    """Style-agnostic card container using CSS custom properties.

    Provides flexible content container with optional header, footer, and interactive features.

    Usage in a LiveView::

        # Basic card
        self.basic = Card(content="<p>Card content</p>")

        # Card with header and footer
        self.full = Card(
            header="<h3>Card Title</h3>",
            content="<p>Main content</p>",
            footer='<button class="dj-btn">Action</button>',
        )

        # Elevated card with hover effect
        self.elevated = Card(
            content="<p>Stats here</p>",
            variant="elevated",
            hover=True,
        )

        # Clickable card
        self.clickable = Card(
            content="<p>Click me</p>",
            action="card_clicked",
            data={"card_id": "123"},
        )

        # Card with image
        self.image_card = Card(
            image='<img src="/static/image.jpg" alt="Image">',
            content="<h3>Title</h3><p>Description</p>",
            variant="bordered",
        )

    In template::

        {{ basic|safe }}
        {{ full|safe }}

    CSS Custom Properties::

        --dj-card-bg: background color
        --dj-card-border: border color
        --dj-card-radius: border radius
        --dj-card-padding: internal padding
        --dj-card-shadow: box shadow (for elevated variant)

    Args:
        content: Main card content (required)
        header: Optional header content
        footer: Optional footer content
        image: Optional image content (placed above header)
        variant: Style variant (default, bordered, elevated, flat)
        hover: Add hover effect (scale/shadow animation)
        padding: Padding size (none, sm, md, lg)
        action: djust event handler for card click
        data: Data attributes for clickable cards
        custom_class: Additional CSS classes
    """

    def __init__(
        self,
        content: str = "",
        header: Optional[str] = None,
        footer: Optional[str] = None,
        image: Optional[str] = None,
        variant: str = "default",
        hover: bool = False,
        padding: str = "md",
        action: Optional[str] = None,
        data: Optional[dict] = None,
        custom_class: str = "",
        **kwargs: Any,
    ) -> None:
        super().__init__(
            content=content,
            header=header,
            footer=footer,
            image=image,
            variant=variant,
            hover=hover,
            padding=padding,
            action=action,
            data=data,
            custom_class=custom_class,
            **kwargs,
        )
        self.content = content
        self.header = header
        self.footer = footer
        self.image = image
        self.variant = variant
        self.hover = hover
        self.padding = padding
        self.action = action
        self.data = data or {}
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        """Render the card HTML."""
        classes = ["dj-card"]

        # Add variant class
        if self.variant != "default":
            classes.append(f"dj-card-{conditional_escape(self.variant)}")

        # Add hover class
        if self.hover:
            classes.append("dj-card-hover")

        # Add padding class (none = no padding class)
        if self.padding != "none":
            classes.append(f"dj-card-p-{conditional_escape(self.padding)}")

        # Add clickable class if action provided
        if self.action:
            classes.append("dj-card-clickable")

        # Add custom classes
        if self.custom_class:
            classes.append(html.escape(self.custom_class))

        class_str = " ".join(classes)

        # Build attributes
        attrs = [f'class="{class_str}"']

        # Add dj-click action for clickable cards
        ea = self.event_attrs(self.action)
        if ea:
            attrs.append(ea)

        # Add data attributes
        for k, v in self.data.items():
            attrs.append(f'data-{html.escape(k)}="{html.escape(str(v))}"')

        attrs_str = " ".join(attrs)

        # Build card sections
        sections = []

        # Image section
        if self.image:
            sections.append(f'<div class="dj-card-image">{conditional_escape(self.image)}</div>')

        # Header section
        if self.header:
            sections.append(f'<div class="dj-card-header">{conditional_escape(self.header)}</div>')

        # Content section (always present)
        sections.append(f'<div class="dj-card-content">{conditional_escape(self.content)}</div>')

        # Footer section
        if self.footer:
            sections.append(f'<div class="dj-card-footer">{conditional_escape(self.footer)}</div>')

        sections_html = "".join(sections)

        return f"<div {attrs_str}>{sections_html}</div>"
