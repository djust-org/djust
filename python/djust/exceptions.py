"""
Custom exceptions and error messages for djust LiveView.

This module provides improved error messages with actionable suggestions
for common LiveView mistakes.
"""

from typing import Optional


class LiveViewError(Exception):
    """Base exception for LiveView errors."""
    
    def __init__(self, message: str, hint: Optional[str] = None):
        super().__init__(message)
        self.message = message
        self.hint = hint


class MissingMountError(LiveViewError):
    """Raised when a state variable is accessed before mount() is called."""
    
    def __init__(self, var_name: str, view_name: str):
        message = (
            f"State variable '{var_name}' not initialized on {view_name}. "
            f"Did you forget to define it in mount()?"
        )
        hint = (
            f"\n    Example:\n"
            f"        class {view_name}(LiveView):\n"
            f"            def mount(self, request, **kwargs):\n"
            f"                self.{var_name} = 0  # <-- Initialize here"
        )
        super().__init__(message, hint)


class MissingMountMethodError(LiveViewError):
    """Raised when mount() method is not defined on LiveView class."""
    
    def __init__(self, view_name: str):
        message = (
            f"LiveView class '{view_name}' does not have a mount() method.\n"
            f"    mount() is required to initialize state variables."
        )
        hint = (
            f"\n    Example:\n"
            f"        class {view_name}(LiveView):\n"
            f"            template_name = '{view_name.lower()}.html'\n"
            f"\n"
            f"            def mount(self, request, **kwargs):\n"
            f"                # Initialize your state here\n"
            f"                self.count = 0\n"
            f"                self.message = 'Hello!'"
        )
        super().__init__(message, hint)


class MissingAttributeError(LiveViewError):
    """Raised when accessing an undefined attribute on LiveView."""
    
    def __init__(self, attr_name: str, view_name: str, suggestion: Optional[str] = None):
        message = (
            f"'{view_name}' has no attribute '{attr_name}'.\n"
            f"    Did you forget to initialize it in mount()?"
        )
        hint = suggestion or (
            f"\n    Quick fix:\n"
            f"        def mount(self, request, **kwargs):\n"
            f"            self.{attr_name} = <initial_value>  # Add this line"
        )
        super().__init__(message, hint)


class ReturnValueError(LiveViewError):
    """Raised when an event handler returns a value instead of modifying state."""
    
    def __init__(self, handler_name: str, view_name: str, returned_value: str):
        message = (
            f"Event handler '{handler_name}' returned a value ({returned_value}).\n"
            f"    LiveView handlers should modify self attributes, not return values."
        )
        hint = (
            f"\n    Instead of:\n"
            f"        def {handler_name}(self):\n"
            f"            return self.count + 1  # ❌ Wrong!\n"
            f"\n"
            f"    Do this:\n"
            f"        def {handler_name}(self):\n"
            f"            self.count += 1  # ✅ Correct! Modify state instead"
        )
        super().__init__(message, hint)


class InvalidEventHandlerError(LiveViewError):
    """Raised when an event handler has invalid signature or is missing @event_handler."""
    
    def __init__(self, handler_name: str, view_name: str, issue: str):
        message = (
            f"Invalid event handler '{handler_name}' on {view_name}: {issue}"
        )
        hint = (
            f"\n    Make sure to:\n"
            f"    1. Use @event_handler decorator\n"
            f"    2. Define the handler as a method (not a nested function)\n"
            f"    3. Accept 'self' as first parameter\n"
            f"\n"
            f"    Example:\n"
            f"        class {view_name}(LiveView):\n"
            f"            @event_handler\n"
            f"            def {handler_name}(self, **kwargs):\n"
            f"                # Handle the event\n"
            f"                pass"
        )
        super().__init__(message, hint)


class MissingDjPrefixError(LiveViewError):
    """Raised when event attribute is missing 'dj-' prefix."""
    
    def __init__(self, event_name: str, correct_name: str):
        message = (
            f"Event attribute '@{event_name}' is missing the 'dj-' prefix.\n"
            f"    LiveView requires 'dj-' prefixed event attributes."
        )
        hint = (
            f"\n    Instead of:\n"
            f"        <button @click=\"{event_name}\">Click</button>  ❌\n"
            f"\n"
            f"    Use:\n"
            f"        <button @{correct_name}=\"{event_name}\">Click</button>  ✅"
        )
        super().__init__(message, hint)


class SelfRenderError(LiveViewError):
    """Raised when self.render() is called incorrectly."""
    
    def __init__(self, handler_name: str, view_name: str):
        message = (
            f"Incorrect use of self.render() in handler '{handler_name}'.\n"
            f"    self.render() is not needed in LiveView - state changes trigger re-renders automatically."
        )
        hint = (
            f"\n    Instead of:\n"
            f"        def {handler_name}(self):\n"
            f"            self.count += 1\n"
            f"            self.render()  # ❌ Not needed!\n"
            f"\n"
            f"    Do this:\n"
            f"        def {handler_name}(self):\n"
            f"            self.count += 1  # ✅ Auto re-render!"
        )
        super().__init__(message, hint)


class MissingASGIConfigError(LiveViewError):
    """Raised when WSGI is used instead of ASGI for LiveView."""
    
    def __init__(self):
        message = (
            "LiveView requires ASGI, but WSGI was detected.\n"
            "    LiveView uses async WebSockets which require ASGI."
        )
        hint = (
            "\n    Use ASGI in your Django settings:\n"
            "\n"
            "    INSTALLED_APPS = [\n"
            "        ...\n"
            '        "djust",\n'
            "    ]\n"
            "\n"
            "    # Use djust's get_asgi_application() in asgi.py:\n"
            "    from djust import get_asgi_application\n"
            "    application = get_asgi_application()"
        )
        super().__init__(message, hint)


# Export all exception classes
__all__ = [
    "LiveViewError",
    "MissingMountError",
    "MissingMountMethodError",
    "MissingAttributeError",
    "ReturnValueError",
    "InvalidEventHandlerError",
    "MissingDjPrefixError",
    "SelfRenderError",
    "MissingASGIConfigError",
]
