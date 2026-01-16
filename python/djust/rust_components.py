"""
Rust-backed UI components for djust.

This module provides high-performance, type-safe UI components implemented in Rust
with a Pythonic API. Components automatically render to Bootstrap5, Tailwind, or Plain HTML
based on your framework configuration.

Example:
    >>> from djust.rust_components import Button
    >>> btn = Button("my-btn", "Click Me", variant="primary", size="large")
    >>> html = btn.render()  # Renders to Bootstrap5 by default
    >>>
    >>> # Builder pattern
    >>> btn2 = Button("btn2", "Submit").with_variant("success").with_icon("✓")
"""

from typing import Optional, Literal

try:
    from djust._rust import RustButton

    _rust_available = True
except ImportError:
    _rust_available = False
    RustButton = None


class ComponentNotAvailableError(ImportError):
    """Raised when Rust components are not available (not compiled)."""

    pass


def _check_rust_available():
    """Check if Rust components are available."""
    if not _rust_available:
        raise ComponentNotAvailableError(
            "Rust components are not available. "
            "Make sure the package was built with: maturin develop --features python"
        )


class Button:
    """
    High-performance button component backed by Rust.

    Supports multiple variants, sizes, and frameworks (Bootstrap5, Tailwind, Plain HTML).

    Args:
        id: Unique component ID
        label: Button text
        variant: Button style (primary, secondary, success, danger, warning, info, light, dark, link)
        size: Button size (sm/small, md/medium, lg/large)
        outline: Use outline style (default: False)
        disabled: Disable the button (default: False)
        full_width: Make button full width (default: False)
        icon: Optional icon HTML/text
        on_click: Optional click event handler name
        button_type: HTML button type (default: "button")

    Example:
        >>> btn = Button(
        ...     "submit-btn",
        ...     "Submit Form",
        ...     variant="success",
        ...     size="large",
        ...     on_click="handle_submit"
        ... )
        >>> html = btn.render()

        >>> # Builder pattern
        >>> btn2 = Button("btn", "Click").with_variant("danger").with_disabled(True)
    """

    def __init__(
        self,
        id: str,
        label: str,
        variant: Literal[
            "primary", "secondary", "success", "danger", "warning", "info", "light", "dark", "link"
        ] = "primary",
        size: Literal["sm", "small", "md", "medium", "lg", "large"] = "medium",
        outline: bool = False,
        disabled: bool = False,
        full_width: bool = False,
        icon: Optional[str] = None,
        on_click: Optional[str] = None,
        button_type: str = "button",
    ):
        _check_rust_available()

        kwargs = {}
        if variant != "primary":
            kwargs["variant"] = variant
        if size not in ("md", "medium"):
            kwargs["size"] = size
        if outline:
            kwargs["outline"] = outline
        if disabled:
            kwargs["disabled"] = disabled
        if full_width:
            kwargs["full_width"] = full_width
        if icon:
            kwargs["icon"] = icon
        if on_click:
            kwargs["on_click"] = on_click
        if button_type != "button":
            kwargs["button_type"] = button_type

        self._inner = RustButton(id, label, **kwargs)

    @property
    def id(self) -> str:
        """Get component ID."""
        return self._inner.id

    @property
    def label(self) -> str:
        """Get button label."""
        return self._inner.label

    @label.setter
    def label(self, value: str):
        """Set button label."""
        self._inner.label = value

    @property
    def disabled(self) -> bool:
        """Get disabled state."""
        return self._inner.disabled

    @disabled.setter
    def disabled(self, value: bool):
        """Set disabled state."""
        self._inner.disabled = value

    def set_variant(self, variant: str) -> None:
        """
        Set button variant.

        Args:
            variant: One of primary, secondary, success, danger, warning, info, light, dark, link
        """
        self._inner.variant(variant)

    def render(self) -> str:
        """
        Render button to HTML using the configured framework.

        Returns:
            HTML string
        """
        return self._inner.render()

    def render_with_framework(self, framework: Literal["bootstrap5", "tailwind", "plain"]) -> str:
        """
        Render button with a specific framework.

        Args:
            framework: Framework to use (bootstrap5, tailwind, or plain)

        Returns:
            HTML string
        """
        return self._inner.render_with_framework(framework)

    # Builder pattern methods
    def with_variant(self, variant: str) -> "Button":
        """
        Set variant and return self for chaining.

        Args:
            variant: Button variant

        Returns:
            Self for chaining
        """
        self._inner = self._inner.with_variant(variant)
        return self

    def with_size(self, size: str) -> "Button":
        """
        Set size and return self for chaining.

        Args:
            size: Button size (sm, md, lg)

        Returns:
            Self for chaining
        """
        self._inner = self._inner.with_size(size)
        return self

    def with_outline(self, outline: bool) -> "Button":
        """
        Set outline style and return self for chaining.

        Args:
            outline: Whether to use outline style

        Returns:
            Self for chaining
        """
        self._inner = self._inner.with_outline(outline)
        return self

    def with_disabled(self, disabled: bool) -> "Button":
        """
        Set disabled state and return self for chaining.

        Args:
            disabled: Whether button is disabled

        Returns:
            Self for chaining
        """
        self._inner = self._inner.with_disabled(disabled)
        return self

    def with_icon(self, icon: str) -> "Button":
        """
        Set icon and return self for chaining.

        Args:
            icon: Icon HTML or text

        Returns:
            Self for chaining
        """
        self._inner = self._inner.with_icon(icon)
        return self

    def with_on_click(self, handler: str) -> "Button":
        """
        Set click handler and return self for chaining.

        Args:
            handler: Event handler name

        Returns:
            Self for chaining
        """
        self._inner = self._inner.with_on_click(handler)
        return self

    def __repr__(self) -> str:
        return repr(self._inner)


__all__ = [
    "Button",
    "ComponentNotAvailableError",
]
