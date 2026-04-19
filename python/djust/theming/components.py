"""
Theme UI components for djust.

Provides reusable components for theme switching.
"""

from dataclasses import dataclass

from django.template.loader import select_template
from django.utils.safestring import mark_safe

from .manager import ThemeManager, get_theme_config
from .template_resolver import _get_component_candidates, _get_theme_template_candidates


@dataclass
class ThemeSwitcherConfig:
    """Configuration for ThemeSwitcher component."""

    show_presets: bool = True
    show_mode_toggle: bool = True
    show_labels: bool = True
    dropdown_position: str = "bottom-end"  # bottom-start, bottom-end, top-start, top-end
    button_class: str = ""
    dropdown_class: str = ""


class ThemeSwitcher:
    """
    Theme switcher component.

    Renders a dropdown or button group for switching themes and modes.
    """

    def __init__(
        self,
        theme_manager: ThemeManager | None = None,
        config: ThemeSwitcherConfig | None = None,
    ):
        """
        Initialize ThemeSwitcher.

        Args:
            theme_manager: ThemeManager instance (will create one if not provided)
            config: Configuration options
        """
        self.manager = theme_manager or ThemeManager()
        self.config = config or ThemeSwitcherConfig()

    def get_context(self) -> dict:
        """Get context for template rendering."""
        state = self.manager.get_state()
        presets = self.manager.get_available_presets()

        return {
            "theme_state": state,
            "theme_preset": state.preset,
            "theme_mode": state.mode,
            "theme_resolved_mode": state.resolved_mode,
            "presets": presets,
            "show_presets": self.config.show_presets,
            "show_mode_toggle": self.config.show_mode_toggle,
            "show_labels": self.config.show_labels,
            "dropdown_position": self.config.dropdown_position,
            "button_class": self.config.button_class,
            "dropdown_class": self.config.dropdown_class,
            "css_prefix": get_theme_config().get("css_prefix", ""),
        }

    def render(self) -> str:
        """Render the theme switcher component."""
        context = self.get_context()
        state = self.manager.get_state()
        candidates = _get_theme_template_candidates(state.theme, "theme_switcher")
        tmpl = select_template(candidates)
        html = tmpl.render(context)
        return mark_safe(html)

    def __str__(self) -> str:
        """Allow using component directly in templates."""
        return self.render()


class ThemeModeButton:
    """
    Simple theme mode toggle button component.

    Renders via the ``djust_theming/components/theme_mode_button.html`` template.
    """

    def __init__(
        self,
        theme_manager: ThemeManager | None = None,
        button_class: str = "",
        show_label: bool = False,
    ):
        self.manager = theme_manager or ThemeManager()
        self.button_class = button_class
        self.show_label = show_label

    def get_context(self) -> dict:
        state = self.manager.get_state()
        return {
            "theme_mode": state.mode,
            "theme_resolved_mode": state.resolved_mode,
            "button_class": self.button_class,
            "show_label": self.show_label,
            "css_prefix": get_theme_config().get("css_prefix", ""),
        }

    def render(self) -> str:
        """Render the mode toggle button via its Django template."""
        context = self.get_context()
        state = self.manager.get_state()
        candidates = _get_component_candidates(state.theme, "theme_mode_button")
        tmpl = select_template(candidates)
        html = tmpl.render(context)
        return mark_safe(html)

    def __str__(self) -> str:
        return self.render()


class PresetSelector:
    """
    Theme preset selector component.

    Supports three layouts (``dropdown``, ``grid``, ``list``), each rendered
    via its own template under ``djust_theming/components/preset_selector_*.html``.
    """

    def __init__(
        self,
        theme_manager: ThemeManager | None = None,
        show_descriptions: bool = True,
        layout: str = "dropdown",  # dropdown, grid, list
        dropdown_class: str = "",
    ):
        self.manager = theme_manager or ThemeManager()
        self.show_descriptions = show_descriptions
        self.layout = layout
        self.dropdown_class = dropdown_class

    def get_context(self) -> dict:
        state = self.manager.get_state()
        return {
            "current_preset": state.preset,
            "presets": self.manager.get_available_presets(),
            "show_descriptions": self.show_descriptions,
            "layout": self.layout,
            "dropdown_class": self.dropdown_class,
        }

    def render(self) -> str:
        """Render the preset selector via the layout-specific Django template."""
        context = self.get_context()

        if self.layout == "dropdown":
            return self._render_dropdown(context)
        elif self.layout == "grid":
            return self._render_grid(context)
        else:
            return self._render_list(context)

    def _render_dropdown(self, context: dict) -> str:
        """Render as dropdown select via ``preset_selector_dropdown.html``."""
        state = self.manager.get_state()
        candidates = _get_component_candidates(state.theme, "preset_selector_dropdown")
        tmpl = select_template(candidates)
        html = tmpl.render(context)
        return mark_safe(html)

    def _render_grid(self, context: dict) -> str:
        """Render as grid of buttons via ``preset_selector_grid.html``."""
        state = self.manager.get_state()
        candidates = _get_component_candidates(state.theme, "preset_selector_grid")
        tmpl = select_template(candidates)
        html = tmpl.render(context)
        return mark_safe(html)

    def _render_list(self, context: dict) -> str:
        """Render as list of radio buttons via ``preset_selector_list.html``."""
        state = self.manager.get_state()
        candidates = _get_component_candidates(state.theme, "preset_selector_list")
        tmpl = select_template(candidates)
        html = tmpl.render(context)
        return mark_safe(html)

    def __str__(self) -> str:
        return self.render()
