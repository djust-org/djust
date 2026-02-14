#!/usr/bin/env python3
"""
Demo of dj-confirm attribute across different event types.

Usage:
    python dj_confirm_demo.py
    Then visit: http://localhost:8002
"""
import os
import sys
import django

# Add parent directory to path so we can import djust
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'python'))

# Django setup
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'demo_project.settings')
django.setup()

from django.urls import path
from djust import LiveView


class ConfirmDemoView(LiveView):
    """Demo of dj-confirm attribute."""

    template_name = 'inline'

    def mount(self, request, **kwargs):
        self.count = 0
        self.selected_option = "A"
        self.items = ["Item 1", "Item 2", "Item 3"]

    def increment(self):
        """Increment counter - requires confirmation."""
        self.count += 1

    def delete_item(self, item_index: int):
        """Delete an item - requires confirmation."""
        if 0 <= item_index < len(self.items):
            del self.items[item_index]

    def change_option(self, value: str = ""):
        """Change option - requires confirmation."""
        self.selected_option = value

    def reset(self):
        """Reset all state - requires confirmation."""
        self.count = 0
        self.selected_option = "A"
        self.items = ["Item 1", "Item 2", "Item 3"]

    def get_template(self):
        return """
<!DOCTYPE html>
<html>
<head>
    <title>dj-confirm Demo</title>
    <style>
        body {
            font-family: system-ui, -apple-system, sans-serif;
            max-width: 800px;
            margin: 40px auto;
            padding: 20px;
            background: #f5f5f5;
        }
        .section {
            background: white;
            padding: 20px;
            margin-bottom: 20px;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }
        h1 {
            color: #333;
            border-bottom: 2px solid #007bff;
            padding-bottom: 10px;
        }
        h2 {
            color: #555;
            margin-top: 0;
        }
        button, select {
            padding: 8px 16px;
            margin: 5px;
            border-radius: 4px;
            border: 1px solid #ddd;
            cursor: pointer;
            font-size: 14px;
        }
        button {
            background: #007bff;
            color: white;
            border: none;
        }
        button:hover {
            background: #0056b3;
        }
        .danger {
            background: #dc3545;
        }
        .danger:hover {
            background: #c82333;
        }
        .count {
            font-size: 24px;
            font-weight: bold;
            color: #007bff;
            margin: 10px 0;
        }
        ul {
            list-style: none;
            padding: 0;
        }
        li {
            padding: 10px;
            margin: 5px 0;
            background: #f8f9fa;
            border-radius: 4px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .info {
            background: #e7f3ff;
            padding: 10px;
            border-radius: 4px;
            margin-bottom: 15px;
            border-left: 4px solid #007bff;
        }
        code {
            background: #f4f4f4;
            padding: 2px 6px;
            border-radius: 3px;
            font-family: 'Courier New', monospace;
        }
    </style>
</head>
<body dj-root>
    <h1>dj-confirm Demo</h1>

    <div class="info">
        <strong>About:</strong> The <code>dj-confirm</code> attribute shows a native browser confirmation
        dialog before executing any dj-* event handler. Click any button below to see it in action.
    </div>

    <!-- dj-click example -->
    <div class="section">
        <h2>dj-click with confirmation</h2>
        <div class="count">Count: {{ count }}</div>
        <button dj-click="increment" dj-confirm="Increment the counter?">
            Increment (with confirm)
        </button>
        <button dj-click="increment">
            Increment (no confirm)
        </button>
    </div>

    <!-- dj-change example -->
    <div class="section">
        <h2>dj-change with confirmation</h2>
        <p>Selected: <strong>{{ selected_option }}</strong></p>
        <select dj-change="change_option" dj-confirm="Change the selected option?">
            <option value="A" {% if selected_option == "A" %}selected{% endif %}>Option A</option>
            <option value="B" {% if selected_option == "B" %}selected{% endif %}>Option B</option>
            <option value="C" {% if selected_option == "C" %}selected{% endif %}>Option C</option>
        </select>
    </div>

    <!-- dj-submit example -->
    <div class="section">
        <h2>dj-submit with confirmation</h2>
        <form dj-submit="reset" dj-confirm="Reset all values to defaults?">
            <button type="submit" class="danger">Reset Everything</button>
        </form>
    </div>

    <!-- List with delete confirmation -->
    <div class="section">
        <h2>List items with delete confirmation</h2>
        <ul>
            {% for item in items %}
            <li>
                {{ item }}
                <button
                    dj-click="delete_item({{ forloop.counter0 }})"
                    dj-confirm="Delete {{ item }}?"
                    class="danger">
                    Delete
                </button>
            </li>
            {% endfor %}
            {% if not items %}
            <li style="text-align: center; color: #999;">No items remaining</li>
            {% endif %}
        </ul>
    </div>

    <!-- Code examples -->
    <div class="section">
        <h2>Code Examples</h2>
        <p><strong>Basic usage:</strong></p>
        <pre><code>&lt;button dj-click="delete_item" dj-confirm="Are you sure?"&gt;Delete&lt;/button&gt;</code></pre>

        <p><strong>Works with all event directives:</strong></p>
        <ul style="margin-left: 20px;">
            <li>✓ dj-click</li>
            <li>✓ dj-submit</li>
            <li>✓ dj-change</li>
            <li>✓ dj-input</li>
            <li>✓ dj-blur</li>
            <li>✓ dj-focus</li>
            <li>✓ dj-keydown / dj-keyup</li>
        </ul>

        <p><strong>No confirmation:</strong></p>
        <ul style="margin-left: 20px;">
            <li>If <code>dj-confirm</code> is not present → event fires immediately</li>
            <li>If <code>dj-confirm=""</code> (empty) → event fires immediately</li>
        </ul>
    </div>
</body>
</html>
        """


urlpatterns = [
    path('', ConfirmDemoView.as_view()),
]

if __name__ == '__main__':
    from django.core.management import execute_from_command_line
    execute_from_command_line(['manage.py', 'runserver', '0.0.0.0:8002', '--noreload'])
