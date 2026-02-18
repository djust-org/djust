"""
Type stubs for djust.live_view module.

This file provides type information for the LiveView class and its methods,
enabling proper type checking and IDE autocomplete for LiveView instance methods
that come from mixins (navigation, push_event, streams, etc.).

Generated for djust framework - enables catching typos like 'live_navigate' at lint time.
"""

from typing import Any, Callable, Dict, List, Optional, Union

from django.http import HttpRequest, HttpResponse
from django.views import View

from .session_utils import Stream

# ============================================================================
# LiveView Base Class with All Mixin Methods
# ============================================================================

class LiveView(View):
    """
    Base class for reactive LiveView components.

    Provides server-side rendering with reactive updates over WebSocket.
    Methods from mixins are included here for type checking and autocomplete.
    """

    # Class attributes
    template_name: Optional[str]
    template: Optional[str]
    use_actors: bool
    tick_interval: Optional[int]
    temporary_assigns: Dict[str, Any]
    static_assigns: List[str]
    login_required: Optional[bool]
    permission_required: Optional[Union[str, List[str]]]
    login_url: Optional[str]

    def __init__(self, **kwargs: Any) -> None: ...

    # ========================================================================
    # Core LiveView Lifecycle Methods
    # ========================================================================

    def mount(self, request: HttpRequest, **kwargs: Any) -> None:
        """
        Initialize LiveView state on first connection.

        Called once when the WebSocket connection is established.
        Set instance variables here to initialize state.

        Args:
            request: The Django request object
            **kwargs: URL parameters from the route

        Example::

            def mount(self, request, product_id=None, **kwargs):
                self.product_id = product_id
                self.count = 0
        """
        ...

    def get_context_data(self, **kwargs: Any) -> Dict[str, Any]:
        """
        Get context data for template rendering.

        Returns a dictionary of context variables to pass to the template.
        By default, returns all public instance attributes.

        Args:
            **kwargs: Additional context variables

        Returns:
            Dictionary of context variables for the template

        Example::

            def get_context_data(self, **kwargs):
                context = super().get_context_data(**kwargs)
                context['formatted_date'] = self.date.strftime('%Y-%m-%d')
                return context
        """
        ...

    def handle_tick(self) -> None:
        """
        Handle periodic server-side updates.

        Called every tick_interval milliseconds if tick_interval is set.
        Override to implement periodic state updates.

        Example::

            class LiveClockView(LiveView):
                tick_interval = 1000  # Update every second

                def handle_tick(self):
                    self.current_time = datetime.now()
        """
        ...

    def get_state(self) -> Dict[str, Any]:
        """
        Get serializable state from this LiveView instance.

        Validates that all public instance attributes can be serialized.
        In DEBUG mode, raises TypeError for non-serializable values.

        Returns:
            Dictionary of serializable public state

        Raises:
            TypeError: If non-serializable value found (in DEBUG mode)
        """
        ...

    # ========================================================================
    # Navigation Methods (NavigationMixin)
    # ========================================================================

    def live_patch(
        self,
        params: Optional[Dict[str, Any]] = None,
        path: Optional[str] = None,
        replace: bool = False,
    ) -> None:
        """
        Update the browser URL without remounting the view.

        The view's state is updated and re-rendered, but mount() is NOT called
        again. The browser URL changes via history.pushState.

        Args:
            params: Query parameters to set. Pass {} to clear all params.
            path: Optional new path. Defaults to current path.
            replace: If True, use replaceState instead of pushState.

        Example::

            @event_handler
            def filter_results(self, category="all", **kwargs):
                self.category = category
                self.live_patch(params={"category": category, "page": 1})
        """
        ...

    def live_redirect(
        self,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        replace: bool = False,
    ) -> None:
        """
        Navigate to a different LiveView over the existing WebSocket.

        The current view is unmounted and the new view is mounted without
        a full page reload or WebSocket reconnection.

        Args:
            path: URL path to navigate to (e.g. "/items/42/")
            params: Optional query parameters
            replace: If True, use replaceState instead of pushState

        Example::

            @event_handler
            def go_to_detail(self, item_id, **kwargs):
                self.live_redirect(f"/items/{item_id}/")
        """
        ...

    def handle_params(self, params: Dict[str, Any], uri: str) -> None:
        """
        Handle URL parameter changes.

        Called when URL params change via live_patch or browser back/forward.
        Override to update view state based on URL params.

        Args:
            params: The new URL query parameters as a dict
            uri: The full URI string

        Example::

            def handle_params(self, params, uri):
                self.category = params.get("category", "all")
                self.page = int(params.get("page", 1))
        """
        ...

    # ========================================================================
    # Push Events (PushEventMixin)
    # ========================================================================

    def push_event(self, event: str, payload: Optional[Dict[str, Any]] = None) -> None:
        """
        Push an event to the connected client.

        The event will be dispatched to dj-hook instances and as a
        CustomEvent on the document for non-hook listeners.

        Args:
            event: Event name (e.g. "flash", "scroll_to")
            payload: Dictionary of data to send with the event

        Example::

            @event_handler
            def save_data(self, **kwargs):
                self.save()
                self.push_event("flash", {"message": "Saved!", "type": "success"})
                self.push_event("scroll_to", {"selector": "#result"})
        """
        ...

    # ========================================================================
    # Streams (StreamsMixin)
    # ========================================================================

    def stream(
        self,
        name: str,
        items: Any,
        dom_id: Optional[Callable[[Any], str]] = None,
        at: int = -1,
        reset: bool = False,
    ) -> Stream:
        """
        Initialize or update a stream with items.

        Streams are memory-efficient collections that are automatically cleared
        after each render. The client preserves existing DOM elements.

        Args:
            name: Stream name (used in template as streams.{name})
            items: Iterable of items to add to the stream
            dom_id: Function to generate DOM id from item (default: lambda x: x.id)
            at: Position to insert (-1 = end, 0 = beginning)
            reset: If True, clear existing items first

        Returns:
            Stream object for chaining

        Example::

            def mount(self, request, **kwargs):
                # Initial load
                self.stream('messages', Message.objects.all()[:50])

            @event_handler
            def load_more(self, **kwargs):
                # Append more items
                messages = Message.objects.all()[50:100]
                self.stream('messages', messages, at=-1)
        """
        ...

    def stream_insert(self, name: str, item: Any, at: int = -1) -> None:
        """
        Insert an item into a stream.

        Args:
            name: Stream name
            item: Item to insert
            at: Position (-1 = append, 0 = prepend)

        Example::

            @event_handler
            def handle_new_message(self, content, **kwargs):
                msg = Message.objects.create(content=content)
                self.stream_insert('messages', msg, at=-1)  # Append
        """
        ...

    def stream_delete(self, name: str, item_or_id: Any) -> None:
        """
        Delete an item from a stream by item or id.

        Args:
            name: Stream name
            item_or_id: Model instance or ID to delete

        Example::

            @event_handler
            def delete_message(self, msg_id, **kwargs):
                Message.objects.filter(id=msg_id).delete()
                self.stream_delete('messages', msg_id)
        """
        ...

    def stream_reset(self, name: str, items: Optional[Any] = None) -> None:
        """
        Reset a stream, clearing all items and optionally adding new ones.

        Args:
            name: Stream name
            items: Optional new items to add after clearing

        Example::

            @event_handler
            def reset_filter(self, **kwargs):
                self.category = "all"
                messages = Message.objects.all()
                self.stream_reset('messages', messages)
        """
        ...

    # ========================================================================
    # HTTP Handlers
    # ========================================================================

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        """Handle GET requests."""
        ...

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        """Handle POST requests."""
        ...

# ============================================================================
# Function-based View Decorator
# ============================================================================

def live_view(
    template_name: Optional[str] = None,
    template: Optional[str] = None,
) -> Callable[[Callable], Callable]:
    """
    Decorator to convert a function-based view into a LiveView.

    Args:
        template_name: Path to Django template
        template: Inline template string

    Returns:
        Decorated view function

    Example::

        @live_view(template_name='counter.html')
        def counter_view(request):
            count = 0

            def increment():
                nonlocal count
                count += 1

            return locals()
    """
    ...

# ============================================================================
# Exported Names
# ============================================================================

__all__ = ["LiveView", "live_view"]
