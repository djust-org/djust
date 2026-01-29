"""
Component Demo - Demonstrates LiveComponent system with parent-child communication.

This demo shows:
1. LiveComponent with lifecycle methods
2. Parent-child communication (props down, events up)
3. Multiple components coordinating
4. State isolation
"""

from djust import LiveView, LiveComponent
from djust.decorators import event


class UserListComponent(LiveComponent):
    """Component that displays a list of users and handles selection."""

    template = """
        <div class="user-list card">
            <div class="card-header">
                <h5>Users ({{ users|length }})</h5>
            </div>
            <div class="list-group list-group-flush">
                {% for user in users %}
                <a href="#"
                   class="list-group-item list-group-item-action {% if user.id == selected_id %}active{% endif %}"
                   @click="select_user"
                   data-id="{{ user.id }}">
                    <div class="d-flex w-100 justify-content-between">
                        <h6 class="mb-1">{{ user.name }}</h6>
                        <small>{{ user.role }}</small>
                    </div>
                    <small class="text-muted">{{ user.email }}</small>
                </a>
                {% endfor %}
            </div>
        </div>
    """

    def mount(self, users=None, selected_id=None):
        """Initialize component state."""
        self.users = users or []
        self.selected_id = selected_id

    @event
    def update(self, users=None, selected_id=None, **props):
        """Update component when props change."""
        if users is not None:
            self.users = users
        if selected_id is not None:
            self.selected_id = selected_id

    @event
    def select_user(self, id: str = None, **kwargs):
        """Handle user selection and notify parent."""
        user_id = int(id)
        self.selected_id = user_id
        # Notify parent of selection
        self.send_parent("user_selected", {"user_id": user_id})


class UserDetailComponent(LiveComponent):
    """Component that displays details of the selected user."""

    template = """
        <div class="user-detail card">
            <div class="card-header">
                <h5>User Details</h5>
            </div>
            <div class="card-body">
                {% if user %}
                    <h4>{{ user.name }}</h4>
                    <dl class="row">
                        <dt class="col-sm-3">Email:</dt>
                        <dd class="col-sm-9">{{ user.email }}</dd>

                        <dt class="col-sm-3">Role:</dt>
                        <dd class="col-sm-9"><span class="badge bg-primary">{{ user.role }}</span></dd>

                        <dt class="col-sm-3">Department:</dt>
                        <dd class="col-sm-9">{{ user.department }}</dd>

                        <dt class="col-sm-3">Tasks:</dt>
                        <dd class="col-sm-9">{{ user.tasks|length }} active</dd>
                    </dl>

                    <h6 class="mt-3">Tasks:</h6>
                    <ul class="list-group">
                        {% for task in user.tasks %}
                        <li class="list-group-item d-flex justify-content-between align-items-center">
                            {{ task.title }}
                            <span class="badge bg-{% if task.completed %}success{% else %}warning{% endif %}">
                                {% if task.completed %}Done{% else %}Pending{% endif %}
                            </span>
                        </li>
                        {% endfor %}
                    </ul>
                {% else %}
                    <p class="text-muted text-center py-5">
                        <i>Select a user from the list to view details</i>
                    </p>
                {% endif %}
            </div>
        </div>
    """

    def mount(self, user=None):
        """Initialize component with optional user."""
        self.user = user

    @event
    def update(self, user=None, **props):
        """Update user data when parent changes selection."""
        if user is not None:
            self.user = user


class TodoComponent(LiveComponent):
    """Component for managing a todo list."""

    template = """
        <div class="todo-component card">
            <div class="card-header">
                <h5>Tasks for {{ user_name }} ({{ completed_count }}/{{ items|length }})</h5>
            </div>
            <div class="card-body">
                {% if items %}
                    <div class="list-group">
                        {% for item in items %}
                        <div class="list-group-item">
                            <div class="form-check">
                                <input class="form-check-input"
                                       type="checkbox"
                                       {% if item.completed %}checked{% endif %}
                                       @change="toggle_todo"
                                       data-id="{{ item.id }}">
                                <label class="form-check-label {% if item.completed %}text-decoration-line-through text-muted{% endif %}">
                                    {{ item.title }}
                                </label>
                            </div>
                        </div>
                        {% endfor %}
                    </div>

                    <div class="mt-3">
                        <div class="progress">
                            <div class="progress-bar"
                                 role="progressbar"
                                 style="width: {{ progress }}%"
                                 aria-valuenow="{{ progress }}"
                                 aria-valuemin="0"
                                 aria-valuemax="100">
                                {{ progress }}%
                            </div>
                        </div>
                    </div>
                {% else %}
                    <p class="text-muted text-center">No tasks assigned</p>
                {% endif %}
            </div>
        </div>
    """

    def mount(self, items=None, user_name="User"):
        """Initialize todo list."""
        self.items = items or []
        self.user_name = user_name

    @event
    def update(self, items=None, user_name=None, **props):
        """Update todos when parent changes selection."""
        if items is not None:
            self.items = items
        if user_name is not None:
            self.user_name = user_name

    @event
    def toggle_todo(self, id: str = None, **kwargs):
        """Toggle todo completion status."""
        todo_id = int(id)
        # Find and toggle the todo
        for item in self.items:
            if item["id"] == todo_id:
                item["completed"] = not item["completed"]
                break

        # Notify parent of change
        self.send_parent("todo_toggled", {
            "todo_id": todo_id,
            "completed": item["completed"]
        })

    def get_context_data(self):
        """Add computed properties to context."""
        context = super().get_context_data()

        # Computed: completed count
        completed = sum(1 for item in self.items if item.get("completed", False))
        context["completed_count"] = completed

        # Computed: progress percentage
        total = len(self.items)
        context["progress"] = int((completed / total * 100) if total > 0 else 0)

        return context


class ComponentDemoView(LiveView):
    """
    Demo view showcasing LiveComponent system.

    Demonstrates:
    - Multiple coordinating components
    - Parent-child communication
    - Props flowing down
    - Events bubbling up
    - State isolation
    """

    template_name = "demos/component_demo.html"

    def mount(self, request, **kwargs):
        """Initialize view with sample data and components."""
        # Sample data
        self.users = [
            {
                "id": 1,
                "name": "Alice Johnson",
                "email": "alice@example.com",
                "role": "Developer",
                "department": "Engineering",
                "tasks": [
                    {"id": 1, "title": "Implement user auth", "completed": True},
                    {"id": 2, "title": "Write unit tests", "completed": True},
                    {"id": 3, "title": "Code review PR #42", "completed": False},
                ],
            },
            {
                "id": 2,
                "name": "Bob Smith",
                "email": "bob@example.com",
                "role": "Designer",
                "department": "Product",
                "tasks": [
                    {"id": 4, "title": "Design mockups", "completed": True},
                    {"id": 5, "title": "User research", "completed": False},
                    {"id": 6, "title": "Update style guide", "completed": False},
                ],
            },
            {
                "id": 3,
                "name": "Carol Davis",
                "email": "carol@example.com",
                "role": "Manager",
                "department": "Product",
                "tasks": [
                    {"id": 7, "title": "Sprint planning", "completed": True},
                    {"id": 8, "title": "Review roadmap", "completed": False},
                ],
            },
        ]

        # Initial state
        self.selected_user_id = None
        self.selected_user = None
        self.event_log = []

        # Create child components
        self.user_list = UserListComponent(
            users=self.users,
            selected_id=self.selected_user_id
        )

        self.user_detail = UserDetailComponent(user=None)

        self.todo_list = TodoComponent(items=[], user_name="")

    def handle_component_event(self, component_id, event, data):
        """Handle events from child components."""
        # Log event for debugging
        event_entry = {
            "component_id": component_id[:8],  # Short ID for display
            "event": event,
            "data": data,
        }
        self.event_log.append(event_entry)

        # Keep only last 10 events
        if len(self.event_log) > 10:
            self.event_log = self.event_log[-10:]

        # Handle user selection
        if event == "user_selected":
            user_id = data["user_id"]
            self.selected_user_id = user_id

            # Find selected user
            self.selected_user = next(
                (u for u in self.users if u["id"] == user_id), None
            )

            # Update child components
            if self.selected_user:
                # Update user detail component
                self.user_detail.update(user=self.selected_user)

                # Update todo list component
                self.todo_list.update(
                    items=self.selected_user["tasks"],
                    user_name=self.selected_user["name"]
                )

                # Update user list to show selection
                self.user_list.update(selected_id=user_id)

        # Handle todo toggle
        elif event == "todo_toggled":
            todo_id = data["todo_id"]
            completed = data["completed"]

            # Update the task in the selected user's tasks
            if self.selected_user:
                for task in self.selected_user["tasks"]:
                    if task["id"] == todo_id:
                        task["completed"] = completed
                        break

                # Update the main users list
                for user in self.users:
                    if user["id"] == self.selected_user["id"]:
                        user["tasks"] = self.selected_user["tasks"]
                        break

    def get_context_data(self):
        """Get context data for template."""
        context = super().get_context_data()
        # Event log is now reversed in the template using {% for entry in event_log reversed %}
        return context
