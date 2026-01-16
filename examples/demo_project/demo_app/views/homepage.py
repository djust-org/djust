"""
Homepage LiveView Demo Components

These are embedded demo components shown on the homepage to showcase
djust's reactive capabilities in action.
"""

from djust import LiveView
import time
import random


class HomeCounterDemo(LiveView):
    """
    Simple counter demo for homepage - showcases instant reactivity
    """
    template = """
    <div class="text-center">
        <div class="display-1 fw-bold mb-3" style="color: #667eea;">{{ count }}</div>
        <div class="d-flex gap-2 justify-content-center">
            <button @click="decrement" class="btn btn-outline-primary btn-lg">
                <span style="font-size: 1.5rem;">−</span>
            </button>
            <button @click="increment" class="btn btn-primary btn-lg">
                <span style="font-size: 1.5rem;">+</span>
            </button>
            <button @click="reset" class="btn btn-outline-secondary btn-lg">
                Reset
            </button>
        </div>
        <p class="text-muted mt-3 mb-0">
            <small>✨ Updates instantly via WebSocket - no page reload!</small>
        </p>
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


class HomeSearchDemo(LiveView):
    """
    Live search/filter demo - showcases real-time filtering
    """
    template = """
    <div>
        <input
            @input="filter_items"
            type="text"
            class="form-control form-control-lg mb-3"
            placeholder="🔍 Type to filter frameworks..."
            value="{{ search_query }}"
        />
        <div class="list-group">
            {% for item in filtered_items %}
            <div class="list-group-item d-flex justify-content-between align-items-center">
                <div>
                    <strong>{{ item.name }}</strong>
                    <br/>
                    <small class="text-muted">{{ item.description }}</small>
                </div>
                <span class="badge bg-primary rounded-pill">{{ item.speed }}</span>
            </div>
            {% endfor %}
        </div>
        <p class="text-muted mt-3 mb-0 text-center">
            <small>Showing {{ filtered_items|length }} of {{ items|length }} frameworks</small>
        </p>
    </div>
    """

    def mount(self, request, **kwargs):
        self.search_query = ""
        self.items = [
            {"name": "djust", "description": "Reactive Django with Rust", "speed": "⚡ 100x"},
            {"name": "Phoenix LiveView", "description": "Elixir real-time framework", "speed": "🚀 Fast"},
            {"name": "Laravel Livewire", "description": "PHP reactive components", "speed": "🐌 Slow"},
            {"name": "Hotwire Turbo", "description": "Rails HTML-over-wire", "speed": "🏃 Medium"},
            {"name": "HTMX", "description": "HTML attributes framework", "speed": "💨 Quick"},
            {"name": "React", "description": "JavaScript UI library", "speed": "🔧 Complex"},
        ]
        self.filtered_items = self.items.copy()

    def filter_items(self, value=""):
        self.search_query = value
        query_lower = value.lower()
        if query_lower:
            self.filtered_items = [
                item for item in self.items
                if query_lower in item['name'].lower() or query_lower in item['description'].lower()
            ]
        else:
            self.filtered_items = self.items.copy()


class HomeLiveDataDemo(LiveView):
    """
    Live updating data table - showcases real-time data updates
    """
    template = """
    <div>
        <div class="d-flex justify-content-between align-items-center mb-3">
            <h5 class="mb-0">Live Performance Metrics</h5>
            <button @click="refresh_data" class="btn btn-sm btn-primary">
                🔄 Refresh
            </button>
        </div>
        <div class="table-responsive">
            <table class="table table-hover">
                <thead class="table-light">
                    <tr>
                        <th>Metric</th>
                        <th class="text-end">Value</th>
                        <th class="text-end">Trend</th>
                    </tr>
                </thead>
                <tbody>
                    {% for metric in metrics %}
                    <tr>
                        <td><strong>{{ metric.name }}</strong></td>
                        <td class="text-end">
                            <span class="badge {{ metric.badge_class }}">
                                {{ metric.value }}{{ metric.unit }}
                            </span>
                        </td>
                        <td class="text-end">{{ metric.trend }}</td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
        <p class="text-muted mb-0 text-center">
            <small>Last updated: {{ last_update }}</small>
        </p>
    </div>
    """

    def mount(self, request, **kwargs):
        self.generate_metrics()

    def generate_metrics(self):
        """Generate realistic-looking performance metrics"""
        import datetime

        self.metrics = [
            {
                "name": "Render Time",
                "value": f"{random.uniform(0.1, 0.9):.1f}",
                "unit": "ms",
                "badge_class": "bg-success",
                "trend": "📈" if random.random() > 0.5 else "📉"
            },
            {
                "name": "VDOM Diff",
                "value": f"{random.randint(50, 150)}",
                "unit": "μs",
                "badge_class": "bg-success",
                "trend": "📈" if random.random() > 0.5 else "📉"
            },
            {
                "name": "WebSocket Latency",
                "value": f"{random.randint(10, 50)}",
                "unit": "ms",
                "badge_class": "bg-info",
                "trend": "📊"
            },
            {
                "name": "Memory Usage",
                "value": f"{random.randint(15, 35)}",
                "unit": "MB",
                "badge_class": "bg-primary",
                "trend": "📊"
            },
        ]

        self.last_update = datetime.datetime.now().strftime("%H:%M:%S")

    def refresh_data(self):
        """Refresh the metrics data"""
        self.generate_metrics()


class HomeTodoDemo(LiveView):
    """
    Simple todo list demo - showcases list manipulation
    """
    template = """
    <div>
        <form @submit="add_todo" class="mb-3">
            <div class="input-group input-group-lg">
                <input
                    type="text"
                    name="text"
                    class="form-control"
                    placeholder="Add a task..."
                    required
                />
                <button type="submit" class="btn btn-primary">
                    ➕ Add
                </button>
            </div>
        </form>

        <div class="list-group">
            {% for todo in todos %}
            <div class="list-group-item d-flex justify-content-between align-items-center">
                <div class="form-check flex-grow-1">
                    <input
                        class="form-check-input"
                        type="checkbox"
                        {% if todo.done %}checked{% endif %}
                        @change="toggle_todo"
                        data-id="{{ todo.id }}"
                    />
                    <label class="form-check-label {% if todo.done %}text-decoration-line-through text-muted{% endif %}">
                        {{ todo.text }}
                    </label>
                </div>
                <button
                    @click="delete_todo"
                    data-id="{{ todo.id }}"
                    class="btn btn-sm btn-outline-danger"
                >
                    🗑️
                </button>
            </div>
            {% empty %}
            <div class="text-center text-muted py-4">
                <p class="mb-0">No tasks yet. Add one above! ✨</p>
            </div>
            {% endfor %}
        </div>

        {% if todos %}
        <div class="mt-3 text-center">
            <small class="text-muted">
                {{ active_count }} active, {{ done_count }} completed
                <button @click="clear_completed" class="btn btn-sm btn-link">
                    Clear completed
                </button>
            </small>
        </div>
        {% endif %}
    </div>
    """

    def mount(self, request, **kwargs):
        self.todos = [
            {'id': 1, 'text': 'Try djust for your next project', 'done': False},
            {'id': 2, 'text': 'Read the documentation', 'done': False},
        ]
        self.next_id = 3

    def add_todo(self, text=""):
        if text.strip():
            self.todos.append({
                'id': self.next_id,
                'text': text,
                'done': False,
            })
            self.next_id += 1

    def toggle_todo(self, id=None, **kwargs):
        todo_id = int(id or kwargs.get('todo_id', 0))
        for todo in self.todos:
            if todo['id'] == todo_id:
                todo['done'] = not todo['done']
                break

    def delete_todo(self, id=None, **kwargs):
        todo_id = int(id or kwargs.get('todo_id', 0))
        self.todos = [t for t in self.todos if t['id'] != todo_id]

    def clear_completed(self):
        self.todos = [t for t in self.todos if not t['done']]

    @property
    def active_count(self):
        return sum(1 for t in self.todos if not t['done'])

    @property
    def done_count(self):
        return sum(1 for t in self.todos if t['done'])
