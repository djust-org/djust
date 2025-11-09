"""
Decorators for LiveView event handlers and reactive properties
"""

import functools
from typing import Callable, Any


def event_handler(func: Callable) -> Callable:
    """
    Mark a method as an event handler.

    Usage:
        class MyView(LiveView):
            @event_handler
            def on_click(self, event_data):
                self.counter += 1
    """
    func._is_event_handler = True
    return func


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
    # Store the getter
    getter = func

    # Create internal property name
    internal_name = f"_{func.__name__}_reactive"

    def _getter(self):
        return getattr(self, internal_name, None)

    def _setter(self, value):
        old_value = getattr(self, internal_name, None)
        setattr(self, internal_name, value)

        # Trigger update if value changed
        if old_value != value and hasattr(self, 'update'):
            self.update()

    return property(_getter, _setter)


def debounce(wait: float):
    """
    Debounce event handler calls.

    Usage:
        @debounce(0.3)
        @event_handler
        def on_input(self, value):
            self.search_query = value

    Args:
        wait: Seconds to wait before calling the function
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            # Simple debounce implementation
            # In production, this would use proper async timers
            import asyncio
            await asyncio.sleep(wait)
            return func(*args, **kwargs)
        return wrapper
    return decorator


def throttle(interval: float):
    """
    Throttle event handler calls.

    Usage:
        @throttle(0.5)
        @event_handler
        def on_scroll(self, position):
            self.scroll_position = position

    Args:
        interval: Minimum interval between calls in seconds
    """
    def decorator(func: Callable) -> Callable:
        last_call = [0.0]

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            import time
            now = time.time()
            if now - last_call[0] >= interval:
                last_call[0] = now
                return func(*args, **kwargs)

        return wrapper
    return decorator
