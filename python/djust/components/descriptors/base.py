"""
Re-exports for descriptor-based component development.

Provides a DescriptorBase that satisfies LiveComponent's abstract interface
so descriptors can be instantiated as class attributes on view classes.
"""

from djust.components.base import LiveComponent as _DjustLiveComponent
from djust.components.mixins.base import TypedState
from typing import Any


class LiveComponent(_DjustLiveComponent):
    """Base for descriptor-based components.

    Provides default no-op implementations of LiveComponent's abstract
    methods so that concrete descriptor classes (Accordion, Tabs, etc.)
    can be instantiated as class attributes on view classes without
    subclassing them further.
    """

    # ``BoundComponent`` stops forwarding at this class: ``mount`` and
    # ``get_context_data`` below are framework no-ops, not component API.
    _djust_framework_component_base = True

    def mount(self, **kwargs: Any) -> None:
        """No-op — descriptors don't have their own lifecycle."""

    def get_context_data(self) -> dict:
        """No-op — descriptors contribute context via __get__, not this method."""
        return {}


__all__ = ["LiveComponent", "TypedState"]
