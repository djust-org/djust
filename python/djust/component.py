"""
Reusable LiveView components
"""

from typing import Dict, Any, Optional
from ._rust import RustLiveView


class Component:
    """
    Base class for reusable LiveView components.

    Usage:
        class Button(Component):
            template = '<button @click="on_click">{{ label }}</button>'

            def __init__(self, label="Click me"):
                super().__init__()
                self.label = label

            def on_click(self):
                print(f"Button {self.label} clicked!")
    """

    template: str = ""

    def __init__(self, **kwargs):
        self._rust_view: Optional[RustLiveView] = None
        self._parent = None

        # Set initial properties
        for key, value in kwargs.items():
            setattr(self, key, value)

    def get_context_data(self) -> Dict[str, Any]:
        """Get context data for rendering"""
        context = {}
        for key in dir(self):
            if not key.startswith("_") and not callable(getattr(self, key)):
                context[key] = getattr(self, key)
        return context

    def render(self) -> str:
        """Render the component to HTML"""
        if self._rust_view is None:
            self._rust_view = RustLiveView(self.template)

        context = self.get_context_data()
        self._rust_view.update_state(context)
        return self._rust_view.render()

    def update(self):
        """Trigger a re-render (will notify parent if set)"""
        if self._parent:
            self._parent.update()


class ComponentRegistry:
    """Registry for component definitions"""

    _components: Dict[str, type] = {}

    @classmethod
    def register(cls, name: str, component_class: type):
        """Register a component"""
        cls._components[name] = component_class

    @classmethod
    def get(cls, name: str) -> Optional[type]:
        """Get a registered component"""
        return cls._components.get(name)

    @classmethod
    def create(cls, name: str, **kwargs) -> Optional[Component]:
        """Create an instance of a registered component"""
        component_class = cls.get(name)
        if component_class:
            return component_class(**kwargs)
        return None


def register_component(name: str):
    """
    Decorator to register a component.

    Usage:
        @register_component('my-button')
        class MyButton(Component):
            template = '<button>Click me</button>'
    """

    def decorator(component_class: type):
        ComponentRegistry.register(name, component_class)
        return component_class

    return decorator
