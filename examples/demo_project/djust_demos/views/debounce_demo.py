"""
Debounce Demo - demonstrates @debounce decorator with search
"""

from djust import state
from djust.decorators import debounce
from djust.decorators import event_handler
from djust_shared.views import BaseViewWithNavbar


class DebounceSearchView(BaseViewWithNavbar):
    """
    Demonstrates @debounce decorator with a search input.

    Features:
    - Debounces search input (waits 500ms after typing stops)
    - Shows search count (should be much lower than keypress count)
    - Demonstrates max_wait forcing execution after 2 seconds
    """

    template_name = "demos/debounce.html"

    # persist="server": both survive the HTTP fallback and a reconnect.
    query = state("", persist="server")
    search_count = state(0, persist="server")

    # Code example stored as class attribute
    PYTHON_EXAMPLE = '''from djust import LiveView, state
from djust.decorators import debounce, event_handler

class DebounceSearchView(LiveView):
    template_name = "debounce.html"

    query = state("", persist="server")
    search_count = state(0, persist="server")

    @event_handler
    @debounce(wait=0.5, max_wait=2.0)
    def search(self, value: str = "", **kwargs):
        """Debounced search - waits 500ms after typing stops"""
        self.query = value
        self.search_count += 1'''

    def get_context_data(self, **kwargs):
        from djust_shared.components.ui import CodeBlock, HeroSection, BackButton, FeatureCard, FeatureGrid

        # Presentation components: built per render, never part of the view's state.
        return super().get_context_data(
            hero=HeroSection(
                title="Debounce Demo",
                subtitle="Type rapidly - search waits until you stop typing (500ms)",
                icon="⏱️",
            ),
            code=CodeBlock(code=self.PYTHON_EXAMPLE, language="python", filename="views.py"),
            features=FeatureGrid(features=[
                FeatureCard(icon="⏱️", title="500ms Wait", description="Waits 500ms after last keystroke before executing"),
                FeatureCard(icon="⏰", title="2s Max Wait", description="Forces execution after 2 seconds maximum to prevent indefinite delays"),
                FeatureCard(icon="📉", title="~90% Reduction", description="Reduces server calls by ~90% for typical typing patterns"),
            ]),
            back_btn=BackButton(href="/demos/"),
            **kwargs,
        )

    @event_handler
    @debounce(wait=0.5, max_wait=2.0)
    def search(self, value: str = "", **kwargs):
        """
        Handle search input with debouncing.

        This will only execute after user stops typing for 500ms,
        or after 2 seconds maximum (max_wait).
        """
        self.query = value
        self.search_count += 1
