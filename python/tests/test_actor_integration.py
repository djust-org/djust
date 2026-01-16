"""
Tests for actor system integration with LiveView.

These tests verify that the Rust actor system (SessionActor, ViewActor)
properly integrates with the Python LiveView framework.
"""

import pytest
import asyncio


class TestActorIntegration:
    """Test Rust actor system integration"""

    @pytest.mark.asyncio
    async def test_session_actor_creation(self):
        """Test creating and shutting down a SessionActor"""
        from djust._rust import create_session_actor

        # Create actor
        handle = await create_session_actor("test-session-001")
        assert handle is not None
        assert handle.session_id == "test-session-001"

        # Ping test
        await handle.ping()

        # Shutdown
        await handle.shutdown()

    @pytest.mark.asyncio
    async def test_multiple_session_actors(self):
        """Test creating multiple concurrent SessionActors"""
        from djust._rust import create_session_actor

        # Create multiple actors
        handles = []
        for i in range(5):
            handle = await create_session_actor(f"session-{i}")
            handles.append(handle)
            assert handle.session_id == f"session-{i}"

        # Ping all
        for handle in handles:
            await handle.ping()

        # Shutdown all
        for handle in handles:
            await handle.shutdown()

    @pytest.mark.asyncio
    async def test_actor_stats(self):
        """Test getting actor system statistics (Phase 7: supervisor stats)"""
        from djust._rust import get_actor_stats, create_session_actor

        # Initially should have no active sessions
        stats = get_actor_stats()
        assert hasattr(stats, "active_sessions")
        assert hasattr(stats, "ttl_secs")
        assert stats.ttl_secs == 3600  # Default 1-hour TTL
        initial_count = stats.active_sessions

        # Create an actor - supervisor should track it
        handle = await create_session_actor("stats-test")
        stats_with_session = get_actor_stats()
        assert stats_with_session.active_sessions == initial_count + 1

        # Shutdown - but supervisor may still track it briefly
        await handle.shutdown()
        # Note: Session cleanup happens async, so we don't assert exact count here

    @pytest.mark.asyncio
    async def test_actor_lifecycle_stress(self):
        """Stress test actor creation/shutdown cycle"""
        from djust._rust import create_session_actor

        for i in range(20):
            handle = await create_session_actor(f"stress-{i}")
            await handle.ping()
            await handle.shutdown()

    @pytest.mark.asyncio
    async def test_concurrent_actor_operations(self):
        """Test concurrent operations on multiple actors"""
        from djust._rust import create_session_actor

        async def actor_lifecycle(session_id: str):
            handle = await create_session_actor(session_id)
            for _ in range(3):
                await handle.ping()
            await handle.shutdown()

        # Run 10 actors concurrently
        tasks = [actor_lifecycle(f"concurrent-{i}") for i in range(10)]
        await asyncio.gather(*tasks)

    # ========================================
    # Phase 5: Python Event Handler Integration Tests
    # ========================================

    @pytest.mark.asyncio
    async def test_actor_mount_with_python_view(self):
        """Test mounting a view with Python instance (Phase 5)"""
        from djust._rust import create_session_actor

        # Create a mock Python view
        class MockView:
            def __init__(self):
                self.count = 0

            def get_context_data(self):
                return {"count": self.count}

            def increment(self):
                self.count += 1

        # Create actor and mount
        handle = await create_session_actor("test-phase5-mount")
        view = MockView()

        # Mount with Python view instance
        result = await handle.mount(
            "test.MockView",
            {"count": 0},
            view,  # Pass Python view instance!
        )

        assert result is not None
        assert "html" in result
        assert "session_id" in result
        assert result["session_id"] == "test-phase5-mount"

        await handle.shutdown()

    @pytest.mark.asyncio
    async def test_actor_event_calls_python_handler(self):
        """Test that actor events call Python handlers (Phase 5 core feature)"""
        from djust._rust import create_session_actor

        # Create a mock Python view with event handler
        class CounterView:
            def __init__(self):
                self.count = 0
                self.increment_called = False

            def get_context_data(self):
                return {"count": self.count}

            def increment(self):
                self.increment_called = True
                self.count += 1

            def decrement(self):
                self.count -= 1

        # Create actor and mount
        handle = await create_session_actor("test-phase5-events")
        view = CounterView()

        # Mount with Python view
        await handle.mount("test.CounterView", {"count": 0}, view)

        # Trigger event
        result = await handle.event("increment", {})

        # Verify Python handler was called
        assert view.increment_called, "Python increment() handler should have been called"
        assert view.count == 1, "Python state should have been updated"

        # Verify result has expected structure
        assert result is not None
        assert "version" in result

        # Trigger another event
        await handle.event("increment", {})
        assert view.count == 2

        # Test decrement
        await handle.event("decrement", {})
        assert view.count == 1

        await handle.shutdown()

    @pytest.mark.asyncio
    async def test_actor_event_with_params(self):
        """Test actor events with parameters"""
        from djust._rust import create_session_actor

        class FormView:
            def __init__(self):
                self.name = ""
                self.age = 0

            def get_context_data(self):
                return {"name": self.name, "age": self.age}

            def update_profile(self, name=None, age=None):
                if name is not None:
                    self.name = name
                if age is not None:
                    self.age = age

        # Create actor and mount
        handle = await create_session_actor("test-phase5-params")
        view = FormView()

        await handle.mount("test.FormView", {}, view)

        # Trigger event with params
        await handle.event("update_profile", {"name": "Alice", "age": 30})

        assert view.name == "Alice"
        assert view.age == 30

        await handle.shutdown()

    @pytest.mark.asyncio
    async def test_actor_event_missing_handler(self):
        """Test actor event with missing handler is handled gracefully"""
        from djust._rust import create_session_actor

        class MinimalView:
            def get_context_data(self):
                return {}

        handle = await create_session_actor("test-phase5-missing")
        view = MinimalView()

        await handle.mount("test.MinimalView", {}, view)

        # Calling non-existent handler returns empty response (not exception)
        result = await handle.event("nonexistent_handler", {})

        # Should return empty response on error
        assert isinstance(result, dict)
        assert result.get("patches") is None
        assert result.get("html") == ""

        await handle.shutdown()

    @pytest.mark.asyncio
    async def test_actor_mount_without_python_view(self):
        """Test that mount works without Python view (backward compatibility)"""
        from djust._rust import create_session_actor

        handle = await create_session_actor("test-phase5-no-view")

        # Mount without Python view (python_view=None)
        result = await handle.mount(
            "test.SomeView",
            {"initial": "data"},
            None,  # No Python view
        )

        assert result is not None
        assert "html" in result

        # Events will fail without Python view, but mount should succeed
        await handle.shutdown()

    @pytest.mark.asyncio
    async def test_actor_event_python_exception(self):
        """Test that Python exceptions are properly handled"""
        from djust._rust import create_session_actor

        class BuggyView:
            def get_context_data(self):
                return {}

            def broken_handler(self):
                raise ValueError("Intentional error for testing")

        handle = await create_session_actor("test-phase5-exception")
        view = BuggyView()

        await handle.mount("test.BuggyView", {}, view)

        # Python exception should be caught and return empty response
        result = await handle.event("broken_handler", {})

        # Should return empty response on error
        assert isinstance(result, dict)
        assert result.get("patches") is None
        assert result.get("html") == ""

        await handle.shutdown()

    @pytest.mark.asyncio
    async def test_actor_event_invalid_return_from_get_context_data(self):
        """Test that invalid get_context_data() return is handled"""
        from djust._rust import create_session_actor

        class InvalidView:
            def get_context_data(self):
                # Should return dict, but returns string
                return "not a dict"

            def some_event(self):
                pass

        handle = await create_session_actor("test-phase5-invalid-context")
        view = InvalidView()

        await handle.mount("test.InvalidView", {}, view)

        # Should handle invalid context_data gracefully (return empty response)
        result = await handle.event("some_event", {})

        # Should return empty response on error
        assert isinstance(result, dict)
        assert result.get("patches") is None
        assert result.get("html") == ""

        await handle.shutdown()

    # ========================================
    # Phase 6: View Identification Tests
    # ========================================

    @pytest.mark.asyncio
    async def test_mount_returns_view_id(self):
        """Test that mount returns a view_id"""
        from djust._rust import create_session_actor

        handle = await create_session_actor("test-phase6-view-id")

        result = await handle.mount("test.View", {}, None)

        assert "view_id" in result
        assert result["view_id"] is not None
        assert len(result["view_id"]) > 0  # UUID should be non-empty

        await handle.shutdown()

    @pytest.mark.asyncio
    async def test_multiple_views_per_session(self):
        """Test mounting multiple views in a single session"""
        from djust._rust import create_session_actor

        handle = await create_session_actor("test-phase6-multiple-views")

        # Mount multiple views
        view1 = await handle.mount("view1", {}, None)
        view2 = await handle.mount("view2", {}, None)
        view3 = await handle.mount("view3", {}, None)

        # Each should have a unique view_id
        assert view1["view_id"] != view2["view_id"]
        assert view1["view_id"] != view3["view_id"]
        assert view2["view_id"] != view3["view_id"]

        # All should have the same session_id
        assert view1["session_id"] == view2["session_id"] == view3["session_id"]

        await handle.shutdown()

    @pytest.mark.asyncio
    async def test_event_routing_by_view_id(self):
        """Test routing events to specific views by view_id"""
        from djust._rust import create_session_actor

        class Counter:
            def __init__(self):
                self.count = 0

            def get_context_data(self):
                return {"count": self.count}

            def increment(self):
                self.count += 1

        handle = await create_session_actor("test-phase6-routing")

        # Mount two views
        counter1 = Counter()
        counter2 = Counter()

        view1 = await handle.mount("counter1", {}, counter1)
        view2 = await handle.mount("counter2", {}, counter2)

        # Increment counter1
        await handle.event("increment", {}, view1["view_id"])
        assert counter1.count == 1
        assert counter2.count == 0  # counter2 unchanged

        # Increment counter2
        await handle.event("increment", {}, view2["view_id"])
        assert counter1.count == 1  # counter1 unchanged
        assert counter2.count == 1

        # Increment counter1 again
        await handle.event("increment", {}, view1["view_id"])
        assert counter1.count == 2
        assert counter2.count == 1

        await handle.shutdown()

    @pytest.mark.asyncio
    async def test_unmount_view(self):
        """Test unmounting a specific view"""
        from djust._rust import create_session_actor

        handle = await create_session_actor("test-phase6-unmount")

        # Mount two views
        view1 = await handle.mount("view1", {}, None)
        view2 = await handle.mount("view2", {}, None)

        # Unmount view1
        await handle.unmount(view1["view_id"])

        # Event to view1 should fail
        with pytest.raises(Exception) as exc_info:
            await handle.event("click", {}, view1["view_id"])

        assert "not found" in str(exc_info.value).lower()

        # Event to view2 should still work (explicit view_id)
        result = await handle.event("click", {}, view2["view_id"])
        assert result is not None

        await handle.shutdown()

    @pytest.mark.asyncio
    async def test_unmount_nonexistent_view(self):
        """Test unmounting a view that doesn't exist"""
        from djust._rust import create_session_actor

        handle = await create_session_actor("test-phase6-unmount-fail")

        # Try to unmount non-existent view
        with pytest.raises(Exception) as exc_info:
            await handle.unmount("nonexistent-uuid")

        assert "not found" in str(exc_info.value).lower()

        await handle.shutdown()

    @pytest.mark.asyncio
    async def test_backward_compatibility_no_view_id(self):
        """Test that events work without view_id (backward compat)"""
        from djust._rust import create_session_actor

        class Counter:
            def __init__(self):
                self.count = 0

            def get_context_data(self):
                return {"count": self.count}

            def increment(self):
                self.count += 1

        handle = await create_session_actor("test-phase6-compat")

        counter = Counter()
        await handle.mount("counter", {}, counter)

        # Event without view_id should route to first view
        await handle.event("increment", {})
        assert counter.count == 1

        # Multiple events without view_id
        await handle.event("increment", {})
        await handle.event("increment", {})
        assert counter.count == 3

        await handle.shutdown()

    @pytest.mark.asyncio
    async def test_deterministic_routing_to_first_view(self):
        """Test that backward compat routing is deterministic (IndexMap)"""
        from djust._rust import create_session_actor

        class Counter:
            def __init__(self, name: str):
                self.name = name
                self.count = 0

            def get_context_data(self):
                return {"count": self.count, "name": self.name}

            def increment(self):
                self.count += 1

        handle = await create_session_actor("test-phase6-deterministic")

        # Mount 3 views in specific order
        counter1 = Counter("first")
        counter2 = Counter("second")
        counter3 = Counter("third")

        await handle.mount("counter1", {}, counter1)
        await handle.mount("counter2", {}, counter2)
        await handle.mount("counter3", {}, counter3)

        # Events without view_id should ALWAYS go to first mounted view
        # This tests that IndexMap preserves insertion order
        await handle.event("increment", {})
        await handle.event("increment", {})
        await handle.event("increment", {})

        # Only counter1 should have been incremented
        assert counter1.count == 3, "First mounted view should receive all events"
        assert counter2.count == 0, "Second view should not receive events"
        assert counter3.count == 0, "Third view should not receive events"

        await handle.shutdown()

    # ========================================
    # Phase 8: ComponentActor Integration Tests
    # ========================================

    @pytest.mark.asyncio
    async def test_create_component(self):
        """Test creating a component in a view"""
        from djust._rust import create_session_actor

        handle = await create_session_actor("test-phase8-create")

        # Mount a view first
        view = await handle.mount("test.View", {}, None)
        view_id = view["view_id"]

        # Create a component
        html = await handle.create_component(
            view_id, "counter-1", "<div>Count: {{ count }}</div>", {"count": 0}
        )

        assert html is not None
        assert "Count: 0" in html

        await handle.shutdown()

    @pytest.mark.asyncio
    async def test_component_event(self):
        """Test sending events to a component"""
        from djust._rust import create_session_actor

        handle = await create_session_actor("test-phase8-event")

        # Mount view
        view = await handle.mount("test.View", {}, None)
        view_id = view["view_id"]

        # Create component
        await handle.create_component(
            view_id, "counter-1", "<div>Count: {{ count }}</div>", {"count": 0}
        )

        # Send event to component (simplified - updates props)
        html = await handle.component_event(view_id, "counter-1", "increment", {"count": 5})

        assert "Count: 5" in html

        await handle.shutdown()

    @pytest.mark.asyncio
    async def test_update_component_props(self):
        """Test updating component props"""
        from djust._rust import create_session_actor

        handle = await create_session_actor("test-phase8-update-props")

        # Mount view
        view = await handle.mount("test.View", {}, None)
        view_id = view["view_id"]

        # Create component
        await handle.create_component(
            view_id, "counter-1", "<div>Count: {{ count }}</div>", {"count": 0}
        )

        # Update props
        html = await handle.update_component_props(view_id, "counter-1", {"count": 42})

        assert "Count: 42" in html

        await handle.shutdown()

    @pytest.mark.asyncio
    async def test_remove_component(self):
        """Test removing a component"""
        from djust._rust import create_session_actor

        handle = await create_session_actor("test-phase8-remove")

        # Mount view
        view = await handle.mount("test.View", {}, None)
        view_id = view["view_id"]

        # Create component
        await handle.create_component(
            view_id, "counter-1", "<div>Count: {{ count }}</div>", {"count": 0}
        )

        # Remove component
        await handle.remove_component(view_id, "counter-1")

        # Trying to send event to removed component should fail
        with pytest.raises(Exception) as exc_info:
            await handle.component_event(view_id, "counter-1", "increment", {})

        assert "not found" in str(exc_info.value).lower()

        await handle.shutdown()

    @pytest.mark.asyncio
    async def test_multiple_components(self):
        """Test managing multiple components in a single view"""
        from djust._rust import create_session_actor

        handle = await create_session_actor("test-phase8-multiple")

        # Mount view
        view = await handle.mount("test.View", {}, None)
        view_id = view["view_id"]

        # Create multiple components
        html1 = await handle.create_component(
            view_id, "counter-1", "<div>Counter 1: {{ count }}</div>", {"count": 1}
        )
        html2 = await handle.create_component(
            view_id, "counter-2", "<div>Counter 2: {{ count }}</div>", {"count": 2}
        )

        assert "Counter 1: 1" in html1
        assert "Counter 2: 2" in html2

        # Update first component
        html1_updated = await handle.update_component_props(view_id, "counter-1", {"count": 10})
        assert "Counter 1: 10" in html1_updated

        # Second component should be unchanged (update its props to verify)
        html2_check = await handle.update_component_props(view_id, "counter-2", {})
        assert "Counter 2: 2" in html2_check

        await handle.shutdown()

    @pytest.mark.asyncio
    async def test_component_not_found(self):
        """Test error when component doesn't exist"""
        from djust._rust import create_session_actor

        handle = await create_session_actor("test-phase8-not-found")

        # Mount view
        view = await handle.mount("test.View", {}, None)
        view_id = view["view_id"]

        # Try to send event to non-existent component
        with pytest.raises(Exception) as exc_info:
            await handle.component_event(view_id, "nonexistent", "click", {})

        assert "not found" in str(exc_info.value).lower()

        await handle.shutdown()

    @pytest.mark.asyncio
    async def test_component_in_unmounted_view(self):
        """Test that component operations fail after view is unmounted"""
        from djust._rust import create_session_actor

        handle = await create_session_actor("test-phase8-unmounted-view")

        # Mount view and create component
        view = await handle.mount("test.View", {}, None)
        view_id = view["view_id"]

        await handle.create_component(
            view_id, "counter-1", "<div>Count: {{ count }}</div>", {"count": 0}
        )

        # Unmount the view
        await handle.unmount(view_id)

        # Component operations should fail
        with pytest.raises(Exception) as exc_info:
            await handle.component_event(view_id, "counter-1", "increment", {})

        assert "not found" in str(exc_info.value).lower()

        await handle.shutdown()

    @pytest.mark.asyncio
    async def test_component_cleanup_on_view_shutdown(self):
        """Verify components are shut down when view is unmounted"""
        from djust._rust import create_session_actor

        handle = await create_session_actor("test-cleanup")
        view = await handle.mount("test.View", {}, None)
        view_id = view["view_id"]

        # Create multiple components
        for i in range(5):
            await handle.create_component(view_id, f"comp-{i}", "<div>{{ x }}</div>", {"x": i})

        # Unmount view - should shut down all components
        await handle.unmount(view_id)

        # Attempting to access component should fail (view not found)
        with pytest.raises(Exception) as exc_info:
            await handle.component_event(view_id, "comp-0", "click", {})

        assert "not found" in str(exc_info.value).lower()

        await handle.shutdown()

    # ========================================================================
    # Phase 8.2: Python Event Handler Integration Tests
    # ========================================================================

    @pytest.mark.asyncio
    async def test_component_with_python_handler(self):
        """Test component with Python event handler (Phase 8.2)."""
        from djust._rust import create_session_actor

        # Create a Python component class with event handlers
        class CounterComponent:
            def __init__(self):
                self.count = 0

            def increment(self, amount=1, **kwargs):
                """Event handler called from Rust ComponentActor."""
                self.count += int(amount)

            def decrement(self, amount=1, **kwargs):
                """Another event handler."""
                self.count -= int(amount)

            def get_context_data(self):
                """Return current state for template rendering."""
                return {"count": self.count}

        handle = await create_session_actor("test-phase8.2-python-handler")

        # Mount a view
        view_path = "test.CounterView"
        result = await handle.mount(view_path, {}, None)
        view_id = result["view_id"]

        # Create Python component instance
        py_component = CounterComponent()
        assert py_component.count == 0

        # Create component with Python instance
        template = "<div>Count: {{ count }}</div>"
        html = await handle.create_component(
            view_id,
            "counter-comp",
            template,
            {"count": 0},
            py_component,  # Phase 8.2: Pass Python component
        )

        assert "Count: 0" in html

        # Send event that calls Python handler
        html = await handle.component_event(view_id, "counter-comp", "increment", {"amount": 5})

        # Verify Python handler was called and state updated
        assert py_component.count == 5
        assert "Count: 5" in html

        # Send another event
        html = await handle.component_event(view_id, "counter-comp", "decrement", {"amount": 2})

        assert py_component.count == 3
        assert "Count: 3" in html

        await handle.shutdown()

    @pytest.mark.asyncio
    async def test_component_state_sync_from_python(self):
        """Test state synchronization from Python get_context_data() (Phase 8.2)."""
        from djust._rust import create_session_actor

        class TodoComponent:
            def __init__(self):
                self.items = []
                self.next_id = 1

            def add_item(self, text="", **kwargs):
                """Add item and update state."""
                self.items.append({"id": self.next_id, "text": text, "done": False})
                self.next_id += 1

            def toggle_item(self, id=None, **kwargs):
                """Toggle item completion."""
                for item in self.items:
                    if item["id"] == int(id):
                        item["done"] = not item["done"]
                        break

            def get_context_data(self):
                """Rust syncs this state after event handlers."""
                return {
                    "items": self.items,
                    "count": len(self.items),
                    "completed": sum(1 for i in self.items if i["done"]),
                }

        handle = await create_session_actor("test-phase8.2-state-sync")
        result = await handle.mount("test.TodoView", {}, None)
        view_id = result["view_id"]

        py_component = TodoComponent()

        template = """
        <div>
            <p>Total: {{ count }}, Completed: {{ completed }}</p>
        </div>
        """

        html = await handle.create_component(
            view_id, "todo-comp", template, {"count": 0, "completed": 0}, py_component
        )

        assert "Total: 0" in html
        assert "Completed: 0" in html

        # Add items via event handlers
        await handle.component_event(view_id, "todo-comp", "add_item", {"text": "Buy milk"})
        html = await handle.component_event(view_id, "todo-comp", "add_item", {"text": "Walk dog"})

        # State should sync from get_context_data()
        assert "Total: 2" in html
        assert "Completed: 0" in html

        # Toggle first item
        html = await handle.component_event(view_id, "todo-comp", "toggle_item", {"id": 1})

        assert "Total: 2" in html
        assert "Completed: 1" in html

        await handle.shutdown()

    @pytest.mark.asyncio
    async def test_component_without_python_handler_fallback(self):
        """Test component without Python handler uses fallback (Phase 8.2)."""
        from djust._rust import create_session_actor

        handle = await create_session_actor("test-phase8.2-fallback")
        result = await handle.mount("test.View", {}, None)
        view_id = result["view_id"]

        # Create component WITHOUT Python instance
        template = "<div>{{ message }}</div>"
        html = await handle.create_component(
            view_id,
            "simple-comp",
            template,
            {"message": "Hello"},
            None,  # No Python component
        )

        assert "Hello" in html

        # Event should still work using fallback (direct state update)
        html = await handle.component_event(
            view_id, "simple-comp", "update", {"message": "Goodbye"}
        )

        assert "Goodbye" in html

        await handle.shutdown()

    @pytest.mark.asyncio
    async def test_component_python_handler_not_found(self):
        """Test calling non-existent Python handler (Phase 8.2)."""
        from djust._rust import create_session_actor

        class SimpleComponent:
            def get_context_data(self):
                return {"value": 42}

        handle = await create_session_actor("test-phase8.2-handler-not-found")
        result = await handle.mount("test.View", {}, None)
        view_id = result["view_id"]

        py_component = SimpleComponent()

        template = "<div>{{ value }}</div>"
        await handle.create_component(view_id, "comp", template, {"value": 42}, py_component)

        # Call handler that doesn't exist - should fall back to state update
        html = await handle.component_event(view_id, "comp", "nonexistent_handler", {"value": 99})

        # Should use fallback and update state
        assert "99" in html

        await handle.shutdown()

    @pytest.mark.asyncio
    async def test_component_multiple_with_python_handlers(self):
        """Test multiple components each with their own Python handlers (Phase 8.2)."""
        from djust._rust import create_session_actor

        class Counter:
            def __init__(self, start=0):
                self.value = start

            def increment(self, **kwargs):
                self.value += 1

            def get_context_data(self):
                return {"value": self.value}

        handle = await create_session_actor("test-phase8.2-multiple")
        result = await handle.mount("test.View", {}, None)
        view_id = result["view_id"]

        # Create two components with separate Python instances
        counter1 = Counter(start=0)
        counter2 = Counter(start=100)

        template = "<div>{{ value }}</div>"

        await handle.create_component(view_id, "c1", template, {}, counter1)
        await handle.create_component(view_id, "c2", template, {}, counter2)

        # Increment counter1
        html1 = await handle.component_event(view_id, "c1", "increment", {})
        assert "1" in html1
        assert counter1.value == 1
        assert counter2.value == 100  # Counter2 unchanged

        # Increment counter2
        html2 = await handle.component_event(view_id, "c2", "increment", {})
        assert "101" in html2
        assert counter1.value == 1  # Counter1 unchanged
        assert counter2.value == 101

        await handle.shutdown()
