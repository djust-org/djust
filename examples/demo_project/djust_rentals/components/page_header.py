"""
PageHeader Component

Displays consistent page headers with title, subtitle, icon, and action buttons.
Used at the top of every page for navigation and context.
"""

from djust.components.base import Component
from django.utils.safestring import mark_safe
from typing import List, Dict, Optional


class PageHeader(Component):
    """
    A header component for page titles and actions.

    Features:
    - Lucide icon support
    - Optional subtitle
    - Action buttons (with icons)
    - Responsive layout
    - Full dark/light mode support

    Example:
        header = PageHeader(
            title="Properties",
            subtitle="Manage your rental properties",
            icon="home",
            actions=[
                {"label": "Add Property", "url": "/rentals/properties/add/", "icon": "plus"}
            ]
        )
        # In template: {{ header.render }}
    """

    def __init__(
        self,
        title: str,
        subtitle: Optional[str] = None,
        icon: Optional[str] = None,
        actions: Optional[List[Dict[str, str]]] = None
    ):
        self.title = title
        self.subtitle = subtitle
        self.icon = icon
        self.actions = actions or []

    def render(self) -> str:
        """Render the page header component."""

        # Icon HTML
        icon_html = ""
        if self.icon:
            icon_html = f'<i data-lucide="{self.icon}" class="w-10 h-10 text-primary mr-3"></i>'

        # Subtitle HTML
        subtitle_html = ""
        if self.subtitle:
            subtitle_html = f'<p class="text-muted-foreground text-lg">{self.subtitle}</p>'

        # Actions HTML
        actions_html = ""
        if self.actions:
            buttons = []
            for action in self.actions:
                icon = action.get('icon', 'plus')
                label = action.get('label', 'Action')
                url = action.get('url', '#')
                variant = action.get('variant', 'primary')  # primary, secondary, destructive

                # Button color classes based on variant
                if variant == "destructive":
                    btn_class = "bg-destructive text-destructive-foreground hover:bg-destructive/90"
                elif variant == "secondary":
                    btn_class = "bg-secondary text-secondary-foreground hover:bg-secondary/80"
                else:  # primary
                    btn_class = "bg-primary text-primary-foreground hover:bg-primary/90"

                buttons.append(f'''
                <a href="{url}"
                   data-djust-navigate
                   class="inline-flex items-center gap-2 px-4 py-2 rounded-md font-medium transition-colors {btn_class}">
                    <i data-lucide="{icon}" class="w-4 h-4"></i>
                    <span>{label}</span>
                </a>
                ''')

            actions_html = f'''
            <div class="flex items-center gap-3">
                {"".join(buttons)}
            </div>
            '''

        return mark_safe(f'''
        <div class="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 mb-8">
            <div class="flex items-center">
                {icon_html}
                <div>
                    <h1 class="text-4xl font-bold text-card-foreground mb-1">{self.title}</h1>
                    {subtitle_html}
                </div>
            </div>
            {actions_html}
        </div>
        ''')
