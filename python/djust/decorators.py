"""
Decorators for LiveView event handlers and reactive properties

These decorators make LiveView code more elegant and explicit by marking
event handlers, reactive state, and computed properties.
"""

import functools
from typing import Callable, Any, TypeVar, cast


F = TypeVar("F", bound=Callable[..., Any])


def event_handler(func: F) -> F:
    """
    Mark a method as an event handler.

    Usage:
        class MyView(LiveView):
            @event_handler
            def on_click(self, event_data):
                self.counter += 1

    Note: You can also use the shorter @event alias.
    """
    func._is_event_handler = True  # type: ignore
    func._event_name = func.__name__  # type: ignore
    return func


# Shorter alias for event_handler
def event(func: F) -> F:
    """
    Shorter alias for @event_handler.

    Mark a method as an event handler for cleaner syntax.

    Usage:
        class MyView(LiveView):
            @event
            def increment(self):
                self.count += 1

            @event
            def update_item(self, item_id: str, value: str = "", **kwargs):
                self.items[item_id] = value
    """
    return event_handler(func)


def reactive(func: Callable) -> property:
    """
    Create a reactive property that triggers re-render on change.

    Usage:
        class MyView(LiveView):
            @reactive
            def count(self):
                return self._count

            @count.setter
            def count(self, value):
                self._count = value
    """
    # Create internal property name
    internal_name = f"_{func.__name__}_reactive"

    def _getter(self):
        return getattr(self, internal_name, None)

    def _setter(self, value):
        old_value = getattr(self, internal_name, None)
        setattr(self, internal_name, value)

        # Trigger update if value changed
        if old_value != value and hasattr(self, "update"):
            self.update()

    return property(_getter, _setter)


def state(default: Any = None):
    """
    Decorator to mark a property as reactive state.

    This provides a cleaner syntax than manually setting attributes in mount().
    The state is automatically included in the view's context and triggers
    re-renders when changed.

    Usage:
        class MyView(LiveView):
            count = state(default=0)
            message = state(default="Hello")

            @event
            def increment(self):
                self.count += 1

    Args:
        default: Default value for the state property

    Returns:
        Property descriptor for the state attribute
    """

    class StateProperty:
        def __init__(self):
            self.default = default
            self.attr_name = None
            self.public_name = None

        def __set_name__(self, owner, name):
            self.attr_name = f"_state_{name}"
            self.public_name = name

        def __get__(self, obj, objtype=None):
            if obj is None:
                return self
            return getattr(obj, self.attr_name, self.default)

        def __set__(self, obj, value):
            setattr(obj, self.attr_name, value)
            # Mark this as reactive state
            if not hasattr(obj, "_reactive_state"):
                obj._reactive_state = set()
            obj._reactive_state.add(self.public_name)

    return StateProperty()


def computed(func: F) -> F:
    """
    Decorator to mark a method as a computed property.

    Computed properties are derived from state and are automatically
    recalculated when state changes. They are available in templates
    but not stored in state.

    Usage:
        class MyView(LiveView):
            count = state(default=0)

            @computed
            def count_doubled(self):
                return self.count * 2

            @computed
            def is_even(self):
                return self.count % 2 == 0

    In template:
        <div>Count: {{ count }}</div>
        <div>Doubled: {{ count_doubled }}</div>
        <div>Is even? {{ is_even }}</div>
    """

    @functools.wraps(func)
    @property
    def wrapper(self):
        return func(self)

    wrapper._is_computed = True  # type: ignore
    wrapper._computed_name = func.__name__  # type: ignore
    return cast(F, wrapper)


def debounce(wait: float = 0.3):
    """
    Debounce event handler calls on the client side.

    This decorator adds metadata to the event handler that the JavaScript
    client uses to debounce events. Useful for input events where you want
    to wait until the user stops typing.

    Usage:
        class MyView(LiveView):
            @event
            @debounce(wait=0.5)
            def on_search(self, value: str = "", **kwargs):
                self.results = search_database(value)

    Args:
        wait: Seconds to wait before triggering (default: 0.3)

    Returns:
        Decorator function
    """

    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            return func(*args, **kwargs)

        wrapper._debounce_seconds = wait  # type: ignore
        wrapper._debounce_ms = int(wait * 1000)  # type: ignore
        return cast(F, wrapper)

    return decorator


def throttle(interval: float = 0.1):
    """
    Throttle event handler calls on the client side.

    This decorator adds metadata to the event handler that the JavaScript
    client uses to throttle events. Useful for scroll, resize, or mouse
    move events where you want to limit how often the handler runs.

    Usage:
        class MyView(LiveView):
            @event
            @throttle(interval=0.1)
            def on_scroll(self, scroll_y: int = 0, **kwargs):
                self.scroll_position = scroll_y

    Args:
        interval: Minimum interval between calls in seconds (default: 0.1)

    Returns:
        Decorator function
    """

    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            return func(*args, **kwargs)

        wrapper._throttle_seconds = interval  # type: ignore
        wrapper._throttle_ms = int(interval * 1000)  # type: ignore
        return cast(F, wrapper)

    return decorator


__all__ = [
    "event_handler",
    "event",
    "reactive",
    "state",
    "computed",
    "debounce",
    "throttle",
]
