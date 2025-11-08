"""
Base classes for Django Rust Live components.

Provides the foundational LiveComponent ABC for creating reusable, reactive components.
"""

from typing import Dict, Any, Optional
from abc import ABC, abstractmethod


class LiveComponent(ABC):
    """
    Base class for creating reusable, reactive components.

    Components are self-contained UI elements with their own state and event handlers.
    They can be embedded in LiveViews or other components.

    Usage:
        class AlertComponent(LiveComponent):
            template_name = 'components/alert.html'

            def mount(self, **kwargs):
                self.message = kwargs.get('message', '')
                self.type = kwargs.get('type', 'info')
                self.visible = True

            def dismiss(self):
                self.visible = False

            def get_context(self):
                return {
                    'message': self.message,
                    'type': self.type,
                    'visible': self.visible,
                }

        # In template:
        {{ alert.render }}
    """

    # Component configuration
    template_name: Optional[str] = None
    component_id: Optional[str] = None

    def __init__(self, component_id: Optional[str] = None, **kwargs):
        """
        Initialize component.

        Args:
            component_id: Unique identifier for this component instance
            **kwargs: Component initialization parameters
        """
        self.component_id = component_id or self._generate_id()
        self._mounted = False
        self._parent = None

        # Mount with provided kwargs
        self.mount(**kwargs)
        self._mounted = True

    def _generate_id(self) -> str:
        """Generate a unique component ID"""
        import uuid
        return f"{self.__class__.__name__.lower()}_{uuid.uuid4().hex[:8]}"

    @abstractmethod
    def mount(self, **kwargs):
        """
        Initialize component state.

        This method is called when the component is created.
        Override to set up initial state.

        Args:
            **kwargs: Initialization parameters
        """
        pass

    @abstractmethod
    def get_context(self) -> Dict[str, Any]:
        """
        Get template context for rendering.

        Returns:
            Dictionary of context variables

        Example:
            return {
                'title': self.title,
                'items': self.items,
                'count': len(self.items),
            }
        """
        pass

    def render(self) -> str:
        """
        Render the component to HTML.

        Returns:
            HTML string (marked as safe for Django templates)

        Raises:
            ValueError: If template_name is not set
        """
        if not self.template_name:
            raise ValueError(f"{self.__class__.__name__} must set template_name")

        from django.template.loader import render_to_string
        from django.utils.safestring import mark_safe

        context = self.get_context()
        context['component_id'] = self.component_id

        return mark_safe(render_to_string(self.template_name, context))

    def set_parent(self, parent):
        """
        Set the parent LiveView for this component.

        Args:
            parent: Parent LiveView instance
        """
        self._parent = parent

    def trigger_update(self):
        """
        Trigger a re-render of the parent LiveView.

        This notifies the parent that the component state has changed
        and the view should be re-rendered.
        """
        if self._parent and hasattr(self._parent, '_trigger_update'):
            self._parent._trigger_update()
