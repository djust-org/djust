"""
Debounce Demo - Debounced search input
"""

from djust.decorators import debounce
from djust_shared.components.ui import CodeBlock


class DebounceDemo:
    """
    Self-contained debounce demo with state and code examples.

    State is stored on the parent view to persist across requests.
    """

    PYTHON_CODE = '''from djust.decorators import debounce

def mount(self, request):
    self.debounce_query = ""
    self.debounce_count = 0

@debounce(wait=0.5, max_wait=2.0)
def debounce_search(self, value: str = "", **kwargs):
    self.debounce_query = value
    self.debounce_count += 1'''

    HTML_CODE = '''<input
    type="text"
    class="form-control"
    @input="debounce_search"
    placeholder="Type something..."
    value="{{ debounce_query }}"
>

<div>Current Query: {{ debounce_query }}</div>
<div>Server Calls: {{ debounce_count }}</div>'''

    def __init__(self, view):
        """Initialize debounce demo"""
        self.view = view

        # Initialize state on view if not present
        if not hasattr(view, 'debounce_query'):
            view.debounce_query = ""
        if not hasattr(view, 'debounce_count'):
            view.debounce_count = 0

    @debounce(wait=0.5, max_wait=2.0)
    def debounce_search(self, value: str = "", **kwargs):
        """Debounced search - waits 500ms after typing stops"""
        self.view.debounce_query = value
        self.view.debounce_count += 1

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
            filename="debounce.html",
            show_header=False
        )

        return {
            'debounce_query': self.view.debounce_query,
            'debounce_count': self.view.debounce_count,
            'debounce_code_python': code_python,
            'debounce_code_html': code_html,
        }
