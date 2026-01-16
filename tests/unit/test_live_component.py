"""
Tests for LiveComponent class - stateful components with lifecycle methods.
"""

import pytest
from djust.component import LiveComponent


class TodoListComponent(LiveComponent):
    """Test component for todo list."""

    template = """
        <div class="todo-list">
            {% for item in items %}
            <div @click="toggle_todo" data-id="{{ item.id }}">
                <input type="checkbox" {% if item.completed %}checked{% endif %}>
                {{ item.text }}
            </div>
            {% endfor %}
        </div>
    """

    def mount(self, items=None, filter="all"):
        """Initialize component state."""
        self.items = items or []
        self.filter = filter
        self.mount_called = True

    def update(self, items=None, **props):
        """Update props."""
        if items is not None:
            self.items = items
        self.update_called = True

    def toggle_todo(self, id: str = None, **kwargs):
        """Event handler for checkbox toggle."""
        item = next(i for i in self.items if i["id"] == int(id))
        item["completed"] = not item["completed"]
        self.send_parent("todo_toggled", {"id": int(id)})

    def unmount(self):
        """Cleanup."""
        self.unmount_called = True
        super().unmount()


class CounterComponent(LiveComponent):
    """Simple counter component."""

    template = """
        <div>
            <span>{{ count }}</span>
            <button @click="increment">+</button>
        </div>
    """

    def mount(self, initial=0):
        """Initialize counter."""
        self.count = initial

    def update(self, initial=None, **props):
        """Update counter."""
        if initial is not None:
            self.count = initial

    def increment(self, **kwargs):
        """Increment counter."""
        self.count += 1
        self.send_parent("count_changed", {"count": self.count})


class TestLiveComponentLifecycle:
    """Test LiveComponent lifecycle methods."""

    def test_mount_called_on_creation(self):
        """Test mount() is called when component is created."""
        component = TodoListComponent(items=[{"id": 1, "text": "Test"}])

        assert component.mount_called
        assert component.items == [{"id": 1, "text": "Test"}]
        assert component.filter == "all"  # default value

    def test_mount_with_custom_props(self):
        """Test mount() receives custom props."""
        component = TodoListComponent(items=[], filter="active")

        assert component.items == []
        assert component.filter == "active"

    def test_mount_with_no_props(self):
        """Test mount() works with no props."""
        component = TodoListComponent()

        assert component.items == []
        assert component.filter == "all"

    def test_update_called_on_prop_change(self):
        """Test update() is called when props change."""
        component = TodoListComponent(items=[])

        component.update(items=[{"id": 1, "text": "New"}])

        assert component.update_called
        assert len(component.items) == 1
        assert component.items[0]["text"] == "New"

    def test_update_partial_props(self):
        """Test update() with partial props only updates specified props."""
        component = TodoListComponent(items=[], filter="all")

        # Update only items, filter should remain
        component.update(items=[{"id": 1, "text": "Test"}])

        assert len(component.items) == 1
        assert component.filter == "all"

    def test_unmount_cleanup(self):
        """Test unmount() is called for cleanup."""
        component = TodoListComponent()

        component.unmount()

        assert component.unmount_called
        assert component._mounted is False
        assert component._parent_callback is None


class TestLiveComponentID:
    """Test component ID generation."""

    def test_component_has_unique_id(self):
        """Test each component gets a unique ID."""
        component1 = CounterComponent()
        component2 = CounterComponent()

        assert component1.component_id != component2.component_id

    def test_component_id_is_string(self):
        """Test component ID is a string."""
        component = CounterComponent()

        assert isinstance(component.component_id, str)
        assert len(component.component_id) > 0


class TestLiveComponentParentCommunication:
    """Test parent-child communication via send_parent()."""

    def test_send_parent_emits_event(self):
        """Test send_parent() emits event to parent."""
        component = CounterComponent(initial=0)
        events = []

        def parent_callback(event_data):
            events.append(event_data)

        component._set_parent_callback(parent_callback)

        # Trigger event
        component.increment()

        # Verify event was sent
        assert len(events) == 1
        assert events[0]["component_id"] == component.component_id
        assert events[0]["event"] == "count_changed"
        assert events[0]["data"] == {"count": 1}

    def test_send_parent_with_no_callback(self):
        """Test send_parent() does nothing if no callback set."""
        component = CounterComponent()

        # Should not raise error
        component.send_parent("test_event", {"data": "value"})

    def test_send_parent_with_custom_data(self):
        """Test send_parent() with custom event data."""
        component = TodoListComponent(
            items=[{"id": 1, "text": "Test", "completed": False}]
        )
        events = []

        def parent_callback(event_data):
            events.append(event_data)

        component._set_parent_callback(parent_callback)

        # Trigger event
        component.toggle_todo(id="1")

        # Verify event
        assert len(events) == 1
        assert events[0]["event"] == "todo_toggled"
        assert events[0]["data"] == {"id": 1}

    def test_send_parent_without_data(self):
        """Test send_parent() with no data payload."""
        component = CounterComponent()
        events = []

        def parent_callback(event_data):
            events.append(event_data)

        component._set_parent_callback(parent_callback)

        component.send_parent("simple_event")

        assert len(events) == 1
        assert events[0]["data"] == {}


class TestLiveComponentRendering:
    """Test component rendering."""

    def test_render_returns_html(self):
        """Test render() returns HTML string."""
        component = CounterComponent(initial=5)

        html = component.render()

        assert isinstance(html, str)
        assert "5" in html
        assert "data-component-id" in html
        assert component.component_id in html

    def test_render_with_list(self):
        """Test render() with list of items."""
        component = TodoListComponent(
            items=[
                {"id": 1, "text": "First", "completed": False},
                {"id": 2, "text": "Second", "completed": True},
            ]
        )

        html = component.render()

        assert "First" in html
        assert "Second" in html
        assert "checked" in html  # Second item is completed

    def test_render_empty_list(self):
        """Test render() with empty list."""
        component = TodoListComponent(items=[])

        html = component.render()

        assert "data-component-id" in html
        assert component.component_id in html

    def test_cannot_render_unmounted_component(self):
        """Test render() raises error if component unmounted."""
        component = CounterComponent()
        component.unmount()

        with pytest.raises(RuntimeError, match="Cannot render unmounted component"):
            component.render()

    def test_component_requires_template_string(self):
        """Test component raises error if no template defined."""

        class NoTemplateComponent(LiveComponent):
            def mount(self):
                pass

        component = NoTemplateComponent()

        with pytest.raises(ValueError, match="must define 'template' attribute"):
            component.render()


class TestLiveComponentContextData:
    """Test get_context_data() method."""

    def test_get_context_data_returns_attributes(self):
        """Test get_context_data() returns public attributes."""
        component = CounterComponent(initial=10)

        context = component.get_context_data()

        assert "count" in context
        assert context["count"] == 10

    def test_get_context_data_excludes_private(self):
        """Test get_context_data() excludes private attributes."""
        component = CounterComponent()

        context = component.get_context_data()

        assert "_mounted" not in context
        assert "_parent_callback" not in context
        assert "component_id" in context  # public

    def test_get_context_data_excludes_methods(self):
        """Test get_context_data() excludes methods."""
        component = CounterComponent()

        context = component.get_context_data()

        assert "mount" not in context
        assert "render" not in context
        assert "send_parent" not in context


class TestLiveComponentStateIsolation:
    """Test state isolation between components."""

    def test_components_have_independent_state(self):
        """Test components maintain independent state."""
        component1 = CounterComponent(initial=0)
        component2 = CounterComponent(initial=10)

        component1.count = 5

        assert component1.count == 5
        assert component2.count == 10  # unchanged

    def test_update_one_component_doesnt_affect_other(self):
        """Test updating one component doesn't affect another."""
        component1 = TodoListComponent(items=[{"id": 1, "text": "First"}])
        component2 = TodoListComponent(items=[{"id": 2, "text": "Second"}])

        component1.update(items=[])

        assert len(component1.items) == 0
        assert len(component2.items) == 1  # unchanged


class TestLiveComponentEdgeCases:
    """Test edge cases and error handling."""

    def test_mount_multiple_times(self):
        """Test calling mount() multiple times."""
        component = CounterComponent(initial=0)

        # Mount again
        component.mount(initial=5)

        # Should update state
        assert component.count == 5

    def test_unmount_multiple_times(self):
        """Test calling unmount() multiple times."""
        component = CounterComponent()

        component.unmount()
        component.unmount()  # Should not raise error

        assert component._mounted is False

    def test_component_with_none_values(self):
        """Test component handles None values gracefully."""
        component = TodoListComponent(items=None)

        assert component.items == []  # Default to empty list

    def test_component_state_after_update(self):
        """Test component state persists after update."""
        component = CounterComponent(initial=0)
        component.count = 5

        component.update(initial=10)

        # Count should be updated
        assert component.count == 10


class TestLiveComponentIntegration:
    """Integration tests for LiveComponent."""

    def test_full_lifecycle(self):
        """Test complete component lifecycle."""
        events = []

        def parent_callback(event_data):
            events.append(event_data)

        # Create component
        component = CounterComponent(initial=0)
        assert component.count == 0

        # Set parent callback
        component._set_parent_callback(parent_callback)

        # Trigger event
        component.increment()
        assert component.count == 1
        assert len(events) == 1

        # Update props
        component.update(initial=10)
        assert component.count == 10

        # Render
        html = component.render()
        assert "10" in html

        # Unmount
        component.unmount()
        assert component._mounted is False

    def test_todo_list_workflow(self):
        """Test complete todo list component workflow."""
        events = []

        def parent_callback(event_data):
            events.append(event_data)

        # Create component with initial items
        component = TodoListComponent(
            items=[
                {"id": 1, "text": "Task 1", "completed": False},
                {"id": 2, "text": "Task 2", "completed": False},
            ]
        )
        component._set_parent_callback(parent_callback)

        # Render initial state
        html = component.render()
        assert "Task 1" in html
        assert "Task 2" in html

        # Toggle item
        component.toggle_todo(id="1")

        # Verify state changed
        assert component.items[0]["completed"] is True

        # Verify event sent
        assert len(events) == 1
        assert events[0]["event"] == "todo_toggled"
        assert events[0]["data"]["id"] == 1

        # Update from parent
        component.update(
            items=[
                {"id": 1, "text": "Task 1", "completed": True},
                {"id": 2, "text": "Task 2", "completed": False},
                {"id": 3, "text": "Task 3", "completed": False},
            ]
        )

        # Verify update applied
        assert len(component.items) == 3

        # Render updated state
        html = component.render()
        assert "Task 3" in html
