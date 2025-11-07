"""
Demo LiveView examples
"""

from django_rust_live.simple_live_view import LiveView
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
