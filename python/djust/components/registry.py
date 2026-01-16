"""
Component registry for djust.

Provides registration and discovery of LiveComponent classes.
"""

from typing import Dict, Optional, Type
from .base import LiveComponent


# Global component registry
_component_registry: Dict[str, Type[LiveComponent]] = {}


def register_component(name: str, component_class: Type[LiveComponent]):
    """
    Register a custom component.

    Args:
        name: Component name (e.g., 'alert', 'button', 'tabs')
        component_class: Component class extending LiveComponent

    Example:
        from djust.components import register_component

        class MyWidgetComponent(LiveComponent):
            ...

        register_component('my_widget', MyWidgetComponent)
    """
    if not issubclass(component_class, LiveComponent):
        raise TypeError(f"{component_class.__name__} must extend LiveComponent")

    _component_registry[name] = component_class


def get_component(name: str) -> Optional[Type[LiveComponent]]:
    """
    Get a component class by name.

    Args:
        name: Component name

    Returns:
        Component class or None if not found

    Example:
        from djust.components import get_component

        AlertClass = get_component('alert')
        if AlertClass:
            alert = AlertClass(message="Hello", type="success")
    """
    return _component_registry.get(name)


def list_components() -> Dict[str, Type[LiveComponent]]:
    """
    Get all registered components.

    Returns:
        Dictionary mapping component names to their classes

    Example:
        from djust.components import list_components

        for name, component_class in list_components().items():
            print(f"{name}: {component_class.__name__}")
    """
    return _component_registry.copy()


def unregister_component(name: str) -> bool:
    """
    Unregister a component.

    Args:
        name: Component name

    Returns:
        True if component was removed, False if it didn't exist

    Example:
        from djust.components import unregister_component

        if unregister_component('old_widget'):
            print("Component removed")
    """
    if name in _component_registry:
        del _component_registry[name]
        return True
    return False
