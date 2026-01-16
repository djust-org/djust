"""
Radio component for djust.

Simple stateless radio button group with automatic Rust optimization.
"""

from typing import Optional, List, Dict, Any
from ..base import Component


class Radio(Component):
    """
    Radio button group component (Bootstrap 5).

    Features:
    - Group of radio buttons with single selection
    - Label for the group and individual options
    - Inline or stacked layout
    - Disabled options support
    - Pre-selected option
    - Help text support

    Args:
        name: Radio group name attribute (required)
        options: List of dicts with 'value', 'label', and optional 'disabled' keys
        label: Optional group label
        value: Currently selected value
        inline: Use inline layout instead of stacked (default: False)
        help_text: Optional help text shown below the group

    Example:
        >>> radio = Radio(
        ...     name="size",
        ...     label="Select Size",
        ...     options=[
        ...         {'value': 's', 'label': 'Small'},
        ...         {'value': 'm', 'label': 'Medium'},
        ...         {'value': 'l', 'label': 'Large'},
        ...     ],
        ...     value="m"
        ... )
        >>> radio.render()
        '<div class="mb-3">...'

        >>> # With disabled option
        >>> radio = Radio(
        ...     name="plan",
        ...     label="Choose Plan",
        ...     options=[
        ...         {'value': 'free', 'label': 'Free'},
        ...         {'value': 'pro', 'label': 'Pro'},
        ...         {'value': 'enterprise', 'label': 'Enterprise', 'disabled': True},
        ...     ],
        ...     value="free",
        ...     help_text="Pro plan recommended for teams"
        ... )

        >>> # Inline layout
        >>> radio = Radio(
        ...     name="priority",
        ...     label="Priority",
        ...     options=[
        ...         {'value': 'low', 'label': 'Low'},
        ...         {'value': 'medium', 'label': 'Medium'},
        ...         {'value': 'high', 'label': 'High'},
        ...     ],
        ...     inline=True
        ... )
    """

    def __init__(
        self,
        name: str,
        options: List[Dict[str, Any]],
        label: Optional[str] = None,
        value: Optional[str] = None,
        inline: bool = False,
        help_text: Optional[str] = None,
    ):
        # Validate options
        if not options:
            raise ValueError("Radio component requires at least one option")

        for opt in options:
            if "value" not in opt or "label" not in opt:
                raise ValueError("Each option must have 'value' and 'label' keys")

        # Pass kwargs to parent (for potential future Rust implementation)
        super().__init__(
            name=name,
            options=options,
            label=label,
            value=value,
            inline=inline,
            help_text=help_text,
        )

        # Set instance attributes for Python rendering
        self.name = name
        self.options = options
        self.label = label
        self.value = value
        self.inline = inline
        self.help_text = help_text

    def get_context_data(self):
        """Return context for template rendering."""
        return {
            "name": self.name,
            "options": self.options,
            "label": self.label,
            "value": self.value,
            "inline": self.inline,
            "help_text": self.help_text,
        }

    def _render_custom(self) -> str:
        """Pure Python rendering (recommended for components with loops)."""
        parts = ['<div class="mb-3">']

        # Group label
        if self.label:
            parts.append(f'    <label class="form-label">{self.label}</label>')

        # Radio buttons
        for i, option in enumerate(self.options):
            opt_value = option["value"]
            opt_label = option["label"]
            opt_disabled = option.get("disabled", False)

            # Generate unique ID for each radio button
            radio_id = f"{self.name}_{i}"

            # Build form-check classes
            check_classes = ["form-check"]
            if self.inline:
                check_classes.append("form-check-inline")

            parts.append(f'    <div class="{" ".join(check_classes)}">')

            # Build input attributes
            attrs = [
                'class="form-check-input"',
                'type="radio"',
                f'id="{radio_id}"',
                f'name="{self.name}"',
                f'value="{opt_value}"',
            ]

            # Check if this option is selected
            if self.value is not None and str(opt_value) == str(self.value):
                attrs.append("checked")

            if opt_disabled:
                attrs.append("disabled")

            parts.append(f"        <input {' '.join(attrs)}>")
            parts.append(f'        <label class="form-check-label" for="{radio_id}">')
            parts.append(f"            {opt_label}")
            parts.append("        </label>")
            parts.append("    </div>")

        # Help text
        if self.help_text:
            parts.append(f'    <div class="form-text">{self.help_text}</div>')

        parts.append("</div>")

        return "\n".join(parts)
