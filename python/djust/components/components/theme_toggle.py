"""ThemeToggle component."""

import html
from djust import Component


class ThemeToggle(Component):
    """Light/dark/system theme toggle component.

    Args:
        current: current theme (light, dark, system)
        event: dj-click event name"""

    def __init__(
        self,
        current: str = "system",
        event: str = "",
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            current=current,
            event=event,
            custom_class=custom_class,
            **kwargs,
        )
        self.current = current
        self.event = event
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        """Render the themetoggle HTML."""
        cls = "dj-theme-toggle"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"
        e_current = html.escape(self.current)
        click_attr = f' dj-click="{html.escape(self.event)}"' if self.event else ""
        themes = [
            ("light", "Light", "&#9728;"),
            ("dark", "Dark", "&#9790;"),
            ("system", "System", "&#9881;"),
        ]
        buttons = ""
        for theme, label, icon_char in themes:
            active_cls = " dj-theme-toggle__btn--active" if theme == self.current else ""
            buttons += (
                f'<button type="button" class="dj-theme-toggle__btn{active_cls}" '
                f'data-theme="{theme}" aria-label="{label} theme">{icon_char}</button>'
            )
        return (
            f'<div class="{cls}" data-current="{e_current}"{click_attr} '
            f'role="radiogroup" aria-label="Color theme">'
            f"{buttons}"
            f"</div>"
        )
