"""
LiveView base class and decorator for reactive Django views
"""

import io
import json
import logging
import socket
import threading
from typing import Any, Callable, Dict, List, Optional, Union

from django.views import View

from .serialization import DjangoJSONEncoder  # noqa: F401
from .session_utils import (  # noqa: F401
    DEFAULT_SESSION_TTL,
    cleanup_expired_sessions,
    get_session_stats,
    _jit_serializer_cache,
    _get_model_hash,
    clear_jit_cache,
    Stream,
)

from .mixins import (
    StreamsMixin,
    StreamingMixin,
    TemplateMixin,
    ComponentMixin,
    JITMixin,
    ContextMixin,
    RustBridgeMixin,
    HandlerMixin,
    RequestMixin,
    PostProcessingMixin,
    ModelBindingMixin,
    PushEventMixin,
    NavigationMixin,
)

# Configure logger
logger = logging.getLogger(__name__)

try:
    from ._rust import (
        RustLiveView,
        create_session_actor,
        SessionActorHandle,
        extract_template_variables,
    )
except ImportError:
    RustLiveView = None
    create_session_actor = None
    SessionActorHandle = None
    extract_template_variables = None


class LiveView(
    StreamsMixin,
    StreamingMixin,
    TemplateMixin,
    ComponentMixin,
    JITMixin,
    ContextMixin,
    RustBridgeMixin,
    HandlerMixin,
    RequestMixin,
    PostProcessingMixin,
    ModelBindingMixin,
    PushEventMixin,
    NavigationMixin,
    View,
):
    """
    Base class for reactive LiveView components.

    Usage:
        class CounterView(LiveView):
            template_name = 'counter.html'
            use_actors = True  # Enable actor-based state management (optional)

            def mount(self, request, **kwargs):
                self.count = 0

            def increment(self):
                self.count += 1

            def decrement(self):
                self.count -= 1

    Memory Optimization with temporary_assigns:
        For views with large collections (chat messages, feed items, etc.),
        use temporary_assigns to clear data from server memory after each render.

        class ChatView(LiveView):
            template_name = 'chat.html'
            temporary_assigns = {'messages': []}  # Clear after each render

            def mount(self, request, **kwargs):
                self.messages = Message.objects.all()[:50]

            def handle_new_message(self, content):
                msg = Message.objects.create(content=content)
                self.messages = [msg]  # Only new messages sent to client

        IMPORTANT: When using temporary_assigns, use dj-update="append" in your
        template to tell the client to append new items instead of replacing:

            <ul dj-update="append" id="messages">
                {% for msg in messages %}
                    <li id="msg-{{ msg.id }}">{{ msg.content }}</li>
                {% endfor %}
            </ul>

    Streams API (recommended for collections):
        For a more ergonomic API, use streams instead of temporary_assigns:

        class ChatView(LiveView):
            template_name = 'chat.html'

            def mount(self, request, **kwargs):
                self.stream('messages', Message.objects.all()[:50])

            def handle_new_message(self, content):
                msg = Message.objects.create(content=content)
                self.stream_insert('messages', msg)

        Template:
            <ul dj-stream="messages">
                {% for msg in streams.messages %}
                    <li id="messages-{{ msg.id }}">{{ msg.content }}</li>
                {% endfor %}
            </ul>
    """

    template_name: Optional[str] = None
    template: Optional[str] = None
    use_actors: bool = False  # Enable Tokio actor-based state management (Phase 5+)
    tick_interval: Optional[int] = None  # Periodic tick in ms (e.g. 2000 for 2s)

    # Memory optimization: assigns to clear after each render
    # Format: {'assign_name': default_value, ...}
    # Example: {'messages': [], 'feed_items': [], 'notifications': []}
    temporary_assigns: Dict[str, Any] = {}

    # Authentication & authorization
    login_required: Optional[bool] = None  # True = must be authenticated
    permission_required: Optional[Union[str, List[str]]] = None  # Django permission string(s)
    login_url: Optional[str] = None  # Override settings.LOGIN_URL

    # ============================================================================
    # INITIALIZATION & SETUP
    # ============================================================================

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._rust_view: Optional[RustLiveView] = None
        self._actor_handle: Optional[SessionActorHandle] = None
        self._session_id: Optional[str] = None
        self._cache_key: Optional[str] = None
        self._handler_metadata: Optional[dict] = None  # Cache for decorator metadata
        self._components: Dict[str, Any] = {}  # Registry of child components by ID
        self._temporary_assigns_initialized: bool = False  # Track if temp assigns are set up
        self._streams: Dict[str, Stream] = {}  # Stream collections
        self._stream_operations: list = []  # Pending stream operations for this render
        # Initialize navigation support (live_patch, live_redirect)
        self._init_navigation()

    def handle_tick(self):
        """Override for periodic server-side updates. Called every tick_interval ms."""
        pass

    # ============================================================================
    # STATE SERIALIZATION VALIDATION
    # ============================================================================

    @staticmethod
    def _is_serializable(value: Any) -> bool:
        """Check if a value can be safely serialized to JSON for state transfer.

        Returns True for primitives, collections, Django models/QuerySets, and
        any value that json.dumps can handle. Returns False for service instances,
        connections, file handles, threads, and other non-serializable objects.
        """
        # Primitives are always fine
        if value is None or isinstance(value, (str, int, float, bool)):
            return True

        # Collections: check recursively would be expensive; allow them and
        # let actual serialization catch nested issues
        if isinstance(value, (list, tuple, set, frozenset)):
            return True

        if isinstance(value, dict):
            return True

        # Django models and QuerySets are serialized by JIT pipeline
        try:
            from django.db import models
            from django.db.models import QuerySet

            if isinstance(value, (models.Model, QuerySet)):
                return True
        except ImportError:
            pass

        # Non-serializable types: file handles, threads, locks, sockets
        _non_serializable = (io.IOBase, threading.Thread, socket.socket)
        try:
            # threading.Lock() returns _thread.lock which isn't directly a type
            import _thread

            _non_serializable = _non_serializable + (_thread.LockType,)
        except (ImportError, AttributeError):
            pass
        if isinstance(value, _non_serializable):
            return False

        # Detect common service/client patterns by type name
        type_name = type(value).__name__.lower()
        _suspect_names = ("service", "client", "session", "connection", "api")
        if any(name in type_name for name in _suspect_names):
            return False

        # Detect objects with generic repr like '<ClassName object at 0x...>'
        try:
            obj_repr = repr(value)
            if " object at 0x" in obj_repr:
                return False
        except Exception:
            return False

        # Final check: try to actually serialize it
        try:
            json.dumps(value, cls=DjangoJSONEncoder)
            return True
        except (TypeError, ValueError, OverflowError):
            return False

    def get_state(self) -> Dict[str, Any]:
        """Get serializable state from this LiveView instance.

        Iterates over public (non-underscore) instance attributes and validates
        that each value can be serialized. In DEBUG mode, raises TypeError with
        a helpful message for non-serializable values. In production, logs an
        error and skips the attribute.

        Returns:
            Dictionary of {attribute_name: value} for all serializable public state.
        """
        from django.conf import settings

        state = {}
        for key, value in self.__dict__.items():
            if key.startswith("_"):
                continue

            if callable(value):
                continue

            if not self._is_serializable(value):
                class_name = self.__class__.__name__
                value_type = type(value).__name__
                msg = (
                    f"Non-serializable value in {class_name}.{key}: "
                    f"{value_type} cannot be stored in LiveView state. "
                    f"Service instances, connections, and file handles must "
                    f"be created in event handlers or accessed via utility "
                    f"functions — not stored as instance attributes. "
                    f"See: https://djust.org/docs/guides/services.md"
                )
                if getattr(settings, "DEBUG", False):
                    raise TypeError(msg)
                else:
                    logger.error(msg)
                    continue

            state[key] = value

        return state

    # ============================================================================
    # TEMPORARY ASSIGNS - Memory optimization for large collections
    # ============================================================================

    def _reset_temporary_assigns(self) -> None:
        """
        Reset temporary assigns to their default values after rendering.

        Called automatically after each render to free memory for large collections.
        """
        if not self.temporary_assigns:
            return

        for assign_name, default_value in self.temporary_assigns.items():
            if hasattr(self, assign_name):
                # Reset to default value (make a copy to avoid sharing state)
                if isinstance(default_value, list):
                    setattr(self, assign_name, list(default_value))
                elif isinstance(default_value, dict):
                    setattr(self, assign_name, dict(default_value))
                elif isinstance(default_value, set):
                    setattr(self, assign_name, set(default_value))
                else:
                    setattr(self, assign_name, default_value)

                logger.debug(
                    f"[LiveView] Reset temporary assign '{assign_name}' to {type(default_value).__name__}"
                )

        # Also reset streams
        self._reset_streams()

    def _initialize_temporary_assigns(self) -> None:
        """Initialize temporary assigns with their default values on first mount."""
        if self._temporary_assigns_initialized:
            return

        for assign_name, default_value in self.temporary_assigns.items():
            if not hasattr(self, assign_name):
                if isinstance(default_value, list):
                    setattr(self, assign_name, list(default_value))
                elif isinstance(default_value, dict):
                    setattr(self, assign_name, dict(default_value))
                elif isinstance(default_value, set):
                    setattr(self, assign_name, set(default_value))
                else:
                    setattr(self, assign_name, default_value)

        self._temporary_assigns_initialized = True


def live_view(template_name: Optional[str] = None, template: Optional[str] = None):
    """
    Decorator to convert a function-based view into a LiveView.

    Usage:
        @live_view(template_name='counter.html')
        def counter_view(request):
            count = 0

            def increment():
                nonlocal count
                count += 1

            def decrement():
                nonlocal count
                count -= 1

            return locals()

    Args:
        template_name: Path to Django template
        template: Inline template string

    Returns:
        View function
    """

    def decorator(func: Callable) -> Callable:
        def wrapper(request, *args, **kwargs):
            # Create a dynamic LiveView class
            class DynamicLiveView(LiveView):
                pass

            if template_name:
                DynamicLiveView.template_name = template_name
            if template:
                DynamicLiveView.template = template

            view = DynamicLiveView()

            # Execute the function to get initial state
            result = func(request, *args, **kwargs)
            if isinstance(result, dict):
                for key, value in result.items():
                    if not callable(value):
                        setattr(view, key, value)
                    else:
                        setattr(view, key, value)

            # Handle the request
            if request.method == "GET":
                return view.get(request, *args, **kwargs)
            elif request.method == "POST":
                return view.post(request, *args, **kwargs)

        return wrapper

    return decorator
