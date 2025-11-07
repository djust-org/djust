"""
Demo LiveView examples
"""

from django_rust_live import LiveView
from django_rust_live._rust import fast_json_dumps
from django.views.generic import TemplateView
from django.http import JsonResponse


class IndexView(TemplateView):
    """Landing page with links to demos"""
    template_name = 'index.html'


class CounterView(LiveView):
    """
    Simple counter demo - showcases reactive state updates
    """
    template_string = """
    <div class="container">
        <h1>Counter Demo</h1>
        <div class="counter-display">
            <h2>Count: {{ count }}</h2>
        </div>
        <div class="button-group">
            <button @click="increment" class="btn btn-primary">Increment</button>
            <button @click="decrement" class="btn btn-secondary">Decrement</button>
            <button @click="reset" class="btn btn-danger">Reset</button>
        </div>
        <style>
            .container { max-width: 600px; margin: 50px auto; text-align: center; font-family: Arial; }
            .counter-display { background: #f0f0f0; padding: 30px; border-radius: 10px; margin: 20px 0; }
            .counter-display h2 { font-size: 48px; margin: 0; color: #333; }
            .button-group { display: flex; gap: 10px; justify-content: center; }
            .btn { padding: 10px 20px; font-size: 16px; border: none; border-radius: 5px; cursor: pointer; }
            .btn-primary { background: #007bff; color: white; }
            .btn-secondary { background: #6c757d; color: white; }
            .btn-danger { background: #dc3545; color: white; }
            .btn:hover { opacity: 0.9; }
        </style>
    </div>
    """

    def mount(self, request, **kwargs):
        self.count = 0

    def increment(self):
        self.count += 1

    def decrement(self):
        self.count -= 1

    def reset(self):
        self.count = 0


class TodoView(LiveView):
    """
    Todo list demo - showcases list manipulation and forms
    """
    template_string = """
    <div class="container">
        <h1>Todo List Demo</h1>
        <form @submit="add_todo" class="todo-form">
            <input type="text" name="text" placeholder="What needs to be done?" class="todo-input" />
            <button type="submit" class="btn btn-primary">Add</button>
        </form>
        <div class="todo-list">
            {% for todo in todos %}
            <div class="todo-item {% if todo.done %}done{% endif %}">
                <input type="checkbox"
                       {% if todo.done %}checked{% endif %}
                       @change="toggle_todo"
                       data-id="{{ todo.id }}" />
                <span>{{ todo.text }}</span>
                <button @click="delete_todo"
                        data-id="{{ todo.id }}"
                        class="btn-delete">Delete</button>
            </div>
            {% endfor %}
        </div>
        <div class="todo-stats">
            <span>{{ active_count }} active</span>
            <span>{{ done_count }} completed</span>
        </div>
        <style>
            .container { max-width: 600px; margin: 50px auto; font-family: Arial; }
            .todo-form { display: flex; gap: 10px; margin: 20px 0; }
            .todo-input { flex: 1; padding: 10px; font-size: 16px; border: 1px solid #ddd; border-radius: 5px; }
            .todo-list { background: #f9f9f9; padding: 20px; border-radius: 5px; min-height: 200px; }
            .todo-item { display: flex; align-items: center; gap: 10px; padding: 10px; background: white; margin: 5px 0; border-radius: 5px; }
            .todo-item.done span { text-decoration: line-through; color: #999; }
            .todo-item span { flex: 1; }
            .btn-delete { padding: 5px 10px; background: #dc3545; color: white; border: none; border-radius: 3px; cursor: pointer; }
            .todo-stats { margin-top: 20px; text-align: center; color: #666; }
            .btn { padding: 10px 20px; font-size: 16px; border: none; border-radius: 5px; cursor: pointer; }
            .btn-primary { background: #007bff; color: white; }
        </style>
    </div>
    """

    def mount(self, request, **kwargs):
        self.todos = []
        self.next_id = 1

    def add_todo(self, text=""):
        if text.strip():
            self.todos.append({
                'id': self.next_id,
                'text': text,
                'done': False,
            })
            self.next_id += 1

    def toggle_todo(self, id=None, **kwargs):
        # Accept both 'id' and 'todo_id' for backwards compatibility
        item_id = id or kwargs.get('todo_id')
        if item_id:
            for todo in self.todos:
                if todo['id'] == int(item_id):
                    todo['done'] = not todo['done']
                    break

    def delete_todo(self, id=None, **kwargs):
        # Accept both 'id' and 'todo_id' for backwards compatibility
        item_id = id or kwargs.get('todo_id')
        if item_id:
            self.todos = [t for t in self.todos if t['id'] != int(item_id)]

    @property
    def active_count(self):
        return sum(1 for t in self.todos if not t['done'])

    @property
    def done_count(self):
        return sum(1 for t in self.todos if t['done'])


class ChatView(LiveView):
    """
    Chat demo - showcases real-time communication
    """
    template_string = """
    <div class="container">
        <h1>Chat Demo</h1>
        <div class="chat-messages" id="messages">
            {% for message in messages %}
            <div class="message">
                <strong>{{ message.user }}:</strong>
                <span>{{ message.text }}</span>
                <small>{{ message.time }}</small>
            </div>
            {% endfor %}
        </div>
        <form @submit="send_message" class="chat-form">
            <input type="text"
                   name="message"
                   placeholder="Type a message..."
                   class="chat-input"
                   autocomplete="off" />
            <button type="submit" class="btn btn-primary">Send</button>
        </form>
        <style>
            .container { max-width: 800px; margin: 50px auto; font-family: Arial; }
            .chat-messages { background: #f9f9f9; padding: 20px; border-radius: 5px; height: 400px; overflow-y: auto; margin: 20px 0; }
            .message { background: white; padding: 10px; margin: 5px 0; border-radius: 5px; }
            .message strong { color: #007bff; }
            .message small { color: #999; margin-left: 10px; }
            .chat-form { display: flex; gap: 10px; }
            .chat-input { flex: 1; padding: 10px; font-size: 16px; border: 1px solid #ddd; border-radius: 5px; }
            .btn { padding: 10px 20px; font-size: 16px; border: none; border-radius: 5px; cursor: pointer; }
            .btn-primary { background: #007bff; color: white; }
        </style>
    </div>
    """

    def mount(self, request, **kwargs):
        self.messages = []
        self.username = request.user.username if request.user.is_authenticated else "Guest"

    def send_message(self, message=""):
        if message.strip():
            import datetime
            self.messages.append({
                'user': self.username,
                'text': message,
                'time': datetime.datetime.now().strftime("%H:%M"),
            })


class ReactDemoView(LiveView):
    """
    React integration demo - showcases React components within LiveView templates

    This demonstrates:
    - Using JSX-style component syntax in templates
    - Server-side rendering of React components with Rust
    - Client-side hydration for interactivity
    - Mixing server-side LiveView state with client-side React state
    """
    template_string = """
    <div class="container">
        <h1>React Integration Demo</h1>

        <div class="demo-section">
            <h2>Server Counter (LiveView)</h2>
            <p>This counter is managed server-side with LiveView:</p>
            <div class="server-counter">
                <h3>Server Count: {{ server_count }}</h3>
                <button @click="increment_server" class="btn btn-primary">Increment Server</button>
            </div>
        </div>

        <div class="demo-section">
            <h2>Client Counter (React)</h2>
            <p>This counter is a React component with client-side state:</p>
            <Counter initialCount="{{ client_count }}" label="Client Count" />
        </div>

        <div class="demo-section">
            <h2>React Components</h2>
            <Button variant="primary">Primary Button</Button>
            <Button variant="secondary">Secondary Button</Button>
            <Button variant="danger">Danger Button</Button>
        </div>

        <div class="demo-section">
            <h2>Cards</h2>
            <div class="card-grid">
                <Card title="Welcome">
                    This is a server-rendered React card component that will be hydrated on the client.
                </Card>
                <Card title="Features">
                    <ul>
                        <li>Server-side rendering with Rust</li>
                        <li>Client-side React hydration</li>
                        <li>Mix LiveView and React seamlessly</li>
                    </ul>
                </Card>
            </div>
        </div>

        <div class="demo-section">
            <h2>Todo List (React + LiveView)</h2>
            <p>Todos managed server-side, rendered with React components:</p>
            <div class="todo-list-react">
                {% for todo in todos %}
                <TodoItem text="{{ todo.text }}" completed="{{ todo.done }}" />
                {% endfor %}
            </div>
            <form @submit="add_todo_item" class="todo-form">
                <input type="text" name="text" placeholder="New todo..." class="form-control" />
                <button type="submit" class="btn btn-primary">Add Todo</button>
            </form>
        </div>

        <div class="demo-section">
            <h2>Alerts</h2>
            <Alert type="success">Operation completed successfully!</Alert>
            <Alert type="warning" dismissible="true">This is a warning message.</Alert>
            <Alert type="info">React components rendered with Django Rust Live.</Alert>
        </div>

        <style>
            .container { max-width: 1200px; margin: 50px auto; font-family: Arial; }
            .demo-section { margin: 30px 0; padding: 20px; background: #f9f9f9; border-radius: 8px; }
            .demo-section h2 { margin-top: 0; color: #333; }
            .server-counter { background: #e3f2fd; padding: 20px; border-radius: 5px; margin: 10px 0; }
            .server-counter h3 { margin: 0 0 10px 0; color: #1976d2; }
            .btn { padding: 10px 20px; margin: 5px; border: none; border-radius: 5px; cursor: pointer; font-size: 14px; }
            .btn-primary { background: #007bff; color: white; }
            .btn-secondary { background: #6c757d; color: white; }
            .btn-danger { background: #dc3545; color: white; }
            .btn:hover { opacity: 0.9; }
            .card-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 20px; }
            .card { background: white; border: 1px solid #ddd; border-radius: 8px; overflow: hidden; }
            .card-header { background: #f8f9fa; padding: 15px; border-bottom: 1px solid #ddd; }
            .card-header h3 { margin: 0; font-size: 18px; color: #333; }
            .card-body { padding: 15px; }
            .todo-list-react { margin: 15px 0; }
            .todo-form { display: flex; gap: 10px; margin-top: 15px; }
            .form-control { flex: 1; padding: 10px; border: 1px solid #ddd; border-radius: 5px; }
            .alert { padding: 15px; margin: 10px 0; border-radius: 5px; position: relative; }
            .alert-success { background: #d4edda; border: 1px solid #c3e6cb; color: #155724; }
            .alert-warning { background: #fff3cd; border: 1px solid #ffeaa7; color: #856404; }
            .alert-info { background: #d1ecf1; border: 1px solid #bee5eb; color: #0c5460; }
            .counter-widget { background: white; padding: 20px; border-radius: 8px; text-align: center; }
            .counter-label { font-size: 14px; color: #666; margin-bottom: 10px; }
            .counter-display { font-size: 48px; font-weight: bold; color: #007bff; margin: 20px 0; }
            .counter-controls { display: flex; justify-content: center; gap: 10px; }
            .btn-sm { padding: 5px 15px; font-size: 12px; }
            .todo-item { display: flex; align-items: center; gap: 10px; padding: 10px; background: white; margin: 5px 0; border-radius: 5px; }
            .todo-item.completed .todo-text { text-decoration: line-through; color: #999; }
            .todo-text { flex: 1; }
        </style>
    </div>
    """

    def mount(self, request, **kwargs):
        """Initialize component state"""
        # Import React components
        from . import react_components  # Ensure components are registered

        self.server_count = 0
        self.client_count = 0
        self.todos = [
            {'text': 'Try React integration', 'done': False},
            {'text': 'Build amazing apps', 'done': False},
        ]

    def increment_server(self):
        """Increment server-side counter (LiveView state)"""
        self.server_count += 1

    def add_todo_item(self, text=""):
        """Add a new todo item"""
        if text.strip():
            self.todos.append({'text': text, 'done': False})

    def delete_todo_item(self, text=""):
        """Delete a todo item by text"""
        self.todos = [t for t in self.todos if t['text'] != text]


class PerformanceTestView(LiveView):
    """
    Performance test demo - stress testing with many interactive elements

    Features:
    - Large list rendering (100-1000 items)
    - Real-time filtering and sorting
    - Batch operations
    - Performance metrics tracking
    """
    template_string = """
    <div class="container">
        <h1>Performance Test Demo</h1>

        <div class="stats-bar">
            <div class="stat">
                <strong>Items:</strong> {{ item_count }}
            </div>
            <div class="stat">
                <strong>Selected:</strong> {{ selected_count }}
            </div>
            <div class="stat">
                <strong>Filter:</strong> {{ filter_text or 'None' }}
            </div>
            <div class="stat">
                <strong>Sort:</strong> {{ sort_by }}
            </div>
        </div>

        <div class="controls">
            <div class="control-group">
                <button @click="add_items_10" class="btn btn-primary">Add 10 Items</button>
                <button @click="add_items_100" class="btn btn-primary">Add 100 Items</button>
                <button @click="add_items_1000" class="btn btn-warning">Add 1000 Items</button>
                <button @click="clear_all" class="btn btn-danger">Clear All</button>
            </div>

            <div class="control-group">
                <input type="text"
                       placeholder="Filter items..."
                       value="{{ filter_text }}"
                       @change="filter_items"
                       class="filter-input" />

                <select @change="sort_items" class="sort-select">
                    <option value="name" {% if sort_by == 'name' %}selected{% endif %}>Sort by Name</option>
                    <option value="priority" {% if sort_by == 'priority' %}selected{% endif %}>Sort by Priority</option>
                    <option value="timestamp" {% if sort_by == 'timestamp' %}selected{% endif %}>Sort by Time</option>
                </select>
            </div>

            <div class="control-group">
                <button @click="select_all" class="btn btn-secondary">Select All</button>
                <button @click="deselect_all" class="btn btn-secondary">Deselect All</button>
                <button @click="delete_selected" class="btn btn-danger">Delete Selected</button>
                <button @click="toggle_priority_selected" class="btn btn-info">Toggle Priority</button>
            </div>
        </div>

        <div class="items-container">
            {% for item in items %}
            <div class="performance-item {% if item.selected %}selected{% endif %} {% if item.priority %}high-priority{% endif %}"
                 data-id="{{ item.id }}">
                <input type="checkbox"
                       class="item-checkbox"
                       {% if item.selected %}checked{% endif %}
                       @change="toggle_item"
                       data-id="{{ item.id }}" />
                <div class="item-content">
                    <span class="item-name">{{ item.name }}</span>
                    <span class="item-timestamp">{{ item.timestamp }}</span>
                </div>
                <div class="item-actions">
                    {% if item.priority %}
                    <span class="priority-badge">High Priority</span>
                    {% endif %}
                    <button @click="toggle_priority"
                            data-id="{{ item.id }}"
                            class="btn btn-xs">★</button>
                    <button @click="delete_item"
                            data-id="{{ item.id }}"
                            class="btn btn-xs btn-danger">×</button>
                </div>
            </div>
            {% endfor %}
        </div>

        <style>
            .container { max-width: 1400px; margin: 20px auto; font-family: 'Segoe UI', Arial, sans-serif; }

            .stats-bar {
                display: flex;
                gap: 20px;
                padding: 20px;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                border-radius: 10px;
                margin-bottom: 20px;
                color: white;
            }

            .stat { display: flex; gap: 8px; align-items: center; }
            .stat strong { font-weight: 600; }

            .controls {
                background: #f8f9fa;
                padding: 20px;
                border-radius: 10px;
                margin-bottom: 20px;
            }

            .control-group {
                display: flex;
                gap: 10px;
                margin-bottom: 15px;
                flex-wrap: wrap;
            }

            .control-group:last-child { margin-bottom: 0; }

            .btn {
                padding: 10px 20px;
                border: none;
                border-radius: 5px;
                cursor: pointer;
                font-size: 14px;
                font-weight: 500;
                transition: all 0.2s;
            }

            .btn:hover { transform: translateY(-1px); box-shadow: 0 4px 8px rgba(0,0,0,0.1); }
            .btn:active { transform: translateY(0); }

            .btn-primary { background: #007bff; color: white; }
            .btn-secondary { background: #6c757d; color: white; }
            .btn-danger { background: #dc3545; color: white; }
            .btn-warning { background: #ffc107; color: #000; }
            .btn-info { background: #17a2b8; color: white; }
            .btn-xs { padding: 5px 10px; font-size: 12px; }

            .filter-input, .sort-select {
                flex: 1;
                min-width: 200px;
                padding: 10px;
                border: 1px solid #ddd;
                border-radius: 5px;
                font-size: 14px;
            }

            .items-container {
                background: white;
                border: 1px solid #ddd;
                border-radius: 10px;
                max-height: 600px;
                overflow-y: auto;
                padding: 10px;
            }

            .performance-item {
                display: flex;
                align-items: center;
                gap: 15px;
                padding: 12px 15px;
                margin: 5px 0;
                background: #f8f9fa;
                border: 2px solid transparent;
                border-radius: 8px;
                transition: all 0.2s;
            }

            .performance-item:hover {
                background: #e9ecef;
                border-color: #dee2e6;
            }

            .performance-item.selected {
                background: #d1ecf1;
                border-color: #17a2b8;
            }

            .performance-item.high-priority {
                border-left: 4px solid #ffc107;
            }

            .item-checkbox {
                width: 20px;
                height: 20px;
                cursor: pointer;
            }

            .item-content {
                flex: 1;
                display: flex;
                justify-content: space-between;
                align-items: center;
            }

            .item-name {
                font-weight: 500;
                color: #333;
            }

            .item-timestamp {
                font-size: 12px;
                color: #6c757d;
            }

            .item-actions {
                display: flex;
                gap: 8px;
                align-items: center;
            }

            .priority-badge {
                background: #ffc107;
                color: #000;
                padding: 2px 8px;
                border-radius: 4px;
                font-size: 11px;
                font-weight: 600;
            }

            /* Scrollbar styling */
            .items-container::-webkit-scrollbar { width: 10px; }
            .items-container::-webkit-scrollbar-track { background: #f1f1f1; border-radius: 10px; }
            .items-container::-webkit-scrollbar-thumb { background: #888; border-radius: 10px; }
            .items-container::-webkit-scrollbar-thumb:hover { background: #555; }
        </style>
    </div>
    """

    def mount(self, request, **kwargs):
        """Initialize with empty state"""
        self.items = []
        self.next_id = 1
        self.filter_text = ""
        self.sort_by = "name"

    def _generate_items(self, count):
        """Generate sample items"""
        import time
        import random

        priorities = ["Low", "Medium", "High"]
        base_time = time.time()

        new_items = []
        for i in range(count):
            new_items.append({
                'id': self.next_id,
                'name': f'Item {self.next_id:04d}',
                'priority': random.choice([True, False]),
                'selected': False,
                'timestamp': time.strftime('%H:%M:%S', time.localtime(base_time - i * 60)),
            })
            self.next_id += 1

        return new_items

    def _apply_filter_and_sort(self):
        """Apply current filter and sort settings"""
        if not hasattr(self, '_items'):
            return []

        # Filter
        if self.filter_text:
            filtered = [item for item in self._items if self.filter_text.lower() in item['name'].lower()]
        else:
            filtered = self._items[:]

        # Sort
        if self.sort_by == "name":
            filtered.sort(key=lambda x: x['name'])
        elif self.sort_by == "priority":
            filtered.sort(key=lambda x: (not x['priority'], x['name']))
        elif self.sort_by == "timestamp":
            filtered.sort(key=lambda x: x['timestamp'], reverse=True)

        return filtered

    @property
    def items(self):
        """Return filtered and sorted items"""
        return self._apply_filter_and_sort()

    @items.setter
    def items(self, value):
        """Set the raw items list"""
        self._items = value

    @property
    def item_count(self):
        return len(self._items) if hasattr(self, '_items') else 0

    @property
    def selected_count(self):
        if not hasattr(self, '_items'):
            return 0
        return sum(1 for item in self._items if item.get('selected', False))

    def add_items_10(self):
        """Add 10 items"""
        if not hasattr(self, '_items'):
            self._items = []
        self._items.extend(self._generate_items(10))

    def add_items_100(self):
        """Add 100 items"""
        if not hasattr(self, '_items'):
            self._items = []
        self._items.extend(self._generate_items(100))

    def add_items_1000(self):
        """Add 1000 items"""
        if not hasattr(self, '_items'):
            self._items = []
        self._items.extend(self._generate_items(1000))

    def clear_all(self):
        """Clear all items"""
        self._items = []
        self.next_id = 1
        self.filter_text = ""

    def filter_items(self, value=""):
        """Update filter text"""
        self.filter_text = value

    def sort_items(self, value="name"):
        """Update sort order"""
        self.sort_by = value

    def toggle_item(self, **kwargs):
        """Toggle item selection"""
        item_id = kwargs.get('id')
        if item_id and hasattr(self, '_items'):
            item_id = int(item_id)
            for item in self._items:
                if item['id'] == item_id:
                    item['selected'] = not item.get('selected', False)
                    break

    def select_all(self):
        """Select all items"""
        if hasattr(self, '_items'):
            for item in self._items:
                item['selected'] = True

    def deselect_all(self):
        """Deselect all items"""
        if hasattr(self, '_items'):
            for item in self._items:
                item['selected'] = False

    def delete_selected(self):
        """Delete all selected items"""
        if hasattr(self, '_items'):
            self._items = [item for item in self._items if not item.get('selected', False)]

    def toggle_priority(self, **kwargs):
        """Toggle item priority"""
        item_id = kwargs.get('id')
        if item_id and hasattr(self, '_items'):
            item_id = int(item_id)
            for item in self._items:
                if item['id'] == item_id:
                    item['priority'] = not item.get('priority', False)
                    break

    def toggle_priority_selected(self):
        """Toggle priority for all selected items"""
        if hasattr(self, '_items'):
            for item in self._items:
                if item.get('selected', False):
                    item['priority'] = not item.get('priority', False)

    def delete_item(self, **kwargs):
        """Delete a specific item"""
        item_id = kwargs.get('id')
        if item_id and hasattr(self, '_items'):
            item_id = int(item_id)
            self._items = [item for item in self._items if item['id'] != item_id]


class ProductDataTableView(LiveView):
    """
    React DataTable demo - showcases hybrid LiveView + React components
    
    This view demonstrates how to integrate React components into LiveView:
    - Server manages the data state  
    - React handles rich UI (sorting, filtering, pagination)
    - Custom POST handler returns JSON instead of patches
    """
    
    # Disable normal LiveView patching for this view
    use_dom_patching = False
    
    def get_template(self):
        return """
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Product DataTable - Django Rust Live</title>
            <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css">
            <link rel="stylesheet" href="/static/css/datatable.css">
            <style>
                body {
                    background-color: #f5f5f5;
                    padding: 20px;
                }
                .header {
                    background: white;
                    padding: 30px;
                    border-radius: 8px;
                    box-shadow: 0 2px 4px rgba(0,0,0,0.1);
                    margin-bottom: 20px;
                }
                .stats {
                    display: grid;
                    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
                    gap: 15px;
                    margin-top: 20px;
                }
                .stat-card {
                    background: #f8f9fa;
                    padding: 15px;
                    border-radius: 6px;
                    text-align: center;
                }
                .stat-value {
                    font-size: 24px;
                    font-weight: bold;
                    color: #007bff;
                }
                .stat-label {
                    font-size: 14px;
                    color: #6c757d;
                    margin-top: 5px;
                }
                .actions {
                    display: flex;
                    gap: 10px;
                    margin-top: 15px;
                }
            </style>
        </head>
        <body>
            <div class="container-fluid">
                <div class="header">
                    <h1>Product DataTable</h1>
                    <p class="text-muted">Hybrid LiveView + React DataTable Example</p>
                    
                    <div class="stats">
                        <div class="stat-card">
                            <div class="stat-value" id="stat-total">{{ total_products }}</div>
                            <div class="stat-label">Total Products</div>
                        </div>
                        <div class="stat-card">
                            <div class="stat-value" id="stat-active">{{ active_products }}</div>
                            <div class="stat-label">Active</div>
                        </div>
                        <div class="stat-card">
                            <div class="stat-value" id="stat-value">${{ total_value }}</div>
                            <div class="stat-label">Total Inventory Value</div>
                        </div>
                        <div class="stat-card">
                            <div class="stat-value" id="stat-low-stock">{{ low_stock_count }}</div>
                            <div class="stat-label">Low Stock Items</div>
                        </div>
                    </div>

                    <div class="actions">
                        <button onclick="handleAction('add_sample_products')" class="btn btn-primary">Add Sample Products</button>
                        <button onclick="handleAction('clear_products')" class="btn btn-danger">Clear All</button>
                        <button onclick="handleAction('toggle_inactive')" class="btn btn-secondary">Toggle Inactive</button>
                    </div>
                </div>

                <!-- React DataTable Mount Point -->
                <div id="react-datatable-root"></div>
            </div>

            <!-- React and Babel -->
            <script crossorigin src="https://unpkg.com/react@18/umd/react.production.min.js"></script>
            <script crossorigin src="https://unpkg.com/react-dom@18/umd/react-dom.production.min.js"></script>
            <script src="https://unpkg.com/@babel/standalone/babel.min.js"></script>

            <!-- Initial data from server -->
            <script>
                window.INITIAL_PRODUCTS = {{ products_json }};
            </script>

            <!-- DataTable Component + Initialization -->
            <script type="text/babel">
                const { useState, useEffect, useMemo } = React;

                // DataTable Component
                function DataTable({ data, columns, onEvent }) {
                    console.log('[DataTable] Component called with', data?.length, 'items');
                    const [sortColumn, setSortColumn] = useState(null);
                    const [sortDirection, setSortDirection] = useState('asc');
                    const [filterText, setFilterText] = useState('');
                    const [currentPage, setCurrentPage] = useState(1);
                    const [pageSize, setPageSize] = useState(10);

                    // Sort data
                    const sortedData = useMemo(() => {
                        if (!sortColumn) return data;
                        return [...data].sort((a, b) => {
                            const aVal = a[sortColumn];
                            const bVal = b[sortColumn];
                            if (aVal === bVal) return 0;
                            const comparison = aVal < bVal ? -1 : 1;
                            return sortDirection === 'asc' ? comparison : -comparison;
                        });
                    }, [data, sortColumn, sortDirection]);

                    // Filter data
                    const filteredData = useMemo(() => {
                        if (!filterText) return sortedData;
                        const lowerFilter = filterText.toLowerCase();
                        return sortedData.filter(row => {
                            return columns.some(col => {
                                const value = String(row[col.key] || '').toLowerCase();
                                return value.includes(lowerFilter);
                            });
                        });
                    }, [sortedData, filterText, columns]);

                    // Paginate data
                    const paginatedData = useMemo(() => {
                        const start = (currentPage - 1) * pageSize;
                        return filteredData.slice(start, start + pageSize);
                    }, [filteredData, currentPage, pageSize]);

                    const totalPages = Math.ceil(filteredData.length / pageSize);

                    const handleSort = (columnKey) => {
                        if (sortColumn === columnKey) {
                            setSortDirection(sortDirection === 'asc' ? 'desc' : 'asc');
                        } else {
                            setSortColumn(columnKey);
                            setSortDirection('asc');
                        }
                    };

                    const handleRowClick = (row) => {
                        if (onEvent) {
                            onEvent('row_click', { id: row.id });
                        }
                    };

                    const handlePageChange = (page) => {
                        setCurrentPage(page);
                    };

                    // Define styles to avoid Django template {{ }} conflicts
                    const thStyle = { cursor: 'pointer', userSelect: 'none' };
                    const rowStyle = { cursor: 'pointer' };

                    return (
                        <div className="datatable-container">
                            {/* Filter */}
                            <div className="datatable-filter">
                                <input
                                    type="text"
                                    className="form-control"
                                    placeholder="Search..."
                                    value={filterText}
                                    onChange={(e) => {
                                        setFilterText(e.target.value);
                                        setCurrentPage(1);
                                    }}
                                />
                            </div>

                            {/* Table */}
                            <div className="table-responsive">
                                <table className="table table-striped table-hover">
                                    <thead>
                                        <tr>
                                            {columns.map((col) => (
                                                <th
                                                    key={col.key}
                                                    onClick={() => handleSort(col.key)}
                                                    style={thStyle}
                                                >
                                                    {col.label}
                                                    {sortColumn === col.key && (
                                                        <span className="sort-indicator">
                                                            {sortDirection === 'asc' ? ' ▲' : ' ▼'}
                                                        </span>
                                                    )}
                                                </th>
                                            ))}
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {paginatedData.length === 0 ? (
                                            <tr>
                                                <td colSpan={columns.length} className="text-center text-muted">
                                                    No data found
                                                </td>
                                            </tr>
                                        ) : (
                                            paginatedData.map((row, idx) => (
                                                <tr
                                                    key={row.id || idx}
                                                    onClick={() => handleRowClick(row)}
                                                    style={rowStyle}
                                                >
                                                    {columns.map((col) => (
                                                        <td key={col.key}>
                                                            {col.render
                                                                ? col.render(row[col.key], row)
                                                                : row[col.key]}
                                                        </td>
                                                    ))}
                                                </tr>
                                            ))
                                        )}
                                    </tbody>
                                </table>
                            </div>

                            {/* Pagination */}
                            <div className="datatable-pagination">
                                <div className="pagination-info">
                                    Showing {(currentPage - 1) * pageSize + 1} to{' '}
                                    {Math.min(currentPage * pageSize, filteredData.length)} of{' '}
                                    {filteredData.length} entries
                                    {filterText && ` (filtered from ${data.length} total)`}
                                </div>

                                <div className="pagination-controls">
                                    <select
                                        className="form-control form-control-sm"
                                        value={pageSize}
                                        onChange={(e) => {
                                            setPageSize(Number(e.target.value));
                                            setCurrentPage(1);
                                        }}
                                    >
                                        <option value="10">10</option>
                                        <option value="25">25</option>
                                        <option value="50">50</option>
                                        <option value="100">100</option>
                                    </select>

                                    <nav>
                                        <ul className="pagination pagination-sm mb-0">
                                            <li className={`page-item ${currentPage === 1 ? 'disabled' : ''}`}>
                                                <button
                                                    className="page-link"
                                                    onClick={() => handlePageChange(1)}
                                                    disabled={currentPage === 1}
                                                >
                                                    First
                                                </button>
                                            </li>
                                            <li className={`page-item ${currentPage === 1 ? 'disabled' : ''}`}>
                                                <button
                                                    className="page-link"
                                                    onClick={() => handlePageChange(currentPage - 1)}
                                                    disabled={currentPage === 1}
                                                >
                                                    Previous
                                                </button>
                                            </li>

                                            {/* Page numbers */}
                                            {[...Array(Math.min(5, totalPages))].map((_, i) => {
                                                let pageNum;
                                                if (totalPages <= 5) {
                                                    pageNum = i + 1;
                                                } else if (currentPage <= 3) {
                                                    pageNum = i + 1;
                                                } else if (currentPage >= totalPages - 2) {
                                                    pageNum = totalPages - 4 + i;
                                                } else {
                                                    pageNum = currentPage - 2 + i;
                                                }

                                                return (
                                                    <li
                                                        key={pageNum}
                                                        className={`page-item ${currentPage === pageNum ? 'active' : ''}`}
                                                    >
                                                        <button
                                                            className="page-link"
                                                            onClick={() => handlePageChange(pageNum)}
                                                        >
                                                            {pageNum}
                                                        </button>
                                                    </li>
                                                );
                                            })}

                                            <li className={`page-item ${currentPage === totalPages ? 'disabled' : ''}`}>
                                                <button
                                                    className="page-link"
                                                    onClick={() => handlePageChange(currentPage + 1)}
                                                    disabled={currentPage === totalPages}
                                                >
                                                    Next
                                                </button>
                                            </li>
                                            <li className={`page-item ${currentPage === totalPages ? 'disabled' : ''}`}>
                                                <button
                                                    className="page-link"
                                                    onClick={() => handlePageChange(totalPages)}
                                                    disabled={currentPage === totalPages}
                                                >
                                                    Last
                                                </button>
                                            </li>
                                        </ul>
                                    </nav>
                                </div>
                            </div>
                        </div>
                    );
                }

                // Bridge between server and React
                let reactSetData = null;  // Will be set by React component

                // Bridge between server and React
                function LiveViewDataTable({ initialData }) {
                    console.log('[DataTable] LiveViewDataTable rendering with', initialData?.length, 'products');
                    const [data, setData] = useState(initialData);

                    // Expose setData globally so server events can update it
                    React.useEffect(() => {
                        console.log('[DataTable] Setting up reactSetData');
                        reactSetData = setData;
                    }, []);

                    // Handle events from React back to server
                    const handleEvent = async (eventName, params) => {
                        console.log('[React->Server] Event:', eventName, params);
                        // Could send to server if needed
                    };

                    // Define columns
                    const columns = [
                        { key: 'id', label: 'ID' },
                        { key: 'name', label: 'Product Name' },
                        { key: 'category', label: 'Category' },
                        {
                            key: 'price',
                            label: 'Price',
                            render: (value) => (
                                <span className="price">${value}</span>
                            )
                        },
                        {
                            key: 'stock',
                            label: 'Stock',
                            render: (value) => {
                                const className = value < 10 ? 'stock low' : value < 50 ? 'stock medium' : 'stock high';
                                return <span className={className}>{value}</span>;
                            }
                        },
                        {
                            key: 'is_active',
                            label: 'Status',
                            render: (value) => (
                                <span className={`badge ${value ? 'badge-success' : 'badge-danger'}`}>
                                    {value ? 'Active' : 'Inactive'}
                                </span>
                            )
                        }
                    ];

                    console.log('[DataTable] Rendering DataTable component...');
                    console.log('[DataTable] Data length:', data?.length);
                    console.log('[DataTable] Columns:', columns);

                    // Test simple render first
                    if (!data || data.length === 0) {
                        console.log('[DataTable] No data, rendering empty message');
                        return React.createElement('div', { style: { padding: '20px', background: 'yellow' } },
                            'No data available'
                        );
                    }

                    console.log('[DataTable] Calling DataTable component');
                    const result = React.createElement(DataTable, { data, columns, onEvent: handleEvent });
                    console.log('[DataTable] Created element:', result);
                    return result;
                }

                // Error Boundary
                class ErrorBoundary extends React.Component {
                    constructor(props) {
                        super(props);
                        this.state = { hasError: false, error: null };
                    }

                    static getDerivedStateFromError(error) {
                        return { hasError: true, error };
                    }

                    componentDidCatch(error, errorInfo) {
                        console.error('[ErrorBoundary] Caught error:', error, errorInfo);
                    }

                    render() {
                        if (this.state.hasError) {
                            return React.createElement('div', { style: { color: 'red', padding: '20px' } },
                                React.createElement('h2', null, 'Something went wrong'),
                                React.createElement('pre', null, this.state.error?.toString())
                            );
                        }
                        return this.props.children;
                    }
                }

                // Initial render
                try {
                    const rootElement = document.getElementById('react-datatable-root');
                    console.log('[DataTable] Root element:', rootElement);

                    if (!rootElement) {
                        console.error('[DataTable] Could not find react-datatable-root element!');
                    } else {
                        const root = ReactDOM.createRoot(rootElement);
                        console.log('[DataTable] ReactDOM root created, rendering...');
                        console.log('[DataTable] INITIAL_PRODUCTS:', window.INITIAL_PRODUCTS);

                        // Test with simple component first
                        root.render(
                            React.createElement(ErrorBoundary, null,
                                React.createElement(LiveViewDataTable, { initialData: window.INITIAL_PRODUCTS })
                            )
                        );
                        console.log('[DataTable] Render called successfully');
                    }
                } catch (error) {
                    console.error('[DataTable] Error during initialization:', error);
                }
            </script>

            <!-- Handle server actions -->
            <script>
                function getCookie(name) {
                    let cookieValue = null;
                    if (document.cookie && document.cookie !== '') {
                        const cookies = document.cookie.split(';');
                        for (let i = 0; i < cookies.length; i++) {
                            const cookie = cookies[i].trim();
                            if (cookie.substring(0, name.length + 1) === (name + '=')) {
                                cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                                break;
                            }
                        }
                    }
                    return cookieValue;
                }

                async function handleAction(action) {
                    console.log('[Client] Action:', action);
                    
                    try {
                        const response = await fetch(window.location.href, {
                            method: 'POST',
                            headers: {
                                'Content-Type': 'application/json',
                                'X-CSRFToken': getCookie('csrftoken'),
                            },
                            body: JSON.stringify({
                                event: action,
                                params: {}
                            })
                        });

                        if (response.ok) {
                            const data = await response.json();
                            console.log('[Server] Response:', data);
                            
                            // Update stats
                            if (data.stats) {
                                document.getElementById('stat-total').textContent = data.stats.total_products;
                                document.getElementById('stat-active').textContent = data.stats.active_products;
                                document.getElementById('stat-value').textContent = '$' + data.stats.total_value;
                                document.getElementById('stat-low-stock').textContent = data.stats.low_stock_count;
                            }
                            
                            // Update React table data
                            if (data.products && reactSetData) {
                                reactSetData(data.products);
                            }
                        }
                    } catch (error) {
                        console.error('[Client] Error:', error);
                    }
                }
            </script>
        </body>
        </html>
        """

    def post(self, request, *args, **kwargs):
        """Override POST to return JSON instead of patches"""
        import json

        try:
            # Ensure products is initialized
            if not hasattr(self, 'products'):
                self.products = self._generate_sample_products(20)

            data = json.loads(request.body)
            event = data.get('event')
            params = data.get('params', {})

            # Call the event handler
            handler = getattr(self, event, None)
            if handler and callable(handler):
                if params:
                    handler(**params)
                else:
                    handler()

            # Return JSON with updated data and stats
            total_value = sum(float(p['price']) * p['stock'] for p in self.products)
            active_products = sum(1 for p in self.products if p['is_active'])
            low_stock = sum(1 for p in self.products if p['stock'] < 10)

            return JsonResponse({
                'products': self.products,
                'stats': {
                    'total_products': len(self.products),
                    'active_products': active_products,
                    'total_value': f"{total_value:,.2f}",
                    'low_stock_count': low_stock,
                }
            })

        except Exception as e:
            import traceback
            traceback.print_exc()
            return JsonResponse({'error': str(e)}, status=500)

    def mount(self, request, **kwargs):
        """Initialize with sample data"""
        self.products = self._generate_sample_products(20)

    def add_sample_products(self):
        """Add more sample products"""
        new_products = self._generate_sample_products(10, start_id=len(self.products) + 1)
        self.products.extend(new_products)

    def clear_products(self):
        """Clear all products"""
        self.products = []

    def toggle_inactive(self):
        """Toggle active status of some products"""
        import random
        for product in random.sample(self.products, min(5, len(self.products))):
            product['is_active'] = not product['is_active']

    def get_context_data(self):
        """Provide data to template"""
        # Use Rust-powered JSON serialization
        # Benefits: Releases GIL (better for concurrent workloads), more memory efficient
        # Trade-off: Slightly slower than Python json.dumps for small datasets due to PyO3 overhead

        total_value = sum(float(p['price']) * p['stock'] for p in self.products)
        active_products = sum(1 for p in self.products if p['is_active'])
        low_stock = sum(1 for p in self.products if p['stock'] < 10)

        return {
            'products': self.products,
            'products_json': fast_json_dumps(self.products),  # Rust-powered serialization
            'total_products': len(self.products),
            'active_products': active_products,
            'total_value': f"{total_value:,.2f}",
            'low_stock_count': low_stock,
        }

    def _generate_sample_products(self, count, start_id=1):
        """Generate sample product data"""
        import random
        
        categories = ['Electronics', 'Clothing', 'Food', 'Books', 'Toys', 'Sports', 'Home & Garden']
        adjectives = ['Premium', 'Deluxe', 'Standard', 'Economy', 'Pro', 'Ultra', 'Mini', 'Max']
        nouns = ['Widget', 'Gadget', 'Device', 'Tool', 'Item', 'Product', 'Kit', 'Set']
        
        products = []
        for i in range(count):
            product_id = start_id + i
            name = f"{random.choice(adjectives)} {random.choice(nouns)} {product_id}"
            category = random.choice(categories)
            price = round(random.uniform(9.99, 499.99), 2)
            stock = random.randint(0, 200)
            is_active = random.choice([True, True, True, False])  # 75% active
            
            products.append({
                'id': product_id,
                'name': name,
                'category': category,
                'price': str(price),
                'stock': stock,
                'is_active': is_active,
            })
        
        return products
