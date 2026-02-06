"""
PWA Demo - Progressive Web App features with djust LiveView

Demonstrates:
- PWAMixin providing PWA config to context
- Template tags: djust_pwa_manifest, djust_sw_register, djust_offline_indicator
- Offline indicator rendering
- LiveView interactivity alongside PWA features
"""

import json
import time

from djust.decorators import event_handler
from djust.pwa import PWAMixin
from djust_shared.views import BaseViewWithNavbar


class PWADemoView(PWAMixin, BaseViewWithNavbar):
    """
    PWA demo showcasing Progressive Web App integration with LiveView.

    Proves:
    - PWAMixin merges view-level + global PWA config
    - Template tags render manifest, SW registration, offline indicator
    - LiveView event handlers work alongside PWA features
    """

    template_name = "demos/pwa_demo.html"

    # Bounds for demo safety
    MAX_NOTES = 50
    MAX_TEXT_LENGTH = 500

    # PWA configuration (overrides defaults from PWAMixin)
    pwa_name = "djust PWA Demo"
    pwa_short_name = "djust Demo"
    pwa_description = "A demo showcasing djust PWA capabilities"
    pwa_theme_color = "#6366f1"
    pwa_background_color = "#f8fafc"
    pwa_display = "standalone"

    def mount(self, request, **kwargs):
        from djust_shared.components.ui import HeroSection, BackButton

        # State — simple notes list to prove LiveView + PWA work together
        self.notes = []
        self.note_counter = 0

        # Render components to HTML strings for reliable VDOM serialization
        self.hero_html = HeroSection(
            title="PWA Demo",
            subtitle="Progressive Web App features with djust LiveView",
            icon="📱",
        ).render()
        self.back_btn_html = BackButton(href="/demos/").render()

    @event_handler
    def add_note(self, text: str = "", **kwargs):
        """Add a note to the list."""
        text = text.strip()
        if text:
            text = text[:self.MAX_TEXT_LENGTH]
            self.note_counter += 1
            self.notes.append({
                "id": self.note_counter,
                "text": text,
                "time": time.strftime("%H:%M:%S"),
            })
            # Cap notes list — drop oldest on overflow
            if len(self.notes) > self.MAX_NOTES:
                self.notes = self.notes[-self.MAX_NOTES:]

    @event_handler
    def clear_notes(self, **kwargs):
        """Clear all notes."""
        self.notes = []
        self.note_counter = 0

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # PWAMixin already adds pwa_config to context
        # Add a formatted JSON preview for display
        pwa_config = context.get("pwa_config", {})
        self.manifest_preview = json.dumps(pwa_config, indent=2)
        context["manifest_preview"] = self.manifest_preview

        # Render PWA head HTML via Django's template engine and pass as
        # context variable.  The Rust VDOM engine can't reliably process
        # inclusion tags like {% djust_pwa_head %} in {% block extra_head %}
        # (arg-splitting breaks quoted values with spaces), so we
        # pre-render and inject with {{ pwa_head_html|safe }}.
        from django.template import Template, Context as DjangoContext

        pwa_head_tpl = (
            '{%% load djust_pwa %%}'
            '{%% djust_pwa_head name="%s" theme_color="%s" %%}'
            % (self.pwa_name, self.pwa_theme_color)
        )
        self.pwa_head_html = Template(pwa_head_tpl).render(DjangoContext({}))
        context["pwa_head_html"] = self.pwa_head_html

        return context
