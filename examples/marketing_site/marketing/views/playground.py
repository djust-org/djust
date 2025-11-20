"""
Interactive playground view.
"""
import json
from djust import LiveView
from djust.decorators import event_handler, debounce
from .base import BaseMarketingView


class PlaygroundView(BaseMarketingView):
    """
    Interactive code playground.

    Allows users to write and execute djust code in the browser.
    Uses Monaco editor for syntax highlighting and code editing.
    """

    template_name = 'marketing/playground.html'
    page_slug = 'playground'

    def mount(self, request, **kwargs):
        """Initialize playground state."""
        super().mount(request, **kwargs)

        # Editor state
        self.code = self._get_default_code()
        self.template = self._get_default_template()
        self.output = ""
        self.error = ""

        # Example selector
        self.selected_example = "counter"
        self.examples = self._get_examples()

    def _get_default_code(self):
        """Get default Python code for playground."""
        return '''from djust import LiveView

class CounterView(LiveView):
    template_name = 'counter.html'

    def mount(self, request):
        self.count = 0

    def increment(self):
        self.count += 1

    def decrement(self):
        self.count -= 1

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        return context
'''

    def _get_default_template(self):
        """Get default template for playground."""
        return '''<div class="counter">
    <h1>Count: {{ count }}</h1>
    <div class="buttons">
        <button @click="decrement">-</button>
        <button @click="increment">+</button>
    </div>
</div>
'''

    def _get_examples(self):
        """Get list of example code snippets."""
        return [
            {
                'id': 'counter',
                'name': 'Simple Counter',
                'description': 'Basic reactive counter',
                'code': self._get_default_code(),
                'template': self._get_default_template(),
            },
            {
                'id': 'search',
                'name': 'Search with Debounce',
                'description': 'Live search with debouncing',
                'code': '''from djust import LiveView
from djust.decorators import debounce

class SearchView(LiveView):
    template_name = 'search.html'

    def mount(self, request):
        self.query = ""
        self.results = []

    @debounce(wait=0.5)
    def search(self, value="", **kwargs):
        self.query = value
        # Simulate search
        if value:
            self.results = [
                f"Result {i}: {value}"
                for i in range(1, 6)
            ]
        else:
            self.results = []
''',
                'template': '''<div class="search">
    <input type="text" @input="search"
           placeholder="Search..."
           value="{{ query }}" />
    <div class="results">
        {% for result in results %}
            <div class="result">{{ result }}</div>
        {% endfor %}
    </div>
</div>
''',
            },
            {
                'id': 'todo',
                'name': 'Todo List',
                'description': 'Interactive todo list',
                'code': '''from djust import LiveView
from djust.decorators import optimistic

class TodoView(LiveView):
    template_name = 'todo.html'

    def mount(self, request):
        self.todos = [
            {'id': 1, 'text': 'Learn djust', 'done': True},
            {'id': 2, 'text': 'Build an app', 'done': False},
            {'id': 3, 'text': 'Deploy to prod', 'done': False},
        ]
        self.new_todo = ""

    @optimistic()
    def toggle_todo(self, id=None, **kwargs):
        for todo in self.todos:
            if todo['id'] == int(id):
                todo['done'] = not todo['done']
                break

    def add_todo(self, **kwargs):
        if self.new_todo.strip():
            new_id = max(t['id'] for t in self.todos) + 1
            self.todos.append({
                'id': new_id,
                'text': self.new_todo,
                'done': False
            })
            self.new_todo = ""
''',
                'template': '''<div class="todos">
    <form @submit="add_todo">
        <input type="text" @input="on_input"
               name="new_todo"
               value="{{ new_todo }}"
               placeholder="Add a todo..." />
        <button type="submit">Add</button>
    </form>
    <div class="todo-list">
        {% for todo in todos %}
            <div class="todo-item">
                <input type="checkbox"
                       @change="toggle_todo"
                       data-id="{{ todo.id }}"
                       {% if todo.done %}checked{% endif %} />
                <span {% if todo.done %}class="done"{% endif %}>
                    {{ todo.text }}
                </span>
            </div>
        {% endfor %}
    </div>
</div>
''',
            },
        ]

    @event_handler()
    def select_example(self, example_id: str = "", **kwargs):
        """Load selected example into editor."""
        for example in self.examples:
            if example['id'] == example_id:
                self.selected_example = example_id
                self.code = example['code']
                self.template = example['template']
                self.output = ""
                self.error = ""
                break

    @event_handler()
    @debounce(wait=0.5)
    def update_code(self, value: str = "", **kwargs):
        """Update Python code with debouncing."""
        self.code = value
        self._execute_code()

    @event_handler()
    @debounce(wait=0.5)
    def update_template(self, value: str = "", **kwargs):
        """Update template code with debouncing."""
        self.template = value
        self._execute_code()

    def _execute_code(self):
        """
        Execute user code in sandbox.

        NOTE: This is a simplified demo. In production, use proper
        sandboxing with restricted execution environment.
        """
        try:
            # For demo purposes, just validate syntax
            compile(self.code, '<string>', 'exec')

            # Simulate successful execution
            self.output = "✓ Code is valid! In a real playground, this would render the LiveView."
            self.error = ""
        except SyntaxError as e:
            self.error = f"Syntax Error: {e.msg} at line {e.lineno}"
            self.output = ""
        except Exception as e:
            self.error = f"Error: {str(e)}"
            self.output = ""

    def get_context_data(self, **kwargs):
        """Add playground page context."""
        context = super().get_context_data(**kwargs)

        # Escape code strings for JavaScript embedding
        # Remove quotes from json.dumps() output (we want just the escaped string content)
        code_escaped = json.dumps(self.code)[1:-1]
        template_escaped = json.dumps(self.template)[1:-1]

        context.update({
            'code': code_escaped,
            'template': template_escaped,
            'output': self.output,
            'error': self.error,
            'selected_example': self.selected_example,
            'examples': self.examples,
            'examples_json': json.dumps(self.examples),
        })
        return context
