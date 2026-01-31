"""
Counter Demo - Simple reactive counter showcasing LiveView basics
"""

from djust.decorators import event_handler
from djust_shared.views import BaseViewWithNavbar


class CounterView(BaseViewWithNavbar):
    """
    Simple counter demo - showcases reactive state updates.

    This demonstrates:
    - Instance attributes automatically available in templates
    - Automatic dependency injection
    - Clean separation of state and components
    """
    template_name = "demos/counter.html"

    # Code examples stored as class attributes (easy to maintain!)
    PYTHON_EXAMPLE = '''from djust import LiveView

class CounterView(LiveView):
    template_name = "demos/counter.html"

    def mount(self, request, **kwargs):
        self.count = 0

    def increment(self):
        self.count += 1

    def decrement(self):
        self.count -= 1

    def reset(self):
        self.count = 0'''

    HTML_EXAMPLE = '''<!-- Buttons -->
<button @click="increment" class="btn-primary-modern">
    <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 6v6m0 0v6m0-6h6m-6 0H6"></path>
    </svg>
    <span>Increment</span>
</button>
<button @click="decrement" class="btn-secondary-modern">
    <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M20 12H4"></path>
    </svg>
    <span>Decrement</span>
</button>
<button @click="reset" class="btn-secondary-modern">
    <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"></path>
    </svg>
    <span>Reset</span>
</button>'''

    def mount(self, request, **kwargs):
        from djust_shared.components.ui import CodeBlock, HeroSection, BackButton, FeatureCard, FeatureGrid

        # State
        self.count = 0

        # Components
        self.hero = HeroSection(
            title="Counter Demo",
            subtitle="Real-time reactive counter demonstrating instant state updates via WebSocket",
            icon="⚡"
        )

        self.code = CodeBlock(code=self.PYTHON_EXAMPLE, language="python", filename="views.py")
        self.code_1 = CodeBlock(code=self.HTML_EXAMPLE, language="html", filename="counter.html")

        self.features = FeatureGrid(features=[
            FeatureCard(icon="🔌", title="WebSocket Connection", description="Persistent connection keeps client and server in sync"),
            FeatureCard(icon="⚡", title="Event Handling", description="@click directive sends events to Python methods"),
            FeatureCard(icon="🔄", title="VDOM Updates", description="Minimal DOM patches update only what changed"),
        ])

        self.back_btn = BackButton(href="/demos/")

    @event_handler
    def increment(self):
        self.count += 1

    @event_handler
    def decrement(self):
        self.count -= 1

    @event_handler
    def reset(self):
        self.count = 0
