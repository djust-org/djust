"""
Presence tracking system for djust LiveView.

Allows LiveView instances to track which users are currently viewing a page,
similar to Phoenix LiveView's Presence system.

Example usage:

    class DocumentView(LiveView, PresenceMixin):
        presence_key = "document:{doc_id}"  # Group key

        def mount(self, request, **kwargs):
            self.doc_id = kwargs.get("doc_id")
            # Auto-track this user's presence
            self.track_presence(meta={"name": request.user.username, "color": "#6c63ff"})

        def get_context_data(self):
            ctx = super().get_context_data()
            ctx["presences"] = self.list_presences()  # [{"id": ..., "name": ..., "color": ...}]
            ctx["presence_count"] = self.presence_count()
            return ctx

        def handle_presence_join(self, presence):
            self.push_event("flash", {"message": f"{presence['name']} joined"})

        def handle_presence_leave(self, presence):
            pass

Template usage:

    <div class="presence-bar">
      {{ presence_count }} users online
      {% for p in presences %}
        <span class="avatar" style="background: {{ p.color }}">{{ p.name.0 }}</span>
      {% endfor %}
    </div>
"""

import asyncio
import json
import logging
import time
from typing import Any, Dict, List, Optional
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync, sync_to_async
from django.core.cache import cache

logger = logging.getLogger(__name__)

# Cache keys
PRESENCE_KEY_PREFIX = "djust_presence"
HEARTBEAT_KEY_PREFIX = "djust_heartbeat"
PRESENCE_GROUP_PREFIX = "djust_presence"

# Timeouts
HEARTBEAT_INTERVAL = 30  # seconds
PRESENCE_TIMEOUT = 60  # seconds - stale if no heartbeat for this long
CLEANUP_INTERVAL = 300  # seconds - cleanup every 5 minutes


class PresenceManager:
    """
    Manages presence state across the application.

    Delegates to the configured presence backend (memory or Redis).
    See ``djust.backends`` for backend implementations.
    """

    @staticmethod
    def _backend():
        from djust.backends.registry import get_presence_backend
        return get_presence_backend()

    @staticmethod
    def presence_group_name(presence_key: str) -> str:
        """Get the channels group name for a presence key."""
        return f"{PRESENCE_GROUP_PREFIX}_{presence_key.replace(':', '_').replace('{', '').replace('}', '')}"

    @classmethod
    def join_presence(cls, presence_key: str, user_id: str, meta: Dict[str, Any]) -> Dict[str, Any]:
        """
        Add a user to a presence group.

        Args:
            presence_key: The presence group identifier
            user_id: Unique identifier for the user
            meta: Metadata about the user (name, color, etc.)

        Returns:
            The presence record that was added
        """
        return cls._backend().join(presence_key, user_id, meta)

    @classmethod
    def leave_presence(cls, presence_key: str, user_id: str) -> Optional[Dict[str, Any]]:
        """
        Remove a user from a presence group.

        Args:
            presence_key: The presence group identifier
            user_id: Unique identifier for the user

        Returns:
            The presence record that was removed, or None if not found
        """
        return cls._backend().leave(presence_key, user_id)

    @classmethod
    def list_presences(cls, presence_key: str) -> List[Dict[str, Any]]:
        """
        Get all active presences for a group.

        Args:
            presence_key: The presence group identifier

        Returns:
            List of presence records
        """
        return cls._backend().list(presence_key)

    @classmethod
    def presence_count(cls, presence_key: str) -> int:
        """Get the count of active users in a presence group."""
        return cls._backend().count(presence_key)

    @classmethod
    def update_heartbeat(cls, presence_key: str, user_id: str):
        """Update the heartbeat timestamp for a user."""
        cls._backend().heartbeat(presence_key, user_id)


class PresenceMixin:
    """
    Mixin that provides presence tracking capabilities to LiveView.
    
    Usage:
        class MyView(LiveView, PresenceMixin):
            presence_key = "my_view:{id}"  # Define the presence group
            
            def mount(self, request, **kwargs):
                self.track_presence(meta={"name": request.user.username})
    """

    presence_key: Optional[str] = None
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._presence_tracked = False
        self._presence_user_id = None
        self._presence_meta = None

    def get_presence_key(self) -> str:
        """
        Get the presence key for this view instance.
        
        Override this method for dynamic presence keys, or set the class attribute.
        The key can contain format variables that will be resolved from view attributes.
        
        Example:
            presence_key = "document:{doc_id}"
            
        Returns:
            The formatted presence key
        """
        if not self.presence_key:
            # Default to view class path
            module = self.__class__.__module__
            name = self.__class__.__name__
            return f"{module}.{name}"
        
        # Format the presence key with view attributes
        try:
            return self.presence_key.format(**self.__dict__)
        except KeyError as e:
            logger.warning(f"Presence key format error: {e}. Using unformatted key.")
            return self.presence_key

    def get_presence_user_id(self) -> str:
        """
        Get the unique user identifier for presence tracking.
        
        Override this method to customize user identification.
        
        Returns:
            Unique user identifier
        """
        if hasattr(self, 'request') and self.request.user.is_authenticated:
            return str(self.request.user.id)
        
        # Fallback to session key for anonymous users
        if hasattr(self, 'request') and hasattr(self.request, 'session'):
            return f"anon_{self.request.session.session_key}"
        
        # Last resort - use a default identifier
        return "unknown_user"

    def track_presence(self, meta: Optional[Dict[str, Any]] = None):
        """
        Start tracking this user's presence.
        
        Args:
            meta: Metadata to associate with the user (name, color, avatar, etc.)
        """
        if self._presence_tracked:
            return
        
        presence_key = self.get_presence_key()
        user_id = self.get_presence_user_id()
        
        if meta is None:
            meta = {}
            
        # Add default metadata
        if hasattr(self, 'request') and self.request.user.is_authenticated:
            meta.setdefault('name', self.request.user.username)
            meta.setdefault('user_id', user_id)
        
        self._presence_user_id = user_id
        self._presence_meta = meta
        
        # Join presence
        presence_data = PresenceManager.join_presence(presence_key, user_id, meta)
        
        self._presence_tracked = True
        
        # Call presence join handler if it exists
        if hasattr(self, 'handle_presence_join'):
            try:
                self.handle_presence_join(presence_data)
            except Exception as e:
                logger.exception(f"Error in handle_presence_join: {e}")

    def untrack_presence(self):
        """Stop tracking this user's presence."""
        if not self._presence_tracked:
            return
        
        presence_key = self.get_presence_key()
        user_id = self._presence_user_id
        
        if user_id:
            presence_data = PresenceManager.leave_presence(presence_key, user_id)
            
            # Call presence leave handler if it exists
            if presence_data and hasattr(self, 'handle_presence_leave'):
                try:
                    self.handle_presence_leave(presence_data)
                except Exception as e:
                    logger.exception(f"Error in handle_presence_leave: {e}")
        
        self._presence_tracked = False
        self._presence_user_id = None
        self._presence_meta = None

    def list_presences(self) -> List[Dict[str, Any]]:
        """Get all active presences for this view's presence group."""
        presence_key = self.get_presence_key()
        return PresenceManager.list_presences(presence_key)

    def presence_count(self) -> int:
        """Get the count of active users in this view's presence group."""
        presence_key = self.get_presence_key()
        return PresenceManager.presence_count(presence_key)

    def update_presence_heartbeat(self):
        """Update the heartbeat for this user's presence."""
        if not self._presence_tracked or not self._presence_user_id:
            return
        
        presence_key = self.get_presence_key()
        PresenceManager.update_heartbeat(presence_key, self._presence_user_id)

    def handle_presence_join(self, presence: Dict[str, Any]):
        """
        Called when a user joins the presence group.
        
        Override this method to handle presence join events.
        
        Args:
            presence: The presence record of the user who joined
        """
        pass

    def handle_presence_leave(self, presence: Dict[str, Any]):
        """
        Called when a user leaves the presence group.
        
        Override this method to handle presence leave events.
        
        Args:
            presence: The presence record of the user who left
        """
        pass

    def broadcast_to_presence(self, event: str, payload: Dict[str, Any] = None):
        """
        Broadcast an event to all users in the presence group.
        
        Args:
            event: Event name
            payload: Event payload
        """
        if payload is None:
            payload = {}
        
        presence_key = self.get_presence_key()
        group_name = PresenceManager.presence_group_name(presence_key)
        
        channel_layer = get_channel_layer()
        if channel_layer:
            message = {
                "type": "presence_event",
                "event": event,
                "payload": payload,
            }
            async_to_sync(channel_layer.group_send)(group_name, message)


# Cursor tracking for live cursors (bonus feature)
class CursorTracker:
    """Manages live cursor positions for collaborative features."""
    
    CURSOR_KEY_PREFIX = "djust_cursors"
    CURSOR_TIMEOUT = 10  # seconds
    
    @classmethod
    def cursor_cache_key(cls, presence_key: str) -> str:
        """Get cache key for cursor positions."""
        return f"{cls.CURSOR_KEY_PREFIX}:{presence_key}"
    
    @classmethod
    def update_cursor(cls, presence_key: str, user_id: str, x: int, y: int, meta: Dict[str, Any] = None):
        """Update cursor position for a user."""
        cache_key = cls.cursor_cache_key(presence_key)
        cursors = cache.get(cache_key, {})
        
        cursors[user_id] = {
            "x": x,
            "y": y,
            "timestamp": time.time(),
            "meta": meta or {},
        }
        
        cache.set(cache_key, cursors, timeout=cls.CURSOR_TIMEOUT + 5)
    
    @classmethod
    def get_cursors(cls, presence_key: str) -> Dict[str, Dict[str, Any]]:
        """Get all active cursor positions."""
        cache_key = cls.cursor_cache_key(presence_key)
        cursors = cache.get(cache_key, {})
        
        # Clean up stale cursors
        now = time.time()
        active_cursors = {}
        
        for user_id, cursor_data in cursors.items():
            if (now - cursor_data["timestamp"]) < cls.CURSOR_TIMEOUT:
                active_cursors[user_id] = cursor_data
        
        # Update cache if we cleaned up stale cursors
        if len(active_cursors) != len(cursors):
            cache.set(cache_key, active_cursors, timeout=cls.CURSOR_TIMEOUT + 5)
        
        return active_cursors
    
    @classmethod
    def remove_cursor(cls, presence_key: str, user_id: str):
        """Remove cursor for a user."""
        cache_key = cls.cursor_cache_key(presence_key)
        cursors = cache.get(cache_key, {})
        
        if user_id in cursors:
            del cursors[user_id]
            cache.set(cache_key, cursors, timeout=cls.CURSOR_TIMEOUT + 5)


class LiveCursorMixin(PresenceMixin):
    """
    Extends PresenceMixin with live cursor tracking capabilities.
    
    Usage:
        class MyView(LiveView, LiveCursorMixin):
            presence_key = "document:{doc_id}"
            
            def handle_cursor_move(self, x, y):
                # Called when client sends cursor position
                pass
    """
    
    def update_cursor_position(self, x: int, y: int):
        """Update cursor position for this user."""
        if not self._presence_tracked or not self._presence_user_id:
            return
        
        presence_key = self.get_presence_key()
        meta = self._presence_meta or {}
        
        CursorTracker.update_cursor(presence_key, self._presence_user_id, x, y, meta)
        
        # Broadcast to other users in the presence group
        self.broadcast_to_presence("cursor_move", {
            "user_id": self._presence_user_id,
            "x": x,
            "y": y,
            "meta": meta,
        })
    
    def get_cursors(self) -> Dict[str, Dict[str, Any]]:
        """Get all active cursor positions for this presence group."""
        presence_key = self.get_presence_key()
        return CursorTracker.get_cursors(presence_key)
    
    def handle_cursor_move(self, x: int, y: int):
        """
        Handler called when cursor position is received from client.
        
        Override this method to add custom cursor move logic.
        
        Args:
            x: X coordinate
            y: Y coordinate
        """
        self.update_cursor_position(x, y)
    
    def untrack_presence(self):
        """Override to also remove cursor when leaving presence."""
        if self._presence_tracked and self._presence_user_id:
            presence_key = self.get_presence_key()
            CursorTracker.remove_cursor(presence_key, self._presence_user_id)
        
        super().untrack_presence()