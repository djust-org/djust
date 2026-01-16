"""
Counter Demo - Simple reactive counter
"""

from djust_shared.components.ui import CodeBlock


class CounterDemo:
    """
    Self-contained counter demo with state and code examples.

    State is stored on the parent view to persist across requests.
    """

    PYTHON_CODE = '''def mount(self, request):
    self.counter = 0

def increment(self):
    self.counter += 1

def decrement(self):
    self.counter -= 1

def reset(self):
    self.counter = 0'''

    HTML_CODE = '''<div style="text-align: center;">
    <div style="font-size: 4rem;">{{ counter }}</div>
    <div style="display: flex; gap: 1rem;">
        <button @click="decrement">Decrement</button>
        <button @click="reset">Reset</button>
        <button @click="increment">Increment</button>
    </div>
</div>'''

    def __init__(self, view):
        """Initialize counter demo"""
        self.view = view

        # Initialize state on view if not present
        if not hasattr(view, 'counter'):
            view.counter = 0

    def increment(self):
        """Increment counter"""
        self.view.counter += 1

    def decrement(self):
        """Decrement counter"""
        self.view.counter -= 1

    def reset(self):
        """Reset counter to zero"""
        self.view.counter = 0

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
            filename="counter.html",
            show_header=False
        )

        return {
            'counter': self.view.counter,
            'counter_code_python': code_python,
            'counter_code_html': code_html,
        }
