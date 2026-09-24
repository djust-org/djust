"""
Card component for djust.

Provides card/panel layout with optional title, body, footer, and image.
"""

from typing import Dict, Any
from ..base import LiveComponent
from ..utils import url_attr
from django.utils.html import conditional_escape
from django.utils.safestring import SafeString, mark_safe


class CardComponent(LiveComponent):
    """
    Pre-built card component.

    A versatile container component for grouping related content. Supports
    title, body text, footer, and optional header image. Adapts to the
    configured CSS framework.

    Usage:
        from djust.components import CardComponent

        # In your LiveView:
        def mount(self, request):
            self.user_card = CardComponent(
                title="User Profile",
                body="User information goes here",
                footer="Last updated: 2 days ago",
                image="/static/profile.jpg"
            )

        # In template:
        {{ user_card.render }}
    """

    template_name = None  # Uses inline rendering

    def mount(self, **kwargs: Any) -> None:
        """Initialize card state"""
        self.title = kwargs.get("title", "")
        self.body = kwargs.get("body", "")
        self.footer = kwargs.get("footer", "")
        self.image = kwargs.get("image", "")

    def get_context(self) -> Dict[str, Any]:
        """Get card context"""
        return {
            "title": self.title,
            "body": self.body,
            "footer": self.footer,
            "image": self.image,
        }

    def render(self) -> SafeString:
        """Render card with inline HTML"""
        from ...config import config

        framework = config.get("css_framework", "bootstrap5")

        if framework == "bootstrap5":
            return mark_safe(self._render_bootstrap())
        elif framework == "tailwind":
            return mark_safe(self._render_tailwind())
        else:
            return mark_safe(self._render_plain())

    def _render_bootstrap(self) -> str:
        """Render Bootstrap 5 card"""
        html = f'<div class="card" id="{conditional_escape(self.component_id)}">'

        if self.image:
            html += f'<img src="{url_attr(self.image, image=True)}" class="card-img-top" alt="{conditional_escape(self.title)}">'

        html += '<div class="card-body">'

        if self.title:
            html += f'<h5 class="card-title">{conditional_escape(self.title)}</h5>'

        if self.body:
            html += f'<p class="card-text">{conditional_escape(self.body)}</p>'

        html += "</div>"

        if self.footer:
            html += f'<div class="card-footer text-muted">{conditional_escape(self.footer)}</div>'

        html += "</div>"
        return html

    def _render_tailwind(self) -> str:
        """Render Tailwind CSS card"""
        html = f'<div class="overflow-hidden rounded-lg bg-white shadow" id="{conditional_escape(self.component_id)}">'

        if self.image:
            html += f'<img src="{url_attr(self.image, image=True)}" class="w-full" alt="{conditional_escape(self.title)}">'

        html += '<div class="px-4 py-5 sm:p-6">'

        if self.title:
            html += f'<h3 class="text-lg font-medium leading-6 text-gray-900">{conditional_escape(self.title)}</h3>'

        if self.body:
            html += f'<div class="mt-2 text-sm text-gray-500">{conditional_escape(self.body)}</div>'

        html += "</div>"

        if self.footer:
            html += f'<div class="bg-gray-50 px-4 py-4 text-sm text-gray-500 sm:px-6">{conditional_escape(self.footer)}</div>'

        html += "</div>"
        return html

    def _render_plain(self) -> str:
        """Render plain HTML card"""
        html = f'<div class="card" id="{conditional_escape(self.component_id)}">'

        if self.image:
            html += f'<img src="{url_attr(self.image, image=True)}" alt="{conditional_escape(self.title)}">'

        if self.title:
            html += f"<h3>{conditional_escape(self.title)}</h3>"

        if self.body:
            html += f"<div>{conditional_escape(self.body)}</div>"

        if self.footer:
            html += f"<footer>{conditional_escape(self.footer)}</footer>"

        html += "</div>"
        return html
