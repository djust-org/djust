"""
Optimistic Counter Demo - demonstrates @optimistic with error handling
"""

from djust import LiveView
from djust.decorators import optimistic
import time


class OptimisticCounterView(LiveView):
    """
    Demonstrates @optimistic with error handling.

    Features:
    - Instant increment/decrement
    - Server-side validation (prevent negative)
    - Error handling reverts optimistic update
    - Shake animation on error
    """

    template = """
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            .demo-container {
                max-width: 600px;
                margin: 50px auto;
            }
            .counter-display {
                font-size: 120px;
                font-weight: bold;
                text-align: center;
                padding: 60px 0;
                color: #28a745;
                background: #f8f9fa;
                border-radius: 16px;
                margin: 30px 0;
            }
            .btn-group-custom {
                display: flex;
                gap: 20px;
                justify-content: center;
                margin: 30px 0;
            }
            .btn-custom {
                width: 100px;
                height: 100px;
                font-size: 48px;
                font-weight: bold;
                border-radius: 50%;
                border: none;
                cursor: pointer;
                transition: all 0.3s ease;
            }
            .btn-custom:hover:not(:disabled) {
                transform: scale(1.1);
            }
            .btn-custom:active:not(:disabled) {
                transform: scale(0.95);
            }
            .btn-decrement {
                background: #dc3545;
                color: white;
            }
            .btn-increment {
                background: #28a745;
                color: white;
            }
            .error-message {
                text-align: center;
                color: #dc3545;
                font-weight: bold;
                font-size: 18px;
                min-height: 30px;
                margin: 20px 0;
            }
        </style>
    </head>
    <body>
        <div data-djust-root class="demo-container">
            <div class="card">
                <div class="card-header bg-primary text-white">
                    <h3 class="mb-0">Optimistic Updates - Counter with Validation</h3>
                    <p class="mb-0 small">Click buttons rapidly - watch instant response</p>
                </div>
                <div class="card-body">
                    <div class="counter-display">
                        {{ count }}
                    </div>

                    <div class="btn-group-custom">
                        <button
                            class="btn-custom btn-decrement"
                            @click="decrement"
                            data-loading-text="..."
                        >-</button>
                        <button
                            class="btn-custom btn-increment"
                            @click="increment"
                            data-loading-text="..."
                        >+</button>
                    </div>

                    <div class="error-message">
                        {% if error %}
                        ⚠️ {{ error }}
                        {% endif %}
                    </div>

                    <div class="alert alert-info">
                        <h5 class="alert-heading">How to test:</h5>
                        <ol class="mb-2">
                            <li>Click + button multiple times - instant feedback</li>
                            <li>Try to go below 0 - optimistic update reverts</li>
                            <li>Watch for shake animation on error</li>
                            <li>Open console: <code>window.djustDebug = true</code></li>
                            <li>See optimistic update and revert logs</li>
                        </ol>
                        <p class="mb-0 small">
                            <strong>Validation:</strong> Counter cannot go below 0<br>
                            <strong>Error handling:</strong> Optimistic update reverts with shake animation
                        </p>
                    </div>

                    <div class="alert alert-warning mt-3">
                        <h6 class="alert-heading">Try this:</h6>
                        <p class="mb-0">
                            1. Decrement to 0<br>
                            2. Click - button again<br>
                            3. Watch the button shake and revert<br>
                            4. Error message appears briefly
                        </p>
                    </div>
                </div>
            </div>
        </div>
    </body>
    </html>
    """

    def mount(self, request):
        """Initialize counter state"""
        self.count = 5
        self.error = None

    @optimistic
    def increment(self, **kwargs):
        """
        Increment counter.

        Always succeeds - button shows instant feedback.
        """
        self.count += 1
        self.error = None

        # Simulate network delay (comment out for instant updates)
        # time.sleep(0.3)

    @optimistic
    def decrement(self, **kwargs):
        """
        Decrement counter (with validation).

        Server rejects if count would go below 0.
        This causes the optimistic update to revert.
        """
        # Clear previous error
        self.error = None

        # Simulate network delay (comment out for instant updates)
        # time.sleep(0.3)

        # Validate: cannot go below 0
        if self.count <= 0:
            self.error = "Cannot go below zero!"
            # Don't change state - causes optimistic update to revert
            return

        # Success: update state
        self.count -= 1
