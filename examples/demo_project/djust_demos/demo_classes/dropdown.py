"""
Dropdown Demo - Interactive dropdown menu
"""

from djust_shared.components.ui import CodeBlock


class DropdownDemo:
    """
    Self-contained dropdown demo with state and code examples.

    State is stored on the parent view to persist across requests.
    """

    PYTHON_CODE = '''def mount(self, request):
    self.dropdown_open = False
    self.selected_item = "Select an option..."
    self.dropdown_items = ["Python", "JavaScript", "Rust"]

def toggle_dropdown(self):
    self.dropdown_open = not self.dropdown_open

def select_item(self, item: str = "", **kwargs):
    self.selected_item = item
    self.dropdown_open = False'''

    HTML_CODE = '''<div class="dropdown">
    <button @click="toggle_dropdown">
        {{ selected_item }}
        {% if dropdown_open %}▲{% else %}▼{% endif %}
    </button>
    {% if dropdown_open %}
    <div class="dropdown-menu">
        {% for item in dropdown_items %}
        <div @click="select_item" data-item="{{ item }}">
            {{ item }}
        </div>
        {% endfor %}
    </div>
    {% endif %}
</div>'''

    def __init__(self, view):
        """Initialize dropdown demo"""
        self.view = view

        # Initialize state on view if not present
        if not hasattr(view, 'dropdown_open'):
            view.dropdown_open = False
        if not hasattr(view, 'selected_item'):
            view.selected_item = "Select an option..."
        if not hasattr(view, 'dropdown_items'):
            view.dropdown_items = ["Python", "JavaScript", "Rust", "Go", "TypeScript"]

    def toggle_dropdown(self):
        """Toggle dropdown open/closed"""
        self.view.dropdown_open = not self.view.dropdown_open

    def select_item(self, item: str = "", **kwargs):
        """Select item from dropdown"""
        self.view.selected_item = item
        self.view.dropdown_open = False

    def get_context(self):
        """Return context for template rendering"""
        # Create CodeBlock components on-the-fly (can't be serialized)
        code_python = CodeBlock(
            code=self.PYTHON_CODE,
            language="python",
            filename="views.py",
            show_header=False
        )
        code_html = CodeBlock(
            code=self.HTML_CODE,
            language="html",
            filename="dropdown.html",
            show_header=False
        )

        return {
            'dropdown_open': self.view.dropdown_open,
            'selected_item': self.view.selected_item,
            'dropdown_items': self.view.dropdown_items,
            'dropdown_code_python': code_python,
            'dropdown_code_html': code_html,
        }
