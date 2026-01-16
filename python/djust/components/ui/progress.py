"""
Progress component for djust.

Provides progress bars for showing completion status.
"""

from typing import Dict, Any
from ..base import LiveComponent
from django.utils.safestring import mark_safe


class ProgressComponent(LiveComponent):
    """
    Progress bar component for showing task completion.

    Displays a progress bar with customizable value, variant, size,
    labels, and striped/animated effects.

    Usage:
        from djust.components import ProgressComponent

        # In your LiveView:
        def mount(self, request):
            self.upload_progress = ProgressComponent(
                value=45,
                max_value=100,
                variant="success",
                show_label=True,
                striped=True,
                animated=True
            )

        def update_progress(self, value: int):
            self.upload_progress.set_value(value)

        # In template:
        {{ upload_progress.render }}
    """

    template_name = None  # Uses inline rendering

    def mount(self, **kwargs):
        """Initialize progress state"""
        self.value = kwargs.get("value", 0)
        self.max_value = kwargs.get("max_value", 100)
        self.min_value = kwargs.get("min_value", 0)
        self.variant = kwargs.get(
            "variant", "primary"
        )  # primary, secondary, success, danger, warning, info
        self.size = kwargs.get("size", "md")  # sm, md, lg
        self.show_label = kwargs.get("show_label", False)  # Show percentage
        self.custom_label = kwargs.get("custom_label", None)  # Custom label text
        self.striped = kwargs.get("striped", False)
        self.animated = kwargs.get("animated", False)  # Animated stripes
        self.height = kwargs.get("height", None)  # Custom height

    def get_context(self) -> Dict[str, Any]:
        """Get progress context"""
        return {
            "value": self.value,
            "max_value": self.max_value,
            "min_value": self.min_value,
            "percentage": self.get_percentage(),
            "variant": self.variant,
        }

    def get_percentage(self) -> int:
        """Calculate percentage value"""
        if self.max_value == self.min_value:
            return 0
        return int((self.value - self.min_value) / (self.max_value - self.min_value) * 100)

    def set_value(self, value: float):
        """Update progress value"""
        self.value = max(self.min_value, min(value, self.max_value))
        self.trigger_update()

    def increment(self, amount: float = 1):
        """Increment progress value"""
        self.set_value(self.value + amount)

    def reset(self):
        """Reset progress to minimum"""
        self.value = self.min_value
        self.trigger_update()

    def complete(self):
        """Set progress to maximum"""
        self.value = self.max_value
        self.trigger_update()

    def render(self) -> str:
        """Render progress with inline HTML"""
        from ...config import config

        framework = config.get("css_framework", "bootstrap5")

        if framework == "bootstrap5":
            return mark_safe(self._render_bootstrap())
        elif framework == "tailwind":
            return mark_safe(self._render_tailwind())
        else:
            return mark_safe(self._render_plain())

    def _render_bootstrap(self) -> str:
        """Render Bootstrap 5 progress"""
        percentage = self.get_percentage()

        # Size mapping
        size_map = {"sm": "0.5rem", "md": "1rem", "lg": "1.5rem"}
        height = self.height or size_map.get(self.size, "1rem")

        # Progress container
        html = f'<div class="progress" id="{self.component_id}" style="height: {height};">'

        # Progress bar classes
        classes = f"progress-bar bg-{self.variant}"
        if self.striped:
            classes += " progress-bar-striped"
        if self.animated:
            classes += " progress-bar-animated"

        # Label
        label = ""
        if self.custom_label:
            label = self.custom_label
        elif self.show_label:
            label = f"{percentage}%"

        html += f'<div class="{classes}" role="progressbar" style="width: {percentage}%" '
        html += f'aria-valuenow="{self.value}" aria-valuemin="{self.min_value}" aria-valuemax="{self.max_value}">'
        html += label
        html += "</div></div>"

        return html

    def _render_tailwind(self) -> str:
        """Render Tailwind CSS progress"""
        percentage = self.get_percentage()

        # Size mapping
        size_map = {"sm": "h-2", "md": "h-4", "lg": "h-6"}
        height_class = size_map.get(self.size, "h-4")

        # Variant colors
        variant_map = {
            "primary": "bg-blue-600",
            "secondary": "bg-gray-600",
            "success": "bg-green-600",
            "danger": "bg-red-600",
            "warning": "bg-yellow-500",
            "info": "bg-cyan-600",
        }
        color_class = variant_map.get(self.variant, "bg-blue-600")

        # Custom height
        style = f' style="height: {self.height}"' if self.height else ""

        # Progress container
        html = f'<div class="w-full bg-gray-200 rounded-full {height_class}" id="{self.component_id}"{style}>'

        # Progress bar
        bar_classes = f"{color_class} {height_class} rounded-full transition-all duration-300"
        if self.striped or self.animated:
            bar_classes += " bg-gradient-to-r from-transparent via-white/20 to-transparent bg-[length:20px_100%]"
        if self.animated:
            bar_classes += " animate-[shimmer_1s_linear_infinite]"

        html += f'<div class="{bar_classes}" style="width: {percentage}%"'
        html += f' role="progressbar" aria-valuenow="{self.value}" aria-valuemin="{self.min_value}" aria-valuemax="{self.max_value}">'

        # Label
        if self.custom_label or self.show_label:
            label = self.custom_label or f"{percentage}%"
            html += f'<span class="flex items-center justify-center h-full text-xs font-medium text-white px-2">{label}</span>'

        html += "</div></div>"

        # Add shimmer animation style if needed
        if self.animated:
            html = (
                "<style>@keyframes shimmer {0% {background-position: -100% 0;} 100% {background-position: 100% 0;}}</style>"
                + html
            )

        return html

    def _render_plain(self) -> str:
        """Render plain HTML progress"""
        percentage = self.get_percentage()

        # Size mapping
        size_map = {"sm": "10px", "md": "20px", "lg": "30px"}
        height = self.height or size_map.get(self.size, "20px")

        html = f'<div class="progress" id="{self.component_id}" style="height: {height};">'

        # Progress bar
        bar_class = f"progress-bar progress-bar-{self.variant}"
        if self.striped:
            bar_class += " progress-bar-striped"

        # Label
        label = ""
        if self.custom_label:
            label = self.custom_label
        elif self.show_label:
            label = f"{percentage}%"

        html += f'<div class="{bar_class}" style="width: {percentage}%" '
        html += f'role="progressbar" aria-valuenow="{self.value}" aria-valuemin="{self.min_value}" aria-valuemax="{self.max_value}">'
        html += label
        html += "</div></div>"

        return html
