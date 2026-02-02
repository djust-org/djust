"""
Embedded LiveViews - allows nesting LiveViews inside other LiveViews.

Each embedded view has independent state, mounts separately, and re-renders
independently. All embedded views share the parent's WebSocket connection.

Communication between parent and child views is done via send_parent() and
send_child() methods.

Usage in templates:
    {% load live_tags %}
    {% live_render "myapp.views.SearchBar" query="" %}
    {% live_render "myapp.views.NotificationBell" %}
"""

import importlib
import json
import logging
import uuid
from typing import Any, Dict, Optional, TYPE_CHECKING

from django.utils.safestring import mark_safe

if TYPE_CHECKING:
    from .live_view import LiveView

logger = logging.getLogger(__name__)


class LiveSession:
    """
    A group of LiveViews sharing one WebSocket connection.

    Provides session-level state that all views can read, and supports
    navigation between views without page reload.
    """

    def __init__(self, session_id: Optional[str] = None):
        self.session_id = session_id or str(uuid.uuid4())
        self._shared_state: Dict[str, Any] = {}
        self._views: Dict[str, "LiveView"] = {}  # view_id -> LiveView instance

    def get(self, key: str, default: Any = None) -> Any:
        """Get a value from shared session state."""
        return self._shared_state.get(key, default)

    def put(self, key: str, value: Any) -> None:
        """Set a value in shared session state."""
        self._shared_state[key] = value

    def delete(self, key: str) -> None:
        """Remove a value from shared session state."""
        self._shared_state.pop(key, None)

    @property
    def state(self) -> Dict[str, Any]:
        """Read-only access to shared state."""
        return dict(self._shared_state)

    def register_view(self, view_id: str, view: "LiveView") -> None:
        """Register a view in this session."""
        self._views[view_id] = view

    def unregister_view(self, view_id: str) -> None:
        """Unregister a view from this session."""
        self._views.pop(view_id, None)

    def get_view(self, view_id: str) -> Optional["LiveView"]:
        """Get a view by ID."""
        return self._views.get(view_id)


class EmbeddedViewMixin:
    """
    Mixin that adds embedded LiveView support to a LiveView class.

    Adds:
    - _parent_view / _child_views references
    - send_parent() / send_child() communication
    - _view_id for WebSocket event routing
    - _live_session for shared state
    """

    def _init_embedded(self):
        """Initialize embedded view tracking. Called from LiveView.__init__."""
        self._view_id: str = str(uuid.uuid4())[:8]
        self._parent_view: Optional["LiveView"] = None
        self._child_views: Dict[str, "LiveView"] = {}  # view_id -> child instance
        self._live_session: Optional[LiveSession] = None

    @property
    def view_id(self) -> str:
        """Unique ID for this view instance."""
        return getattr(self, "_view_id", "")

    @property
    def live_session(self) -> Optional[LiveSession]:
        """Access the shared live session."""
        return getattr(self, "_live_session", None)

    @property
    def parent(self) -> Optional["LiveView"]:
        """Access the parent view (if this is an embedded child)."""
        return getattr(self, "_parent_view", None)

    def send_parent(self, event: str, **data) -> None:
        """
        Send an event to the parent view.

        Args:
            event: Event name
            **data: Event data
        """
        parent = getattr(self, "_parent_view", None)
        if parent is None:
            logger.warning(
                "send_parent() called on view %s but it has no parent",
                self.__class__.__name__,
            )
            return

        # Call parent's handle_child_event
        if hasattr(parent, "handle_child_event"):
            parent.handle_child_event(self._view_id, event, **data)

    def send_child(self, child_id: str, event: str, **data) -> None:
        """
        Send an event to a child view.

        Args:
            child_id: The view_id of the child
            event: Event name
            **data: Event data
        """
        child_views = getattr(self, "_child_views", {})
        child = child_views.get(child_id)
        if child is None:
            logger.warning(
                "send_child() called with unknown child_id %s on view %s",
                child_id,
                self.__class__.__name__,
            )
            return

        # Call child's handle_parent_event
        if hasattr(child, "handle_parent_event"):
            child.handle_parent_event(event, **data)

    def handle_child_event(self, child_id: str, event: str, **params) -> None:
        """
        Handle an event sent from a child view via send_parent().

        Override this method to respond to child events.

        Args:
            child_id: The view_id of the child that sent the event
            event: Event name
            **params: Event data
        """
        pass

    def handle_parent_event(self, event: str, **params) -> None:
        """
        Handle an event sent from the parent view via send_child().

        Override this method to respond to parent events.

        Args:
            event: Event name
            **params: Event data
        """
        pass

    def _register_child(self, child: "LiveView") -> str:
        """
        Register a child embedded view.

        Returns the child's view_id.
        """
        if not hasattr(self, "_child_views"):
            self._child_views = {}

        child._parent_view = self
        child._live_session = getattr(self, "_live_session", None)
        self._child_views[child._view_id] = child

        # Register in live session if available
        session = getattr(self, "_live_session", None)
        if session:
            session.register_view(child._view_id, child)

        return child._view_id

    def _unregister_child(self, view_id: str) -> None:
        """Unregister a child embedded view."""
        child_views = getattr(self, "_child_views", {})
        child = child_views.pop(view_id, None)
        if child:
            child._parent_view = None
            session = getattr(self, "_live_session", None)
            if session:
                session.unregister_view(view_id)

    def _get_all_child_views(self) -> Dict[str, "LiveView"]:
        """Get all child views (including nested children) recursively."""
        result = {}
        for vid, child in getattr(self, "_child_views", {}).items():
            result[vid] = child
            if hasattr(child, "_get_all_child_views"):
                result.update(child._get_all_child_views())
        return result


def resolve_view_class(view_path: str) -> type:
    """
    Import and return a LiveView class from a dotted path.

    Args:
        view_path: Dotted path like "myapp.views.SearchBarView"

    Returns:
        The LiveView class
    """
    module_path, class_name = view_path.rsplit(".", 1)
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


def render_embedded_view(
    parent_view: "LiveView",
    view_path: str,
    request=None,
    **kwargs,
) -> str:
    """
    Instantiate and render an embedded child LiveView.

    Called by the {% live_render %} template tag during parent rendering.

    Args:
        parent_view: The parent LiveView instance
        view_path: Dotted path to the child LiveView class
        request: Django request object (optional, inherited from parent)
        **kwargs: Initial kwargs passed to child's mount()

    Returns:
        HTML string with a wrapper div containing the child's rendered content
    """
    from django.test import RequestFactory

    # Import the child view class
    view_class = resolve_view_class(view_path)

    # Instantiate
    child = view_class()

    # Initialize embedded support
    if hasattr(child, "_init_embedded"):
        child._init_embedded()

    # Register with parent
    view_id = parent_view._register_child(child)

    # Create a request if not provided
    if request is None:
        factory = RequestFactory()
        request = factory.get("/")

    # Mount the child
    child.mount(request, **kwargs)

    # Render the child's template
    try:
        # Try to use render() which goes through Rust VDOM
        child._initialize_rust_view(request)
        child._sync_state_to_rust()
        html = child._rust_view.render()
    except Exception:
        # Fallback: get context and render template directly
        try:
            context = child.get_context_data()
            from django.template import engines

            template_str = child.get_template()
            engine = engines["django"] if "django" in engines else list(engines.all())[0]
            tmpl = engine.from_string(template_str)
            html = tmpl.render(context)
        except Exception as e:
            logger.error("Failed to render embedded view %s: %s", view_path, e)
            html = f"<!-- Error rendering {view_path}: {e} -->"

    # Wrap in a container div with view_id for scoped patching
    wrapper = (
        f'<div data-djust-embedded="{view_id}" '
        f'data-djust-view-path="{view_path}" '
        f'id="embedded-{view_id}">'
        f"{html}"
        f"</div>"
    )

    return mark_safe(wrapper)
