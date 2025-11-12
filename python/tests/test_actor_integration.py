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
        assert hasattr(stats, 'active_sessions')
        assert hasattr(stats, 'ttl_secs')
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
            view  # Pass Python view instance!
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
        await handle.mount(
            "test.CounterView",
            {"count": 0},
            view
        )

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
        """Test actor event with missing handler raises error"""
        from djust._rust import create_session_actor

        class MinimalView:
            def get_context_data(self):
                return {}

        handle = await create_session_actor("test-phase5-missing")
        view = MinimalView()

        await handle.mount("test.MinimalView", {}, view)

        # Calling non-existent handler should raise error
        with pytest.raises(Exception) as exc_info:
            await handle.event("nonexistent_handler", {})

        assert "not found" in str(exc_info.value).lower() or "error" in str(exc_info.value).lower()

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
            None  # No Python view
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

        # Python exception should be caught and converted to error
        with pytest.raises(Exception) as exc_info:
            await handle.event("broken_handler", {})

        error_msg = str(exc_info.value).lower()
        assert "error" in error_msg or "intentional" in error_msg

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

        # Should handle invalid context_data gracefully
        with pytest.raises(Exception) as exc_info:
            await handle.event("some_event", {})

        # Error should mention the problem
        error_msg = str(exc_info.value).lower()
        assert "error" in error_msg or "dict" in error_msg

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

