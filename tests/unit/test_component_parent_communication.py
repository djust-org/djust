"""
Tests for parent-child component communication in LiveView.
"""

import pytest
from djust import LiveView, LiveComponent
from unittest.mock import Mock


class TodoListComponent(LiveComponent):
    """Test component for todo list."""

    template = """
        <div class="todo-list">
            {% for item in items %}
            <div @click="toggle_todo" data-id="{{ item.id }}">
                {{ item.text }}
            </div>
            {% endfor %}
        </div>
    """

    def mount(self, items=None):
        """Initialize component state."""
        # Make a copy to ensure state isolation from parent
        self.items = list(items) if items else []

    def update(self, items=None, **props):
        """Update props."""
        if items is not None:
            # Make a copy to ensure state isolation from parent
            self.items = list(items)

    def toggle_todo(self, id: str = None, **kwargs):
        """Event handler for checkbox toggle."""
        # Don't modify state directly - let parent handle it
        self.send_parent("todo_toggled", {"id": int(id)})


class UserListComponent(LiveComponent):
    """Test component for user list."""

    template = """
        <div>
            {% for user in users %}
            <div @click="select_user" data-id="{{ user.id }}">
                {{ user.name }}
            </div>
            {% endfor %}
        </div>
    """

    def mount(self, users=None):
        self.users = users or []

    def select_user(self, id: str = None, **kwargs):
        self.send_parent("user_selected", {"user_id": int(id)})


class TodoDashboardView(LiveView):
    """Test view with child components."""

    template = """
        <div>
            <h1>Dashboard</h1>
            {{ todo_list.render }}
        </div>
    """

    def mount(self, request, **kwargs):
        """Initialize view with components."""
        self.todos = [
            {"id": 1, "text": "Task 1", "completed": False},
            {"id": 2, "text": "Task 2", "completed": False},
        ]
        self.todo_list = TodoListComponent(items=self.todos)
        self.events_received = []

    def handle_component_event(self, component_id, event, data):
        """Handle events from child components."""
        self.events_received.append({"component_id": component_id, "event": event, "data": data})

        if event == "todo_toggled":
            # Find and toggle the todo
            todo = next(t for t in self.todos if t["id"] == data["id"])
            todo["completed"] = not todo["completed"]


class MultiComponentView(LiveView):
    """Test view with multiple child components."""

    template = """
        <div>
            {{ user_list.render }}
            {{ todo_list.render }}
        </div>
    """

    def mount(self, request, **kwargs):
        self.users = [{"id": 1, "name": "Alice"}]
        self.todos = [{"id": 1, "text": "Task", "completed": False}]

        self.user_list = UserListComponent(users=self.users)
        self.todo_list = TodoListComponent(items=self.todos)

        self.selected_user_id = None
        self.events_received = []

    def handle_component_event(self, component_id, event, data):
        self.events_received.append({"component_id": component_id, "event": event, "data": data})

        if event == "user_selected":
            self.selected_user_id = data["user_id"]
            # Update todo list based on selected user
            self.todo_list.update(items=self.todos)


class TestParentChildCommunication:
    """Test parent-child communication via handle_component_event."""

    def test_component_sends_event_to_parent(self, rf):
        """Test component can send events to parent via send_parent()."""
        view = TodoDashboardView()
        request = rf.get("/")
        view.mount(request)

        # Get context to trigger component registration
        context = view.get_context_data()

        # Simulate component event
        view.todo_list.toggle_todo(id="1")

        # Verify parent received event
        assert len(view.events_received) == 1
        assert view.events_received[0]["event"] == "todo_toggled"
        assert view.events_received[0]["data"]["id"] == 1

    def test_parent_can_update_component_props(self, rf):
        """Test parent can update child component props via update_component()."""
        view = TodoDashboardView()
        request = rf.get("/")
        view.mount(request)

        # Get context to trigger component registration
        view.get_context_data()

        # Update component props
        new_items = [{"id": 3, "text": "New Task", "completed": False}]
        view.update_component(view.todo_list.component_id, items=new_items)

        # Verify component updated
        assert view.todo_list.items == new_items

    def test_multiple_components_send_events(self, rf):
        """Test multiple components can send events independently."""
        view = MultiComponentView()
        request = rf.get("/")
        view.mount(request)

        # Get context to trigger component registration
        view.get_context_data()

        # Send events from different components
        view.user_list.select_user(id="1")
        view.todo_list.toggle_todo(id="1")

        # Verify both events received
        assert len(view.events_received) == 2
        assert view.events_received[0]["event"] == "user_selected"
        assert view.events_received[1]["event"] == "todo_toggled"

    def test_component_id_identifies_sender(self, rf):
        """Test component_id correctly identifies which component sent event."""
        view = MultiComponentView()
        request = rf.get("/")
        view.mount(request)

        view.get_context_data()

        # Send event from user_list
        view.user_list.select_user(id="1")

        # Verify component_id matches
        assert view.events_received[0]["component_id"] == view.user_list.component_id

    def test_parent_receives_custom_event_data(self, rf):
        """Test parent receives custom event data from components."""
        view = TodoDashboardView()
        request = rf.get("/")
        view.mount(request)

        view.get_context_data()

        # Send event with custom data
        view.todo_list.send_parent("custom_event", {"foo": "bar", "count": 42})

        # Verify data received
        assert len(view.events_received) == 1
        assert view.events_received[0]["event"] == "custom_event"
        assert view.events_received[0]["data"]["foo"] == "bar"
        assert view.events_received[0]["data"]["count"] == 42


class TestComponentAutoRegistration:
    """Test automatic component registration."""

    def test_livecomponent_auto_registered(self, rf):
        """Test LiveComponents are automatically registered in get_context_data()."""
        view = TodoDashboardView()
        request = rf.get("/")
        view.mount(request)

        # Before get_context_data, component not registered
        assert len(view._components) == 0

        # After get_context_data, component registered
        view.get_context_data()
        assert view.todo_list.component_id in view._components

    def test_stateless_component_not_registered(self, rf):
        """Test stateless Components are not registered."""
        from djust import Component

        class SimpleComponent(Component):
            template = "<div>Hello</div>"

        class SimpleView(LiveView):
            template = "{{ simple.render }}"

            def mount(self, request, **kwargs):
                self.simple = SimpleComponent()

        view = SimpleView()
        request = rf.get("/")
        view.mount(request)

        view.get_context_data()

        # Stateless component should not be in registry
        assert len(view._components) == 0

    def test_component_callback_set_on_registration(self, rf):
        """Test component gets parent callback when registered."""
        view = TodoDashboardView()
        request = rf.get("/")
        view.mount(request)

        # Before registration, no callback
        assert view.todo_list._parent_callback is None

        # After registration, callback set
        view.get_context_data()
        assert view.todo_list._parent_callback is not None

    def test_multiple_components_all_registered(self, rf):
        """Test multiple LiveComponents are all registered."""
        view = MultiComponentView()
        request = rf.get("/")
        view.mount(request)

        view.get_context_data()

        # Both components registered
        assert len(view._components) == 2
        assert view.user_list.component_id in view._components
        assert view.todo_list.component_id in view._components


class TestComponentPropUpdates:
    """Test component prop updates from parent."""

    def test_update_component_triggers_update_method(self, rf):
        """Test update_component() calls component's update() method."""
        view = TodoDashboardView()
        request = rf.get("/")
        view.mount(request)

        view.get_context_data()

        # Track if update was called
        original_items = view.todo_list.items.copy()
        new_items = [{"id": 3, "text": "New", "completed": False}]

        view.update_component(view.todo_list.component_id, items=new_items)

        # Verify component state changed
        assert view.todo_list.items != original_items
        assert view.todo_list.items == new_items

    def test_update_nonexistent_component(self, rf):
        """Test updating nonexistent component does nothing."""
        view = TodoDashboardView()
        request = rf.get("/")
        view.mount(request)

        view.get_context_data()

        # Should not raise error
        view.update_component("nonexistent-id", items=[])

    def test_update_component_partial_props(self, rf):
        """Test updating component with partial props."""

        class ConfigComponent(LiveComponent):
            template = "<div>{{ title }} - {{ count }}</div>"

            def mount(self, title="Default", count=0):
                self.title = title
                self.count = count

            def update(self, title=None, count=None, **props):
                if title is not None:
                    self.title = title
                if count is not None:
                    self.count = count

        class ConfigView(LiveView):
            template = "{{ config.render }}"

            def mount(self, request, **kwargs):
                self.config = ConfigComponent(title="Test", count=5)

        view = ConfigView()
        request = rf.get("/")
        view.mount(request)

        view.get_context_data()

        # Update only count, title should remain
        view.update_component(view.config.component_id, count=10)

        assert view.config.title == "Test"
        assert view.config.count == 10


class TestDefaultHandleComponentEvent:
    """Test default handle_component_event behavior."""

    def test_default_implementation_does_nothing(self, rf):
        """Test default handle_component_event() does nothing."""

        class MinimalView(LiveView):
            template = "{{ comp.render }}"

            def mount(self, request, **kwargs):
                self.comp = TodoListComponent(items=[])

        view = MinimalView()
        request = rf.get("/")
        view.mount(request)

        view.get_context_data()

        # Should not raise error even though not overridden
        view.comp.send_parent("test_event", {"data": "value"})


class TestComponentEventHandling:
    """Test real-world component event handling scenarios."""

    def test_todo_toggle_workflow(self, rf):
        """Test complete todo toggle workflow."""
        view = TodoDashboardView()
        request = rf.get("/")
        view.mount(request)

        view.get_context_data()

        # Initial state
        assert view.todos[0]["completed"] is False

        # Toggle todo via component
        view.todo_list.toggle_todo(id="1")

        # Verify parent updated state
        assert view.todos[0]["completed"] is True

        # Verify event logged
        assert len(view.events_received) == 1

    def test_cascading_updates(self, rf):
        """Test cascading updates between parent and components."""
        view = MultiComponentView()
        request = rf.get("/")
        view.mount(request)

        view.get_context_data()

        # User selection triggers todo list update
        assert view.selected_user_id is None

        view.user_list.select_user(id="1")

        assert view.selected_user_id == 1
        assert len(view.events_received) == 1

    def test_component_state_isolation(self, rf):
        """Test component state remains isolated from parent."""
        view = MultiComponentView()
        request = rf.get("/")
        view.mount(request)

        view.get_context_data()

        # Modify parent state
        view.todos.append({"id": 2, "text": "New", "completed": False})

        # Component state should be unchanged (isolation)
        assert len(view.todo_list.items) == 1

        # Explicit update required
        view.update_component(view.todo_list.component_id, items=view.todos)

        assert len(view.todo_list.items) == 2
