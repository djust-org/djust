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
        """Test getting actor system statistics"""
        from djust._rust import get_actor_stats, create_session_actor

        stats = get_actor_stats()
        assert "info" in stats

        # Create and shutdown an actor
        handle = await create_session_actor("stats-test")
        await handle.shutdown()

        stats2 = get_actor_stats()
        assert stats2 is not None

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
