"""
Tests for the presence tracking system
"""

import pytest
import time
from unittest.mock import Mock, patch
from django.test import RequestFactory
from django.contrib.auth.models import User, AnonymousUser
from django.core.cache import cache

from djust.presence import (
    PresenceManager,
    PresenceMixin, 
    LiveCursorMixin,
    CursorTracker,
    PRESENCE_TIMEOUT
)
from djust import LiveView


@pytest.fixture(autouse=True)
def clear_cache():
    """Clear cache before each test"""
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def user():
    """Create a test user"""
    return User.objects.create_user(username='testuser', email='test@example.com')


@pytest.fixture
def request_factory():
    """Request factory for creating mock requests"""
    return RequestFactory()


class TestPresenceManager:
    """Test the PresenceManager class"""
    
    def test_presence_group_name(self):
        """Test presence group name generation"""
        assert PresenceManager.presence_group_name("document:123") == "djust_presence_document_123"
        assert PresenceManager.presence_group_name("chat:{room_id}") == "djust_presence_chat_room_id"
    
    def test_presence_cache_key(self):
        """Test presence cache key generation"""
        assert PresenceManager.presence_cache_key("document:123") == "djust_presence:document:123"
    
    def test_heartbeat_cache_key(self):
        """Test heartbeat cache key generation"""
        assert PresenceManager.heartbeat_cache_key("document:123", "user1") == "djust_heartbeat:document:123:user1"
    
    def test_join_presence(self):
        """Test joining a presence group"""
        presence_key = "document:123"
        user_id = "user1"
        meta = {"name": "Test User", "color": "#ff0000"}
        
        # Join presence
        presence_data = PresenceManager.join_presence(presence_key, user_id, meta)
        
        assert presence_data["id"] == user_id
        assert presence_data["meta"] == meta
        assert "joined_at" in presence_data
        
        # Check presence is stored
        presences = PresenceManager.list_presences(presence_key)
        assert len(presences) == 1
        assert presences[0]["id"] == user_id
    
    def test_leave_presence(self):
        """Test leaving a presence group"""
        presence_key = "document:123"
        user_id = "user1"
        meta = {"name": "Test User"}
        
        # Join then leave
        PresenceManager.join_presence(presence_key, user_id, meta)
        presence_data = PresenceManager.leave_presence(presence_key, user_id)
        
        assert presence_data is not None
        assert presence_data["id"] == user_id
        
        # Check presence is removed
        presences = PresenceManager.list_presences(presence_key)
        assert len(presences) == 0
    
    def test_presence_count(self):
        """Test presence count"""
        presence_key = "document:123"
        
        assert PresenceManager.presence_count(presence_key) == 0
        
        # Add users
        PresenceManager.join_presence(presence_key, "user1", {"name": "User 1"})
        PresenceManager.join_presence(presence_key, "user2", {"name": "User 2"})
        
        assert PresenceManager.presence_count(presence_key) == 2
    
    def test_presence_cleanup_stale(self):
        """Test cleanup of stale presences"""
        presence_key = "document:123"
        user_id = "user1"
        meta = {"name": "Test User"}
        
        # Join presence
        PresenceManager.join_presence(presence_key, user_id, meta)
        
        # Manually set heartbeat to old timestamp
        heartbeat_key = PresenceManager.heartbeat_cache_key(presence_key, user_id)
        old_time = time.time() - PRESENCE_TIMEOUT - 10
        cache.set(heartbeat_key, old_time)
        
        # List presences should clean up stale ones
        presences = PresenceManager.list_presences(presence_key)
        assert len(presences) == 0


class TestPresenceMixin:
    """Test the PresenceMixin class"""
    
    def test_presence_key_formatting(self):
        """Test presence key formatting with view attributes"""
        class TestView(LiveView, PresenceMixin):
            presence_key = "document:{doc_id}"
            
            def __init__(self):
                super().__init__()
                self.doc_id = "123"
        
        view = TestView()
        assert view.get_presence_key() == "document:123"
    
    def test_presence_key_default(self):
        """Test default presence key generation"""
        class TestView(LiveView, PresenceMixin):
            pass
        
        view = TestView()
        key = view.get_presence_key()
        assert "TestView" in key
    
    def test_track_presence_authenticated_user(self, user, request_factory):
        """Test tracking presence with authenticated user"""
        class TestView(LiveView, PresenceMixin):
            presence_key = "test_view"
        
        request = request_factory.get('/')
        request.user = user
        
        view = TestView()
        view.request = request
        
        # Mock the handle_presence_join method
        view.handle_presence_join = Mock()
        
        view.track_presence(meta={"color": "#ff0000"})
        
        assert view._presence_tracked is True
        assert view._presence_user_id == str(user.id)
        assert view.handle_presence_join.called
        
        # Check presence in manager
        presences = view.list_presences()
        assert len(presences) == 1
        assert presences[0]["meta"]["name"] == user.username
    
    def test_track_presence_anonymous_user(self, request_factory):
        """Test tracking presence with anonymous user"""
        class TestView(LiveView, PresenceMixin):
            presence_key = "test_view"
        
        request = request_factory.get('/')
        request.user = AnonymousUser()
        request.session = Mock()
        request.session.session_key = "test_session_123"
        
        view = TestView()
        view.request = request
        
        view.track_presence()
        
        assert view._presence_tracked is True
        assert view._presence_user_id == "anon_test_session_123"
        
        # Check presence in manager
        presences = view.list_presences()
        assert len(presences) == 1
    
    def test_untrack_presence(self, user, request_factory):
        """Test untracking presence"""
        class TestView(LiveView, PresenceMixin):
            presence_key = "test_view"
        
        request = request_factory.get('/')
        request.user = user
        
        view = TestView()
        view.request = request
        
        # Mock the handle_presence_leave method
        view.handle_presence_leave = Mock()
        
        # Track then untrack
        view.track_presence()
        view.untrack_presence()
        
        assert view._presence_tracked is False
        assert view._presence_user_id is None
        assert view.handle_presence_leave.called
        
        # Check presence is removed
        presences = view.list_presences()
        assert len(presences) == 0
    
    def test_presence_count(self, user, request_factory):
        """Test presence count method"""
        class TestView(LiveView, PresenceMixin):
            presence_key = "test_view"
        
        request = request_factory.get('/')
        request.user = user
        
        view = TestView()
        view.request = request
        
        assert view.presence_count() == 0
        
        view.track_presence()
        assert view.presence_count() == 1
        
        view.untrack_presence()
        assert view.presence_count() == 0
    
    @patch('djust.presence.get_channel_layer')
    @patch('djust.presence.async_to_sync')
    def test_broadcast_to_presence(self, mock_async_to_sync, mock_get_channel_layer, user, request_factory):
        """Test broadcasting to presence group"""
        mock_channel_layer = Mock()
        mock_get_channel_layer.return_value = mock_channel_layer
        mock_group_send = Mock()
        mock_async_to_sync.return_value = mock_group_send
        
        class TestView(LiveView, PresenceMixin):
            presence_key = "test_view"
        
        request = request_factory.get('/')
        request.user = user
        
        view = TestView()
        view.request = request
        
        view.broadcast_to_presence("test_event", {"message": "hello"})
        
        mock_group_send.assert_called_once()
        # Check the group name and message
        call_args = mock_group_send.call_args[0]
        assert call_args[0] == "djust_presence_test_view"
        assert call_args[1]["type"] == "presence_event"
        assert call_args[1]["event"] == "test_event"
        assert call_args[1]["payload"]["message"] == "hello"


class TestCursorTracker:
    """Test the CursorTracker class"""
    
    def test_update_cursor(self):
        """Test updating cursor position"""
        presence_key = "document:123"
        user_id = "user1"
        
        CursorTracker.update_cursor(presence_key, user_id, 100, 200, {"color": "#ff0000"})
        
        cursors = CursorTracker.get_cursors(presence_key)
        assert user_id in cursors
        assert cursors[user_id]["x"] == 100
        assert cursors[user_id]["y"] == 200
        assert cursors[user_id]["meta"]["color"] == "#ff0000"
    
    def test_remove_cursor(self):
        """Test removing cursor"""
        presence_key = "document:123"
        user_id = "user1"
        
        # Add then remove cursor
        CursorTracker.update_cursor(presence_key, user_id, 100, 200)
        CursorTracker.remove_cursor(presence_key, user_id)
        
        cursors = CursorTracker.get_cursors(presence_key)
        assert user_id not in cursors
    
    def test_cursor_cleanup(self):
        """Test cleanup of stale cursors"""
        presence_key = "document:123"
        user_id = "user1"
        
        # Manually add stale cursor
        cache_key = CursorTracker.cursor_cache_key(presence_key)
        old_time = time.time() - CursorTracker.CURSOR_TIMEOUT - 10
        cursors = {user_id: {"x": 100, "y": 200, "timestamp": old_time, "meta": {}}}
        cache.set(cache_key, cursors)
        
        # Get cursors should clean up stale ones
        active_cursors = CursorTracker.get_cursors(presence_key)
        assert len(active_cursors) == 0


class TestLiveCursorMixin:
    """Test the LiveCursorMixin class"""
    
    @patch('djust.presence.get_channel_layer')
    @patch('djust.presence.async_to_sync')
    def test_update_cursor_position(self, mock_async_to_sync, mock_get_channel_layer, user, request_factory):
        """Test updating cursor position with broadcasting"""
        mock_channel_layer = Mock()
        mock_get_channel_layer.return_value = mock_channel_layer
        mock_group_send = Mock()
        mock_async_to_sync.return_value = mock_group_send
        
        class TestView(LiveView, LiveCursorMixin):
            presence_key = "document:123"
        
        request = request_factory.get('/')
        request.user = user
        
        view = TestView()
        view.request = request
        view.track_presence(meta={"name": user.username, "color": "#ff0000"})
        
        view.update_cursor_position(150, 250)
        
        # Check cursor was stored
        cursors = view.get_cursors()
        user_id = str(user.id)
        assert user_id in cursors
        assert cursors[user_id]["x"] == 150
        assert cursors[user_id]["y"] == 250
        
        # Check broadcast was called
        mock_group_send.assert_called_once()
    
    def test_handle_cursor_move(self, user, request_factory):
        """Test cursor move handler"""
        class TestView(LiveView, LiveCursorMixin):
            presence_key = "document:123"
        
        request = request_factory.get('/')
        request.user = user
        
        view = TestView()
        view.request = request
        view.track_presence()
        
        view.handle_cursor_move(75, 125)
        
        cursors = view.get_cursors()
        user_id = str(user.id)
        assert cursors[user_id]["x"] == 75
        assert cursors[user_id]["y"] == 125
    
    def test_untrack_presence_removes_cursor(self, user, request_factory):
        """Test that untracking presence also removes cursor"""
        class TestView(LiveView, LiveCursorMixin):
            presence_key = "document:123"
        
        request = request_factory.get('/')
        request.user = user
        
        view = TestView()
        view.request = request
        view.track_presence()
        view.update_cursor_position(100, 100)
        
        # Check cursor exists
        cursors = view.get_cursors()
        assert len(cursors) > 0
        
        # Untrack presence
        view.untrack_presence()
        
        # Check cursor is removed
        cursors = view.get_cursors()
        assert len(cursors) == 0


@pytest.mark.django_db
class TestPresenceIntegration:
    """Integration tests for presence system"""
    
    def test_multiple_users_same_presence_group(self, request_factory):
        """Test multiple users in the same presence group"""
        class TestView(LiveView, PresenceMixin):
            presence_key = "document:123"
        
        # Create two users
        user1 = User.objects.create_user(username='user1')
        user2 = User.objects.create_user(username='user2')
        
        # Create two view instances
        request1 = request_factory.get('/')
        request1.user = user1
        view1 = TestView()
        view1.request = request1
        
        request2 = request_factory.get('/')
        request2.user = user2
        view2 = TestView()
        view2.request = request2
        
        # Track presence for both users
        view1.track_presence(meta={"color": "#ff0000"})
        view2.track_presence(meta={"color": "#00ff00"})
        
        # Both views should see 2 users
        assert view1.presence_count() == 2
        assert view2.presence_count() == 2
        
        presences1 = view1.list_presences()
        presences2 = view2.list_presences()
        
        assert len(presences1) == 2
        assert len(presences2) == 2
        
        # User IDs should match
        user_ids = {p["id"] for p in presences1}
        assert str(user1.id) in user_ids
        assert str(user2.id) in user_ids
    
    def test_presence_isolation_between_groups(self, request_factory):
        """Test that presence groups are isolated"""
        user = User.objects.create_user(username='testuser')
        request = request_factory.get('/')
        request.user = user
        
        class View1(LiveView, PresenceMixin):
            presence_key = "document:123"
        
        class View2(LiveView, PresenceMixin):
            presence_key = "document:456"
        
        view1 = View1()
        view1.request = request
        view1.track_presence()
        
        view2 = View2()
        view2.request = request
        view2.track_presence()
        
        # Each view should only see its own presence
        assert view1.presence_count() == 1
        assert view2.presence_count() == 1
        
        view1.untrack_presence()
        
        # view1 should now be empty, view2 should still have the user
        assert view1.presence_count() == 0
        assert view2.presence_count() == 1