"""
Demo LiveView examples
"""

from django_rust_live import LiveView
from django.views.generic import TemplateView


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

    def toggle_todo(self, todo_id=None):
        if todo_id:
            for todo in self.todos:
                if todo['id'] == int(todo_id):
                    todo['done'] = not todo['done']
                    break

    def delete_todo(self, todo_id=None):
        if todo_id:
            self.todos = [t for t in self.todos if t['id'] != int(todo_id)]

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
