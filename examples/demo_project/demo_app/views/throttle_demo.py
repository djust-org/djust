"""
Throttle Demo - demonstrates @throttle decorator with scroll events
"""

from djust import LiveView
from djust.decorators import throttle


class ThrottleScrollView(LiveView):
    """
    Demonstrates @throttle decorator with scroll events.

    Features:
    - Throttles scroll events to max 10/second (100ms interval)
    - Shows scroll position and update count
    - Demonstrates leading + trailing execution
    """

    template = """
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            .fixed-stats {
                position: fixed;
                top: 20px;
                left: 50%;
                transform: translateX(-50%);
                z-index: 1000;
                background: white;
                padding: 20px;
                border-radius: 8px;
                box-shadow: 0 2px 10px rgba(0,0,0,0.1);
                min-width: 600px;
            }
            .spacer {
                height: 3000px;
                background: linear-gradient(to bottom,
                    #e3f2fd 0%,
                    #bbdefb 25%,
                    #90caf9 50%,
                    #64b5f6 75%,
                    #42a5f5 100%
                );
                padding-top: 150px;
            }
            .scroll-marker {
                text-align: center;
                padding: 20px;
                background: rgba(255,255,255,0.8);
                margin: 100px 0;
                font-size: 24px;
                font-weight: bold;
            }
        </style>
    </head>
    <body>
        <div dj-root>
            <!-- Fixed Stats Panel -->
            <div class="fixed-stats">
                <div class="card border-0">
                    <div class="card-body">
                        <h3 class="mb-3">Throttle Demo - Scroll Events</h3>

                        <div class="row g-3 mb-3">
                            <div class="col-md-6">
                                <div class="card bg-light">
                                    <div class="card-body">
                                        <h6 class="text-muted small mb-1">Scroll Position</h6>
                                        <p class="h4 mb-0">{{ scroll_y }}px</p>
                                    </div>
                                </div>
                            </div>
                            <div class="col-md-6">
                                <div class="card bg-light">
                                    <div class="card-body">
                                        <h6 class="text-muted small mb-1">Server Updates</h6>
                                        <p class="h4 mb-0 text-success">{{ update_count }}</p>
                                        <small class="text-muted">Max 10/second</small>
                                    </div>
                                </div>
                            </div>
                        </div>

                        <div class="alert alert-info mb-0">
                            <h6 class="alert-heading">How to test:</h6>
                            <ol class="mb-2">
                                <li>Open browser console: <code>window.djustDebug = true</code></li>
                                <li>Scroll rapidly up and down</li>
                                <li>Watch console - you'll see throttle logs</li>
                                <li>Notice "Server Updates" limited to ~10/second</li>
                            </ol>
                            <p class="mb-0 small">
                                <strong>Config:</strong> <code>@throttle(interval=0.1, leading=True, trailing=True)</code>
                            </p>
                        </div>
                    </div>
                </div>
            </div>

            <!-- Scrollable Content -->
            <div class="spacer">
                <div class="scroll-marker">⬆️ Scroll to top</div>
                <div class="scroll-marker">Keep scrolling...</div>
                <div class="scroll-marker">Halfway there!</div>
                <div class="scroll-marker">Almost at bottom...</div>
                <div class="scroll-marker">⬇️ Bottom reached!</div>
            </div>
        </div>

        <!-- Scroll Event Handler -->
        <script>
            // Wait for djust to be ready
            document.addEventListener('DOMContentLoaded', function() {
                // Wait a tick for djust initialization
                setTimeout(function() {
                    // Attach scroll listener
                    window.addEventListener('scroll', function() {
                        const scrollY = Math.round(window.scrollY);
                        // Call djustHandleEvent which is exposed by the embedded JS
                        if (window.djustHandleEvent) {
                            window.djustHandleEvent('on_scroll', { scroll_y: scrollY });
                        } else {
                            console.warn('[Throttle Demo] window.djustHandleEvent not available yet');
                        }
                    });

                    console.log('[Throttle Demo] Scroll listener attached');
                    console.log('[Throttle Demo] Try: window.djustDebug = true');
                }, 100);
            });
        </script>
    </body>
    </html>
    """

    def mount(self, request):
        """Initialize state"""
        self.scroll_y = 0
        self.update_count = 0

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
