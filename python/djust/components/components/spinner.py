"""Spinner component for loading state indicators."""

import html

from typing import Optional

from djust import Component


class Spinner(Component):
    """Style-agnostic spinner component using CSS custom properties.

    Displays an animated loading indicator with optional screen-reader text.

    Usage in a LiveView::

        # Basic spinner
        self.loading = Spinner()

        # Large primary spinner with label
        self.saving = Spinner(size="lg", variant="primary", label="Saving...")

        # Small muted spinner
        self.fetching = Spinner(size="sm", variant="muted")

    In template::

        {{ loading|safe }}
        {{ saving|safe }}

    CSS Custom Properties::

        --dj-spinner-color: spinner color (default: currentColor)
        --dj-spinner-size-sm: small size (default: 1rem)
        --dj-spinner-size-md: medium size (default: 1.5rem)
        --dj-spinner-size-lg: large size (default: 2rem)
        --dj-spinner-border-width: border width (default: 2px)
        --dj-spinner-speed: animation duration (default: 0.6s)

    Args:
        size: Size variant (sm, md, lg)
        variant: Color variant (default, primary, muted)
        label: Screen-reader accessible label (default: "Loading...")
        custom_class: Additional CSS classes
    """

    def __init__(
        self,
        size: str = "md",
        variant: str = "default",
        label: Optional[str] = "Loading...",
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            size=size,
            variant=variant,
            label=label,
            custom_class=custom_class,
            **kwargs,
        )
        self.size = size
        self.variant = variant
        self.label = label
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        """Render the spinner HTML."""
        classes = ["dj-spinner"]

        if self.size != "md":
            classes.append(f"dj-spinner-{self.size}")

        if self.variant != "default":
            classes.append(f"dj-spinner-{self.variant}")

        if self.custom_class:
            classes.append(html.escape(self.custom_class))

        class_str = " ".join(classes)

        sr_label = ""
        e_label = html.escape(self.label) if self.label else ""
        if self.label:
            sr_label = f'<span class="dj-sr-only">{e_label}</span>'

        return f'<span class="{class_str}" role="status" aria-label="{e_label}">{sr_label}</span>'
