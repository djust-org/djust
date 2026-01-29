"""
Throttle Demo - demonstrates @throttle decorator with scroll events
"""

from djust import LiveView
from djust.decorators import event, throttle


class ThrottleScrollView(LiveView):
    """
    Demonstrates @throttle decorator with scroll events.

    Features:
    - Throttles scroll events to max 10/second (100ms interval)
    - Shows scroll position and update count
    - Demonstrates leading + trailing execution

    Note: This demo uses a full HTML template (not BaseViewWithNavbar)
    to demonstrate scroll tracking with custom styling.
    """

    template_name = "demos/throttle.html"

    def mount(self, request):
        """Initialize state"""
        self.scroll_y = 0
        self.update_count = 0

    @event
    @throttle(interval=0.1, leading=True, trailing=True)
    def on_scroll(self, scroll_y: int = 0, **kwargs):
        """
        Handle scroll events with throttling.

        Throttled to execute max 10 times per second (100ms interval).
        - leading=True: Execute on first scroll event
        - trailing=True: Execute final position after scrolling stops
        """
        self.scroll_y = scroll_y
        self.update_count += 1
