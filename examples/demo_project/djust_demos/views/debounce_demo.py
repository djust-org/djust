"""
Debounce Demo - demonstrates @debounce decorator with search
"""

from djust.decorators import debounce
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

    # Code example stored as class attribute
    PYTHON_EXAMPLE = '''from djust import LiveView
from djust.decorators import debounce

class DebounceSearchView(LiveView):
    template_name = "debounce.html"

    def mount(self, request):
        self.query = ""
        self.search_count = 0

    @debounce(wait=0.5, max_wait=2.0)
    def search(self, value: str = "", **kwargs):
        """Debounced search - waits 500ms after typing stops"""
        self.query = value
        self.search_count += 1'''

    def mount(self, request):
        from djust_shared.components.ui import CodeBlock, HeroSection, BackButton, FeatureCard, FeatureGrid

        # State
        self.query = ""
        self.search_count = 0

        # Components
        self.hero = HeroSection(
            title="Debounce Demo",
            subtitle="Type rapidly - search waits until you stop typing (500ms)",
            icon="⏱️"
        )

        self.code = CodeBlock(code=self.PYTHON_EXAMPLE, language="python", filename="views.py")

        self.features = FeatureGrid(features=[
            FeatureCard(icon="⏱️", title="500ms Wait", description="Waits 500ms after last keystroke before executing"),
            FeatureCard(icon="⏰", title="2s Max Wait", description="Forces execution after 2 seconds maximum to prevent indefinite delays"),
            FeatureCard(icon="📉", title="~90% Reduction", description="Reduces server calls by ~90% for typical typing patterns"),
        ])

        self.back_btn = BackButton(href="/demos/")

    @debounce(wait=0.5, max_wait=2.0)
    def search(self, value: str = "", **kwargs):
        """
        Handle search input with debouncing.

        This will only execute after user stops typing for 500ms,
        or after 2 seconds maximum (max_wait).
        """
        self.query = value
        self.search_count += 1
