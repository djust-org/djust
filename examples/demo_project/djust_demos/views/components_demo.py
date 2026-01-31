"""
Demo view showcasing the new unified Component system with automatic Rust optimization.

This demonstrates:
1. Simple stateless Badge and Button components
2. Hybrid rendering (template_string with Rust template engine)
3. Automatic performance optimization
4. Multiple components rendered efficiently
"""

from djust import LiveView
from djust.decorators import event_handler
from djust.components.ui import Badge, Button


class ComponentsDemoView(LiveView):
    """
    Demo view showing the new unified Component system.

    Features:
    - Simple Badge components (stateless)
    - Simple Button components (stateless)
    - Multiple components (tests performance)
    - Automatic Rust optimization if available
    """

    template = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>djust Components Demo</title>
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
        <!-- Syntax highlighting -->
        <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github.min.css">
        <style>
            body { padding: 20px; }
            .demo-section { margin-bottom: 40px; }
            .component-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; }
            .performance-note { background: #f8f9fa; padding: 15px; border-left: 4px solid #0d6efd; margin: 20px 0; }
            pre code.hljs { padding: 1em; border-radius: 4px; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>djust Unified Component System</h1>
            <p class="lead">Demonstrating automatic performance optimization: Rust → Hybrid → Python</p>

            <div class="performance-note">
                <strong>Performance:</strong> These components automatically use the fastest available rendering:
                <ul class="mb-0 mt-2">
                    <li><strong>Pure Rust</strong> (if compiled): ~1μs per component ⚡</li>
                    <li><strong>Hybrid template</strong> (Rust engine): ~5-10μs per component ✅</li>
                    <li><strong>Python fallback</strong>: ~50-100μs per component 🐍</li>
                </ul>
            </div>

            <!-- Badge Demos -->
            <div class="demo-section">
                <h2>Badge Component</h2>
                <p>Simple, stateless badges with automatic Rust optimization</p>

                <h4>Basic Badges</h4>
                <div class="mb-3">
                    {{ badge_primary }} {{ badge_secondary }} {{ badge_success }}
                    {{ badge_danger }} {{ badge_warning }} {{ badge_info }}
                </div>

                <h4>Pill Badges</h4>
                <div class="mb-3">
                    {{ badge_pill_primary }} {{ badge_pill_success }} {{ badge_pill_danger }}
                </div>

                <h4>Sized Badges</h4>
                <div class="mb-3">
                    {{ badge_sm }} {{ badge_md }} {{ badge_lg }}
                </div>
            </div>

            <!-- Button Demos -->
            <div class="demo-section">
                <h2>Button Component</h2>
                <p>Simple, stateless buttons with automatic Rust optimization</p>

                <h4>Basic Buttons</h4>
                <div class="mb-3">
                    {{ btn_primary }} {{ btn_secondary }} {{ btn_success }}
                    {{ btn_danger }} {{ btn_warning }} {{ btn_info }}
                </div>

                <h4>Outline Buttons</h4>
                <div class="mb-3">
                    {{ btn_outline_primary }} {{ btn_outline_success }} {{ btn_outline_danger }}
                </div>

                <h4>Sized Buttons</h4>
                <div class="mb-3">
                    {{ btn_sm }} {{ btn_md }} {{ btn_lg }}
                </div>

                <h4>Disabled Button</h4>
                <div class="mb-3">
                    {{ btn_disabled }}
                </div>
            </div>

            <!-- Performance Test -->
            <div class="demo-section">
                <h2>Performance Test: Many Components</h2>
                <p>Rendering {{ badge_count }} badges to test performance...</p>

                <div class="component-grid">
                    {% for badge in many_badges %}
                        {{ badge }}
                    {% endfor %}
                </div>

                <p class="mt-3 text-muted">
                    <small>
                        Expected render time:
                        Pure Rust: ~{{ badge_count }}μs (~0.{{ badge_count_ms }}ms) |
                        Hybrid: ~{{ badge_count_hybrid }}μs (~{{ badge_count_hybrid_ms }}ms) |
                        Python: ~{{ badge_count_python }}μs (~{{ badge_count_python_ms }}ms)
                    </small>
                </p>
            </div>

            <!-- Interactive Counter (uses LiveView) -->
            <div class="demo-section">
                <h2>Interactive Counter (LiveView)</h2>
                <p>This uses LiveView reactivity, not the simple components above</p>

                <div class="card">
                    <div class="card-body">
                        <h3>Count: {{ count }}</h3>
                        <button @click="increment" class="btn btn-primary">Increment</button>
                        <button @click="decrement" class="btn btn-secondary">Decrement</button>
                        <button @click="reset" class="btn btn-danger">Reset</button>

                        <div class="mt-3">
                            {% if count > 0 %}
                                {{ count_badge }}
                            {% endif %}
                        </div>
                    </div>
                </div>
            </div>

            <!-- Code Examples -->
            <div class="demo-section">
                <h2>Code Examples</h2>

                <h4>Badge Component</h4>
                <pre><code class="language-python">from djust.components.ui import Badge

# Simple usage
badge = Badge("New", variant="primary")
html = badge.render()

# In template: &#123;&#123; badge &#125;&#125;</code></pre>

                <h4>Button Component</h4>
                <pre><code class="language-python">from djust.components.ui import Button

# Simple usage
button = Button("Click me", variant="success", size="lg")
html = button.render()

# In template: &#123;&#123; button &#125;&#125;</code></pre>

                <h4>LiveView with Components</h4>
                <pre><code class="language-python">from djust import LiveView
from djust.components.ui import Badge, Button

class MyView(LiveView):
    def mount(self, request):
        # Create components
        self.status_badge = Badge("Active", variant="success")
        self.action_btn = Button("Click me", variant="primary")

    # In template:
    # &lt;div&gt;
    #     &#123;&#123; status_badge &#125;&#125;
    #     &#123;&#123; action_btn &#125;&#125;
    # &lt;/div&gt;</code></pre>

                <h4>Performance Notes</h4>
                <pre><code class="language-python"># Component automatically chooses fastest method:
# 1. Pure Rust (if available) → ~1μs
# 2. template_string + Rust engine → ~5-10μs
# 3. Python fallback → ~50-100μs

# For 100 badges:
# Rust: 0.1ms ⚡
# Hybrid: 0.5ms ✅
# Python: 5ms 🐍

# All variants supported:
variants = ["primary", "secondary", "success",
            "danger", "warning", "info"]
badges = [Badge(f"Badge {i}", variant=v)
          for i, v in enumerate(variants)]</code></pre>
            </div>
        </div>

        <!-- Include LiveView client -->
        <script src="/static/djust/client.js"></script>

        <!-- Syntax highlighting -->
        <script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js"></script>
        <script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/languages/python.min.js"></script>
        <script>hljs.highlightAll();</script>
    </body>
    </html>
    """

    def mount(self, request):
        """Initialize component demos"""

        # Badge variants
        self.badge_primary = Badge("Primary", variant="primary")
        self.badge_secondary = Badge("Secondary", variant="secondary")
        self.badge_success = Badge("Success", variant="success")
        self.badge_danger = Badge("Danger", variant="danger")
        self.badge_warning = Badge("Warning", variant="warning")
        self.badge_info = Badge("Info", variant="info")

        # Pill badges
        self.badge_pill_primary = Badge("Pill Primary", variant="primary", pill=True)
        self.badge_pill_success = Badge("Pill Success", variant="success", pill=True)
        self.badge_pill_danger = Badge("Pill Danger", variant="danger", pill=True)

        # Sized badges
        self.badge_sm = Badge("Small", variant="secondary", size="sm")
        self.badge_md = Badge("Medium", variant="secondary", size="md")
        self.badge_lg = Badge("Large", variant="secondary", size="lg")

        # Button variants
        self.btn_primary = Button("Primary", variant="primary")
        self.btn_secondary = Button("Secondary", variant="secondary")
        self.btn_success = Button("Success", variant="success")
        self.btn_danger = Button("Danger", variant="danger")
        self.btn_warning = Button("Warning", variant="warning")
        self.btn_info = Button("Info", variant="info")

        # Outline buttons
        self.btn_outline_primary = Button("Primary", variant="primary", outline=True)
        self.btn_outline_success = Button("Success", variant="success", outline=True)
        self.btn_outline_danger = Button("Danger", variant="danger", outline=True)

        # Sized buttons
        self.btn_sm = Button("Small", variant="primary", size="sm")
        self.btn_md = Button("Medium", variant="primary", size="md")
        self.btn_lg = Button("Large", variant="primary", size="lg")

        # Disabled button
        self.btn_disabled = Button("Disabled", variant="secondary", disabled=True)

        # Performance test: Many components
        badge_count = 100
        self.badge_count = badge_count
        self.badge_count_ms = badge_count // 1000
        self.badge_count_hybrid = badge_count * 5
        self.badge_count_hybrid_ms = (badge_count * 5) // 1000
        self.badge_count_python = badge_count * 50
        self.badge_count_python_ms = (badge_count * 50) // 1000

        self.many_badges = [
            Badge(f"Item {i}", variant=["primary", "secondary", "success", "info"][i % 4])
            for i in range(badge_count)
        ]

        # Interactive counter (LiveView state)
        self.count = 0

    def get_context_data(self):
        """Return context with dynamic count badge"""
        context = super().get_context_data()

        # Create count badge dynamically based on count
        if self.count > 0:
            variant = "success" if self.count <= 5 else "warning" if self.count <= 10 else "danger"
            context['count_badge'] = Badge(f"Count: {self.count}", variant=variant, pill=True)

        return context

    # Event handlers for interactive counter
    @event_handler
    def increment(self):
        """Increment counter"""
        self.count += 1

    @event_handler
    def decrement(self):
        """Decrement counter"""
        if self.count > 0:
            self.count -= 1

    @event_handler
    def reset(self):
        """Reset counter"""
        self.count = 0
